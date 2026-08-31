"""
realtime_ingest.py — Ingestão incremental de POIs no PostgreSQL (psycopg2)

Espelha as regras do src/ingest.ts (Prisma), mas registro a registro, para o
servidor web gravar no banco EM TEMPO REAL conforme o coletor produz resultados:
  - pula sem nome / match_valido=False
  - guard geográfico Brasil (lat∈[-34,6], lng∈[-74,-34])
  - gate de polígono (área desenhada no frontend), quando fornecido
  - idempotente por place_id (delete + recreate, derivadas em cascade manual)

Também expõe utilidades usadas pelo server.py (limpeza fora da área, consultas).
"""

import os
import re
import json
import threading

import psycopg2
import psycopg2.extras

import config  # carrega o .env
import area_utils

_LOCK = threading.Lock()


def _opcoes() -> str:
    """So o `search_path`. A identidade NAO cabe aqui — ver `_assumir`.

    O `search_path` ja vem do papel (`alter role ... set search_path`), entao o
    codigo segue escrevendo `pois` sem qualificar o schema. Declarar aqui
    tambem protege quem conectar com outro papel.
    """
    return "-c search_path=radar_comercial,extensions,public"


def _assumir(con) -> None:
    """Declara de quem e o que este processo grava.

    POR QUE AQUI, E NAO NAS OPCOES DA CONEXAO. Medido em 31/08/2026: um
    `options="-c request.jwt.claim.sub=<uuid>"` chega VAZIO ao servidor. O
    `search_path`, da MESMA string, chega — o Postgres aplica os parametros
    conhecidos do pacote de conexao e ignora em silencio os customizados, que
    sao os de nome com ponto.

    Nao ha erro. A conexao abre, o schema esta certo, e `core.empresa_atual()`
    devolve nulo: toda gravacao passa a ser recusada pela policy, com a
    mensagem apontando para a tabela e nao para a conexao.

    ESCOPO DE SESSAO (`false`), e nao de transacao. O pipeline abre uma conexao
    e roda milhares de transacoes nela; local a transacao morreria no primeiro
    commit e a segunda gravacao ja nasceria sem dono. Isso e seguro na 7100
    (modo sessao), onde a conexao e pinada — e SO nela: na 7110 um GUC de
    sessao vaza para o cliente seguinte do pool, o que tambem foi medido.

    Sem `RADAR_USUARIO_SERVICO` a variavel nao e declarada, `empresa_atual()`
    volta nulo e o `not null` recusa a linha. E de proposito: linha sem dono
    nao some, ela nasce invisivel para todo mundo — e ninguem procura o que nao
    sabe que perdeu.
    """
    quem = (os.environ.get("RADAR_USUARIO_SERVICO")
            or os.environ.get("CR_TENANT_ID") or "").strip()
    if not quem:
        return
    with con.cursor() as cur:
        cur.execute("select set_config('request.jwt.claim.sub', %s, false)",
                    (quem,))
    con.commit()


