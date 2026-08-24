/* fila.js — distribuir e decidir.
 *
 * Duas telas para dois papéis, no mesmo arquivo porque são as duas metades de
 * uma coisa só:
 *   - admin: vê TUDO que há na área, filtra, marca e manda para quem escolher;
 *   - supervisor: recebe, abre o ponto com a evidência na frente, preenche o
 *     que sustenta a mudança de cadastro e decide.
 *
 * As travas de decisão (motivo escrito + genérico para reprovar, observação
 * para devolver, uso + atividade para aprovar) estão no CHECK do banco. O que
 * este arquivo faz é impedir o usuário de descobrir isso por um erro 422 —
 * mostra o que falta enquanto ele escreve.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const eu = () => window.EU || {};
  const MOTIVOS = {
    fachada_residencial: "Fachada residencial",
    endereco_divergente: "Endereço divergente",
    comercio_encerrado: "Comércio encerrado",
    duplicado: "Duplicado",
    evidencia_insuficiente: "Evidência insuficiente",
    ja_e_comercial: "Já é comercial no cadastro",
    outro: "Outro",
  };
  /* O rótulo do cruzamento em português de gente. `reclassificar_alta` é o que
     o banco grava; "Reclassificar — CNPJ confirmado" é o que diz ao supervisor
     por que aquele ponto vale o deslocamento. */
  const CRUZ = {
    reclassificar_alta: "Reclassificar — CNPJ confirmado",
    reclassificar_media: "Reclassificar — CNPJ parcial",
    reclassificar_baixa: "Reclassificar — sem CNPJ",
    ja_cadastrado: "Já é comercial no cadastro",
    fora_do_cadastro: "Fora do cadastro — achado novo",
    sem_poi: "Sem POI",
  };
  const USOS = {
    comercial: "Comercial", misto: "Misto (comércio + moradia)",
    residencial: "Residencial", indefinido: "Não deu para definir",
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const nf = (n) => Number(n || 0).toLocaleString("pt-BR");

  function montar() {
    const d = document.createElement("div");
    d.innerHTML = `
      <div id="fila-tela" class="sessao-bg hidden">
        <div class="sessao-cartao fila-cartao">
          <div class="admin-abas">
            <strong id="fila-titulo">Fila de aprovação</strong>
            <!-- DUAS FILAS, e a separação é do trabalho, não da tela: numa o
                 supervisor confirma um imóvel que o cadastro já conhece; na
                 outra decide se um comércio que ninguém conhecia entra na base.
                 O contador no rótulo existe porque a fila divergente nasce
                 vazia e ninguém abriria uma aba para descobrir isso. -->
            <button id="aba-normal" class="fila-aba on">Cadastro</button>
            <button id="aba-diverg" class="fila-aba">Divergentes <span id="diverg-n"></span></button>
            <select id="fila-status">
              <option value="pendente">Pendentes</option>
              <option value="aprovado">Aprovados</option>
              <option value="reprovado">Reprovados</option>
              <option value="devolvido">Devolvidos</option>
              <option value="todos">Todos</option>
            </select>
            <button id="fila-distribuir" class="hidden">Distribuir…</button>
            <button id="fila-fechar" class="admin-x" title="Fechar">✕</button>
          </div>
          <p id="fila-msg" class="sessao-msg"></p>
          <div id="fila-lista" class="admin-lista fila-lista"></div>
        </div>
      </div>

      <!-- ── Revisão: quase tela cheia, evidência à esquerda, decisão à direita.
           A decisão de tarifa é tomada olhando foto e cadastro lado a lado; o
           formulário de duas linhas que existia aqui pedia um veredito sobre
           algo que o supervisor não estava vendo. -->
      <div id="decidir-tela" class="sessao-bg hidden">
        <form id="decidir-form" class="revisao-cartao">
          <header class="revisao-topo">
            <div>
              <h2 id="decidir-nome">—</h2>
              <p id="decidir-sub" class="sessao-nota"></p>
            </div>
            <div class="revisao-topo-acoes">
              <a id="dec-dossie" class="botao-link hidden" target="_blank">Dossiê</a>
              <button type="button" id="decidir-fechar" class="admin-x" title="Fechar">✕</button>
            </div>
          </header>
          <div class="revisao-corpo">
            <div class="revisao-evid" id="dec-evidencia"></div>
            <div class="revisao-form">
              <div class="d-sub">O que você constatou <em>— vai para o dossiê</em></div>
              <label>Uso observado <b class="obrig">*</b>
                <select id="rev-uso">
                  <option value="">escolha…</option>
                  ${Object.entries(USOS).map(([k, v]) =>
                    `<option value="${k}">${v}</option>`).join("")}
                </select></label>
              <label>Atividade no local <b class="obrig">*</b>
                <input id="rev-atividade" maxlength="160"
                       placeholder="oficina mecânica, mercearia, salão…"></label>
              <label>Nome do estabelecimento
                <input id="rev-nome" maxlength="160"></label>
              <div class="revisao-par">
                <label>CNPJ<input id="rev-cnpj" maxlength="20"></label>
                <label>Telefone<input id="rev-telefone" maxlength="32"></label>
              </div>
              <label>Economias comerciais no imóvel
                <input id="rev-economias" type="number" min="0" max="999" step="1"></label>
              <label class="revisao-check">
                <input type="checkbox" id="rev-end-ok" checked> O endereço do cadastro confere</label>
              <label id="rev-end-campo" class="hidden">Endereço correto
                <input id="rev-endereco" maxlength="200"></label>
              <label class="revisao-check">
                <input type="checkbox" id="rev-visita"> Precisa de visita em campo</label>
              <label>Observação técnica
                <textarea id="rev-obs-tec" rows="2" maxlength="600"
                  placeholder="o que mais sustenta ou enfraquece a mudança"></textarea></label>

              <div class="d-sub">Decisão</div>
              <div class="decidir-acoes">
                <label><input type="radio" name="dec" value="aprovado" checked> Aprovar</label>
                <label><input type="radio" name="dec" value="reprovado"> Reprovar</label>
                <label><input type="radio" name="dec" value="devolvido"> A revisar</label>
                <label><input type="radio" name="dec" value="campo"> Visita de campo</label>
              </div>
              <div id="campo-pauta" class="hidden">
                <div class="d-sub">O que verificar no local <b class="obrig">*</b></div>
                <p class="sessao-nota">Mandar a campo sem dizer o que conferir
                  é mandar de novo depois. Marque ao menos um.</p>
                <div id="pauta-itens" class="pauta-itens"></div>
                <label>Por que cada item — o fato que sustenta
                  <textarea id="dec-pauta-porque" rows="2" maxlength="600"
                    placeholder="ex.: o Maps devolveu S/N e a Receita registra 1842"></textarea>
                </label>
              </div>
              <label id="campo-generico" class="hidden">Motivo (para estatística)
                <select id="dec-generico">
                  <option value="">escolha…</option>
                  ${Object.entries(MOTIVOS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}
                </select>
              </label>
              <label id="campo-escrito" class="hidden">Motivo, com suas palavras
                <textarea id="dec-escrito" rows="3" maxlength="600"
                  placeholder="O que você viu que sustenta a reprovação"></textarea>
              </label>
              <label id="campo-obs" class="hidden">O que impede aprovar agora
                <textarea id="dec-obs" rows="3" maxlength="600"
                  placeholder="A pendência, ou a edição necessária antes de aprovar"></textarea>
              </label>
              <p id="decidir-msg" class="sessao-msg"></p>
              <div class="sessao-acoes">
                <button type="submit" id="dec-enviar">Registrar decisão</button>
              </div>
            </div>
          </div>
        </form>
      </div>

      <!-- ── Distribuição: a lista inteira da área, para escolher com critério.
           Antes eram "os N primeiros", e o admin não via o que estava mandando. -->
      <div id="distribuir-tela" class="sessao-bg hidden">
        <form id="distribuir-form" class="revisao-cartao">
          <header class="revisao-topo">
            <div>
              <h2>Distribuir para supervisores</h2>
              <p id="dist-resumo" class="sessao-nota">carregando a área…</p>
            </div>
            <button type="button" id="distribuir-fechar" class="admin-x" title="Fechar">✕</button>
          </header>

          <div class="dist-filtros">
            <input id="f-busca" placeholder="🔎 nome ou endereço…">
            <select id="f-cruz"><option value="">situação no cadastro — todas</option></select>
            <select id="f-categoria"><option value="">categoria — todas</option></select>
            <select id="f-edificacao"><option value="">edificação — todas</option></select>
            <select id="f-conservacao"><option value="">conservação — todas</option></select>
            <label><input type="checkbox" id="f-foto"> com foto</label>
            <label><input type="checkbox" id="f-sv"> com Street View</label>
            <label><input type="checkbox" id="f-cnpj"> com CNPJ</label>
            <label><input type="checkbox" id="f-fila" checked> esconder o que já está na fila</label>
            <label><input type="checkbox" id="f-jacom"> esconder os já comerciais</label>
          </div>

          <div class="dist-barra">
            <span id="dist-contagem">—</span>
            <button type="button" id="dist-marcar">Marcar os filtrados</button>
            <button type="button" id="dist-limpar">Limpar seleção</button>
          </div>

          <div id="dist-lista" class="dist-lista"></div>

          <footer class="dist-rodape">
            <div>
              <div class="dist-rot">Enviar para</div>
              <div id="dist-supers" class="dist-supers"></div>
            </div>
            <div class="dist-enviar">
              <p id="dist-msg" class="sessao-msg"></p>
              <button type="submit" id="dist-btn" disabled>Enviar</button>
            </div>
          </footer>
        </form>
      </div>
      <!-- ── Decidir o achado divergente. Formulário mais curto que o da fila
           normal de propósito: aqui não se corrige um cadastro, se diz sim ou
           não a um estabelecimento que a leitura encontrou. -->
      <div id="divergente-tela" class="sessao-bg hidden">
        <form id="divergente-form" class="revisao-cartao">
          <header class="revisao-topo">
            <div>
              <h2 id="div-nome">—</h2>
              <p id="div-sub" class="sessao-nota"></p>
            </div>
            <button type="button" id="div-fechar" class="admin-x" title="Fechar">✕</button>
          </header>
          <div class="revisao-corpo">
            <div class="revisao-evid" id="div-evidencia"></div>
            <div class="revisao-form">
              <div class="d-sub">Decisão</div>
              <div class="decidir-acoes">
                <label><input type="radio" name="divdec" value="aceito" checked> Aceitar o achado</label>
                <label><input type="radio" name="divdec" value="recusado"> Recusar</label>
                <label><input type="radio" name="divdec" value="devolvido"> Devolver</label>
              </div>
              <label id="div-campo-generico" class="hidden">Motivo (para estatística)
                <select id="div-generico"></select></label>
              <label id="div-campo-escrito" class="hidden">Motivo, com suas palavras
                <textarea id="div-escrito" rows="3" maxlength="600"
                  placeholder="O que você viu que derruba este achado"></textarea></label>
              <label id="div-campo-obs" class="hidden">O que impede decidir agora
                <textarea id="div-obs" rows="3" maxlength="600"
                  placeholder="A pendência que precisa ser resolvida antes"></textarea></label>
              <p id="div-msg" class="sessao-msg"></p>
              <div class="sessao-acoes">
                <button type="submit">Registrar decisão</button>
              </div>
            </div>
          </div>
        </form>
      </div>
      `;
    document.body.appendChild(d);
  }

  let itemAtual = null;      // id da atribuição em revisão
  let fichaAtual = null;     // o que /ficha devolveu, para pré-preencher
  let candidatos = [];       // tudo que há na área
  let marcados = new Set();  // ids de POI escolhidos, sobrevive à troca de filtro

  function ligar() {
    $("fila-fechar").onclick = () => $("fila-tela").classList.add("hidden");
    $("aba-normal").onclick = () => trocarAba("normal");
    $("aba-diverg").onclick = () => trocarAba("diverg");
    $("div-fechar").onclick = () => $("divergente-tela").classList.add("hidden");
    $("divergente-form").onsubmit = enviarDivergente;
    document.querySelectorAll('input[name="divdec"]')
      .forEach((r) => { r.onchange = pintarDivergente; });
    $("fila-status").onchange = carregar;
    $("fila-distribuir").onclick = abrirDistribuir;
    $("decidir-fechar").onclick = () => $("decidir-tela").classList.add("hidden");
    $("distribuir-fechar").onclick = () => $("distribuir-tela").classList.add("hidden");
    $("decidir-form").onsubmit = enviarDecisao;
    $("distribuir-form").onsubmit = enviarDistribuicao;
    document.querySelectorAll('input[name="dec"]').forEach((r) => { r.onchange = pintarCampos; });
    $("rev-end-ok").onchange = () =>
      $("rev-end-campo").classList.toggle("hidden", $("rev-end-ok").checked);

    for (const id of ["f-busca", "f-cruz", "f-categoria", "f-edificacao",
                      "f-conservacao", "f-foto", "f-sv", "f-cnpj", "f-fila",
                      "f-jacom"]) {
      const el = $(id);
      // busca reage a cada tecla; select e caixa, à mudança
      el.addEventListener(el.tagName === "INPUT" && el.type === "text" ? "input" : "change",
                          pintarLista);
    }
    $("dist-marcar").onclick = () => { filtrados().forEach((i) => marcados.add(i.id)); pintarLista(); };
    $("dist-limpar").onclick = () => { marcados.clear(); pintarLista(); };
  }

  function pintarCampos() {
    const v = document.querySelector('input[name="dec"]:checked').value;
    $("campo-generico").classList.toggle("hidden", v !== "reprovado");
    $("campo-escrito").classList.toggle("hidden", v !== "reprovado");
    $("campo-obs").classList.toggle("hidden", v !== "devolvido");
    $("campo-pauta").classList.toggle("hidden", v !== "campo");
    if (v === "campo") montarPauta();
    $("decidir-msg").textContent = "";
  }

  /* Qual fila está em foco. Duas listas, um cartão só. */
  let abaFila = "normal";
  const STATUS_ABA = {
    normal: [["pendente", "Pendentes"], ["aprovado", "Aprovados"],
             ["reprovado", "Reprovados"], ["devolvido", "Devolvidos"],
             ["todos", "Todos"]],
    // O vocabulário muda com o trabalho: aqui não se "aprova um cadastro", se
    // ACEITA um estabelecimento novo na base.
    diverg: [["pendente", "Pendentes"], ["aceito", "Aceitos"],
             ["recusado", "Recusados"], ["devolvido", "Devolvidos"],
             ["todos", "Todos"]],
  };

  function trocarAba(qual) {
    abaFila = qual;
    $("aba-normal").classList.toggle("on", qual === "normal");
    $("aba-diverg").classList.toggle("on", qual === "diverg");
    $("fila-distribuir").classList.toggle(
      "hidden", qual !== "normal" || !["admin", "root"].includes(eu().nivel));
    $("fila-status").innerHTML = STATUS_ABA[qual]
      .map(([v, r]) => `<option value="${v}">${r}</option>`).join("");
    return carregar();
  }

  async function abrir() {
    $("fila-titulo").textContent = eu().nivel === "supervisor"
      ? "Minha fila" : "Fila de aprovação da empresa";
    $("fila-tela").classList.remove("hidden");
    await trocarAba(abaFila);
    contarDivergentes();
  }

  /* O contador da aba. Sem ele a fila divergente é uma aba que ninguém clica:
     não há como saber se há trabalho lá dentro sem entrar. */
  async function contarDivergentes() {
    try {
      const r = await fetch("/api/divergentes?status=pendente&limite=1000");
      if (!r.ok) return;
      const n = ((await r.json()).itens || []).length;
      $("diverg-n").textContent = n ? `(${n})` : "";
    } catch { /* contador é conforto, não pode derrubar a tela */ }
  }

  async function carregar() {
    const st = $("fila-status").value;
    const r = await fetch(`/api/fila?status=${st}&limite=300`);
    if (!r.ok) { $("fila-lista").innerHTML = "<p class='sessao-msg erro'>Não consegui carregar.</p>"; return; }
    const { itens } = await r.json();
    $("fila-msg").textContent = `${itens.length} ${itens.length === 1 ? "item" : "itens"}`;
    if (!itens.length) {
      $("fila-lista").innerHTML = "<p class='sessao-nota'>Nada aqui. Quando o admin distribuir, aparece nesta lista.</p>";
      return;
    }
    // A LINHA INTEIRA ABRE O ITEM. O botão "Decidir" numa ponta escondia que
    // aquilo é um registro para examinar, não uma decisão de um clique.
    $("fila-lista").innerHTML = itens.map((i) => `
      <div class="admin-item fila-item" data-item="${i.id}" data-poi="${i.poi_id}"
           role="button" tabindex="0">
        <strong>${esc(i.nome || "sem nome")}</strong>
        <span>${esc(i.categoria || "—")}</span>
        ${i.nota_ia !== null && i.nota_ia !== undefined
          ? `<span class="nota-ia n${i.nota_ia >= 7 ? "alta" : i.nota_ia >= 4 ? "media" : "baixa"}"
                   title="${esc((VEREDITO[i.veredito_ia] || [""])[0])} — nota da IA, 0 a 10">${
              i.nota_ia}</span>` : ""}
        <span class="admin-tag st-${i.status}">${i.status}</span>
        ${i.supervisor ? `<span>${esc(i.supervisor)}</span>` : ""}
        <span class="fila-abrir">abrir ›</span>
      </div>`).join("");
    $("fila-lista").querySelectorAll(".fila-item").forEach((el) => {
      const ir = () => abrirRevisao(el.dataset.item);
      el.onclick = ir;
      el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ir(); } };
    });
  }

  /* ── Fila do comércio divergente ───────────────────────────────────────── */

  let motivosDiv = {};
  let divAtual = null;

  async function carregarDivergentes() {
    const st = $("fila-status").value;
    const r = await fetch(`/api/divergentes?status=${st}&limite=300`);
    if (!r.ok) {
      $("fila-lista").innerHTML = "<p class='sessao-msg erro'>Não consegui carregar.</p>";
      return;
    }
    const { itens, motivos } = await r.json();
    motivosDiv = motivos || {};
    $("fila-msg").textContent = `${itens.length} ${itens.length === 1 ? "achado" : "achados"}`;
    if (!itens.length) {
      $("fila-lista").innerHTML = `<p class='sessao-nota'>Nada aqui. Quando a leitura
        encontrar comércio com nome diferente do cadastro, ele cai nesta lista.</p>`;
      return;
    }
    /* OS DOIS NOMES LADO A LADO. A decisão É comparar o que o cadastro diz com
       o que está escrito na parede; obrigar a abrir a ficha para ver isso
       esconderia justamente a pergunta. */
    $("fila-lista").innerHTML = itens.map((i) => `
      <div class="admin-item fila-item div-item" data-item="${i.id}"
           role="button" tabindex="0">
        <div class="div-par">
          <span class="div-de">${esc(i.nome_cadastro || "sem nome")}</span>
          <span class="div-seta">→</span>
          <strong class="div-para">${esc(i.nome_lido || "(sem nome legível)")}</strong>
        </div>
        <span>${esc(i.atividade || "—")}</span>
        <span class="admin-tag st-${i.status}">${i.status}</span>
        ${i.supervisor ? `<span>${esc(i.supervisor)}</span>` : ""}
        <span class="fila-abrir">abrir ›</span>
      </div>`).join("");
    $("fila-lista").querySelectorAll(".div-item").forEach((el) => {
      const ir = () => abrirDivergente(el.dataset.item);
      el.onclick = ir;
      el.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ir(); }
      };
    });
  }

  async function abrirDivergente(itemId) {
    divAtual = itemId;
    $("div-evidencia").innerHTML = "<p class='sessao-nota'>carregando a evidência…</p>";
    $("divergente-tela").classList.remove("hidden");
    const r = await fetch(`/api/divergentes/${itemId}/ficha`);
    if (!r.ok) {
      $("div-evidencia").innerHTML = "<p class='sessao-msg erro'>Não consegui abrir.</p>";
      return;
    }
    const f = await r.json();
    const p = f.poi || {}, it = f.item || {}, fa = p.fachada || {};
    $("div-nome").textContent = it.nome_lido || "(sem nome legível)";
    $("div-sub").textContent = [p.endereco, p.cidade].filter(Boolean).join(" · ");
    const outros = (fa.comercios_encontrados || []).map((c) =>
      [c.nome_lido || "(sem nome)",
       `${c.atividade || ""}${c.e_o_procurado ? " — é o do cadastro" : ""}`]);
    $("div-evidencia").innerHTML = `
      <section class="ver-ia ver-med">
        <h3>O que mudou em relação ao cadastro</h3>
        <table class="revisao-tab">
          <tr><th>O cadastro diz</th><td>${esc(p.nome || "—")}</td></tr>
          <tr><th>A IA leu na parede</th><td><b>${esc(it.nome_lido || "sem nome legível")}</b></td></tr>
          <tr><th>Atividade aparente</th><td>${esc(it.atividade || "—")}</td></tr>
        </table>
        <p class="sessao-nota">Aceitar registra este estabelecimento como achado do
          endereço. Recusar exige dizer por quê.</p>
      </section>
      ${evidenciaVisual(p)}
      ${fa.descricao ? `<section><h3>O que a IA descreveu na cena</h3>
        <p class="revisao-desc">${esc(fa.descricao)}</p></section>` : ""}
      ${linhas("Todos os comércios vistos na cena", outros)}`;
    ligarVisor("div-evidencia");
    $("div-generico").innerHTML = '<option value="">escolha…</option>'
      + Object.entries(motivosDiv).map(([k, v]) =>
          `<option value="${k}">${esc(v)}</option>`).join("");
    $("div-escrito").value = ""; $("div-obs").value = "";
    $("div-msg").textContent = "";
    document.querySelector('input[name="divdec"][value="aceito"]').checked = true;
    pintarDivergente();
  }

  function pintarDivergente() {
    const v = document.querySelector('input[name="divdec"]:checked')?.value;
    $("div-campo-generico").classList.toggle("hidden", v !== "recusado");
    $("div-campo-escrito").classList.toggle("hidden", v !== "recusado");
    $("div-campo-obs").classList.toggle("hidden", v !== "devolvido");
  }

  async function enviarDivergente(ev) {
    ev.preventDefault();
    const status = document.querySelector('input[name="divdec"]:checked').value;
    const corpo = { status, observacao: $("div-obs").value.trim() || null };
    if (status === "recusado") {
      corpo.motivo_generico = $("div-generico").value;
      corpo.motivo_escrito = $("div-escrito").value.trim();
      // As mesmas travas do CHECK do banco, ditas ANTES do 422: descobrir a
      // regra por mensagem de erro é descobrir tarde.
      if (!corpo.motivo_generico || corpo.motivo_escrito.length < 10) {
        msg("div-msg", "Recusar exige o motivo da lista e a explicação escrita (10+ caracteres).", true);
        return;
      }
    }
    const r = await fetch(`/api/divergentes/${divAtual}/decidir`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      msg("div-msg", b.detail || "Não consegui registrar.", true);
      return;
    }
    $("divergente-tela").classList.add("hidden");
    await carregarDivergentes();
    contarDivergentes();
  }


  /* ── Revisão ───────────────────────────────────────────────────────────── */

  // `moradia_luxo_habitada` → "Moradia luxo habitada". Chave de enum na tela de
  // quem decide parece erro de exportação.
  const rot = (v) => !v ? null : String(v).split(",")
    .map((p) => p.replace(/_/g, " ").trim())
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(", ");

  // "sim"/"não"/nada — booleano cru na tela de quem decide vira ruído: `false`
  // e campo vazio são coisas diferentes e precisam parecer diferentes.
  const sn = (v) => (v === undefined || v === null ? null : (v ? "sim" : "não"));

  const VEREDITO = {
    aprova_comercial: ["Aprova como comercial", "ok"],
    recomenda_visita: ["Recomenda visita para comprovar", "med"],
    reprova: ["Reprova — não é comercial", "ruim"],
  };

  /* A SUGESTÃO da leitura em quatro fases — e a palavra é sugestão de propósito.
     A IA só devolve `aprovar`, `reprovar` ou `revisar`, e nenhuma das três é
     decisão: o cadastro do cliente só muda quando uma pessoa assina embaixo. A
     tela precisa dizer isso, senão o supervisor vira carimbo do modelo. */
  const ACAO_IA = {
    aprovar: ["Sugere APROVAR como comercial", "ok"],
    revisar: ["Sugere REVISÃO — não se decidiu sozinha", "med"],
    reprovar: ["Sugere REPROVAR — não achou comércio", "ruim"],
  };

  function sugestaoIA(fa) {
    if (!fa.acao) return "";
    const [txt, cls] = ACAO_IA[fa.acao] || [fa.acao, ""];
    return `<section class="ver-ia ver-${cls}">
      <h3>O que a IA sugere</h3>
      <div class="ver-cab"><strong>${esc(txt)}</strong>
        ${fa.confianca !== null && fa.confianca !== undefined
          ? `<span class="ver-nota">${esc(fa.confianca)}<em> de confiança</em></span>` : ""}</div>
      ${fa.justificativa ? `<p class="ver-just">${esc(fa.justificativa)}</p>` : ""}
      ${fa.ressalva ? `<p class="ver-just"><em>Ressalva:</em> ${esc(fa.ressalva)}</p>` : ""}
      <p class="sessao-nota">Sugestão da leitura automática. A decisão que vale é
        a sua — aprovar, devolver ou reprovar abaixo.</p>
    </section>`;
  }

  /* O veredito da IA VEM PRIMEIRO na coluna de evidência: é a resposta que o
     supervisor está ali para conferir. Enterrado no meio da tabela, ele lia as
     30 linhas anteriores antes de saber o que estava sendo proposto. */
  function vereditoIA(fa) {
    if (!fa.veredito) return "";
    const [txt, cls] = VEREDITO[fa.veredito] || [fa.veredito, ""];
    let fat = fa.fatores || [];
    if (typeof fat === "string") { try { fat = JSON.parse(fat); } catch { fat = [fat]; } }
    return `<section class="ver-ia ver-${cls}">
      <h3>Veredito da IA</h3>
      <div class="ver-cab"><strong>${esc(txt)}</strong>
        ${fa.nota !== null && fa.nota !== undefined
          ? `<span class="ver-nota">${esc(fa.nota)}<em>/10</em></span>` : ""}</div>
      ${fa.justificativa ? `<p class="ver-just">${esc(fa.justificativa)}</p>` : ""}
      ${fat.length ? `<ul class="ver-fat">${fat.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <p class="sessao-nota">0 = com certeza não é comercial · 10 = com certeza é.
        A nota é sugestão: quem decide é você.</p>
    </section>`;
  }

  /* TELA CHEIA da imagem. 180 px de altura serve para escolher entre as fotos,
     não para ler o número na parede nem o texto do letreiro — e são esses dois
     que decidem. O visor é criado uma vez e reaproveitado. */
  function ligarVisor(container = "dec-evidencia") {
    let v = $("fila-visor");
    if (!v) {
      v = document.createElement("div");
      v.id = "fila-visor";
      v.innerHTML = '<img alt="">';
      v.addEventListener("click", () => v.classList.remove("on"));
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") v.classList.remove("on");
      });
      document.body.appendChild(v);
    }
    const alvo = v.querySelector("img");
    $(container).querySelectorAll("img[data-zoom]").forEach((im) => {
      im.onclick = () => { alvo.src = im.src; v.classList.add("on"); };
    });
  }

  function linhas(titulo, pares) {
    const corpo = pares.filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("");
    return corpo ? `<section><h3>${titulo}</h3><table class="revisao-tab">${corpo}</table></section>` : "";
  }

  /* A EVIDÊNCIA VISUAL — a mesma nas duas filas.
   *
   * A fachada com a mira sobre o imóvel avaliado, e cada foto do Maps com a
   * DATA e o veredito que a IA deu A ELA. Foto sem essas duas coisas é
   * ilustração; com elas é prova — e duas das quatro costumam ser de outro
   * estabelecimento.
   *
   * É função e não HTML repetido porque a fila de divergentes mostra
   * exatamente isto: duplicar faria uma das duas envelhecer sozinha. */
  const VER_FOTO = {
    mostra_o_alvo: ["mostra o estabelecimento", "ok"],
    mostra_atividade_compativel: ["mesmo ramo, sem nome legível", "ok"],
    mostra_outro: ["é de OUTRO estabelecimento", "baixo"],
    nao_e_estabelecimento: ["não mostra estabelecimento", "baixo"],
    indefinido: ["não deu para dizer", "med"],
  };

  /* Rótulo do item de pauta. A chave vem do enum do banco; o texto mora aqui
     porque é linguagem de tela, não de dado. */
  /* PESO, abasEvidencia e ligarAbas vivem em `abas.js`: o popup do mapa usa os
     mesmos, e duplicar faria as duas telas divergirem no primeiro ajuste. */
  const abasEvidencia = window.abasEvidencia, ligarAbas = window.ligarAbas;

  const PAUTA = {
    confirmar_atividade: "Confirmar que o comércio opera",
    confirmar_numero:    "Conferir o número da porta",
    contar_unidades:     "Contar as unidades no imóvel",
    fotografar_fachada:  "Fotografar a fachada",
    registrar_coordenada:"Registrar a coordenada no local",
    confirmar_endereco:  "Confirmar qual é o endereço",
  };

  /* O peso de cada campo vira cor. `contraria` é o que importa: evidência que
     desmente tem de saltar, senão some no meio das que confirmam. */
  
  
  function montarPauta() {
    const caixa = $("pauta-itens");
    if (caixa.dataset.pronto) return;
    const itens = (fichaAtual && fichaAtual.pauta_possivel) || Object.keys(PAUTA);
    caixa.innerHTML = itens.map((k) => `
      <label class="pauta-item">
        <input type="checkbox" name="pauta" value="${k}">
        ${esc(PAUTA[k] || k)}
      </label>`).join("");
    caixa.dataset.pronto = "1";
  }

  
  function evidenciaVisual(p) {
    const sv = p.streetview_mirado || p.streetview;
    const fotos = (p.fotos || []).filter((f) => f && f.src);
    if (!sv && !fotos.length) {
      return "<p class='sessao-nota'>Sem imagem gravada para este ponto.</p>";
    }
    return `<div class="revisao-fotos">
      ${sv ? `<figure><img src="${sv}" alt="" data-zoom>
        <figcaption>Street View${p.streetview_data ? ` · ${esc(p.streetview_data)}` : ""}
          ${p.streetview_mirado ? "<br><small>a mira aponta o imóvel avaliado</small>" : ""}
          ${p.streetview_url ? `<br><a href="${esc(p.streetview_url)}" target="_blank"
             rel="noopener">abrir no Street View ↗</a>` : ""}
        </figcaption></figure>` : ""}
      ${fotos.map((f) => {
        const v = VER_FOTO[f.veredito] || [f.veredito || "", ""];
        return `<figure><img src="${f.src}" alt="" data-zoom>
          <figcaption>Maps${f.data ? ` · ${esc(f.data)}` : ""}
            ${v[0] ? `<br><span class="tag ${v[1]}">${esc(v[0])}</span>` : ""}
            ${f.texto ? `<br><small>🔤 ${esc(f.texto)}</small>` : ""}
          </figcaption></figure>`;
      }).join("")}
      </div>`;
  }

  async function abrirRevisao(itemId) {
    itemAtual = itemId;
    $("dec-evidencia").innerHTML = "<p class='sessao-nota'>carregando a evidência…</p>";
    $("decidir-tela").classList.remove("hidden");
    const r = await fetch(`/api/fila/${itemId}/ficha`);
    if (!r.ok) {
      $("dec-evidencia").innerHTML = "<p class='sessao-msg erro'>Não consegui abrir esta ficha.</p>";
      return;
    }
    const f = await r.json();
    fichaAtual = f;
    const p = f.poi || {}, it = f.item || {}, cad = it.cadastro || {},
          ia = p.ia || {}, fa = p.fachada || {};

    $("decidir-nome").textContent = p.nome || "sem nome";
    $("decidir-sub").textContent = [p.endereco, p.cidade].filter(Boolean).join(" · ");
    // aba nova tambem nao manda cabecalho: o token vai na query
    $("dec-dossie").href = window.comToken(`/api/dossie/${it.poi_id}`);
    $("dec-dossie").classList.remove("hidden");

    // A EVIDÊNCIA COMO A GALERIA MOSTRA, porque foi lá que ela provou servir
    // para julgar: a fachada com a mira sobre o imóvel avaliado, e cada foto do
    // Maps com a DATA e o veredito que a IA deu a ELA. Foto sem essas duas
    // coisas é ilustração — e duas das quatro costumam ser de outro comércio.
    $("dec-evidencia").innerHTML = `
      ${evidenciaVisual(p)}
      ${sugestaoIA(fa)}
      ${fa.descricao ? `<section><h3>O que a IA descreveu na cena</h3>
         <p class="revisao-desc">${esc(fa.descricao)}</p></section>` : ""}
      ${p.triagem && p.triagem.veredito ? linhas("Qualidade da foto de fachada", [
        ["Nota", `${p.triagem.nota}/100 — ${esc(p.triagem.veredito)}`],
        ["Tipo de foto", rot(p.triagem.tipo_de_foto)],
        ["Motivo", p.triagem.motivo]]) : ""}
      ${/* AS ABAS SUBSTITUEM a lista escrita a mao que ficava aqui.
            Ela repetia onze campos fixos no JavaScript; cada campo novo que a
            extracao aprendia exigia editar este arquivo. Agora vem do catalogo:
            `campo_catalogo` ganha uma linha e a aba aparece sozinha.
            A procedencia so vem preenchida para o `root` — filtrada no
            servidor, nao aqui. */ ""}
      ${abasEvidencia(f.abas)}
      ${linhas("O que o cadastro do cliente diz", [
        ["Matrícula", cad.num_ligacao], ["Categoria no cadastro", cad.categoria],
        ["É comercial no cadastro", cad.e_comercial === undefined ? null : (cad.e_comercial ? "sim" : "não")],
        ["Cruzamento", CRUZ[cad.cruz_flag] || cad.cruz_flag],
        ["Distância do casamento", cad.cruz_dist_m != null ? `${cad.cruz_dist_m} m` : null],
        ["Economias no imóvel", cad.economias], ["Economias comerciais", cad.economias_com],
      ]) || "<section><h3>Cadastro do cliente</h3><p class='sessao-nota'>Este ponto não casou com nenhum imóvel da base — é achado novo.</p></section>"}
      ${vereditoIA(fa)}
      ${linhas("O que a leitura de fachada viu", [
        ["Achou o estabelecimento", rot(fa.alvo_encontrado)],
        ["Onde na imagem", rot(fa.posicao)],
        ["Letreiro lido na parede", fa.letreiro],
        ["Tipo de cliente", rot(fa.tipo_cliente)],
        ["Tipo de imóvel", rot(fa.tipo_imovel)],
        ["Ocupação", rot(fa.status_ocupacao)],
        ["Atividade aparente", rot(fa.atividade_aparente)],
        ["Atividade no alvo", rot(fa.atividade_no_alvo)],
        ["Indício comercial", sn(fa.indicio_comercial)],
        ["Múltiplas unidades no lote", sn(fa.multiplas_unidades)],
        ["Limite entre lotes ambíguo", sn(fa.limite_ambiguo)],
        ["Distintivo do Google na cena", sn(fa.marcador_google)],
        ["Habitações distintas", fa.habitacoes],
        ["Como foram contadas", rot(fa.metodo_habitacoes)],
        ["Tipo de via", rot(fa.tipo_via)],
        ["Pessoas na imagem", rot(fa.pessoas)],
        ["Uso observado", fa.uso_observado], ["Tipologia", fa.tipologia],
        ["Conservação", fa.conservacao], ["Número lido", fa.numero],
        ["Número na parede", fa.numero_parede],
        ["Número confere", sn(fa.numero_confere)],
        ["Confiança", fa.confianca],
        ["Ressalva da IA", fa.ressalva],
      ])}
      ${linhas("O que a IA concluiu", [
        ["Veredito", ia.veredito], ["Motivo", ia.motivo], ["Atividade", ia.atividade],
        ["Porte", ia.porte], ["Confere com o buscado", ia.confere === undefined || ia.confere === null
          ? null : (ia.confere ? "sim" : "não")],
      ])}
      ${it.status !== "pendente" ? linhas("Decisão anterior", [
        ["Status", it.status], ["Motivo", MOTIVOS[it.motivo_generico] || it.motivo_generico],
        ["Escrito", it.motivo_escrito], ["Observação", it.observacao]]) : ""}`;
    ligarAbas($("dec-evidencia"));
    ligarVisor();

    // Pré-preenche com o que já se sabe. O supervisor CORRIGE o que estiver
    // errado em vez de redigitar o que já está certo — e o que ele não tocar
    // fica gravado como conferido, que é o valor da revisão.
    const rev = it.revisao || {};
    $("rev-uso").value = rev.uso || "";
    // `atividade_no_alvo` é o que a leitura 2.0.0 afirma exercer NAQUELE
    // imóvel — mais específico que a atividade genérica da análise antiga.
    $("rev-atividade").value = rev.atividade || fa.atividade_no_alvo || ia.atividade || "";
    $("rev-nome").value = rev.nome_confirmado || p.nome || "";
    $("rev-cnpj").value = rev.cnpj || p.cnpj || "";
    $("rev-telefone").value = rev.telefone || p.telefone || "";
    $("rev-economias").value = rev.economias_comerciais ?? cad.economias_com ?? "";
    $("rev-end-ok").checked = rev.endereco_confere !== false;
    $("rev-endereco").value = rev.endereco_corrigido || "";
    $("rev-end-campo").classList.toggle("hidden", $("rev-end-ok").checked);
    $("rev-visita").checked = !!rev.visita_necessaria;
    $("rev-obs-tec").value = rev.observacao_tecnica || "";
    $("dec-generico").value = ""; $("dec-escrito").value = ""; $("dec-obs").value = "";
    document.querySelector('input[name="dec"][value="aprovado"]').checked = true;
    pintarCampos();
  }

  function coletarRevisao() {
    const num = parseInt($("rev-economias").value, 10);
    const r = {
      uso: $("rev-uso").value,
      atividade: $("rev-atividade").value.trim(),
      nome_confirmado: $("rev-nome").value.trim(),
      cnpj: $("rev-cnpj").value.trim(),
      telefone: $("rev-telefone").value.trim(),
      endereco_confere: $("rev-end-ok").checked,
      visita_necessaria: $("rev-visita").checked,
      observacao_tecnica: $("rev-obs-tec").value.trim(),
    };
    if (Number.isFinite(num)) r.economias_comerciais = num;
    if (!$("rev-end-ok").checked) r.endereco_corrigido = $("rev-endereco").value.trim();
    return r;
  }

  async function enviarDecisao(ev) {
    ev.preventDefault();
    const status = document.querySelector('input[name="dec"]:checked').value;
    const corpo = { status, revisao: coletarRevisao() };
    if (status === "aprovado" && (!corpo.revisao.uso || corpo.revisao.atividade.length < 3)) {
      msg("decidir-msg", "Aprovar exige o uso observado e a atividade do local — é o que o dossiê afirma.", true);
      return;
    }
    if (status === "reprovado") {
      corpo.motivo_generico = $("dec-generico").value;
      corpo.motivo_escrito = $("dec-escrito").value.trim();
      if (!corpo.motivo_generico || corpo.motivo_escrito.length < 3) {
        msg("decidir-msg", "Reprovar exige o motivo da lista E o motivo escrito.", true);
        return;
      }
    }
    if (status === "devolvido") {
      corpo.observacao = $("dec-obs").value.trim();
      if (corpo.observacao.length < 3) {
        msg("decidir-msg", "Diga o que impede aprovar agora.", true);
        return;
      }
    }
    if (status === "campo") {
      corpo.pauta = [...document.querySelectorAll('input[name="pauta"]:checked')]
        .map((c) => c.value);
      if (!corpo.pauta.length) {
        msg("decidir-msg",
            "Marque o que verificar no local. Mandar a campo sem pauta é mandar de novo depois.",
            true);
        return;
      }
      /* O texto do "por que" fica sob a chave do PRIMEIRO item marcado, que e o
         de maior peso na ordem do catalogo. Nao se inventa justificativa para
         os demais: item sem fato declarado fica sem. */
      const porque = $("dec-pauta-porque").value.trim();
      if (porque) corpo.pauta_porque = { [corpo.pauta[0]]: porque };
    }
    const r = await fetch(`/api/fila/${itemAtual}/decidir`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) { msg("decidir-msg", b.detail || "Não consegui registrar.", true); return; }
    document.querySelectorAll('input[name="pauta"]:checked')
      .forEach((c) => { c.checked = false; });   // a proxima ficha nao herda
    $("dec-pauta-porque").value = "";
    $("decidir-tela").classList.add("hidden");
    carregar();
  }

  /* ── Distribuição ──────────────────────────────────────────────────────── */

  function opcoes(sel, facetas, rotulos) {
    const el = $(sel);
    const antes = el.value;                 // sobrevive à recarga da lista
    const primeira = el.options[0].outerHTML;
    el.innerHTML = primeira + (facetas || [])
      .filter((f) => f.v && f.v !== "—")
      .map((f) => `<option value="${esc(f.v)}">${esc(rotulos?.[f.v] || f.v)} (${nf(f.n)})</option>`)
      .join("");
    if (antes && [...el.options].some((o) => o.value === antes)) el.value = antes;
  }

  async function abrirDistribuir() {
    marcados.clear();
    candidatos = [];
    $("dist-lista").innerHTML = "<p class='sessao-nota'>carregando…</p>";
    $("distribuir-tela").classList.remove("hidden");

    const ru = await fetch("/api/usuarios");
    if (!ru.ok) { msg("dist-msg", "Sem permissão para distribuir.", true); return; }
    const { usuarios } = await ru.json();
    const sups = usuarios.filter((u) => u.ativo && ["supervisor", "admin"].includes(u.nivel));
    $("dist-supers").innerHTML = sups.length
      ? sups.map((s) => `<label class="dist-um"><input type="checkbox" value="${s.id}">
           ${esc(s.nome || s.email)} <span class="admin-tag nivel-${s.nivel}">${s.nivel}</span></label>`).join("")
      : "<p class='sessao-nota'>Nenhum supervisor cadastrado nesta empresa.</p>";

    await carregarCandidatos();
  }

  async function carregarCandidatos() {
    const r = await fetch("/api/fila/candidatos");
    const b = await r.json().catch(() => ({}));
    if (!r.ok) {
      $("dist-lista").innerHTML =
        `<p class='sessao-msg erro'>${esc(b.detail || "Não consegui listar a área.")}</p>`;
      $("dist-resumo").textContent = "";
      return;
    }
    candidatos = b.itens || [];
    $("dist-resumo").innerHTML =
      `<b>${nf(b.total)}</b> pontos dentro da área de trabalho · ${nf(b.na_fila)} já estão na fila`
      + (b.ja_comerciais
         ? ` · <b class="aviso">${nf(b.ja_comerciais)} já são comerciais no cadastro</b>` : "")
      + (b.truncado ? ` · <b class="aviso">mostrando os primeiros ${nf(candidatos.length)}</b>` : "");
    opcoes("f-cruz", b.facetas.cruz_flag, CRUZ);
    opcoes("f-categoria", b.facetas.categoria);
    opcoes("f-edificacao", b.facetas.edificacao);
    opcoes("f-conservacao", b.facetas.conservacao);
    pintarLista();
  }

  function filtrados() {
    const q = $("f-busca").value.trim().toLowerCase();
    const cruz = $("f-cruz").value, cat = $("f-categoria").value;
    const edi = $("f-edificacao").value, con = $("f-conservacao").value;
    const foto = $("f-foto").checked, sv = $("f-sv").checked, cnpj = $("f-cnpj").checked;
    const esconder = $("f-fila").checked, escJaCom = $("f-jacom").checked;
    return candidatos.filter((i) => {
      if (esconder && i.na_fila) return false;
      // Visíveis por padrão, e marcados: o admin decide caso a caso. Esconder
      // por padrão tiraria dele a decisão que ele pediu para ter.
      if (escJaCom && i.ja_comercial) return false;
      if (cruz && i.cruz_flag !== cruz) return false;
      if (cat && i.categoria !== cat) return false;
      if (edi && i.edificacao !== edi) return false;
      if (con && i.conservacao !== con) return false;
      if (foto && !i.tem_foto) return false;
      if (sv && !i.tem_sv) return false;
      if (cnpj && !i.tem_cnpj) return false;
      if (q && !`${i.nome || ""} ${i.endereco || ""}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }

  // Teto de linhas DESENHADAS. A marcação continua valendo para o filtro
  // inteiro — "marcar os filtrados" marca os 3 mil, ainda que a tela mostre
  // 400. Desenhar tudo trava o navegador e não ajuda ninguém a decidir.
  const TETO_LINHAS = 400;

  // Quantos dos marcados JÁ SÃO COMERCIAIS no cadastro. Precisa varrer o
  // conjunto todo, não só o filtro: o admin marca com um filtro e envia com
  // outro, e o aviso tem de valer para o que vai sair.
  const marcadosJaComerciais = () =>
    candidatos.filter((i) => marcados.has(i.id) && i.ja_comercial).length;

  function pintarLista() {
    const f = filtrados();
    const marcadosVisiveis = f.filter((i) => marcados.has(i.id)).length;
    const jc = marcadosJaComerciais();
    $("dist-contagem").innerHTML =
      `<b>${nf(f.length)}</b> no filtro · <b>${nf(marcados.size)}</b> marcados`
      + (marcados.size > marcadosVisiveis ? ` <span class="sessao-nota">(${nf(marcados.size - marcadosVisiveis)} fora do filtro atual)</span>` : "")
      + (jc ? ` · <b class="aviso">${nf(jc)} já comerciais no cadastro</b>` : "");
    $("dist-btn").textContent = marcados.size ? `Enviar ${nf(marcados.size)}` : "Enviar";
    $("dist-btn").disabled = !marcados.size;

    const mostra = f.slice(0, TETO_LINHAS);
    $("dist-lista").innerHTML = (mostra.length
      ? `<table class="dist-tab"><thead><tr>
           <th></th><th>Ponto</th><th>Categoria</th><th>Situação no cadastro</th>
           <th>Edificação</th><th>Evidência</th></tr></thead><tbody>${
        mostra.map((i) => `<tr class="${marcados.has(i.id) ? "marcada" : ""}${
          i.ja_comercial ? " ja-comercial" : ""}">
          <td><input type="checkbox" data-id="${i.id}" ${marcados.has(i.id) ? "checked" : ""}></td>
          <td><b>${esc(i.nome || "sem nome")}</b><small>${esc(i.endereco || "")}</small></td>
          <td>${esc(i.categoria || "—")}</td>
          <td>${i.ja_comercial
            ? `<span class="tag ja-com" title="O cliente já cobra este imóvel como comercial — mandar para a fila não gera reclassificação">⚠ JÁ COMERCIAL NO CADASTRO</span>`
            : `<span class="tag ${i.cruz_flag.startsWith("reclassificar") ? "ok" : ""}">${
                esc(CRUZ[i.cruz_flag] || i.cruz_flag)}</span>`}</td>
          <td>${esc(i.edificacao || "—")}</td>
          <td class="dist-evid">${i.tem_foto ? "📷" : ""}${i.tem_sv ? "📸" : ""}${
            i.tem_cnpj ? "🏢" : ""}${i.na_fila ? "<span title='já está na fila'>↺</span>" : ""}</td>
        </tr>`).join("")}</tbody></table>`
      : "<p class='sessao-nota'>Nenhum ponto com esses filtros.</p>")
      + (f.length > mostra.length
        ? `<p class="sessao-nota">Mostrando ${nf(mostra.length)} de ${nf(f.length)} —
           <b>Marcar os filtrados</b> marca todos os ${nf(f.length)}.</p>` : "");

    $("dist-lista").querySelectorAll("input[data-id]").forEach((c) => {
      c.onchange = () => {
        const id = Number(c.dataset.id);
        if (c.checked) marcados.add(id); else marcados.delete(id);
        c.closest("tr").classList.toggle("marcada", c.checked);
        pintarLista();
      };
    });
  }

  async function enviarDistribuicao(ev) {
    ev.preventDefault();
    const ids = [...$("dist-supers").querySelectorAll("input:checked")].map((i) => i.value);
    if (!ids.length) { msg("dist-msg", "Escolha ao menos um supervisor.", true); return; }
    const lista = [...marcados];
    if (!lista.length) { msg("dist-msg", "Marque ao menos um ponto.", true); return; }
    // O servidor recusa lote acima de 2000; avisar aqui evita perder a seleção
    // inteira num 422 depois de o admin ter passado dez minutos filtrando.
    if (lista.length > 2000) {
      msg("dist-msg", `${nf(lista.length)} marcados — o envio vai até 2.000 por vez. Refine o filtro.`, true);
      return;
    }
    // ÚLTIMA PARADA antes de ocupar o supervisor. O marcado pode ter entrado
    // por um filtro amplo, e a linha amarela some da vista quando o filtro
    // muda; aqui a conta vem na frente de quem está clicando em Enviar.
    const jc = marcadosJaComerciais();
    if (jc && !confirm(
        `${nf(jc)} dos ${nf(lista.length)} marcados JÁ SÃO COMERCIAIS no cadastro `
        + `do cliente.\n\nPara esses não há reclassificação a propor — o supervisor `
        + `vai confirmar o que a base já cobra. Enviar mesmo assim?`)) {
      return;
    }

    const r = await fetch("/api/fila/atribuir", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ poi_ids: lista, supervisor_ids: ids }),
    });
    const b = await r.json().catch(() => ({}));
    if (!r.ok) { msg("dist-msg", b.detail || "Não consegui distribuir.", true); return; }
    msg("dist-msg", `${nf(b.atribuicoes_novas)} atribuições novas` +
      (b.ja_existiam ? ` · ${nf(b.ja_existiam)} já estavam na fila` : ""));
    marcados.clear();
    // Reconsulta: o que acabou de ser enviado passa a contar como "já na fila",
    // e sem isso o admin remandaria o mesmo lote achando que não foi.
    await carregarCandidatos();
    carregar();
  }

  function msg(id, t, erro = false) {
    const p = $(id);
    p.textContent = t;
    p.classList.toggle("erro", erro);
  }

  window.abrirFila = abrir;
  document.addEventListener("DOMContentLoaded", () => { montar(); ligar(); });
})();
