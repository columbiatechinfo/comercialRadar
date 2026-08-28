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

  // O FUNDO É O GOOGLE DE VERDADE, pela Maps JavaScript API — e a máquina é a
  // MESMA da tela anterior, DE PROPÓSITO.
  //
  // Eu havia reescrito esta parte do zero, e com isso reintroduzi defeitos que
  // o `app.js` já tinha resolvido e documentado. O pior: cada base era uma
  // FÁBRICA chamada a cada troca, então clicar em "Satélite" instanciava um
  // `google.maps.Map` novo e largava o anterior. Aqui cada base sabe se criar
  // UMA vez e guarda a camada pronta em `.layer`, como sempre foi.
  //
  // A escolha persiste no `localStorage`: o operador que trabalha em satélite
  // não quer reescolher a cada carregamento.
  //
  // Sem chave, `_mut()` devolve o Carto e o mapa continua utilizável.
  const _carto = (estilo) => L.tileLayer(
    `https://{s}.basemaps.cartocdn.com/${estilo}/{z}/{x}/{y}{r}.png`,
    { maxZoom: 20, subdomains: "abcd" });

  const _mut = (tipo) => (window.L && L.gridLayer && L.gridLayer.googleMutant)
    ? L.gridLayer.googleMutant({ type: tipo, maxZoom: 22 })
    : _carto("light_all");

  const BASES = [
    { chave: "limpo",    nome: "Mapa padrão",     google: true, criar: () => _mut("roadmap") },
    { chave: "satelite", nome: "Satélite",        google: true, criar: () => _mut("satellite") },
    { chave: "hibrido",  nome: "Satélite + ruas", google: true, criar: () => _mut("hybrid") },
    { chave: "claro",    nome: "Mapa claro (sem Google)", criar: () => _carto("light_all") },
    { chave: "escuro",   nome: "Escuro",                  criar: () => _carto("dark_all") },
  ];

  function camadaBase(i) {
    const b = BASES[i];
    if (!b.layer) b.layer = b.criar();
    return b.layer;
  }

  function baseSalva() {
    try {
      const k = localStorage.getItem("cr_base");
      const i = BASES.findIndex((b) => b.chave === k);
      return i < 0 ? 0 : i;
    } catch (e) {
      return 0;                          // navegador sem storage: volta ao padrão
    }
  }

  // A COR DO PONTO DIZ O QUE FAZER COM ELE, e vem do cruzamento com o cadastro.
  //
  // Regra do dono do produto, 28/08/2026: comercial na base do cliente fica
  // AMARELO — já está lá, não há o que reclassificar. Habitacional na base e
  // comércio achado no local fica VERDE e em destaque: é exatamente o achado
  // que o produto existe para encontrar, e a confiança gradua pelo CNPJ.
  const CORES = {
    ja_cadastrado: { cor: "#f59e0b", anel: "#b45309", destaque: false,
                     rotulo: "Comercial no cadastro" },
    reclassificar_alta: { cor: "#16a34a", anel: "#14532d", destaque: true,
                          rotulo: "Reclassificar — CNPJ conferido" },
    reclassificar_media: { cor: "#22c55e", anel: "#166534", destaque: true,
                           rotulo: "Reclassificar — CNPJ parcial" },
    reclassificar_baixa: { cor: "#4ade80", anel: "#15803d", destaque: true,
                           rotulo: "Reclassificar — sem CNPJ" },
  };
  const SEM_LIGACAO = { cor: "#6366f1", anel: "#3730a3", destaque: false,
                        rotulo: "Sem ligação no cadastro" };

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
    basemap: 0, pois: [], stats: null, cadastro: null, eu: null, empresa: null,
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
    // `maxZoom` EXPLÍCITO: o cluster morre com "Map has no maxZoom specified"
    // se a camada base ainda não entrou. Hoje ela entra antes, mas amarrar o
    // mapa à ordem de duas linhas é deixar uma armadilha para o próximo.
    mapa = L.map("mapa", { zoomControl: true, attributionControl: false, maxZoom: 22 })
             .setView([-15.78, -47.93], 4.4);

    // PANES COM ANDAR PRÓPRIO — isto é conserto de defeito, não organização.
    //
    // A malha é uma camada clicável que cobre o mapa inteiro, e sem andar
    // próprio ela rouba o clique de tudo o que está por baixo: clicar num POI
    // dentro do município selecionava o município. Cada coisa no seu andar,
    // marcadores no topo, sempre clicáveis. É a mesma escada da tela antiga.
    mapa.createPane("paneMalha").style.zIndex = 410;
    mapa.createPane("paneArea").style.zIndex = 415;
    mapa.createPane("paneMarcadores").style.zIndex = 630;

    estado.basemap = baseSalva();
    camadaBase(estado.basemap).addTo(mapa);
    camadaBase(estado.basemap).bringToBack();
    tile = camadaBase(estado.basemap);

    // O CLUSTER É O QUE TORNA 32 MIL PONTOS VIÁVEIS.
    //
    // A versão anterior desta tela punha cada POI num `L.layerGroup` como
    // marcador próprio: 32.429 nós no DOM de uma vez, cada um com o popup já
    // montado. O navegador engasgava em qualquer arrasto. O `markerClusterGroup`
    // mantém no DOM só o que cabe na tela — é o que a tela antiga sempre fez, e
    // eu não devia ter trocado.
    camadaPois = L.markerClusterGroup({
      showCoverageOnHover: false, maxClusterRadius: 54, spiderfyOnMaxZoom: true,
      chunkedLoading: true,
      iconCreateFunction(c) {
        const n = c.getChildCount();
        const sz = n < 50 ? 38 : n < 300 ? 46 : 54;
        const cls = n < 50 ? "" : n < 300 ? "md" : "lg";
        return L.divIcon({
          html: `<div class="cluster ${cls}" style="width:${sz}px;height:${sz}px;` +
                `font-size:${n < 50 ? 13 : 14}px">` +
                `${n >= 1000 ? (n / 1000).toFixed(1) + "k" : n}</div>`,
          className: "", iconSize: [sz, sz],
        });
      },
    });
    mapa.addLayer(camadaPois);
    camadaDesenho = L.layerGroup().addTo(mapa);
    const rot = $("basemap-nome");
    if (rot) rot.textContent = BASES[estado.basemap].nome;

    mapa.on("click", cliqueNoMapa);
    mapa.on("dblclick", concluirDesenho);
    mapa.on("moveend", malhaSegueMapa);
    setTimeout(() => mapa.invalidateSize(), 200);
    carregarGoogle();
  }

  // A JS API DO GOOGLE ENTRA DEPOIS, e só a base ATIVA é recriada.
  //
  // Carregar o script antes de montar o mapa deixaria a tela em branco enquanto
  // a rede responde. O Leaflet sobe na hora com o que tiver e o Google entra
  // quando estiver pronto.
  //
  // Só as bases DO GOOGLE são recriadas aqui. Recriar uma do Carto a
  // transformaria num mapa do Google — foi exatamente o defeito que a tela
  // antiga já tinha caçado e documentado: o "Claro (sem Google)" virava Google,
  // e se a API não inicializasse direito ficava sem fundo nenhum.
  async function carregarGoogle() {
    try {
      const c = await pegar("/api/mapa/config");
      if (!c || !c.key) return;
      await new Promise((ok, falha) => {
        const s = document.createElement("script");
        const mid = c.mapId ? "&map_ids=" + encodeURIComponent(c.mapId) : "";
        s.src = "https://maps.googleapis.com/maps/api/js?key="
              + encodeURIComponent(c.key) + mid
              + "&language=pt-BR&region=BR&loading=async";
        s.async = true; s.onload = ok; s.onerror = falha;
        document.head.appendChild(s);
      });
    } catch (e) {
      return;                                 // segue com o Carto
    }
    if (!BASES[estado.basemap].google) return;
    const antigo = BASES[estado.basemap].layer;
    BASES[estado.basemap].layer = null;       // força recriar, agora com Google
    const novo = camadaBase(estado.basemap);
    if (novo === antigo) return;
    novo.addTo(mapa);
    novo.bringToBack();
    if (antigo && mapa.hasLayer(antigo)) mapa.removeLayer(antigo);
    tile = novo;
  }

  function pintarMenuBase() {
    const menu = $("menu-basemap");
    if (!menu) return;
    [...menu.children].forEach((c) => {
      const i = Number(c.dataset.base);
      const ligado = Number.isInteger(i)
        ? i === estado.basemap
        : malhaVisivel;                       // a última linha é o liga/desliga das divisas
      c.className = "flex w-full items-center justify-between px-3 py-1 text-left " +
        "text-sm/6 hover:bg-gray-50 " +
        (ligado ? "bg-indigo-50 text-indigo-600" : "text-gray-900") +
        (c.dataset.base === "malha" ? " border-t border-gray-100 mt-1 pt-2" : "");
      const marca = c.querySelector("span:last-child");
      if (marca) marca.textContent = ligado ? "✓" : "";
    });
  }

  // TROCAR A CAMADA, e não a URL dela: o GoogleMutant não é um `L.TileLayer`,
  // e `setUrl` rebentaria. A camada nova vem do cache — criar uma por clique
  // instanciava um `google.maps.Map` a cada troca.
  function trocarBase(i) {
    if (!mapa || i === estado.basemap) return;
    const nova = camadaBase(i);
    nova.addTo(mapa);
    nova.bringToBack();
    if (tile && tile !== nova && mapa.hasLayer(tile)) mapa.removeLayer(tile);
    tile = nova;
    estado.basemap = i;
    try { localStorage.setItem("cr_base", BASES[i].chave); } catch (e) { /* sem storage */ }
    $("basemap-nome").textContent = BASES[i].nome;
    pintarMenuBase();
  }

  // ── divisas municipais (malha do IBGE) ──────────────────────────────────
  //
  // Desenhar a divisa de um município ponto a ponto é inviável, e a malha
  // oficial já está no banco. Ela NASCE DESLIGADA nesta tela — o pedido de
  // 27/08/2026 foi um mapa sem delimitação — mas a máquina continua aqui, com
  // o liga/desliga no menu do mapa, porque quem confere um município precisa
  // ver onde ele acaba.

  const MALHA_ESTILO = { color: "#5b6b80", weight: 1.2, opacity: 0.9,
                         fillColor: "#5b6b80", fillOpacity: 0.03 };
  let malhaGrupo = null;
  const malhaUFs = new Map();                 // sigla -> camada já baixada
  let malhaVisivel = false;
  let malhaTimer = null;

  function alternarMalha() {
    malhaVisivel = !malhaVisivel;
    if (!mapa) return;
    if (!malhaVisivel) {
      if (malhaGrupo && mapa.hasLayer(malhaGrupo)) mapa.removeLayer(malhaGrupo);
    } else {
      if (malhaGrupo) malhaGrupo.addTo(mapa);
      malhaSegueMapa(true);
    }
    pintarMenuBase();
  }

  // A malha SEGUE O MAPA e acumula as UFs visitadas: pedir só a UF do banco
  // deixaria sem divisa quem navega para o estado vizinho.
  function malhaSegueMapa(agora) {
    if (!malhaVisivel || !mapa) return;
    clearTimeout(malhaTimer);
    malhaTimer = setTimeout(() => {
      if (mapa.getZoom() < 6) return;         // no mundo inteiro não faz sentido
      const c = mapa.getCenter();
      carregarMalha(c.lat, c.lng);
    }, agora ? 0 : 400);
  }

  async function carregarMalha(lat, lng) {
    if (!malhaGrupo) malhaGrupo = L.layerGroup();
    const gj = await pegar(`/api/malha?lat=${lat.toFixed(4)}&lng=${lng.toFixed(4)}`);
    if (!gj || gj.erro || !gj.features) return;   // IBGE fora do ar: segue sem
    const sig = gj.uf || "?";
    if (malhaUFs.has(sig)) return;                // já desenhada
    const camada = L.geoJSON(gj, {
      style: MALHA_ESTILO, pane: "paneMalha", interactive: false,
    });
    malhaUFs.set(sig, camada);
    malhaGrupo.addLayer(camada);
    if (malhaVisivel && !mapa.hasLayer(malhaGrupo)) malhaGrupo.addTo(mapa);
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
      L.polyline(pts, { pane: "paneArea", color: INDIGO, weight: 2, dashArray: "6 5" }).addTo(camadaDesenho);
    }
    pts.forEach((p) => L.circleMarker(p, {
      pane: "paneArea", radius: 4, color: INDIGO, fillColor: "#fff", fillOpacity: 1, weight: 2,
    }).addTo(camadaDesenho));
  }

  async function concluirDesenho() {
    if (estado.pts.length < 3) return;
    const pts = estado.pts.slice();
    camadaDesenho.clearLayers();
    const poly = L.polygon(pts, {
      pane: "paneArea",
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

  // O MARCADOR É O PINO DE SEMPRE — mesma forma, mesma sombra, mesma cauda,
  // vindos do `mapa.css`. O que muda em relação à tela antiga é APENAS o eixo
  // da cor e um distintivo a mais, que foi o pedido de 27/08/2026:
  //
  //   a COR       deixa de ser a categoria e passa a ser o cruzamento com o
  //               cadastro: AMARELO já é comercial na base do cliente, VERDE
  //               em destaque é habitacional na base com comércio achado no
  //               local — o achado que o produto existe para encontrar.
  //   o NÚMERO    acima do pino, quantas bases sustentam o ponto. Só aparece
  //               com mais de uma: "1" em 32 mil marcadores seria ruído.
  //
  // O popup é montado NO CLIQUE. Montar 32 mil de antemão era construir texto
  // que ninguém ia ler.
  function desenharPois() {
    if (!camadaPois) return;
    camadaPois.clearLayers();
    const lista = poisFiltrados();
    const marcadores = [];
    for (const p of lista) {
      if (p.lat == null || p.lng == null) continue;
      const e = CORES[p.cruz_flag] || SEM_LIGACAO;
      const n = Number(p.n_fontes || 1);
      const m = L.marker([p.lat, p.lng], {
        pane: "paneMarcadores",
        icon: L.divIcon({
          className: "pin-wrap",
          html: `<div class="pin${e.destaque ? " achado" : ""}" style="--c:${e.cor}">` +
                `<div class="pin-head"></div><div class="pin-tail"></div>` +
                (n > 1 ? `<div class="pin-fontes">${n}</div>` : "") +
                "</div>",
          iconSize: [34, 43], iconAnchor: [17, 43],
        }),
        // O ACHADO FICA POR CIMA. Numa rua densa o ponto que interessa some
        // atrás dos que já estão no cadastro.
        zIndexOffset: e.destaque ? 1000 : 0,
        keyboard: false,
      });
      m.on("click", () => m.bindPopup(fichaDoPonto(p, e, n)).openPopup());
      marcadores.push(m);
    }
    camadaPois.addLayers(marcadores);         // um lote só: 32 mil `addLayer` não
    $("job-cap").dataset.pontos = lista.length;
  }

  function fichaDoPonto(p, e, n) {
    return `<b>${escapar(p.nome || "(sem nome)")}</b><br>` +
      `<span style="color:#6b7280">${escapar(p.endereco || "sem endereço")}</span>` +
      `<br><small style="color:${e.anel}">${escapar(e.rotulo)}` +
      (p.num_ligacao ? ` · ligação ${escapar(p.num_ligacao)}` : "") + "</small>" +
      `<br><small style="color:#6b7280">${n} ` + (n === 1 ? "fonte" : "fontes") + "</small>" +
      (p.veredito ? `<br><small>${escapar(p.veredito)}</small>` : "");
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
        pane: "paneArea",
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
    estado.eu = eu;
    if (!eu) return;
    $("perfil-nome-topo").textContent = eu.nome || "";
    $("marca-empresa").textContent = eu.empresa || "";
    const ini = (eu.nome || "··").split(" ").filter(Boolean)
      .map((p) => p[0]).slice(0, 2).join("").toUpperCase();
    $("perfil-iniciais").textContent = ini || "··";
  }

  // ── modais de perfil e organização ──────────────────────────────────────

  function abrirModal(id) {
    const m = $(id);
    m.classList.remove("hidden");
    m.classList.add("flex");
  }

  function fecharModais() {
    ["m-stats", "m-perfil", "m-org"].forEach((id) => {
      const m = $(id);
      m.classList.add("hidden");
      m.classList.remove("flex");
    });
  }

  function iniciais(nome) {
    return (nome || "··").split(" ").filter(Boolean)
      .map((p) => p[0]).slice(0, 2).join("").toUpperCase() || "··";
  }

  async function abrirPerfil() {
    // O MODAL ABRE PRIMEIRO, E SÓ DEPOIS BUSCA. A primeira versão fazia o
    // contrário e saía calada quando `/api/eu` falhava — o clique não produzia
    // nada na tela, e um botão que não faz nada é indistinguível de um botão
    // quebrado. Abrindo antes, a falha vira uma frase em vez de silêncio.
    abrirModal("m-perfil");
    $("pf-msg").textContent = "";
    const eu = estado.eu || await pegar("/api/eu");
    estado.eu = eu;
    if (!eu) {
      $("pf-msg").textContent = "não foi possível ler o seu cadastro agora";
      return;
    }
    $("pf-nome").value = eu.nome || "";
    $("pf-cargo").value = eu.cargo || "";
    $("pf-telefone").value = eu.telefone || "";
    $("pf-email").value = eu.email || "";
    $("pf-empresa").value = eu.empresa || "";
    $("pf-nivel").value = eu.nivel || "";
    const av = $("pf-avatar");
    if (eu.tem_foto) {
      av.innerHTML = '<img alt="" class="size-full object-cover">';
      av.querySelector("img").src = window.comToken
        ? window.comToken("/api/eu/foto") : "/api/eu/foto";
    } else {
      av.textContent = iniciais(eu.nome);
    }
    abrirModal("m-perfil");
  }

  async function salvarPerfil() {
    // SÓ OS TRÊS QUE O SERVIDOR ACEITA. O `PATCH /api/eu` ignora nível, empresa
    // e e-mail de propósito — mandar mais campos não os alteraria e daria a
    // impressão de que a tela pode o que não pode.
    const corpo = {
      nome: $("pf-nome").value.trim() || null,
      cargo: $("pf-cargo").value.trim() || null,
      telefone: $("pf-telefone").value.trim() || null,
    };
    $("pf-msg").textContent = "salvando…";
    const r = await fetch("/api/eu", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    }).catch(() => null);
    if (!r || !r.ok) {
      $("pf-msg").textContent = "não foi possível salvar — tente de novo";
      return;
    }
    $("pf-msg").textContent = "salvo";
    estado.eu = null;                       // relê no próximo abrir
    await carregarCracha();
  }

  async function trocarFoto(arquivo) {
    if (!arquivo) return;
    if (arquivo.size > 4 * 1024 * 1024) {
      $("pf-msg").textContent = "a imagem passa de 4 MB";
      return;
    }
    const fd = new FormData();
    fd.append("file", arquivo);
    $("pf-msg").textContent = "enviando…";
    const r = await fetch("/api/eu/foto", { method: "POST", body: fd })
      .catch(() => null);
    $("pf-msg").textContent = (r && r.ok) ? "foto trocada" : "não foi possível enviar";
    if (r && r.ok) { estado.eu = null; await abrirPerfil(); }
  }

  async function abrirOrg() {
    abrirModal("m-org");
    $("org-msg").textContent = "";

    // A LISTA É DE QUEM ADMINISTRA, e quem decide isso é a policy do banco —
    // `/api/usuarios` exige nível admin. Em vez de esconder o item do menu,
    // a tela abre e DIZ por que está vazia: o operador entende o limite em vez
    // de achar que a página quebrou.
    const us = await pegar("/api/usuarios");
    const podeVer = !!(us && us.usuarios);
    $("org-sem-permissao").classList.toggle("hidden", podeVer);
    $("org-conteudo").classList.toggle("hidden", !podeVer);
    if (!podeVer) return;

    const eu = estado.eu || await pegar("/api/eu");
    estado.eu = eu;
    const minha = (eu && eu.empresa) || "";
    const lista = us.usuarios.filter((u) => !minha || u.empresa === minha);

    $("org-n").textContent = lista.length + (lista.length === 1 ? " usuário" : " usuários");
    $("org-total").textContent = nf.format(lista.filter((u) => u.ativo).length);

    const emp = await pegar("/api/empresas");
    const daMinha = emp && emp.empresas
      ? emp.empresas.find((e) => e.nome === minha) : null;
    estado.empresa = daMinha || null;
    $("org-nome").value = (daMinha && daMinha.nome) || minha;
    $("org-doc").value = (daMinha && daMinha.documento) || "";

    const alvo = $("org-usuarios");
    alvo.innerHTML = "";
    lista.forEach((u) => {
      const li = document.createElement("li");
      li.className = "flex items-center justify-between gap-x-6 py-3.5";
      li.innerHTML =
        '<div class="flex min-w-0 gap-x-3.5">' +
        '<span class="flex size-10 shrink-0 items-center justify-center rounded-full bg-gray-100 text-[12.5px] font-semibold text-gray-600"></span>' +
        '<div class="min-w-0 flex-auto">' +
        '<p class="truncate text-sm/6 font-semibold text-gray-900"></p>' +
        '<p class="truncate text-xs/5 text-gray-500"></p></div></div>' +
        '<div class="flex shrink-0 flex-col items-end">' +
        '<p class="text-sm/6 text-gray-900"></p>' +
        '<div class="mt-0.5 flex items-center gap-x-1.5">' +
        '<span class="flex-none rounded-full p-1"><span class="block size-1.5 rounded-full"></span></span>' +
        '<p class="text-xs/5 text-gray-500"></p></div></div>';
      const [av, nome, email] = [li.querySelector("span"),
        li.querySelector("p"), li.querySelectorAll("p")[1]];
      av.textContent = iniciais(u.nome);
      nome.textContent = u.nome || "(sem nome)";
      email.textContent = u.email || "";
      li.querySelectorAll("p")[2].textContent = u.cargo || u.nivel || "";
      const anel = li.querySelectorAll("span")[1];
      const ponto = anel.querySelector("span");
      anel.className = "flex-none rounded-full p-1 " +
        (u.ativo ? "bg-emerald-500/20" : "bg-gray-300/40");
      ponto.className = "block size-1.5 rounded-full " +
        (u.ativo ? "bg-emerald-500" : "bg-gray-400");
      li.querySelectorAll("p")[3].textContent = u.ativo ? "Ativo" : "Inativo";
      alvo.appendChild(li);
    });
  }

  async function salvarOrg() {
    const e = estado.empresa;
    if (!e) {
      $("org-msg").textContent = "empresa não identificada — nada a salvar";
      return;
    }
    $("org-msg").textContent = "salvando…";
    const r = await fetch("/api/empresas/" + encodeURIComponent(e.id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nome: $("org-nome").value.trim(),
        documento: $("org-doc").value.trim() || null,
      }),
    }).catch(() => null);
    $("org-msg").textContent = (r && r.ok)
      ? "salvo"
      : "não foi possível salvar — só quem administra pode alterar a empresa";
    if (r && r.ok) { estado.eu = null; await carregarCracha(); }
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
    const linhaMenu = (rotulo, chave, aoClicar) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.base = chave;
      b.innerHTML = '<span></span><span class="text-xs font-semibold text-indigo-600"></span>';
      b.querySelector("span").textContent = rotulo;
      b.addEventListener("click", () => { aoClicar(); menu.classList.add("hidden"); });
      menu.appendChild(b);
    };
    BASES.forEach((b, i) => linhaMenu(b.nome, String(i), () => trocarBase(i)));
    // AS DIVISAS SÃO UM LIGA/DESLIGA, e vivem no menu do mapa porque é ali que
    // se decide o que se enxerga. Nascem desligadas — o pedido foi um mapa sem
    // delimitação — e quem precisa conferir onde um município acaba liga.
    linhaMenu("Divisas municipais", "malha", alternarMalha);
    pintarMenuBase();
    $("btn-basemap").addEventListener("click", () => {
      menu.classList.toggle("hidden");
      $("menu-perfil").classList.add("hidden");
    });

    $("btn-perfil-menu").addEventListener("click", () => {
      $("menu-perfil").classList.toggle("hidden");
      menu.classList.add("hidden");
    });
    // SAIR APAGA AS TRÊS CHAVES CERTAS, e elas estão no `sessionStorage`.
    //
    // A primeira versão removia `localStorage.cr_sessao` — uma chave que não
    // existe. O clique recarregava a página, o token continuava lá, e o
    // operador voltava logado: o botão parecia funcionar e não fazia nada.
    //
    // O `sessao.js` guarda `cr_token`, `cr_refresh` e `cr_expira` no
    // `sessionStorage` de propósito (fechou a aba, acabou a sessão). Deixar o
    // refresh para trás seria pior que não limpar nada: a próxima carga o
    // usaria para renovar sozinha.
    $("btn-sair").addEventListener("click", () => {
      try {
        ["cr_token", "cr_refresh", "cr_expira"]
          .forEach((k) => sessionStorage.removeItem(k));
      } catch (e) { /* aba sem storage: o reload devolve ao login mesmo assim */ }
      location.href = "/";
    });

    $("btn-expandir").addEventListener("click", () => abrirModal("m-stats"));
    document.querySelectorAll("[data-fechar-modal]").forEach((b) =>
      b.addEventListener("click", fecharModais));

    $("btn-abrir-perfil").addEventListener("click", () => {
      $("menu-perfil").classList.add("hidden");
      abrirPerfil();
    });
    $("btn-abrir-org").addEventListener("click", () => {
      $("menu-perfil").classList.add("hidden");
      abrirOrg();
    });
    $("pf-salvar").addEventListener("click", salvarPerfil);
    $("org-salvar").addEventListener("click", salvarOrg);
    $("pf-trocar-foto").addEventListener("click", () => $("pf-arquivo").click());
    $("pf-arquivo").addEventListener("change", (e) => trocarFoto(e.target.files[0]));

    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      $("menu-basemap").classList.add("hidden");
      $("menu-perfil").classList.add("hidden");
      fecharModais();
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
        pane: "paneArea",
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
