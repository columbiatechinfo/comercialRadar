/* Abas por fonte — o mesmo desenho nas DUAS telas que mostram um ponto.
 *
 * POR QUE ARQUIVO PROPRIO
 *
 * Isto nasceu dentro do `fila.js`, na tela do supervisor. Mas a tela que o
 * usuario mais usa e outra: o popup que abre ao CLICAR NUM PONTO no mapa. As
 * duas mostram o mesmo estabelecimento, e mostravam de jeitos diferentes —
 * cada uma com sua lista de campos escrita a mao.
 *
 * Duplicar o renderizador faria as duas divergirem no primeiro ajuste. Aqui
 * elas leem o mesmo catalogo e desenham igual.
 *
 * A PROCEDENCIA vem ou nao vem do servidor conforme o nivel do usuario. Este
 * arquivo nao decide nada sobre isso: se o campo veio, desenha; se nao veio,
 * nao existe. Filtrar no navegador seria fingir.
 */
(function (w) {
  const esc = (t) => String(t == null ? "" : t)
    .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const PESO = { forte: "peso-forte", media: "peso-media",
               neutra: "peso-neutra", contraria: "peso-contra" };

function abasEvidencia(abas) {
  if (!abas || !abas.length) return "";
  const botoes = abas.map((a, i) => {
    const contra = a.contrarias
      ? `<b class="aba-contra" title="${a.contrarias} evidência(s) que pesam contra">${a.contrarias}</b>`
      : "";
    return `<button type="button" class="aba-fonte${i === 0 ? " ativa" : ""}"
              data-aba="${a.fonte}">${esc(a.rotulo)}
              <i>${a.campos}</i>${contra}</button>`;
  }).join("");

  const paineis = abas.map((a, i) => `
    <div class="aba-painel${i === 0 ? "" : " hidden"}" data-painel="${a.fonte}">
      ${a.grupos.map((g) => `
        <div class="aba-grupo">
          <h4>${esc(g.grupo)}</h4>
          ${g.linhas.map((ln) => `
            <div class="aba-linha ${PESO[ln.peso] || ""}"
                 ${ln.ajuda ? `title="${esc(ln.ajuda)}"` : ""}>
              <span class="aba-rot">${esc(ln.rotulo)}</span>
              <span class="aba-val">${
                /* Lista vira lista: socios colados numa linha unica sao
                   ilegiveis, e sao exatamente o campo que alguem confere
                   nome a nome. */
                Array.isArray(ln.valor)
                  ? ln.valor.map((v) => `<span class="aba-chip">${esc(v)}</span>`).join("")
                  : esc(String(ln.valor))
              }</span>
              ${ln.procedencia
                  ? `<em class="aba-proc">${esc(ln.procedencia)}</em>` : ""}
            </div>`).join("")}
        </div>`).join("")}
    </div>`).join("");

  return `<div class="abas-fonte">
            <nav class="abas-nav">${botoes}</nav>
            <div class="abas-corpo">${paineis}</div>
          </div>`;
}

/* Os itens de pauta vem da API (`pauta_possivel`), que os le do enum do
   banco. Duplicar a lista aqui criaria a chance de a tela oferecer um item
   que o banco recusa — e o usuario descobriria isso no erro, depois de
   preencher tudo. */
function ligarAbas(raiz) {
  raiz.querySelectorAll(".aba-fonte").forEach((b) => {
    b.onclick = () => {
      raiz.querySelectorAll(".aba-fonte").forEach((x) => x.classList.remove("ativa"));
      b.classList.add("ativa");
      raiz.querySelectorAll(".aba-painel").forEach((p) => {
        p.classList.toggle("hidden", p.dataset.painel !== b.dataset.aba);
      });
    };
  });
}


  w.abasEvidencia = abasEvidencia;
  w.ligarAbas = ligarAbas;
})(window);
