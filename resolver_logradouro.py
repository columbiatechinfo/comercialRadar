# -*- coding: utf-8 -*-
"""resolver_logradouro.py — em que rua está cada POI, e com que direito.

A PERGUNTA QUE ESTE ARQUIVO RESPONDE

`ajuste_logradouro` responde *como se escreve* uma rua. Esta etapa responde
*em que rua está este POI* — e, junto, com que direito se afirma isso.

Cada POI chega com um endereço escrito por outra pessoa: às vezes completo, às
vezes só o nome de uma loja, às vezes só um ponto no mapa. A saída é o
logradouro do cadastro do IBGE, com número, CEP, bairro, quadra e face.

A ORDEM DAS PENEIRAS, E POR QUE ELA É ESSA

CEP e endereço escrito são AFIRMAÇÕES sobre o ponto. Coordenada é INFERÊNCIA por
proximidade. Um pin cai no meio do terreno, no fundo do lote ou na quadra
vizinha; o CEP escrito na ficha, não. Por isso a coordenada é a última, e o que
sai dela é gravado como `indicio`, nunca como `prova`.

O caso que fechou a discussão: o `Cachorro do Rosário` fica no Canoas Shopping,
na Avenida Guilherme Schell. Com a coordenada em primeiro lugar ele recebia a
`Rua Mathias Velho`, a 139 m dali — e nada no dado denunciaria.

    1  CEP        o CEP do POI existe no cadastro?
                  aponta uma via só  -> resolvido
                  aponta várias      -> a via escrita desempata
    2  ENDEREÇO   a via escrita existe entre as vias do município?
    3a PHOTON     a via existe mas o cadastro de 2022 não tem (loteamento novo).
                  Só vale se o Photon devolver A VIA QUE O TEXTO DIZ — sem essa
                  trava ele respondia `Rua Venâncio Aires` para `Kroty Car`, a
                  1,3 km de distância.
    3b OSRM       encosta a coordenada na malha viária. Aceito até 20 m.
    3c CNEFE      o endereço cadastrado mais próximo. Aceito até 20 m.
    4  HUMANO     o que sobrou. Sem IA: são nome de prédio, CEP solto e
                  `Rua Projetada` — gastar tempo de modelo neles entrega quase
                  nada, e metade está a mais de 60 m de qualquer porta
                  cadastrada, sem vizinho para herdar.

MEDIDO EM CANOAS, 02/09/2026, sobre 27.694 POIs

    CEP           18.464 (66,7%)   prova
    endereço       5.537 (20,0%)   prova
    Photon           111 ( 0,4%)   prova
    OSRM ≤20 m     1.799 ( 6,5%)   indício
    CNEFE ≤20 m      439 ( 1,6%)   indício
    humano         1.344 ( 4,9%)

    16 segundos por cidade, sem internet e sem custo.

QUEM FRACIONA O ENDEREÇO É O LIBPOSTAL, NÃO REGRA DE VÍRGULA

O serviço em `libpostal_servico.py` separa `house` (o estabelecimento) de `road`
(a via) mesmo sem pontuação entre os dois: `Canoas Shopping Avenida Guilherme
Schell` sai correto. Toda regra escrita à mão para isso só aprendia depois de ver
o exemplo falhar — e o exemplo seguinte ainda não tinha aparecido.

QUEM COMPARA DUAS GRAFIAS É A SKILL `ajuste-logradouro`

`norm_logradouro` expande tipo, título e patente e converte numeral por extenso;
`token_sort` mede a semelhança. É assim que `Rua Cel. Vicente` encontra
`RUA CORONEL VICENTE` e `Rua Rosacruz` encontra `AVENIDA ROSA CRUZ`.

O ViaCEP NÃO É USADO, e não é preferência: a documentação dele diz que "uso
massivo para validação de bases de dados locais, poderá automaticamente bloquear
seu acesso por tempo indeterminado" — que é exatamente o que esta etapa faz. O
cadastro do IBGE responde offline e ainda dá o número da porta, que ele não dá.

NADA É APAGADO. O endereço original vai gravado ao lado do resolvido, e o que
não se prova é marcado para uma pessoa olhar.
"""
from __future__ import annotations

import argparse
import difflib  # noqa: F401  (mantido: usado por similaridade quando sem C)
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg2.extras

import base_comum as bc

