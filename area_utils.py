"""
area_utils.py — Polígono de área válida (limite geográfico do trabalho)

O frontend desenha um polígono no mapa; ele é o FOCO do trabalho, não um filtro
de gravação. Todo POI encontrado é gravado — o que cai fora do polígono entra
identificado por `cidade`/`uf` e simplesmente não aparece na tela enquanto
aquela área está em foco. Achar custa tempo de busca; descartar o que já foi
achado é jogar esse tempo fora, e no dia em que a cidade vizinha for minerada
ela já começa com parte do trabalho pronto.

Formato do arquivo de área:
  {"nome": "parnaiba", "polygon": [[lat, lng], [lat, lng], ...]}
  (também aceita uma lista pura [[lat, lng], ...])

Sem dependências externas (ray casting puro).
"""

import json
from pathlib import Path

AREA_PADRAO = "area_atual"


def _esquema(cur):
    """Cria a tabela na primeira execução — e desiste em silêncio se não puder.

    `CREATE TABLE IF NOT EXISTS` exige CREATE no schema. O worker do pipeline
    tem; o papel da APLICAÇÃO não tem, e não deve ter. Antes de 13/08/2026 isto
    era chamado a cada LEITURA, dentro de um `try` que devolvia `None` em
    qualquer falha: para todo usuário logado, a área de trabalho simplesmente
    não existia. A tela dizia "Nenhuma área definida — desenhe o polígono", o
    mapa abria no lugar errado e nenhum marcador aparecia — com 22 mil POIs
    gravados e a área salva no banco.

    O engano é fácil de repetir: a exceção era de PERMISSÃO, num CREATE que nem
    precisava acontecer, escondida por um `except` que tratava tudo como
    "não achei".
    """
    # PERGUNTA antes de tentar. Não é otimização: a primeira versão desta
    # correção usava try/except com `rollback()`, e o rollback DESFAZIA o
    # `set_config('app.tenant_id')` que a conexão do usuário tinha acabado de
    # declarar — a RLS passava a negar tudo e a área sumia de novo, agora por
    # outro motivo. Consultar o catálogo não mexe na transação.
    cur.execute("select to_regclass('comercialradar.area_trabalho')")
    if cur.fetchone()[0] is not None:
        return
    cur.execute("""CREATE TABLE IF NOT EXISTS area_trabalho (
                     nome      text PRIMARY KEY,
                     polygon   jsonb NOT NULL,     -- [[lat, lng], ...]
                     salvo_em  timestamp DEFAULT now())""")


def salvar_area(poligono, nome: str = AREA_PADRAO, tenant: str | None = None) -> int:
    """Grava a área NO BANCO. Lista vazia apaga.

    `tenant` existe por causa do ROOT, e o motivo merece ficar escrito.

    Dentro de uma requisição, `bc.conectar()` devolve a conexão do USUÁRIO
    (`auth.conectar_como`), e ela só declara `app.tenant_id` quando o usuário
    tem empresa. O `root` não tem — é o único usuário sem `tenant_id`, de
    propósito, porque ele atravessa todas as empresas. Resultado: a trigger
    `preencher_tenant` não achava o que carimbar, o `NOT NULL` recusava a linha
    e a rota estourava 500.

    Foi o defeito de 24/08/2026: o usuário desenhava a área, a tela dizia
    "Área salva ✔" (o front não olhava a resposta) e o banco seguia com a área
    de NOVE DIAS antes. A mineração então rodava sobre a área velha — 264 tiles
    do município inteiro em vez das 4 quadras desenhadas.

    Quando `tenant` vem preenchido, ele é declarado por `set_config(..., true)`
    — LOCAL À TRANSAÇÃO, igual ao que o `auth` faz. Vale só para este INSERT e
    morre no commit; a conexão não leva o crachá para a requisição seguinte.
    """
    import base_comum as bc
    con = bc.conectar()
    try:
        with con.cursor() as cur:
            _esquema(cur)
            if tenant:
                cur.execute("select set_config('app.tenant_id', %s, true)",
                            (str(tenant),))
            if not poligono or len(poligono) < 3:
                cur.execute("DELETE FROM area_trabalho WHERE nome=%s", (nome,))
                con.commit()
                return 0
            pol = [[float(a), float(b)] for a, b in poligono]
            # `ON CONFLICT (tenant_id, nome)`: a chave passou a ser por empresa
            # em 13/08/2026. Com `(nome)` sozinho, a segunda empresa a desenhar
            # sobrescrevia a área da primeira — que ela nem enxerga, porque a
            # RLS esconde a linha mas a unicidade vale sobre a tabela inteira.
            # `tenant_id` não aparece no INSERT de propósito: quem o preenche é
            # a trigger, a partir da mesma variável de sessão que a RLS lê.
            cur.execute("""INSERT INTO area_trabalho (nome, polygon, salvo_em)
                           VALUES (%s, %s, now())
                           ON CONFLICT (tenant_id, nome) DO UPDATE
                             SET polygon = EXCLUDED.polygon, salvo_em = now()""",
                        (nome, json.dumps(pol)))
        con.commit()
        return len(pol)
    finally:
        con.close()


