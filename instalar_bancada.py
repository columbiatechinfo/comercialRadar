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

  function comecar() { carregar(); botaoVoltar(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", comecar);
  } else { comecar(); }
})();
</script>
"""


def instalar() -> dict:
    if not ZIP.exists():
        raise SystemExit(f"nao achei o modelo em {ZIP}")

    with zipfile.ZipFile(ZIP) as z:
        html = z.read("Radar_Comercial.html").decode("utf-8")

    tamanho_original = len(html)

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
    }
    return {"bytes": len(html), "original": tamanho_original,
            "checagens": checagens}


if __name__ == "__main__":
    r = instalar()
    print(f"  bancada.html instalada · {r['bytes']/1024:.0f} KB "
          f"(modelo: {r['original']/1024:.0f} KB)")
    ruins = [k for k, v in r["checagens"].items() if not v]
    for nome, bom in r["checagens"].items():
        print(f"  {'OK ' if bom else '>> '} {nome}")
    if ruins:
        print("\n  A tela abre, mas pode não receber os dados — o zip do "
              "modelo mudou de mecanismo?")
    sys.exit(1 if ruins else 0)
