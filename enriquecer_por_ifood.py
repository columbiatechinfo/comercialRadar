# -*- coding: utf-8 -*-
"""enriquecer_por_ifood.py — o iFood ao contrário: do POI para a loja.

A INVERSÃO, e por que ela resolve mais do que o bloqueio

Até aqui o iFood era FONTE DE DESCOBERTA: um navegador abria o feed de cada
bairro, colhia a lista de lojas, e só então o CNPJ era buscado. Essa metade
morreu em 26/08/2026 — o Cloudflare passou de desafio automático para Turnstile
INTERATIVO ("Confirme que é humano"), e navegador automatizado não clica.
Medido: 1.598 lojas em 25/08, zero em 26/08, no notebook e no i9, com proxy e
sem proxy.

Aqui o sentido é o contrário, e a descoberta deixa de ser necessária:

    POI que já existe   →  busca web "nome cidade uf ifood"
    link do iFood       →  o id está DENTRO da URL
    /v1/merchants/{id}/extra  →  CNPJ, rua, número, CEP, coordenada

Nenhum passo abre o iFood. O `/extra` responde 200 sem navegador — medido:
1.598 respostas, zero falhas, CNPJ em 99,7%.

O GANHO NÃO É SÓ CONTORNAR O BLOQUEIO. Antes o iFood só enriquecia o que ele
mesmo tinha descoberto; agora alcança qualquer POI da base, venha do Overture,
do OSM ou da captura do Maps.

O QUE ISTO NÃO É

Não é descoberta. Um POI que não está no iFood continua não estando, e isso
aparece como `sem_link` — que quer dizer "não achei", nunca "não existe".

A CONFERÊNCIA É OBRIGATÓRIA, e é o que separa isto de inventar dado

"Farmácia São João" existe em dezenas de cidades. O buscador devolve alguma, e
gravar o CNPJ dela no POI errado seria pior que não gravar nada — vira dado
falso com aparência de verificado. Por isso todo par passa por `_confere`: a
coordenada que o `/extra` devolve tem de estar perto da do POI. Não estando, o
par é DESCARTADO e contado como `longe`.

USO
    python enriquecer_por_ifood.py --area area_atual --limite 10
    python enriquecer_por_ifood.py --cidade Canoas --aplicar
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import urllib.error
import urllib.request

import config  # noqa: F401
import area_utils as au
import base_comum as bc
import evidencia as ev

# O id da loja vive na própria URL: /delivery/<cidade-uf>/<slug>/<uuid>
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
EXTRA = "https://marketplace.ifood.com.br/v1/merchants/%s/extra"
CABECALHO = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36")}

# 250 m. Não é o raio de fusão (20 m) e não deveria ser.
#
# Aqui não se está decidindo se dois registros são o mesmo ponto — isso já está
# decidido pelo nome que foi buscado. Está-se conferindo se o buscador devolveu
# a loja da cidade certa ou a homônima de outro estado. O iFood publica a
# coordenada do estabelecimento e o POI tem a do Maps ou da base pública; entre
# elas há divergência honesta de dezenas de metros. Apertar para 20 m
# descartaria par legítimo; afrouxar para 2 km deixaria passar o bairro errado.
RAIO_CONFERE_M = 250.0

# E 2 km QUANDO O NOME BATE EXATAMENTE.
#
# A trava fixa de 250 m descartava acerto legítimo. Medido em 26/08: "Santo Açaí
# Oficial" casou com uma loja chamada, literalmente, "Santo Açaí Oficial" — e foi
# recusada por estar a 2.147 m. A distância não vinha de a loja ser outra: vinha
# da coordenada do POI, que na base estadual erra por quilômetros.
#
# Nome idêntico é evidência MUITO mais forte que proximidade. Quando ele bate,
# a distância deixa de ser prova e vira só um guarda-corpo contra a homônima de
# outra cidade — e para isso 2 km bastam, porque cidade vizinha fica bem além.
#
# Quando o nome NÃO bate, os 250 m continuam valendo: aí a proximidade é a única
# coisa segurando o par, e afrouxá-la deixaria passar o vizinho de ramo.
RAIO_NOME_EXATO_M = 2000.0
SIM_NOME_EXATO = 0.85


# ─── quem pode estar no iFood ───────────────────────────────────────────────
#
# FILTRO PERIGOSO, E O DONO DO PRODUTO DISSE ISSO ANTES DE MIM: o iFood deixou
# de ser só comida. Hoje tem Mercados, Bebidas, Farmácias, Pets e Shopping.
#
# Então a lista abaixo é generosa de propósito, e erra para o lado de INCLUIR.
# Uma busca a mais custa uma consulta; uma loja de fora do filtro custa um CNPJ
# que nunca será colhido, e ninguém saberá que faltou.
#
# O que fica de fora é o que não vende ao consumidor por entrega: oficina,
# imobiliária, escritório, indústria, escola, igreja, estacionamento, via.
RAMOS = (
    # comida preparada
    "restaurante", "lanchonete", "pizzaria", "hamburgue", "burger", "lanche",
    "churrascaria", "cafeteria", "café", "cafe", "bar", "pub", "sorveteria",
    "açaí", "acai", "doceria", "confeitaria", "padaria", "panificadora",
    "pastelaria", "esfiha", "sushi", "japones", "japonês", "chines", "chinês",
    "italian", "mexican", "árabe", "arabe", "marmit", "buffet", "delivery",
    "food", "comida", "pizza", "salgad", "espetinho", "xis", "cachorro quente",
    "hot dog", "dogão", "dogao", "creperia", "tapioca", "temaki", "yakisoba",
    "self service", "restaurant",
    # mercado e bebida
    "mercado", "mercearia", "supermercado", "minimercado", "hortifruti",
    "quitanda", "empório", "emporio", "adega", "distribuidora de bebidas",
    "bebidas", "conveniência", "conveniencia", "açougue", "acougue",
    "peixaria", "frios", "laticínio", "laticinio",
    # farmácia e pet
    "farmácia", "farmacia", "drogaria", "manipulaç", "pet shop", "petshop",
    "pet", "veterinár", "veterinar", "agropecuária", "agropecuaria",
    # o "shopping" do iFood
    "floricultura", "flores", "presente", "tabacaria", "loja de conveniência",
    "suplemento", "produtos naturais", "natural", "cosmétic", "cosmetic",
    "perfumaria",
)


def _pode_estar(categoria: str, nome: str) -> bool:
    """O filtro olha categoria E nome.

    A categoria da base pública é grosseira: "compras/comércio" cobre de
    joalheria a hortifruti. O nome costuma dizer mais — "Pizzaria do Zé" entra
    mesmo com a categoria vaga, e é assim que o filtro erra para o lado certo.
    """
    alvo = ev._sem_acento((categoria or "") + " " + (nome or ""))
    return any(ev._sem_acento(r) in alvo for r in RAMOS)


# ─── a corrente ─────────────────────────────────────────────────────────────

def id_do_link(url: str) -> str:
    """O uuid da loja, quando a URL é de uma loja do iFood.

    QUALQUER SUBDOMÍNIO SERVE. `madero.ifood.com.br/delivery/...` é loja tão
    válida quanto `www.ifood.com.br/delivery/...` — grandes redes têm subdomínio
    próprio. Exigir `www` descartava essas, e foi o que aconteceu com o Madero
    na medição de 26/08.
    """
    if ".ifood.com.br" not in url or "/delivery/" not in url:
        return ""
    m = UUID.search(url)
    return m.group(0) if m else ""


def _desembrulhar(url: str) -> str:
    """A URL real dentro do redirecionador do buscador.

    O Bing devolve `bing.com/ck/a?...&u=<base64>` e o Yahoo `.../RU=<encoded>/`.
    Sem desembrulhar, uma busca que ACHOU a loja conta como `sem_link` — medido:
    "Sonho Meu Doceria" trouxe 9 resultados, todos embrulhados, e o par foi dado
    como não encontrado.
    """
    import base64
    from urllib.parse import unquote, urlparse, parse_qs

    if "search.yahoo.com" in url:
        m = re.search(r"/RU=([^/]+)/", url)
        return unquote(m.group(1)) if m else url
    if "bing.com/ck/" in url:
        q = parse_qs(urlparse(url).query)
        bruto = (q.get("u") or [""])[0]
        # o Bing prefixa com "a1" e usa base64 url-safe sem padding
        if bruto.startswith("a1"):
            bruto = bruto[2:]
        try:
            faltando = "=" * (-len(bruto) % 4)
            return base64.urlsafe_b64decode(bruto + faltando).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — embrulho ilegível é "não veio"
            return url
    return url


def candidatos_de(links) -> list:
    """TODOS os ids de loja nos resultados, desembrulhados e sem repetir.

    O slug e um sinal FRACO — medido em 26/08: "Sushi Arte" acertou com 0,20 de
    semelhanca de slug (quase sorte) e "Tempero do Cheff" escolheu por slug uma
    loja a 1.088 km. Escolher SO por ele erra nos dois sentidos.

    Quem decide de verdade e a DISTANCIA, e ela so existe depois do `/extra` —
    que custa uma chamada HTTP barata, sem navegador. Entao aqui nao se escolhe:
    junta-se os candidatos, e a escolha vira medicao la na frente.
    """
    vistos, saida = set(), []
    for url, texto in (links or []):
        real = _desembrulhar(url)
        mid = id_do_link(real)
        if mid and mid not in vistos:
            vistos.add(mid)
            m = re.search(r"/delivery/[^/]+/([^/]+)/", real)
            saida.append((mid, (m.group(1) if m else "").replace("-", " ")))

        # O TEXTO DO RESULTADO TAMBÉM CARREGA ID, e eu o descartava.
        #
        # O buscador mostra a URL da loja no corpo do resultado, não só no
        # `href` — e às vezes o `href` é do agregador enquanto o texto traz o
        # link do iFood inteiro. Ler só o `href` jogava fora o id que estava ali
        # na tela, e o par virava `sem_link`.
        #
        # Vale para qualquer motor da cascata: o snippet é texto, e o id é um
        # padrão fixo dentro dele. Custa uma regex sobre o que já foi baixado.
        for achado in UUID.findall(texto or ""):
            if achado in vistos:
                continue
            # só conta quando o texto REALMENTE fala do iFood; um uuid solto
            # pode ser de qualquer outro serviço
            if "ifood" not in (texto or "").lower():
                continue
            vistos.add(achado)
            saida.append((achado, ""))
    return saida


def escolher_link(poi: dict, links) -> tuple:
    """O link da loja MAIS PARECIDA com o POI — não o primeiro que aparecer.

    ISTO ERA O MAIOR DEFEITO, e a medição de 26/08 o expôs: buscar "Sushi Arte"
    devolvia sete lojas do iFood, e o código pegava a primeira —
    `daniisushi-harmonia`. A trava dos 250 m então descartava o par, e o placar
    dizia "longe" quando o certo estava no terceiro resultado.

    O slug da URL carrega o nome da loja (`/delivery/canoas-rs/<slug>/<uuid>`),
    então dá para comparar ANTES de gastar uma chamada ao `/extra`. Devolve
    `(merchant_id, semelhança)`; sem candidato razoável, `("", 0)`.
    """
    melhor, melhor_sem = "", 0.0
    for url, _texto in (links or []):
        real = _desembrulhar(url)
        mid = id_do_link(real)
        if not mid:
            continue
        # o slug fica entre a cidade e o uuid
        m = re.search(r"/delivery/[^/]+/([^/]+)/", real)
        slug = (m.group(1) if m else "").replace("-", " ")
        sem = ev.semelhanca_nome(poi["nome"], slug)
        if sem > melhor_sem:
            melhor, melhor_sem = mid, sem
    # Um token em comum já basta para tentar: o `/extra` e a trava de distância
    # decidem depois. Exigir mais aqui descartaria "Manga Rosa" x "manga rosa
    # modas", que é o mesmo negócio.
    return (melhor, melhor_sem) if melhor_sem > 0 else ("", 0.0)


def detalhe(merchant_id: str, timeout: int = 20) -> dict | None:
    """`/extra` — CNPJ, endereço com número, CEP e coordenada. Sem navegador."""
    req = urllib.request.Request(EXTRA % merchant_id, headers=CABECALHO)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            json.JSONDecodeError):
        return None


def _raio_para(poi: dict, det: dict) -> float:
    """O raio aceitável para ESTE par — depende de quanto o nome já provou."""
    sem = ev.semelhanca_nome(poi.get("nome", ""), det.get("name") or "")
    return RAIO_NOME_EXATO_M if sem >= SIM_NOME_EXATO else RAIO_CONFERE_M


def _confere(poi: dict, det: dict) -> tuple:
    """`(ok, distancia_m)` — o que veio é mesmo a loja deste POI?"""
    end = det.get("address") or {}
    la, lo = end.get("latitude"), end.get("longitude")
    if la is None or lo is None:
        return False, float("inf")
    d = ev.distancia_m(poi["lat"], poi["lng"], float(la), float(lo))
    return d <= _raio_para(poi, det), d


def campos(det: dict) -> dict:
    """O que interessa da resposta do iFood, achatado."""
    docs = det.get("documents") or {}
    end = det.get("address") or {}
    cnpj = ((docs.get("CNPJ") or {}).get("value") or "").strip() or None
    ddd = str(det.get("areaCode") or "").strip()
    fone = str(det.get("phoneIf") or "").strip()
    return {
        "merchant_id": det.get("id"),
        "cnpj": cnpj,
        "telefone": (ddd + fone) if (ddd and fone) else None,
        "rua": end.get("streetName"), "numero": end.get("streetNumber"),
        "bairro": end.get("district"), "cep": end.get("zipCode"),
        "cidade": end.get("city"), "uf": end.get("state"),
        "lat": end.get("latitude"), "lng": end.get("longitude"),
        "categoria": (det.get("mainCategory") or {}).get("name")
        if isinstance(det.get("mainCategory"), dict) else det.get("mainCategory"),
    }


PROMPT_LOJA = """Você decide QUAL loja do iFood corresponde a um estabelecimento, ou se NENHUMA corresponde.

