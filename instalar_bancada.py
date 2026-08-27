# -*- coding: utf-8 -*-
"""Instala a bancada de validação a partir do modelo, apontada para a nossa API.

POR QUE UM INSTALADOR, E NÃO COPIAR O ARQUIVO NO REPOSITÓRIO

O HTML do modelo tem 480 KB. Versioná-lo faria cada ajuste virar um diff
ilegível de meio megabyte, e a origem — `assets/modelo_frontend/` — deixaria de
ser a fonte da verdade: alguém editaria a cópia e as duas divergiriam em
silêncio.

Aqui ele é extraído do zip e recebe UM enxerto: o carregamento automático do
nosso `/api/bancada/dataset`. O resto fica exatamente como veio.

O QUE O ENXERTO FAZ

O modelo já tem o botão "Carregar dataset.json" e a função que consome o
payload. O enxerto só chama essa função sozinho, com os nossos dados, ao abrir
a página — em vez de exigir que alguém escolha um arquivo toda vez.

Rodar de novo é seguro: refaz do zip, então o arquivo instalado nunca acumula
enxertos.

Uso:
    python instalar_bancada.py
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import sys
import zipfile

RAIZ = pathlib.Path(__file__).parent
# DOIS lugares, e o segundo não é redundância: em 24/08/2026 os assets foram
# reorganizados e o zip passou de `modelo_frontend/` para `skills_locais/`. O
# `instalar_bancada.py` ficou apontando para o caminho antigo e só falhou na
# primeira publicação no i9 — aqui no notebook o `frontend/bancada.html` já
# existia de antes, então nada acusou. Arquivo movido não quebra quem já tem o
# resultado; quebra quem for gerar de novo, meses depois.
_CANDIDATOS = (
    RAIZ / "assets" / "skills_locais" / "tela_radar_comercial.zip",
    RAIZ / "assets" / "modelo_frontend" / "tela_radar_comercial.zip",
)
ZIP = next((p for p in _CANDIDATOS if p.exists()), _CANDIDATOS[0])
ALVO = RAIZ / "frontend" / "bancada.html"

# O enxerto. Fica no fim do `body`, depois de todo o JavaScript do modelo, para
# que as funções dele já existam quando isto rodar.
ENXERTO = """
<!-- cr-enxerto-v1 -->
<script src="/static/sessao.js"></script>
<script>
/* ── ComercialRadar: a bancada com os NOSSOS dados ─────────────────────────
 *
 * COMO O MODELO FUNCIONA, conferido no proprio arquivo:
 *
 *     let D = JSON.parse(document.getElementById('dataset').textContent);
 *     ...  D = novo; boot();
 *
 * O dataset vem embutido num `<script id="dataset">` e trocar de dados e
 * atribuir a `D` e chamar `boot()`.
 *
 * DUAS COISAS QUE ESTA VERSAO CORRIGE
 *
 * 1. O TOKEN vem do `sessao.js`, o mesmo do resto do sistema — com renovacao e
 *    redirecionamento para o login. A versao anterior lia `sessionStorage` a
 *    mao: funcionava enquanto o token estivesse vivo e dava "sessao expirada"
 *    no primeiro vencimento, sem tentar renovar.
 *
 * 2. FALHA NAO MOSTRA DADO DE DEMONSTRACAO. Quando a busca falhava, `D`
 *    continuava sendo o payload de exemplo do modelo, e a tela exibia
 *    "CARLA A. FIGUEIREDO NUNES" e "40 ligacoes" como se fossem do usuario.
 *    Dado de exemplo se passando por dado real e pior que tela vazia: quem
 *    olha decide sobre um ponto que nao existe.
 */