def carregar_area(ref=AREA_PADRAO) -> list | None:
    """Vértices [[lat, lng], ...] da área — DO BANCO.

    A área é dado, e dado mora no banco (tabela `area_trabalho`), não num .json
    dentro da pasta do sistema: ela é compartilhada entre o servidor e todos os
    coletores, que rodam como subprocessos separados.

    `ref` é o NOME da área. Ainda aceita um caminho de arquivo, para não quebrar
    quem chame com `areas/area_atual.json` — nesse caso lê o arquivo e MIGRA para
    o banco, para a próxima leitura já vir de lá."""
    if ref is None:
        ref = AREA_PADRAO
    p = Path(str(ref))
    if p.suffix.lower() == ".json":
        nome = p.stem or AREA_PADRAO
        if p.exists():                       # legado: migra e segue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                poly = data.get("polygon") if isinstance(data, dict) else data
                if poly and len(poly) >= 3:
                    salvar_area(poly, nome)
                    p.unlink(missing_ok=True)
            except Exception:
                pass
        ref = nome

    try:
        import base_comum as bc
        con = bc.conectar()
    except Exception:
        return None
    try:
        with con.cursor() as cur:
            _esquema(cur)
            # SEM `con.commit()` aqui. Ele existia para fechar o CREATE TABLE, e
            # passou a ser destrutivo quando a conexão virou a do usuário: o
            # `app.tenant_id` é declarado com `set_config(..., true)`, que é
            # LOCAL À TRANSAÇÃO. Commitar apagava o tenant, e o SELECT logo
            # abaixo rodava sem identidade — a RLS negava, `carregar_area`
            # devolvia None e a tela dizia "nenhuma área definida", com a área
            # salva no banco e visível para o worker.
            #
            # Nada aqui precisa de commit: é leitura.
            cur.execute("SELECT polygon FROM area_trabalho WHERE nome=%s", (str(ref),))
            r = cur.fetchone()
        if not r or not r[0] or len(r[0]) < 3:
            return None
        return [[float(a), float(b)] for a, b in r[0]]
    except Exception as e:
        # Falar. O silêncio aqui custou uma sessão inteira de diagnóstico: a
        # área sumia para todo usuário logado e o sintoma aparecia três telas
        # adiante, como "nenhum POI no mapa".
        print(f"  [area] não consegui ler '{ref}': {type(e).__name__}: "
              f"{str(e).splitlines()[0][:110]}", flush=True)
        return None
    finally:
        con.close()


