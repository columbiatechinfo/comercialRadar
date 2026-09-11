# -*- coding: utf-8 -*-
"""gerar_kml.py — a lista de visita: so o que a IA aprovou, em ordem de rua.

O KML E PARA IR A CAMPO, e isso decide tudo que ele traz. Nao e um despejo do
banco: e um roteiro. Por isso

  ORDEM POR RUA E NUMERO, e nao por id nem por veredito. Quem visita anda por
  quarteirao; uma lista ordenada por id manda a pessoa atravessar a cidade
  quatro vezes. As pastas separam o grau de certeza, mas dentro de cada uma a
  ordem e a do calcamento.

  O NUMERO DA LIGACAO NO NOME DO PINO. E o que a pessoa precisa digitar no
  sistema da companhia quando chegar la. Sem ele, o cadastro tem de ser
  procurado pelo endereco, em pe na calcada.

  A JUSTIFICATIVA INTEIRA NO BALAO, e o link que REABRE O PANORAMA exato que a
  IA viu. Quem chega e encontra uma casa fechada precisa poder conferir, ali
  mesmo no celular, se a foto era outra — duvida em campo custa mais que byte.

DOIS ARQUIVOS, e a razao e do Google: o Google Earth le KML com estilo, pasta e
balao formatado; o Google Maps (Meus Mapas) tambem importa KML, mas engasga com
HTML longo. O `.kml` serve ao Earth; o `.csv` serve para importar em qualquer
coisa e para abrir no telefone sem app nenhum.
"""
from __future__ import annotations

import csv
import html
import io
import os
import re

import base_comum as bc

SAIDA_DIR = "/app/saida"
APROVADOS = ("aprovado", "aprovado_exato", "aprovado_comercial")

PASTA = {
    "aprovado": ("Aprovado",
                 "As fontes mostram que ao menos um POI da lista pertence a esta "
                 "ligacao (modelo de 12/09/2026)."),
    "aprovado_exato": ("Aprovado - estabelecimento exato",
                       "O negocio do cadastro foi identificado na fachada. "
                       "Ja se sabe por quem perguntar."),
    "aprovado_comercial": ("Aprovado - atividade comercial",
                           "O imovel tem comercio visivel, mas nao se confirmou "
                           "que e o do cadastro. Descobrir no local."),
}
# Pino verde para o exato, ambar para o comercial. Sao os icones que o proprio
# Google hospeda: nao dependem de arquivo junto do KML.
ICONE = {
    "aprovado": "http://maps.google.com/mapfiles/kml/paddle/grn-circle.png",
    "aprovado_exato": "http://maps.google.com/mapfiles/kml/paddle/grn-circle.png",
    "aprovado_comercial": "http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png",
}
ESPECIE = {1: "domicilio particular", 2: "domicilio coletivo",
           3: "agropecuario", 4: "ensino", 5: "saude",
           6: "outras finalidades", 7: "em construcao", 8: "religioso"}
SECAO = {"A": "agropecuaria", "B": "extrativas", "C": "transformacao",
         "D": "eletricidade e gas", "E": "agua e esgoto", "F": "construcao",
         "G": "comercio e reparacao", "H": "transporte",
         "I": "alojamento e alimentacao", "J": "informacao", "K": "financeiras",
         "L": "imobiliarias", "M": "profissionais e tecnicas",
         "N": "administrativas", "O": "administracao publica", "P": "educacao",
         "Q": "saude", "R": "artes e esporte", "S": "outros servicos",
         "T": "domesticos", "U": "organismos internacionais"}