BASE = Path(__file__).resolve().parent
SKILL = BASE / "skills" / "ajuste-logradouro"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

import normalizacao_base as nb          # noqa: E402  (depois do sys.path)
import similaridade as sim              # noqa: E402

LIBPOSTAL = os.environ.get("LIBPOSTAL_URL", "http://127.0.0.1:7250")
PHOTON = os.environ.get("PHOTON_URL", "http://127.0.0.1:7210")
OSRM = os.environ.get("OSRM_URL", "http://127.0.0.1:7300")

# 20 metros: cabem o recuo de calçada e um estacionamento pequeno; o outro lado
# da rua, não. Ver o cabeçalho da migração 0044 para a medição que decidiu.
RAIO_M = float(os.environ.get("LOGRADOURO_RAIO_M", "20"))
# 0,90 no `token_sort` da skill. Abaixo disso começam a entrar ruas diferentes
# com nomes parecidos; acima, grafias legítimas passam a ser recusadas.
LIMIAR = float(os.environ.get("LOGRADOURO_LIMIAR", "0.90"))
CELULA = 0.0015                         # ~165 m: a busca varre 3x3 células
THREADS = int(os.environ.get("LOGRADOURO_THREADS", "8"))

CEP_RE = re.compile(r"\b(\d{5})-?(\d{3})\b")
# Palavras que não distinguem uma via de outra. O tipo entra aqui porque as
# fontes discordam dele o tempo todo: o cadastro traz `RUA ANTONIO FREDERICO
# OZANAN` e o POI diz `Avenida Antonio Frederico Ozanam`.
IRRELEVANTES = {"RUA", "AVENIDA", "TRAVESSA", "ESTRADA", "RODOVIA", "BECO",
                "PRACA", "ALAMEDA", "LARGO", "VIA", "DE", "DA", "DO", "DAS",
                "DOS", "E"}


def _log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------- texto ------
def norm(s) -> str:
    """A forma canônica da skill. Nunca deixa a etapa cair por causa de um
    texto estranho: no pior caso devolve maiúsculas."""
    try:
        return nb.norm_logradouro(s or "", hard=True)
    except Exception:                                          # noqa: BLE001
        return (s or "").upper().strip()


def so_nome(k: str) -> str:
    return " ".join(x for x in k.split() if x not in IRRELEVANTES)


def colado(k: str) -> str:
    return so_nome(k).replace(" ", "")


def _digitos(k: str) -> set:
    return set(re.findall(r"\d+", k))


def numeros_batem(a: str, b: str) -> bool:
    """`RUA 21 DE MARCO` não pode virar `RUA 25 DE MARCO`.

    Foi o único erro genuíno que a aferição contra 12.643 POIs encontrou, e é a
    classe mais grave: duas datas se parecem muito na escrita e são ruas
    diferentes, em bairros diferentes. Quando os dois lados carregam número, o
    número tem de ser o mesmo — não há grafia que justifique 21 virar 25. A
    skill já converteu extenso em dígito antes daqui, então `CENTO E QUINZE` e
    `115` chegam iguais.
    """
    da, db = _digitos(a), _digitos(b)
    return not (da or db) or da == db