(function () {
  const aviso = (t, erro) => {
    let d = document.getElementById("cr-aviso");
    if (!d) {
      d = document.createElement("div");
      d.id = "cr-aviso";
      d.style.cssText = "position:fixed;left:50%;top:14px;transform:translateX(-50%)"
        + ";color:#fff;padding:10px 20px;border-radius:8px;font:14px system-ui"
        + ";z-index:99999;box-shadow:0 12px 36px rgba(11,46,89,.16)"
        + ";max-width:70vw;text-align:center;font-weight:600";
      document.body.appendChild(d);
    }
    /* #B42318 nao era cor deste sistema: veio de um exemplo. O critico do
       painel e #D64545. */
    d.style.background = erro ? "#D64545" : "#0B2E59";
    d.textContent = t;
    return d;
  };

  /* A TELA FICA VAZIA em vez de mostrar o exemplo do modelo. */
  function limpar(motivo) {
    try {
      const vazio = { meta: { produto: "ComercialRadar" },
                      vocabulario: (window.__CR_VOCAB || {}),
                      fontes: [], ligacoes: [] };
      new Function("d", "D = d; boot();")(vazio);
    } catch (e) { /* se nem limpar der, o aviso vermelho ja explica */ }
    aviso(motivo, true);
  }

  async function carregar() {
    aviso("carregando…");
    const alvo = new URLSearchParams(location.search).get("poi");
    const rota = "/api/bancada/dataset" + (alvo ? "?poi=" + encodeURIComponent(alvo) : "");
    try {
      /* `window.fetch` foi trocado pelo `sessao.js`: ele poe o cabecalho,
         renova o token vencido e manda para o login quando nao ha sessao. */
      const r = await fetch(rota);
      if (r.status === 401) { limpar("sessao expirada — entre pelo mapa e volte"); return; }
      if (!r.ok) { limpar("nao consegui carregar (HTTP " + r.status + ")"); return; }
      const novo = await r.json();
      if (!novo.ligacoes || !novo.fontes) { limpar("payload sem ligacoes/fontes"); return; }
      if (!novo.ligacoes.length) {
        limpar(alvo ? "ponto " + alvo + " nao esta na sua base"
                    : "nenhum ponto na sua fila");
        return;
      }
      window.__CR_DATASET = novo;
      new Function("d", "D = d; boot();")(novo);
      document.getElementById("cr-aviso")?.remove();
    } catch (e) {
      limpar("falha ao carregar: " + e.message);
      console.error(e);
    }
  }

  /* O CAMINHO DE VOLTA. Sem ele quem clica num ponto entra e fica preso: no
     modelo aquela tela era a aplicacao inteira; aqui e uma etapa. */
  function botaoVoltar() {
    const b = document.createElement("button");
    b.textContent = "← mapa";
    b.title = "Voltar ao mapa (Esc)";
    b.style.cssText = "position:fixed;left:14px;bottom:14px;z-index:99998"
      + ";background:#0B2E59;color:#fff;border:0;border-radius:8px"
      + ";padding:9px 15px;font:600 13px system-ui;cursor:pointer"
      + ";box-shadow:0 4px 14px rgba(11,46,89,.16)";
    b.onclick = () => { location.href = "/"; };
    document.body.appendChild(b);
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      /* Esc cede a vez: a tela do modelo fecha popover e assistente com ele, e
         roubar a tecla faria o operador sair ao tentar fechar uma caixinha. */
      if (document.querySelector("[aria-expanded='true'], dialog[open]")) return;
      location.href = "/";
    });
  }

  /* ── O `x` DA ABA: desfazer uma fusao errada ─────────────────────────────
   *
   * A fusao e feita por maquina com evidencia incompleta e VAI errar. Medido no
   * RS em 25/08/2026: a IA julgou 400 fusoes suspeitas sorteadas e 66,2% delas
   * uniram estabelecimentos DISTINTOS. Este e o caminho de volta.
   *
   * POR QUE ISTO E ENXERTO E NAO EDICAO DO HTML
   *
   * `frontend/bancada.html` e GERADO deste script a partir do zip do modelo, e
   * esta no .gitignore. Editar o HTML direto funciona ate alguem rodar
   * `instalar_bancada.py` — e ai a mudanca some sem deixar rastro. O enxerto e
   * versionado e sobrevive a regeneracao.
   *
   * O modelo desenha as abas em `pintaAbas()`, com template literal. Em vez de
   * reescrever a funcao dele — que muda a cada versao do zip —, embrulhamos:
   * ele desenha, nos acrescentamos. Se o modelo mudar o HTML da aba, o pior que
   * acontece e o `x` nao aparecer; nada quebra.
   */
  function selo(v) {
    const e = document.createElement("span");
    e.className = "cr-conf";
    e.textContent = v.confianca;
    e.title = "Confianca " + v.confianca + "/10 (" + v.confianca_origem + ")"
            + (v.motivo ? " — " + v.motivo : "");
    e.style.cssText = "font-size:11px;margin-left:5px;padding:1px 5px;border-radius:4px"
      + ";background:#EEF2F7;color:#5A6B7C;font-weight:700;font-variant-numeric:tabular-nums";
    return e;
  }

  function botaoX(fonte, v) {
    const x = document.createElement("span");
    x.className = "cr-desv";
    x.textContent = "\u00d7";
    x.setAttribute("role", "button");
    x.tabIndex = 0;
    x.title = "Desvincular esta fonte deste ponto";
    /* Discreto ate o ponteiro chegar: e um botao que desfaz juncao, e destaque
       permanente convida ao clique distraido. */
    x.style.cssText = "margin-left:6px;padding:0 4px;border-radius:4px;color:#9AA7B4"
      + ";font-size:12px;line-height:1;opacity:.4;cursor:pointer";
    x.onmouseenter = () => { x.style.opacity = "1"; x.style.background = "#FDE7E9";
                             x.style.color = "#B3261E"; };
    x.onmouseleave = () => { x.style.opacity = ".4"; x.style.background = "";
                             x.style.color = "#9AA7B4"; };
    const agir = (ev) => { ev.stopPropagation(); ev.preventDefault();
                           desvincular(fonte, v); };
    x.onclick = agir;
    x.onkeydown = (ev) => { if (ev.key === "Enter" || ev.key === " ") agir(ev); };
    return x;
  }

  async function desvincular(fonte, v) {
    /* `atual` e `let` no topo do script do modelo. Isso o poe no escopo
       lexical global — visivel daqui — mas NAO em `window`, e `window.atual`
       dava undefined em silencio. */
    const a = (typeof atual !== "undefined") ? atual : null;
    const poi = (a && a.num_ligacao) || null;
    if (!poi) return;
    /* A confirmacao diz as DUAS consequencias, porque as duas surpreendem quem
       nao leu a documentacao: o registro vira um ponto NOVO (nada e apagado), e
       esse ponto passa NA HORA pelo Maps e pelo Street View proprios — o que
       leva dezenas de segundos. Avisar depois seria deixar o operador achando
       que travou. */
    /* SEM UMA BARRA SEQUER nesta string, e isso e cicatriz.
       O texto e escrito dentro de um heredoc no `instalar_bancada.py`, e a
       primeira versao usava aspas escapadas: o heredoc comeu o escape, o
       JS gerado ficou com `"" + fonte + ""` e a BANCADA INTEIRA parou de
       carregar por erro de sintaxe. Template literal com quebras de linha
       reais nao tem esse problema.

       E o texto diz as DUAS consequencias, porque as duas surpreendem quem
       nao leu a documentacao: o registro vira um ponto NOVO (nada e
       apagado), e esse ponto passa NA HORA pelo Maps e pelo Street View. */
    if (!confirm(
`Desvincular a fonte "${fonte}" deste ponto?

• O registro dela vira um PONTO NOVO, separado. Nada e apagado.
• O ponto novo passa agora pelo Google Maps e pelo Street View,
  o que leva alguns segundos.

A evidencia ja capturada continua neste ponto.`)) return;

    try {
      const r = await fetch("/api/poi/" + encodeURIComponent(poi) + "/desvincular", {
        method: "POST",
        /* SEM cabecalho de autorizacao aqui: o `sessao.js` ja trocou o
           `window.fetch` e poe o token em toda requisicao, com renovacao. Por
           um de proposito seria ter duas verdades sobre a sessao. */
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({fonte: fonte, id_fonte: v.id_fonte}),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
      const cap = d.captura || {};
      const fez = [];
      if (cap.maps && !cap.maps.erro) fez.push("painel do Maps");
      if (cap.fachada && !cap.fachada.erro) fez.push("fachada");
      /* Montado por lista e unido com `String.fromCharCode(10)`.
         O `ENXERTO` e uma string comum do Python — nao raw — entao um "
"
         escrito aqui vira QUEBRA DE LINHA REAL no JS gerado, e a string
         literal fica aberta. Foi assim que a bancada quebrou duas vezes. */
      const linhas = ["Separado. Novo ponto #" + d.poi_novo + "."];
      linhas.push(fez.length
        ? "Capturado: " + fez.join(" e ") + "."
        : "A captura nao completou; e refeita no proximo enriquecimento.");
      if (d.virou_ancora) linhas.push("Este ponto foi reancorado na fonte que ficou.");
      alert(linhas.join(String.fromCharCode(10)));
      location.reload();
    } catch (e) {
      alert("Nao consegui desvincular: " + e.message);
    }
  }

  function enfeitarAbas() {
    const a = (typeof atual !== "undefined") ? atual : null;
    const f = (a && a.fontes) || {};
    document.querySelectorAll("#abas .aba[data-a]").forEach((b) => {
      if (b.querySelector(".cr-conf, .cr-desv")) return;   // ja enfeitada
      const v = (f[b.dataset.a] || {}).vinculo;
      if (!v) return;                                       // aba de sintese
      b.appendChild(selo(v));
      /* So oferece o `x` quando o SERVIDOR disse que pode: a ultima fonte nao
         pode sair, e recusar depois do clique e pior que nao oferecer. */
      if (v.pode_desvincular) b.appendChild(botaoX(b.dataset.a, v));
    });
  }

  function embrulharAbas() {
    if (typeof window.pintaAbas !== "function") return;
    const original = window.pintaAbas;
    window.pintaAbas = function () {
      const r = original.apply(this, arguments);
      try { enfeitarAbas(); } catch (e) { console.warn("cr: abas", e); }
      return r;
    };
    enfeitarAbas();
  }

  function comecar() { carregar(); botaoVoltar(); setTimeout(embrulharAbas, 0); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", comecar);
  } else { comecar(); }
})();
</script>
"""


# Trocas no HTML do modelo. Cada uma tem um defeito medido atrás, e todas
# precisam viver AQUI: `frontend/bancada.html` é gerado e está no `.gitignore`,
# então conserto feito lá some na próxima instalação.
_TROCAS = [
    # ── `lon` NUNCA EXISTIU no nosso payload ────────────────────────────────
    # O modelo foi escrito contra o exemplo dele, que usava `lon`. Em todo o
    # projeto o campo é `lng` — `pois.maps_lng`, `lng_origem`, `ANCORA_CAMPOS`.
    # A tela quebrava com "Cannot read properties of undefined (reading
    # 'toFixed')" e o operador via o aviso vermelho POR CIMA do dataset de
    # exemplo, que continuava desenhado.
    ("  if (a.lat == null) {",
     "  if (a.lat == null || a.lng == null) {"),
    ("const lat = a.lat.toFixed(6), lon = a.lon.toFixed(6), head = azimuteDaLigacao();",
     "const lat = a.lat.toFixed(6), lon = a.lng.toFixed(6), head = azimuteDaLigacao();"),
    ("${esc(a.lat?.toFixed(6))}, ${esc(a.lon?.toFixed(6))} · ${esc(a.nv_geo || '')} ·",
     "${esc(a.lat?.toFixed(6))}, ${esc(a.lng?.toFixed(6))} · ${esc(a.nv_geo || '')} ·"),
    ("const lat = c.lat ?? a.lat, lon = c.lon ?? a.lon, hd = c.heading ?? 0;",
     "const lat = c.lat ?? a.lat, lon = c.lng ?? a.lng, hd = c.heading ?? 0;"),
    ("const p = proj(a.lat, a.lon, MAPA.z);",
     "const p = proj(a.lat, a.lng, MAPA.z);"),
    ("const pts = [{id: 'ancora', rot: 'Cadastro', lat: atual.ancora.lat, lon: atual.ancora.lon}];",
     "const pts = [{id: 'ancora', rot: 'Cadastro', lat: atual.ancora.lat, lon: atual.ancora.lng}];"),
    ("if (c && c.lat != null && c.lon != null) pts.push({id: f.id, rot: f.rotulo, lat: c.lat, lon: c.lon});",
     "if (c && c.lat != null && c.lng != null) pts.push({id: f.id, rot: f.rotulo, lat: c.lat, lon: c.lng});"),
    ("return [`${a.lat?.toFixed(6)}, ${a.lon?.toFixed(6)}, nível ${rotOS(a.nv_geo || '—')}`",
     "return [`${a.lat?.toFixed(6)}, ${a.lng?.toFixed(6)}, nível ${rotOS(a.nv_geo || '—')}`"),
    # ── FILA VAZIA é estado normal ──────────────────────────────────────────
    # `boot()` abria a primeira ligação sem checar se existe alguma. Com as 40
    # do exemplo embutidas isso nunca falhava; tirado o exemplo, a PRIMEIRA
    # carga passou a estourar aqui, antes de a busca sequer responder.
    ("  abrir(D.ligacoes[0].num_ligacao);",
     "  if (D.ligacoes && D.ligacoes.length) abrir(D.ligacoes[0].num_ligacao);"),
]


def _esvaziar_dataset(html: str) -> tuple:
    """Troca o lote de DEMONSTRAÇÃO por um esqueleto da mesma forma.

    O modelo faz `D = JSON.parse(<script id=dataset>)` ao carregar e SÓ DEPOIS
    busca o real. Enquanto a busca não volta — e sempre que ela falha — o que
    fica na tela são as 40 ligações de exemplo: nomes, faturas e comentários de
    gente que não existe, com a marca do cliente em volta.

    Em 27/08/2026 o operador viu o aviso vermelho de falha E a tela cheia de
    dado plausível. O aviso ele leu; a tela ele acreditou.

    > Dado de exemplo se passando por dado real é pior que tela vazia. A tela
    > vazia faz perguntar; o exemplo faz decidir errado.

    O `vocabulario` FICA: são os rótulos da própria bancada (status, tier,
    motivos de reprova), não dado de ninguém, e sem ele a página não monta nem
    o cabeçalho. A API manda o dela por cima.
    """
    abre = '<script id="dataset" type="application/json">'
    if abre not in html:
        return html, 0
    i = html.index(abre)
    j = html.index("</script>", i)
    antigo = json.loads(html[i + len(abre):j])
    quantas = len(antigo.get("ligacoes") or [])

    meta = antigo.get("meta", {})
    vazio = {
        "meta": {"produto": meta.get("produto"),
                 "versao_payload": meta.get("versao_payload"),
                 "gerado_em": None, "base": "", "tenant": "",
                 "fonte_ancora": "POI",
                 "srid_armazenamento": meta.get("srid_armazenamento"),
                 "srid_analise": meta.get("srid_analise"),
                 "assistente": {"nome": "", "endpoint": None},
                 "nota_probabilidade": ""},
        "vocabulario": antigo.get("vocabulario", {}),
        "ancora_campos": [], "imagem_campos": [],
        "servico_campos": [], "fatura_campos": [],
        "fontes": [], "ligacoes": [],
    }
    novo = abre + "\n" + json.dumps(vazio, ensure_ascii=False, indent=1) + "\n"
    return html[:i] + novo + html[j:], quantas


def _corrigir_modelo(html: str) -> tuple:
    """Aplica as trocas e esvazia o exemplo. Devolve `(html, relatorio)`.

    Troca que NÃO CASA é avisada, nunca silenciada: o zip do modelo pode mudar,
    e uma correção que deixou de ser aplicada tem de aparecer na instalação —
    não semanas depois, na tela do operador.
    """
    relatorio = {"aplicadas": 0, "nao_casaram": []}
    for velho, novo in _TROCAS:
        if velho in html:
            html = html.replace(velho, novo, 1)
            relatorio["aplicadas"] += 1
        else:
            relatorio["nao_casaram"].append(velho[:56])
    html, relatorio["ligacoes_de_exemplo_removidas"] = _esvaziar_dataset(html)
    return html, relatorio


def instalar() -> dict:
    if not ZIP.exists():
        raise SystemExit(f"nao achei o modelo em {ZIP}")

    with zipfile.ZipFile(ZIP) as z:
        html = z.read("Radar_Comercial.html").decode("utf-8")

    tamanho_original = len(html)

    html, correcoes = _corrigir_modelo(html)

    # O enxerto entra antes de `</body>`, depois de todo o script do modelo.
    if "</body>" not in html:
        raise SystemExit("o HTML do modelo nao tem </body> — nao sei onde enxertar")
    html = html.replace("</body>", ENXERTO + "</body>", 1)

    ALVO.parent.mkdir(parents=True, exist_ok=True)
    io.open(ALVO, "w", encoding="utf-8").write(html)

    # CONFERE O MECANISMO, e não nomes de função que eu supus.
    #
    # A primeira versão procurava por `aplicarDataset`, `carregarDataset` e
    # afins — nomes que chutei — e avisava "nenhuma encontrada" toda vez. O
    # modelo não tem função com nome próprio: ele atribui a `D` e chama
    # `boot()`. Conferir o nome errado dava um alarme que não significava nada,
    # e alarme que sempre toca é alarme que ninguém escuta.
    checagens = {
        "o modelo troca dados com `D = novo; boot()`": "D = novo; boot();" in html,
        "o dataset vive num <script id=dataset>": 'id="dataset"' in html
                                                  or "id='dataset'" in html,
        "o enxerto aponta para a nossa rota": "/api/bancada/dataset" in html,
        # Conta uma MARCA EXPLICITA. Tentei a rota — aparece tambem no
        # comentario que explica o bloco. Tentei `id="cr-aviso"` — no JavaScript
        # e `d.id = "cr-aviso"`, e a busca literal dava zero. Marca posta de
        # proposito para ser contada nao tem essas surpresas.
        "o enxerto entrou UMA vez": html.count("cr-enxerto-v1") == 1,
        # As correções do modelo. Elas são feitas por troca de texto, e texto
        # muda quando o zip muda — uma que deixou de casar tem de aparecer AQUI,
        # na instalação, e não semanas depois na tela do operador.
        "todas as correções do modelo casaram": not correcoes["nao_casaram"],
        "nenhuma ligação de exemplo sobrou": '"ligacoes": []' in html,
        "a tela lê `lng`, não `lon`": "a.lon" not in html,
    }
    return {"bytes": len(html), "original": tamanho_original,
            "checagens": checagens, "correcoes": correcoes}


if __name__ == "__main__":
    r = instalar()
    c = r["correcoes"]
    print(f"  bancada.html instalada · {r['bytes']/1024:.0f} KB "
          f"(modelo: {r['original']/1024:.0f} KB)")
    print(f"  correções aplicadas: {c['aplicadas']}/{len(_TROCAS)} · "
          f"ligações de exemplo removidas: {c['ligacoes_de_exemplo_removidas']}")
    for t in c["nao_casaram"]:
        print(f"  >>  NAO CASOU: {t}…")
    ruins = [k for k, v in r["checagens"].items() if not v]
    for nome, bom in r["checagens"].items():
        print(f"  {'OK ' if bom else '>> '} {nome}")
    if ruins:
        print("\n  A tela abre, mas pode não receber os dados — o zip do "
              "modelo mudou de mecanismo?")
    sys.exit(1 if ruins else 0)