Recebe um ESTABELECIMENTO (nome, categoria, endereço, cidade) e uma lista de LOJAS candidatas do iFood, cada uma com nome, endereço completo e a distância até o estabelecimento.

Responda o NÚMERO da loja correta, ou 0 se nenhuma for.

O QUE PESA, nesta ordem:
  1. NOME. "Santo Açaí Oficial" e "Santo Açaí" são a mesma loja; "Santo Açaí" e "La Casa de Açaí" NÃO são, ainda que ambas vendam açaí e estejam perto.
  2. ENDEREÇO. Mesma rua e número é confirmação forte.
  3. DISTÂNCIA. Ajuda a desempatar, mas NÃO decide sozinha: a loja certa pode estar 300 m longe (coordenada imprecisa) e a errada a 50 m.

RESPONDA 0 QUANDO:
  - nenhum nome corresponde de verdade (só o ramo em comum não basta)
  - todas são de rede diferente, mesmo que o produto seja igual
  - você ficaria em dúvida — errar aqui grava CNPJ de outra empresa no ponto

Responda APENAS um array JSON, um objeto por caso, na MESMA ORDEM:
  "escolha": <número da loja, ou 0>
  "motivo": no maximo 12 palavras

CASOS:
"""


def _descrever_caso(poi: dict, cands: list) -> str:
    """Um estabelecimento e suas lojas candidatas, numeradas."""
    linhas = [f'ESTABELECIMENTO: "{poi["nome"]}"']
    for rot, val in (("categoria", poi.get("categoria")),
                     ("cidade", poi.get("cidade"))):
        if val:
            linhas.append(f"   {rot}: {val}")
    linhas.append("LOJAS DO IFOOD:")
    for i, (det, d) in enumerate(cands, 1):
        end = det.get("address") or {}
        partes = [x for x in (end.get("streetName"), end.get("streetNumber"),
                              end.get("district")) if x]
        linhas.append(f'  {i}. "{det.get("name") or "?"}" · '
                      f'{", ".join(partes) or "sem endereço"} · a {d:.0f} m')
    return "\n".join(linhas)


def _chamar_ia(lote: list) -> list:
    """Manda o lote à Spark e devolve as escolhas."""
    import json as _json
    import urllib.request as _url
    from segmentar_endereco import SPARK, MODELO, _conferir_endpoint, _extrair_json

    _conferir_endpoint(SPARK)
    texto = "\n\n".join(f"{i + 1}.\n{_descrever_caso(p, c)}"
                        for i, (p, c) in enumerate(lote))
    corpo = {"model": MODELO,
             "messages": [{"role": "user", "content": PROMPT_LOJA + texto}],
             "temperature": 0, "max_tokens": 200 * len(lote) + 400}
    req = _url.Request(f"{SPARK}/chat/completions", method="POST",
                       data=_json.dumps(corpo).encode("utf-8"),
                       headers={"Content-Type": "application/json"})
    with _url.urlopen(req, timeout=300) as r:
        d = _json.loads(r.read())
    txt = (d["choices"][0]["message"].get("content") or "").strip()
    # UM CASO SÓ VOLTA COMO OBJETO, NÃO COMO ARRAY.
    #
    # O `_extrair_json` exige array e levanta `ValueError` no objeto solto — e o
    # lote inteiro virava "a IA recusou". Medido em 26/08: ela respondeu
    # `{"escolha": 1, "motivo": "nome exato e mesmo estabelecimento"}`, que é o
    # ACERTO, e o meu parser transformou isso em recusa. Seis casos de seis.
    #
    # Envolver o objeto num array é mais honesto que pedir ao modelo que sempre
    # devolva array: a resposta dele estava certa; quem lia é que era estreito.
    try:
        return _extrair_json(txt)
    except ValueError:
        pass
    # A RESPOSTA VEM EM TRÊS FORMATOS, e os três são legítimos.
    #
    # Com um caso só, um objeto solto. Com vários, às vezes um array (que o
    # `_extrair_json` já pega) e às vezes UM OBJETO POR LINHA, sem array em
    # volta. Recortar do primeiro `{` ao último `}` funciona no primeiro caso e
    # quebra no terceiro com "Extra data" — foi o que zerou o placar em 26/08.
    #
    # Ler objeto a objeto atende os três, e um objeto ilegível no meio custa só
    # aquele caso, não o lote inteiro.
    achados = []
    for m in re.finditer(r"\{[^{}]*\}", txt):
        try:
            achados.append(_json.loads(m.group(0)))
        except ValueError:
            continue
    if not achados:
        raise ValueError(f"nao consegui ler a resposta: {txt[:160]!r}")
    return achados


def julgar_lojas(casos: list, lote: int = 4) -> dict:
    """`{indice_do_caso: (det_escolhido, distancia)}` — ou ausente se NENHUMA.

    POR QUE A IA ENTRA AQUI, e a medição de 26/08 é o argumento.

    O slug é sinal fraco: "Sushi Arte" acertou com 0,20 de semelhança (quase
    sorte) e "Tempero do Cheff" escolheu por slug uma loja a 1.088 km. A
    distância também não basta sozinha: entre seis açaís de Canoas, o mais
    PRÓXIMO do POI era "La Casa de Açaí" — e o certo era "Santo Açaí", mais
    longe. Escolher pelo mínimo geográfico trocou um acerto por um erro.

    Decidir "esta loja é este estabelecimento?" é leitura semântica de nome e
    endereço — a mesma classe de julgamento que a Spark já faz na fusão de POIs,
    e pela mesma régua do projeto: segmentar é leitura (IA), canonizar é prova
    (skill), julgar identidade sem prova é leitura de novo.

    `escolha: 0` é resposta legítima e obrigatória: gravar o CNPJ da loja errada
    é pior que não gravar nada — vira dado falso com aparência de verificado.
    """
    from concurrent.futures import ThreadPoolExecutor
    import json as _json
    import urllib.error as _err

    if not casos:
        return {}
    lotes = [casos[k:k + lote] for k in range(0, len(casos), lote)]
    fora: dict = {}

    def _um(bloco):
        idxs = [i for i, _, _ in bloco]
        entrada = [(p, c) for _, p, c in bloco]
        try:
            lidas = _chamar_ia(entrada)
        except Exception as erro:  # noqa: BLE001
            # LOTE QUE FALHOU FICA SEM ESCOLHA, e isso e' diferente de "nenhuma
            # serve": o par nao foi julgado.
            #
            # E O ERRO E' DITO. A primeira versao engolia a excecao em silencio,
            # e uma falha DENTRO das threads virou "a IA recusou 6 de 6" — eu
            # cheguei a culpar o modelo por um defeito meu. Erro escondido em
            # thread e' o mais caro de achar, porque some sem deixar rastro.
            print(f"    ⚠️  lote nao julgado: {type(erro).__name__}: "
                  f"{str(erro)[:120]}", flush=True)
            return {}
        saida = {}
        for k, idx in enumerate(idxs):
            d = lidas[k] if k < len(lidas) and isinstance(lidas[k], dict) else {}
            try:
                escolha = int(d.get("escolha") or 0)
            except (TypeError, ValueError):
                escolha = 0
            cands = entrada[k][1]
            if 1 <= escolha <= len(cands):
                saida[idx] = cands[escolha - 1]
        return saida

    with ThreadPoolExecutor(max_workers=16) as pool:
        for parcial in pool.map(_um, lotes):
            fora.update(parcial)
    return fora


SQL = """
select p.id, p.nome, p.categoria, p.maps_lat, p.maps_lng, p.cidade, p.uf
  from pois p
 where p.maps_lat is not null and p.maps_lng is not null
   and coalesce(p.nome, '') <> ''
   and p.cnpj is null
   and p.fundido_em is null
   and p.id_empresa = core.empresa_atual()
   and (%(cidade)s = '' or upper(translate(coalesce(p.cidade,''), %(ac)s, %(li)s))
                         = upper(translate(%(cidade)s, %(ac)s, %(li)s)))
