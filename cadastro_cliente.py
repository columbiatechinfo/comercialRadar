"""
cadastro_cliente.py — A base de CADASTRO do cliente, e o cruzamento dela com os POIs.

O que muda de figura aqui: até agora o sistema só sabia o que ele mesmo tinha
achado (captura+OCR, Maps, web). Esta tabela traz o outro lado — a carteira de
imóveis/ligações que o cliente já tem. Cruzar as duas responde a pergunta que
paga a conta:

    "de tudo que existe na rua, o que meu cliente JÁ tem, o que ele tem
     classificado errado, e o que ele não tem?"

PADRONIZAÇÃO DOS CAMPOS
A planilha da concessionária vem com nomes de sistema legado (`NOM_LOGRADOURO`,
`SIT_LIG`, `TPO_MEDICAO`, `COD_LATITUDE`). Aqui eles viram o mesmo vocabulário do
resto do banco — `logradouro`, `situacao_ligacao`, `cod_tipo_medicao`, `lat` —
sem mudar o SENTIDO de nenhum dado. O de-para está em `MAPA`, que é a fonte única:
a criação da tabela, a importação e o modelo em branco saem todos dele, então não
há como um sair do outro.

Duas colunas do arquivo original não sobrevivem, e por bom motivo:
  · `DATA_INSTALACAO` é cópia byte a byte de `DAT_INSTALACAO` (102.065 de 102.065);
  · `REFERENCIA` é a competência do extrato, igual na planilha inteira — vira
    coluna da IMPORTAÇÃO (`referencia`), não do imóvel.

CHAVE
`num_ligacao` é única no arquivo (102.065 distintos em 102.065 linhas) e é a
identidade do imóvel na concessionária — é ela que dedup a reimportação.

USO:
  .venv\\Scripts\\python cadastro_cliente.py --criar
  .venv\\Scripts\\python cadastro_cliente.py --importar arquivo.csv [--cliente corsan]
  .venv\\Scripts\\python cadastro_cliente.py --cruzar --cidade Canoas
  .venv\\Scripts\\python cadastro_cliente.py --modelo modelo.csv
"""

import io
import csv
import sys
import math
import re
import argparse
import unicodedata
from collections import defaultdict

import base_comum as bc