def ponto_no_poligono(lat, lng, poligono) -> bool:
    """Ray casting: True se (lat, lng) está dentro do polígono [[lat, lng], ...]."""
    if lat is None or lng is None or not poligono:
        return False
    dentro = False
    n = len(poligono)
    j = n - 1
    for i in range(n):
        yi, xi = poligono[i][0], poligono[i][1]
        yj, xj = poligono[j][0], poligono[j][1]
        if ((yi > lat) != (yj > lat)) and \
           (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            dentro = not dentro
        j = i
    return dentro


_MUN_CACHE: dict = {}


def municipio_da_area(poligono=None, ref=AREA_PADRAO) -> tuple:
    """(cidade, uf) DA ÁREA DE TRABALHO — a fonte única.

    Cidade e UF não são adivinhadas por módulo. Elas saem de onde o usuário as
    definiu: o polígono desenhado no mapa, ou o município escolhido no painel
    (que vira polígono pela malha do IBGE). Um ponto dentro dele — o centroide —
    resolve o município pela `ibge_malha`, com índice espacial.

    Antes cada consumidor deduzia por conta própria e o `minerar_web` tinha
    `"Parnaíba"` FIXO no código, herança de quando havia um cliente só: numa
    rodada de Canoas ele montava a busca `"Fulano" parnaiba RS` e, pior, a
    conferência do CNPJ na Receita comparava o município com "parnaiba" e
    REJEITAVA todo CNPJ encontrado. O dado chegava e era jogado fora."""
    poly = poligono if poligono is not None else carregar_area(ref)
    if not poly or len(poly) < 3:
        return ("", "")
    chave = (round(poly[0][0], 5), round(poly[0][1], 5), len(poly))
    if chave in _MUN_CACHE:
        return _MUN_CACHE[chave]
    lat = sum(p[0] for p in poly) / len(poly)
    lng = sum(p[1] for p in poly) / len(poly)
    # A consulta é feita AQUI, e não mais em `quadras_br`. O módulo de quadras
    # mudou-se para o radarTelhados em 11/08/2026, junto com todo o território;
    # o comercialRadar continua precisando resolver município por ponto porque
    # cidade e UF são a fonte única de toda a ferramenta. A tabela `ibge_malha`
    # fica nas duas — são 8,6 MB de divisa oficial, e duplicar dado de
    # referência público é mais barato que acoplar duas ferramentas.
    # A `ibge_malha` é base pública e mora no banco de REFERÊNCIA desde
    # 12/08/2026. Consultá-la pela conexão do produto devolvia ("", "") em
    # silêncio — e cidade vazia não estoura erro: ela envenena a busca web e faz
    # a conferência de CNPJ recusar tudo, que foi exatamente o bug do "Parnaíba"
    # fixo descrito acima. Falha silenciosa em fonte única é a pior de todas.
    try:
        import base_comum as bc
        con = bc.conectar_referencia()
        try:
            with con.cursor() as cur:
                cur.execute("""SELECT nome, uf FROM ibge_malha
                                WHERE ST_Contains(geom,
                                        ST_SetSRID(ST_Point(%s, %s), 4326))
                                LIMIT 1""", (lng, lat))
                r = cur.fetchone()
            out = ((r[0] or ""), (r[1] or "").upper()) if r else ("", "")
        finally:
            con.close()
    except Exception:
        out = ("", "")
    _MUN_CACHE[chave] = out
    return out


def bbox(poligono):
    lats = [p[0] for p in poligono]
    lngs = [p[1] for p in poligono]
    return min(lats), max(lats), min(lngs), max(lngs)


# A MARGEM DA ÁREA DE TRABALHO — UMA definição, para TODAS as etapas.
#
# Ela nasceu dentro do `cruzar_fontes`: a fusão precisa enxergar o vizinho de
# FORA da linha, porque o duplicado do POI que está na borda pode estar do outro
# lado dela, e cortar exato o tornaria invisível.
#
# O QUE ISSO QUEBROU, e foi medido em 26/08/2026: cada etapa acabou com um
# recorte próprio, cada um mais largo que o anterior.
#
#     segmentar    polígono EXATO        80 POIs
#     normalizar   caixa do polígono     85 POIs
#     cruzar       caixa + 150 m        307 POIs
#
# O cruzamento comparava 222 POIs que ninguém tinha normalizado — e a
# normalização existe justamente para lhe dar a chave de junção. Só 22% dos
# POIs que ele viu tinham logradouro canônico, e a culpa não era da skill: era
# de eu ter escrito três recortes em três momentos do dia sem alinhá-los.
#
# 150 m é a própria rede de candidatos do cruzamento (célula de ~110 m mais as
# vizinhas), então a margem não inventa alcance — ela só não amputa o que o
# algoritmo já usa. Mudá-la aqui muda para todo mundo, que é o ponto.
MARGEM_TRABALHO_M = 150.0


def bbox_com_margem(poligono, metros: float = MARGEM_TRABALHO_M):
    """A caixa do polígono, folgada. `(sul, norte, oeste, leste)`.

    Um grau de latitude são ~111 km em qualquer lugar; de longitude encolhe com
    o cosseno da latitude. Usar 111 km nos dois daria uma margem mais ESTREITA
    em longitude do que a pedida — no RS, ~13% menor. Aqui cada eixo usa a sua.
    """
    import math

    s, n, o, l = bbox(poligono)
    d_lat = metros / 111_000.0
    lat_media = math.radians((s + n) / 2)
    d_lng = metros / (111_320.0 * max(0.1, math.cos(lat_media)))
    return (s - d_lat, n + d_lat, o - d_lng, l + d_lng)


def recorte_sql(poligono, col_lat: str, col_lng: str, prefixo: str = "area",
                margem_m: float = MARGEM_TRABALHO_M):
    """Corte de uma consulta pela área desenhada, em parâmetros NOMEADOS.

    A CAIXA vai ao banco; o polígono exato fica em Python. Não é preguiça: a
    caixa é comparação de quatro floats numa coluna que costuma ter índice,
    enquanto o `ponto_no_poligono` é ray casting por linha — mandá-lo ao banco
    viraria varredura sequencial. A caixa derruba a ordem de grandeza, e o que
    sobra dela é pouco o bastante para julgar em memória.

    QUEM CHAMA PRECISA FILTRAR DEPOIS. A caixa aceita os cantos que estão fora
    do desenho; devolver só ela seria entregar mais do que o operador pediu — e
    é exatamente o erro que produziu 0 pontos no iFood e 157 buscas fora da área
    no Maps.

A COORDENADA MANDA, E ÀS VEZES ELA DISCORDA DO CAMPO `cidade`. Quem consulta
    por nome de cidade e recorta por geometria vai achar registro que o nome
    incluía e a posição exclui. Medido em Cachoeirinha: 2 POIs em 8.391 dizem
    "Cachoeirinha" e caem fora da divisa — um deles a 11 metros dela. Está
    certo excluí-los; está errado fazer isso calado, que é como "a base
    encolheu" nasce. Quem chama deve DIZER o número.

    Devolve `(fragmento, params)`. Com polígono nulo devolve `("", {})`, e a
    consulta segue valendo para o município inteiro.
    """
    if not poligono:
        return "", {}
    s, n, o, l = (bbox_com_margem(poligono, margem_m) if margem_m
                  else bbox(poligono))
    return (f" and {col_lat} between %({prefixo}_s)s and %({prefixo}_n)s"
            f" and {col_lng} between %({prefixo}_o)s and %({prefixo}_l)s",
            {f"{prefixo}_s": s, f"{prefixo}_n": n,
             f"{prefixo}_o": o, f"{prefixo}_l": l})


def coord_do_registro(reg):
    """Coordenada efetiva de um registro do pipeline (maps_* > *_origem)."""
    la = reg.get("maps_lat")
    lo = reg.get("maps_lng")
    if la is None or lo is None:
        la = reg.get("lat_origem")
        lo = reg.get("lng_origem")
    try:
        return (float(la), float(lo)) if la is not None and lo is not None else (None, None)
    except (TypeError, ValueError):
        return (None, None)


def gate_registro(reg: dict, poligono) -> bool:
    """
    Marca se o registro caiu DENTRO da área de trabalho.
    Retorna True se está dentro; False se está fora.
    Registros sem coordenada nenhuma passam (não há como julgar).

    **Estar fora não invalida mais o registro** (regra do usuário, 04/08/2026).
    Antes isto zerava `match_valido`, e o ingestor descartava o POI logo na
    primeira checagem — um comércio já encontrado e já pago em tempo de busca era
    jogado fora por estar 40 m além da linha desenhada. Agora ele é gravado, com
    `cidade`/`uf` tiradas do endereço, e o polígono só decide o que aparece na
    TELA: o mapa mostra a área em foco ou o município selecionado, e o de fora
    espera ali até que aquela cidade seja o foco.

    O `status` original é preservado — ele diz como o POI foi encontrado, que é
    outra pergunta. Quem quiser o banco restrito à área usa "Limpar banco fora
    da área", que é uma decisão explícita.
    """
    if not poligono:
        return True
    la, lo = coord_do_registro(reg)
    if la is None:
        return True
    if ponto_no_poligono(la, lo, poligono):
        reg["fora_da_area"] = False
        return True
    reg["fora_da_area"] = True
    return False


def garantir_malha(uf: str, log=print) -> int:
    """A malha municipal daquela UF no banco de referência, baixando se faltar.

    POR QUE ISTO SAIU DE DENTRO DO ENDPOINT

    `ibge_malha` é o que traduz "Canoas/RS" em `4304606`, e o código é o que
    liga o município ao Cadastur, ao CNEFE, à importação da base estadual e à
    normalização de endereço. Sem ele, quatro das sete etapas da mineração caem
    juntas — e a mensagem que o operador via era "município fora da malha IBGE
    carregada", que descreve o sintoma e esconde a saída.

    A malha nunca foi carregada em lote: ela entra SOB DEMANDA, quando alguém
    navega o mapa por aquela UF (`/api/malha`). Por isso o banco tem 20 das 27 —
    são as que já foram visitadas. Uma área em Goiás simplesmente não tinha sido.

    Agora a mineração pede a sua antes de desistir. Devolve quantos municípios a
    UF tem no banco depois da tentativa; `0` significa que nem o IBGE respondeu.
    """
    import json
    import urllib.request

    import base_comum as bc   # import local, como no resto do modulo

    uf = (uf or "").strip().upper()
    if len(uf) != 2:
        return 0

    ref = bc.conectar_referencia()
    try:
        with ref.cursor() as cur:
            cur.execute("select count(*) from ibge_malha where uf = %s", (uf,))
            n = cur.fetchone()[0]
            if n:
                return n

        log(f"  malha de {uf} ainda não está no banco — baixando do IBGE")
        base = "https://servicodados.ibge.gov.br/api/v3/malhas/estados"
        url = (f"{base}/{uf}?formato=application/vnd.geo+json"
               f"&qualidade=intermediaria&intrarregiao=municipio")
        # `intermediaria` e não `minima`: na mínima as divisas são generalizadas
        # e Itambé-PE, a 2 km da fronteira, caía na Paraíba.
        def _json(u, timeout):
            """O IBGE responde GZIP mesmo sem `Accept-Encoding`.

            Sem descomprimir, o `json.loads` estoura com
            `UnicodeDecodeError: byte 0x8b in position 1` — que e a assinatura
            do gzip, e nao um problema de acentuacao como o nome do erro
            sugere. O painel ja tratava isso no `/api/malha`; esta funcao
            nasceu sem, e o sintoma apontava para o lugar errado.
            """
            import gzip
            req = urllib.request.Request(u, headers={"User-Agent": "comercialRadar"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                bruto = r.read()
            if bruto[:2] == bytes((0x1F, 0x8B)):   # assinatura do gzip
                bruto = gzip.decompress(bruto)
            return json.loads(bruto)

        gj = _json(url, 120)
        nomes = {str(m["id"]): m["nome"] for m in _json(
            f"https://servicodados.ibge.gov.br/api/v1/localidades/"
            f"estados/{uf}/municipios", 60)}

        gravados = 0
        with ref.cursor() as cur:
            for f in gj.get("features", []):
                cod = str((f.get("properties") or {}).get("codarea") or "")
                if not cod:
                    continue
                cur.execute(
                    """insert into ibge_malha (cod_municipio, nome, uf, geom)
                       values (%s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
                       on conflict (cod_municipio) do update
                          set nome = coalesce(excluded.nome, ibge_malha.nome),
                              uf = excluded.uf, geom = excluded.geom""",
                    (cod, nomes.get(cod), uf, json.dumps(f.get("geometry"))))
                gravados += 1
        ref.commit()
        log(f"  malha de {uf}: {gravados} municípios gravados")
        return gravados
    except Exception as erro:  # noqa: BLE001
        # NÃO derruba a rodada: sem a malha algumas etapas ficam de fora, e o
        # diagnóstico já diz quais. Trocar a mineração inteira por uma falha de
        # rede do IBGE seria o pior negócio possível.
        log(f"  ⚠️  não consegui a malha de {uf}: {type(erro).__name__}: {erro}")
        return 0
    finally:
        ref.close()
