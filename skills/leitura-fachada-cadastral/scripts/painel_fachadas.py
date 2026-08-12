#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel A2L de conferência humana das anotações de fachada.

Gera um HTML autocontido (CSS inline, imagens embutidas em base64) com a fila de revisão já
priorizada. A ordem não é alfabética nem por confiança pura: é por **risco de decisão errada** —
alerta crítico primeiro, depois divergência de economias, depois confiança baixa. Revisor humano é
o recurso escasso da operação; o painel existe para gastá-lo onde muda decisão.

Uso:
    python3 painel_fachadas.py <fachadas.csv> [--imagens fotos/] [--out saida/painel_fachadas.html]
       [--limite 300] [--conf-min 0.5]
"""

import argparse
import base64
import csv
import html
import mimetypes
import os
import sys

NAVY, GOLD, DARK, DARK2 = "#0B2E59", "#F28C28", "#12161C", "#2A2E35"
CINZA, CLARO, VERM, VERDE, AZUL = "#6B7684", "#F2F4F7", "#D64545", "#1F9D62", "#006DFF"

CRITICOS = ["alerta_imagem_inapta", "alerta_imagem_fora_de_escopo",
            "alerta_suspeita_imovel_inexistente",
            "oportunidade_divergencia_uc_economias", "oportunidade_divergencia_numero"]
ATENCAO = ["alerta_divergencia_economias", "alerta_suspeita_irregularidade",
           "alerta_impedimento_leitura", "alerta_medicao_nao_localizada",
           "alerta_numero_diverge_cadastro", "alerta_uso_diverge_cadastro",
           "alerta_suspeita_vacancia", "alerta_revisao_humana",
           "oportunidade_multiplas_ucs", "oportunidade_uso_comercial", "oportunidade_uso_misto",
           "oportunidade_economias_ocultas", "oportunidade_esgoto_sem_cobranca",
           "oportunidade_area_divergente", "alerta_atividade_domiciliar_aparente",
           "alerta_atividade_de_vizinho"]

DESTAQUE = [
    ("triagem_conteudo_imagem", "Conteúdo"),
    ("triagem_atividade_economica_aparente", "Atividade econ."),
    ("triagem_atividade_no_alvo", "Atividade de quem"),
    ("triagem_formalidade_aparente", "Formalidade"),
    ("enderecamento_numero_fachada", "Nº fachada"),
    ("enderecamento_numero_endereco_consolidado", "Nº consolidado"),
    ("enderecamento_numero_caixa_correio", "Nº caixa correio"),
    ("enderecamento_complementos_unidades_visiveis", "Complementos/unidades"),
    ("edificacao_tipo_edificacao", "Tipo"),
    ("edificacao_pavimentos_visiveis", "Pav."),
    ("edificacao_padrao_construtivo", "Padrão"),
    ("edificacao_estado_conservacao", "Conservação"),
    ("uso_uso_predominante", "Uso"),
    ("uso_nome_estabelecimento_visivel", "Estabelecimento"),
    ("uso_descricao_atividade_funcional", "O que funciona"),
    ("uso_situacao_estabelecimento_na_data_imagem", "Situação comércio"),
    ("ocupacao_situacao_ocupacao", "Ocupação"),
    ("agua_hidrometro_presente", "Hidrômetro"),
    ("agua_acessibilidade_medicao", "Acesso medição"),
    ("energia_medidores_energia_qtd", "Medidores energia"),
    ("esgoto_caixa_inspecao_aparente", "Caixa inspeção"),
    ("esgoto_lancamento_sarjeta_aparente", "Lançam. sarjeta"),
]


def _num(v, default=0.0):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return default


def prioridade(linha):
    """Menor = revisar antes. Risco de decisão errada, não confiança pura."""
    p = 3
    if any(_num(linha.get(c)) >= 1 for c in CRITICOS):
        p = 0
    elif any(_num(linha.get(c)) >= 1 for c in ATENCAO):
        p = 1
    elif _num(linha.get("confianca_media"), 1.0) < 0.5:
        p = 2
    return (p, _num(linha.get("confianca_media"), 1.0), linha.get("fonte_arquivo") or "")


def embutir(caminho):
    try:
        tipo = mimetypes.guess_type(caminho)[0] or "image/jpeg"
        with open(caminho, "rb") as fh:
            return "data:%s;base64,%s" % (tipo, base64.b64encode(fh.read()).decode("ascii"))
    except Exception:
        return None


def achar_imagem(linha, dir_imagens):
    if not dir_imagens:
        return None
    nome = (linha.get("arquivo") or "").strip()
    for cand in ([os.path.join(dir_imagens, os.path.basename(nome))] if nome else []):
        if os.path.isfile(cand):
            return cand
    alvo = os.path.splitext(os.path.basename(nome or linha.get("fonte_arquivo") or ""))[0].lower()
    if not alvo:
        return None
    for raiz, _, nomes in os.walk(dir_imagens):
        for n in sorted(nomes):
            if os.path.splitext(n)[0].lower() == alvo:
                return os.path.join(raiz, n)
    return None


def chip(rotulo, valor, conf, juizo):
    c = _num(conf, 1.0)
    cor = VERDE if c >= 0.75 else (GOLD if c >= 0.5 else VERM)
    if juizo == "nao_observavel" or valor in (None, ""):
        cor, valor = CINZA, "—"
    marca = "≈" if juizo == "inferido" else ""
    return ("<div class='chip'><span class='rot'>%s</span>"
            "<span class='val' style='color:%s'>%s%s</span>"
            "<span class='cf'>%s</span></div>"
            % (html.escape(rotulo), cor, marca, html.escape(str(valor)),
               ("%.2f" % c) if valor != "—" else ""))


def card(linha, dir_imagens):
    p = prioridade(linha)[0]
    faixa = {0: VERM, 1: GOLD, 2: AZUL, 3: DARK2}[p]
    rotulo = {0: "CRÍTICO", 1: "ATENÇÃO", 2: "BAIXA CONFIANÇA", 3: "OK"}[p]

    img = achar_imagem(linha, dir_imagens)
    src = embutir(img) if img else None
    fig = ("<img src='%s' alt='fachada'>" % src) if src else "<div class='semimg'>sem imagem</div>"

    # colunas de triagem não têm _conf/_juizo próprios: usam a confiança do bloco
    chips = "".join(
        chip(rot, linha.get(col),
             linha.get("triagem_confianca") if col.startswith("triagem_") else linha.get(col + "_conf"),
             linha.get(col + "_juizo"))
        for col, rot in DESTAQUE)

    eco = linha.get("economias_estimativa") or "—"
    if linha.get("economias_divergencia") in ("True", "true", "1"):
        eco = "%s  [%s–%s] divergente" % (eco, linha.get("economias_faixa_min") or "?",
                                          linha.get("economias_faixa_max") or "?")
    alertas = (linha.get("alertas_lista") or "").replace(";", " · ") or "—"

    return """
    <article class='card' data-p='%d'>
      <div class='faixa' style='background:%s'></div>
      <div class='corpo'>
        <div class='fig'>%s</div>
        <div class='dados'>
          <header>
            <span class='tag' style='background:%s'>%s</span>
            <h3>%s</h3>
            <span class='meta'>%s · %s · %s · conf. média %s</span>
          </header>
          <div class='chips'>%s</div>
          <div class='rodape'>
            <span><b>Economias:</b> %s (%s)</span>
            <span><b>Oportunidades:</b> %s</span>
            <span><b>Alertas:</b> %s</span>
          </div>
        </div>
      </div>
    </article>""" % (
        p, faixa, fig, faixa, rotulo,
        html.escape(str(linha.get("matricula") or linha.get("arquivo") or linha.get("fonte_arquivo") or "—")),
        html.escape(str(linha.get("fonte") or "?")),
        html.escape(str(linha.get("data_captura") or linha.get("ano_captura") or "sem data")),
        html.escape(str(linha.get("enquadramento") or "?")),
        html.escape(str(linha.get("confianca_media") or "—")),
        chips, html.escape(str(eco)),
        html.escape(str(linha.get("economias_metodo") or "—")),
        html.escape((linha.get("oportunidades_lista") or "—").replace(";", " · ")),
        html.escape(alertas))


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:%(dark)s;color:%(claro)s;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:28px}
h1{font-size:20px;letter-spacing:.14em;text-transform:uppercase;font-weight:600}
h1 b{color:%(gold)s}
.sub{color:%(cinza)s;font-size:12px;margin-top:4px}
.barra{display:flex;gap:10px;margin:20px 0;flex-wrap:wrap;align-items:center}
.kpi{background:%(dark2)s;border-left:3px solid %(gold)s;padding:10px 14px;border-radius:3px}
.kpi span{display:block;color:%(cinza)s;font-size:10px;letter-spacing:.1em;text-transform:uppercase}
.kpi b{font-size:20px}
button{background:%(dark2)s;color:%(claro)s;border:1px solid #3a4048;padding:8px 14px;border-radius:3px;cursor:pointer;font-size:12px}
button.on{background:%(gold)s;color:#12161C;border-color:%(gold)s;font-weight:600}
.card{background:%(dark2)s;border-radius:4px;overflow:hidden;margin-bottom:12px;display:flex}
.faixa{width:4px;flex:0 0 4px}
.corpo{display:flex;gap:16px;padding:14px;flex:1;min-width:0}
.fig{flex:0 0 210px}
.fig img{width:210px;height:150px;object-fit:cover;border-radius:3px;display:block}
.semimg{width:210px;height:150px;display:grid;place-items:center;background:#1b2029;color:%(cinza)s;font-size:11px;border-radius:3px}
.dados{flex:1;min-width:0}
header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.tag{font-size:9px;letter-spacing:.12em;padding:3px 7px;border-radius:2px;color:#12161C;font-weight:700}
h3{font-size:15px}
.meta{color:%(cinza)s;font-size:11px}
.chips{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px}
.chip{background:#1b2029;border-radius:3px;padding:6px 8px;display:flex;flex-direction:column;min-width:0}
.rot{color:%(cinza)s;font-size:9px;letter-spacing:.08em;text-transform:uppercase}
.val{font-size:12px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cf{color:%(cinza)s;font-size:9px}
.rodape{display:flex;gap:20px;margin-top:10px;padding-top:9px;border-top:1px solid #343a44;font-size:11px;color:#c3cad3;flex-wrap:wrap}
footer{color:%(cinza)s;font-size:11px;margin-top:22px;border-top:1px solid %(dark2)s;padding-top:12px}
""" % {"dark": DARK, "dark2": DARK2, "claro": CLARO, "gold": GOLD, "cinza": CINZA}

