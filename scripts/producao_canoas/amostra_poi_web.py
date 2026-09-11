# -*- coding: utf-8 -*-
"""Amostra dos POIs "busca web" que SERIAM criados — nada e gravado.

Decisao do dono do produto em 11/09/2026: o estabelecimento que so a busca web
acha no endereco da instalacao vira POI com fonte "busca web", mas depois de ver
uma amostra. Esta e a amostra.

Candidato: estabelecimento da pagina com o MESMO endereco, que nao esta fechado
para sempre, cujo nome nao e um endereco, e que nao casa com nenhum POI ja
vinculado a instalacao — nem pelo nome, nem pelo telefone, nem pelo CNPJ.
Uso: python amostra_poi_web.py <saida.html> [quantos]
"""
import base64
import collections
import html
import io
import json
import random
import re
import sys
import unicodedata

from PIL import Image

import base_comum as bc

SAIDA = sys.argv[1]
QUANTOS = int(sys.argv[2]) if len(sys.argv) > 2 else 40
e = html.escape
ENDERECO = re.compile(r"^\s*(r\.|rua|av\.?|avenida|travessa|tv\.?|estrada|rod\.?|alameda|pra[cç]a|\d)", re.I)
PALAVRAS_FRACAS = {"ltda", "me", "mei", "eireli", "sa", "s/a", "comercio", "servicos", "de", "da", "do",
                   "e", "dos", "das", "em", "canoas", "rs", "the", "&"}