# ── DE-PARA ────────────────────────────────────────────────────────────────
# (coluna no arquivo, coluna no banco, tipo SQL, o que é)
MAPA = [
    ("EMP_CODIGO",              "empresa_codigo",       "integer",  "código da empresa/unidade"),
    ("CIDADE",                  "cidade",               "text",     "município"),
    ("CATEGORIA",               "categoria",            "text",     "RESIDENCIAL / COMERCIAL / INDUSTRIAL / PUBLICA"),
    ("SUB_CATEGORIA",           "subcategoria",         "text",     "subcategoria tarifária"),
    ("PERFIL",                  "perfil",               "text",     "perfil do consumidor"),
    ("UTILIZACAO",              "utilizacao",           "text",     "uso declarado do imóvel"),
    ("NUM_LIGACAO",             "num_ligacao",          "bigint",   "CHAVE — identidade da ligação"),
    ("QTD_ECONOMIAS_RES",       "economias_res",        "integer",  "economias residenciais"),
    ("QTD_ECONOMIAS_COM",       "economias_com",        "integer",  "economias comerciais"),
    ("QTD_ECONOMIAS_IND",       "economias_ind",        "integer",  "economias industriais"),
    ("QTD_ECONOMIAS_PUB",       "economias_pub",        "integer",  "economias públicas"),
    ("QTD_ECONOMIAS_OUT",       "economias_out",        "integer",  "outras economias"),
    ("TIPO_FATURAMENTO",        "tipo_faturamento",     "text",     "água / água+esgoto"),
    ("SIT_LIG",                 "situacao_ligacao",     "text",     "Ativa / Cortada / Inativa…"),
    ("TIPO_LIGACAO",            "tipo_ligacao",         "text",     "hidrometrado ou não"),
    ("SIT_CONTRATO",            "situacao_contrato",    "text",     "situação do contrato"),
    ("NOM_LOGRADOURO",          "logradouro",           "text",     "nome da rua"),
    ("END_LIGACAO",             "endereco_origem",      "text",     "endereço como veio no arquivo"),
    ("NRO",                     "numero",               "text",     "número"),
    ("DSC_COMPLEMENTO",         "complemento",          "text",     "complemento"),
    ("COD_CEP",                 "cep",                  "text",     "CEP (8 dígitos)"),
    ("NOM_BAIRRO",              "bairro",               "text",     "bairro"),
    ("CLASSIFICACAO",           "classificacao",        "text",     "classificação tarifária"),
    ("REMESSA",                 "remessa",              "text",     "forma de entrega da fatura"),
    ("NUM_MEDIDOR",             "num_medidor",          "text",     "série do hidrômetro"),
    ("DAT_INSTALACAO",          "data_instalacao",      "date",     "instalação do medidor"),
    ("COD_GRUPO",               "cod_grupo",            "integer",  "grupo de faturamento"),
    ("COD_SETOR_COMERCIAL",     "cod_setor_comercial",  "integer",  "setor comercial"),
    ("NUM_QUADRA",              "num_quadra",           "integer",  "quadra do cadastro"),
    ("NUM_LOTE",                "num_lote",             "integer",  "lote do cadastro"),
    ("COD_ROTA_LEITURA",        "cod_rota_leitura",     "integer",  "rota de leitura"),
    ("SEQ_ROTA",                "seq_rota",             "integer",  "sequência na rota"),
    ("COD_LATITUDE",            "lat",                  "double precision", "latitude"),
    ("COD_LONGITUDE",           "lng",                  "double precision", "longitude"),
    ("COD_CLASSE_LIGACAO_AGUA", "cod_classe_agua",      "integer",  "código da classe de água"),
    ("CLASSE_AGUA",             "classe_agua",          "text",     "classe de água"),
    ("COD_CLASSE_LIGACAO_ESG",  "cod_classe_esgoto",    "integer",  "código da classe de esgoto"),
    ("CLASSE_ESGOTO",           "classe_esgoto",        "text",     "classe de esgoto"),
    ("TPO_FONTE",               "tipo_fonte",           "text",     "fonte alternativa"),
    ("VL_VOL_CISTERNA",         "vol_cisterna",         "double precision", "volume da cisterna"),
    ("VL_VOL_PISCINA",          "vol_piscina",          "double precision", "volume da piscina"),
    ("VL_VOL_CAIXA",            "vol_caixa",            "double precision", "volume da caixa"),
    ("TIPO_ENTREGA",            "tipo_entrega",         "text",     "tipo de entrega da fatura"),
    ("ULTIMO_CONTRATO",         "ultimo_contrato",      "text",     "é o último contrato"),
    ("DAT_ATIVACAO",            "data_ativacao",        "date",     "ativação da ligação"),
    ("DAT_ENCERRAMENTO",        "data_encerramento",    "date",     "encerramento da ligação"),
    ("DICA_LOCALIZACAO",        "dica_localizacao",     "text",     "observação do leiturista"),
    ("COD_CATEGORIA",           "cod_categoria",        "text",     "sigla da categoria"),
    ("COD_FATURAMENTO",         "cod_faturamento",      "integer",  "código de faturamento"),
    ("NUM_MEDIDOR_MASTER",      "num_medidor_master",   "text",     "medidor master"),
    ("SEGMENTO",                "segmento",             "text",     "segmento"),
    ("COD_TIPO_CONSUMIDOR",     "cod_tipo_consumidor",  "integer",  "código do tipo de consumidor"),
    ("TIPO_CONSUMIDOR",         "tipo_consumidor",      "text",     "tipo de consumidor"),
    ("TPO_MEDICAO",             "cod_tipo_medicao",     "integer",  "código do tipo de medição"),
    ("DSC_TPO_MEDICAO",         "tipo_medicao",         "text",     "tipo de medição"),
    ("ZONA_LIGACAO",            "zona_ligacao",         "text",     "zona da ligação"),
    ("FLAG_DISTRITO",           "flag_distrito",        "text",     "marca de distrito"),
    ("FLAG_VCG",                "flag_vcg",             "text",     "marca VCG"),
    ("FLAG_TARIFA_MINIMA",      "flag_tarifa_minima",   "text",     "marca de tarifa mínima"),
    ("COD_DISTRITO",            "cod_distrito",         "text",     "código do distrito"),
    ("BACIAS",                  "bacia",                "text",     "bacia"),
    ("SUB_BACIAS",              "sub_bacia",            "text",     "sub-bacia"),
    ("STA_COMUNIDADE",          "sta_comunidade",       "text",     "status de comunidade"),
    ("COD_COMUNIDADE",          "cod_comunidade",       "text",     "código da comunidade"),
    ("DSC_COMUNIDADE",          "comunidade",           "text",     "nome da comunidade"),
    ("DESC_ZONA_ABAST",         "zona_abastecimento",   "text",     "zona de abastecimento"),
    ("FLAG_VAL",                "flag_val",             "text",     "marca de validação"),
    ("SPE",                     "spe",                  "text",     "SPE"),
    ("PROGRAMA",                "programa",             "text",     "programa social"),
    ("STA_TELEMETRIA",          "sta_telemetria",       "text",     "telemetria"),
    ("STA_AREA_RISCO",          "sta_area_risco",       "text",     "área de risco"),
]
COL_ARQ = [m[0] for m in MAPA]
COL_DB = [m[1] for m in MAPA]