def conectar():
    """Conexão com o banco do PRODUTO.

    O banco mora no i9, dentro da pilha Supabase, no schema `comercialradar`.

    Entre 12 e 13/08/2026 houve uma queda para o Postgres do notebook: as `I9_*`
    mandavam quando existiam, e sem elas o pipeline voltava ao banco antigo. Essa
    rede existia para permitir voltar atrás mudando uma linha do `.env` enquanto
    o i9 não estivesse provado. O i9 se provou e o banco do notebook foi
    aposentado — a queda virou armadilha e por isso agora é erro explícito.

    O `search_path` já vem do papel (`alter role ... set search_path`), então o
    código segue escrevendo `pois` sem qualificar o schema. Definir aqui também
    protege quem conectar com outro papel.
    """
    # DENTRO de uma requisição autenticada, a conexão é a do USUÁRIO: o uuid
    # dele em `request.jwt.claim.sub`, e a RLS filtra pelo que `core.tb_users`
    # disser. Fora dela — pipeline, scripts, jobs — vale o usuário de SERVIÇO da
    # empresa.
    #
    # O QUE MUDOU EM 30/08/2026, e é mais do que um nome: antes o pipeline usava
    # um papel com BYPASSRLS ("o worker"), que ignorava toda policy de toda
    # tabela. Agora ele é `app_user` como o resto, e passa pelas mesmas
    # políticas. Se uma consulta do pipeline voltar vazia onde antes voltava
    # cheia, a causa provável é esta — e é a política funcionando, não um
    # defeito.
    #
    # A checagem mora aqui, e não em cada rota, porque era assim que o buraco
    # nascia: 29 rotas chamando este mesmo `conectar()` e recebendo um papel que
    # ignora toda policy. Corrigir rota a rota deixaria a próxima de fora.
    try:
        import auth
        u = auth.USUARIO_DA_REQUISICAO.get()
        if u is not None:
            return auth.conectar_como(u)
    except ImportError:
        pass          # ambiente sem FastAPI (pipeline puro): segue no worker

    # A 7100 — MODO SESSAO, e nao a 7110 do doc 23.
    #
    # E divergencia assumida, decidida em 30/08/2026, e o motivo e velho: ETL faz
    # POUCAS conexoes com trabalho LONGO. Em modo transacao o Supavisor devolve o
    # backend a cada comando, e ali morrem tabela temporaria entre transacoes,
    # prepared statement e lock de sessao — que e exatamente o que um `COPY` de
    # 111 milhoes de linhas do CNEFE usa. O doc diz "toda aplicacao pela 7110";
    # a regra vale para aplicacao, e o pipeline e lote.
    # SEM QUEDA PARA A `A2L_DB_URL`, e isto foi medido em 31/08/2026.
    #
    # A queda parecia inofensiva: faltando a variavel do pipeline, usaria a da
    # API. So que o pipeline declara a identidade em escopo de SESSAO — ele roda
    # milhares de transacoes na mesma conexao, e escopo de transacao morreria no
    # primeiro commit.
    #
    # E GUC de sessao na 7110 VAZA. Medido: declarar `request.jwt.claim.sub` com
    # `set_config(..., false)` naquela porta suja o backend de forma PERMANENTE,
    # e toda conexao seguinte que pegar aquele backend do pool herda a empresa —
    # inclusive as do painel, de outros clientes. Dez conexoes novas, sem
    # declarar nada, responderam com a empresa do teste anterior.
    #
    # Uma variavel esquecida no `.env` transformaria isso num vazamento entre
    # clientes, calado. Melhor recusar a subir.
    dsn = (os.environ.get("A2L_PIPELINE_DB_URL") or "").strip()
    if dsn:
        con = psycopg2.connect(
            dsn, options=_opcoes(),
            connect_timeout=int(os.environ.get("PG_CONNECT_TIMEOUT", "20")))
        _assumir(con)
        return con

    raise RuntimeError(
        "A2L_PIPELINE_DB_URL nao esta no .env, e NAO ha queda para a A2L_DB_URL: "
        "o pipeline declara a identidade em escopo de sessao, e GUC de sessao na "
        "7110 vaza para a conexao seguinte do pool (medido). "
        "Ate 30/08/2026 a conexao vinha "
        "das I9_POSTGRES_*, que apontavam para uma maquina que nao existe mais. "
        "Cair em 'localhost' produziria um erro de conexao longe da causa — a "
        "causa e o .env.\n\n"
        "  A2L_PIPELINE_DB_URL=postgresql://app_user.a2l:<senha>@127.0.0.1:7100/a2l\n\n"
        "O `.a2l` no nome do usuario NAO e enfeite: o Supavisor exige o tenant "
        "embutido, e sem ele responde ENOIDENTIFIER.")


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None or v == "":
        return None
    try:
        return int("".join(ch for ch in str(v) if ch.isdigit() or ch == "-") or "0")
    except ValueError:
        return None


def _s(v):
    if v is None or v == "":
        return None
    return str(v)


