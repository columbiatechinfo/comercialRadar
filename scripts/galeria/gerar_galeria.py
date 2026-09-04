# -*- coding: utf-8 -*-
"""gerar_galeria.py — a quadra inteira numa pagina: imagens, prompts, vereditos.

Gera UM arquivo HTML com tudo embutido. As imagens vao como data: URI porque a
pagina precisa abrir sozinha, sem servidor e sem depender do banco.

O TETO E DE 16 MB no destino, e base64 infla 37%. Por isso a escala das imagens
nao e fixa: o script tenta uma escada de (largura, qualidade) e para na primeira
que couber no orcamento. Assim a pagina fica com a melhor imagem que cabe, em
vez de ficar com uma imagem escolhida no chute.
"""
from __future__ import annotations

import base64
import html
import io
import json
import os
import sys

import cv2
import numpy as np

import base_comum as bc

SAIDA = "/app/saida/galeria_quadra.html"
ORCAMENTO_BYTES = 9_200_000          # bytes CRUS de imagem; base64 vira ~12,6 MB
ESCADA = [(420, 62), (380, 58), (340, 54), (300, 50), (260, 45)]
LARG_PAGINA = 300                    # o print do Airbnb e altissimo: entra estreito


def _jpeg(raw: bytes, larg: int, q: int) -> bytes | None:
    im = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        return None
    h, w = im.shape[:2]
    if w > larg:
        im = cv2.resize(im, (larg, max(int(h * larg / w), 1)),
                        interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes() if ok else None


# ── o que se lê do banco ───────────────────────────────────────────────────
con = bc.conectar()
cur = con.cursor()

cur.execute("""
    select v.poi_id, coalesce(p.nome,''), coalesce(p.fonte,''),
           coalesce(p.categoria,''), coalesce(p.endereco,''),
           coalesce(p.cidade,''), coalesce(p.uf,''),
           v.veredito, coalesce(v.justificativa,''),
           v.especie_cnefe, v.secao_cnae, v.medidores,
           v.percepcao, coalesce(v.modelo,''), v.imagens, v.segundos,
           to_char(v.avaliado_em at time zone 'America/Sao_Paulo',
                   'DD/MM/YYYY HH24:MI'),
           st_y(p.pt_geo::geometry), st_x(p.pt_geo::geometry),
           coalesce(p.cnpj,''), coalesce(p.telefone,'')
      from radar_comercial.poi_veredito v
      join radar_comercial.pois p on p.id = v.poi_id
     order by case v.veredito
                when 'aprovado_exato' then 1 when 'aprovado_comercial' then 2
                when 'revisao_humana' then 3 else 4 end, v.poi_id
""")
COLS = ("id nome fonte categoria endereco cidade uf veredito justificativa "
        "especie secao medidores percepcao modelo imagens segundos avaliado "
        "lat lng cnpj telefone").split()
pois = [dict(zip(COLS, r)) for r in cur.fetchall()]

# AMOSTRA PARA CONFERIR O LEIAUTE. A pagina cheia tem 11,7 MB e nenhum
# visualizador a abre de graca; com quatro cartoes ela abre em qualquer lugar e
# o CSS e exatamente o mesmo. Confere-se a forma na pequena, publica-se a
# inteira. `GALERIA_SAIDA` deixa as duas conviverem sem uma apagar a outra.
_lim = int(os.environ.get("GALERIA_LIMITE") or 0)
if _lim:
    vistos, amostra = set(), []
    for _p in pois:                      # um de cada veredito primeiro
        if _p["veredito"] not in vistos:
            vistos.add(_p["veredito"])
            amostra.append(_p)
    pois = (amostra + [x for x in pois if x not in amostra])[:_lim]
SAIDA = os.environ.get("GALERIA_SAIDA") or SAIDA
print("POIs com veredito:", len(pois))

ids = [p["id"] for p in pois]

cur.execute("""select poi_id, fonte, url, coalesce(rotulo,'')
                 from radar_comercial.poi_link
                where poi_id = any(%s) and ativo order by poi_id, fonte""", (ids,))
links = {}
for pid, f, u, r in cur.fetchall():
    links.setdefault(pid, []).append({"fonte": f, "url": u, "rotulo": r})

cur.execute("""
    select lp.poi_id, lp.ligacao, coalesce(l.categoria,''),
           coalesce(l.sit_ligacao,''), coalesce(lp.origem,'')
      from radar_comercial.ligacao_poi lp
      left join resources_root.cadastro_corsan l
             on l.num_ligacao::text = lp.ligacao
     where lp.poi_id = any(%s)""", (ids,))
ligacoes = {}
for pid, lig, cat, sit, orig in cur.fetchall():
    ligacoes.setdefault(pid, []).append(
        {"ligacao": lig, "categoria": cat, "situacao": sit, "origem": orig})

cur.execute("""select poi_id, tipo, dados, coalesce(motivo_falha,''),
                      heading, distancia_m, largura_px, altura_px, bytes_tam,
                      coalesce(pano_id,''), fov
                 from radar_comercial.poi_evidencia
                where poi_id = any(%s)""", (ids,))
brutas = {}
for pid, tipo, dados, falha, head, dist, lp, ap, tam, pano, fov in cur.fetchall():
    brutas.setdefault(pid, {})[tipo] = {
        "raw": bytes(dados) if dados is not None else None, "falha": falha,
        "heading": head, "dist": dist, "w": lp, "h": ap, "tam": tam,
        "pano": pano, "fov": fov}

# o dado do iFood que vai à IA como texto
cur.execute("""select poi_id, nota, avaliacoes, cnpj, telefone,
                      to_char(visto_em,'DD/MM/YYYY'), bruto->>'disponivel'
                 from radar_comercial.ifood_merchant where poi_id = any(%s)""",
            (ids,))
ifood = {r[0]: {"nota": r[1], "aval": r[2], "cnpj": r[3], "tel": r[4],
                "visto": r[5], "disp": r[6]} for r in cur.fetchall()}
con.close()

# ── as imagens, na melhor escala que couber ───────────────────────────────
ORDEM = ["satelite", "sv_frente", "sv_fundo", "pagina_airbnb"]
escolhida = None
for larg, q in ESCADA:
    total = 0
    for pid, m in brutas.items():
        for tipo, d in m.items():
            if d["raw"] is None:
                continue
            b = _jpeg(d["raw"], LARG_PAGINA if tipo.startswith("pagina") else larg, q)
            total += len(b or b"")
    print("  escala %dpx q%d → %.1f MB crus" % (larg, q, total / 1e6))
    if total <= ORCAMENTO_BYTES:
        escolhida = (larg, q, total)
        break
if not escolhida:
    escolhida = (ESCADA[-1][0], ESCADA[-1][1], total)
LARG, Q, TOT = escolhida
print("escala escolhida: %dpx q%d (%.1f MB crus)" % (LARG, Q, TOT / 1e6))

for pid, m in brutas.items():
    for tipo, d in m.items():
        if d["raw"] is None:
            d["b64"] = None
            continue
        b = _jpeg(d["raw"], LARG_PAGINA if tipo.startswith("pagina") else LARG, Q)
        d["b64"] = base64.b64encode(b).decode() if b else None
        d["raw"] = None

# ── os prompts, lidos do proprio modulo que os enviou ─────────────────────
sys.path.insert(0, ".")
import avaliar_ia as ai                                        # noqa: E402

PROMPTS = [
    ("Percepção · satélite", "1 imagem — o satélite com o anel sobre a coordenada",
     ai.PROMPT_SAT % {"ignorar": ai.IGNORAR}),
    ("Percepção · fachada", "1 imagem — a foto de rua com a mira sobre o imóvel",
     ai.PROMPT_FACHADA % {"ignorar": ai.IGNORAR}),
    ("Percepção · lado oposto", "1 imagem — a mesma câmera girada 180°",
     ai.PROMPT_FUNDO % {"ignorar": ai.IGNORAR}),
    ("Percepção · página do anúncio", "1 imagem — a página inteira do Airbnb",
     ai.PROMPT_PAGINA),
    ("Julgamento", "nenhuma imagem — só a percepção acima e o cadastro",
     ai.PROMPT_JULGAR),
    ("Julgamento · hospedagem", "nenhuma imagem — escala idêntica, prova diferente",
     ai.PROMPT_JULGAR_HOSPEDAGEM),
]


def cadastro_texto(p):
    """O mesmo bloco que foi ao modelo, remontado a partir do que está gravado."""
    linhas = [
        "- nome: %s" % (p["nome"] or "(sem nome)"),
        "- categoria declarada: %s" % (p["categoria"] or "(sem categoria)"),
        "- fonte do dado: %s" % p["fonte"],
        "- endereço: %s — %s/%s" % (p["endereco"] or "(sem endereço)",
                                    p["cidade"], p["uf"]),
    ]
    f = ifood.get(p["id"])
    if f:
        linhas.append("- iFood em %s: loja %s, nota %s, %s avaliação(ões)%s%s"
                      % (f["visto"] or "?",
                         "DISPONÍVEL" if f["disp"] == "true" else "não disponível",
                         f["nota"] if f["nota"] is not None else "?",
                         f["aval"] if f["aval"] is not None else "?",
                         ", CNPJ %s" % f["cnpj"] if f["cnpj"] else "",
                         ", telefone %s" % f["tel"] if f["tel"] else ""))
    for l in (links.get(p["id"]) or [])[:4]:
        linhas.append("- link: %s" % l["url"])
    return "\n".join(linhas)


VER_ROTULO = {
    "aprovado_exato": "Aprovado · estabelecimento exato",
    "aprovado_comercial": "Aprovado · atividade comercial",
    "revisao_humana": "Revisão humana",
    "reprovado": "Reprovado",
}
ESPECIE = {1: "domicílio particular", 2: "domicílio coletivo",
           3: "agropecuário", 4: "ensino", 5: "saúde",
           6: "outras finalidades", 7: "em construção", 8: "religioso"}
SECAO = {
    "A": "agropecuária", "B": "indústrias extrativas",
    "C": "indústrias de transformação", "D": "eletricidade e gás",
    "E": "água e esgoto", "F": "construção", "G": "comércio e reparação",
    "H": "transporte e armazenagem", "I": "alojamento e alimentação",
    "J": "informação e comunicação", "K": "atividades financeiras",
    "L": "atividades imobiliárias", "M": "profissionais e técnicas",
    "N": "administrativas", "O": "administração pública", "P": "educação",
    "Q": "saúde e serviços sociais", "R": "artes, cultura e esporte",
    "S": "outros serviços", "T": "serviços domésticos",
    "U": "organismos internacionais"}
TIPO_ROTULO = {"satelite": "satélite", "sv_frente": "fachada",
               "sv_fundo": "lado oposto", "pagina_airbnb": "página do anúncio"}

# ── a montagem ─────────────────────────────────────────────────────────────
e = html.escape
cartoes = []
for p in pois:
    pid = p["id"]
    ev = brutas.get(pid, {})
    figs = []
    for tipo in ORDEM:
        d = ev.get(tipo)
        if not d:
            continue
        legenda = TIPO_ROTULO[tipo]
        meta = []
        if d["dist"] is not None:
            meta.append("%.0f m da câmera" % float(d["dist"]))
        if d["heading"] is not None:
            meta.append("%.0f°" % float(d["heading"]))
        if d["tam"]:
            meta.append("%.0f KB" % (d["tam"] / 1024))
        if d["b64"]:
            cls = "im pag" if tipo.startswith("pagina") else "im"
            figs.append(
                '<figure class="%s"><img loading="lazy" alt="%s do POI %d" '
                'src="data:image/jpeg;base64,%s" data-cap="%s"><figcaption>'
                '<b>%s</b>%s</figcaption></figure>'
                % (cls, e(legenda), pid, d["b64"],
                   e("%d · %s" % (pid, legenda)), e(legenda),
                   (" · " + e(" · ".join(meta))) if meta else ""))
        else:
            figs.append('<figure class="im vazia"><div class="semfoto">sem imagem'
                        '</div><figcaption><b>%s</b> · %s</figcaption></figure>'
                        % (e(legenda), e(d["falha"] or "não capturada")))

    # OS DOIS LINKS QUE TODO POI TEM, e que são os que servem para auditar.
    #
    # Só 33 dos 143 POIs têm link de anúncio — a maioria veio da Receita, que
    # não publica site. Mas todo POI tem coordenada, e o que foi capturado tem
    # `pano_id`: com ele dá para REABRIR NO GOOGLE exatamente o mesmo panorama,
    # no mesmo ângulo e no mesmo zoom que a IA viu. É a diferença entre
    # "confira você mesmo" e "acredite na imagem que eu recortei".
    proprios = []
    if p["lat"] is not None:
        proprios.append(
            '<a class="lk" href="https://www.google.com/maps/search/?api=1&amp;'
            'query=%.7f,%.7f" target="_blank" rel="noopener">ver no mapa ↗</a>'
            % (p["lat"], p["lng"]))
    d_fr = (brutas.get(pid) or {}).get("sv_frente") or {}
    if d_fr.get("pano") and d_fr.get("heading") is not None:
        proprios.append(
            '<a class="lk" href="https://www.google.com/maps/@?api=1&amp;'
            'map_action=pano&amp;pano=%s&amp;heading=%.0f&amp;pitch=5&amp;fov=%d"'
            ' target="_blank" rel="noopener">reabrir este panorama ↗</a>'
            % (e(d_fr["pano"]), float(d_fr["heading"]),
               int(d_fr.get("fov") or 80)))
    ls = "".join(proprios) + "".join(
        '<a class="lk" href="%s" target="_blank" rel="noopener">%s ↗</a>'
        % (e(l["url"]), e(l["rotulo"] or l["fonte"]))
        for l in (links.get(pid) or []))

    lg = "".join(
        '<span class="lig"><b>%s</b> %s · %s</span>'
        % (e(str(l["ligacao"])), e(l["categoria"] or "—"), e(l["situacao"] or "—"))
        for l in (ligacoes.get(pid) or [])[:6])

    chips = []
    if p["especie"]:
        chips.append('<span class="chip">espécie %d · %s</span>'
                     % (p["especie"], e(ESPECIE.get(p["especie"], "?"))))
    if p["secao"]:
        chips.append('<span class="chip">seção %s · %s</span>'
                     % (e(p["secao"]), e(SECAO.get(p["secao"], "?"))))
    if p["medidores"] is not None:
        cls = "chip forte" if p["medidores"] >= 2 else "chip"
        chips.append('<span class="%s">%d medidor%s</span>'
                     % (cls, p["medidores"], "es" if p["medidores"] != 1 else ""))
    else:
        chips.append('<span class="chip fraco">medidores não contados</span>')

    perc = json.dumps(p["percepcao"], ensure_ascii=False, indent=2)
    prompt_jul = ai.PROMPT_JULGAR % {
        "percepcao": json.dumps(p["percepcao"], ensure_ascii=False, indent=1),
        "cadastro": cadastro_texto(p), "especies": ai.ESPECIES,
        "secoes": "A a U, ver o painel de prompts acima"}

    cartoes.append(
        '<article class="poi" data-v="%s" data-f="%s" '
        'data-busca="%s">'
        '<header><div class="tit"><h3>%s</h3>'
        '<span class="id">#%d</span><span class="fonte">%s</span></div>'
        '<span class="pill %s">%s</span></header>'
        '<p class="end">%s%s</p>'
        '<div class="tira">%s</div>'
        '<p class="just">%s</p>'
        '<div class="chips">%s</div>'
        '%s%s'
        '<details><summary>percepção cega — o que a IA descreveu antes de ver o '
        'cadastro</summary><pre>%s</pre></details>'
        '<details><summary>o prompt de julgamento, como foi enviado</summary>'
        '<pre>%s</pre></details>'
        '<p class="rod">%s · %s imagem(ns) · %.1f s · %s</p>'
        '</article>'
        % (e(p["veredito"]), e(p["fonte"]),
           e((p["nome"] + " " + p["categoria"] + " " + p["endereco"]).lower()),
           e(p["nome"] or "(sem nome)"), pid, e(p["fonte"]),
           e(p["veredito"]), e(VER_ROTULO[p["veredito"]]),
           e(p["endereco"] or "sem endereço"),
           (" · " + e(p["categoria"])) if p["categoria"] else "",
           "".join(figs), e(p["justificativa"]), "".join(chips),
           ('<div class="links">%s</div>' % ls) if ls else "",
           ('<div class="ligs">%s</div>' % lg) if lg else "",
           e(perc), e(prompt_jul),
           e(p["modelo"]), p["imagens"] or 0, float(p["segundos"] or 0),
           e(p["avaliado"])))

placar = {}
for p in pois:
    placar[p["veredito"]] = placar.get(p["veredito"], 0) + 1

prompts_html = "".join(
    '<details class="pr"><summary><b>%s</b> <span>%s</span></summary>'
    '<pre>%s</pre></details>' % (e(t), e(s), e(c)) for t, s, c in PROMPTS)

TPL = io.open("modelo_galeria.html", encoding="utf-8").read()
saida = (TPL.replace("{{CARTOES}}", "".join(cartoes))
            .replace("{{PROMPTS}}", prompts_html)
            .replace("{{PLACAR}}", json.dumps(placar))
            .replace("{{TOTAL}}", str(len(pois)))
            .replace("{{ESCALA}}", "%d px · qualidade %d" % (LARG, Q)))
os.makedirs(os.path.dirname(SAIDA), exist_ok=True)
io.open(SAIDA, "w", encoding="utf-8").write(saida)
print("gerado: %s · %.1f MB" % (SAIDA, os.path.getsize(SAIDA) / 1e6))