# ── Flags do cruzamento ────────────────────────────────────────────────────
# O valor prático da ferramenta está nesta coluna. Ela responde, por imóvel, o
# que a equipe de campo deve fazer — e o motivo fica junto, senão o vendedor
# não confia na lista.
FLAGS = {
    "ja_cadastrado":   "Comercial na base do cliente. NÃO VISITAR.",
    "reclassificar_alta":  "Não é comercial na base e o POI tem CNPJ confirmado (4/4). Visitar primeiro.",
    "reclassificar_media": "Não é comercial na base e o POI tem CNPJ de confiança parcial.",
    "reclassificar_baixa": "Não é comercial na base e há POI sem CNPJ. Confirmar em campo.",
    "novo_comercial":  "POI comercial que não existe na base de cadastro.",
    "sem_poi":         "Imóvel da base sem POI correspondente.",
}
ORDEM_FLAGS = ["ja_cadastrado", "reclassificar_alta", "reclassificar_media",
               "reclassificar_baixa", "sem_poi", "novo_comercial"]


def _flag_por_confianca(conf: str) -> str:
    """A flag do imóvel mal classificado GRADUA pela confiança do POI.

    Não é detalhe cosmético: é o que ordena a fila de visita. Um POI com CNPJ
    conferido na base nacional e no município é quase certeza de comércio ativo;
    um sem CNPJ nenhum é uma pista que ainda precisa de olho humano. Mandar os
    dois com o mesmo rótulo é jogar fora o trabalho todo das fases de CNPJ."""
    c = str(conf or "")
    if c.startswith("4/4"):
        return "reclassificar_alta"
    if c[:1].isdigit():
        return "reclassificar_media"
    return "reclassificar_baixa"