"""

_ACENTOS = "áàâãéêíóôõúüçÁÀÂÃÉÊÍÓÔÕÚÜÇ"
_LISOS = "aaaaeeiooouucAAAAEEIOOOUUC"


def candidatos(cur, cidade: str, poligono=None, limite: int = 0) -> list:
    par = {"cidade": cidade, "ac": _ACENTOS, "li": _LISOS}
    sql = SQL
    if poligono:
        s, n, o, l = au.bbox(poligono)
        sql += (" and p.maps_lat between %(s)s and %(n)s"
                " and p.maps_lng between %(o)s and %(l)s")
        par.update({"s": s, "n": n, "o": o, "l": l})
    cur.execute(sql, par)
    saida = []
    for pid, nome, cat, la, lo, cid, uf in cur.fetchall():
        if poligono and not au.ponto_no_poligono(float(la), float(lo), poligono):
            continue
        if not _pode_estar(cat, nome):
            continue
        saida.append({"id": pid, "nome": nome, "categoria": cat or "",
                      "lat": float(la), "lng": float(lo),
                      "cidade": cid or "", "uf": uf or ""})
        if limite and len(saida) >= limite:
            break
    return saida


def consulta(poi: dict) -> str:
    """`site:ifood.com.br` porque o alvo é UM link, não uma leitura de página.

    Sem a restrição o buscador devolve Instagram, Aiqfome e agregadores, e o
    primeiro link do iFood pode nem aparecer nos dez primeiros.
    """
    lugar = " ".join(x for x in (poi.get("cidade"), poi.get("uf")) if x)
    return f'site:ifood.com.br "{poi["nome"]}" {lugar}'.strip()


async def enriquecer(pois: list, workers: int = 4, usar_proxy: bool = True,
                     visivel: bool = False) -> list:
    """Devolve um registro por POI, com `estado` dizendo o que houve."""
    import minerar_web as MW

    pool = MW.SerpPool(workers, usar_proxy=usar_proxy, visivel=visivel)
    await pool.start()
    resultados = []
    sem = asyncio.Semaphore(workers)

    async def um(poi):
        async with sem:
            try:
                links = await pool.buscar(consulta(poi))
            except Exception as erro:  # noqa: BLE001
                return {**poi, "estado": "erro_busca", "detalhe": type(erro).__name__}
            cands = candidatos_de(links)
            if not cands:
                return {**poi, "estado": "sem_link"}

            # BUSCA O DETALHE DE CADA CANDIDATO e guarda para a IA julgar.
            #
            # Nem o slug nem a distancia decidem sozinhos — os dois foram
            # medidos e falham (ver `julgar_lojas`). Aqui so se COLETA; quem
            # escolhe e a Spark, depois, com nome e endereco na mesa.
            detalhados = []
            for mid, _slug in cands[:5]:
                det_i = await asyncio.to_thread(detalhe, mid)
                if det_i:
                    _, d_i = _confere(poi, det_i)
                    detalhados.append((det_i, d_i))
            if not detalhados:
                return {**poi, "estado": "sem_detalhe",
                        "merchant_id": cands[0][0]}
            if len(detalhados) > 1:
                # varios candidatos: a IA decide, no fim, em lote
                return {**poi, "estado": "para_ia", "cands": detalhados}
            det, d = detalhados[0]
            sem_slug = 0.0
            ok = d <= _raio_para(poi, det)
            if not ok:
                # DESCARTADO, e dito. Gravar o CNPJ de uma homônima seria dado
                # falso com aparência de verificado.
                return {**poi, "estado": "longe",
                        "merchant_id": det.get("id"), "candidatos": len(cands),
                        "dist_m": d, "achado": det.get("name")}
            return {**poi, "estado": "ok", "dist_m": d,
                    "sem_slug": round(sem_slug, 2), **campos(det)}

    for r in await asyncio.gather(*(um(p) for p in pois)):
        resultados.append(r)
    try:
        await pool.close()
    except Exception:  # noqa: BLE001
        pass

    # ── A IA DECIDE OS EMPATES, EM LOTE ──────────────────────────────────
    #
    # Em lote de propósito: uma chamada por POI desperdiçaria a janela do modelo
    # e o paralelismo já medido (lote 4, 64 threads). Aqui os casos duvidosos
    # viajam juntos e voltam decididos.
    duvidosos = [(i, r) for i, r in enumerate(resultados)
                 if r.get("estado") == "para_ia"]
    if duvidosos:
        print(f"  {len(duvidosos)} POIs com vários candidatos — a Spark decide",
              flush=True)
        casos = [(i, r, r["cands"]) for i, r in duvidosos]
        escolhas = await asyncio.to_thread(julgar_lojas, casos)
        for i, r in duvidosos:
            r.pop("cands", None)
            if i not in escolhas:
                # `0` da IA, ou lote que falhou. Nos dois casos NÃO se grava —
                # e o motivo aparece, em vez de virar um "longe" enganoso.
                resultados[i] = {**r, "estado": "ia_recusou"}
                continue
            det, d = escolhas[i]
            if d > _raio_para(r, det):
                # A IA escolheu, mas a coordenada desmente. A trava continua
                # valendo ACIMA da IA: ela lê nome, não mede metro.
                resultados[i] = {**r, "estado": "longe", "dist_m": d,
                                 "achado": det.get("name"),
                                 "merchant_id": det.get("id")}
                continue
            resultados[i] = {**r, "estado": "ok", "dist_m": d,
                             "sem_slug": 0.0, **campos(det)}
    return resultados


def gravar(con, achados: list) -> int:
    """Só o que passou na conferência entra, e só onde ainda falta."""
    cur = con.cursor()
    n = 0
    for a in achados:
        if a.get("estado") != "ok" or not a.get("cnpj"):
            continue
        cur.execute("""
            update pois set cnpj = coalesce(cnpj, %s),
                            telefone = coalesce(nullif(telefone,''), %s),
                            presente_no_ifood = true,
                            ifood_visto_em = now()
             where id = %s""",
                    (a["cnpj"], a.get("telefone"), a["id"]))
        n += cur.rowcount
    return n


def _resumo(achados: list) -> None:
    from collections import Counter
    c = Counter(a["estado"] for a in achados)
    t = max(1, len(achados))
    print()
    for k in ("ok", "longe", "ia_recusou", "sem_link", "sem_detalhe",
              "erro_busca", "para_ia"):
        if c.get(k):
            print(f"    {k:12} {c[k]:>5}  {c[k]/t:>5.0%}")
    com = [a for a in achados if a.get("estado") == "ok" and a.get("cnpj")]
    print(f"    {'com CNPJ':12} {len(com):>5}  {len(com)/t:>5.0%}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cidade", default="")
    p.add_argument("--area", default="")
    p.add_argument("--empresa", default="",
                   help="nome da empresa dona do dado; sem ele, vale o RADAR_USUARIO_SERVICO do .env")
    p.add_argument("--limite", type=int, default=0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--sem-proxy", dest="sem_proxy", action="store_true")
    p.add_argument("--visivel", action="store_true")
    p.add_argument("--aplicar", action="store_true")
    a = p.parse_args(argv)

    con = bc.conectar()
    con.autocommit = False
    cur = con.cursor()
    # `empresa_da_sessao`: sem `--empresa`, vale o RADAR_USUARIO_SERVICO
    # do .env. Igual ao extracao_estadual, ao povoar_vinculo e ao
    # cruzar_fontes — um so jeito de pedir identidade nos cinco.
    bc.empresa_da_sessao(cur, a.empresa)

    poligono = au.carregar_area(a.area) if a.area else None
    if a.area and not poligono:
        raise SystemExit(f"não há área desenhada com a referência {a.area!r}")

    pois = candidatos(cur, a.cidade, poligono, a.limite)
    print(f"  {len(pois):,} POIs sem CNPJ e com ramo que pode estar no iFood")
    if not pois:
        con.close()
        return 0
    for x in pois[:5]:
        print(f"    {x['nome'][:34]:36} {x['categoria'][:24]}")

    achados = asyncio.run(enriquecer(pois, a.workers, not a.sem_proxy, a.visivel))
    _resumo(achados)

    for x in [y for y in achados if y.get("estado") == "ok"][:6]:
        print(f"    ✔ {x['nome'][:28]:30} CNPJ {x.get('cnpj') or '—'} "
              f"· {x.get('rua') or ''} {x.get('numero') or ''} · {x['dist_m']:.0f} m")
    for x in [y for y in achados if y.get("estado") == "longe"][:4]:
        print(f"    ✘ {x['nome'][:28]:30} achou {str(x.get('achado'))[:24]} "
              f"a {x['dist_m']:.0f} m — descartado")

    if not a.aplicar:
        print("\n  SIMULAÇÃO — nada gravado. Use --aplicar.")
        con.close()
        return 0
    n = gravar(con, achados)
    con.commit()
    print(f"\n  GRAVADO: {n:,} POIs ganharam CNPJ do iFood")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