_RE_CIDADE_UF = re.compile(r",\s*([^,\-–]+?)\s*-\s*([A-Z]{2})\s*(?:,|$)")


def _cidade_uf(*enderecos):
    """Extrai (cidade, uf) do padrão brasileiro '..., Cidade - UF, CEP'.
    Preenchido automaticamente em toda ingestão (colunas pois.cidade/uf)."""
    for e in enderecos:
        if not e:
            continue
        m = _RE_CIDADE_UF.search(str(e))
        if m:
            return m.group(1).strip()[:80], m.group(2)
    return None, None


def _horarios(h):
    if not h:
        return []
    if isinstance(h, list):
        return [(str(x.get("dia", "")), _s(x.get("horario")))
                for x in h if x and (x.get("dia") or x.get("horario"))]
    if isinstance(h, dict):
        return [(str(dia), _s(hor)) for dia, hor in h.items()]
    return []


def _endereco_pela_coordenada(r: dict):
    """A coordenada vira endereço com número, ou o ponto não entra.

    A busca no Maps nem sempre devolve endereço. Desde 27/08/2026 o trigger
    `poi_comparavel` recusa POI sem ele — um registro sem nome nem endereço tem
    teto de 1 ponto de evidência e nunca funde com ninguém.

    Sem esta ponte o INSERT levantaria `CheckViolation` e derrubaria a ingestão
    da SESSÃO INTEIRA, não só daquele ponto. Aqui a cascata é a mesma da
    descoberta por categoria — CNEFE primeiro (95% em 2 ms), Maps no resíduo.

    O município sai da CIDADE do registro, não da área: a ingestão roda ponto a
    ponto e o tile fotografa além da faixa desenhada, então o POI da cidade
    vizinha precisa ser resolvido contra o CNEFE DELA.
    """
    try:
        import endereco_reverso as rev
        import base_comum as _bc
    except ImportError:
        return None
    la = _f(r.get("maps_lat")) or _f(r.get("lat_origem")) or _f(r.get("lat"))
    lo = _f(r.get("maps_lng")) or _f(r.get("lng_origem")) or _f(r.get("lng"))
    if la is None or lo is None:
        return None

    cidade = _s(r.get("cidade")) or _cidade_uf(r.get("endereco_planilha"))[0]
    if not cidade:
        return None
    try:
        ref = _bc.conectar_referencia()
        try:
            c = ref.cursor()
            c.execute("""select cod_municipio from ibge_malha
                          where st_contains(geom, st_setsrid(st_point(%s,%s),4326))
                          limit 1""", (lo, la))
            achado = c.fetchone()
        finally:
            ref.close()
    except Exception:
        return None
    if not achado:
        return None
    return rev.endereco_de(la, lo, achado[0], nome=str(r.get("nome") or ""),
                           cidade=cidade, usar_maps=False)