# Distância máxima para dizer que o POI e a ligação são o mesmo lugar QUANDO o
# endereço já bate. Só a distância não decide nada: numa rua densa todo imóvel
# residencial tem um comércio a 35 m, e a primeira versão disto marcou 57.723
# imóveis como "reclassificar" — mais que o total de comércios da cidade. O que
# identifica o mesmo lugar é CEP + número; a distância entra depois, para o caso
# em que o endereço de um dos lados está incompleto.
RAIO_M = 35.0
# Sem endereço em comum, a coincidência tem de ser muito mais apertada — é a
# porta ao lado, não o quarteirão.
#
# 8 m É A TESTADA MÉDIA DE UM LOTE, e é essa a medida certa aqui: dois pontos a
# menos que uma frente de lote de distância estão no MESMO lote; a partir dela,
# já é o vizinho. Regra do dono do produto, 28/08/2026.
#
# Os 12 m anteriores não vinham de medida nenhuma — eram um "bem menos que 35"
# escolhido a olho. Com testada de lote o número passa a ter significado, e é
# defensável em campo: quem for conferir sabe o que 8 m querem dizer na rua.
RAIO_SO_GEO_M = 8.0


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


def _nulo(v):
    """'null', '' e ' ' do arquivo viram NULL de verdade."""
    s = str(v or "").strip()
    return None if s.lower() in ("", "null", "none", "-") else s


def _conv(valor, tipo):
    v = _nulo(valor)
    if v is None:
        return None
    try:
        if tipo == "integer":
            return int(float(v))
        if tipo == "bigint":
            return int(float(v))
        if tipo == "double precision":
            return float(v.replace(",", "."))
        if tipo == "date":
            return v[:10]
    except (ValueError, TypeError):
        return None
    return v[:500]


# O CADASTRO NAO TRAZ O TIPO DO LOGRADOURO, e isso decide como se casa.
#
# O dado bruto do cliente e `INDIO SEPE`, `HENRIQUE DIAS`, `DAS ANDORINHAS` —
# sem `RUA`/`AVENIDA`, e nao ha coluna de tipo em nenhuma das 84. Os POIs vem
# com o tipo: `RUA DA BARCA`, `RUA TOBIAS BARRETO`. A normalizacao dos dois
# lados e fiel a fonte, entao ela nao aproxima o que a fonte separou.
#
# MEDIDO em Canoas, 28/08/2026, casando logradouro + numero:
#
#     com o tipo como veio ......    297 POIs
#     com o tipo removido ....... 17.712 POIs
#
# Por isso a chave tira o tipo dos DOIS lados. O que sobra e o nome da via, que
# e o que as duas fontes tem em comum.
_TIPO_LOGRADOURO = re.compile(
    r"^(RUA|R|AVENIDA|AV|TRAVESSA|TV|ESTRADA|ESTR|RODOVIA|ROD|BECO|PRACA|PCA|"
    r"ALAMEDA|AL|LARGO|LOTEAMENTO|VIA|LINHA|PARQUE|ACESSO|SERVIDAO|VILA)\.?\s+")


def via_sem_tipo(logradouro: str) -> str:
    """O nome da via, sem `RUA`/`AVENIDA` e sem acento — a chave que casa as
    duas fontes. Ver o comentario de `_TIPO_LOGRADOURO`."""
    s = unicodedata.normalize("NFD", (logradouro or "").upper().strip())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    anterior = None
    while anterior != s:                    # "ESTRADA VELHA DO ..." tira uma vez so
        anterior = s
        s = _TIPO_LOGRADOURO.sub("", s, count=1)
    return s


def _dist_m(la1, lo1, la2, lo2):
    dlat = (la1 - la2) * 111_320.0
    dlon = (lo1 - lo2) * 111_320.0 * math.cos(math.radians(la1))
    return math.hypot(dlat, dlon)