con = bc.conectar()
cur = con.cursor()
cur.execute("""
    select v.poi_id, coalesce(p.nome,''), coalesce(p.fonte,''),
           coalesce(p.categoria,''), coalesce(p.endereco,''),
           coalesce(p.cidade,''), coalesce(p.uf,''),
           v.veredito, coalesce(v.justificativa,''),
           v.especie_cnefe, v.secao_cnae, v.medidores,
           st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
           coalesce(p.cnpj,''), coalesce(p.telefone,''),
           to_char(v.avaliado_em at time zone 'America/Sao_Paulo','DD/MM/YYYY')
      from radar_comercial.poi_veredito v
      join radar_comercial.pois p on p.id = v.poi_id
     where v.veredito = any(%s) and p.pt_geo is not null
""", (list(APROVADOS),))
COLS = ("id nome fonte categoria endereco cidade uf veredito just especie "
        "secao medidores lat lng cnpj telefone data").split()
pois = [dict(zip(COLS, r)) for r in cur.fetchall()]
print("aprovados com coordenada:", len(pois))
if not pois:
    raise SystemExit("nada a exportar")

ids = [p["id"] for p in pois]
cur.execute("""select lp.poi_id, lp.ligacao, coalesce(l.categoria,''),
                      coalesce(l.sit_ligacao,'')
                 from radar_comercial.ligacao_poi lp
                 left join resources_root.cadastro_corsan l
                        on l.num_ligacao::text = lp.ligacao
                where lp.poi_id = any(%s)""", (ids,))
ligs = {}
for pid, lig, cat, sit in cur.fetchall():
    ligs.setdefault(pid, []).append((str(lig), cat, sit))

cur.execute("""select poi_id, fonte, url from radar_comercial.poi_link
                where poi_id = any(%s) and ativo""", (ids,))
links = {}
for pid, f, u in cur.fetchall():
    links.setdefault(pid, []).append((f, u))

cur.execute("""select poi_id, coalesce(pano_id,''), heading, fov
                 from radar_comercial.poi_evidencia
                where poi_id = any(%s) and tipo = 'sv_frente'
                  and dados is not null""", (ids,))
panos = {r[0]: r[1:] for r in cur.fetchall()}

# O CODIGO DO CNAE NAO SE LE NA CALCADA. A `pois.categoria` guarda "4520001"
# quando o POI veio da Receita, e quem esta no campo precisa de "manutencao e
# reparacao mecanica de veiculos". A traducao vem da `rf_cnaes`, e vem de UMA
# vez para os 37 — nao uma consulta por pino.
codigos = sorted({(p["categoria"] or "").strip() for p in pois
                  if (p["categoria"] or "").strip().isdigit()})
CNAE_NOME = {}
if codigos:
    cur.execute("select codigo, descricao from resources_root.rf_cnaes "
                "where codigo = any(%s)", (codigos,))
    CNAE_NOME = dict(cur.fetchall())
con.close()

# A MESMA RUA VINHA GRAFADA DE DOIS JEITOS, e isso quebrava o roteiro.
#
# O endereco da `pois` vem de fontes diferentes e cada uma escreve a sua: "Av.
# Rio Grande do Sul" do Maps, "AVENIDA RIO GRANDE DO SUL" da Receita, "R.
# Jaguari" e "RUA JAGUARI". Ordenar por esse texto cru poe a mesma avenida em
# dois blocos separados da lista — e quem seguir a lista percorre a rua, sai
# dela, e volta.
#
# A normalizacao e so para AGRUPAR E ORDENAR. O endereco exibido no balao
# continua sendo o original, com a grafia da fonte: quem confere no local
# precisa ver o que o cadastro tem escrito, nao a nossa versao arrumada.
_TIPO = [
    ("AVENIDA", ("AV", "AVE", "AVENIDA")),
    ("RUA", ("R", "RU", "RUA")),
    ("TRAVESSA", ("TV", "TRAV", "TRAVESSA")),
    ("ESTRADA", ("EST", "ESTR", "ESTRADA")),
    ("RODOVIA", ("ROD", "RODOVIA")),
    ("PRACA", ("PC", "PCA", "PRACA")),
]
_ACENTO = str.maketrans("ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ", "AAAAAEEEEIIIIOOOOOUUUUC")


