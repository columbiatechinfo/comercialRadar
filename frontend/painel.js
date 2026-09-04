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

  // O ÍCONE DIZ O RAMO, A COR DIZ O QUE FAZER. São duas perguntas diferentes e
  // cada uma tem seu canal.
  //
  // Eu havia tirado o ícone por categoria junto com a cor, e errei: o pedido
  // era trocar o EIXO DA COR, que passou a ser o cruzamento com o cadastro. O
  // ramo continua sendo a identificação imediata de quem varre o mapa — "tem
  // uma farmácia nesta esquina" se lê num relance, e num popup não.
  //
  // A tabela é a MESMA do `app.js`. Duplicá-la com outras palavras faria as
  // duas telas classificarem o mesmo POI de formas diferentes.
  const CATS = [
    { re: /restaurante|lanchonete|pizzari|hamburg|churrasc|comida|alimenta|café|cafeteria|padaria|sorveter|açai|acai|bar\b|petiscaria|self service|marmita/i, emo: "🍽️" },
    { re: /supermercado|mercado|mercearia|mercadinho|atacad|hortifruti|conveni|frios|distribuidora de bebidas|bebidas/i, emo: "🛒" },
    { re: /farm[aá]cia|drogaria|hospital|cl[ií]nica|laborat[oó]rio|dentista|odonto|m[eé]dic|sa[uú]de|fisioter|psicol|veterin|pet/i, emo: "💊" },
    { re: /escola|col[eé]gio|creche|faculdade|universi|curso|educa/i, emo: "🎓" },
    { re: /hotel|pousada|hostel|motel|hospedagem/i, emo: "🛏️" },
    { re: /banco|caixa eletr|lot[eé]rica|financ|cr[eé]dito|seguros/i, emo: "🏦" },
    { re: /oficina|mec[aâ]nica|auto ?pe[cç]as|autope[cç]as|borracharia|lava.?jato|concession|moto|el[eé]trica automotiva|posto de (comb|gas)/i, emo: "🔧" },
    { re: /sal[aã]o|barbearia|beleza|est[eé]tica|manicure|cabele/i, emo: "✂️" },
    { re: /academia|gym|crossfit|esporte|fitness/i, emo: "💪" },
    { re: /igreja|templo|par[oó]quia|assembleia/i, emo: "⛪" },
    { re: /constru|madeirei|ferragem|material|tinta|vidra[cç]|serralheria|marmoraria/i, emo: "🧱" },
    { re: /loja|boutique|magazine|variedade|utilidade|presente|papelaria|livraria|cal[cç]ado|roupa|confec|m[oó]veis|eletro|celular|inform[aá]tica|[oó]tica|joalheria|relojoaria|shopping/i, emo: "🛍️" },
  ];

  // O ACHADO DA IA VEM ANTES DA CATEGORIA: a coordenada dele é APROXIMADA,
  // deslocada pelo lado em que o comércio apareceu no quadro. O losango diz
  // isso sem legenda.
  function ramoDoPoi(poi) {
    if (poi.fonte === "ia_fachada") return { emo: "🔎", ia: true };
    const c = (poi.categoria || "").toString();
    for (const k of CATS) if (k.re.test(c)) return k;
    if (poi.status === "descoberto") return { emo: "✨" };
    return { emo: "📍" };
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
    desenhando: false, pts: [], temArea: false, anel: null,
    basemap: 0, pois: [], stats: null, cadastro: null, eu: null, empresa: null,
    filtros: { origem: [], atributos: [], ia: [], construcao: [] },
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
    // Sair pela borda, ir para o painel ou trocar de janela não dispara
    // `mouseout` no polígono sob o cursor, e o rótulo do município fica preso
    // na tela. Fechar na saída do container cobre todos esses casos.
    const fechar = () => mapa.eachLayer((l) => { if (l.closeTooltip) l.closeTooltip(); });
    mapa.getContainer().addEventListener("mouseleave", fechar);
    window.addEventListener("blur", fechar);
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
      style: MALHA_ESTILO, pane: "paneMalha",
      onEachFeature: (f, l) => {
        const nome = (f.properties || {}).nome || "";
        const cod = (f.properties || {}).codarea || "";
        l._dono = null;                        // preenchido logo abaixo
        l.bindTooltip(nome, { sticky: true, direction: "top", className: "muni-tip" });
        l.on("mouseover", () => {
          if (l !== malhaSelecionada) l.setStyle({ weight: 2.4, color: "#334155", fillOpacity: 0.08 });
        });
        l.on("mouseout", () => { if (l !== malhaSelecionada) estiloPadraoMalha(l); });
        l.on("click", (e) => {
          L.DomEvent.stopPropagation(e);
          // DESENHANDO, O CLIQUE É DO DESENHO. Sem isto, marcar um vértice
          // dentro de um município selecionaria o município e jogaria fora o
          // traçado em andamento.
          if (estado.desenhando) { cliqueNoMapa(e); return; }
          escolherMunicipio({ nome, cod, uf: sig });
        });
      },
    });
    camada.eachLayer((l) => { l._dono = camada; });
    malhaUFs.set(sig, camada);
    malhaGrupo.addLayer(camada);
    if (malhaVisivel && !mapa.hasLayer(malhaGrupo)) malhaGrupo.addTo(mapa);
  }

  // A MALHA É CLICÁVEL, e é assim que se escolhe município no mapa.
  //
  // Eu a havia posto com `interactive: false` achando que a lista lateral
  // bastava. Não basta: quem está olhando o mapa quer clicar no que vê. O
  // clique cai no mesmo `escolherMunicipio` da lista — um caminho só, senão as
  // duas formas de escolher divergem.
  //
  // O andar dela é o 410, abaixo da área (415) e dos marcadores (630): clicar
  // num POI dentro do município abre o POI, não seleciona o município. Foi
  // exatamente esse o defeito que a escada de panes existe para impedir.
  const MALHA_SEL = { color: "#0e7490", weight: 3, opacity: 1,
                      fillColor: "#0e7490", fillOpacity: 0.07 };
  let malhaSelecionada = null;

  function estiloPadraoMalha(l) {
    if (l && l._dono) l._dono.resetStyle(l);
    else if (l && l.setStyle) l.setStyle(MALHA_ESTILO);
  }

  function realcarMalha(cod) {
    if (malhaSelecionada) estiloPadraoMalha(malhaSelecionada);
    malhaSelecionada = null;
    if (!cod) return;
    for (const camada of malhaUFs.values()) {
      camada.eachLayer((l) => {
        if (String((l.feature && l.feature.properties || {}).codarea || "") !== String(cod)) return;
        l.setStyle(MALHA_SEL); l.bringToFront(); malhaSelecionada = l;
      });
    }
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
      L.polyline(pts, { pane: "paneArea", interactive: false,
                        color: INDIGO, weight: 2, dashArray: "6 5" }).addTo(camadaDesenho);
    }
    pts.forEach((p) => L.circleMarker(p, {
      pane: "paneArea", interactive: false, radius: 4, color: INDIGO, fillColor: "#fff", fillOpacity: 1, weight: 2,
    }).addTo(camadaDesenho));
  }

  async function concluirDesenho() {
    if (estado.pts.length < 3) return;
    const pts = estado.pts.slice();
    camadaDesenho.clearLayers();
    const poly = ligarFichaDaArea(L.polygon(pts, {
      pane: "paneArea", className: "area-poly",
      color: INDIGO, weight: 2, fillColor: INDIGO, fillOpacity: 0.12,
    })).addTo(camadaDesenho);
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
    estado.anel = pts;
    estado.pts = [];
    $("faixa-desenho").classList.add("hidden");
    $("faixa-desenho").classList.remove("flex");
    pintarEstado();
    await carregarBases();
    await carregarTudo();
  }

  // ── ficha da área desenhada ─────────────────────────────────────────────
  //
  // CLICAR NO POLÍGONO RESPONDE "quantos POIs há aqui dentro", e a quebra é por
  // FONTE porque foi ela que faltou quando 42 POIs do Overture apareceram como
  // "Outros" e ninguém sabia de onde tinham vindo. Vale para o desenho à mão e
  // para o contorno do município — nos dois casos a pergunta é a mesma.

  const FONTE_ROTULO = {
    estadual: ["Bases públicas", "Overture + OpenStreetMap + Foursquare"],
    pipeline: ["Captura + OCR", "Tiles do Maps lidos por visão computacional"],
    ifood: ["iFood", "Cardápios e lojas do iFood"],
    cadastur: ["Cadastur/MTur", "Prestadores registrados no Ministério do Turismo"],
    ia_fachada: ["Lidos na parede", "Nomes que a IA leu na fachada do Street View"],
    cliente: ["Base do cliente", "O cadastro que a concessionária entregou"],
  };

  // Área do anel em hectares, por projeção plana local: em polígono de bairro o
  // erro é irrelevante, e trazer uma biblioteca de geodésia para escrever
  // "3,5 ha" num popup seria peso sem retorno.
  function areaHectares(anel) {
    if (!anel || anel.length < 3) return 0;
    const latMed = anel.reduce((s, p) => s + p[0], 0) / anel.length;
    const mx = 111320 * Math.cos((latMed * Math.PI) / 180), my = 110540;
    let s2 = 0;
    for (let i = 0; i < anel.length; i++) {
      const j = (i + 1) % anel.length;
      s2 += (anel[j][1] * mx) * (anel[i][0] * my) - (anel[i][1] * mx) * (anel[j][0] * my);
    }
    return Math.abs(s2 / 2) / 10000;
  }

  function dentroDoAnel(anel, lat, lng) {
    let dentro = false;
    for (let i = 0, j = anel.length - 1; i < anel.length; j = i++) {
      const ai = anel[i], aj = anel[j];
      if ((ai[0] > lat) !== (aj[0] > lat) &&
          lng < ((aj[1] - ai[1]) * (lat - ai[0])) / (aj[0] - ai[0]) + ai[1]) dentro = !dentro;
    }
    return dentro;
  }

  function htmlFichaArea(poly) {
    const ll = (poly.getLatLngs() || [])[0] || [];
    const anel = ll.map((p) => [p.lat, p.lng]);
    // Recontado A CADA abertura: a mineração em tempo real muda `estado.pois`,
    // e um HTML preso no bind mostraria o número de quando o polígono foi
    // desenhado — justamente o engano que esta ficha existe para desfazer.
    const dentro = estado.pois.filter(
      (p) => p.lat != null && p.lng != null && dentroDoAnel(anel, p.lat, p.lng));
    const porFonte = new Map();
    for (const p of dentro) {
      const f = p.fonte || "sem fonte";
      porFonte.set(f, (porFonte.get(f) || 0) + 1);
    }
    const fontes = [...porFonte.entries()].sort((a, b) => b[1] - a[1]);
    const multi = dentro.filter((p) => Number(p.n_fontes || 1) > 1).length;
    const ha = areaHectares(anel);

    const linhas = fontes.map(([f, n]) => {
      const rot = FONTE_ROTULO[f] || [f, ""];
      return `<tr><td title="${escapar(rot[1])}">${escapar(rot[0])}</td>` +
             `<td class="num">${nf.format(n)}</td>` +
             `<td class="pct">${Math.round((n / dentro.length) * 100)}%</td></tr>`;
    }).join("");

    const corpo = dentro.length
      ? `<table class="area-pop-tab"><tbody>${linhas}</tbody></table>` +
        (multi ? `<p class="area-pop-nota">${nf.format(multi)} são <b>multiorigem</b> —
                  sustentados por mais de uma base. É onde a fusão pode ter errado.</p>` : "")
      : `<p class="area-pop-nota">Nenhum POI do banco aqui dentro. Se acabou de
         minerar, o mapa só mostra o que já foi gravado.</p>`;

    return `<div class="area-pop">
      <div class="area-pop-topo">
        <span class="area-pop-num">${nf.format(dentro.length)}</span>
        <span class="area-pop-cap">POIs do banco<br>dentro do desenho</span>
      </div>
      <div class="area-pop-sub">${ha < 10 ? ha.toLocaleString("pt-BR", { maximumFractionDigits: 1 })
                                          : nf.format(Math.round(ha))} ha ·
        ${anel.length} vértices${estado.cidade ? " · " + escapar(estado.cidade) : ""}</div>
      ${corpo}
      <button class="area-pop-del" type="button">Limpar a área</button>
    </div>`;
  }

  // O popup é montado UMA vez e só troca de conteúdo, e o botão é pego por
  // DELEGAÇÃO. As duas coisas vêm do mesmo defeito, medido em 25/08/2026 na
  // tela antiga: passar `options` no `bindPopup` faz o Leaflet construir uma
  // Popup NOVA a cada clique, e a partir da segunda abertura o `querySelector`
  // devolvia nulo — o popup abria com os números certos e o botão não fazia
  // nada. O pior tipo de defeito, porque a tela não acusa.
  function ligarFichaDaArea(poly) {
    poly.bindPopup("", { className: "area-pop-wrap", maxWidth: 340 });
    poly.on("click", (e) => {
      L.DomEvent.stopPropagation(e);
      poly.setPopupContent(htmlFichaArea(poly));
      poly.openPopup(e.latlng);
    });
    return poly;
  }

  document.addEventListener("click", (ev) => {
    if (!ev.target.closest || !ev.target.closest(".area-pop-del")) return;
    camadaDesenho.eachLayer((l) => { if (l.closePopup) l.closePopup(); });
    $("btn-limpar").click();          // um caminho só para limpar a área
  });

  function marcadorDoPoi(p) {
    if (p.lat == null || p.lng == null) return null;
    const e = CORES[p.cruz_flag] || SEM_LIGACAO;
    const n = Number(p.n_fontes || 1);
    const k = ramoDoPoi(p);
    const m = L.marker([p.lat, p.lng], {
      pane: "paneMarcadores",
      icon: L.divIcon({
        className: "pin-wrap",
        html: `<div class="pin${e.destaque ? " achado" : ""}${k.ia ? " m-ia-fachada" : ""}" ` +
              `style="--c:${e.cor}">` +
              `<div class="pin-head"><i>${k.emo}</i></div><div class="pin-tail"></div>` +
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
    return m;
  }

  function desenharPois() {
    if (!camadaPois) return;
    camadaPois.clearLayers();
    const lista = poisFiltrados();
    const marcadores = [];
    for (const p of lista) {
      const m = marcadorDoPoi(p);
      if (m) marcadores.push(m);
    }
    camadaPois.addLayers(marcadores);         // um lote só: 32 mil `addLayer` não
    marcadoresVivos.clear();                  // o registro do tempo real morre junto
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
      if (f.origem.length && !f.origem.includes(p.fonte || "")) return false;
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
    return f.origem.length + f.atributos.length + f.ia.length + f.construcao.length;
  }

  function montarFiltros() {
    // AS OPÇÕES SAEM DO DADO, e não de uma lista escrita à mão. O desenho traz
    // "Deliverys" e "SaaS de hospedagem"; nós temos as fontes que temos, e
    // oferecer o que não existe é pior que não oferecer.
    const fontes = [...new Set(estado.pois.map((p) => p.fonte).filter(Boolean))].sort();
    // ORIGEM É MULTISSELEÇÃO, como os outros três filtros. Rádio obrigava a
    // escolher UMA base por vez, e a pergunta real do operador é comparativa —
    // "o que a captura e o iFood acharam e o cadastro do cliente não tem".
    // Nenhuma marcada = todas, que é o mesmo contrato dos demais.
    const alvoOrigem = $("f-origem");
    alvoOrigem.innerHTML = "";
    fontes.forEach((f) => {
      const n = estado.pois.filter((p) => p.fonte === f).length;
      linhaCheck(alvoOrigem, `${f} · ${nf.format(n)}`,
        estado.filtros.origem.includes(f), () => alternar("origem", f));
    });
    if (!fontes.length) {
      alvoOrigem.innerHTML = '<p class="px-2 text-[11.5px] text-gray-400">Nenhuma origem nesta seleção.</p>';
    }

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

  function linhaCheck(alvo, rotulo, marcado, ao, cor) {
    const l = document.createElement("label");
    l.className = "flex cursor-pointer items-start gap-x-2.5 rounded-md px-2 py-1.5 text-[13px]/[18px] text-gray-700 hover:bg-gray-50";
    l.innerHTML = `<input type="checkbox" class="mt-0.5 size-4 shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-2 focus:ring-indigo-500"${marcado ? " checked" : ""}>` +
      (cor ? `<span class="mt-1 size-2 shrink-0 rounded-full ${cor}"></span>` : "") + "<span></span>";
    l.querySelector("span:last-child").textContent = rotulo;
    l.querySelector("input").addEventListener("change", ao);
    alvo.appendChild(l);
  }

  // ── monitor de proxies ──────────────────────────────────────────────────
  //
  // O ESTADO NÃO VEM DO POOL, e não tem como vir: ele vive dentro do processo
  // de mineração, no i9. O `/api/proxies` DERIVA o estado dos eventos que o
  // pool grava (migração 0041) — e é por isso que "de castigo" é o último
  // castigo cuja duração ainda não venceu, e não um booleano guardado.

  const PX_ESTADO = {
    livre:         ["Livres", "bg-green-500", "text-green-700"],
    em_uso:        ["Em uso", "bg-indigo-500", "text-indigo-700"],
    castigo:       ["De castigo", "bg-red-500", "text-red-700"],
    reservado:     ["Reservados (outro país)", "bg-gray-300", "text-gray-500"],
    fora_do_plano: ["Fora do plano", "bg-amber-400", "text-amber-700"],
  };

  let pxDados = null;

  function pxBytes(n) {
    n = Number(n || 0);
    if (!n) return "—";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : String(n)) + " " + u[i];
  }

  function pxQuando(iso) {
    if (!iso) return "nunca";
    const d = new Date(iso);
    const min = Math.round((Date.now() - d.getTime()) / 60000);
    if (min < 1) return "agora";
    if (min < 60) return min + " min";
    const h = Math.round(min / 60);
    if (h < 48) return h + " h";
    return Math.round(h / 24) + " d";
  }

  async function carregarProxies() {
    const horas = Number($("px-janela").value || 24);
    const d = await pegar("/api/proxies?horas=" + horas);
    pxDados = d;
    if (!d) {
      $("px-sub").textContent = "Não consegui ler o consumo agora.";
      return;
    }
    pintarProxies(d);
  }

  function pxCartao(rot, valor, nota) {
    return `<div class="rounded-lg border border-gray-200 p-4">
        <div class="text-[10.5px] font-semibold uppercase tracking-[0.07em] text-gray-400">${escapar(rot)}</div>
        <div class="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-gray-900">${escapar(valor)}</div>
        <div class="mt-0.5 text-[11.5px] text-gray-400">${escapar(nota)}</div>
      </div>`;
  }

  function pintarProxies(d) {
    const plano = d.plano || {};
    const est = d.por_estado || {};
    const cons = d.consumo || {};
    const pegou = (cons.pegou || {}).n || 0;
    const castigo = (cons.castigo || {}).n || 0;
    const bytes = Object.values(cons).reduce((s, v) => s + (v.bytes || 0), 0);
    const janela = d.janela_horas >= 24
      ? Math.round(d.janela_horas / 24) + (d.janela_horas >= 48 ? " dias" : " dia")
      : d.janela_horas + " h";

    $("px-sub").textContent =
      `${nf.format(plano.total || 0)} IPs no plano · ` +
      Object.entries(plano.por_pais || {}).map(([p, n]) => `${n} ${p}`).join(" · ") +
      (d.pais_ativo ? ` · em uso hoje: ${d.pais_ativo}` : "");

    $("px-cartoes").innerHTML =
      pxCartao("No plano", nf.format(plano.total || 0),
               `${nf.format(plano.ativos || 0)} ainda na última carga`) +
      pxCartao("Disponíveis para " + (d.pais_ativo || "uso"),
               nf.format(est.livre || 0), "prontos agora") +
      // A TAXA É O NÚMERO QUE DECIDE. "12 castigos" não diz nada sem saber de
      // quantas pegadas; 12 em 20 é um problema, 12 em 4.000 é rotina.
      pxCartao("Queimados na janela", nf.format(castigo),
               pegou ? `${(castigo / pegou * 100).toFixed(1)}% das ${nf.format(pegou)} pegadas`
                     : "nenhuma pegada no período") +
      pxCartao("Tráfego na janela", pxBytes(bytes), `últimos ${janela}`);

    // barra de estados, na proporção real
    const total = (d.itens || []).length || 1;
    $("px-barra").innerHTML = Object.keys(PX_ESTADO)
      .filter((k) => est[k])
      .map((k) => `<span class="${PX_ESTADO[k][1]}" style="width:${est[k] / total * 100}%" title="${escapar(PX_ESTADO[k][0])}: ${est[k]}"></span>`)
      .join("");

    const dl = $("px-estados");
    dl.innerHTML = "";
    for (const [k, [rot, bg, cor]] of Object.entries(PX_ESTADO)) {
      if (!est[k]) continue;
      const d2 = document.createElement("div");
      d2.className = "flex items-center gap-x-2";
      d2.innerHTML = `<span class="size-2 rounded-full ${bg}"></span>` +
        `<dt class="text-[12px] text-gray-500"></dt>` +
        `<dd class="text-[12.5px] font-semibold tabular-nums ${cor}"></dd>`;
      d2.querySelector("dt").textContent = rot;
      d2.querySelector("dd").textContent = nf.format(est[k]);
      dl.appendChild(d2);
    }

    pxRanking("px-motivos", d.motivos_de_castigo || [], "motivo", castigo);
    pxRanking("px-etapas", d.castigo_por_etapa || [], "etapa", castigo);
    pxLista();
  }

  function pxRanking(id, linhas, chave, total) {
    const alvo = $(id);
    alvo.innerHTML = "";
    if (!linhas.length) {
      alvo.innerHTML = '<p class="text-[12px] text-gray-400">Nenhum IP foi para castigo nesta janela.</p>';
      return;
    }
    for (const l of linhas) {
      const pct = total ? Math.round(l.n / total * 100) : 0;
      const d = document.createElement("div");
      d.innerHTML =
        '<div class="flex items-baseline justify-between gap-x-3 text-[12.5px]">' +
        '<dt class="min-w-0 truncate text-gray-600"></dt>' +
        '<dd class="shrink-0 font-semibold tabular-nums text-gray-900"></dd></div>' +
        `<div class="mt-1 h-1 w-full overflow-hidden rounded-full bg-gray-100">
           <div class="h-1 rounded-full bg-red-400" style="width:${pct}%"></div></div>`;
      d.querySelector("dt").textContent = l[chave];
      d.querySelector("dd").textContent = nf.format(l.n);
      alvo.appendChild(d);
    }
  }

  function pxLista() {
    if (!pxDados) return;
    const q = ($("px-busca").value || "").trim().toLowerCase();
    const f = $("px-filtro").value;
    const linhas = (pxDados.itens || []).filter((i) => {
      if (f && i.estado !== f) return false;
      if (!q) return true;
      return (i.endereco + " " + i.cidade + " " + i.pais).toLowerCase().includes(q);
    });

    const tb = $("px-linhas");
    tb.innerHTML = "";
    // AS PRIMEIRAS 300, e a contagem DIZ que cortou. São 500 linhas de <tr>,
    // e a tabela existe para conferir um IP específico — quem precisa de um
    // filtra. Cortar em silêncio faria a tela mentir sobre o tamanho do plano.
    for (const i of linhas.slice(0, 300)) {
      const [rot, bg, cor] = PX_ESTADO[i.estado] || ["?", "bg-gray-300", "text-gray-500"];
      const tr = document.createElement("tr");
      tr.innerHTML =
        '<td class="px-5 py-1.5 font-mono text-[11.5px] text-gray-700"></td>' +
        '<td class="px-3 py-1.5 text-gray-500"></td>' +
        '<td class="px-3 py-1.5 text-gray-500"></td>' +
        `<td class="px-3 py-1.5"><span class="inline-flex items-center gap-x-1.5 ${cor}">` +
        `<span class="size-1.5 rounded-full ${bg}"></span><span class="rot"></span></span></td>` +
        '<td class="px-3 py-1.5 text-right tabular-nums text-gray-700 usos"></td>' +
        '<td class="px-3 py-1.5 text-right tabular-nums cast"></td>' +
        '<td class="px-5 py-1.5 text-right tabular-nums text-gray-400 ult"></td>';
      const td = tr.querySelectorAll("td");
      td[0].textContent = i.endereco + ":" + i.porta;
      td[1].textContent = i.pais || "—";
      td[2].textContent = i.cidade || "—";
      tr.querySelector(".rot").textContent = rot;
      tr.querySelector(".usos").textContent = nf.format(i.usos || 0);
      const c = tr.querySelector(".cast");
      c.textContent = nf.format(i.castigos || 0);
      c.className += i.castigos ? " text-red-600 font-semibold" : " text-gray-400";
      tr.querySelector(".ult").textContent = pxQuando(i.ultimo);
      tb.appendChild(tr);
    }
    $("px-conta").textContent = linhas.length > 300
      ? `mostrando 300 de ${nf.format(linhas.length)} — filtre para ver os demais`
      : `${nf.format(linhas.length)} de ${nf.format((pxDados.itens || []).length)} IPs`;
  }

  // ── estatísticas ────────────────────────────────────────────────────────

  // "NOVOS PONTOS ÚNICOS" É O QUE O CADASTRO DO CLIENTE NÃO COBRE, e são dois
  // grupos, não um.
  //
  // Regra do dono do produto, 28/08/2026: contar quantos pontos são novos —
  // os que NÃO ESTAVAM no cadastro, mais os que estavam mas com classificação
  // DIFERENTE DE COMERCIAL. O segundo grupo vai numa caixinha cinza à direita
  // do número grande.
  //
  // Os dois são trabalho novo para a concessionária, e por motivos diferentes:
  //
  //   sem ligação      o cadastro não conhece este imóvel. Vira fila de
  //                    vinculação humana.
  //   reclassificar_*  o imóvel ESTÁ no cadastro, com tarifa que não é
  //                    comercial, e nós achamos comércio no local. É a
  //                    reclassificação — a razão de o produto existir.
  //
  // `ja_cadastrado` fica de fora dos dois: já é comercial na base do cliente,
  // não há o que fazer com ele. Somá-lo aqui inflaria o número que decide
  // quanta gente vai a campo.
  //
  // A contagem sai de `estado.pois`, e não de `s.validos`: é a mesma lista de
  // onde já sai "Multifontes" neste cartão, e é a única que traz `cruz_flag`.
  // O CARTÃO DESCREVE O ESCOPO, NÃO O FILTRO. `/api/stats?cidade=` já vinha
  // por município, mas `/api/pois` traz a base inteira — com Canoas escolhida,
  // "Multifontes" contava o Brasil todo ao lado de um total que era só de
  // Canoas. Os chips de filtro NÃO entram aqui: quem marca "com CNPJ" está
  // recortando o mapa, não redefinindo quantos pontos novos a cidade tem.
  function poisDoEscopo() {
    let pois = estado.pois || [];
    if (estado.cidade) {
      const c = estado.cidade.toLowerCase();
      pois = pois.filter((p) => (p.cidade || "").toLowerCase() === c);
    }
    // A ÁREA DESENHADA RECORTA IGUAL AO SERVIDOR. O `/api/stats?area=1` aplica
    // o MESMO teste de raio sobre a mesma área; se só um dos dois recortasse, o
    // número grande e as barras do cartão discordariam entre si.
    const anel = estado.anel;
    if (anel && anel.length >= 3) {
      pois = pois.filter((p) => p.lat != null && p.lng != null &&
                                dentroDoAnel(anel, p.lat, p.lng));
    }
    return pois;
  }

  function contarNovos() {
    const pois = poisDoEscopo();
    let semLigacao = 0, reclassificar = 0;
    for (const p of pois) {
      const f = p.cruz_flag || "";
      if (!f) semLigacao++;
      else if (f !== "ja_cadastrado") reclassificar++;
    }
    return { semLigacao, reclassificar, total: semLigacao + reclassificar };
  }

  // OS DOIS CARTÕES DIZEM A MESMA COISA DE DUAS FORMAS: um número grande e as
  // parcelas que o compõem, cada uma com nome inteiro.
  //
  // Antes eram distintivos com número solto — "12  6", "3  2 c/ POI". Ninguém
  // que lesse a tela sabia o que era o segundo número, e na lateral estreita o
  // "2 c/ POI" ainda quebrava em duas linhas. Pior: falavam em POI, que é
  // vocabulário nosso. Quem opera pensa em ESTABELECIMENTO e em LIGAÇÃO.
  //
  // A regra que ficou: nada de sigla, nada de número sem rótulo, e a soma das
  // parcelas tem de bater com o número grande — se não bate, é porque a
  // parcela que falta não tem nome, e aí ela precisa ganhar um.
  function linhaQuebra(alvo, rotulo, valor, sub) {
    const l = document.createElement("div");
    l.className = "flex items-baseline justify-between gap-x-2" +
      // A linha SUBORDINADA recua de verdade, com margem — espaço em branco no
      // texto o navegador colapsa, e ela ficava rente às outras, parecendo mais
      // uma parcela da soma em vez de um detalhe da parcela acima.
      (sub ? " ml-2.5 border-l border-gray-200 pl-2" : "");
    const forte = !sub;
    l.innerHTML =
      `<dt class="min-w-0 text-[11px] leading-snug ${forte ? "text-gray-500" : "text-gray-400"}"></dt>` +
      `<dd class="shrink-0 text-[11.5px] font-semibold tabular-nums ${forte ? "text-gray-900" : "text-gray-500"}"></dd>`;
    l.querySelector("dt").textContent = rotulo;
    l.querySelector("dd").textContent = nf.format(valor);
    alvo.appendChild(l);
  }

  function pintarQuebra(ids, linhas) {
    for (const id of ids) {
      const alvo = $(id);
      if (!alvo) continue;
      alvo.innerHTML = "";
      linhas.forEach(([rot, v, sub]) => linhaQuebra(alvo, rot, v, sub));
    }
  }

  function pintarNovos() {
    const n = contarNovos();
    for (const id of ["s-total", "m-total"]) {
      const e = $(id);
      if (e) e.textContent = nf.format(n.total);
    }
    // AS PARCELAS SOMAM O NÚMERO GRANDE. São os dois motivos de um comércio
    // achado não estar sendo cobrado como comércio, e eles pedem trabalhos
    // diferentes: um vira cadastro novo, o outro vira reclassificação.
    const alta = ((estado.cadastro || {}).por_flag || {}).reclassificar_alta || 0;
    const linhas = [
      ["Sem ligação no cadastro", n.semLigacao],
      ["Cobrado como outra coisa", n.reclassificar],
    ];
    if (alta) linhas.push(["destes, com CNPJ conferido", alta, true]);
    pintarQuebra(["s-novos-quebra", "m-novos-quebra"], linhas);
    return n;
  }

  function pintarStats(s) {
    if (!s) return;
    // O DENOMINADOR DAS BARRAS CONTINUA SENDO O TOTAL VÁLIDO: "com CNPJ" é uma
    // fatia de tudo o que foi minerado, não dos pontos novos. Trocá-lo faria
    // percentuais passarem de 100%.
    const total = s.validos || 0;
    pintarNovos();

    const multi = poisDoEscopo().filter((p) => p.multiorigem).length;
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

  // "JÁ COBRADO COMO COMÉRCIO" É O CONTRAPONTO do cartão de cima: quanto do
  // comércio daqui o cliente JÁ fatura como tal.
  //
  // O rótulo era "já comerciais no cadastro" e o número era `com_poi` — toda
  // ligação casada com um ponto nosso, de qualquer classificação, inclusive as
  // de reclassificar, que por definição NÃO são comerciais. Na base inteira:
  // 14.959 sob um rótulo que descreve 11.749.
  //
  // A quebra tem valor próprio, e não é enfeite: a ligação cobrada como
  // comércio onde NÃO achamos estabelecimento nenhum é um cadastro a conferir
  // pelo outro lado — pode ser ponto fechado, endereço errado ou falha nossa de
  // varredura. Sem a linha, esse número não existiria em lugar nenhum da tela.
  function pintarCadastro() {
    const c = estado.cadastro;
    if (!c) {
      ["s-cadastro", "m-cadastro"].forEach((id) => {
        const e = $(id);
        if (e) e.textContent = "—";
      });
      pintarQuebra(["s-cadastro-quebra", "m-cadastro-quebra"], []);
      return;
    }
    const com = c.comerciais || 0;
    const conf = c.comerciais_com_poi || 0;

    for (const id of ["s-cadastro", "m-cadastro"]) {
      const e = $(id);
      if (e) e.textContent = nf.format(com);
    }
    pintarQuebra(["s-cadastro-quebra", "m-cadastro-quebra"], [
      ["Com estabelecimento encontrado", conf],
      ["Sem estabelecimento encontrado", Math.max(0, com - conf)],
    ]);
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

  // QUEM PROCURA É O BANCO, E NÃO A TELA.
  //
  // Isto aqui montava a lista pedindo `/api/ufs` e, para cada UF de lá, os
  // municípios daquela UF. Mas `/api/ufs` responde as UFs QUE JÁ TÊM POI — em
  // base nova ela vem vazia, o laço não roda nenhuma vez, e digitar "canoas"
  // não achava nada: para escolher a cidade a minerar era preciso já ter
  // minerado a cidade. O sintoma era "Nenhum município com esse texto", que
  // culpa o texto.
  //
  // Agora a caixa de busca pergunta ao servidor, que procura na malha inteira
  // pelo nome. São 5.570 municípios do país inteiro, e nenhum deles precisa
  // atravessar a rede antes de alguém digitar.
  let buscaTimer = null;

  async function carregarMunicipios() {
    // Sem texto não há o que listar — a malha inteira num seletor não ajuda a
    // achar Canoas. A tela abre com o convite a digitar.
    pintarMunicipios();
  }

  function agendarBuscaMunicipio() {
    clearTimeout(buscaTimer);
    buscaTimer = setTimeout(buscarMunicipios, 250);   // não uma busca por tecla
  }

  async function buscarMunicipios() {
    const texto = ($("busca-municipio").value || "").trim();
    if (texto.length < 2) { municipios = []; pintarMunicipios(); return; }
    municipios = await pegar("/api/municipios?q=" + encodeURIComponent(texto)) || [];
    pintarMunicipios();
  }

  function pintarMunicipios() {
    const q = ($("busca-municipio").value || "").trim();
    const alvo = $("lista-municipios");
    alvo.innerHTML = "";
    const filtrados = municipios;
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
      // A MENSAGEM DIZ O ESTADO CERTO. "Nenhum município com esse texto" com a
      // caixa vazia culpava um texto que não existe, e foi assim que a lista
      // vazia passou por defeito de busca.
      alvo.innerHTML = q.length < 2
        ? '<p class="px-3 py-2 text-[12px] text-gray-400">Digite ao menos duas letras do nome do município.</p>'
        : '<p class="px-3 py-2 text-[12px] text-gray-400">Nenhum município com esse texto.</p>';
    }
  }

  async function escolherMunicipio(m) {
    estado.cidade = m.nome;
    estado.cod = m.cod;
    estado.temArea = false;
    estado.anel = null;
    camadaDesenho.clearLayers();

    // A ÁREA VIRA O MUNICÍPIO NO BANCO. O `minerar_tudo` lê a mesma área
    // desenhada — sem isto, escolher município na tela não mudaria o que a
    // extração faz.
    // O CÓDIGO VAI NA QUERY, e não no corpo. O endpoint declara `cod: str`,
    // que no FastAPI é parâmetro de QUERY — ele não lê corpo nenhum.
    //
    // Eu mandava `body: JSON.stringify({cod})`, o servidor respondia 422 por
    // não achar `cod`, e o `.catch(() => {})` ENGOLIA: `fetch` não rejeita em
    // erro HTTP, só em falha de rede. A tela pintava o município escolhido, o
    // banco continuava com a área anterior, e a mineração rodava a cidade
    // errada sem uma linha em lugar nenhum.
    //
    // Custou uma run de Rio Grande que minerou Canoas, em 28/08/2026. A tela
    // ANTIGA sempre chamou pela query — o defeito nasceu quando reescrevi esta.
    const r = await fetch("/api/area/municipio?cod=" + encodeURIComponent(m.cod),
                          { method: "POST" }).catch(() => null);
    if (!r || !r.ok) {
      // FALHA DE ÁREA É FALHA DE TUDO: sem ela a extração vai para o município
      // anterior. Melhor parar aqui, em voz alta, do que deixar a tela dizer
      // "Rio Grande" com Canoas no banco.
      linhaLog(`Não consegui definir ${m.nome} como área` +
               (r ? ` (HTTP ${r.status})` : " — sem resposta do servidor"),
               "text-red-400");
      estado.cidade = null;
      estado.cod = null;
      pintarEstado();
      return;
    }

    // O POLÍGONO VEM DA RESPOSTA QUE ACABOU DE GRAVAR A ÁREA.
    //
    // Aqui havia `pegar("/api/malha?cod=" + cod)`. Essa rota declara `uf`,
    // `lat` e `lng` — `cod` não existe nela, e o FastAPI ignora parâmetro de
    // query que não declarou. Ela respondia 200 com a UF INTEIRA (um
    // FeatureCollection), `malha.polygon` vinha `undefined`, o `if` era falso
    // e NENHUM polígono era desenhado. Escolher a cidade não mostrava a divisa
    // — só o realce, e só para quem tivesse ligado a camada de divisas no menu
    // do mapa. A área ia certa para o banco; a tela é que ficava muda.
    //
    // O `POST /api/area/municipio` já devolve o anel que gravou, na resolução
    // oficial. É a mesma geometria que a mineração vai recortar, o que é
    // exatamente a garantia que se quer ver na tela — e custa zero requisição.
    const dados = await r.json().catch(() => null);
    const anel = (dados && dados.polygon) || [];
    if (anel.length) {
      const poly = ligarFichaDaArea(L.polygon(anel, {
        pane: "paneArea", className: "area-poly",
        color: INDIGO, weight: 2, fillColor: INDIGO, fillOpacity: 0.1,
      })).addTo(camadaDesenho);
      mapa.fitBounds(poly.getBounds(), { padding: [30, 30] });
      // O desenho manual guarda o anel aqui, e a ficha do polígono o lê para
      // dizer quantos vértices tem. O do município passa a guardar também:
      // para a tela, os dois são a mesma coisa — inclusive na hora de apagar.
      estado.anel = anel;
    }
    realcarMalha(m.cod);
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
    // OS BOTOES DE PARAR SO APARECEM COM RUN VIVA. Sem run, nao ha o que parar.
    const acoes = $("job-acoes");
    if (acoes) acoes.classList.toggle("hidden", !rodando);
    if (acoes) acoes.classList.toggle("flex", rodando);
  }

  // PARAR A RUN — e, se pedido, APAGAR o que ela gerou no banco.
  //
  // O backend sabe qual e a sessao corrente (`JOB.sessao`) e apaga os POIs dela
  // e tudo que aponta para eles. "Parar" so derruba; "Parar e apagar" derruba e
  // limpa — foi o pedido de quem desenhou a area errada e nao tinha como
  // desfazer.
  async function pararRun(limpar) {
    if (limpar && !confirm("Parar a extração e APAGAR do banco tudo que ela "
                           + "gerou? Isso não pode ser desfeito.")) return;
    if (!limpar && !confirm("Parar a extração? O que já foi gravado fica.")) return;
    $("btn-parar-run").disabled = true;
    $("btn-parar-limpar").disabled = true;
    try {
      const r = await fetch("/api/jobs/parar", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limpar: !!limpar }),
      });
      const d = await r.json().catch(() => ({}));
      const ap = (d && d.apagados) || {};
      if (limpar) {
        linhaLog(ap.erro ? ("Parado, mas a limpeza falhou: " + ap.erro)
                 : ("Extração parada e apagada — " + (ap.pois || 0) + " POIs removidos"),
                 ap.erro ? "text-red-400" : "text-amber-300");
      } else {
        linhaLog("Extração parada — o que já foi gravado permanece.", "text-amber-300");
      }
    } catch (e) {
      linhaLog("Não consegui falar com o servidor para parar.", "text-red-400");
    } finally {
      $("btn-parar-run").disabled = false;
      $("btn-parar-limpar").disabled = false;
    }
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

  // O WEBSOCKET NÃO PASSA PELO `window.fetch`, ENTÃO NÃO HERDA A RENOVAÇÃO.
  //
  // Medido no log do servidor durante a extração de 28/08/2026:
  //
  //     "WebSocket /ws" 403   connection rejected   (repetido, SEM ?token=)
  //     "GET /api/jobs/atual" 401 Unauthorized
  //
  // Duas coisas ao mesmo tempo. A sessão vence em 1 h; o `fetch` embrulhado
  // renova sozinho antes de sair, mas o soquete é aberto direto pelo
  // `WebSocket`, que não tem esse embrulho. Quando a hora virava, o soquete
  // caía e a reconexão reapresentava um token morto — ou nenhum, porque o
  // `comToken` devolve a URL crua quando não há token guardado. O servidor
  // recusava com 403 e o recuo exponencial só deixava o laço mais silencioso.
  //
  // As três defesas abaixo são as MESMAS da tela antiga, e ela não tinha esse
  // problema. Eu escrevi este handler do zero e deixei todas de fora.
  function ligarWebsocket() {
    let espera = 2000;

    const abrir = async () => {
      // 1. RENOVA ANTES DE ABRIR. Se o soquete caiu POR o token ter vencido,
      //    reapresentar o mesmo não vai adiantar nunca.
      try {
        if (window.crVencendo && window.crVencendo() && window.crRenovar) {
          await window.crRenovar();
        }
      } catch (e) { /* segue: sem sessão, o passo 2 barra */ }

      // 2. SEM SESSÃO, NÃO TENTA. Tentar antes do login enche o servidor de 403
      //    num laço que só pararia quando alguém entrasse — e o log fica
      //    ilegível justamente na hora em que se quer ler o log. Em vez de
      //    sondar, espera o aviso de que a sessão existe.
      let tok = "";
      try { tok = sessionStorage.getItem("cr_token") || ""; } catch (e) { tok = ""; }
      if (!tok) {
        document.addEventListener("cr:sessao", () => abrir(), { once: true });
        return;
      }

      let ws;
      try {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(proto + "://" + location.host +
                           "/ws?token=" + encodeURIComponent(tok));
      } catch (e) {
        setTimeout(abrir, espera = Math.min(espera * 2, 60000));
        return;
      }

      let ping = null;
      ws.onopen = () => {
        espera = 2000;
        // MANTER A CONEXÃO VIVA. Sem tráfego, proxy e navegador fecham um
        // WebSocket ocioso — e numa etapa longa e silenciosa (fusão, cadastro)
        // a tela perdia a conexão justamente antes da próxima notícia.
        clearInterval(ping);
        ping = setInterval(() => {
          if (ws.readyState === 1) ws.send("ping");
        }, 25000);
      };
      ws.onmessage = (ev) => {
        let m;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m.tipo === "job") pintarJob(m.dados);
        else if (m.tipo === "progresso" && m.dados) pintarProgresso(m.dados);
        else if (m.tipo === "log" && m.linha) linhaLog(m.linha);
        else if (m.tipo === "poi" && m.poi) chegouPoi(m.poi);
        else if (m.tipo === "reload") { carregarPois(); carregarStats(); }
      };
      // 3. CAIU SEM TOKEN, ESPERA O LOGIN. Reagendar não devolve sessão
      //    nenhuma; quem devolve o WebSocket é o próximo login.
      ws.onclose = () => {
        clearInterval(ping);
        let ainda = "";
        try { ainda = sessionStorage.getItem("cr_token") || ""; } catch (e) { ainda = ""; }
        if (!ainda) {
          document.addEventListener("cr:sessao", () => abrir(), { once: true });
          return;
        }
        setTimeout(abrir, espera);
        espera = Math.min(espera * 2, 60000);
      };
    };

    abrir();
  }

  // A BARRA SEGUE O `progresso`, NÃO O `job`. O `job` chega em troca de etapa;
  // o `progresso`, a cada lote — é ele que faz a tela parecer viva.
  function pintarProgresso(d) {
    const c = d.contadores || {};
    const total = Number(d.total || 0);
    const feitos = Number(c.processados || 0);
    if (d.fase_rotulo) $("job-titulo").textContent = d.fase_rotulo;
    if (total > 0) {
      const pct = Math.max(0, Math.min(100, Math.round((feitos / total) * 100)));
      $("job-pct").textContent = pct + "%";
      $("job-bar").style.width = pct + "%";
    }
    // O RODAPÉ DIZ O QUE SAIU DE ÚTIL, e não só quantos passaram: numa fase de
    // busca "180 de 200" não diz se achou alguma coisa.
    const partes = [];
    if (total) partes.push(`${nf.format(feitos)} de ${nf.format(total)}`);
    const rot = d.rotulos || {};
    for (const k of ["validos", "recuperados", "descobertos", "sem_match"]) {
      if (c[k]) partes.push(`${nf.format(c[k])} ${rot[k] || k}`);
    }
    if (partes.length) $("job-cap").textContent = partes.join(" · ");
  }

  // O PONTO QUE CHEGA AO VIVO ENTRA SOZINHO. Recarregar `/api/pois` a cada POI
  // era baixar a base inteira por ponto minerado.
  //
  // O ingestor faz delete+recreate por `place_id`, então o POI reingerido chega
  // com id NOVO: sem tirar o antigo, o mapa fica com os dois marcadores lado a
  // lado. O `substitui` vem justamente para isso.
  let cartoesAgendados = null;
  const marcadoresVivos = new Map();          // id do POI -> marcador no mapa

  function chegouPoi(p) {
    if (!p || p.id == null || p.lat == null || p.lng == null) return;

    // O MESMO LUGAR VOLTA COM ID NOVO. O ingestor reingere por delete+recreate,
    // e sem tirar a versão antiga o mapa acumula as duas: num job de 26 POIs o
    // contador marcava 37, contando fantasmas de linhas que já não existem. A
    // identidade estável é o `place_id`, não o `id`.
    const iguais = [];
    if (p.place_id) {
      estado.pois.forEach((x) => {
        if (x.id !== p.id && x.place_id === p.place_id) iguais.push(x.id);
      });
    }
    iguais.push(p.id);                        // e a própria, se já estava lá
    for (const id of iguais) {
      const i = estado.pois.findIndex((x) => x.id === id);
      if (i >= 0) estado.pois.splice(i, 1);
      const m = marcadoresVivos.get(id);
      if (m && camadaPois) { camadaPois.removeLayer(m); marcadoresVivos.delete(id); }
    }
    estado.pois.push(p);

    // Só desenha o que o filtro atual deixaria ver — senão o ponto novo aparece
    // por cima de um recorte que o operador escolheu.
    if (poisFiltrados().some((x) => x.id === p.id)) {
      const m = marcadorDoPoi(p);
      if (m && camadaPois) { camadaPois.addLayer(m); marcadoresVivos.set(p.id, m); }
    }
    // OS CARTÕES ESPERAM MEIO SEGUNDO. Repintar a cada POI faria a tela
    // recontar 36 mil pontos por ponto que chega.
    clearTimeout(cartoesAgendados);
    cartoesAgendados = setTimeout(() => { pintarNovos(); montarFiltros(); }, 500);
  }

  // ── carga ───────────────────────────────────────────────────────────────

  async function carregarPois() {
    const d = await pegar("/api/pois");
    estado.pois = (d && d.pois) || [];
    desenharPois();
    montarFiltros();
    // O CARTÃO NÃO ESPERA O `/api/stats`. Ele conta a partir dos POIs, que já
    // chegaram — e se a rota de estatísticas falhar, o número de pontos novos
    // continua na tela em vez de virar um travessão.
    pintarNovos();
  }

  async function carregarStats() {
    // A ÁREA VAI COMO SINALIZADOR, e não como polígono na URL: ela já está
    // gravada no banco (`/api/area`), e mandar centenas de vértices numa query
    // string seria repetir o que o servidor já tem.
    const p = [];
    if (estado.cidade) p.push("cidade=" + encodeURIComponent(estado.cidade));
    const q = p.length ? "?" + p.join("&") : "";
    const qa = estado.temArea ? (q ? q + "&area=1" : "?area=1") : q;
    estado.stats = await pegar("/api/stats" + qa);
    // O RESUMO DO CADASTRO RECORTA IGUAL. Eu havia deixado de fora achando que
    // ligação não tem coordenada — tem: `lat`/`lng` próprias, 100% preenchidas
    // nas 102.065 linhas. Sem isto o cartão encostava o 14.959 do município
    // inteiro num número de bairro.
    estado.cadastro = await pegar("/api/cadastro/resumo" + qa);
    pintarStats(estado.stats);
  }

  async function carregarTudo() {
    await carregarPois();
    await carregarStats();
  }

  // O CRACHÁ DA LATERAL diz quantos IPs estão prontos sem ninguém abrir o
  // modal. Um "0" ali é a diferença entre "a extração está lenta" e "a extração
  // não tem por onde sair", e essa distinção não pode custar dois cliques.
  async function carregarCrachaProxies() {
    const d = await pegar("/api/proxies?horas=24");
    const e = $("proxies-n");
    if (!e) return;
    if (!d) { e.textContent = "—"; return; }
    const livres = (d.por_estado || {}).livre || 0;
    e.textContent = nf.format(livres);
    e.className = "ml-auto inline-flex min-w-5 items-center justify-center " +
      "rounded-full px-2 text-[11px] font-semibold " +
      (livres ? "bg-gray-100 text-gray-500" : "bg-red-50 text-red-600");
    e.title = livres
      ? `${nf.format(livres)} IPs prontos para uso`
      : "Nenhum IP disponível: a extração vai parar esperando o pool";
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
    ["m-stats", "m-perfil", "m-org", "m-bases"].forEach((id) => {
      const m = $(id);
      m.classList.add("hidden");
      m.classList.remove("flex");
    });
  }


  // ── bases do cliente: a declaração que libera a extração ────────────────
  //
  // O fluxo da fase 1 começa aqui. Enquanto ninguém declarar qual coluna é a
  // latitude, qual é a ligação e quais tipos são comércio, escolher área é
  // trabalhar no escuro — e errar a coluna de tipo faz o produto inteiro
  // classificar comércio como residência.

  let basesEstado = { lista: [], atual: null, detalhe: null };

  async function carregarBases() {
    try {
      const r = await pegar("/api/base-cliente");
      basesEstado.lista = r.bases || [];
      aplicarTravaDeBase(!!r.alguma_pronta);
    } catch (e) {
      // Sem a rota, a trava não pode ser aplicada às cegas: travar tudo por um
      // erro de rede deixaria a tela inútil sem explicar.
      aplicarTravaDeBase(true);
    }
  }

  function aplicarTravaDeBase(liberado) {
    const motivo = "Declare as colunas da base do cliente antes: sem saber " +
      "qual coluna é o tipo de cliente, o Radar não sabe o que é comércio.";
    ["btn-desenhar", "btn-municipio"].forEach((id) => {
      const b = $(id);
      if (!b) return;
      b.disabled = !liberado;
      b.title = liberado ? "" : motivo;
      b.classList.toggle("cursor-not-allowed", !liberado);
      b.classList.toggle("opacity-50", !liberado);
    });
    const marca = $("sel-bases-marca");
    if (marca) marca.textContent = liberado ? "" : "declare";
    const dica = $("dica-modo");
    if (dica && !liberado) dica.textContent = "Declare a base do cliente primeiro";
  }

  async function abrirBases() {
    abrirModal("m-bases");
    await carregarBases();
    const sel = $("bases-lista");
    sel.innerHTML = basesEstado.lista.map(
      (b) => `<option value="${b.id}">${b.nome} — ${b.estado}</option>`).join("");
    if (!basesEstado.lista.length) {
      $("bases-info").textContent = "nenhuma base cadastrada";
      $("bases-aviso").textContent =
        "Rode `base_cliente_mapear.py --tabela schema.tabela --aplicar` para a IA ler a base.";
      return;
    }
    sel.onchange = () => carregarDetalheBase(sel.value);
    await carregarDetalheBase(basesEstado.lista[0].id);
  }

  async function carregarDetalheBase(id) {
    const d = await pegar(`/api/base-cliente/${id}`);
    basesEstado.atual = id;
    basesEstado.detalhe = d;

    const b = d.base;
    $("bases-info").textContent =
      `${(b.linhas || 0).toLocaleString("pt-BR")} linhas · ${b.tabela_dados}`;
    $("bases-sub").textContent = b.estado === "pronta"
      ? "Confirmada. A extração está liberada."
      : "Confira o que a IA sugeriu. A extração só libera depois de confirmar.";

    // A planilha: títulos reais e linhas reais.
    const cols = b.colunas_brutas || [];
    $("bases-cabecalho").innerHTML =
      "<tr>" + cols.map((c) =>
        `<th class="border-b border-gray-200 px-2 py-1.5 text-left font-semibold text-gray-600">${c}</th>`
      ).join("") + "</tr>";
    $("bases-corpo").innerHTML = (d.amostra || []).map((linha) =>
      "<tr>" + linha.map((v) =>
        `<td class="border-b border-gray-100 px-2 py-1 text-gray-500">${v === null ? "" : v}</td>`
      ).join("") + "</tr>").join("");

    const opcoes = (sel) => "<option value=''>— não tem —</option>" +
      cols.map((c) => `<option value="${c}" ${c === sel ? "selected" : ""}>${c}</option>`).join("");
    const linha = (campo, rotulo, valor) =>
      `<label class="flex flex-col gap-y-1">
         <span class="text-[12.5px] font-medium text-gray-700">${rotulo}</span>
         <select data-campo="${campo}" class="rounded-md border-gray-300 py-1.5 text-[13px]">${opcoes(valor)}</select>
       </label>`;

    const mapa = b.mapa_colunas || {};
    const ROT = {
      latitude: "Latitude", longitude: "Longitude",
      ligacao: "Instalação / número da ligação",
      endereco: "Endereço (logradouro)", tipo_cliente: "Tipo de cliente",
      situacao: "Situação da ligação (ativa, cortada, inativa…)",
      numero: "Número", bairro: "Bairro", cep: "CEP", cidade: "Cidade",
    };
    $("bases-obrigatorias").innerHTML =
      (d.obrigatorias || []).map((c) => linha(c, ROT[c] || c, mapa[c])).join("");
    $("bases-complementares").innerHTML =
      (d.complementares || []).map((c) => linha(c, ROT[c] || c, mapa[c])).join("");

    // Os valores do tipo, com contagem: sem ela ninguém sabe o que pesa.
    const jaMarcados = new Set((b.tipos_comerciais || []).map(String));
    const sugeridos = new Set(((d.sugestao || {}).tipos || {}).comerciais || []);
    const duvidosos = new Set(((d.sugestao || {}).tipos || {}).duvidosos || []);
    $("bases-tipos").innerHTML = (d.valores_tipo || []).map(([v, n]) => {
      const marcado = jaMarcados.size ? jaMarcados.has(String(v)) : sugeridos.has(v);
      const nota = duvidosos.has(v)
        ? `<span class="ml-1 text-[10px] font-semibold uppercase text-amber-600">a decidir</span>` : "";
      return `<label class="flex items-center gap-x-2 text-[13px]">
                <input type="checkbox" data-tipo="${String(v).replace(/"/g, "&quot;")}"
                       ${marcado ? "checked" : ""} class="rounded border-gray-300">
                <span class="text-gray-700">${v}</span>${nota}
                <span class="ml-auto text-[11.5px] tabular-nums text-gray-400">${n.toLocaleString("pt-BR")}</span>
              </label>`;
    }).join("") || "<p class='text-[12.5px] text-gray-400'>Escolha a coluna de tipo de cliente para ver os valores.</p>";

    $("bases-aviso").textContent = "";
  }

  async function confirmarBase() {
    const mapa = {};
    document.querySelectorAll("#m-bases select[data-campo]").forEach((s) => {
      if (s.value) mapa[s.dataset.campo] = s.value;
    });
    const tipos = [];
    document.querySelectorAll("#m-bases input[data-tipo]:checked")
      .forEach((c) => tipos.push(c.dataset.tipo));

    const b = $("bases-confirmar");
    b.disabled = true;
    try {
      const r = await fetch(`/api/base-cliente/${basesEstado.atual}/confirmar`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mapa_colunas: mapa, tipos_comerciais: tipos }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        // O erro do servidor DIZ o que falta. Mostrar o texto dele é melhor que
        // um "não foi possível" que não ajuda ninguém.
        $("bases-aviso").textContent = d.detail || "não deu para confirmar";
        $("bases-aviso").className = "text-[12.5px] text-red-600";
        return;
      }
      $("bases-aviso").textContent = "Confirmada. Extração liberada.";
      $("bases-aviso").className = "text-[12.5px] text-emerald-600";
      await carregarBases();
    } finally {
      b.disabled = false;
    }
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
    carregarCrachaProxies();
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
    $("btn-bases").addEventListener("click", abrirBases);
    $("bases-confirmar").addEventListener("click", confirmarBase);

    $("btn-desenhar").addEventListener("click", () => {
      estado.modo = "desenho";
      estado.painel = null;
      estado.cidade = null; estado.cod = null;
      estado.temArea = false; estado.pts = [];
      estado.anel = null;
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

    // monitor de proxies
    $("btn-proxies").addEventListener("click", () => {
      abrirModal("m-proxies");
      carregarProxies();          // abre primeiro, busca depois: rede lenta não
    });                           // pode deixar o clique sem resposta
    $("px-janela").addEventListener("change", carregarProxies);
    $("px-busca").addEventListener("input", pxLista);
    $("px-filtro").addEventListener("change", pxLista);

    $("busca-municipio").addEventListener("input", agendarBuscaMunicipio);
    $("btn-parar-run").addEventListener("click", () => pararRun(false));
    $("btn-parar-limpar").addEventListener("click", () => pararRun(true));
    $("busca").addEventListener("input", desenharPois);

    $("btn-limpar").addEventListener("click", async () => {
      estado.modo = null; estado.cidade = null; estado.cod = null;
      estado.temArea = false; estado.desenhando = false; estado.pts = [];
      estado.anel = null;
      camadaDesenho.clearLayers();
      realcarMalha(null);

      // APAGAR APAGA NO BANCO, e não só na tela.
      //
      // Este botão limpava as camadas e o estado, e a área continuava gravada
      // em `area_trabalho`. Quem apagasse e mandasse minerar rodaria a área
      // que acabara de apagar, sem nada na tela sugerindo isso — é o mesmo
      // defeito que já custou uma run de Rio Grande minerando Canoas, do outro
      // lado: lá a tela mudava e o banco não, aqui a tela limpa e o banco não.
      //
      // `polygon: []` é como `/api/area` apaga, e apagar não exige empresa —
      // a própria rota diz isso, para que o root também consiga limpar.
      await fetch("/api/area", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ polygon: [] }),
      }).catch(() => {});

      abrirPainel(null);
      pintarEstado();
      await carregarTudo();
    });

    $("btn-limpar-filtros").addEventListener("click", () => {
      estado.filtros = { origem: [], atributos: [], ia: [], construcao: [] };
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
      estado.anel = area.polygon;
      const poly = ligarFichaDaArea(L.polygon(area.polygon, {
        pane: "paneArea", className: "area-poly",
        color: INDIGO, weight: 2, fillColor: INDIGO, fillOpacity: 0.12,
      })).addTo(camadaDesenho);
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