# ── Esquema ────────────────────────────────────────────────────────────────
def criar_tabela(con=None) -> None:
    fechar = con is None
    con = con or bc.conectar()
    try:
        cols = ",\n  ".join(f"{c} {t}" for _a, c, t, _d in MAPA)
        with con.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS cadastro_cliente (
                  id            bigserial PRIMARY KEY,
                  cliente       text NOT NULL DEFAULT 'corsan',
                  referencia    date,
                  {cols},
                  uf            text,
                  endereco      text,
                  e_comercial   boolean,
                  poi_id        bigint,
                  cruz_flag     text,
                  cruz_motivo   text,
                  cruz_dist_m   double precision,
                  cruz_conf     text,
                  importado_em  timestamp DEFAULT now(),
                  UNIQUE (cliente, num_ligacao))""")
            for sql in (
                "CREATE INDEX IF NOT EXISTS ix_cad_cidade ON cadastro_cliente (cidade)",
                "CREATE INDEX IF NOT EXISTS ix_cad_flag ON cadastro_cliente (cruz_flag)",
                "CREATE INDEX IF NOT EXISTS ix_cad_geo ON cadastro_cliente (lat, lng)",
                "CREATE INDEX IF NOT EXISTS ix_cad_poi ON cadastro_cliente (poi_id)",
            ):
                cur.execute(sql)
        con.commit()
    finally:
        if fechar:
            con.close()


def modelo_csv(destino=None) -> str:
    """Planilha-modelo: cabeçalho padronizado + uma linha de exemplo comentada.

    O modelo sai do MESMO `MAPA` que cria a tabela — não há um arquivo de
    exemplo envelhecendo em paralelo com o esquema."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COL_DB)
    exemplo = {"num_ligacao": "1983604", "cidade": "CANOAS", "uf": "RS",
               "categoria": "COMERCIAL", "logradouro": "DEZESSETE DE ABRIL",
               "numero": "1187", "cep": "92415000", "bairro": "GUAJUVIRAS",
               "lat": "-29.896617", "lng": "-51.135594",
               "situacao_ligacao": "Ativa", "economias_com": "1"}
    w.writerow([exemplo.get(c, "") for c in COL_DB])
    txt = buf.getvalue()
    if destino:
        io.open(destino, "w", encoding="utf-8-sig", newline="").write(txt)
    return txt


# ── Importação ─────────────────────────────────────────────────────────────
def _endereco_maps(r: dict) -> str:
    """Endereço no formato do Maps, para casar com o resto do banco."""
    log = r.get("logradouro") or ""
    if not log:
        return r.get("endereco_origem") or ""
    p = f"{log}, {r['numero']}" if r.get("numero") else log
    if r.get("bairro"):
        p += f" - {r['bairro']}"
    if r.get("cidade"):
        p += f", {str(r['cidade']).title()}"
    if r.get("uf"):
        p += f" - {r['uf']}"
    cep = "".join(ch for ch in str(r.get("cep") or "") if ch.isdigit())
    if len(cep) == 8:
        p += f", {cep[:5]}-{cep[5:]}"
    return p


def ler_arquivo(caminho: str, limite: int = 0) -> tuple:
    """(linhas, referencia, avisos). Aceita o cabeçalho ORIGINAL ou o PADRONIZADO."""
    f = io.open(caminho, encoding="utf-8-sig", newline="")
    rd = csv.DictReader(f)
    cab = [c.strip() for c in (rd.fieldnames or [])]
    # de-para tolerante: aceita o nome do arquivo legado e o já padronizado
    por_arq = {a: (b, t) for a, b, t, _d in MAPA}
    por_db = {b: (b, t) for _a, b, t, _d in MAPA}
    usa = {}
    for c in cab:
        if c in por_arq:
            usa[c] = por_arq[c]
        elif c in por_db:
            usa[c] = por_db[c]
    avisos = []
    ignoradas = [c for c in cab if c not in usa and c != "REFERENCIA"]
    if ignoradas:
        avisos.append(f"{len(ignoradas)} coluna(s) do arquivo sem lugar na tabela "
                      f"e ignoradas: {', '.join(ignoradas[:6])}"
                      + (" …" if len(ignoradas) > 6 else ""))
    faltando = [b for _a, b, _t, _d in MAPA if b not in {v[0] for v in usa.values()}]
    if "num_ligacao" in faltando:
        f.close()
        raise ValueError("o arquivo não tem `num_ligacao` (nem `NUM_LIGACAO`) — "
                         "sem a chave não dá para importar nem deduplicar")
    if faltando:
        avisos.append(f"{len(faltando)} coluna(s) da tabela ausentes no arquivo "
                      f"(ficam nulas): {', '.join(faltando[:6])}"
                      + (" …" if len(faltando) > 6 else ""))

    linhas, ref, vistos, dups = [], None, set(), 0
    for r in rd:
        ref = ref or _nulo(r.get("REFERENCIA") or r.get("referencia"))
        d = {}
        for c, (col, tipo) in usa.items():
            d[col] = _conv(r.get(c), tipo)
        if d.get("num_ligacao") is None:
            continue
        if d["num_ligacao"] in vistos:
            dups += 1
            continue
        vistos.add(d["num_ligacao"])
        d["uf"] = d.get("uf") or "RS"
        d["endereco"] = _endereco_maps(d)
        # COMERCIAL de verdade: a categoria diz, ou há economia comercial contada.
        # As duas coisas discordam em parte da base, e ignorar a segunda deixaria
        # de fora quem tem loja no térreo de prédio residencial.
        d["e_comercial"] = (_norm(d.get("categoria")) == "COMERCIAL"
                            or (d.get("economias_com") or 0) > 0)
        linhas.append(d)
        if limite and len(linhas) >= limite:
            break
    f.close()
    if dups:
        avisos.append(f"{dups} linha(s) com `num_ligacao` repetido — ficou a primeira")
    return linhas, ref, avisos