# O TIPO DA VIA SAI DA CHAVE, e isso e decisao tomada com o dado na mao.
#
# O POI 351446 aparece duas vezes: "AVENIDA RIO GRANDE DO SUL, 177" pela
# Receita e "RUA RIO GRANDE DO SUL, 177" por outra fonte — mesma ligacao, mesmo
# numero, mesmo imovel. Manter o tipo na chave punha o mesmo endereco em duas
# pastas e mandava a pessoa voltar. Num roteiro dentro de UMA quadra o risco
# oposto — existir uma Rua X e uma Avenida X distintas — e menor que o de
# percorrer a mesma rua duas vezes, e o endereco original continua no balao.
#
# O tipo nao se perde: o nome da PASTA usa o que aparece mais no grupo.
def rua_normal(end: str) -> str:
    """O nome da rua para AGRUPAR: sem acento, sem tipo de via, em caixa alta."""
    p = _partes(end)
    if not p:
        return ""
    if _tipo_de(p[0]):
        p = p[1:]
    return " ".join(p)


def _partes(end: str) -> list:
    s = (end or "").upper().translate(_ACENTO)
    s = s.split(",")[0].split(" - ")[0]
    return re.sub(r"[^A-Z0-9 ]", " ", s).split()


def _tipo_de(palavra: str):
    for canon, formas in _TIPO:
        if palavra in formas:
            return canon
    return None


def TIPO_COMUM(grupo) -> str:
    """O tipo de via que mais aparece no grupo — e o rotulo honesto da pasta."""
    cont = {}
    for p in grupo:
        pes = _partes(p["endereco"])
        tp = _tipo_de(pes[0]) if pes else None
        if tp:
            cont[tp] = cont.get(tp, 0) + 1
    if not cont:
        return ""
    return max(cont.items(), key=lambda x: x[1])[0]


# O NUMERO E ANCORADO NO COMECO, e nao procurado no endereco inteiro.
#
# `re.search(r",\s*(\d+)")` pegava o CEP quando a via nao tinha numero:
# "R. Jaguari - Mathias Velho, Canoas - RS, 92330-120" virava "no 92330", e o
# ponto ia para o fim da rua. Ancorado, so casa o numero que vem logo apos o
# nome da via — que e o unico que e porta.
_PORTA = re.compile(r"^[^,]+,\s*(\d{1,6})(?!\d)")


def numero_de(end: str) -> int:
    """O numero da porta. Zero quando o endereco nao traz — vai para o comeco."""
    m = _PORTA.match(end or "")
    return int(m.group(1)) if m else 0


def chave_rua(p):
    """Rua canonica, depois o numero como NUMERO.

    Ordenar o numero como texto poe o 100 antes do 9 e faz a pessoa subir e
    descer a mesma quadra.
    """
    return (rua_normal(p["endereco"]), numero_de(p["endereco"]), p["id"])


def so_residenciais(pid):
    """As ligacoes RESIDENCIAIS ativas — que sao o motivo da visita."""
    return [l for l in ligs.get(pid, [])
            if l[1].upper() == "RESIDENCIAL" and l[2].upper() == "ATIVA"]


def link_pano(pid):
    d = panos.get(pid)
    if not d or not d[0] or d[1] is None:
        return None
    return ("https://www.google.com/maps/@?api=1&map_action=pano&pano=%s"
            "&heading=%.0f&pitch=5&fov=%d" % (d[0], float(d[1]), int(d[2] or 80)))