def metros(la1, lo1, la2, lo2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = math.radians(la2 - la1), math.radians(lo2 - lo1)
    h = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _pegar(url: str, timeout: int = 15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "radar/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as h:
            return json.load(h)
    except Exception:                                          # noqa: BLE001
        return None


# ------------------------------------------------------------ libpostal -----
def fracionar(enderecos: list) -> list:
    """Os campos de cada endereço, pelo serviço do libpostal.

    Se o serviço estiver fora, a etapa NÃO inventa um fracionamento pior em
    silêncio: ela avisa e devolve o texto inteiro como via. Assim a peneira 1
    ainda funciona (o CEP sai por regex) e a 2 fica pior — mas fica DITO.
    """
    corpo = json.dumps({"enderecos": enderecos}).encode("utf-8")
    req = urllib.request.Request(LIBPOSTAL + "/parse", data=corpo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as h:
            resposta = json.load(h)
        return resposta.get("campos") or []
    except Exception as erro:                                  # noqa: BLE001
        _log("   ⚠️  libpostal fora do ar em %s (%s: %s)."
             % (LIBPOSTAL, type(erro).__name__, erro))
        _log("      Suba com: docker compose -f deploy/compose.libpostal.yml up -d")
        _log("      A etapa continua, mas SEM fracionamento: a peneira 2 perde força.")
        return [{"road": e} for e in enderecos]


# -------------------------------------------------------------- cadastro ----
class Cadastro:
    """O cadastro do IBGE de um município, nos três formatos que a etapa usa.

    Uma leitura só do banco alimenta os três índices — por CEP, por nome de via
    e por célula geográfica. Ler três vezes custaria três varreduras dos 111
    milhões de endereços do CNEFE.
    """

    def __init__(self, cod_municipio: str):
        self.cod = cod_municipio
        self.por_cep: dict = defaultdict(Counter)
        self.vias: dict = {}
        self.por_palavra: dict = defaultdict(list)
        self.por_colado: dict = {}
        self.grade: dict = defaultdict(list)
        self.enderecos = 0

    def carregar(self, cur) -> float:
        t0 = time.time()
        cur.execute("""
            select replace(coalesce(cep,''),'-','') as cep,
                   btrim(regexp_replace(
                     coalesce(nom_tipo_seglogr,'') || ' ' ||
                     coalesce(nom_titulo_seglogr,'') || ' ' ||
                     coalesce(nom_seglogr,''), '\\s+', ' ', 'g')) as via,
                   dsc_localidade, num_endereco, num_quadra, num_face,
                   latitude::float8, longitude::float8
              from resources_root.ibge_cnefe
             where cod_municipio = %s
               and coalesce(nom_seglogr,'') <> ''
               and latitude is not null and longitude is not null
        """, (self.cod,))
        for cep, via, bairro, num, quadra, face, lat, lon in cur:
            self.enderecos += 1
            if cep:
                self.por_cep[cep][via] += 1
            k = norm(via)
            if k and k not in self.vias:
                self.vias[k] = (via, bairro, cep)
            self.grade[(int(lat / CELULA), int(lon / CELULA))].append(
                (via, bairro, cep, num, quadra, face, lat, lon))

        for k, v in self.vias.items():
            for p in set(k.split()):
                if p not in IRRELEVANTES and len(p) > 2:
                    self.por_palavra[p].append((k, v))
            # `ROSACRUZ` e `ROSA CRUZ` são a mesma rua e não compartilham UMA
            # palavra sequer. Sem este índice o candidato nunca chegaria à
            # comparação, e o POI descia até a coordenada por nada.
            c = colado(k)
            if c and c not in self.por_colado:
                self.por_colado[c] = v
        return time.time() - t0

    def parear(self, k: str):
        """A via do cadastro que corresponde a `k`, ou None."""
        reg = self.vias.get(k)
        if reg is not None:
            return reg
        nome_k, colado_k = so_nome(k), colado(k)
        if not nome_k:
            return None
        reg = self.por_colado.get(colado_k)
        if reg is not None and numeros_batem(nome_k, so_nome(norm(reg[0]))):
            return reg
        vistos = set()
        for p in set(k.split()):
            if p in IRRELEVANTES or len(p) <= 2:
                continue
            for kk, vv in self.por_palavra.get(p, ()):
                if kk in vistos:
                    continue
                vistos.add(kk)
                if not numeros_batem(nome_k, so_nome(kk)):
                    continue
                if (sim.token_sort(k, kk) >= LIMIAR
                        or sim.token_sort(nome_k, so_nome(kk)) >= LIMIAR
                        or colado_k == colado(kk)):
                    return vv
        return None

    def vizinho(self, lat: float, lon: float, raio: float):
        """`(distância, registro)` do endereço cadastrado mais próximo, ou
        `(distância, None)` quando o mais próximo está além do raio."""
        ci, cj = int(lat / CELULA), int(lon / CELULA)
        melhor = (float("inf"), None)
        for i in (ci - 1, ci, ci + 1):
            for j in (cj - 1, cj, cj + 1):
                for reg in self.grade.get((i, j), ()):
                    d = metros(lat, lon, reg[6], reg[7])
                    if d < melhor[0]:
                        melhor = (d, reg)
        return melhor if melhor[0] <= raio else (melhor[0], None)


# ------------------------------------------------------------- peneiras -----
def _campos_do_poi(campos: dict, endereco: str) -> tuple:
    """As vias candidatas e o CEP, a partir do que o libpostal leu.

    `house` entra como segunda candidata porque às vezes o nome do lugar CONTÉM
    a via — e porque, quando não contém, ela simplesmente não pareia com nada.
    """
    vias = [x for x in (campos.get("road"), campos.get("house")) if x]
    cep = re.sub(r"\D", "", campos.get("postcode") or "")
    if len(cep) != 8:
        m = CEP_RE.search(endereco or "")
        cep = (m.group(1) + m.group(2)) if m else None
    return vias, cep


def _por_cep(cadastro: Cadastro, cep, vias: list):
    if not cep or cep not in cadastro.por_cep:
        return None
    do_cep = cadastro.por_cep[cep]
    if len(do_cep) == 1:
        via = next(iter(do_cep))
        return {"logradouro": via, "cep": cep, "peneira": "cep",
                "forca": "prova", "motivo": "CEP aponta uma via so"}
    for v in do_cep:
        for x in vias:
            if (norm(x) == norm(v)
                    or sim.token_sort(so_nome(norm(x)), so_nome(norm(v))) >= LIMIAR):
                return {"logradouro": v, "cep": cep, "peneira": "cep",
                        "forca": "prova",
                        "motivo": "CEP com %d vias, desempatado pela escrita"
                                  % len(do_cep)}
    return None


def _por_endereco(cadastro: Cadastro, vias: list):
    for v in vias:
        reg = cadastro.parear(norm(v))
        if reg:
            return {"logradouro": reg[0], "bairro": reg[1], "cep": reg[2],
                    "peneira": "endereco", "forca": "prova",
                    "motivo": "via escrita existe no municipio"}
    return None


def _por_photon(vias: list, lat, lng, cidade: str, uf: str):
    """A via que existe de verdade e o cadastro de 2022 não tem.

    A trava de fidelidade não é zelo: sem ela o Photon devolvia `Rua Venâncio
    Aires` para `Kroty Car` e `Avenida Getúlio Vargas` para `BR-386`, ambas a
    mais de 1,3 km do POI. São o melhor palpite do buscador para um texto que
    não é endereço — e virariam rua errada gravada como prova.
    """
    if not vias:
        return None
    pedida = vias[0]
    q = urllib.parse.quote("%s, %s, %s" % (pedida, cidade, uf))
    alvo = "%s/api?q=%s&limit=1" % (PHOTON, q)
    if lat is not None and lng is not None:
        alvo += "&lat=%s&lon=%s" % (lat, lng)
    d = _pegar(alvo)
    f = ((d or {}).get("features") or [None])[0]
    if not f:
        return None
    p = f.get("properties") or {}
    rua = p.get("street") or (p.get("name") if p.get("osm_key") == "highway"
                              else None)
    if not rua:
        return None
    fiel = (norm(rua) == norm(pedida)
            or sim.token_sort(so_nome(norm(rua)), so_nome(norm(pedida))) >= LIMIAR)
    if not fiel:
        return None
    return {"logradouro": rua, "cep": (p.get("postcode") or "").replace("-", "")
            or None, "bairro": p.get("district") or p.get("city"),
            "peneira": "photon", "forca": "prova",
            "motivo": "via fora do cadastro de 2022, confirmada pelo Photon"}


def _por_osrm(lat, lng):
    if lat is None or lng is None:
        return None
    d = _pegar("%s/nearest/v1/driving/%s,%s?number=1" % (OSRM, lng, lat))
    w = ((d or {}).get("waypoints") or [None])[0]
    if not w or not w.get("name"):
        return None
    dist = w.get("distance")
    if dist is None or dist > RAIO_M:
        return None
    return {"logradouro": w["name"], "peneira": "osrm", "forca": "indicio",
            "metros": round(float(dist), 1),
            "motivo": "coordenada encostada na malha viaria a %.1f m" % dist}


def _por_cnefe(cadastro: Cadastro, lat, lng):
    if lat is None or lng is None:
        return None
    dist, reg = cadastro.vizinho(float(lat), float(lng), RAIO_M)
    if not reg:
        return None
    return {"logradouro": reg[0], "bairro": reg[1], "cep": reg[2],
            "numero": reg[3], "num_quadra": reg[4], "num_face": reg[5],
            "peneira": "cnefe", "forca": "indicio", "metros": round(dist, 1),
            "motivo": "endereco cadastrado a %.1f m" % dist}


def resolver_um(poi: dict, campos: dict, cadastro: Cadastro,
                cidade: str, uf: str) -> dict:
    """A cascata inteira para um POI. A ordem é o contrato desta função."""
    vias, cep = _campos_do_poi(campos, poi["endereco"])
    lat, lng = poi["lat"], poi["lng"]

    achado = (_por_cep(cadastro, cep, vias)
              or _por_endereco(cadastro, vias)
              or _por_photon(vias, lat, lng, cidade, uf)
              or _por_osrm(lat, lng)
              or _por_cnefe(cadastro, lat, lng))

    if achado is None:
        achado = {"peneira": "nenhuma", "forca": "sem",
                  "motivo": "nenhuma peneira resolveu"}

    # O número da porta: o que o próprio POI escreveu vale mais que o do vizinho.
    numero = campos.get("house_number") or achado.get("numero")
    if numero:
        numero = re.sub(r"^\s*(n[oº°.]?)\s*", "", str(numero), flags=re.I).strip()

    return {
        "poi_id": poi["id"],
        "cod_municipio": cadastro.cod,
        "logradouro": achado.get("logradouro"),
        "numero": numero or None,
        "cep": achado.get("cep") or cep,
        "bairro": achado.get("bairro"),
        "num_quadra": achado.get("num_quadra"),
        "num_face": achado.get("num_face"),
        "peneira": achado["peneira"],
        "forca": achado["forca"],
        "metros": achado.get("metros"),
        "logradouro_original": (vias[0] if vias else None),
        "cep_original": cep,
        "revisao_humana": achado["forca"] == "sem",
        "motivo": achado.get("motivo"),
    }


# -------------------------------------------------------------- a etapa -----
COLUNAS = ("poi_id", "cod_municipio", "logradouro", "numero", "cep", "bairro",
           "num_quadra", "num_face", "peneira", "forca", "metros",
           "logradouro_original", "cep_original", "revisao_humana", "motivo")


def do_municipio(cod: str, cidade: str, uf: str = "RS", area: str = "",
                 limite: int = 0, aplicar: bool = False) -> dict:
    con = bc.conectar()
    cur = con.cursor()

    cadastro = Cadastro(cod)
    dt = cadastro.carregar(cur)
    _log("   cadastro do IBGE: %d enderecos, %d CEPs, %d vias (%.1f s)"
         % (cadastro.enderecos, len(cadastro.por_cep), len(cadastro.vias), dt))
    if not cadastro.enderecos:
        _log("   ⚠️  o CNEFE nao tem o municipio %s. A etapa PARA aqui: sem" % cod)
        _log("      autoridade nao ha o que provar, e inventar seria pior.")
        con.close()
        return {"erro": "municipio sem CNEFE"}

    sql = """
        select id, endereco, coalesce(maps_lat, lat_origem),
               coalesce(maps_lng, lng_origem)
          from radar_comercial.pois
         where translate(upper(coalesce(cidade,'')),
                         'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC') =
               translate(upper(%s), 'ÁÀÂÃÉÊÍÓÔÕÚÜÇ', 'AAAAEEIOOOUUC')
    """
    if limite:
        sql += " limit %d" % int(limite)
    cur.execute(sql, (cidade,))
    pois = [{"id": r[0], "endereco": r[1] or "",
             "lat": float(r[2]) if r[2] is not None else None,
             "lng": float(r[3]) if r[3] is not None else None}
            for r in cur.fetchall()]

    # A área desenhada é FOCO, não filtro de gravação: quem está fora do
    # polígono continua no banco, só não gasta as peneiras que custam rede.
    dentro = None
    if area:
        try:
            import area_utils as au
            poligono = au.carregar_area(area)
            dentro = {p["id"] for p in pois
                      if p["lat"] is not None
                      and au.ponto_no_poligono(p["lat"], p["lng"], poligono)}
            _log("   area %s: %d de %d POIs dentro do desenho"
                 % (area, len(dentro), len(pois)))
            pois = [p for p in pois if p["id"] in dentro]
        except Exception as erro:                              # noqa: BLE001
            _log("   ⚠️  area '%s' nao carregou (%s). Segue com a cidade inteira."
                 % (area, erro))

    _log("   %d POIs de %s" % (len(pois), cidade))
    if not pois:
        con.close()
        return {"pois": 0}

    t0 = time.time()
    campos = fracionar([p["endereco"] for p in pois])
    _log("   libpostal fracionou %d enderecos em %.1f s"
         % (len(campos), time.time() - t0))
    if len(campos) != len(pois):
        campos = campos + [{}] * (len(pois) - len(campos))

    # Photon e OSRM só são chamados pelo resíduo, e em paralelo: são os únicos
    # passos que falam com um serviço, e o resíduo é ~13% da cidade.
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        linhas = list(ex.map(
            lambda par: resolver_um(par[0], par[1], cadastro, cidade, uf),
            zip(pois, campos)))
    dt_cascata = time.time() - t0

    placar = Counter((l["peneira"], l["forca"]) for l in linhas)
    _log("   cascata em %.1f s (%.0f POIs/s)"
         % (dt_cascata, len(linhas) / max(dt_cascata, 1e-6)))
    for (peneira, forca), n in sorted(placar.items(), key=lambda x: -x[1]):
        _log("      %-10s %-8s %6d (%4.1f%%)"
             % (peneira, forca, n, 100.0 * n / len(linhas)))
    prova = sum(1 for l in linhas if l["forca"] == "prova")
    indicio = sum(1 for l in linhas if l["forca"] == "indicio")
    humano = sum(1 for l in linhas if l["revisao_humana"])
    _log("   PROVA %d (%.1f%%) · INDICIO %d (%.1f%%) · REVISAO HUMANA %d (%.1f%%)"
         % (prova, 100.0 * prova / len(linhas), indicio,
            100.0 * indicio / len(linhas), humano, 100.0 * humano / len(linhas)))

    if not aplicar:
        _log("   (ensaio: nada gravado. Use --aplicar para escrever)")
        con.close()
        return {"pois": len(linhas), "prova": prova, "indicio": indicio,
                "humano": humano, "gravado": 0}

    valores = [tuple(l[c] for c in COLUNAS) for l in linhas]
    psycopg2.extras.execute_values(cur, """
        insert into radar_comercial.logradouro_resolvido
            (poi_id, cod_municipio, logradouro, numero, cep, bairro,
             num_quadra, num_face, peneira, forca, metros,
             logradouro_original, cep_original, revisao_humana, motivo)
        values %s
        -- `id_empresa` NÃO vai no INSERT: quem carimba é a trigger
        -- `preencher_empresa`. Mandar aqui abriria caminho para gravar na
        -- empresa errada.
        on conflict (poi_id) do update set
            cod_municipio = excluded.cod_municipio,
            logradouro = excluded.logradouro,
            numero = excluded.numero,
            cep = excluded.cep,
            bairro = excluded.bairro,
            num_quadra = excluded.num_quadra,
            num_face = excluded.num_face,
            peneira = excluded.peneira,
            forca = excluded.forca,
            metros = excluded.metros,
            logradouro_original = excluded.logradouro_original,
            cep_original = excluded.cep_original,
            revisao_humana = excluded.revisao_humana,
            motivo = excluded.motivo,
            resolvido_em = now()
    """, valores, page_size=1000)
    con.commit()
    _log("   gravados %d POIs em logradouro_resolvido" % len(valores))
    con.close()
    return {"pois": len(linhas), "prova": prova, "indicio": indicio,
            "humano": humano, "gravado": len(valores)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Resolve o logradouro de cada POI contra o cadastro do IBGE.")
    p.add_argument("--municipio", required=True,
                   help="codigo IBGE de 7 digitos (Canoas = 4304606)")
    p.add_argument("--cidade", required=True,
                   help="nome da cidade como aparece em pois.cidade")
    p.add_argument("--uf", default="RS")
    p.add_argument("--area", default="",
                   help="nome da area desenhada; restringe o foco ao poligono")
    p.add_argument("--limite", type=int, default=0,
                   help="processa so os N primeiros POIs (para testar)")
    p.add_argument("--aplicar", action="store_true",
                   help="grava em logradouro_resolvido; sem isto e ensaio")
    a = p.parse_args(argv)

    _log("▶ logradouro de %s (%s)" % (a.cidade, a.municipio))
    r = do_municipio(a.municipio, a.cidade, a.uf, a.area, a.limite, a.aplicar)
    return 1 if r.get("erro") else 0


if __name__ == "__main__":
    raise SystemExit(main())