def importar(linhas: list, cliente: str, referencia, con=None) -> dict:
    from psycopg2.extras import execute_values
    fechar = con is None
    con = con or bc.conectar()
    try:
        criar_tabela(con)
        with con.cursor() as cur:
            cur.execute("SELECT count(*) FROM cadastro_cliente WHERE cliente=%s",
                        (cliente,))
            antes = cur.fetchone()[0]
            cols = ["cliente", "referencia"] + COL_DB + ["uf", "endereco", "e_comercial"]
            vals = [tuple([cliente, referencia]
                          + [d.get(c) for c in COL_DB]
                          + [d.get("uf"), d.get("endereco"), d.get("e_comercial")])
                    for d in linhas]
            atualiza = ", ".join(f"{c} = EXCLUDED.{c}" for c in
                                 COL_DB + ["uf", "endereco", "e_comercial", "referencia"]
                                 if c != "num_ligacao")
            execute_values(cur, f"""
                INSERT INTO cadastro_cliente ({', '.join(cols)}) VALUES %s
                ON CONFLICT (cliente, num_ligacao) DO UPDATE SET {atualiza},
                    importado_em = now()""", vals, page_size=1000)
            cur.execute("SELECT count(*) FROM cadastro_cliente WHERE cliente=%s",
                        (cliente,))
            depois = cur.fetchone()[0]
        con.commit()
        return {"recebidas": len(linhas), "novas": depois - antes,
                "atualizadas": len(linhas) - (depois - antes), "total": depois}
    finally:
        if fechar:
            con.close()


