/* admin.js — telas de usuários e empresas.
 *
 * O que esta tela esconde NÃO é o que protege. As travas de verdade estão no
 * `server.py`: nível acima do próprio, criar root, mexer em empresa alheia —
 * tudo recusado lá. Aqui a regra é repetida só para não oferecer um botão que
 * vai devolver 403; botão escondido é cortesia, não permissão.
 */
(() => {
  const NIVEIS = ["user", "supervisor", "admin", "root"];
  const $ = (id) => document.getElementById(id);
  const eu = () => window.EU || {};
  const podeAdmin = () => NIVEIS.indexOf(eu().nivel) >= NIVEIS.indexOf("admin");

  function montar() {
    const d = document.createElement("div");
    d.innerHTML = `
      <div id="admin-tela" class="sessao-bg hidden">
        <div class="sessao-cartao admin-cartao">
          <div class="admin-abas">
            <button data-aba="usuarios" class="ativa">Usuários</button>
            <button data-aba="empresas" id="aba-empresas">Empresas</button>
            <button id="admin-fechar" class="admin-x" title="Fechar">✕</button>
          </div>

          <section data-painel="usuarios">
            <form id="form-usuario" class="admin-form">
              <div class="admin-linha">
                <label>Nome<input id="nu-nome" required maxlength="120"></label>
                <label>E-mail<input id="nu-email" type="email" required maxlength="200"></label>
              </div>
              <div class="admin-linha">
                <label>Nível<select id="nu-nivel"></select></label>
                <label id="nu-empresa-campo" class="hidden">Empresa<select id="nu-empresa"></select></label>
                <label>Cargo<input id="nu-cargo" maxlength="80"></label>
              </div>
              <p id="nu-msg" class="sessao-msg"></p>
              <button type="submit">Criar usuário</button>
            </form>
            <div id="lista-usuarios" class="admin-lista"></div>
          </section>

          <section data-painel="empresas" class="hidden">
            <form id="form-empresa" class="admin-form">
              <div class="admin-linha">
                <label>Nome<input id="ne-nome" required maxlength="120"></label>
                <label>CNPJ<input id="ne-doc" maxlength="20"></label>
              </div>
              <p id="ne-msg" class="sessao-msg"></p>
              <button type="submit">Criar empresa</button>
            </form>
            <div id="lista-empresas" class="admin-lista"></div>
          </section>
        </div>
      </div>`;
    document.body.appendChild(d);
  }

  function ligar() {
    document.querySelectorAll("#admin-tela .admin-abas button[data-aba]").forEach((b) => {
      b.onclick = () => {
        document.querySelectorAll("#admin-tela .admin-abas button[data-aba]")
          .forEach((x) => x.classList.toggle("ativa", x === b));
        document.querySelectorAll("#admin-tela section")
          .forEach((s) => s.classList.toggle("hidden", s.dataset.painel !== b.dataset.aba));
      };
    });
    $("admin-fechar").onclick = () => $("admin-tela").classList.add("hidden");
    $("form-usuario").onsubmit = criarUsuario;
    $("form-empresa").onsubmit = criarEmpresa;
  }

  async function abrir() {
    if (!podeAdmin()) return;
    // Empresas é aba só de root: admin não cria nem lista cliente alheio.
    $("aba-empresas").classList.toggle("hidden", eu().nivel !== "root");
    montarNiveis();
    $("admin-tela").classList.remove("hidden");
    await Promise.all([carregarUsuarios(), eu().nivel === "root" ? carregarEmpresas() : null]);
  }

  function montarNiveis() {
    // Só até o próprio nível — e root nunca aparece para quem não é root.
    const teto = NIVEIS.indexOf(eu().nivel);
    $("nu-nivel").innerHTML = NIVEIS.slice(0, teto + 1)
      .map((n) => `<option value="${n}">${n}</option>`).join("");
    $("nu-nivel").value = "user";
    $("nu-empresa-campo").classList.toggle("hidden", eu().nivel !== "root");
  }

  async function carregarEmpresas() {
    const r = await fetch("/api/empresas");
    if (!r.ok) return;
    const { empresas } = await r.json();
    $("lista-empresas").innerHTML = empresas.map((e) => `
      <div class="admin-item${e.ativo ? "" : " inativo"}">
        <strong>${esc(e.nome)}</strong>
        <span>${esc(e.documento || "sem CNPJ")}</span>
        <span class="admin-tag">${e.ativo ? "ativa" : "inativa"}</span>
        ${e.ativo ? `<button data-empresa="${e.id}">Desativar</button>` : ""}
      </div>`).join("") || "<p class='sessao-nota'>Nenhuma empresa.</p>";
    $("lista-empresas").querySelectorAll("button[data-empresa]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("Desativar esta empresa? O acervo dela é preservado.")) return;
        await fetch(`/api/empresas/${b.dataset.empresa}`, { method: "DELETE" });
        carregarEmpresas();
      };
    });
    const sel = $("nu-empresa");
    if (sel) sel.innerHTML = empresas.filter((e) => e.ativo)
      .map((e) => `<option value="${e.id}">${esc(e.nome)}</option>`).join("");
  }

  async function carregarUsuarios() {
    const r = await fetch("/api/usuarios");
    if (!r.ok) { $("lista-usuarios").innerHTML = "<p class='sessao-msg erro'>Sem permissão.</p>"; return; }
    const { usuarios } = await r.json();
    $("lista-usuarios").innerHTML = usuarios.map((x) => `
      <div class="admin-item${x.ativo ? "" : " inativo"}">
        <strong>${esc(x.nome || x.email)}</strong>
        <span>${esc(x.email)}</span>
        <span class="admin-tag nivel-${x.nivel}">${x.nivel}</span>
        <span>${esc(x.empresa || "todas")}</span>
        ${x.ativo && x.id !== eu().id && !(x.nivel === "root" && eu().nivel !== "root")
          ? `<button data-usuario="${x.id}">Desativar</button>` : ""}
      </div>`).join("") || "<p class='sessao-nota'>Nenhum usuário.</p>";
    $("lista-usuarios").querySelectorAll("button[data-usuario]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("Desativar este usuário? O histórico dele é preservado.")) return;
        await fetch(`/api/usuarios/${b.dataset.usuario}`, { method: "DELETE" });
        carregarUsuarios();
      };
    });
  }

  async function criarUsuario(ev) {
    ev.preventDefault();
    const corpo = {
      nome: $("nu-nome").value.trim(),
      email: $("nu-email").value.trim(),
      nivel: $("nu-nivel").value,
      cargo: $("nu-cargo").value.trim() || null,
    };
    if (eu().nivel === "root" && corpo.nivel !== "root") corpo.tenant_id = $("nu-empresa").value;
    const r = await fetch("/api/usuarios", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) { msg("nu-msg", b.detail || "Não consegui criar.", true); return; }
    // A senha aparece UMA vez. Não guardamos: recuperação é pelo Auth.
    msg("nu-msg", `Criado. Senha inicial: ${b.senha_inicial} — copie agora, ela não será mostrada de novo.`);
    $("form-usuario").reset();
    montarNiveis();
    carregarUsuarios();
  }

  async function criarEmpresa(ev) {
    ev.preventDefault();
    const r = await fetch("/api/empresas", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nome: $("ne-nome").value.trim(), documento: $("ne-doc").value.trim() || null }),
    });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) { msg("ne-msg", b.detail || "Não consegui criar.", true); return; }
    msg("ne-msg", `Empresa "${b.nome}" criada.`);
    $("form-empresa").reset();
    carregarEmpresas();
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function msg(id, t, erro = false) {
    const p = $(id);
    p.textContent = t;
    p.classList.toggle("erro", erro);
  }

  window.abrirAdmin = abrir;
  document.addEventListener("DOMContentLoaded", () => { montar(); ligar(); });
})();