def balao(p):
    e = html.escape
    lin = ["<b>%s</b>" % e(p["nome"] or "(sem nome)")]
    cat = (p["categoria"] or "").strip()
    if cat:
        # O codigo fica junto, e nao no lugar: quem for lancar no sistema da
        # companhia precisa dele, e quem esta na porta precisa do nome.
        nome_cat = CNAE_NOME.get(cat)
        lin.append(" <i>%s</i>" % e("%s (CNAE %s)" % (nome_cat, cat)
                                    if nome_cat else cat))
    lin.append("<br>%s" % e(p["endereco"] or "sem endereco"))
    res = so_residenciais(p["id"])
    if res:
        lin.append("<br><b>Ligacao(oes) residencial(is) ativa(s): %s</b>"
                   % e(", ".join(l[0] for l in res)))
    outras = [l for l in ligs.get(p["id"], []) if l not in res]
    if outras:
        lin.append("<br><small>outras no endereco: %s</small>"
                   % e(", ".join("%s (%s/%s)" % l for l in outras)))
    lin.append("<hr><b>%s</b><br>%s" % (e(PASTA[p["veredito"]][0]), e(p["just"])))
    marcas = []
    if p["especie"]:
        marcas.append("especie %d - %s"
                      % (p["especie"], ESPECIE.get(p["especie"], "?")))
    if p["secao"]:
        marcas.append("secao %s - %s" % (p["secao"], SECAO.get(p["secao"], "?")))
    if p["medidores"] is not None:
        marcas.append("%d medidor(es)" % p["medidores"])
    if marcas:
        lin.append("<br><small>%s</small>" % e(" | ".join(marcas)))
    if p["cnpj"]:
        lin.append("<br><small>CNPJ %s</small>" % e(p["cnpj"]))
    if p["telefone"]:
        lin.append("<br><small>tel %s</small>" % e(p["telefone"]))
    lp = link_pano(p["id"])
    if lp:
        lin.append('<br><br><a href="%s">Reabrir o panorama que a IA viu</a>'
                   % e(lp))
    for f, u in (links.get(p["id"]) or [])[:3]:
        lin.append('<br><a href="%s">%s</a>' % (e(u), e(f)))
    lin.append("<br><small>avaliado em %s | POI #%d | fonte %s</small>"
               % (e(p["data"]), p["id"], e(p["fonte"])))
    return "".join(lin)


def estilos():
    s = []
    for v, url in ICONE.items():
        s.append('<Style id="%s"><IconStyle><scale>1.15</scale>'
                 '<Icon><href>%s</href></Icon></IconStyle>'
                 '<LabelStyle><scale>0.85</scale></LabelStyle></Style>'
                 % (v, url))
    return "".join(s)


def placemark(p, n):
    res = so_residenciais(p["id"])
    # O NOME DO PINO COMECA PELO NUMERO DA PORTA, que e o que se le andando na
    # rua — antes do nome do lugar, que muitas vezes e um CNPJ de MEI e nao
    # existe em placa nenhuma. Depois vem a ligacao a conferir no sistema.
    num = numero_de(p["endereco"])
    nome = "%s%s%s" % (("no %d - " % num) if num else "",
                       p["nome"] or "(sem nome)",
                       (" - lig %s" % res[0][0]) if res else "")
    campos = (("poi_id", p["id"]), ("veredito", p["veredito"]),
              ("ligacoes_residenciais", ", ".join(l[0] for l in res)),
              ("endereco", p["endereco"]), ("categoria", p["categoria"]),
              ("fonte", p["fonte"]), ("cnpj", p["cnpj"]),
              ("medidores", "" if p["medidores"] is None else p["medidores"]),
              ("especie_cnefe", p["especie"] or ""),
              ("secao_cnae", p["secao"] or ""))
    dados = "".join('<Data name="%s"><value>%s</value></Data>'
                    % (k, html.escape(str(v))) for k, v in campos)
    return ('<Placemark><name>%s</name><styleUrl>#%s</styleUrl>'
            '<description><![CDATA[%s]]></description>'
            '<ExtendedData>%s</ExtendedData>'
            '<Point><coordinates>%.7f,%.7f,0</coordinates></Point></Placemark>'
            % (html.escape(nome), p["veredito"], balao(p), dados,
               p["lng"], p["lat"]))


