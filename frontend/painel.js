// painel.js — a tela principal nova, ligada ao banco de verdade.
//
// NADA AQUI INVENTA NÚMERO. Regra do dono do produto, 28/08/2026: "apenas deixe
// sem uso aquilo que não temos ainda". O que não tem origem no banco está
// marcado `data-sem-origem` no HTML, desabilitado e com o motivo no `title` —
// aparece na tela como lacuna declarada, não como zero que parece dado.
//
// O TOKEN VEM DO `sessao.js`, que embrulha o `window.fetch`. Por isso ele carrega
// antes deste arquivo e aqui não há uma linha de autenticação.

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const nf = new Intl.NumberFormat("pt-BR");
  const INDIGO = "#4f46e5";

  const BASEMAPS = [
    ["Mapa padrão", "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"],
    ["Mapa claro", "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"],
    ["Mapa escuro", "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"],
    ["Satélite", "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
  ];

  // OS QUATRO VEREDITOS SÃO OS DA `analise_ia`, e as cores vêm do desenho.
  // Verde e lima aprovam, âmbar manda para o humano, vermelho reprova.
  const VEREDITOS = [
    ["aprovado_exato", "Aprovado, estabelecimento exato", "bg-green-700"],
    ["aprovado_comercial", "Aprovado, estabelecimentos comerciais", "bg-lime-600"],
    ["revisao_humana", "Revisão humana, provável atividade comercial", "bg-amber-500"],
    ["reprovado", "Reprovado, nenhuma atividade econômica relevante", "bg-red-700"],
  ];

  const NAV_BASE = "nav-item";
  const estado = {
    modo: null, painel: null, cidade: null, cod: null,
    desenhando: false, pts: [], temArea: false,
    basemap: 0, pois: [], stats: null, cadastro: null,
    filtros: { origem: "", atributos: [], ia: [], construcao: [] },
  };

  let mapa, camadaDesenho, camadaPois, tile;

  // ── utilidades ──────────────────────────────────────────────────────────

  async function pegar(url) {
    try {
      const r = await fetch(url);
      if (!r.ok) return null;
      return await r.json();
    } catch (e) {
      return null;                       // rede caiu: a tela segue com o que tem
    }
  }

  function classeNav(ativo) {
    return NAV_BASE + (ativo ? " nav-on" : "");
  }

  // ── mapa ────────────────────────────────────────────────────────────────

  function iniciarMapa() {
    if (!window.L || mapa) return;
    mapa = L.map("mapa", { zoomControl: true, attributionControl: false })
             .setView([-15.78, -47.93], 4.4);
    tile = L.tileLayer(BASEMAPS[0][1], { maxZoom: 19, subdomains: "abc" }).addTo(mapa);
    camadaPois = L.layerGroup().addTo(mapa);
    camadaDesenho = L.layerGroup().addTo(mapa);
    mapa.on("click", cliqueNoMapa);
    mapa.on("dblclick", concluirDesenho);
    setTimeout(() => mapa.invalidateSize(), 200);
  }

  function cliqueNoMapa(e) {
    if (!estado.desenhando) return;
    estado.pts.push([e.latlng.lat, e.latlng.lng]);
    redesenharTemporario();
    $("n-vertices").textContent = estado.pts.length + " vértices";
    const pronto = estado.pts.length >= 3;
    const b = $("btn-concluir");
    b.disabled = !pronto;
    b.className = pronto
      ? "rounded-full bg-white px-3 py-1 text-xs font-semibold text-gray-900 hover:bg-gray-100"
      : "cursor-not-allowed rounded-full bg-white/10 px-3 py-1 text-xs font-semibold text-white/40";
  }

  function redesenharTemporario() {
    camadaDesenho.clearLayers();
    const pts = estado.pts;
    if (pts.length >= 2) {
      L.polyline(pts, { color: INDIGO, weight: 2, dashArray: "6 5" }).addTo(camadaDesenho);
    }
    pts.forEach((p) => L.circleMarker(p, {
      radius: 4, color: INDIGO, fillColor: "#fff", fillOpacity: 1, weight: 2,
    }).addTo(camadaDesenho));
  }

  async function concluirDesenho() {
    if (estado.pts.length < 3) return;
    const pts = estado.pts.slice();
    camadaDesenho.clearLayers();
    const poly = L.polygon(pts, {
      color: INDIGO, weight: 2, fillColor: INDIGO, fillOpacity: 0.12,
    }).addTo(camadaDesenho);
    mapa.fitBounds(poly.getBounds(), { padding: [40, 40] });
    if (mapa.doubleClickZoom) mapa.doubleClickZoom.enable();

    // A ÁREA VAI PARA O BANCO, e não fica só na tela. É ela que o
    // `minerar_tudo` lê — desenhar sem gravar produziria uma extração da área
    // ANTERIOR, sem nada dizer.
    await fetch("/api/area", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ polygon: pts.map(([la, ln]) => [la, ln]) }),
    }).catch(() => {});

    estado.desenhando = false;
    estado.temArea = true;
    estado.pts = [];
    $("faixa-desenho").classList.add("hidden");
    $("faixa-desenho").classList.remove("flex");
    pintarEstado();
    await carregarTudo();
  }

  function desenharPois() {
    if (!camadaPois) return;
    camadaPois.clearLayers();
    const lista = poisFiltrados();
    lista.forEach((p) => {
      if (p.lat == null || p.lng == null) return;
      const cor = p.multiorigem ? "#f59e0b" : INDIGO;
      L.circleMarker([p.lat, p.lng], {
        radius: 4, color: cor, weight: 1.5,
        fillColor: cor, fillOpacity: 0.55,
      }).bindPopup(
        `<b>${escapar(p.nome || "(sem nome)")}</b><br>` +
        `<span style="color:#6b7280">${escapar(p.endereco || "sem endereço")}</span>` +
        (p.veredito ? `<br><small>${escapar(p.veredito)}</small>` : "")
      ).addTo(camadaPois);
    });
    $("job-cap").dataset.pontos = lista.length;
  }

  function escapar(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // ── filtros ─────────────────────────────────────────────────────────────

  function poisFiltrados() {
    const f = estado.filtros;
    const q = ($("busca").value || "").trim().toLowerCase();
    return estado.pois.filter((p) => {
      if (estado.cidade && (p.cidade || "").toLowerCase() !== estado.cidade.toLowerCase()) return false;
      if (f.origem && (p.fonte || "") !== f.origem) return false;
      if (f.ia.length && !f.ia.includes(p.veredito || "")) return false;
      if (f.construcao.length && !f.construcao.includes(p.tipo_construcao || "")) return false;
      for (const a of f.atributos) {
        if (a === "cnpj" && !p.tem_cnpj) return false;
        if (a === "tel" && !p.tem_tel) return false;
        if (a === "foto" && !p.tem_foto) return false;
        if (a === "sv" && !p.tem_sv) return false;
        if (a === "multi" && !p.multiorigem) return false;
      }
      if (q) {
        const alvo = ((p.nome || "") + " " + (p.endereco || "")).toLowerCase();
        if (!alvo.includes(q)) return false;
      }
      return true;
    });
  }

  function contarFiltros() {
    const f = estado.filtros;
    return (f.origem ? 1 : 0) + f.atributos.length + f.ia.length + f.construcao.length;
  }

  function montarFiltros() {
    // AS OPÇÕES SAEM DO DADO, e não de uma lista escrita à mão. O desenho traz
    // "Deliverys" e "SaaS de hospedagem"; nós temos as fontes que temos, e
    // oferecer o que não existe é pior que não oferecer.
    const fontes = [...new Set(estado.pois.map((p) => p.fonte).filter(Boolean))].sort();
    const alvoOrigem = $("f-origem");
    alvoOrigem.innerHTML = "";
    linhaRadio(alvoOrigem, "origem", "", "Todas as origens", !estado.filtros.origem, () => {
      estado.filtros.origem = ""; aposFiltro();
    });
    fontes.forEach((f) => {
      const n = estado.pois.filter((p) => p.fonte === f).length;
      linhaRadio(alvoOrigem, "origem", f, `${f} · ${nf.format(n)}`,
        estado.filtros.origem === f, () => { estado.filtros.origem = f; aposFiltro(); });
    });

    const attrs = [
      ["cnpj", "Com CNPJ"], ["tel", "Com telefone"], ["foto", "Com foto"],
      ["sv", "Com visão de fachada"], ["multi", "Multiorigem"],
    ];
    const alvoAttr = $("f-atributos");
    alvoAttr.innerHTML = "";
    attrs.forEach(([k, rot]) => linhaCheck(alvoAttr, rot,
      estado.filtros.atributos.includes(k), () => alternar("atributos", k)));

    const alvoIa = $("f-ia");
    alvoIa.innerHTML = "";
    const vistos = new Set(estado.pois.map((p) => p.veredito).filter(Boolean));
    VEREDITOS.filter((v) => vistos.has(v[0])).forEach(([k, rot, cor]) =>
      linhaCheck(alvoIa, rot, estado.filtros.ia.includes(k),
        () => alternar("ia", k), cor));
    if (!alvoIa.children.length) {
      alvoIa.innerHTML = '<p class="px-2 text-[11.5px] text-gray-400">Nenhum ponto analisado pela IA nesta seleção.</p>';
    }

    const alvoC = $("f-construcao");
    alvoC.innerHTML = "";
    const tipos = [...new Set(estado.pois.map((p) => p.tipo_construcao).filter(Boolean))].sort();
    tipos.forEach((t) => linhaCheck(alvoC, t, estado.filtros.construcao.includes(t),
      () => alternar("construcao", t)));
    if (!tipos.length) {
      alvoC.innerHTML = '<p class="px-2 text-[11.5px] text-gray-400">Nenhum tipo de construção registrado nesta seleção.</p>';
    }

    $("filtros-n").textContent = contarFiltros();
  }

  function alternar(chave, valor) {
    const l = estado.filtros[chave];
    const i = l.indexOf(valor);
    if (i >= 0) l.splice(i, 1); else l.push(valor);
    aposFiltro();
  }

  function aposFiltro() {
    $("filtros-n").textContent = contarFiltros();
    montarFiltros();
    desenharPois();
  }

  function linhaRadio(alvo, grupo, valor, rotulo, marcado, ao) {
    const l = document.createElement("label");
    l.className = "flex cursor-pointer items-center gap-x-2.5 rounded-md px-2 py-1.5 text-sm text-gray-700 hover:bg-gray-50";
    l.innerHTML = `<input type="radio" name="${grupo}" class="size-4 border-gray-300 text-indigo-600 focus:ring-2 focus:ring-indigo-500"${marcado ? " checked" : ""}><span></span>`;
    l.querySelector("span").textContent = rotulo;
    l.querySelector("input").addEventListener("change", ao);
    alvo.appendChild(l);
  }

  function linhaCheck(alvo, rotulo, marcado, ao, cor) {
    const l = document.createElement("label");
    l.className = "flex cursor-pointer items-start gap-x-2.5 rounded-md px-2 py-1.5 text-[13px]/[18px] text-gray-700 hover:bg-gray-50";
    l.innerHTML = `<input type="checkbox" class="mt-0.5 size-4 shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-2 focus:ring-indigo-500"${marcado ? " checked" : ""}>` +
      (cor ? `<span class="mt-1 size-2 shrink-0 rounded-full ${cor}"></span>` : "") + "<span></span>";
    l.querySelector("span:last-child").textContent = rotulo;
    l.querySelector("input").addEventListener("change", ao);
    alvo.appendChild(l);
  }

  // ── estatísticas ────────────────────────────────────────────────────────

  function pintarStats(s) {
    if (!s) return;
    const total = s.validos || 0;
    $("s-total").textContent = nf.format(total);
    $("m-total").textContent = nf.format(total);

    const multi = (estado.pois || []).filter((p) => p.multiorigem).length;
    $("s-multi").textContent = nf.format(multi);
    $("m-multi").textContent = nf.format(multi);
    $("s-multi-bar").style.width = (total ? Math.round((multi / total) * 100) : 0) + "%";

    pintarCadastro();

    const linhas = [
      ["Com CNPJ", s.com_cnpj], ["Com telefone", s.com_telefone],
      ["Com fachada (Street View)", s.com_streetview], ["Com fotos", s.fotos],
      ["Com comentários", s.comentarios], ["Analisados pela IA", s.analisados],
    ];
    barras($("s-atributos"), linhas, total, true);
    barras($("m-atributos"), linhas, total, false);

    const pv = s.por_veredito || {};
    const ia = VEREDITOS.map(([k, rot, cor]) => [rot, pv[k] || 0, cor])
      .filter((r) => r[1] > 0);
    barrasIa($("s-ia"), ia, total, true);
    barrasIa($("m-ia"), ia, total, false);
  }

  // "JÁ COMERCIAIS NO CADASTRO" É A ETAPA 9, e o número tem dois lados.
  //
  // `com_poi` são as ligações do cliente que casaram com um ponto — o que o
  // desenho chama de "já comerciais no cadastro". `poi_sem_ligacao` é o
  // inverso: pontos que o cadastro não conhece, e que viram a fila de
  // vinculação humana. Mostrar só o primeiro esconderia metade do trabalho.
  //
  // O endpoint LÊ, não recruza: disparar um cruzamento de 102 mil linhas para
  // pintar um cartão seria trocar leitura por trabalho pesado a cada F5.
  function pintarCadastro() {
    const c = estado.cadastro;
    const alvos = [$("s-cadastro"), $("m-cadastro")];
    if (!c) {
      alvos.forEach((a) => { a.textContent = "—"; });
      return;
    }
    alvos.forEach((a) => { a.textContent = nf.format(c.com_poi || 0); });
    const alta = (c.por_flag || {}).reclassificar_alta || 0;
    $("s-cadastro").title =
      `${nf.format(c.com_poi || 0)} ligações com ponto associado · ` +
      `${nf.format(c.poi_sem_ligacao || 0)} pontos sem ligação · ` +
      `${nf.format(alta)} para reclassificar com CNPJ conferido`;
    const cx = $("m-cadastro-detalhe");
    if (cx) {
      cx.textContent =
        `${nf.format(c.poi_sem_ligacao || 0)} pontos sem ligação · ` +
        `${nf.format(alta)} a reclassificar (CNPJ conferido)`;
    }
  }

  function barras(alvo, linhas, total, compacto) {
    alvo.innerHTML = "";
    linhas.forEach(([rot, v]) => {
      const pct = total ? Math.round(((v || 0) / total) * 100) : 0;
      const d = document.createElement("div");
      d.innerHTML =
        `<div class="flex items-start justify-between gap-x-2 text-[${compacto ? "11.5" : "13"}px]">` +
        `<dt class="min-w-0 pt-px text-gray-500"></dt>` +
        `<dd class="shrink-0 font-semibold text-gray-900"></dd></div>` +
        `<div class="relative mt-1 h-1 w-full overflow-hidden rounded-full bg-gray-100">` +
        `<div class="absolute inset-y-0 left-0 rounded-full bg-indigo-500" style="width:${pct}%"></div></div>`;
      d.querySelector("dt").textContent = rot;
      d.querySelector("dd").textContent = nf.format(v || 0);
      alvo.appendChild(d);
    });
  }

  function barrasIa(alvo, linhas, total, compacto) {
    alvo.innerHTML = "";
    if (!linhas.length) {
      alvo.innerHTML = '<p class="text-[11.5px] text-gray-400">Nenhum ponto analisado pela IA nesta seleção.</p>';
      return;
    }
    linhas.forEach(([rot, v, cor]) => {
      const pct = total ? Math.round((v / total) * 100) : 0;
      const d = document.createElement("div");
      d.innerHTML =
        `<div class="flex items-start justify-between gap-x-2 text-[${compacto ? "11.5" : "13"}px]">` +
        `<dt class="flex min-w-0 items-start gap-x-1.5 text-gray-500">` +
        `<span class="mt-1 size-1.5 shrink-0 rounded-full ${cor}"></span><span></span></dt>` +
        `<dd class="shrink-0 font-semibold text-gray-900"></dd></div>` +
        `<div class="relative mt-1 h-1 w-full overflow-hidden rounded-full bg-gray-100">` +
        `<div class="absolute inset-y-0 left-0 rounded-full bg-indigo-500" style="width:${pct}%"></div></div>`;
      d.querySelector("dt span:last-child").textContent = rot;
      d.querySelector("dd").textContent = nf.format(v);
      alvo.appendChild(d);
    });
  }

  // ── municípios ──────────────────────────────────────────────────────────

  let municipios = [];

  async function carregarMunicipios() {
    if (municipios.length) return;
    const ufs = await pegar("/api/ufs") || [];
    for (const u of ufs) {
      const lista = await pegar("/api/municipios?uf=" + encodeURIComponent(u.uf)) || [];
      lista.forEach((m) => municipios.push({ ...m, uf: u.uf }));
    }
    pintarMunicipios();
  }

  function pintarMunicipios() {
    const q = ($("busca-municipio").value || "").trim().toLowerCase();
    const alvo = $("lista-municipios");
    alvo.innerHTML = "";
    const filtrados = municipios.filter((m) =>
      !q || m.nome.toLowerCase().includes(q) || m.uf.toLowerCase().includes(q));
    filtrados.slice(0, 400).forEach((m) => {
      const b = document.createElement("button");
      const ativo = estado.cod === m.cod;
      b.type = "button";
      b.className = "flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm font-medium " +
        (ativo ? "bg-indigo-50 text-indigo-600" : "text-gray-700 hover:bg-gray-50");
      b.innerHTML = '<span></span><span class="text-xs font-medium text-gray-400"></span>';
      b.querySelector("span").textContent = m.nome;
      b.querySelector("span:last-child").textContent = m.uf;
      b.addEventListener("click", () => escolherMunicipio(m));
      alvo.appendChild(b);
    });
    if (!filtrados.length) {
      alvo.innerHTML = '<p class="px-3 py-2 text-[12px] text-gray-400">Nenhum município com esse texto.</p>';
    }
  }

  async function escolherMunicipio(m) {
    estado.cidade = m.nome;
    estado.cod = m.cod;
    estado.temArea = false;
    camadaDesenho.clearLayers();

    // A ÁREA VIRA O MUNICÍPIO NO BANCO. O `minerar_tudo` lê a mesma área
    // desenhada — sem isto, escolher município na tela não mudaria o que a
    // extração faz.
    await fetch("/api/area/municipio", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cod: m.cod }),
    }).catch(() => {});

    const malha = await pegar("/api/malha?cod=" + encodeURIComponent(m.cod));
    if (malha && malha.polygon && malha.polygon.length) {
      const poly = L.polygon(malha.polygon, {
        color: INDIGO, weight: 2, fillColor: INDIGO, fillOpacity: 0.1,
      }).addTo(camadaDesenho);
      mapa.fitBounds(poly.getBounds(), { padding: [30, 30] });
    }
    pintarMunicipios();
    pintarEstado();
    await carregarTudo();
  }

  // ── estado da tela ──────────────────────────────────────────────────────

  function pintarEstado() {
    const pronto = estado.temArea || !!estado.cidade;
    $("status-dot").className = "size-2 rounded-full " + (pronto ? "bg-green-600" : "bg-gray-400");
    $("status-txt").textContent = estado.cidade
      ? "Município selecionado — " + estado.cidade
      : (estado.temArea ? "Área desenhada no mapa" : "Nenhuma área de interesse definida");

    $("dica-modo").textContent = estado.modo === "desenho"
      ? "Modo desenho de área ativo"
      : (estado.modo === "municipio"
        ? (estado.cidade ? "Município: " + estado.cidade : "Escolha um município na lista")
        : "Escolha um dos dois modos");

    const ex = $("btn-extrair");
    ex.disabled = !pronto;
    ex.className = classeNav(false) + (pronto ? " mt-1 nav-pronto" : " mt-1 nav-off");

    const lim = $("btn-limpar");
    lim.disabled = !pronto;
    lim.className = pronto
      ? "inline-flex flex-1 items-center justify-center gap-x-1.5 rounded-md border border-gray-300 bg-white px-2 py-1.5 text-[11.5px] font-semibold text-gray-700 shadow-sm hover:bg-gray-50"
      : "inline-flex flex-1 cursor-not-allowed items-center justify-center gap-x-1.5 rounded-md border border-gray-200 bg-white px-2 py-1.5 text-[11.5px] font-semibold text-gray-300";

    const escopo = pronto
      ? "Totais restritos à área de interesse selecionada."
      : "Sem área selecionada: exibindo o total disponível na base.";
    $("escopo").textContent = escopo;
    $("escopo-modal").textContent = escopo;

    $("btn-desenhar").className = classeNav(estado.modo === "desenho");
    $("btn-municipio").className = classeNav(estado.modo === "municipio");
    $("btn-filtros").className = classeNav(estado.painel === "filtros");
  }

  function abrirPainel(qual) {
    estado.painel = estado.painel === qual ? null : qual;
    for (const [id, nome] of [["p-municipios", "municipios"], ["p-filtros", "filtros"]]) {
      const el = $(id);
      const on = estado.painel === nome;
      el.classList.toggle("hidden", !on);
      el.classList.toggle("flex", on);
    }
    if (mapa) setTimeout(() => mapa.invalidateSize(), 60);
    pintarEstado();
  }

  // ── job e log ───────────────────────────────────────────────────────────

  function pintarJob(j) {
    if (!j) return;
    const rodando = j.status === "rodando";
    $("job-titulo").textContent = rodando
      ? (j.fase_rotulo || "Extraindo dados…")
      : ({ ocioso: "Nenhum processo em andamento", finalizado: "Última extração concluída",
           parado: "Extração interrompida", erro: "A extração terminou com erro" }[j.status] || j.status);
    const pct = Number(j.pct || 0);
    $("job-pct").textContent = rodando && pct ? pct + "%" : "";
    $("job-bar").style.width = (rodando ? pct : 0) + "%";
    if (j.mensagem) $("job-cap").textContent = j.mensagem;
  }

  function linhaLog(texto, classe) {
    const d = document.createElement("div");
    d.className = "flex gap-x-2";
    const h = new Date().toLocaleTimeString("pt-BR", { hour12: false });
    d.innerHTML = '<span class="shrink-0 text-gray-600"></span><span class="min-w-0 break-words"></span>';
    d.querySelector("span").textContent = h;
    const alvo = d.querySelector("span:last-child");
    alvo.textContent = texto;
    alvo.className = "min-w-0 break-words " + (classe || "text-gray-300");
    const log = $("log");
    log.appendChild(d);
    while (log.children.length > 400) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
  }

  // O WEBSOCKET RECUA EM VEZ DE INSISTIR NO MESMO RITMO.
  //
  // A primeira versão reabria a cada 4 s, para sempre. Sem sessão o handshake é
  // recusado sempre, e o resultado era uma tentativa a cada 4 segundos pela
  // vida inteira da aba — o console enche, o servidor leva um pedido inútil por
  // tentativa, e nada disso indica o problema real, que é a falta de token.
  //
  // Agora o intervalo dobra a cada falha até 60 s, e volta a 2 s assim que uma
  // conexão abre. Falha que se repete espaça; falha que passou não deixa
  // rastro.
  function ligarWebsocket() {
    let espera = 2000;
    const abrir = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const t = (window.comToken ? window.comToken("/ws") : "/ws");
      let ws;
      try {
        ws = new WebSocket(proto + "://" + location.host + t);
      } catch (e) {
        setTimeout(abrir, espera = Math.min(espera * 2, 60000));
        return;
      }
      ws.onopen = () => { espera = 2000; };
      ws.onmessage = (ev) => {
        let m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m.tipo === "job") pintarJob(m.dados);
        else if (m.tipo === "log" && m.dados) {
          linhaLog(String(m.dados.texto || m.dados), m.dados.classe);
        } else if (m.tipo === "poi") {
          carregarPois();
        }
      };
      ws.onclose = () => {
        setTimeout(abrir, espera);
        espera = Math.min(espera * 2, 60000);
      };
    };
    abrir();
  }

  // ── carga ───────────────────────────────────────────────────────────────

  async function carregarPois() {
    const d = await pegar("/api/pois");
    estado.pois = (d && d.pois) || [];
    desenharPois();
    montarFiltros();
  }

  async function carregarStats() {
    const q = estado.cidade ? "?cidade=" + encodeURIComponent(estado.cidade) : "";
    estado.stats = await pegar("/api/stats" + q);
    estado.cadastro = await pegar("/api/cadastro/resumo" + q);
    pintarStats(estado.stats);
  }

  async function carregarTudo() {
    await carregarPois();
    await carregarStats();
  }

  async function carregarCracha() {
    const eu = await pegar("/api/eu");
    if (!eu) return;
    $("perfil-nome-topo").textContent = eu.nome || "";
    $("marca-empresa").textContent = eu.empresa || "";
    const ini = (eu.nome || "··").split(" ").filter(Boolean)
      .map((p) => p[0]).slice(0, 2).join("").toUpperCase();
    $("perfil-iniciais").textContent = ini || "··";
  }

  // ── ligações da interface ───────────────────────────────────────────────

  function ligar() {
    $("btn-desenhar").addEventListener("click", () => {
      estado.modo = "desenho";
      estado.painel = null;
      estado.cidade = null; estado.cod = null;
      estado.temArea = false; estado.pts = [];
      estado.desenhando = true;
      camadaDesenho.clearLayers();
      if (mapa.doubleClickZoom) mapa.doubleClickZoom.disable();
      const f = $("faixa-desenho");
      f.classList.remove("hidden"); f.classList.add("flex");
      $("n-vertices").textContent = "0 vértices";
      abrirPainel(null);
    });

    $("btn-cancelar-desenho").addEventListener("click", () => {
      estado.desenhando = false; estado.pts = [];
      camadaDesenho.clearLayers();
      if (mapa.doubleClickZoom) mapa.doubleClickZoom.enable();
      const f = $("faixa-desenho");
      f.classList.add("hidden"); f.classList.remove("flex");
      pintarEstado();
    });

    $("btn-concluir").addEventListener("click", concluirDesenho);

    $("btn-municipio").addEventListener("click", () => {
      estado.modo = "municipio";
      estado.desenhando = false; estado.pts = [];
      const f = $("faixa-desenho");
      f.classList.add("hidden"); f.classList.remove("flex");
      abrirPainel("municipios");
      carregarMunicipios();
    });

    $("btn-filtros").addEventListener("click", () => abrirPainel("filtros"));
    document.querySelectorAll("[data-fechar]").forEach((b) =>
      b.addEventListener("click", () => abrirPainel(null)));

    $("busca-municipio").addEventListener("input", pintarMunicipios);
    $("busca").addEventListener("input", desenharPois);

    $("btn-limpar").addEventListener("click", async () => {
      estado.modo = null; estado.cidade = null; estado.cod = null;
      estado.temArea = false; estado.desenhando = false; estado.pts = [];
      camadaDesenho.clearLayers();
      abrirPainel(null);
      pintarEstado();
      await carregarTudo();
    });

    $("btn-limpar-filtros").addEventListener("click", () => {
      estado.filtros = { origem: "", atributos: [], ia: [], construcao: [] };
      aposFiltro();
    });

    $("btn-extrair").addEventListener("click", async () => {
      if ($("btn-extrair").disabled) return;
      const r = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ modo: "mineracao", opcoes: { sessao: "painel" } }),
      }).catch(() => null);
      if (!r) return;
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        linhaLog(d.erro || "não foi possível iniciar a extração", "text-amber-400");
        $("log-wrap").classList.remove("hidden");
        return;
      }
      $("log-wrap").classList.remove("hidden");
      linhaLog("extração iniciada", "text-lime-400");
    });

    $("btn-log").addEventListener("click", () => {
      const w = $("log-wrap");
      const aberto = !w.classList.contains("hidden");
      w.classList.toggle("hidden", aberto);
      $("log-chevron").className = "size-4 text-gray-400" + (aberto ? " rotate-180" : "");
    });

    // basemap
    const menu = $("menu-basemap");
    BASEMAPS.forEach(([rot, url], i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "flex w-full items-center justify-between px-3 py-1 text-left text-sm/6 hover:bg-gray-50 " +
        (i === 0 ? "bg-indigo-50 text-indigo-600" : "text-gray-900");
      b.innerHTML = '<span></span><span class="text-xs font-semibold text-indigo-600"></span>';
      b.querySelector("span").textContent = rot;
      b.querySelector("span:last-child").textContent = i === 0 ? "✓" : "";
      b.addEventListener("click", () => {
        estado.basemap = i;
        tile.setUrl(url);
        $("basemap-nome").textContent = rot;
        menu.classList.add("hidden");
        [...menu.children].forEach((c, k) => {
          c.className = "flex w-full items-center justify-between px-3 py-1 text-left text-sm/6 hover:bg-gray-50 " +
            (k === i ? "bg-indigo-50 text-indigo-600" : "text-gray-900");
          c.querySelector("span:last-child").textContent = k === i ? "✓" : "";
        });
      });
      menu.appendChild(b);
    });
    $("btn-basemap").addEventListener("click", () => {
      menu.classList.toggle("hidden");
      $("menu-perfil").classList.add("hidden");
    });

    $("btn-perfil-menu").addEventListener("click", () => {
      $("menu-perfil").classList.toggle("hidden");
      menu.classList.add("hidden");
    });
    $("btn-sair").addEventListener("click", () => {
      try { localStorage.removeItem("cr_sessao"); } catch (e) {}
      location.reload();
    });

    $("btn-expandir").addEventListener("click", () => {
      const m = $("m-stats");
      m.classList.remove("hidden"); m.classList.add("flex");
    });
    document.querySelectorAll("[data-fechar-modal]").forEach((b) =>
      b.addEventListener("click", () => {
        const m = $("m-stats");
        m.classList.add("hidden"); m.classList.remove("flex");
      }));

    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      $("menu-basemap").classList.add("hidden");
      $("menu-perfil").classList.add("hidden");
      const m = $("m-stats");
      m.classList.add("hidden"); m.classList.remove("flex");
    });
  }

  // ── partida ─────────────────────────────────────────────────────────────

  async function iniciar() {
    iniciarMapa();
    if (!mapa) { setTimeout(iniciar, 150); return; }   // Leaflet ainda subindo
    ligar();
    pintarEstado();
    await carregarCracha();

    const area = await pegar("/api/area");
    if (area && area.polygon && area.polygon.length >= 3) {
      estado.temArea = true;
      const poly = L.polygon(area.polygon, {
        color: INDIGO, weight: 2, fillColor: INDIGO, fillOpacity: 0.12,
      }).addTo(camadaDesenho);
      mapa.fitBounds(poly.getBounds(), { padding: [40, 40] });
      pintarEstado();
    }

    await carregarTudo();
    pintarJob(await pegar("/api/jobs/atual"));
    ligarWebsocket();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();