def sem_acento(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def fichas(nome):
    return {t for t in re.findall(r"[a-z0-9]{3,}", sem_acento(nome)) if t not in PALAVRAS_FRACAS}


def digitos(s):
    return re.sub(r"\D", "", s or "")


con = bc.conectar()
cur = con.cursor()
cur.execute("""select b.id, b.ligacao, b.consulta, b.motor, b.ia, c.end_ligacao
                 from radar_comercial.busca_web b
                 left join resources_root.cadastro_corsan c on c.num_ligacao::text = b.ligacao
                where b.tipo = 'endereco' and b.ia is not null""")
buscas = cur.fetchall()
ligs = sorted({r[1] for r in buscas})
cur.execute("""select lp.ligacao, p.id, lower(coalesce(p.fonte,'')), coalesce(p.nome,''),
                      coalesce(p.telefone,''), coalesce(p.cnpj,'')
                 from radar_comercial.ligacao_poi lp join radar_comercial.pois p on p.id = lp.poi_id
                where lp.descartado_em is null and p.fundido_em is null and lp.ligacao = any(%s)""", (ligs,))
pois = collections.defaultdict(list)
for l, pid, f, n, t, c in cur.fetchall():
    pois[str(l)].append({"id": pid, "fonte": f, "nome": n, "tel": digitos(t)[-8:], "cnpj": digitos(c)[:8]})

candidatos, motivos = [], collections.Counter()
for bid, lig, consulta, motor, r, end_l in buscas:
    for x in (r or {}).get("estabelecimentos") or []:
        nome = str(x.get("nome") or "").strip()
        if x.get("mesmo_endereco") is False:
            motivos["outro endereço"] += 1
            continue
        if x.get("status") == "fechado_permanente":
            motivos["fechado para sempre"] += 1
            continue
        if not nome or ENDERECO.match(nome):
            motivos["nome é endereço ou vazio"] += 1
            continue
        tel = digitos(x.get("telefone"))[-8:]
        cnpj = digitos(x.get("cnpj"))[:8]
        fx = fichas(nome)
        casou = None
        for p in pois.get(str(lig), []):
            if (tel and tel == p["tel"]) or (cnpj and cnpj == p["cnpj"]) or \
               (fx and len(fx & fichas(p["nome"])) >= max(1, min(len(fx), 2))):
                casou = p
                break
        if casou:
            motivos["casa com POI já vinculado"] += 1
            continue
        motivos["CANDIDATO a POI busca web"] += 1
        candidatos.append({"busca": bid, "ligacao": lig, "endereco": end_l, "consulta": consulta,
                           "motor": motor, "x": x, "pois": pois.get(str(lig), [])})

por_tipo = collections.Counter(str(c["x"].get("tipo_da_prova") or "?") for c in candidatos)
rnd = random.Random(20260912)
amostra = rnd.sample(candidatos, min(QUANTOS, len(candidatos)))
ids = sorted({c["busca"] for c in amostra})
cur.execute("select id, dados from radar_comercial.busca_web where id = any(%s)", (ids,))
prints = {}
for bid, d in cur.fetchall():
    if d:
        im = Image.open(io.BytesIO(bytes(d))).convert("RGB")
        if im.width > 900:
            im = im.resize((900, int(im.height * 900 / im.width)), Image.LANCZOS)
        im = im.crop((0, 0, im.width, min(im.height, 1600)))
        b = io.BytesIO()
        im.save(b, "WEBP", quality=60)
        prints[bid] = "data:image/webp;base64," + base64.b64encode(b.getvalue()).decode()
con.close()

cartoes = []
for c in amostra:
    x = c["x"]
    campos = [("categoria", x.get("categoria")), ("telefone", x.get("telefone")), ("site", x.get("site")),
              ("Instagram", x.get("instagram")), ("horário", x.get("horario")), ("nota", x.get("nota")),
              ("avaliações", x.get("avaliacoes")), ("CNPJ", x.get("cnpj")), ("situação", x.get("status")),
              ("de onde vem", x.get("tipo_da_prova")), ("domínio", x.get("dominio")),
              ("o que a página diz", x.get("descricao"))]
    dl = "".join("<dt>%s</dt><dd>%s</dd>" % (e(k), e(str(v))) for k, v in campos if v not in (None, "", [], {}))
    ja = "".join("<li><span class='mono'>#%s</span> [%s] %s</li>" % (p["id"], e(p["fonte"]), e(p["nome"][:60]))
                 for p in c["pois"][:8]) or "<li class='cinza'>nenhum</li>"
    img = ("<img src='%s' alt='print da busca' loading='lazy'>" % prints[c["busca"]]) if c["busca"] in prints else ""
    cartoes.append("""<article class="card"><header><div><span class="rotulo">ligação %s · %s</span>
      <h2>%s</h2></div><span class="pill">%s</span></header>
      <div class="grade"><div><dl>%s</dl><h3>POIs já vinculados à instalação</h3><ul>%s</ul>
      <p class="cinza">busca: “%s” (%s)</p></div><figure>%s</figure></div></article>"""
                   % (e(c["ligacao"]), e(c["endereco"] or ""), e(str(x.get("nome"))),
                      e(str(x.get("tipo_da_prova") or "?")), dl, ja, e(c["consulta"]), e(c["motor"]), img))

tot = sum(motivos.values())
linhas_m = "".join("<tr><td>%s</td><td class='n'>%d</td></tr>" % (e(k), v) for k, v in motivos.most_common())
linhas_t = "".join("<tr><td>%s</td><td class='n'>%d</td></tr>" % (e(k), v) for k, v in por_tipo.most_common())
pagina = """<title>POIs da Busca Web</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{--chao:#f1f4f3;--papel:#fff;--tinta:#13262b;--tinta-2:#556a6f;--regua:#d3dedd;--acento:#2f5d8a;--acento-f:#e4edf6;
--display:"Barlow Semi Condensed","Arial Narrow",sans-serif;--texto:"IBM Plex Sans","Segoe UI",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--chao:#0e181b;--papel:#152327;--tinta:#e3eeef;--tinta-2:#9bb0b4;--regua:#29403f;--acento:#7fb0de;--acento-f:#1a3044}}
:root[data-theme="dark"]{--chao:#0e181b;--papel:#152327;--tinta:#e3eeef;--tinta-2:#9bb0b4;--regua:#29403f;--acento:#7fb0de;--acento-f:#1a3044}
*{box-sizing:border-box}body{margin:0;background:var(--chao);color:var(--tinta);font:400 14.5px/1.5 var(--texto)}
main{max-width:1180px;margin:0 auto;padding:36px 20px 60px;display:grid;gap:24px}
h1,h2,h3{font-family:var(--display);margin:0;text-wrap:balance}h1{font-size:42px;line-height:1}h2{font-size:21px}h3{font-size:16px;margin-top:10px}
p{margin:0;max-width:72ch}.rotulo{font:500 11px/1.2 var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--tinta-2)}
.cinza{color:var(--tinta-2)}.mono{font-family:var(--mono);font-size:12.5px}
.tabelas{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
table{border-collapse:collapse;width:100%%;background:var(--papel)}td,th{padding:7px 10px;border-bottom:1px solid var(--regua);text-align:left}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.card{background:var(--papel);border:1px solid var(--regua);padding:16px;display:grid;gap:10px}
.card header{display:flex;justify-content:space-between;gap:12px;align-items:start}
.pill{font:500 12px/1 var(--mono);padding:6px 9px;color:var(--acento);background:var(--acento-f);white-space:nowrap}
.grade{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:16px}
@media (max-width:820px){.grade{grid-template-columns:1fr}}
dl{display:grid;grid-template-columns:auto 1fr;gap:3px 12px;margin:0;font-size:13.5px}dt{color:var(--tinta-2)}dd{margin:0;overflow-wrap:anywhere}
ul{margin:4px 0 8px;padding-left:18px;font-size:13.5px}
figure{margin:0;max-height:520px;overflow:auto;border:1px solid var(--regua)}figure img{display:block;width:100%%}
</style>
<main>
<header style="display:grid;gap:10px"><span class="rotulo">Radar Comercial · Canoas · amostra, nada foi criado</span>
<h1>POIs da Busca Web</h1>
<p>Estabelecimentos que a busca web mostrou no endereço da instalação e que nenhum POI vinculado a ela já tem — nem pelo nome, nem pelo telefone, nem pelo CNPJ. São os que viriam a ser POI com fonte "busca web". Esta página é para você decidir se a regra de corte está boa antes de criar qualquer um. %d buscas lidas até agora.</p></header>
<div class="tabelas"><table><thead><tr><th>O que aconteceu com cada estabelecimento lido</th><th>quantos</th></tr></thead><tbody>%s</tbody></table>
<table><thead><tr><th>Candidatos por origem</th><th>quantos</th></tr></thead><tbody>%s</tbody></table></div>
<h2>%d candidatos sorteados</h2>
%s
</main>""" % (len(buscas), linhas_m, linhas_t, len(amostra), "".join(cartoes))
open(SAIDA, "w", encoding="utf-8").write(pagina)
print("buscas %d · estabelecimentos %d · candidatos %d · amostra %d" % (len(buscas), tot, len(candidatos), len(amostra)))
print(dict(motivos.most_common()))