JS = """
document.querySelectorAll('button[data-f]').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('button[data-f]').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');
  const f=b.dataset.f;
  document.querySelectorAll('.card').forEach(c=>{
    c.style.display=(f==='all'||c.dataset.p===f)?'flex':'none';});
});
"""


def main():
    ap = argparse.ArgumentParser(description="Painel A2L de conferência de anotações de fachada.")
    ap.add_argument("csv_consolidado")
    ap.add_argument("--imagens", default=None)
    ap.add_argument("--out", default="painel_fachadas.html")
    ap.add_argument("--limite", type=int, default=300,
                    help="máx. de cards no HTML (base64 pesa); o corte é reportado, nunca silencioso")
    args = ap.parse_args()

    try:
        with open(args.csv_consolidado, "r", encoding="utf-8-sig") as fh:
            linhas = list(csv.DictReader(fh, delimiter=";"))
    except Exception as exc:
        print("ERRO ao ler %s: %s" % (args.csv_consolidado, exc), file=sys.stderr)
        return 2
    if not linhas:
        print("CSV sem linhas.", file=sys.stderr)
        return 2

    linhas.sort(key=prioridade)
    total = len(linhas)
    exibidas = linhas[:args.limite]
    cortadas = total - len(exibidas)

    n_crit = sum(1 for l in linhas if prioridade(l)[0] == 0)
    n_at = sum(1 for l in linhas if prioridade(l)[0] == 1)
    n_baixa = sum(1 for l in linhas if prioridade(l)[0] == 2)
    conf = [_num(l.get("confianca_media"), 0.0) for l in linhas if l.get("confianca_media")]
    conf_med = sum(conf) / len(conf) if conf else 0.0

    kpis = [("Imagens", total), ("Críticos", n_crit), ("Atenção", n_at),
            ("Baixa confiança", n_baixa), ("Confiança média", "%.2f" % conf_med)]
    kpi_html = "".join("<div class='kpi'><span>%s</span><b>%s</b></div>" % k for k in kpis)
    botoes = "".join("<button data-f='%s'%s>%s</button>" % (f, " class='on'" if f == "all" else "", r)
                     for f, r in [("all", "Todos"), ("0", "Críticos"), ("1", "Atenção"),
                                  ("2", "Baixa confiança"), ("3", "OK")])
    cards = "".join(card(l, args.imagens) for l in exibidas)
    nota_corte = ("<footer>Exibidos %d de %d registros (limite --limite=%d). "
                  "Os %d restantes estão no CSV consolidado.</footer>"
                  % (len(exibidas), total, args.limite, cortadas)) if cortadas else ""

    doc = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A2L · Conferência de fachadas</title><style>%s</style></head><body>
<h1>A<b>2</b>L · Conferência de fachadas</h1>
<div class="sub">Fila ordenada por risco de decisão errada: crítico → atenção → baixa confiança.
&nbsp;·&nbsp; <b>≈</b> antes do valor indica campo inferido, não observado. &nbsp;·&nbsp; O número ao lado é a confiança do campo.</div>
<div class="barra">%s</div><div class="barra">%s</div>
%s
%s
<footer>Anotações produzidas pela skill <b>leitura-fachada-cadastral</b>. Campos inferidos citam a regra de derivação;
suspeitas são hipóteses de vistoria, não conclusões.</footer>
<script>%s</script></body></html>""" % (CSS, kpi_html, botoes, cards, nota_corte, JS)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(doc)

    print("Painel: %s | registros: %d (exibidos %d) | críticos: %d | atenção: %d | baixa conf.: %d"
          % (args.out, total, len(exibidas), n_crit, n_at, n_baixa))
    return 0


if __name__ == "__main__":
    sys.exit(main())