# ── Cruzamento ─────────────────────────────────────────────────────────────
def cruzar(cidade: str, con=None) -> dict:
    """Liga cada imóvel do cadastro ao POI mais próximo e aplica a flag.

    A regra é a do usuário, na ordem em que ele a disse:
      · comercial na base + POI  → `ja_cadastrado` (não visitar);
      · NÃO comercial na base mas com POI comercial na porta → `reclassificar`,
        levando junto a confiança daquele POI (é o que decide se vale a visita);
      · POI sem nenhuma ligação correspondente → `novo_comercial`, acresce a base;
      · ligação sem POI → `sem_poi`.

    Cada POI é reivindicado por UM imóvel só — o de melhor casamento. Sem isso um
    único comércio "explicava" dezenas de ligações vizinhas.
    """
    from psycopg2.extras import execute_values
    from cnpj_local import chave_endereco
    fechar = con is None
    con = con or bc.conectar()
    try:
        with con.cursor() as cur:
            cur.execute("""SELECT c.id, c.lat, c.lng, c.e_comercial, c.cep, c.numero,
                                  la.logradouro_marcado, la.numero_canonico
                             FROM cadastro_cliente c
                             LEFT JOIN logradouro_ajustado la
                                    ON la.fonte = 'cadastro'
                                   AND la.record_id = c.id::text
                            WHERE c.cidade ILIKE %s AND c.lat IS NOT NULL""", (cidade,))
            cads = cur.fetchall()
            # O LOGRADOURO NORMALIZADO VEM JUNTO, e o POI fundido fica de fora.
            #
            # Fundido nao e um ponto: ele foi absorvido e o que estava pendurado
            # nele passou para o sobrevivente. Sem este filtro o cruzamento
            # apontava para pontos que sumiram do mapa — MEDIDO em 28/08/2026:
            # 255 ligacoes do cadastro apontando para POI fundido.
            cur.execute("""SELECT p.id, COALESCE(p.maps_lat, p.lat_origem),
                                  COALESCE(p.maps_lng, p.lng_origem), p.cnpj_conf,
                                  p.nome, p.endereco,
                                  la.logradouro_marcado, la.numero_canonico
                             FROM pois p
                             LEFT JOIN logradouro_ajustado la
                                    ON la.fonte = 'pois'
                                   AND la.record_id = p.id::text
                            WHERE p.cidade ILIKE %s
                              AND p.match_valido IS NOT FALSE
                              AND p.fundido_em IS NULL
                              AND COALESCE(p.maps_lat, p.lat_origem) IS NOT NULL""",
                        (cidade,))
            pois = cur.fetchall()

        # índice dos POIs por logradouro normalizado, por (cep, número) e por célula
        CEL = 0.0005                                   # ~55 m
        por_via, por_end, grade = (defaultdict(list), defaultdict(list),
                                   defaultdict(list))
        for p in pois:
            grade[(round(p[1] / CEL), round(p[2] / CEL))].append(p)
            k = chave_endereco(p[5] or "")
            if k:
                por_end[(k[0], k[1])].append(p)
            via = via_sem_tipo(p[6] or "")
            numc = "".join(c for c in str(p[7] or "") if c.isdigit())
            if via and numc:
                por_via[(via, numc)].append(p)

        # 1ª passada: propõe (imóvel, POI, distância, força). A força ordena, e a
        # ordem é a que o dono do produto declarou em 28/08/2026:
        #
        #     3  logradouro NORMALIZADO + número     "o máximo de confiança"
        #     2  CEP + número
        #     1  só geografia
        #
        # A FORÇA 3 NÃO TEM TETO DE DISTÂNCIA, e é deliberado. Se as duas fontes
        # dizem a mesma via e a mesma porta, quem erra é a coordenada — é a mesma
        # razão pela qual a fusão une "mesmo nome, mesma rua e mesmo número" a
        # qualquer distância. Pôr um raio aqui seria deixar a coordenada, que é o
        # dado fraco, vetar o endereço, que é o forte.
        prop = []
        for cid, la, lo, ecom, cep, num, via_c, numc_c in cads:
            via = via_sem_tipo(via_c or "")
            numc = "".join(c for c in str(numc_c or "") if c.isdigit())
            achou = False
            if via and numc:
                for p in por_via.get((via, numc), ()):
                    d = _dist_m(la, lo, p[1], p[2])
                    prop.append((3, -d, cid, p, d, ecom))
                    achou = True
            if achou:
                continue

            cep = "".join(c for c in str(cep or "") if c.isdigit())
            num = "".join(c for c in str(num or "") if c.isdigit())
            for p in por_end.get((cep, num), ()):
                d = _dist_m(la, lo, p[1], p[2])
                if d <= 250:                # mesmo CEP+número: a folga é do geocode
                    prop.append((2, -d, cid, p, d, ecom))
                    achou = True
            if achou:
                continue
            melhor, dmin = None, 1e9
            cy, cx = round(la / CEL), round(lo / CEL)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for p in grade.get((cy + dy, cx + dx), ()):
                        d = _dist_m(la, lo, p[1], p[2])
                        if d < dmin:
                            melhor, dmin = p, d
            if melhor is not None and dmin <= RAIO_SO_GEO_M:
                prop.append((1, -dmin, cid, melhor, dmin, ecom))

        # melhor proposta por POI, e melhor por imóvel (um para um)
        prop.sort(key=lambda t: (-t[0], -t[1]))
        poi_dono, cad_par = {}, {}
        for forca, _neg, cid, p, d, ecom in prop:
            if p[0] in poi_dono or cid in cad_par:
                continue
            poi_dono[p[0]] = cid
            cad_par[cid] = (p, d, ecom, forca)

        updates = []
        for cid, la, lo, ecom, _cep, _num, _via, _numc in cads:
            par = cad_par.get(cid)
            if par is None:
                updates.append((cid, None, "sem_poi", FLAGS["sem_poi"], None, None))
                continue
            p, d, _e, forca = par
            conf = p[3] or "sem confiança registrada"
            como = ("logradouro normalizado" if forca == 3 else
                    ("CEP e número" if forca == 2 else f"{d:.0f} m"))
            flag = "ja_cadastrado" if ecom else _flag_por_confianca(p[3])
            updates.append((cid, p[0], flag,
                            f"{FLAGS[flag]} POI: {p[4][:60]} ({como})",
                            round(d, 1), conf))

        with con.cursor() as cur:
            execute_values(cur, """
                UPDATE cadastro_cliente c SET poi_id = v.poi_id, cruz_flag = v.flag,
                       cruz_motivo = v.motivo, cruz_dist_m = v.dist, cruz_conf = v.conf
                  FROM (VALUES %s) AS v(id, poi_id, flag, motivo, dist, conf)
                 WHERE c.id = v.id""", updates, page_size=1000,
                template="(%s,%s::bigint,%s,%s,%s::double precision,%s)")
        con.commit()

        res = {"cadastro": len(cads), "pois": len(pois),
               "novo_comercial": sum(1 for p in pois if p[0] not in poi_dono),
               "por_logradouro": sum(1 for v in cad_par.values() if v[3] == 3),
               "por_endereco": sum(1 for v in cad_par.values() if v[3] == 2),
               "so_geo": sum(1 for v in cad_par.values() if v[3] == 1)}
        for f in ORDEM_FLAGS:
            if f != "novo_comercial":
                res[f] = sum(1 for u in updates if u[2] == f)
        return res
    finally:
        if fechar:
            con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--criar", action="store_true")
    ap.add_argument("--importar")
    ap.add_argument("--cliente", default="corsan")
    ap.add_argument("--cruzar", action="store_true")
    ap.add_argument("--cidade", default="Canoas")
    ap.add_argument("--modelo")
    a = ap.parse_args()
    if a.criar:
        criar_tabela()
        print(f"tabela cadastro_cliente pronta ({len(MAPA)} colunas padronizadas)")
    if a.modelo:
        modelo_csv(a.modelo)
        print(f"modelo salvo em {a.modelo}")
    if a.importar:
        linhas, ref, avisos = ler_arquivo(a.importar)
        for w in avisos:
            print("  ⚠️ " + w)
        r = importar(linhas, a.cliente, ref)
        print(f"✅ {r['recebidas']} linhas · {r['novas']} novas · "
              f"{r['atualizadas']} atualizadas · total {r['total']}")
    if a.cruzar:
        r = cruzar(a.cidade)
        print(f"cadastro {r['cadastro']:,} × POIs {r['pois']:,}")
        for k in ORDEM_FLAGS:
            print(f"   {r[k]:>7,}  {k:<21} {FLAGS[k]}")
        print(f"   (casados por endereço: {r['por_endereco']:,} · "
              f"só por proximidade: {r['so_geo']:,})")


if __name__ == "__main__":
    main()