def ingerir_registro(r: dict, poligono=None, conn=None) -> tuple:
    """
    Grava UM registro do pipeline no banco. Retorna (resultado, poi_id):
      ('inserido', id) · ('inserido_fora', id) · ('pulado', None)

    **Tudo que é achado é GRAVADO** — regra do usuário. O polígono deixou de ser
    um portão e virou uma ETIQUETA: quem cai fora dele entra no banco do mesmo
    jeito, identificado pela `cidade`/`uf` extraídas do endereço. O tile
    fotografa muito além da faixa desenhada (medido em Canoas: 57 POIs válidos
    fora contra 26 dentro), e jogar fora um POI já encontrado e já pago em tempo
    de busca é destruir trabalho: no dia em que aquela cidade for minerada, ele
    já está lá.

    Quem quiser o banco restrito à área tem o botão "Limpar banco fora da área",
    que é uma decisão explícita — o oposto de um descarte silencioso.
    """
    if not r.get("nome") or r.get("match_valido") is False or r.get("match_valido") is None:
        return ("pulado", None)

    la = _f(r.get("maps_lat"))
    lo = _f(r.get("maps_lng"))
    if la is None or lo is None:
        la, lo = _f(r.get("lat_origem")), _f(r.get("lng_origem"))
    # Guard Brasil (mesma regra do ingest.ts) — este SIM continua descartando:
    # coordenada fora do país é dado errado, não dado de outro lugar
    if la is not None and lo is not None and (la < -34 or la > 6 or lo < -74 or lo > -34):
        return ("pulado", None)
    fora_da_area = bool(poligono and la is not None
                        and not area_utils.ponto_no_poligono(la, lo, poligono))

    fechar = False
    if conn is None:
        conn = conectar()
        fechar = True
    try:
        with _LOCK, conn, conn.cursor() as cur:
            ids = []
            if r.get("place_id"):
                cur.execute("SELECT id FROM pois WHERE place_id = %s", (r["place_id"],))
                ids = [row[0] for row in cur.fetchall()]
            elif r.get("nome_planilha"):
                # Sem place_id (recuperados do Gemini) → deduplica pela ORIGEM
                # (fonte+sessao+nome da planilha), senão cada rerun re-insere o mesmo POI.
                cur.execute(
                    """SELECT id FROM pois WHERE place_id IS NULL
                       AND fonte = %s AND sessao IS NOT DISTINCT FROM %s AND nome_original = %s""",
                    (r.get("fonte") or "desconhecido", _s(r.get("sessao")), str(r["nome_planilha"])))
                ids = [row[0] for row in cur.fetchall()]
            elif la is not None and lo is not None:
                # ÚLTIMO RECURSO: nome + coordenada.
                #
                # POI vindo da captura+OCR não tem `place_id` nem `nome_planilha`
                # — as duas chaves acima. Sem uma terceira, TODA reingestão dele
                # inseria de novo: medido em 12/08/2026, o banco tinha 45 grupos
                # de duplicatas assim, alguns com 6 cópias do mesmo ponto, e uma
                # única passada de enriquecimento criou mais 4.
                #
                # 5 casas decimais ≈ 1 m. É apertado de propósito: dois negócios
                # diferentes na mesma porta têm nomes diferentes, então o par
                # nome+posição só colide quando é de fato o mesmo POI.
                cur.execute(
                    """SELECT id FROM pois
                        WHERE place_id IS NULL AND nome = %s
                          AND abs(COALESCE(maps_lat, lat_origem) - %s) < 0.00001
                          AND abs(COALESCE(maps_lng, lng_origem) - %s) < 0.00001
                        ORDER BY id""",
                    (str(r["nome"]), la, lo))
                ids = [row[0] for row in cur.fetchall()]
            if ids:
                # MERGE não-destrutivo: um dado NOVO vazio nunca apaga um dado BOM
                # já salvo. Vale p/ enriquecimento (CNPJ/streetview/web) E p/ os campos
                # operacionais — ex.: reabrir o Maps p/ pegar o endereço correto não pode
                # zerar o telefone que a web havia achado. Coluna do banco = chave do reg,
                # exceto endereço/telefone-da-planilha (esses vêm de nome_planilha etc).
                _MERGE = ("cnpj", "cnpj_conf", "razao_social", "nome_fantasia", "natureza_juridica",
                          "cnae", "situacao_cadastral", "socios", "instagram", "email",
                          "facebook",
                          "resumo_avaliacoes", "streetview_path", "fontes_web",
                          "telefone", "website", "categoria", "status_horario",
                          "avaliacao", "total_avaliacoes", "preco_medio", "plus_code",
                          "endereco", "endereco_fonte", "cidade", "uf",
                          # Precisao entra no merge pelo mesmo motivo dos
                          # demais: uma etapa que nao sabe de onde veio a
                          # coordenada nao pode apagar a declaracao de quem
                          # sabia. Enriquecer telefone nao rebaixa o ponto.
                          "coord_precisao", "coord_fonte", "coord_incerteza_m")
                cur.execute(f"SELECT {', '.join(_MERGE)} FROM pois WHERE id = %s", (ids[0],))
                antigo = cur.fetchone()
                if antigo:
                    for campo, valor in zip(_MERGE, antigo):
                        if valor not in (None, "", 0) and r.get(campo) in (None, "", 0):
                            r[campo] = valor
                # PRESERVA comentários/horários já coletados se o registro novo
                # não os traz (uma etapa de enriquecimento que só melhora texto NÃO pode
                # apagar o que o Maps já tinha capturado).
                #
                # Foto não precisa mais deste resgate: ela deixou de ser apagada.
                keep = ids[0]
                if not (r.get("comentarios") or []):
                    cur.execute("SELECT autor, nota, texto, data FROM comentarios WHERE poi_id = %s ORDER BY id", (keep,))
                    cs = cur.fetchall()
                    if cs:
                        r["comentarios"] = [{"autor": a, "nota": n, "texto": t, "data": d} for a, n, t, d in cs]
                if not r.get("horarios"):
                    cur.execute("SELECT dia, horario FROM horario_funcionamento WHERE poi_id = %s ORDER BY id", (keep,))
                    hs = cur.fetchall()
                    if hs:
                        r["horarios"] = [{"dia": d, "horario": h} for d, h in hs]
                # DUPLICATAS: quando o mesmo place_id aparece em mais de uma
                # linha, uma delas fica e as outras vão embora — mas os filhos
                # CAROS mudam de dono antes, senão a cascata os leva junto.
                extras = ids[1:]
                if extras:
                    for tabela in ("streetview_imgs", "fachada_anotacao"):
                        try:
                            cur.execute(
                                f"""UPDATE {tabela} SET poi_id = %s
                                     WHERE poi_id = ANY(%s)
                                       AND NOT EXISTS (SELECT 1 FROM {tabela} z
                                                        WHERE z.poi_id = %s)""",
                                (keep, extras, keep))
                        except Exception:
                            pass          # tabela pode não existir ainda
                    # A FOTO DA CÓPIA também muda de dono. Entrou aqui em
                    # 13/08/2026: até então só fachada e anotação eram resgatadas,
                    # e a foto BAIXADA da duplicata ia na cascata. Custou 11 fotos,
                    # descobertas ao conferir o notebook contra o i9 antes de
                    # aposentá-lo — o byte seguia no Storage, inalcançável, porque
                    # a linha que sabia o caminho tinha sumido.
                    cur.execute("""UPDATE images_urls i SET poi_id = %s
                                    WHERE i.poi_id = ANY(%s)
                                      AND NOT EXISTS (SELECT 1 FROM images_urls z
                                                       WHERE z.poi_id = %s AND z.url = i.url)""",
                                (keep, extras, keep))
                    cur.execute("DELETE FROM pois WHERE id = ANY(%s)", (extras,))

                # Filhos REFEITOS pela própria busca: apagados e reinseridos logo
                # abaixo. `images_urls` NÃO está mais aqui, pelo mesmo motivo de
                # fachada e anotação: a linha da foto guarda o `storage_path`, e
                # apagá-la para reinserir a partir da url descartaria o byte já
                # baixado e deixaria o objeto órfão no Storage. Foto passou a
                # ACUMULAR — url nova entra, url já conhecida fica como está.
                cur.execute("DELETE FROM comentarios WHERE poi_id = %s", (keep,))
                cur.execute("DELETE FROM horario_funcionamento WHERE poi_id = %s", (keep,))

            # ── A linha do POI: ATUALIZADA quando já existe, nunca recriada ──
            #
            # Até 12/08/2026 este caminho fazia `DELETE FROM pois` + `INSERT`. Com
            # `fachada_anotacao` e `streetview_imgs` em `ON DELETE CASCADE`, isso
            # significava que **reimportar uma planilha destruía leitura de fachada
            # já paga** — US$ 0,017 por POI, mais a captura, mais a passagem pelo
            # validador. Medido no dia: 19.838 POIs com dado insubstituível eram
            # alcançáveis por uma reimportação.
            #
            # O `id` preservado é o que mantém os filhos ligados. É por isso que
            # aqui é UPDATE, e não um DELETE mais esperto.

            # ── SEM ENDEREÇO O POI NÃO ENTRA, E ESTA É A ÚLTIMA PORTA ────────
            #
            # O trigger `poi_comparavel` recusa POI sem endereço desde
            # 27/08/2026: um registro sem nome nem endereço tem teto de 1 ponto
            # de evidência e nunca funde com ninguém.
            #
            # A busca no Maps nem sempre devolve endereço — e quando não devolve,
            # este INSERT levantaria `CheckViolation` e derrubaria a ingestão da
            # sessão inteira, não só daquele ponto. Aqui a coordenada vira
            # endereço antes, pela mesma cascata da descoberta por categoria:
            # CNEFE (95% em 2 ms) e Maps no resíduo.
            #
            # Quem nem assim obtiver endereço é PULADO com o motivo dito. Perder
            # um ponto incomparável é barato; perder a sessão é caro.
            if not _s(r.get("endereco")):
                achado = _endereco_pela_coordenada(r)
                if not achado:
                    return ("pulado", None)
                r["endereco"] = achado["endereco"]

            _COLS = ("fonte", "sessao", "nome", "categoria", "endereco", "telefone", "website",
                     "avaliacao", "total_avaliacoes", "plus_code", "status_horario",
                     "lat_origem", "lng_origem", "maps_lat", "maps_lng", "maps_url", "place_id",
                     "status", "distancia_m", "similaridade", "match_valido", "ocr_texto",
                     "nome_original", "endereco_original", "preco_medio", "fonte_dado", "ia_resposta",
                     "cnpj", "razao_social", "nome_fantasia", "natureza_juridica", "cnae",
                     "situacao_cadastral", "socios", "instagram", "email", "resumo_avaliacoes",
                     "streetview_path", "fontes_web", "endereco_fonte", "cidade", "uf",
                     "cnpj_conf", "facebook",
                     # A PRECISAO DA COORDENADA, declarada por quem a produziu.
                     # Sem isto o mapa mostra um centroide de quadra e um pin de
                     # porta como pontos iguais, e quem vai a campo trata os dois
                     # com a mesma confianca. Ver a migracao 0028.
                     "coord_precisao", "coord_fonte", "coord_incerteza_m")
            _VALORES = (
                    r.get("fonte") or "desconhecido", _s(r.get("sessao")), str(r["nome"]),
                    _s(r.get("categoria")), _s(r.get("endereco")), _s(r.get("telefone")),
                    _s(r.get("website")), _s(r.get("avaliacao")), _i(r.get("total_avaliacoes")),
                    _s(r.get("plus_code")), _s(r.get("status_horario")),
                    _f(r.get("lat_origem") if r.get("lat_origem") is not None else r.get("lat")),
                    _f(r.get("lng_origem") if r.get("lng_origem") is not None else r.get("lng")),
                    _f(r.get("maps_lat")), _f(r.get("maps_lng")), _s(r.get("maps_url")),
                    _s(r.get("place_id")), _s(r.get("status")), _f(r.get("distancia_m")),
                    _f(r.get("similaridade")), r.get("match_valido"), _s(r.get("ocr_texto")),
                    _s(r.get("nome_planilha")), _s(r.get("endereco_planilha")),
                    _s(r.get("preco_medio")), _s(r.get("fonte_dado")), _s(r.get("ia_resposta")),
                    _s(r.get("cnpj")), _s(r.get("razao_social")), _s(r.get("nome_fantasia")),
                    _s(r.get("natureza_juridica")), _s(r.get("cnae")),
                    _s(r.get("situacao_cadastral")), _s(r.get("socios")), _s(r.get("instagram")),
                    _s(r.get("email")), _s(r.get("resumo_avaliacoes")),
                    _s(r.get("streetview_path")), _s(r.get("fontes_web")),
                    _s(r.get("endereco_fonte")),
                    # cidade/uf: usa o que o merge preservou ou extrai do endereço
                    _s(r.get("cidade")) or _cidade_uf(r.get("endereco"), r.get("endereco_planilha"))[0],
                    _s(r.get("uf")) or _cidade_uf(r.get("endereco"), r.get("endereco_planilha"))[1],
                    _s(r.get("cnpj_conf")), _s(r.get("facebook")),
                    _s(r.get("coord_precisao")), _s(r.get("coord_fonte")),
                    _i(r.get("coord_incerteza_m")),
            )

            if ids:
                sets = ", ".join(f"{c} = %s" for c in _COLS)
                cur.execute(f"UPDATE pois SET {sets} WHERE id = %s RETURNING id",
                            (*_VALORES, keep))
            else:
                cur.execute(
                    f"INSERT INTO pois ({', '.join(_COLS)}) "
                    f"VALUES ({', '.join(['%s'] * len(_COLS))}) RETURNING id",
                    _VALORES)
            poi_id = cur.fetchone()[0]

            fotos = [(poi_id, str(u), k) for k, u in enumerate(r.get("fotos") or []) if u]
            if fotos:
                # Só a url que ainda NÃO está lá. A linha existente carrega
                # `storage_path`, `bytes_tam` e `content_type`: reinseri-la pela
                # url perderia o byte já baixado e mandaria a próxima etapa
                # baixar de novo o que já estava pago.
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO images_urls (poi_id, url, ordem)
                       SELECT v.poi_id, v.url, v.ordem
                         FROM (VALUES %s) AS v(poi_id, url, ordem)
                        WHERE NOT EXISTS (SELECT 1 FROM images_urls z
                                           WHERE z.poi_id = v.poi_id AND z.url = v.url)""",
                    fotos)

            coments = [(poi_id, _s(c.get("autor")), _f(c.get("nota")), _s(c.get("texto")), _s(c.get("data")))
                       for c in (r.get("comentarios") or []) if isinstance(c, dict)]
            if coments:
                psycopg2.extras.execute_values(
                    cur, "INSERT INTO comentarios (poi_id, autor, nota, texto, data) VALUES %s", coments)

            hors = [(poi_id, dia, hor) for dia, hor in _horarios(r.get("horarios"))]
            if hors:
                psycopg2.extras.execute_values(
                    cur, "INSERT INTO horario_funcionamento (poi_id, dia, horario) VALUES %s", hors)

        # gravado dos dois jeitos; o chamador só precisa saber se está no foco
        return ("inserido_fora" if fora_da_area else "inserido", poi_id)
    finally:
        if fechar:
            conn.close()


def limpar_fora_da_area(poligono, dry_run: bool = True) -> dict:
    """
    Remove do banco os POIs cuja coordenada efetiva cai FORA do polígono.
    dry_run=True só conta (não deleta). POIs sem coordenada são preservados.
    """
    conn = conectar()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT id, COALESCE(maps_lat, lat_origem), COALESCE(maps_lng, lng_origem)
                           FROM pois""")
            fora, dentro, sem_coord = [], 0, 0
            for pid, la, lo in cur.fetchall():
                if la is None or lo is None:
                    sem_coord += 1
                elif area_utils.ponto_no_poligono(la, lo, poligono):
                    dentro += 1
                else:
                    fora.append(pid)

            if not dry_run and fora:
                cur.execute("DELETE FROM images_urls WHERE poi_id = ANY(%s)", (fora,))
                cur.execute("DELETE FROM comentarios WHERE poi_id = ANY(%s)", (fora,))
                cur.execute("DELETE FROM horario_funcionamento WHERE poi_id = ANY(%s)", (fora,))
                cur.execute("DELETE FROM pois WHERE id = ANY(%s)", (fora,))

        return {"fora": len(fora), "dentro": dentro, "sem_coord": sem_coord,
                "removidos": 0 if dry_run else len(fora), "dry_run": dry_run}
    finally:
        conn.close()
