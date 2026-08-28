/* sessao.js — login, crachá e perfil.
 *
 * Carrega ANTES do app.js e faz três coisas:
 *
 *  1. Envolve o `fetch` global. Toda chamada a /api/* passa a levar o token sem
 *     que as 2.758 linhas do app.js precisem saber que autenticação existe. Foi
 *     a forma de ligar sessão sem reescrever cada chamada — e sem deixar
 *     metade delas para trás, que é como buraco de autorização nasce.
 *  2. Bloqueia a tela até haver sessão.
 *  3. Guarda o crachá em `window.EU` para o resto do app decidir o que mostrar.
 *
 * O token vive em `sessionStorage`, não em `localStorage`: fechou a aba, acabou
 * a sessão. Em máquina compartilhada — que é o caso de campo — credencial que
 * sobrevive ao fechamento do navegador é credencial emprestada sem querer.
 */
(() => {
  const CHAVE = "cr_token";
  const CHAVE_REF = "cr_refresh";
  const CHAVE_EXP = "cr_expira";
  const fetchOriginal = window.fetch.bind(window);

  // O LOGIN TRAZ O PRÓPRIO ESTILO, e isto é conserto de 28/08/2026.
  //
  // Este arquivo monta a tela de acesso, e qualquer página do sistema pode
  // incluí-lo. Só que o estilo dela morava no `style.css`, que apenas a tela
  // ANTIGA carrega — então na tela nova o "Sair" funcionava, o login aparecia,
  // e aparecia CRU: empilhado no rodapé, sem cobrir nada, com o painel ainda
  // visível atrás. Quem monta a interface tem de garantir o que ela precisa
  // para existir.
  //
  // `tokens.css` é escopado em `.cr` e `acesso.css` é todo prefixado `.cr-`:
  // nada vaza para a página hospedeira, o que os deixa conviver com o Tailwind
  // da tela nova sem briga de cascata.
  for (const arq of ["tokens.css", "acesso.css"]) {
    if (document.querySelector(`link[href*="${arq}"]`)) continue;
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = "/static/" + arq;
    document.head.appendChild(l);
  }

  const token = () => sessionStorage.getItem(CHAVE) || "";
  /* O LOGIN JÁ DEVOLVIA `refresh_token` e `expira_em`, e nós jogávamos fora.
   *
   * O token de acesso dura uma hora. Guardando só ele, ao completar a hora
   * qualquer chamada tomava 401 e a tela caía no login — no meio de uma carga
   * de 16 mil fachadas, o que fazia a pessoa perder de vista um processo que
   * seguia vivo no servidor. Sessão não pode morrer por decurso de prazo
   * enquanto alguém está usando o sistema. */
  const guardar = (d) => {
    sessionStorage.setItem(CHAVE, d.access_token || "");
    if (d.refresh_token) sessionStorage.setItem(CHAVE_REF, d.refresh_token);
    // Renova com folga: um minuto antes do vencimento, para nunca existir a
    // janela em que a requisição sai com token já morto.
    const seg = Number(d.expira_em || 3600);
    sessionStorage.setItem(CHAVE_EXP, String(Date.now() + Math.max(60, seg - 60) * 1000));
  };
  const limpar = () => [CHAVE, CHAVE_REF, CHAVE_EXP]
    .forEach((k) => sessionStorage.removeItem(k));

  /* Uma renovação por vez. Sem esta trava, as oito chamadas que o painel dispara
   * junto ao abrir uma tela renovariam oito vezes em paralelo, e o GoTrue
   * invalida o refresh anterior a cada uso: as sete últimas voltariam 401 e
   * derrubariam a sessão que a primeira acabou de salvar. */
  let renovando = null;

  async function renovar() {
    const ref = sessionStorage.getItem(CHAVE_REF);
    if (!ref) return false;
    if (!renovando) {
      renovando = (async () => {
        try {
          const r = await fetchOriginal("/api/renovar", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh_token: ref }),
          });
          if (!r.ok) return false;
          guardar(await r.json());
          return true;
        } catch { return false; } finally { renovando = null; }
      })();
    }
    return renovando;
  }

  const vencendo = () => Number(sessionStorage.getItem(CHAVE_EXP) || 0) <= Date.now();

  /* Publicado porque o WEBSOCKET não passa pelo `window.fetch`: ele lê o token
     do sessionStorage direto, já que handshake de WebSocket não aceita
     cabeçalho. Sem isto, ao vencer a hora o soquete caía e o laço de reconexão
     reapresentava o MESMO token morto a cada 2,5 s, para sempre — o processo
     seguia rodando no servidor e o painel ficava mudo, que foi exatamente o
     que aconteceu na carga das 20:44. */
  window.crRenovar = renovar;
  window.crVencendo = vencendo;

  /* URL DE IMAGEM COM O TOKEN NA QUERY.
   *
   * `<img src>` não manda cabeçalho — não existe API para isso. Desde que o
   * portão entrou, toda fachada do modal do mapa vinha 401 e o navegador
   * desenhava imagem quebrada; a foto de perfil caía para as iniciais e
   * parecia escolha de design. O servidor aceita `?token=` só nestas rotas.
   *
   * Publicado em `window.comToken` porque quem monta as imagens é o app.js, e
   * o token mora aqui — atravessar isso por variável global seria espalhar
   * credencial por mais um lugar. */
  window.comToken = (url) => {
    const t = token();
    if (!t) return url;
    return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(t);
  };

  window.fetch = async (entrada, init = {}) => {
    const url = typeof entrada === "string" ? entrada : (entrada && entrada.url) || "";
    const nossa = url.startsWith("/api/") || url.includes("127.0.0.1:8765/api/");
    const autentica = url.startsWith("/api/login") || url.startsWith("/api/renovar");
    // Renova ANTES de sair, quando o prazo está vencendo. Esperar o 401 para
    // reagir significa que a primeira chamada de cada hora falha — e algumas
    // delas (o WebSocket do progresso) não são repetidas por ninguém.
    if (nossa && token() && !autentica && vencendo()) await renovar();
    const comCracha = (i) => (nossa && token() && !autentica
      ? { ...i, headers: { ...(i.headers || {}), Authorization: `Bearer ${token()}` } }
      : i);
    let r = await fetchOriginal(entrada, comCracha(init));
    // 401 ainda acontece — relógio fora de hora, servidor reiniciado, token
    // revogado. Tenta renovar UMA vez e repetir; só então desiste.
    if (nossa && r.status === 401 && token() && !autentica) {
      if (await renovar()) {
        r = await fetchOriginal(entrada, comCracha(init));
      }
      if (r.status === 401) {
        limpar();
        mostrarLogin("Sua sessão expirou. Entre de novo.");
      }
    }
    return r;
  };

  // ── telas ───────────────────────────────────────────────────────────
  function montar() {
    const d = document.createElement("div");
    d.innerHTML = `
      <!-- ACESSO. Estrutura do PNG "pagina de acesso": formulario a esquerda,
           imagem a direita. Cor e tipografia do painel Radar_Comercial.
           NAO ha "criar conta" nem entrar com Google: cadastro publico e
           fechado, so o nivel mais alto cria usuario. Oferecer o que nao
           existe e pior que nao oferecer nada.
           (Sem crase neste comentario: ele vive dentro de um template literal,
           e a crase fecharia a string.) -->
      <div id="login-tela" class="cr cr-acesso hidden">
        <div class="cr-acesso-form">
          <form id="login-form">
            <div class="cr-acesso-marca">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none"
                   stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none"/>
                <path d="M12 12 L18.5 5.5"/><path d="M7.2 7.2a6.8 6.8 0 1 0 9.6 0"/>
                <path d="M4.4 4.4a10.7 10.7 0 1 0 15.2 0"/>
              </svg>
              <span>Comercial<b>Radar</b></span>
            </div>

            <h1>Entrar na sua conta</h1>
            <p class="cr-acesso-sub">O acesso e criado pela sua empresa. Se voce
              ainda nao tem, fale com quem administra.</p>

            <p id="login-msg" class="cr-erro" role="alert" aria-live="polite"></p>

            <div class="cr-campo">
              <label for="login-email">E-mail</label>
              <input id="login-email" type="email" autocomplete="username"
                     required placeholder="voce@empresa.com.br">
            </div>
            <div class="cr-campo">
              <label for="login-senha">Senha</label>
              <input id="login-senha" type="password"
                     autocomplete="current-password" required>
            </div>

            <button type="submit" id="login-btn"
                    class="cr-btn cr-btn--primario cr-acesso-btn">Entrar</button>
          </form>
        </div>
        <!-- A imagem e decorativa: aria-hidden para o leitor de tela nao
             anunciar uma arte que nao carrega informacao. -->
        <div class="cr-acesso-arte" aria-hidden="true"></div>
      </div>
      <!-- PERFIL. Estrutura do PNG "forms gerais e de perfil": secao explicada
           a esquerda, cartao de campos a direita, acoes no rodape do cartao.
           Os dois blocos existem porque tratam de coisas diferentes — o que a
           pessoa edita, e o que so o root muda. Misturar num monte de campos
           iguais e o que fazia alguem tentar editar o proprio nivel. -->
      <div id="perfil-tela" class="sessao-bg hidden">
        <form id="perfil-form" class="sessao-cartao sessao-cartao--largo">
          <div class="perfil-secao">
            <div class="perfil-secao-texto">
              <h2>Meu perfil</h2>
              <p>Nome, foto e contato. E o que aparece para o resto da
                 sua equipe.</p>
            </div>
            <div class="perfil-secao-campos">
              <div class="perfil-foto-linha">
                <img id="perfil-foto" alt="Foto de perfil" src="">
                <div>
                  <input id="perfil-arquivo" type="file"
                         accept="image/jpeg,image/png,image/webp" hidden>
                  <button type="button" id="perfil-trocar-foto">Trocar foto</button>
                  <small>JPEG, PNG ou WebP, ate 4 MB</small>
                </div>
              </div>
              <label>Nome<input id="perfil-nome" maxlength="120"></label>
              <label>Cargo<input id="perfil-cargo" maxlength="80"></label>
              <label>Telefone<input id="perfil-telefone" maxlength="32"></label>
            </div>
          </div>

          <div class="perfil-secao">
            <div class="perfil-secao-texto">
              <h2>Conta</h2>
              <p>Nao se edita aqui: e-mail e credencial, e empresa e nivel quem
                 define e o root.</p>
            </div>
            <div class="perfil-secao-campos">
              <label>E-mail<input id="perfil-email" disabled></label>
              <label>Empresa<input id="perfil-empresa" disabled></label>
              <label>Nivel<input id="perfil-nivel" disabled></label>
            </div>
          </div>

          <p id="perfil-msg" class="sessao-msg"></p>
          <div class="sessao-acoes">
            <button type="button" id="perfil-fila" class="hidden">Fila</button>
            <button type="button" id="perfil-admin" class="hidden">Administrar</button>
            <button type="button" id="perfil-sair" class="sair">Sair</button>
            <span class="sessao-acoes-vao"></span>
            <button type="button" id="perfil-fechar">Fechar</button>
            <button type="submit">Salvar</button>
          </div>
        </form>
      </div>`;
    document.body.appendChild(d);
  }

  const $ = (id) => document.getElementById(id);
  const mostrar = (id) => $(id).classList.remove("hidden");
  const esconder = (id) => $(id).classList.add("hidden");

  function mostrarLogin(msg = "") {
    $("login-msg").textContent = msg;
    $("login-msg").classList.toggle("erro", !!msg);
    mostrar("login-tela");
    esconder("perfil-tela");
    setTimeout(() => $("login-email").focus(), 50);
  }

  async function carregarCracha() {
    const r = await fetch("/api/eu");
    if (!r.ok) return null;
    const eu = await r.json();
    window.EU = eu;
    document.body.dataset.nivel = eu.nivel;
    // A procedência do dado só aparece para root. A regra vive aqui, num lugar
    // só, para não virar `if` espalhado por 20 telas.
    document.body.classList.toggle("ocultar-fontes", !eu.fontes_visiveis);
    pintarBotao(eu);
    pintarMarcaCliente(eu);
    // Avisa quem depende de sessão. O WebSocket do app.js escuta isto: sem o
    // aviso ele ficava tentando conectar a cada 2,5 s antes do login e enchendo
    // o servidor de 403.
    document.dispatchEvent(new CustomEvent("cr:sessao", { detail: eu }));
    return eu;
  }

  /* A marca do cliente no terceiro espaco do cabecalho (ADR 0005).
     Sem logo, mostra o nome em texto — ausencia de imagem nao pode virar
     espaco quebrado. Sem empresa (o `root` global), o bloco nem aparece. */
  function pintarMarcaCliente(eu) {
    const caixa = $("brand-cliente");
    if (!caixa) return;
    if (!eu.empresa) { caixa.classList.add("hidden"); return; }
    caixa.classList.remove("hidden");
    $("brand-empresa").textContent = eu.empresa;
    const img = $("brand-logo");
    if (eu.empresa_logo) {
      img.src = window.comToken(`/api/empresa/logo?t=${Date.now()}`);
      img.alt = eu.empresa;
      img.classList.remove("hidden");
      /* Logo que nao carrega volta ao nome em texto, em vez de deixar o icone
         quebrado do navegador no cabecalho do sistema. */
      img.onerror = () => { img.classList.add("hidden"); };
      $("brand-empresa").classList.add("hidden");
      img.onload = () => { $("brand-empresa").classList.add("hidden"); };
    } else {
      img.classList.add("hidden");
      $("brand-empresa").classList.remove("hidden");
    }
  }

  function pintarBotao(eu) {
    let b = $("btn-perfil");
    if (!b) {
      b = document.createElement("button");
      b.id = "btn-perfil";
      b.title = "Meu perfil";
      (document.getElementById("topbar") || document.body).appendChild(b);
      b.onclick = abrirPerfil;
    }
    const iniciais = (eu.nome || eu.email || "?").trim().slice(0, 1).toUpperCase();
    b.innerHTML = eu.tem_foto
      ? `<img src="${window.comToken(`/api/eu/foto?t=${Date.now()}`)}" alt="">`
      : `<span>${iniciais}</span>`;
  }

  async function abrirPerfil() {
    const eu = window.EU || (await carregarCracha());
    if (!eu) return;
    $("perfil-nome").value = eu.nome || "";
    $("perfil-cargo").value = eu.cargo || "";
    $("perfil-telefone").value = eu.telefone || "";
    $("perfil-email").value = eu.email || "";
    $("perfil-empresa").value = eu.empresa || "(todas — root)";
    $("perfil-nivel").value = eu.nivel || "";
    $("perfil-foto").src = eu.tem_foto
      ? window.comToken(`/api/eu/foto?t=${Date.now()}`)
      : "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' fill='%23e2e8f0'/%3E%3C/svg%3E";
    $("perfil-msg").textContent = "";
    // Administrar aparece de admin para cima. Esconder é cortesia: quem chamar
    // /api/usuarios sem nível leva 403 do servidor de qualquer forma.
    const podeAdmin = ["admin", "root"].includes(eu.nivel);
    $("perfil-admin").classList.toggle("hidden", !podeAdmin);
    $("perfil-admin").onclick = () => {
      esconder("perfil-tela");
      if (window.abrirAdmin) window.abrirAdmin();
    };
    // A fila aparece de supervisor para cima: `user` não decide nada.
    const podeFila = ["supervisor", "admin", "root"].includes(eu.nivel);
    $("perfil-fila").classList.toggle("hidden", !podeFila);
    $("perfil-fila").onclick = () => {
      esconder("perfil-tela");
      if (window.abrirFila) window.abrirFila();
    };
    mostrar("perfil-tela");
  }

  function ligar() {
    $("login-form").onsubmit = async (ev) => {
      ev.preventDefault();
      const btn = $("login-btn");
      btn.disabled = true;
      btn.textContent = "Entrando…";
      try {
        const r = await fetchOriginal("/api/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: $("login-email").value, senha: $("login-senha").value }),
        });
        if (!r.ok) {
          const b = await r.json().catch(() => ({}));
          mostrarLogin(b.detail || "E-mail ou senha inválidos.");
          return;
        }
        guardar(await r.json());
        const eu = await carregarCracha();
        if (!eu) { limpar(); mostrarLogin("Conta sem vínculo com empresa. Fale com o root."); return; }
        $("login-senha").value = "";
        // RECARREGA em vez de só esconder a tela.
        //
        // O `app.js` dispara todas as suas buscas — POIs, malha, UFs, chave do
        // mapa — durante o carregamento da página, que acontece ANTES de existir
        // sessão. Todas tomam 401. Esconder o login depois disso deixava a
        // pessoa dentro de um sistema vazio: mapa sem POI, contadores em zero e
        // "window.google not found", porque nem a chave do mapa tinha chegado.
        // Com o token já em sessionStorage, o reload refaz tudo autenticado.
        location.reload();
        return;
      } finally {
        btn.disabled = false;
        btn.textContent = "Entrar";
      }
    };

    $("perfil-fechar").onclick = () => esconder("perfil-tela");
    $("perfil-sair").onclick = () => { limpar(); window.EU = null; location.reload(); };

    $("perfil-trocar-foto").onclick = () => $("perfil-arquivo").click();
    $("perfil-arquivo").onchange = async (ev) => {
      const f = ev.target.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("arquivo", f);
      const r = await fetch("/api/eu/foto", { method: "POST", body: fd });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) { msgPerfil(b.detail || "Não consegui enviar a foto.", true); return; }
      msgPerfil("Foto atualizada.");
      window.EU.tem_foto = true;
      $("perfil-foto").src = `/api/eu/foto?t=${Date.now()}`;
      pintarBotao(window.EU);
    };

    $("perfil-form").onsubmit = async (ev) => {
      ev.preventDefault();
      const r = await fetch("/api/eu", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nome: $("perfil-nome").value.trim() || null,
          cargo: $("perfil-cargo").value.trim() || null,
          telefone: $("perfil-telefone").value.trim() || null,
        }),
      });
      if (!r.ok) { msgPerfil("Não consegui salvar.", true); return; }
      Object.assign(window.EU, await r.json());
      pintarBotao(window.EU);
      msgPerfil("Salvo.");
    };
  }

  function msgPerfil(t, erro = false) {
    const p = $("perfil-msg");
    p.textContent = t;
    p.classList.toggle("erro", erro);
  }

  window.mostrarLogin = mostrarLogin;

  document.addEventListener("DOMContentLoaded", async () => {
    montar();
    ligar();
    if (!token() || !(await carregarCracha())) mostrarLogin();
  });
})();