os.makedirs(SAIDA_DIR, exist_ok=True)
partes = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
    "<name>Radar Comercial - visitas aprovadas - quadra de Canoas</name>",
    "<description><![CDATA[%s]]></description>" % html.escape(
        "%d imoveis faturados como RESIDENCIAL em que a IA encontrou atividade "
        "economica. UMA PASTA POR RUA, em ordem de numero, para a visita ser "
        "uma passada so. PINO VERDE: o estabelecimento do cadastro foi "
        "identificado na fachada. PINO AMBAR: ha comercio visivel, mas nao se "
        "confirmou que e aquele - descobrir no local. O balao traz o numero da "
        "ligacao a conferir no sistema e o link que reabre o panorama exato que "
        "a IA analisou." % len(pois)),
    estilos(),
]
# UMA PASTA POR RUA, E NAO POR VEREDITO — porque isto e um roteiro, e nao um
# relatorio. Separado por veredito, quem visita percorreria cada rua DUAS vezes:
# uma atras dos exatos, outra atras dos comerciais. O grau de certeza nao se
# perde: ele esta na COR do pino (verde exato, ambar comercial) e no balao.
n_total = 0
por_rua = {}
for p in sorted(pois, key=chave_rua):
    por_rua.setdefault(rua_normal(p["endereco"]) or "SEM ENDERECO", []).append(p)

for rua, grupo in por_rua.items():
    exatos = sum(1 for p in grupo if p["veredito"] == "aprovado_exato")
    rotulo = ("%s %s" % (TIPO_COMUM(grupo).title(), rua.title())).strip()
    partes.append('<Folder><name>%s (%d)</name>'
                  '<description><![CDATA[%s]]></description><open>1</open>'
                  % (html.escape(rotulo), len(grupo),
                     html.escape("%d ponto(s): %d verde(s) - estabelecimento "
                                 "identificado, %d ambar(es) - comercio "
                                 "visivel a confirmar no local."
                                 % (len(grupo), exatos, len(grupo) - exatos))))
    for i, p in enumerate(grupo, 1):
        n_total += 1
        partes.append(placemark(p, i))
    partes.append("</Folder>")
partes.append("</Document></kml>")

kml = "\n".join(partes)
arq = os.path.join(SAIDA_DIR, "visitas_aprovadas_canoas.kml")
io.open(arq, "w", encoding="utf-8").write(kml)
print("KML: %s | %d pinos | %.0f KB" % (arq, n_total, len(kml.encode()) / 1024))

arq_csv = os.path.join(SAIDA_DIR, "visitas_aprovadas_canoas.csv")
with io.open(arq_csv, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["ordem", "veredito", "ligacoes_residenciais_ativas", "nome",
                "categoria", "categoria_descricao",
                "endereco", "cidade", "uf", "latitude", "longitude",
                "medidores", "especie_cnefe", "secao_cnae", "cnpj", "telefone",
                "fonte", "poi_id", "justificativa", "panorama"])
    # A MESMA ORDEM DO KML — rua e numero, numa passada so. Duas ordens para o
    # mesmo roteiro fariam a planilha e o mapa discordarem sobre onde ir depois.
    n = 0
    for i, p in enumerate(sorted(pois, key=chave_rua), 1):
        n += 1
        res = so_residenciais(p["id"])
        w.writerow([i, PASTA[p["veredito"]][0], ", ".join(l[0] for l in res),
                    p["nome"], p["categoria"],
                    CNAE_NOME.get((p["categoria"] or "").strip(), ""),
                    p["endereco"], p["cidade"],
                    p["uf"], "%.7f" % p["lat"], "%.7f" % p["lng"],
                    "" if p["medidores"] is None else p["medidores"],
                    p["especie"] or "", p["secao"] or "", p["cnpj"],
                    p["telefone"], p["fonte"], p["id"], p["just"],
                    link_pano(p["id"]) or ""])
print("CSV: %s | %d linhas" % (arq_csv, n))
