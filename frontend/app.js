/* ComercialRadar — frontend (mapa + polígono + tempo real) */
"use strict";

const $ = (id) => document.getElementById(id);

/* ────────────────────────────────────────────────────────────
   Mapa base (tiles claros — mapa limpo)
──────────────────────────────────────────────────────────── */
const map = L.map("map", { zoomControl: false, attributionControl: false })
  .setView([-2.9055, -41.7734], 13); // Parnaíba-PI como partida

/* Panes com z-index explícito — a malha (divisas municipais) é uma camada
   clicável que cobre o mapa inteiro e roubava o clique das faces e quadras,
   levando ao centro do município. Cada coisa no seu andar:
   malha (baixo) < área desenhada < quadras < faces/vias < marcadores (topo,
   sempre clicáveis). */
map.createPane("paneMalha").style.zIndex = 410;
// A ÁREA DESENHADA FICA ACIMA DA MALHA, e isto é conserto de defeito.
//
// Ela morava no `overlayPane` padrão, z-index 400 — abaixo dos 410 da malha.
// O contorno do município era desenhado POR CIMA do polígono que o operador
// acabou de traçar, e comia o clique: clicar no próprio desenho selecionava o
// município e trocava o recorte do mapa inteiro. O tooltip pegajoso da malha
// também aparecia por cima.
//
// 415 e não mais: quadras (620), marcadores (630) e vias (645) continuam
// ganhando dela. Clicar num POI dentro da área tem de abrir o POI, não a área.
map.createPane("paneArea").style.zIndex = 415;
map.createPane("paneQuadras").style.zIndex = 620;
map.createPane("paneMarcadores").style.zIndex = 630;
map.createPane("paneFaces").style.zIndex = 645;   // vias no topo: clicar nelas sempre vence

/* Tirar o mouse do mapa de uma vez (sair pela borda, ir para o painel, trocar de
   janela) não dispara `mouseout` no <path> que está sob o cursor, e o tooltip
   fica preso na tela. Fechar na saída do container cobre todos esses casos. */
map.getContainer().addEventListener("mouseleave", () => fecharTooltips());
window.addEventListener("blur", () => fecharTooltips());

L.control.zoom({ position: "bottomright" }).addTo(map);
L.control.attribution({ position: "bottomright", prefix: false })
  .addAttribution('&copy; OpenStreetMap &middot; CARTO').addTo(map);

/* Bases disponíveis. O SATÉLITE é a fonte de maior zoom, que é onde as
   (Google XYZ lyrs=s) — então o que aparece no mapa é a imagem que gerou os
   quadras e os pontos ficam legíveis. */
/* Os três primeiros são o mapa do Google DE VERDADE, pela Maps JavaScript API —
   cobrada por CARREGAMENTO de mapa, não por tile. O GoogleMutant instancia um
   `google.maps.Map` por baixo e sincroniza com o Leaflet, então tudo o que já
   existe (POIs, quadras, desenho de área, panes) continua funcionando igual.

   A chave vem do .env pelo /api/mapa/config e É VISÍVEL no navegador — não há
   alternativa: quem carrega a JS API é a página. Chave de JS API se protege
   RESTRINGINDO por referrer HTTP e por API no console do Google, não escondendo.

   Se a chave faltar ou o script do Google não carregar, `googleMutant` não
   existe e os fundos caem para Carto, que não depende de conta nenhuma. */
const _mut = (tipo) => (window.L && L.gridLayer && L.gridLayer.googleMutant)
  ? L.gridLayer.googleMutant({ type: tipo, maxZoom: 22 })
  : L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 20, subdomains: "abcd" });

const _carto = (estilo) => L.tileLayer(
  `https://{s}.basemaps.cartocdn.com/${estilo}/{z}/{x}/{y}{r}.png`,
  { maxZoom: 20, subdomains: "abcd" });

/* Cada base sabe se CRIAR. Antes as do Carto guardavam a camada pronta em
   `.layer` e as do Google tinham `tipo`; quem recriava (`camadaBase`) só olhava
   o `tipo` e caía no default "roadmap". Resultado: a base que estivesse ativa
   quando a JS API do Google terminasse de carregar era zerada e RECRIADA como
   Google — o "Claro (sem Google)" virava um mapa do Google, e se a API não
   inicializasse direito ficava sem fundo nenhum. O `escuro` escapava só porque
   nunca era o que estava ativo naquele instante. */
const BASES = {
  limpo:    { nome: "Mapa limpo",      ico: "🗺️", google: true, criar: () => _mut("roadmap") },
  satelite: { nome: "Satélite",        ico: "🛰️", google: true, criar: () => _mut("satellite") },
  hibrido:  { nome: "Satélite + ruas", ico: "🛣️", google: true, criar: () => _mut("hybrid") },
  claro:    { nome: "Claro (sem Google)", ico: "☁️", criar: () => _carto("light_all") },
  escuro:   { nome: "Escuro",           ico: "🌙", criar: () => _carto("dark_all") },
};
let baseAtual = localStorage.getItem("cr_base") || "limpo";
if (!BASES[baseAtual]) baseAtual = "limpo";

/* A camada só é criada quando pedida: as do Google dependem do script da JS API,
   que carrega depois. Criada uma vez, fica guardada em `.layer`. */
function camadaBase(chave) {
  const b = BASES[chave];
  if (!b.layer) b.layer = b.criar();
  return b.layer;
}

/* Carrega a Maps JavaScript API com a chave do .env e só então põe o fundo.
   Sem chave (ou sem internet), `_mut` já devolve o Carto — o mapa nunca fica
   sem fundo. */
(async function carregarFundoGoogle() {
  try {
    const c = await (await fetch("/api/mapa/config")).json();
    if (c.key) {
      await new Promise((ok, falha) => {
        const s = document.createElement("script");
        const mid = c.mapId ? `&map_ids=${encodeURIComponent(c.mapId)}` : "";
        s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(c.key)}`
              + `${mid}&language=pt-BR&region=BR&loading=async`;
        s.async = true; s.onload = ok; s.onerror = falha;
        document.head.appendChild(s);
      });
    }
  } catch { /* segue com o Carto */ }
  // O fundo do Google entra TROCANDO o provisório. Adicionar a camada só aqui,
  // de forma assíncrona, invertia a ordem de execução do resto do app.js e os
  // botões da topbar perdiam o onclick — o menu superior parava de funcionar.
  // Só as bases DO GOOGLE são recriadas: as do Carto já nasceram certas, e
  // trocá-las aqui só piscaria o fundo sem mudar nada.
  if (!BASES[baseAtual].google) return;
  const antigo = BASES[baseAtual].layer;
  BASES[baseAtual].layer = null;               // força recriar, agora com Google
  const novo = camadaBase(baseAtual);
  if (novo !== antigo) {
    novo.addTo(map);
    novo.bringToBack();
    if (antigo && map.hasLayer(antigo)) map.removeLayer(antigo);
  }
})();

/* Fundo PROVISÓRIO, síncrono: o mapa nunca fica sem base, e — mais importante —
   a ordem de execução do app.js continua a mesma de antes. */
camadaBase(baseAtual).addTo(map);
camadaBase(baseAtual).bringToBack();

function trocarBase(chave) {
  if (!BASES[chave] || chave === baseAtual) return;
  map.removeLayer(camadaBase(baseAtual));
  camadaBase(chave).addTo(map);
  camadaBase(chave).bringToBack();           // nunca por cima dos desenhos
  baseAtual = chave;
  localStorage.setItem("cr_base", chave);
  document.querySelectorAll("#base-menu button").forEach((b) =>
    b.classList.toggle("on", b.dataset.base === chave));
  $("base-atual").textContent = BASES[chave].ico;
}

/* Seletor de tipo de mapa — vive na topbar, não flutuando sobre o mapa: os
   cantos já são disputados pelo painel, pelos stats, pelos filtros e pelo zoom,
   todos com z-index maior, e o botão ficava escondido atrás deles. */
(function montarSeletorBase() {
  const wrap = document.createElement("div");
  wrap.id = "base-switch";
  wrap.innerHTML =
    `<button id="base-btn" title="Tipo de mapa"><span id="base-atual">${BASES[baseAtual].ico}</span>` +
    `<span class="base-cap">Mapa</span><span class="base-seta">▾</span></button>` +
    `<div id="base-menu" class="hidden">` +
    Object.entries(BASES).map(([k, v]) =>
      `<button data-base="${k}" class="${k === baseAtual ? "on" : ""}">${v.ico} ${v.nome}</button>`).join("") +
    `</div>`;
  const alvo = document.querySelector("#topbar .top-right") || document.body;
  alvo.insertBefore(wrap, alvo.firstChild);
  $("base-btn").onclick = (e) => { e.stopPropagation(); $("base-menu").classList.toggle("hidden"); };
  wrap.querySelectorAll("#base-menu button").forEach((b) => {
    b.onclick = () => { trocarBase(b.dataset.base); $("base-menu").classList.add("hidden"); };
  });
  document.addEventListener("click", () => $("base-menu").classList.add("hidden"));
})();

/* ────────────────────────────────────────────────────────────
   Estilo dos markers por categoria (padrão Google/Waze)
──────────────────────────────────────────────────────────── */
const CATS = [
  { re: /restaurante|lanchonete|pizzari|hamburg|churrasc|comida|alimenta|café|cafeteria|padaria|sorveter|açai|acai|bar\b|petiscaria|self service|marmita/i, cor: "#ba5a08", emo: "🍽️" },
  { re: /supermercado|mercado|mercearia|mercadinho|atacad|hortifruti|conveni|frios|distribuidora de bebidas|bebidas/i, cor: "#1c853a", emo: "🛒" },
  { re: /farm[aá]cia|drogaria|hospital|cl[ií]nica|laborat[oó]rio|dentista|odonto|m[eé]dic|sa[uú]de|fisioter|psicol|veterin|pet/i, cor: "#d93025", emo: "💊" },
  { re: /escola|col[eé]gio|creche|faculdade|universi|curso|educa/i, cor: "#5b3cc4", emo: "🎓" },
  { re: /hotel|pousada|hostel|motel|hospedagem/i, cor: "#12805c", emo: "🛏️" },
  { re: /banco|caixa eletr|lot[eé]rica|financ|cr[eé]dito|seguros/i, cor: "#186a3b", emo: "🏦" },
  { re: /oficina|mec[aâ]nica|auto ?pe[cç]as|autope[cç]as|borracharia|lava.?jato|concession|moto|el[eé]trica automotiva|posto de (comb|gas)/i, cor: "#455a75", emo: "🔧" },
  { re: /sal[aã]o|barbearia|beleza|est[eé]tica|manicure|cabele/i, cor: "#cb3d84", emo: "✂️" },
  { re: /academia|gym|crossfit|esporte|fitness/i, cor: "#00838f", emo: "💪" },
  { re: /igreja|templo|par[oó]quia|assembleia/i, cor: "#8d6e63", emo: "⛪" },
  { re: /constru|madeirei|ferragem|material|tinta|vidra[cç]|serralheria|marmoraria/i, cor: "#a05c10", emo: "🧱" },
  { re: /loja|boutique|magazine|variedade|utilidade|presente|papelaria|livraria|cal[cç]ado|roupa|confec|m[oó]veis|eletro|celular|inform[aá]tica|[oó]tica|joalheria|relojoaria|shopping/i, cor: "#8430ce", emo: "🛍️" },
];
function catInfo(cat, fonte) {
  /* O ACHADO DA IA VEM ANTES DA CATEGORIA.
   *
   * Estes pontos não existiam em cadastro nenhum: a IA os leu numa parede, e a
   * coordenada é APROXIMADA — deslocada pelo lado em que o comércio apareceu no
   * quadro. Pintá-los como qualquer outro faria um ponto que ninguém conferiu
   * parecer tão firme quanto um vindo da planilha do cliente. O losango âmbar
   * diz, sem legenda, que aquilo ali é achado a confirmar. */
  if (fonte === "ia_fachada") return { cor: "#b45309", emo: "🔎", ia: true };
  const c = (cat || "").toString();
  for (const k of CATS) if (k.re.test(c)) return k;
  if (fonte === "descoberto") return { cor: "#6d28d9", emo: "✨" };
  return { cor: "#1a73e8", emo: "📍" };
}

function makeIcon(poi, novo) {
  const k = catInfo(poi.categoria, poi.fonte);
  const iaf = k.ia ? " m-ia-fachada" : "";
  const ver = poi.veredito === "aprovado" ? "v-ok"
            : poi.veredito === "reprovado" ? "v-no" : "";
  const rec = poi.recomendar_visita ? " rec" : "";
  return L.divIcon({
    className: "pin-wrap",
    html: `<div class="pin ${novo ? "novo" : ""} ${ver}${rec}${iaf}" style="--c:${k.cor}">
             <div class="pin-head">${k.emo}</div><div class="pin-tail"></div>
             ${poi.recomendar_visita ? '<div class="pin-star">★</div>' : ""}
           </div>`,
    iconSize: [34, 43], iconAnchor: [17, 43],
  });
}

const cluster = L.markerClusterGroup({
  showCoverageOnHover: false, maxClusterRadius: 54, spiderfyOnMaxZoom: true,
  iconCreateFunction(c) {
    const n = c.getChildCount();
    const sz = n < 50 ? 38 : n < 300 ? 46 : 54;
    const cls = n < 50 ? "" : n < 300 ? "md" : "lg";
    return L.divIcon({
      html: `<div class="cluster ${cls}" style="width:${sz}px;height:${sz}px;font-size:${n < 50 ? 13 : 14}px">${n >= 1000 ? (n / 1000).toFixed(1) + "k" : n}</div>`,
      className: "", iconSize: [sz, sz],
    });
  },
});
map.addLayer(cluster);

const markers = new Map(); // poi_id -> marker
const allPois = new Map(); // poi_id -> poi (fonte de verdade p/ os filtros)

/* Origem do dado — de qual camada do processo o POI veio */
const ORIGENS = [
  // Vem ANTES de "maps": o POI da captura também tem status "ok" (ele foi
  // confirmado no Maps), então o teste de lá o pegaria primeiro e a origem
  // sumiria. É o motor padrão da mineração — merece chip próprio, e não o
  // balde cinza "Outros", onde ninguém procura o que acabou de minerar.
  { key: "captura", label: "Captura + OCR", cor: "#0f766e", teste: (p) => p.fonte === "pipeline" },
  { key: "maps",    label: "Maps direto",  cor: "#1a73e8", teste: (p) => p.status === "ok" },
  { key: "proximo", label: "Vizinhos",     cor: "#00838f", teste: (p) => p.status === "recuperado_proximo" },
  { key: "ia",      label: "OpenAI",       cor: "#0d8668", teste: (p) => p.status === "recuperado_ia" },
  { key: "gemini",  label: "Gemini",       cor: "#a142f4", teste: (p) => p.status === "recuperado_gemini" },
  { key: "web",     label: "Web",          cor: "#d81b60", teste: (p) => p.status === "recuperado_web" },
  { key: "desc",    label: "Descobertos",  cor: "#ba5a08", teste: (p) => p.status === "descoberto" || p.status === "minerado" },
  // Chip próprio, e antes do fallback: é a lista que o supervisor precisa
  // trabalhar, e no balde "Outros" ninguém a encontraria.
  { key: "iafach",  label: "Lidos na parede", cor: "#b45309", teste: (p) => p.fonte === "ia_fachada" },
  { key: "outros",  label: "Outros",       cor: "#5f6368", teste: () => true }, // fallback
];
const filtrosAtivos = new Set(ORIGENS.map((o) => o.key));

/* Filtros por ATRIBUTO (combinam com os de origem, em AND). null = inativo. */
const ATRIBUTOS = [
  { key: "cnpj", label: "🏢 Com CNPJ", teste: (p) => p.tem_cnpj },
  { key: "tel", label: "📞 Com telefone", teste: (p) => p.tem_tel },
  { key: "foto", label: "📷 Com foto", teste: (p) => p.tem_foto },
  { key: "sv", label: "📸 Street View", teste: (p) => p.tem_sv },
  { key: "semtel", label: "⚠️ Sem telefone", teste: (p) => !p.tem_tel },
  /* MULTIORIGEM — o ponto sustentado por mais de uma base.
   *
   * É onde a fusão pode ter errado, e por isso é o primeiro recorte que o
   * operador precisa: medido no RS, 66,2% das fusões suspeitas uniram
   * estabelecimentos DISTINTOS. Ponto de fonte única não tem o que revisar —
   * o registro É o ponto, e filtrá-lo junto só esconderia os que importam.
   *
   * Abre a ficha na aba da fonte que entrou com menor confiança, que é onde a
   * dúvida mora. */
  { key: "multi", label: "🔗 Multiorigem", teste: (p) => p.multiorigem },
];
const atributosAtivos = new Set(); // vazio = não filtra por atributo

/* PRECISÃO DA COORDENADA — o quão perto da porta o ponto está.
 *
 * Vive numa fileira PRÓPRIA, e não entre os atributos, porque responde outra
 * pergunta. "Com CNPJ" diz o que se sabe do negócio; isto diz o quanto se pode
 * confiar no lugar — e é essa a pergunta de quem monta uma rota de campo.
 *
 * `desconhecida` NÃO é vermelho, de propósito: ela não significa ruim,
 * significa não conferido. Pintar de vermelho faria descartar 13 mil pontos
 * que podem ser ótimos. */
const PRECISOES = [
  { key: "porta",        label: "🎯 Porta",         cor: "#1c853a", raio: "~15 m" },
  { key: "porta_aprox",  label: "🏢 Prédio",        cor: "#1f7a4d", raio: "~40 m" },
  { key: "via",          label: "🛣️ Rua",           cor: "#976e09", raio: "~150 m" },
  { key: "bairro",       label: "🗺️ Bairro",        cor: "#ba5a08", raio: "~800 m" },
  { key: "municipio",    label: "🏙️ Município",     cor: "#c0392b", raio: "~5 km" },
  { key: "desconhecida", label: "❓ Não declarada", cor: "#6f767f", raio: "—" },
];
const precisaoFiltro = new Set();   // vazio = não filtra por precisão

/* Filtros por ANÁLISE DA IA (descrever_imagens): veredito é seleção única
   (aprovado XOR reprovado, ou todos); recomendar/revisar narram em AND. */
let vereditoFiltro = null;         // null | 'aprovado' | 'reprovado'
const flagsIA = new Set();         // 'recomendar' | 'revisar'

/* Seleção de MUNICÍPIO: nada por padrão. O usuário clica no polígono do município
   no mapa; só então os POIs e as informações daquele município aparecem. */
let municipioSel = null;           // nome normalizado (ex.: 'parnaiba') | null = nada
let municipioNome = null;          // nome de exibição (ex.: 'Parnaíba')
let municipioLayer = null;         // polígono selecionado (realce/limpeza)
const _normCidade = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").trim().toLowerCase();

/* Normaliza logradouro como o backend (norm_via): expande abreviações e tira
   ruído, para "R. das Angélicas" == "RUA DAS ANGELICAS" e a discrepância só
   aparecer quando as ruas forem REALMENTE diferentes. */
const _VIA_ABREV = { r: "rua", av: "avenida", trav: "travessa", tv: "travessa", pc: "praca",
  pca: "praca", al: "alameda", rod: "rodovia", estr: "estrada", dr: "doutor", dra: "doutora",
  prof: "professor", pe: "padre", cel: "coronel", gen: "general", mal: "marechal" };
const _VIA_RUIDO = new Set(["de", "da", "do", "das", "dos", "e"]);
const _normVia = (s) => _normCidade(s).replace(/[.,\-/]/g, " ").split(/\s+/)
  .map((p) => _VIA_ABREV[p] || p).filter((p) => p && !_VIA_RUIDO.has(p)).join(" ");
/* Anel da área desenhada, em [[lat, lng], ...]. Memorizado por identidade da
   camada: `dentroDaArea` roda por POI a cada render, e são milhares. */
let _anelCache = { layer: null, anel: null };
function anelArea() {
  if (!areaLayer) return null;
  if (_anelCache.layer !== areaLayer) {
    const ll = (areaLayer.getLatLngs() || [])[0] || [];
    _anelCache = { layer: areaLayer, anel: ll.map((p) => [p.lat, p.lng]) };
  }
  return _anelCache.anel;
}

function dentroDaArea(p) {
  const a = anelArea();
  if (!a || a.length < 3 || p.lat == null || p.lng == null) return false;
  let dentro = false;                       // ray casting, igual ao do backend
  for (let i = 0, j = a.length - 1; i < a.length; j = i++) {
    const [yi, xi] = a[i], [yj, xj] = a[j];
    if ((yi > p.lat) !== (yj > p.lat) &&
        p.lng < ((xj - xi) * (p.lat - yi)) / ((yj - yi) || 1e-12) + xi) dentro = !dentro;
  }
  return dentro;
}

/* Base dos filtros e das contagens. O município continua mandando quando há um
   escolhido; sem ele, vale a ÁREA DE TRABALHO desenhada — quem acabou de minerar
   um polígono quer ver o que saiu dali, e exigir um clique a mais no município
   fazia a tela inteira zerar em cima dos POIs recém-gravados. */
function poisBase() {
  const todos = [...allPois.values()];
  if (municipioSel) return todos.filter((p) => _normCidade(p.cidade) === municipioSel);
  return anelArea() ? todos.filter(dentroDaArea) : [];
}

function origemDe(p) {
  for (const o of ORIGENS) if (o.teste(p)) return o.key;
  return "outros";
}

function passaAtributos(p) {
  for (const a of ATRIBUTOS) if (atributosAtivos.has(a.key) && !a.teste(p)) return false;
  return true;
}

function visivel(p) {
  // mesma regra do poisBase: município escolhido manda; sem ele, a área desenhada
  if (municipioSel ? _normCidade(p.cidade) !== municipioSel : !dentroDaArea(p)) return false;
  if (!filtrosAtivos.has(origemDe(p)) || !passaAtributos(p)) return false;
  // Vazio = não filtra. Com classes marcadas, só elas aparecem — é assim que
  // se monta uma rota de campo só com pontos em que se pode confiar.
  if (precisaoFiltro.size
      && !precisaoFiltro.has(p.coord_precisao || "desconhecida")) return false;
  if (vereditoFiltro && p.veredito !== vereditoFiltro) return false;
  if (flagsIA.has("recomendar") && !p.recomendar_visita) return false;
  if (flagsIA.has("revisar") && !p.revisar_manual) return false;
  return true;
}

function _criarMarker(poi, novo) {
  const m = L.marker([poi.lat, poi.lng], { icon: makeIcon(poi, novo), title: poi.nome || "" });
  m.on("click", () => abrirPoi(poi));
  cluster.addLayer(m);
  markers.set(poi.id, m);
}

function addPoi(poi, novo = false) {
  if (poi.lat == null || poi.lng == null || poi.id == null) return;
  // O ingestor reingere por delete+recreate, então o MESMO lugar volta com id
  // novo. Sem tirar a versão antiga, o mapa acumulava os dois: durante um job de
  // 26 POIs o chip marcava 37, contando fantasmas de linhas que já não existem.
  if (poi.place_id) {
    for (const [id, p] of allPois) {
      if (id !== poi.id && p.place_id === poi.place_id) {
        allPois.delete(id);
        if (markers.has(id)) { cluster.removeLayer(markers.get(id)); markers.delete(id); }
      }
    }
  }
  allPois.set(poi.id, poi);
  if (markers.has(poi.id)) {
    cluster.removeLayer(markers.get(poi.id));
    markers.delete(poi.id);
  }
  if (visivel(poi)) _criarMarker(poi, novo);
  // o badge agora conta o banco por município: POI que chega durante o job
  // muda essa conta, e sem isto ela ficaria congelada no total do carregamento
  if (novo) { renderChips(); atualizarBannerMunicipio(); }
}

function aplicarFiltro() {
  cluster.clearLayers(); markers.clear();
  for (const poi of allPois.values())
    if (visivel(poi)) _criarMarker(poi, false);
  renderChips();
}

async function carregarPois(fit = false) {
  try {
    const r = await fetch("/api/pois");
    const { pois } = await r.json();
    cluster.clearLayers(); markers.clear(); allPois.clear();
    pois.forEach((p) => { if (p.id != null && p.lat != null) allPois.set(p.id, p); });
    aplicarFiltro();          // nada aparece até um município ser selecionado (visivel gateia)
    atualizarBannerMunicipio();
  } catch { toast("Falha ao carregar POIs do banco", "err"); }
}

/* ────────────────────────────────────────────────────────────
   Chips de filtro por origem (rodapé do mapa) + toggle de divisas
──────────────────────────────────────────────────────────── */
function renderChips() {
  const base = poisBase();                 // POIs do município selecionado (ou [] se nenhum)
  const counts = {};
  for (const poi of base) counts[origemDe(poi)] = (counts[origemDe(poi)] || 0) + 1;
  // contagem por atributo respeita o filtro de ORIGEM atual (mostra o que sobraria)
  const attrCounts = {};
  for (const a of ATRIBUTOS) attrCounts[a.key] = 0;
  for (const poi of base) {
    if (!filtrosAtivos.has(origemDe(poi))) continue;
    for (const a of ATRIBUTOS) if (a.teste(poi)) attrCounts[a.key]++;
  }
  const box = $("filtros");
  let html = '<div class="frow">';
  for (const o of ORIGENS) {
    const n = counts[o.key] || 0;
    if (!n && o.key === "outros") continue;
    const on = filtrosAtivos.has(o.key);
    html += `<button class="fchip ${on ? "on" : ""}" data-k="${o.key}" style="--c:${o.cor}">
               <span class="fdot"></span>${o.label} <span class="n">${n.toLocaleString("pt-BR")}</span>
             </button>`;
  }
  html += `<button class="fchip sep ${malhaVisivel ? "on" : ""}" data-k="_malha" style="--c:#475569">🗺️ Divisas</button>`;
  // linha de ANÁLISE DA IA (veredito + recomendação) — base = município selecionado
  const pois = base;
  const cAp = pois.filter((p) => p.veredito === "aprovado").length;
  const cRp = pois.filter((p) => p.veredito === "reprovado").length;
  const cRec = pois.filter((p) => p.recomendar_visita).length;
  const cRev = pois.filter((p) => p.revisar_manual).length;
  if (cAp || cRp) {
    html += '</div><div class="frow frow-ia">';
    html += `<span class="ia-tag">🤖 IA</span>`;
    html += `<button class="fchip ia ${vereditoFiltro === "aprovado" ? "on" : ""}" data-v="aprovado" style="--c:#1f7a4d">✅ Aprovados <span class="n">${cAp.toLocaleString("pt-BR")}</span></button>`;
    html += `<button class="fchip ia ${vereditoFiltro === "reprovado" ? "on" : ""}" data-v="reprovado" style="--c:#c0392b">❌ Reprovados <span class="n">${cRp.toLocaleString("pt-BR")}</span></button>`;
    html += `<button class="fchip ia ${flagsIA.has("recomendar") ? "on" : ""}" data-f="recomendar" style="--c:#976e09">⭐ Recomendar visita <span class="n">${cRec.toLocaleString("pt-BR")}</span></button>`;
    if (cRev) html += `<button class="fchip ia ${flagsIA.has("revisar") ? "on" : ""}" data-f="revisar" style="--c:#ba5a08">🔍 Revisar manual <span class="n">${cRev}</span></button>`;
  }
  html += '</div><div class="frow frow-attr">';
  for (const a of ATRIBUTOS) {
    const on = atributosAtivos.has(a.key);
    html += `<button class="fchip attr ${on ? "on" : ""}" data-a="${a.key}" style="--c:#1f7a4d">
               ${a.label} <span class="n">${(attrCounts[a.key] || 0).toLocaleString("pt-BR")}</span></button>`;
  }
  /* A fileira da PRECISÃO só aparece quando há mais de uma classe na tela.
     Numa base inteira de precisão `porta`, um filtro com uma opção só é ruído
     ocupando a mesma altura de um filtro útil. */
  const contaPrec = {};
  for (const p of base) {
    const k = p.coord_precisao || "desconhecida";
    contaPrec[k] = (contaPrec[k] || 0) + 1;
  }
  const classesPrec = PRECISOES.filter((c) => contaPrec[c.key]);
  if (classesPrec.length > 1) {
    html += '</div><div class="frow frow-prec">';
    html += '<span class="ia-tag" title="Quão perto da porta o ponto está">📍 Precisão</span>';
    for (const c of classesPrec) {
      const on = precisaoFiltro.has(c.key);
      html += `<button class="fchip prec ${on ? "on" : ""}" data-p="${c.key}"
                 style="--c:${c.cor}" title="${c.raio}">
                 ${c.label} <span class="n">${contaPrec[c.key].toLocaleString("pt-BR")}</span>
               </button>`;
    }
  }

  html += "</div>";
  box.innerHTML = html;
  box.querySelectorAll(".fchip[data-p]").forEach((b) => {
    b.onclick = () => {
      const k = b.dataset.p;
      if (precisaoFiltro.has(k)) precisaoFiltro.delete(k);
      else precisaoFiltro.add(k);
      // `aplicarFiltro` redesenha o cluster e ja chama `renderChips` no fim —
      // era `desenhar()` + `montarFiltros()`, dois nomes que nao existem neste
      // arquivo. O clique estourava no console e o filtro nunca valia.
      aplicarFiltro();
    };
  });
  box.querySelectorAll(".fchip[data-k]").forEach((b) => {
    b.onclick = () => {
      const k = b.dataset.k;
      if (k === "_malha") return toggleMalha();
      if (filtrosAtivos.has(k)) filtrosAtivos.delete(k); else filtrosAtivos.add(k);
      aplicarFiltro();
    };
  });
  box.querySelectorAll(".fchip[data-a]").forEach((b) => {
    b.onclick = () => {
      const a = b.dataset.a;
      if (atributosAtivos.has(a)) atributosAtivos.delete(a); else atributosAtivos.add(a);
      aplicarFiltro();
    };
  });
  box.querySelectorAll(".fchip[data-v]").forEach((b) => {
    b.onclick = () => {
      vereditoFiltro = vereditoFiltro === b.dataset.v ? null : b.dataset.v;
      aplicarFiltro();
    };
  });
  box.querySelectorAll(".fchip[data-f]").forEach((b) => {
    b.onclick = () => {
      const f = b.dataset.f;
      if (flagsIA.has(f)) flagsIA.delete(f); else flagsIA.add(f);
      aplicarFiltro();
    };
  });
}

/* ────────────────────────────────────────────────────────────
   Malha territorial (IBGE) — ênfase nas divisas municipais
──────────────────────────────────────────────────────────── */
let malhaGrupo = null;                  // uma camada geoJSON por UF já baixada
const malhaUFs = new Map();             // sigla -> camada
let malhaVisivel = true;

const MALHA_STYLE = { color: "#5b6b80", weight: 1.2, opacity: 0.9, fillColor: "#5b6b80", fillOpacity: 0.03 };

/* A malha segue o mapa: pede a UF do centro e acumula os estados visitados.
   Antes vinha só a UF majoritária do banco (PI), então em PE não havia divisa. */
async function carregarMalha(param) {
  if (!malhaGrupo) malhaGrupo = L.layerGroup();
  let url = "/api/malha";
  if (typeof param === "string" && param) {
    if (malhaUFs.has(param)) return;
    url += `?uf=${param}`;
  } else if (param && param.lat !== undefined) {
    url += `?lat=${param.lat.toFixed(4)}&lng=${param.lng.toFixed(4)}`;
  }
  try {
    const gj = await (await fetch(url)).json();
    if (gj.erro || !gj.features) return;
    const sig = gj.uf || "?";
    if (malhaUFs.has(sig)) return;                       // já desenhada
    const camada = L.geoJSON(gj, {
      style: MALHA_STYLE, pane: "paneMalha",
      onEachFeature: (f, l) => {
        l._malhaDono = null;                             // preenchido abaixo
        l.bindTooltip(f.properties?.nome || "", { sticky: true, direction: "top", className: "muni-tip" });
        l.on("mouseover", () => { if (l !== municipioLayer) l.setStyle({ weight: 2.4, color: "#334155", fillOpacity: 0.08 }); });
        l.on("mouseout", () => { if (l !== municipioLayer) resetMalha(l); });
        l.on("click", () => selecionarMunicipio(f.properties?.nome, l));
      },
    });
    camada.eachLayer((l) => { l._malhaDono = camada; });
    malhaUFs.set(sig, camada);
    malhaGrupo.addLayer(camada);
    if (malhaVisivel && !map.hasLayer(malhaGrupo)) malhaGrupo.addTo(map);
    // primeira UF, sem área/município: enquadra nela (os POIs só aparecem ao clicar)
    if (malhaUFs.size === 1 && !areaLayer && !municipioSel) {
      try { map.fitBounds(camada.getBounds().pad(0.05)); } catch { /* ok */ }
    }
    renderChips();
  } catch { /* IBGE fora do ar — segue sem a malha */ }
}

/* devolve o estilo padrão a um município (cada um sabe de que camada veio) */
function resetMalha(l) {
  if (l && l._malhaDono) l._malhaDono.resetStyle(l);
  else if (l && l.setStyle) l.setStyle(MALHA_STYLE);
}

/* ao parar de navegar, garante a malha da UF sob o centro do mapa */
let malhaTimer = null;
function malhaSegueMapa() {
  clearTimeout(malhaTimer);
  malhaTimer = setTimeout(() => {
    if (map.getZoom() < 6) return;                       // no mundo inteiro não faz sentido
    const c = map.getCenter();
    carregarMalha({ lat: c.lat, lng: c.lng });
  }, 400);
}

/* Seleção de município por clique no polígono da malha */
const MUNI_STYLE_SEL = { color: "#0e7490", weight: 3, opacity: 1, fillColor: "#0e7490", fillOpacity: 0.07 };

function selecionarMunicipio(nome, layer) {
  const norm = _normCidade(nome);
  if (!norm) return;
  if (municipioSel === norm) return limparMunicipio();   // clicar de novo desmarca
  if (municipioLayer) resetMalha(municipioLayer);
  municipioSel = norm; municipioNome = nome; municipioLayer = layer;
  layer.setStyle(MUNI_STYLE_SEL); layer.bringToFront();
  aplicarFiltro();
  try { map.fitBounds(layer.getBounds().pad(0.12), { maxZoom: 15 }); } catch { /* ok */ }
  carregarStats();
  atualizarBannerMunicipio();
}

window.limparMunicipio = function () {
  if (municipioLayer) resetMalha(municipioLayer);
  municipioSel = null; municipioNome = null; municipioLayer = null;
  aplicarFiltro();
  carregarStats();
  atualizarBannerMunicipio();
};

function atualizarBannerMunicipio() {
  const badge = document.getElementById("db-badge");
  if (!badge) return;
  if (municipioSel) {
    const n = poisBase().length;
    badge.classList.add("sel");
    badge.innerHTML = `<span class="dot"></span>📍 <b>${esc(municipioNome)}</b> · <span id="db-count">${n.toLocaleString("pt-BR")}</span> POIs`
      + ` <button class="muni-x" title="Limpar seleção" onclick="limparMunicipio()">✕</button>`;
  } else {
    // O vazio precisa dizer que existe dado. Sem seleção, TODA contagem do
    // painel zera (poisBase devolve []), e com o mapa parado em cima dos POIs
    // recém-minerados a leitura natural é "não gravou nada" — foi o que
    // aconteceu duas vezes. Aqui o badge deixa de esconder o que há no banco.
    badge.classList.remove("sel");
    const porCidade = new Map();
    for (const p of allPois.values()) {
      const nome = (p.cidade || "").trim() || "sem cidade";
      porCidade.set(nome, (porCidade.get(nome) || 0) + 1);
    }
    let extra = "";
    if (allPois.size) {
      const ord = [...porCidade].sort((a, b) => b[1] - a[1]);
      const topo = ord.slice(0, 3)
        .map(([c, n]) => `${esc(c)} ${n.toLocaleString("pt-BR")}`).join(" · ");
      const resto = ord.length > 3 ? ` · +${ord.length - 3}` : "";
      extra = ` — <b>${allPois.size.toLocaleString("pt-BR")}</b> no banco `
            + `<small>(${topo}${resto})</small>`;
    }
    // havendo área desenhada, ela JÁ é o recorte em vigor: o badge tem de dizer
    // o que está sendo mostrado, e não pedir um clique que não é mais necessário
    const naArea = anelArea() ? poisBase().length : 0;
    badge.innerHTML = anelArea()
      ? `<span class="dot"></span>📐 <b>Área de trabalho</b> · `
        + `<span id="db-count">${naArea.toLocaleString("pt-BR")}</span> POIs${extra}`
      : `<span class="dot"></span>Clique num <b>município</b> no mapa${extra}`;
  }
}

function toggleMalha() {
  malhaVisivel = !malhaVisivel;
  if (malhaGrupo) {
    if (malhaVisivel) malhaGrupo.addTo(map); else map.removeLayer(malhaGrupo);
  }
  renderChips();
}

/* ────────────────────────────────────────────────────────────
   Modal do POI
──────────────────────────────────────────────────────────── */
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

/* LEITURA DE FACHADA (avaliar_fachada.py + skill leitura-fachada-cadastral).
   Mostra a evidência, não só a conclusão: quem vai decidir uma revisão tarifária
   precisa ver de onde saiu o número e o quanto a fonte aguenta. */
const OPORT_ROT = {
  MULTIPLAS_UCS_MESMO_ENDERECO: "Vários medidores de energia",
  DIVERGENCIA_UC_ECONOMIAS: "UCs elétricas × economias cadastradas",
  MULTIPLAS_UNIDADES_FISICAS: "Várias unidades físicas",
  ECONOMIAS_OCULTAS_POTENCIAL: "Economias ocultas",
  USO_COMERCIAL_NAO_CADASTRADO: "Comércio não cadastrado",
  USO_MISTO_POTENCIAL: "Uso misto",
  ATIVIDADE_DOMICILIAR_POTENCIAL: "Atividade dentro de residência",
  ESGOTO_SEM_COBRANCA_POTENCIAL: "Esgoto sem cobrança",
  DIVERGENCIA_NUMERO_ENDERECO: "Número diverge do cadastro",
  AREA_DIVERGENTE_POTENCIAL: "Área divergente",
};
const NIVEL_ROT = {
  achado_convergente: ["Achado convergente", "ok", "duas fontes independentes"],
  sinal_imagem: ["Sinal de imagem", "med", "uma fonte só — falta convergir"],
  contradicao: ["Contradição", "baixo", "as fontes discordam"],
};

function blocoFachada(f) {
  const st = {
    aprovado: ["✅", "Leitura aprovada no gate da skill", "ok"],
    reprovado: ["⚠️", "Leitura fora do gate — use com ressalva", "med"],
    inapto: ["🚫", "Imagem não sustenta leitura", "baixo"],
    fora_escopo: ["🚫", "A imagem não é de imóvel", "baixo"],
  }[f.status] || ["•", f.status, ""];

  let h = `<div class="m-sec-title">🏠 Leitura de fachada (cadastral)</div>`;
  h += `<div class="fa-cab"><span class="tag ${st[2]}">${st[0]} ${esc(st[1])}</span>`
     + `<small>${esc(f.modelo || "")}${f.data_imagem
         ? " · imagem de " + esc(String(f.data_imagem).slice(0, 7)) : " · sem data do panorama"}</small></div>`;

  if (f.status === "fora_escopo" || f.status === "inapto") {
    h += `<div class="m-rows"><div class="m-row"><span class="ico">ℹ️</span><span>${
      esc((f.alertas[0] || {}).descricao || "sem leitura aproveitável")}</span></div></div>`;
    return h;
  }

  h += `<div class="m-rows">`;
  const linha = (ico, rot, val) => val || val === 0
    ? `<div class="m-row"><span class="ico">${ico}</span><span><b>${rot}:</b> ${esc(String(val))}</span></div>` : "";
  h += linha("🏷️", "Uso observado", f.uso_observado);
  h += linha("🏢", "Tipologia", (f.tipologia || "").replace(/_/g, " "));
  if (f.estabelecimento || f.letreiro)
    h += linha("📣", "No letreiro", [f.estabelecimento, f.letreiro].filter(Boolean).join(" — "));
  h += linha("🧭", "O que funciona ali", f.descricao);
  // as quatro medidas SEPARADAS: a skill proíbe fundi-las, e é a divergência
  // entre elas que vira achado
  const med = [
    f.unidades_fisicas != null ? `${f.unidades_fisicas} unidade(s) física(s)` : null,
    f.ucs_energia != null ? `${f.ucs_energia} medidor(es) de energia` : null,
    f.hidrometros != null ? `${f.hidrometros} hidrômetro(s)` : null,
    f.economias_base != null ? `${f.economias_base} economia(s) no cadastro` : null,
  ].filter(Boolean);
  if (med.length) h += linha("🔢", "Contagens", med.join(" · "));
  if (f.numero_lido)
    h += linha(f.numero_confere === false ? "❗" : "🔟",
               "Número na fachada",
               f.numero_lido + (f.numero_confere === false ? " (diverge do cadastro)"
                              : f.numero_confere ? " (confere)" : ""));
  if (f.atividade_no_alvo && f.atividade_no_alvo !== "no_imovel_alvo")
    h += linha("↔️", "Atenção", `a atividade lida é ${f.atividade_no_alvo.replace(/_/g, " ")}`);
  h += `</div>`;

  const ops = f.oportunidades || [];
  if (ops.length) {
    h += `<div class="m-sec-title" style="margin-top:12px">Oportunidades cadastrais</div>`;
    h += ops.map((o) => {
      const n = NIVEL_ROT[o.nivel_evidencia] || ["", "", ""];
      return `<div class="fa-op">
        <div class="fa-op-cab"><b>${esc(OPORT_ROT[o.codigo] || o.codigo)}</b>
          <span class="tag ${n[1]}" title="${esc(n[2])}">${esc(n[0])}</span></div>
        <div class="fa-op-ev">${esc(o.evidencia || "")}</div>
        <div class="fa-op-pe">confiança ${(o.confianca ?? 0).toFixed(2)} ·
          ${esc(o.acao_sugerida || "")}</div>
      </div>`;
    }).join("");
  }
  return h;
}

function stars(v) {
  const n = Math.round(parseFloat(String(v).replace(",", ".")) || 0);
  return "★".repeat(Math.min(n, 5)) + "☆".repeat(Math.max(0, 5 - n));
}

const STATUS_LABEL = {
  ok: "Encontrado no Maps", recuperado_proximo: "Recuperado (vizinhos)",
  recuperado_ia: "Recuperado (OpenAI)", recuperado_gemini: "Recuperado (Gemini)",
  recuperado_web: "Recuperado (web)", descoberto: "Descoberto", minerado: "Minerado",
};

/* De onde veio o endereço/dados obtidos (selo ao lado do endereço) */
const FONTE_END = {
  maps: { label: "Google Maps", cor: "#1a73e8" },
  web: { label: "Web/Receita", cor: "#d81b60" },
  gemini: { label: "Gemini", cor: "#a142f4" },
  ok: { label: "Google Maps", cor: "#1a73e8" },
  recuperado_proximo: { label: "Google Maps", cor: "#1a73e8" },
};

/* normaliza endereço para comparar planilha x obtido (ignora acento/pontuação/caixa) */
function _normEnd(s) {
  return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]/g, "");
}

/* CLICAR NUM PONTO ABRE A BANCADA, em tela cheia.
 *
 * Antes abria um modal pequeno no centro. O modal foi crescendo — ganhou fotos,
 * analise da IA, abas por fonte — e continuava sendo um modal: 620 px no meio
 * da tela para o trabalho que a pessoa passa o dia fazendo.
 *
 * A bancada (`assets/modelo_frontend`) e a tela desse trabalho: lateral com a
 * fila, regua de probabilidade por fonte, tabela de cruzamento, mapa por
 * camadas e barra de decisao com atalho de teclado.
 *
 * O modal continua existindo e alcancavel — `abrirPoiModal` — porque a lista de
 * quadras e o fluxo de revisao ainda o usam, e trocar isso agora mudaria
 * comportamento que nao foi pedido. */
function abrirPoi(poi) {
  const id = poi && poi.id;
  if (id == null) return abrirPoiModal(poi);   // sem id nao ha o que abrir
  location.href = "/bancada?poi=" + encodeURIComponent(id);
}

async function abrirPoiModal(poiLeve) {
  let poi = poiLeve;
  if (poiLeve.id != null) {
    try { poi = await (await fetch(`/api/pois/${poiLeve.id}`)).json(); } catch { /* usa o leve */ }
  }
  const k = catInfo(poi.categoria, poi.fonte);
  const fotos = (poi.fotos || []).slice(0, 10);
  const avaliacao = poi.avaliacao ? parseFloat(String(poi.avaliacao).replace(",", ".")) : null;

  let html = "";
  // A fachada vem do BANCO (`streetview_imgs`, ângulo 'facade'), não da pasta
  // streetview/. Eram 2,6 GB de arquivo dentro do diretório do sistema
  // duplicando o que já estava gravado — conferido: as 4.108 fotos com
  // streetview_path têm linha 'facade' no banco, sem uma falta.
  // COM TOKEN. `/api/sv/` esta em `TOKEN_NA_QUERY` no servidor — imagem em
  // `<img>` nao manda cabecalho, entao o token vai na query. Esta linha era a
  // unica do arquivo que montava a URL sem ele: a foto principal do popup
  // devolvia 401 e virava o texto quebrado "Street View (fachada)", enquanto
  // as da galeria (que usam `comToken`) apareciam normalmente.
  const sv = poi.streetview_path && poi.streetview_path !== "NA" && poi.id != null
    ? window.comToken(`/api/sv/${poi.id}/facade`) : null;
  if (fotos.length) {
    html += `<div class="m-fotos">${sv ? `<img src="${sv}" loading="lazy" title="Street View (fachada)">` : ""}${fotos.map((u) => `<img src="${esc(u)}" loading="lazy" referrerpolicy="no-referrer">`).join("")}</div>`;
  } else if (sv) {
    html += `<div class="m-fotos"><img src="${sv}" loading="lazy" title="Street View (fachada)" style="min-width:100%"></div>`;
  }
  html += `<div class="m-head"><div class="m-nome">${esc(poi.nome)}` +
          (poi.id != null ? ` <span class="m-id" title="ID no banco (clique para copiar)" onclick="navigator.clipboard&&navigator.clipboard.writeText('${poi.id}')">#${poi.id}</span>` : "") +
          `</div><button class="m-close" onclick="fecharModal()">✕</button></div>`;
  html += `<div class="m-meta">`;
  if (poi.categoria) html += `<span class="chip" style="background:${k.cor}1a;color:${k.cor}">${k.emo} ${esc(poi.categoria)}</span>`;
  if (poi.status) html += `<span class="chip st-${esc(poi.status)}">${esc(STATUS_LABEL[poi.status] || poi.status)}</span>`;
  if (avaliacao) html += `<span class="m-rating">${avaliacao.toFixed(1)} <span class="stars">${stars(avaliacao)}</span>
       <small>(${(poi.total_avaliacoes || 0).toLocaleString("pt-BR")})</small></span>`;
  html += `</div>`;

  // AS ABAS POR FONTE, o mesmo desenho da fila do supervisor. O endereco fica
  // fora delas, logo abaixo, porque e' o que localiza o ponto e tem de estar
  // visivel sem clicar em aba nenhuma.
  const _abas = window.abasEvidencia ? window.abasEvidencia(poi.abas) : "";

  html += `<div class="m-rows">`;
  if (poi.endereco) {
    const fonte = FONTE_END[poi.endereco_fonte] || FONTE_END[poi.fonte_dado] || FONTE_END[poi.status] || null;
    html += `<div class="m-row"><span class="ico">📍</span><span>${esc(poi.endereco)}` +
      // `fonte-dado`: de onde veio o endereço (IBGE, CNEFE, Google). Some para
      // quem não é root — para o cliente, a origem do que ele vê somos nós.
      (fonte ? ` <span class="src-tag fonte-dado" style="--c:${fonte.cor}">${fonte.label}</span>` : "") + `</span></div>`;
  }
  // Telefone e site continuam AQUI, e nao so na aba: sao acionaveis — quem
  // abre o ponto costuma querer ligar ou visitar o site sem procurar.
  if (poi.telefone) html += `<div class="m-row"><span class="ico">📞</span><a href="tel:${esc(poi.telefone)}">${esc(poi.telefone)}</a></div>`;
  if (poi.website) html += `<div class="m-row"><span class="ico">🌐</span><a href="${esc(poi.website)}" target="_blank" rel="noopener">${esc(poi.website.replace(/^https?:\/\//, "").slice(0, 48))}</a></div>`;
  html += `</div>`;

  // O resto — horario, Instagram, e-mail, preco, CNPJ, razao social, situacao,
  // CNAE, socios, delivery, veredito da IA — vem do CATALOGO, agrupado por
  // fonte. Antes eram seis `if` escritos a mao que ignoravam tudo que a
  // extracao aprendeu depois de escritos.
  html += _abas;

  // 🤖 Análise visual por IA (descrever_imagens.py)
  if (poi.ia) {
    const a = poi.ia;
    const ap = String(a.veredito || "").startsWith("aprov");
    const MOT = { ok: "Confere com o cadastro", atividade_divergente: "Atividade diverge do cadastro",
      imagem_insuficiente: "Imagem insuficiente", residencia_sem_comercio: "Residência sem comércio",
      sem_estabelecimento: "Sem estabelecimento (área aberta)", ponto_vago: "Ponto comercial vago" };
    const CONSTR = { loja_terrea: "Loja térrea", casa: "Casa", predio: "Prédio",
      area_aberta: "Área aberta", galpao: "Galpão" };
    const ROT = { facade: "Fachada", g90: "Giro 90°", g180: "Giro 180°", g270: "Giro 270°", p1: "Panorama A", p2: "Panorama B" };
    html += `<div class="m-sec-title">🤖 Análise visual (IA)</div>`;
    html += `<div class="m-ia ${ap ? "ap" : "rp"}">`;
    html += `<div class="ia-top"><span class="ia-badge ${ap ? "ap" : "rp"}">${ap ? "✅ Aprovado" : "❌ Reprovado"}</span>`;
    html += `<span class="ia-mot">${esc(MOT[a.motivo] || a.motivo || "")}</span>`;
    if (a.recomendar_visita) html += `<span class="ia-star" title="${esc(a.recomendacao_motivo || "")}">⭐ Recomendar visita</span>`;
    html += `</div>`;
    if (a.atividade_real) html += `<div class="ia-line"><b>O que funciona ali:</b> ${esc(a.atividade_real)}</div>`;
    const meta = [];
    if (a.ramo_visto) meta.push(`Ramo visto: <b>${esc(a.ramo_visto)}</b>`);
    if (a.tipo_construcao) meta.push(`Construção: <b>${esc(CONSTR[a.tipo_construcao] || a.tipo_construcao)}</b>`);
    if (a.porte) meta.push(`Porte: <b>${esc(a.porte)}</b>`);
    if (a.pessoas_estimadas) meta.push(`Funcionários: <b>${esc(a.pessoas_estimadas)}</b>`);
    if (meta.length) html += `<div class="ia-meta">${meta.join(" · ")}</div>`;
    if (!ap && a.outro_estabelecimento)
      html += `<div class="ia-lead">🎯 Outro estabelecimento visto na imagem: <b>${esc(a.outro_estabelecimento)}</b></div>`;
    const angs = a.angulos_sv || [];
    if (angs.length) {
      html += `<div class="ia-360">` + angs.map((g) =>
        `<figure><img src="${window.comToken(`/api/sv/${poi.id}/${esc(g)}`)}" loading="lazy" onerror="this.closest('figure').remove()"><figcaption>${ROT[g] || g}</figcaption></figure>`).join("") + `</div>`;
    }
    html += `</div>`;
  }

  if (poi.resumo_avaliacoes) html += `<div class="m-origem" style="background:#e8f0fe;color:#174ea6"><b>O que dizem:</b> ${esc(poi.resumo_avaliacoes)}</div>`;

  // Dados empresariais (Receita Federal / mineração web)
  if (poi.cnpj || poi.razao_social || poi.socios) {
    let socios = [];
    try { socios = JSON.parse(poi.socios || "[]"); } catch { /* texto livre */ }
    html += `<div class="m-sec-title">🏢 Dados empresariais (Receita Federal)</div><div class="m-rows">`;
    if (poi.cnpj) html += `<div class="m-row"><span class="ico">🔢</span><span><b>CNPJ:</b> ${esc(poi.cnpj)}${poi.situacao_cadastral ? ` <span class="chip" style="margin-left:6px">${esc(poi.situacao_cadastral)}</span>` : ""}</span></div>`;
    if (poi.razao_social) html += `<div class="m-row"><span class="ico">🏛️</span><span><b>Razão social:</b> ${esc(poi.razao_social)}${poi.nome_fantasia ? `<br><small>Fantasia: ${esc(poi.nome_fantasia)}</small>` : ""}</span></div>`;
    if (poi.natureza_juridica) html += `<div class="m-row"><span class="ico">⚖️</span><span>${esc(poi.natureza_juridica)}</span></div>`;
    if (poi.cnae) html += `<div class="m-row"><span class="ico">🏷️</span><span><b>CNAE:</b> ${esc(poi.cnae)}</span></div>`;
    if (Array.isArray(socios) && socios.length) {
      html += `<div class="m-row"><span class="ico">👥</span><span><b>Sócios/Titulares:</b><br>${socios.map((s) => `${esc(s.nome || "")} <small>(${esc(s.qualificacao || "")})</small>`).join("<br>")}</span></div>`;
    } else if (poi.socios && !Array.isArray(socios)) {
      html += `<div class="m-row"><span class="ico">👥</span><span>${esc(String(poi.socios).slice(0, 200))}</span></div>`;
    }
    html += `</div>`;
  }

  if (poi.fachada) html += blocoFachada(poi.fachada);

  if ((poi.horarios || []).length) {
    html += `<div class="m-sec-title">Horário de funcionamento</div><div class="m-horarios">` +
      poi.horarios.map((h) => `<div><span>${esc(h.dia)}</span><span>${esc(h.horario || "—")}</span></div>`).join("") + `</div>`;
  }
  if ((poi.comentarios || []).length) {
    html += `<div class="m-sec-title">Avaliações (${poi.comentarios.length})</div>` +
      poi.comentarios.slice(0, 6).map((c) => `
        <div class="m-review">
          <div class="r-head"><span>${esc(c.autor || "Anônimo")} ${c.nota ? `<span class="stars">${stars(c.nota)}</span>` : ""}</span><small>${esc(c.data || "")}</small></div>
          ${c.texto ? `<p>${esc(c.texto.slice(0, 320))}${c.texto.length > 320 ? "…" : ""}</p>` : ""}
        </div>`).join("");
  }
  if (poi.nome_original || poi.endereco_original) {
    // divergência só quando o NÚMERO da via difere (evita falso positivo de formatação)
    const numA = (poi.endereco || "").match(/\bn?º?\.?\s*(\d{1,5})\b/i);
    const numB = (poi.endereco_original || "").match(/\bn?º?\.?\s*(\d{1,5})\b/i);
    const diverge = numA && numB && numA[1] !== numB[1];
    html += `<div class="m-origem"><b>📄 Como veio na planilha:</b> ${esc(poi.nome_original || "")}` +
      (poi.endereco_original ? `<br><span class="ico">📍</span> ${esc(poi.endereco_original)}` +
        ` <span class="src-tag fonte-dado" style="--c:#8a6d00">planilha</span>` : "") +
      (diverge ? `<br><span class="src-tag" style="--c:#ba5a08">⚠️ número da via difere do obtido — confira</span>` : "") +
      (poi.distancia_m != null ? `<br><small>Distância planilha ↔ ponto: ${Math.round(poi.distancia_m)} m</small>` : "") + `</div>`;
  }
  html += `<div class="m-actions">`;
  if (poi.maps_url) html += `<a class="btn primary" href="${esc(poi.maps_url)}" target="_blank" rel="noopener">Abrir no Google Maps</a>`;
  const la = poi.maps_lat ?? poi.lat, lo = poi.maps_lng ?? poi.lng;
  if (la != null) html += `<button class="btn ghost" onclick="map.setView([${la},${lo}],18);fecharModal()">🎯 Centralizar</button>`;
  html += `</div>`;

  $("modal-card").innerHTML = html;
  // As abas so respondem ao clique depois de existirem no DOM.
  if (window.ligarAbas) window.ligarAbas($("modal-card"));
  $("modal-overlay").classList.remove("hidden");
}
/* Modal genérico — o de POI monta o HTML dele à mão; este serve a qualquer
   conteúdo (a lista de quadras usa). */
window.abrirModal = (html) => {
  $("modal-card").innerHTML = html;
  $("modal-overlay").classList.remove("hidden");
};
window.fecharModal = () => $("modal-overlay").classList.add("hidden");
$("modal-overlay").addEventListener("click", (e) => { if (e.target.id === "modal-overlay") fecharModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { fecharModal(); $("confirm-overlay").classList.add("hidden"); } });

/* ────────────────────────────────────────────────────────────
   Polígono da área
──────────────────────────────────────────────────────────── */
let areaLayer = null;
let drawer = null;
let modoDesenho = "area";        // só a área é desenhada pelo usuário
const AREA_STYLE = { color: "#1a73e8", weight: 2.5, dashArray: "6 6", fillColor: "#1a73e8", fillOpacity: 0.06, className: "area-poly", pane: "paneArea" };
// acima disto a ligação até a coordenada original vira alerta no mapa: o teto
// da régua é 10 m, então 25 m é o dobro e meio do que se considera aceitável
const DESLOC_DESTAQUE_M = 25;

/* ────────────────────────────────────────────────────────────
   Clique no polígono: quantos POIs estão dentro, e apagar.

   Nasceu de uma confusão real, 25/08/2026. O cabeçalho dizia "42 POIs" e o
   cartão da mineração dizia "165" — números da MESMA área, e nenhum dos dois
   errado: os 42 são POIs do banco dentro do desenho, os 165 são ícones lidos
   por OCR num tile que é 14× maior que o desenho. Faltava um lugar onde a
   pergunta "o que tem aqui dentro?" tivesse resposta direta.

   A quebra é por FONTE, não pelos chips de origem. Os chips agrupam por como o
   POI foi CONFIRMADO, e a base estadual inteira cai no balde "Outros" — foi
   exatamente o que escondeu que os 42 vinham do Overture/OSM/Foursquare.
──────────────────────────────────────────────────────────── */
// Rótulo curto e detalhe no `title`: "Bases públicas (Overture/OSM/Foursquare)"
// por extenso quebrava a célula em duas linhas e desalinhava a coluna de
// números — e quem precisa saber QUAIS bases passa o mouse.
const FONTE_ROTULO = {
  estadual: ["Bases públicas", "Overture + OpenStreetMap + Foursquare"],
  pipeline: ["Captura + OCR", "Tiles do Maps lidos por visão computacional"],
  ifood: ["iFood", "Cardápios e lojas do iFood"],
  cadastur: ["Cadastur/MTur", "Prestadores registrados no Ministério do Turismo"],
  ia_fachada: ["Lidos na parede", "Nomes que a IA leu na fachada do Street View"],
  cliente: ["Base do cliente", "O cadastro que a concessionária entregou"],
};

/* Área do anel em hectares. Projeção plana local: em polígonos de bairro o
   erro é irrelevante, e trazer uma biblioteca de geodésia para escrever "3,5 ha"
   num popup seria peso sem retorno. */
function areaHectares(anel) {
  if (!anel || anel.length < 3) return 0;
  const latMed = anel.reduce((s, p) => s + p[0], 0) / anel.length;
  const mx = 111320 * Math.cos((latMed * Math.PI) / 180), my = 110540;
  let s2 = 0;
  for (let i = 0, j = anel.length - 1; i < anel.length; j = i++) {
    s2 += (anel[j][1] * mx) * (anel[i][0] * my) - (anel[i][1] * mx) * (anel[j][0] * my);
  }
  return Math.abs(s2 / 2) / 10000;
}

function resumoArea() {
  const anel = anelArea();
  const dentro = [...allPois.values()].filter(dentroDaArea);
  const porFonte = new Map();
  for (const p of dentro) {
    const f = p.fonte || "sem fonte";
    porFonte.set(f, (porFonte.get(f) || 0) + 1);
  }
  return {
    anel,
    total: dentro.length,
    ha: areaHectares(anel),
    multi: dentro.filter((p) => p.multiorigem).length,
    fontes: [...porFonte.entries()].sort((a, b) => b[1] - a[1]),
  };
}

function htmlResumoArea() {
  const r = resumoArea();
  const nf = (n) => n.toLocaleString("pt-BR");
  const linhas = r.fontes.map(([f, n]) => {
    const pct = Math.round((n / r.total) * 100);
    const [rot, det] = FONTE_ROTULO[f] || [f, ""];
    return `<tr><td title="${det}">${rot}</td><td class="num">${nf(n)}</td>`
         + `<td class="pct">${pct}%</td></tr>`;
  }).join("");

  const corpo = r.total
    ? `<table class="area-pop-tab"><tbody>${linhas}</tbody></table>`
      + (r.multi
          ? `<p class="area-pop-nota">${nf(r.multi)} são <b>multiorigem</b> — sustentados
             por mais de uma base. É onde a fusão pode ter errado.</p>`
          : "")
    : `<p class="area-pop-nota">Nenhum POI do banco aqui dentro. Se acabou de
       minerar, o mapa só mostra o que já foi gravado.</p>`;

  return `<div class="area-pop">
    <div class="area-pop-topo">
      <span class="area-pop-num">${nf(r.total)}</span>
      <span class="area-pop-cap">POIs do banco<br>dentro do desenho</span>
    </div>
    <div class="area-pop-sub">${r.ha < 10
        ? r.ha.toLocaleString("pt-BR", { maximumFractionDigits: 1 })
        : nf(Math.round(r.ha))} ha · ${r.anel ? r.anel.length : 0} vértices</div>
    ${corpo}
    <button class="btn sm danger area-pop-del" type="button">Apagar esta área</button>
  </div>`;
}

/* O popup é recalculado A CADA ABERTURA. Ele conta `allPois`, que muda com a
   mineração em tempo real — um HTML preso no bind mostraria o número de quando
   o polígono foi desenhado, que é justamente o erro que este botão existe para
   não deixar acontecer de novo. */
/* O popup é montado UMA vez e só troca de conteúdo. E o botão de apagar é
   pego por DELEGAÇÃO, não por referência ao elemento.

   As duas coisas vêm do mesmo defeito, medido 25/08/2026. Passar `options` no
   `bindPopup` faz o Leaflet construir uma Popup NOVA a cada clique; a partir da
   segunda abertura o container ainda não existia quando `openPopup` retornava,
   o `querySelector` devolvia nulo e o `if (!b) return` engolia a ligação. O
   popup abria com os números certos e o "Apagar esta área" não fazia nada —
   o pior tipo de defeito, porque a tela não acusa nada.

   Delegação não tem esse problema: o clique é resolvido quando acontece, e aí
   o botão existe por definição. */
function ligarPopupArea(layer) {
  layer.bindPopup("", { className: "area-pop-wrap", maxWidth: 340 });
  layer.on("click", (e) => {
    L.DomEvent.stopPropagation(e);            // não dispara o copiar-coordenada
    // Recalculado A CADA abertura: `allPois` muda com a mineração em tempo
    // real, e um HTML preso no bind mostraria o número de quando o polígono
    // foi desenhado — justamente o engano que este popup existe para desfazer.
    layer.setPopupContent(htmlResumoArea());
    layer.openPopup(e.latlng);
  });
}

document.addEventListener("click", (ev) => {
  if (!ev.target.closest || !ev.target.closest(".area-pop-del")) return;
  if (areaLayer) areaLayer.closePopup();
  confirmar("Apagar a área desenhada?",
    "O desenho some e o mapa volta a mostrar o município escolhido. "
    + "<b>Nenhum POI é apagado</b> — a área é foco de tela, não filtro de banco.",
    async () => {
      setAreaLayer(null);
      await salvarArea([]);
      toast("Área apagada", "ok");
    });
});

function setAreaLayer(latlngs) {
  if (areaLayer) { map.removeLayer(areaLayer); areaLayer = null; }
  if (latlngs && latlngs.length >= 3) {
    areaLayer = L.polygon(latlngs, AREA_STYLE).addTo(map);
    ligarPopupArea(areaLayer);
    // "rejeitados" era verdade quando o polígono barrava a gravação. Hoje ele
    // define o FOCO: o que cai fora é gravado com cidade e UF e fica fora da
    // tela, não fora do banco.
    $("area-status").innerHTML = `Área definida <b>(${latlngs.length} vértices)</b> — `
      + `é o foco do mapa. O que for achado fora dela também é gravado, `
      + `identificado por cidade.`;
    $("area-status").className = "hint ok";
  } else {
    $("area-status").textContent = "Nenhuma área definida — desenhe o polígono no mapa.";
    $("area-status").className = "hint warn";
  }
  atualizarBotoes();
  // a área virou recorte dos POIs: desenhar, redesenhar ou apagar muda o que
  // aparece no mapa e nas contagens, então tudo se refaz aqui
  if (typeof aplicarFiltro === "function") {
    aplicarFiltro();
    atualizarBannerMunicipio();
  }
}

// Devolve true se o banco ACEITOU. Quem chama precisa disso: até 24/08/2026
// este `fetch` era disparado sem olhar a resposta e o chamador dizia
// "Área salva ✔" de qualquer jeito. Com o root, que não tem empresa, a rota
// respondia 500 e a tela anunciava sucesso — o usuário desenhava 4 quadras, o
// banco seguia com a área de nove dias antes e a mineração varria o município
// inteiro. Erro que a tela esconde é pior que erro que ela mostra.
async function salvarArea(latlngs) {
  try {
    const r = await fetch("/api/area", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ polygon: latlngs || [] }),
    });
    if (!r.ok) {
      let msg = "";
      try { msg = (await r.json()).erro || ""; } catch { /* corpo vazio no 500 */ }
      toast(msg || `A área NÃO foi salva (HTTP ${r.status}).`, "err");
      return false;
    }
    return true;
  } catch (e) {
    toast(`A área NÃO foi salva: ${e.message}`, "err");
    return false;
  }
}

async function carregarArea() {
  try {
    const { polygon } = await (await fetch("/api/area")).json();
    if (polygon && polygon.length >= 3) {
      setAreaLayer(polygon);
      map.fitBounds(areaLayer.getBounds().pad(0.15));
    } else setAreaLayer(null);
  } catch { setAreaLayer(null); }
}

$("btn-desenhar").onclick = () => {
  if (drawer) drawer.disable();
  drawer = new L.Draw.Polygon(map, {
    allowIntersection: false, showArea: true,
    shapeOptions: AREA_STYLE,
    icon: new L.DivIcon({ iconSize: new L.Point(10, 10), className: "leaflet-div-icon leaflet-editing-icon" }),
  });
  drawer.enable();
  toast("Clique no mapa para desenhar o polígono. Feche clicando no 1º ponto.");
};

map.on(L.Draw.Event.CREATED, async (e) => {
  const latlngs = e.layer.getLatLngs()[0].map((p) => [p.lat, p.lng]);
  setAreaLayer(latlngs);
  if (await salvarArea(latlngs)) {
    toast("Área salva ✔", "ok");
  } else {
    // A tela volta a mostrar o que o BANCO tem. Deixar o desenho na tela depois
    // de a gravação falhar é o que produziu o defeito: o painel dizia "4
    // vértices" e o coletor lia outra coisa — e é o coletor que manda.
    await carregarArea();
  }
});

$("btn-apagar-area").onclick = async () => {
  setAreaLayer(null);
  await salvarArea([]);
};

/* Limpeza do banco fora da área (com prévia + confirmação) */
$("btn-limpar-fora").onclick = async () => {
  if (!areaLayer) return toast("Desenhe a área primeiro", "err");
  const prev = await (await fetch("/api/limpar-fora", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dry_run: true }),
  })).json();
  if (prev.erro) return toast(prev.erro, "err");
  if (!prev.fora) return toast("Nenhum POI fora da área — banco já está limpo ✔", "ok");
  confirmar(
    "Limpar POIs fora da área?",
    `Serão removidos <b>${prev.fora}</b> POIs que estão fora do polígono (com fotos, avaliações e horários).<br>
     Permanecem: <b>${prev.dentro}</b> dentro da área e <b>${prev.sem_coord}</b> sem coordenada.<br><br>Essa ação não pode ser desfeita.`,
    async () => {
      const res = await (await fetch("/api/limpar-fora", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: false }),
      })).json();
      toast(`🧹 ${res.removidos} POIs removidos`, "ok");
      carregarPois();
    });
};

/* ────────────────────────────────────────────────────────────
   Modos + controles do job
──────────────────────────────────────────────────────────── */
let modo = "planilha";
let arquivoImportado = null;
let jobRodando = false;
let ultimoJob = null;   // alimenta o cartão "Agora" do menu

/* MENU FLYOUT — abre por baixo do cabeçalho, largura inteira.
   O botão mostra a etapa corrente; o painel mostra todas, agrupadas na ordem em
   que o trabalho acontece. */
const flyout = () => $("flyout");
function abrirMenu(abrir) {
  const b = $("menu-btn");
  flyout().classList.toggle("hidden", !abrir);
  $("flyout-fundo").classList.toggle("hidden", !abrir);
  // A busca flutuante tem z-index 1310, acima do menu — ela atravessava o painel
  // e ficava boiando no meio da coluna "Qualificação". Some enquanto o menu está
  // aberto: é busca no MAPA, e o mapa está coberto de qualquer forma.
  $("busca-wrap").classList.toggle("oculto-menu", abrir);
  b.setAttribute("aria-expanded", abrir ? "true" : "false");
  if (abrir) atualizarContextoMenu();
}
$("menu-btn").onclick = () => abrirMenu(flyout().classList.contains("hidden"));
$("flyout-fundo").onclick = () => abrirMenu(false);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !flyout().classList.contains("hidden")) abrirMenu(false);
});

/* A coluna da direita responde "onde eu estou?" antes de o usuário escolher para
   onde ir: qual área está em foco e o que o último processo fez. Sem isso o
   menu é uma lista de destinos sem mapa. */
function atualizarContextoMenu() {
  const temArea = !!anelArea();
  const cid = cidadeEmFoco();
  $("fly-area-val").textContent = temArea
    ? (cid ? `${cid} · área desenhada` : "área desenhada")
    : (cid || "nenhuma definida");
  $("fly-area-sub").textContent = temArea
    ? `${poisBase().length.toLocaleString("pt-BR")} POIs no foco`
    : "desenhe no mapa ou escolha o município";
  const j = ultimoJob;
  $("fly-job-val").textContent = j && j.modo
    ? (MODO_LABEL[j.modo] || j.modo) : "nenhum nesta sessão";
  $("fly-job-sub").textContent = j && j.status
    ? `${j.status}${j.fim ? " às " + j.fim.slice(11, 16) : ""}` : "—";
}

document.querySelectorAll(".fly-item").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".fly-item").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    modo = b.dataset.mode;
    // o botão do cabeçalho passa a ser o rótulo da etapa corrente
    $("menu-ico").textContent = b.querySelector(".fly-ico").textContent;
    $("menu-rot").textContent = b.querySelector("b").textContent;
    abrirMenu(false);
    $("sec-planilha").classList.toggle("hidden", modo !== "planilha");
    $("sec-mineracao").classList.toggle("hidden", modo !== "mineracao");
    $("sec-enriquecimento").classList.toggle("hidden", modo !== "enriquecimento");
    $("sec-cadastur")?.classList.toggle("hidden", modo !== "cadastur");
    if (modo === "cadastur") carregarResumoCadastur();
    // A fila de logradouro nao tem painel proprio no cabecalho: ela e uma
    // LISTA de trabalho, e abre direto. Deixar uma secao vazia atras dela
    // sugeriria que ha configuracao a fazer antes, e nao ha.
    if (modo === "logradouros") abrirFilaLogradouro();
    // A base pública vem por MUNICÍPIO. A área desenhada não participa, e
    // deixar o painel dela visível sugeriria que participa — o operador
    // desenharia um polígono e esperaria que o recorte o respeitasse.
    $("sec-area")?.classList.toggle("hidden", modo === "cadastur");
    $("sec-avaliar")?.classList.toggle("hidden", modo !== "avaliar");
    // O DASHBOARD NÃO É UM PASSO DO PROCESSO — é tela de leitura. Some com tudo
    // que serve para operar: área de trabalho, cards do processo atual, busca no
    // mapa, chips de filtro e log. Deixados no lugar, eles ficam por cima do
    // conteúdo, mostram "0 processados · aguardando processo" ao lado de números
    // reais, e o "▶ Iniciar processo" sugere que aquele botão roda o que está
    // sendo exibido. Na volta para qualquer outra aba tudo reaparece.
    const dash = modo === "dashboard";
    $("sec-dashboard").classList.toggle("hidden", !dash);
    $("dashboard").classList.toggle("hidden", !dash);
    for (const id of ["sec-area", "sec-execucao", "stats", "busca-wrap",
                      "filtros", "log-panel"]) {
      // `sec-area` tem DOIS motivos para sumir: o dashboard e a base pública.
      // Sem o segundo aqui, sair do dashboard para o Cadastur a traria de
      // volta — o laço roda depois e venceria a linha acima.
      const esconder = dash || (id === "sec-area" && modo === "cadastur");
      $(id)?.classList.toggle("hidden", esconder);
    }
    if (dash) carregarDashboard();
    // os cards da leitura substituem os do processo genérico só nesta aba
    const av = modo === "avaliar";
    sincronizarPaineis();
    if (!av) fecharListaFachada();
    if (av) { estimarAvaliacao(); carregarCardsFachada(); }
    atualizarBotoes();
  };
});

// O seletor de motor sumiu em 24/08/2026: a mineração roda as DUAS fontes
// gratuitas, em sequência. Não sobrou nada para alternar — o zoom é da captura
// e vale sempre. A função fica como no-op tolerante para o caso de uma aba
// antiga ainda ter o `<select>` em cache.
function trocarMotorMineracao() {
  $("op-zoom-wrap")?.classList.remove("hidden");
}
trocarMotorMineracao();

$("btn-importar").onclick = () => $("file-input").click();
$("file-input").onchange = async () => {
  const f = $("file-input").files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  $("file-status").textContent = "Enviando…";
  try {
    const r = await (await fetch("/api/upload", { method: "POST", body: fd })).json();
    if (r.erro) { $("file-status").textContent = r.erro; return toast(r.erro, "err"); }
    arquivoImportado = r.arquivo;
    $("file-status").innerHTML = `📄 <b>${esc(r.arquivo)}</b> — ${r.linhas.toLocaleString("pt-BR")} linhas.`;
    $("file-status").className = "hint ok";
    toast("Planilha importada ✔", "ok");
  } catch { $("file-status").textContent = "Falha no upload."; toast("Falha no upload", "err"); }
  atualizarBotoes();
};

/* Os ÚNICOS modos que o botão "Iniciar processo" dispara. A lista é explícita
   porque o último ramo daquele if/else era um `else` aberto: quando o modo
   "quadras" entrou, ele caiu ali e disparou o enriquecimento de POIs — processo
   caro, de outra parte do sistema, sem ninguém ter pedido. Modo fora desta lista
   não inicia job nenhum e o botão nem aparece. */
const MODOS_JOB = { planilha: "da planilha", mineracao: "de mineração",
                    enriquecimento: "de enriquecimento",
                    avaliar: "de avaliação de fachada",
                    cadastur: "de base pública (Cadastur)" };

/* QUEM PODE DISPARAR PROCESSO. O servidor já nega — `/api/jobs` exige admin — e
   é ele que manda. Isto aqui só evita o pior tipo de silêncio: o `user` clicava
   em "Iniciar processo", levava 403, e a tela não dizia absolutamente nada.
   Fonte única: o crachá que o sessao.js publica em `body[data-nivel]`. */
const podeExecutar = () => ["admin", "root"].includes(document.body.dataset.nivel);

/* O crachá chega depois do boot (`/api/eu` é assíncrono), então a primeira
   pintura acontece sem nível nenhum. Sem este aviso, o admin também via o botão
   bloqueado até recarregar a página. */
function aplicarNivel() {
  atualizarBotoes();
  // Links que NAVEGAM (aba nova, download) tambem nao mandam cabecalho.
  const modelo = $("link-modelo-cadastro");
  if (modelo && window.comToken) modelo.href = window.comToken("/api/modelos/cadastro");
  for (const id of ["btn-cnpj-skill", "btn-baixar-imgs", "btn-base-estadual",
                    "btn-base-cadastur"]) {
    if ($(id)) $(id).classList.toggle("hidden", !podeExecutar());
  }
}
document.addEventListener("cr:sessao", aplicarNivel);

/* O RESUMO do que a fonte já trouxe. É a primeira pergunta de quem abre esta
   tela: "o que eu já tenho daqui?" — e não "quais são os parâmetros?".

   Ele mostra também o que NÃO virou ponto, com o motivo. Sem isso, "28
   carregados, 17 pontos" parece perda; com isso, é decisão: aqueles 11 não
   têm coordenada em nenhuma das trêsancoras, e pô-los no mapa significaria
   inventar posição. */
async function carregarResumoCadastur() {
  const caixa = $("cad-resumo");
  if (!caixa) return;
  const municipio = ($("cad-municipio")?.value || "").trim();
  const uf = ($("cad-uf")?.value || "").trim();
  if (!municipio && !uf) { caixa.classList.add("hidden"); return; }
  try {
    const r = await fetch("/api/cadastur/resumo?municipio="
                          + encodeURIComponent(municipio)
                          + "&uf=" + encodeURIComponent(uf));
    if (!r.ok) { caixa.classList.add("hidden"); return; }
    const d = await r.json();
    if (!d.total) {
      /* NADA CARREGADO não é erro — é o estado normal antes da primeira
         atualização. Esconder o card diria menos que dizer isto. */
      caixa.innerHTML = '<div class="hint">Nada do Cadastur neste município '
        + 'ainda. A primeira atualização baixa o snapshot do MTur.</div>';
      caixa.classList.remove("hidden");
      return;
    }
    const n = (v) => (v || 0).toLocaleString("pt-BR");
    let h = '<div class="cad-numeros">'
      + `<div class="cad-num"><b>${n(d.total)}</b><span>prestadores</span></div>`
      + `<div class="cad-num"><b>${n(d.com_poi)}</b><span>viraram ponto</span></div>`;
    if (d.sem_poi) {
      h += `<div class="cad-num"><b>${n(d.sem_poi)}</b><span>sem coordenada</span></div>`;
    }
    /* LEITOS ganha destaque porque é o número que nenhuma outra fonte dá — e
       leito é consumo de água por pessoa/dia. */
    if (d.leitos) {
      h += `<div class="cad-num destaque"><b>${n(d.leitos)}</b><span>leitos declarados</span></div>`;
    }
    if (d.uh) {
      h += `<div class="cad-num"><b>${n(d.uh)}</b><span>unidades habitacionais</span></div>`;
    }
    h += "</div>";

    if ((d.atividades || []).length) {
      h += '<div class="cad-linha-chips">'
        + d.atividades.map((a) => `<span class="cad-chip">${esc(a.nome)} <b>${n(a.n)}</b></span>`).join("")
        + "</div>";
    }
    if (d.sairam) {
      h += `<div class="cr-alerta cr-alerta--atencao"><span><b>${n(d.sairam)} saíram do Cadastur</b>`
        + "Deixaram de aparecer no snapshot — perderam regularidade. Não foram "
        + "apagados: pode ser fechamento, troca de dono ou renovação atrasada, e "
        + "os três casos valem uma olhada.</span></div>";
    }

    /* O CARD DE PESSOA FÍSICA é um TOTALIZADOR, e assume isso: sem lista, sem
       link, sem "ver detalhes". O detalhe não existe no banco de propósito —
       guia de turismo tem CPF e data de nascimento, e contar não identifica
       ninguém, mas guardar identificaria. */
    const pf = (d.pessoa_fisica || []).reduce((a, x) => a + (x.n || 0), 0);
    if (pf) {
      h += `<div class="cad-pf"><b>${n(pf)}</b> prestadores pessoa física `
        + "(guia de turismo) registrados aqui."
        + "<small>Descartados como ponto e como linha: não são economia que "
        + "consome água, e o cadastro deles traz CPF e data de nascimento. "
        + "Guardamos apenas este total.</small></div>";
    }
    if (d.periodo) {
      h += `<div class="hint">Snapshot de referência: <b>${esc(d.periodo)}</b> `
        + "(fim do trimestre publicado pelo MTur).</div>";
    }
    caixa.innerHTML = h;
    caixa.classList.remove("hidden");
  } catch { caixa.classList.add("hidden"); }
}

/* Os campos da base pública reavaliam o botão A CADA TECLA. Sem isto ele só
   acenderia na próxima troca de etapa: a pessoa digitaria o município inteiro
   olhando um botão apagado e concluiria que a tela travou. */
let _cadResumoTimer = null;
for (const id of ["cad-municipio", "cad-uf"]) {
  document.getElementById(id)?.addEventListener("input", () => {
    atualizarBotoes();
    /* Espera a digitação parar. Sem isto, "Cachoeirinha" dispara doze
       consultas — uma por letra — e as respostas chegam fora de ordem: a de
       "Cach" pode chegar depois da de "Cachoeirinha" e sobrescrever o card
       certo com o de um município que não existe. */
    clearTimeout(_cadResumoTimer);
    _cadResumoTimer = setTimeout(carregarResumoCadastur, 400);
  });
}

/* Dispara o job e MOSTRA a falha. O `.json()` direto sobre a resposta engolia
   403 e 500: o corpo do erro tem `detail`, não `erro`, então nenhum toast
   aparecia e o clique parecia não ter acontecido. */
async function pedirJob(body) {
  const resp = await fetch("/api/jobs", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const j = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    toast(j.detail || `falha ao iniciar (HTTP ${resp.status})`, "err");
    return null;
  }
  if (j.erro) { toast(j.erro, "err"); return null; }
  return j;
}

function atualizarBotoes() {
  const temArea = !!areaLayer;
  // o modo Quadras tem o seu próprio botão e NÃO passa pelo job de POIs
  const usaJob = modo in MODOS_JOB;
  const executa = podeExecutar();
  // A base pública não usa a área desenhada: ela vem por município, que é como
  // o governo publica. Exigir polígono aqui deixaria o botão morto sem que
  // nada na tela dissesse o que faltava.
  const precisaArea = modo !== "cadastur";
  // Cada modo tem a SUA pré-condição, e o botão só acende quando ela está
  // satisfeita. A planilha precisa do arquivo; a base pública, do município.
  // Deixar o botão aceso com o aviso escrito ao lado convida ao clique que vai
  // falhar — e a mensagem, que estava certa, passa a parecer decorativa.
  const temMunicipio = modo !== "cadastur" ||
        (($("cad-municipio")?.value || "").trim() &&
         ($("cad-uf")?.value || "").trim().length === 2);
  const pronto = usaJob && (temArea || !precisaArea) && !jobRodando && executa &&
                 !!temMunicipio &&
                 (modo !== "planilha" || !!arquivoImportado);
  /* O RÓTULO diz o que o botão faz NESTE passo. "Iniciar processo" está certo
     para minerar uma área; numa base pública o que se faz é ATUALIZAR a fonte,
     e chamar isso de "iniciar" esconde que a segunda vez custa quase nada — a
     skill revalida por SHA-256 e só rebaixa o trimestre que mudou. */
  $("btn-iniciar").textContent = modo === "cadastur"
    ? "\u21bb Atualizar fonte" : "\u25b6 Iniciar processo";
  $("btn-iniciar").disabled = !pronto;
  $("btn-iniciar").classList.toggle("hidden", jobRodando || !usaJob);
  $("btn-parar").classList.toggle("hidden", !jobRodando || !executa);
  if (!executa) $("job-status-txt").textContent =
    "Seu nível de acesso vê os dados, mas não dispara processo. Peça a um admin da sua empresa.";
  else if (!temArea && precisaArea) $("job-status-txt").textContent = "Defina a área (passo 1) para liberar o início.";
  // Dizer "pronto" com o botão travado é pior que não dizer nada: a pessoa
  // conclui que a tela quebrou em vez de procurar o campo que falta. A
  // mensagem cobre os DOIS campos, e não só o primeiro.
  else if (modo === "cadastur" && !($("cad-municipio")?.value || "").trim())
    $("job-status-txt").textContent = "Informe o município — o Cadastur é publicado por município, não por área.";
  else if (modo === "cadastur" && ($("cad-uf")?.value || "").trim().length !== 2)
    $("job-status-txt").textContent = "Falta a UF — duas letras, ex.: RS.";
  else if (modo === "planilha" && !arquivoImportado && !jobRodando) $("job-status-txt").textContent = "Importe a planilha (passo 2).";
  else if (!jobRodando) $("job-status-txt").textContent = "Pronto para iniciar.";
}

/* A estimativa aparece ANTES de começar, e reage à troca de modelo: a diferença
   entre gpt-4o-mini e gpt-4o é de uma ordem de grandeza sobre 16 mil fachadas, e
   descobrir isso depois de mandar rodar é caro. */
async function estimarAvaliacao() {
  const el = $("av-estimativa");
  if (!el) return;
  el.textContent = "calculando o que há para avaliar…";
  try {
    // "false", não string vazia: o FastAPI recusa `refazer=` com erro de parse,
    // e o catch abaixo transformava isso em "servidor fora?" — mensagem que
    // manda procurar o problema no lugar errado.
    const q = new URLSearchParams({ modelo: $("av-modelo").value,
                                    refazer: $("av-refazer").checked ? "true" : "false" });
    const e = await (await fetch("/api/avaliar/estimativa?" + q)).json();
    // A CONTA DA ÁREA, sempre visível. A IA só lê quem tem fachada capturada —
    // e quando isso não aparece, "40 fachadas a ler" numa área de 254 pontos
    // parece a IA pulando 214.
    const a = e.area || {};
    const fora = (a.nunca_capturados || 0) + (a.sem_panorama || 0);
    const conta = a.validos
      ? `<div class="av-conta"><b>${nfmt(a.validos)}</b> POIs na área
           · <b>${nfmt(a.com_fachada)}</b> com fachada capturada
           ${a.sem_panorama ? `· ${nfmt(a.sem_panorama)} sem panorama no Street View` : ""}
           ${a.nunca_capturados
             ? `· <b class="aviso">${nfmt(a.nunca_capturados)} nunca capturados</b>` : ""}
           ${a.ja_comerciais
             ? `· ${nfmt(a.ja_comerciais)} já comerciais no cadastro (fora por padrão)` : ""}
         ${a.nunca_capturados
           ? `<br><small>A IA não alcança quem não tem imagem. Rode a <b>Fase 4 —
              Street View</b> <b>sem</b> "só nos pobres" para fechar a área.</small>` : ""}
         </div>`
      : "";
    if (!e.pois) {
      el.innerHTML = conta + (e.ja_avaliados
        ? `Tudo lido nesta área — <b>${nfmt(e.ja_avaliados)}</b> fachadas
           já avaliadas (${nfmt(e.ja_aprovados)} aprovadas).
           Marque <b>reavaliar</b> para passar de novo.`
        : "Nenhum ponto da área tem fachada capturada. Rode a <b>Fase 4</b> do "
          + "enriquecimento primeiro — a leitura precisa da imagem.");
      return;
    }
    el.innerHTML = conta +
      `<b>${nfmt(e.pois)}</b> fachadas a ler`
      + (e.cidade ? ` em ${esc(e.cidade)}` : "")
      + (e.local
         ? ` · <b>grátis</b> na GPU do i9 · ~<b>${e.horas} h</b> de processamento<br>`
         : ` · custo estimado <b>US$ ${e.usd}</b> (US$ ${e.usd_por_poi}/ponto)<br>`)
      + `<b>${nfmt(e.com_vinculo)}</b> têm imóvel casado no cadastro `
      + `— só esses podem virar <b>achado convergente</b>; o resto para em sinal de imagem.`
      + (fora ? `<br><small>${nfmt(fora)} pontos da área ficam de fora desta rodada.</small>` : "")
      + (e.ja_avaliados ? `<br><small>${nfmt(e.ja_avaliados)} já lidas
          antes (${nfmt(e.ja_aprovados)} aprovadas).</small>` : "");
  } catch {
    el.textContent = "não consegui calcular a estimativa (servidor fora?).";
  }
}
/* ────────────────────────────────────────────────────────────
   CARDS DA LEITURA DE FACHADA + lista navegável sobre o mapa
──────────────────────────────────────────────────────────── */
const FA_ICO = {
  // leitura em quatro fases
  aprovadas: "✅", especifico: "🎯", divergente: "🔀", revisar: "👁️",
  reprovadas: "⛔", alvo_ausente: "📍",
  letreiro: "🔤", galpao: "🏭", multiplas: "🏘️", so_foto: "📷",
  // leitura anterior
  oportunidade: "⚖️", convergente: "🎯", uso_diverge: "🏷️",
  unidades: "🏢", coletiva: "🔗", medicao: "🔧", numero: "❗",
  conservacao: "🧱", inapto: "🚫", fora_escopo: "🗺️",
};
const FA_COR = {
  // `revisar` em âmbar de propósito: não é erro nem aprovação, é trabalho
  // humano esperando. Pintado de vermelho viraria fila de problema; de verde,
  // ninguém abriria.
  aprovadas: "c-green", especifico: "c-green", divergente: "c-purple",
  revisar: "c-orange", reprovadas: "c-red",
  alvo_ausente: "c-red", letreiro: "c-purple", galpao: "c-blue",
  multiplas: "c-orange", so_foto: "c-blue",
  convergente: "c-green", oportunidade: "c-purple",
  uso_diverge: "c-orange", unidades: "c-orange", coletiva: "c-orange",
  medicao: "c-blue", numero: "c-red", conservacao: "c-gray",
  inapto: "c-gray", fora_escopo: "c-gray",
};

async function carregarCardsFachada() {
  const alvo = $("fa-cards");
  if (!alvo) return;
  try {
    const d = await (await fetch("/api/fachada/resumo")).json();
    if (!d.total) {
      alvo.innerHTML = `<div class="stat-nota">Nenhuma fachada lida nesta área
        ainda. Configure ao lado e inicie o processo.</div>`;
      return;
    }
    // card com zero não some: "0 medições com problema" é informação, e some-lo
    // faria a lista mudar de tamanho a cada rodada
    alvo.innerHTML = d.cards.map((c) => `
      <button class="stat-card fa-card ${FA_COR[c.chave] || ""}" data-recorte="${c.chave}"
              ${c.n ? "" : "disabled"}>
        <div class="stat-ico">${FA_ICO[c.chave] || "•"}</div>
        <div><div class="stat-val">${nfmt(c.n)}</div>
          <div class="stat-label">${esc(c.rotulo)}</div></div>
      </button>`).join("");
    alvo.querySelectorAll(".fa-card").forEach((b) => {
      b.onclick = () => abrirListaFachada(b.dataset.recorte);
    });
  } catch {
    alvo.innerHTML = `<div class="stat-nota">não consegui ler o resumo.</div>`;
  }
}

let faItens = [];
/* ── A FILA DE LOGRADOURO QUE PRECISA DE GENTE ──────────────────────────────
 *
 * A skill `ajuste-logradouro` marca a forma canonica com um TIER que diz quanto
 * confiar. `CONFIRMA` e aplicavel em massa; `REVISAR` significa que houve PERDA
 * DE TEXTO — um segmento entre parenteses, um bairro depois do hifen, uma poda;
 * `HUMANO`, que sobrou pouco ou nada de via.
 *
 * Os dois ultimos sao fila de gente POR DESENHO, nao por falha. Medido em
 * Canoas: 1.271 linhas de 283.423, ou 0,45%.
 *
 * A tela mostra o ORIGINAL e a forma marcada lado a lado. Sem o original, a
 * pessoa nao tem como julgar o corte — e o corte e justamente o motivo de a
 * linha estar aqui.
 */
let filaLogr = [];

async function abrirFilaLogradouro() {
  window.abrirModal(`<div class="m-head"><div class="m-nome">Revisar logradouros</div>
    <button class="m-close" onclick="fecharModal()">✕</button></div>
    <div class="m-meta"><span class="chip">carregando…</span></div>`);
  let d;
  try {
    d = await (await fetch("/api/logradouro/pendencias?limite=300")).json();
  } catch (e) {
    return window.abrirModal(`<div class="m-head"><div class="m-nome">Revisar logradouros</div>
      <button class="m-close" onclick="fecharModal()">✕</button></div>
      <div class="m-meta"><span class="chip">nao consegui carregar: ${esc(e.message)}</span></div>`);
  }
  filaLogr = d.itens || [];
  const r = d.resumo || {};
  const pend = filaLogr.filter((i) => i.revisao_status === "pendente").length;

  const linha = (i, k) => `
    <div class="logr-item" data-k="${k}">
      <div class="logr-orig" title="como a fonte escreveu">${esc(i.logradouro_original || "(vazio)")}</div>
      <div class="logr-seta">→</div>
      <div class="logr-marc">${esc(i.logradouro_corrigido || i.logradouro_marcado || "—")}</div>
      <span class="chip ${i.tier === "HUMANO" ? "st-erro" : ""}">${esc(i.tier)}</span>
      ${i.risco ? `<span class="chip" title="por que caiu aqui">${esc(i.risco)}</span>` : ""}
      <span class="chip">${esc(i.fonte)}</span>
      ${i.revisao_status !== "pendente"
        ? `<span class="chip st-ok" title="${esc(i.revisado_por || "")}">${esc(i.revisao_status)}</span>`
        : `<button class="logr-btn" data-k="${k}">revisar</button>`}
    </div>`;

  window.abrirModal(`
    <div class="m-head"><div class="m-nome">Revisar logradouros</div>
      <button class="m-close" onclick="fecharModal()">✕</button></div>
    <div class="m-meta">
      <span class="chip">${pend} pendente(s)</span>
      ${Object.entries(r).map(([k, v]) => `<span class="chip">${esc(k)}: ${v}</span>`).join("")}
    </div>
    <div class="logr-ajuda">Estas linhas cairam aqui porque a normalizacao
      <b>perdeu texto</b> ao cortar. Compare o original com a forma marcada:
      corrija se o corte estragou, confirme se ficou certo, descarte se nao e
      logradouro utilizavel.</div>
    <div class="logr-lista">${filaLogr.length ? filaLogr.map(linha).join("")
      : `<div class="fa-vazio">nada na fila — a normalizacao resolveu tudo</div>`}</div>`);

  document.querySelectorAll(".logr-btn").forEach((b) => {
    b.onclick = () => revisarLogradouro(parseInt(b.dataset.k, 10));
  });
}

async function revisarLogradouro(k) {
  const i = filaLogr[k];
  if (!i) return;
  /* O `prompt` ja vem preenchido com a forma marcada: na maioria dos casos o
     corte esta certo e a pessoa so confirma. Comecar vazio faria redigitar o
     que a maquina ja acertou, que e o jeito de fazer a fila nao ser usada. */
  const sugerido = i.logradouro_marcado || i.logradouro_original || "";
  const txt = prompt(
    "Original: " + (i.logradouro_original || "(vazio)") +
    String.fromCharCode(10) +
    "Deixe como esta para CONFIRMAR, edite para CORRIGIR, ou apague para DESCARTAR.",
    sugerido);
  if (txt === null) return;                       // cancelou

  const limpo = txt.trim();
  const status = !limpo ? "descartado"
    : (limpo === sugerido ? "confirmado" : "corrigido");
  try {
    const r = await fetch(`/api/logradouro/${encodeURIComponent(i.fonte)}/`
      + `${encodeURIComponent(i.record_id)}/revisar`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({status: status,
                            logradouro_corrigido: status === "corrigido" ? limpo : "",
                            nota: ""}),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
    abrirFilaLogradouro();                        // recarrega com o novo estado
  } catch (e) {
    alert("Nao consegui gravar: " + e.message);
  }
}

async function abrirListaFachada(recorte) {
  $("fa-lista").classList.remove("hidden");
  document.body.classList.add("lista-aberta");
  $("fa-lista-itens").innerHTML = `<div class="fa-vazio">carregando…</div>`;
  const d = await (await fetch("/api/fachada/lista?recorte=" + encodeURIComponent(recorte))).json();
  faItens = d.itens || [];
  $("fa-lista-tit").textContent = d.rotulo || recorte;
  $("fa-lista-sub").textContent = `${faItens.length} ponto(s) — clique para o mapa ir até lá`;
  $("fa-lista-itens").innerHTML = faItens.length
    ? faItens.map((p, i) => `
      <button class="fa-item" data-i="${i}">
        <div class="fa-item-nome">${esc(p.nome || "(sem nome)")}</div>
        <div class="fa-item-sub">${esc((p.endereco || "").slice(0, 52))}</div>
        <div class="fa-item-tags">
          ${p.acao ? `<span class="tag ${p.acao === "aprovar" ? "alto"
                        : p.acao === "reprovar" ? "baixo" : "med"}">${esc(p.acao)}</span>` : ""}
          ${/* O letreiro é o que o auditor lê primeiro: é o nome REAL na
                parede, e ver que ele diverge do nome do cadastro é metade do
                trabalho de decidir. */""}
          ${p.letreiro ? `<span class="tag">🔤 ${esc(p.letreiro.slice(0, 26))}</span>` : ""}
          ${p.alvo_encontrado === "nao" ? `<span class="tag baixo">alvo não achado</span>` : ""}
          ${p.tipo_imovel ? `<span class="tag">${esc(p.tipo_imovel)}</span>` : ""}
          ${p.uso ? `<span class="tag">${esc(p.uso)}</span>` : ""}
          ${p.n_oport ? `<span class="tag med">${p.n_oport} oport.</span>` : ""}
          ${p.numero_confere === false ? `<span class="tag baixo">nº ${esc(p.numero_lido || "?")}</span>` : ""}
        </div>
      </button>`).join("")
    : `<div class="fa-vazio">nada neste recorte.</div>`;
  $("fa-lista-itens").querySelectorAll(".fa-item").forEach((b) => {
    b.onclick = () => selecionarFachada(parseInt(b.dataset.i));
  });
  faAtual = -1;
}

/* Ao escolher um item: o mapa vai até o ponto e a FICHA abre quase em tela
   cheia, com o mapa desfocado atrás. As imagens vêm do banco, coletadas no
   enriquecimento — a fachada avaliada e as fotos do Maps —, e cada uma amplia
   em tela cheia ao clique: conferir número de porta em miniatura não dá. */
let faAtual = -1;

async function selecionarFachada(i) {
  const p = faItens[i];
  if (!p) return;
  faAtual = i;
  document.querySelectorAll("#fa-lista-itens .fa-item").forEach((e, k) =>
    e.classList.toggle("sel", k === i));
  // O MOVIMENTO DO MAPA NÃO PODE DERRUBAR A FICHA. Um `flyTo` com coordenada
  // inválida (ou com o mapa ainda sem tamanho, quando `getZoom()` volta NaN)
  // lança, e a exceção abortava `selecionarFachada` inteira antes de abrir
  // qualquer coisa — o clique no item simplesmente não fazia nada.
  const la = Number(p.lat), lo = Number(p.lng);
  if (Number.isFinite(la) && Number.isFinite(lo)) {
    try {
      const z = map.getZoom();
      map.flyTo([la, lo], Number.isFinite(z) ? Math.max(z, 18) : 18, { duration: 0.7 });
    } catch { /* mapa indisponível: a ficha vale por si */ }
  }

  $("fa-ficha").classList.remove("hidden");
  document.body.classList.add("ficha-aberta");
  $("fa-ficha-nome").textContent = p.nome || "(sem nome)";
  $("fa-ficha-end").textContent = p.endereco || "";
  $("fa-ficha-pos").textContent = `${i + 1} / ${faItens.length}`;
  // o Street View do ponto exato, que é onde a leitura foi feita
  $("fa-ficha-maps").href = (p.lat != null)
    ? `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${p.lat},${p.lng}`
    : `https://www.google.com/maps/search/${encodeURIComponent(p.nome || "")}`;
  $("fa-ficha-imgs").innerHTML = `<div class="fa-vazio">carregando imagens…</div>`;
  $("fa-ficha-dados").innerHTML = "";

  const poi = await (await fetch("/api/pois/" + p.id)).json();
  if (faAtual !== i) return;              // o usuário já mudou de item
  const imgs = [{ src: window.comToken(`/api/sv/${p.id}/facade`), rot: "Fachada avaliada (Street View)" }]
    .concat((poi.fotos || []).slice(0, 8).map((u, k) => ({ src: u, rot: `Foto do Maps ${k + 1}`, ext: true })));
  $("fa-ficha-imgs").innerHTML = imgs.map((im, k) => `
    <figure class="fa-img" data-k="${k}">
      <img src="${esc(im.src)}" loading="lazy" ${im.ext ? 'referrerpolicy="no-referrer"' : ""}>
      <figcaption>${esc(im.rot)}</figcaption>
    </figure>`).join("");
  $("fa-ficha-imgs").querySelectorAll(".fa-img").forEach((f) => {
    f.onclick = () => ampliarImagem(imgs[parseInt(f.dataset.k)].src);
  });
  $("fa-ficha-dados").innerHTML =
    (poi.fachada ? blocoFachada(poi.fachada)
                 : `<div class="fa-vazio">sem leitura para este ponto.</div>`)
    + `<div class="fa-ficha-rodape">
         <button class="btn sm" id="fa-abrir-ficha">Ficha completa do POI</button>
       </div>`;
  const b = $("fa-abrir-ficha");
  if (b) b.onclick = () => { fecharFicha(); abrirPoi(p); };
}

function fecharFicha() {
  $("fa-ficha").classList.add("hidden");
  document.body.classList.remove("ficha-aberta");
}
function ampliarImagem(src) {
  $("fa-zoom-img").src = src;
  $("fa-zoom").classList.remove("hidden");
}
$("fa-ficha-fechar").onclick = fecharFicha;
$("fa-ficha-prev").onclick = () => selecionarFachada(Math.max(0, faAtual - 1));
$("fa-ficha-next").onclick = () => selecionarFachada(Math.min(faItens.length - 1, faAtual + 1));
$("fa-zoom").onclick = () => $("fa-zoom").classList.add("hidden");
$("fa-ficha").onclick = (e) => { if (e.target.id === "fa-ficha") fecharFicha(); };

/* Setas navegam a fila sem sair da ficha — é assim que se revisa cem pontos.
   O Esc fecha a camada mais interna primeiro: zoom, depois ficha, depois lista. */
document.addEventListener("keydown", (e) => {
  const zoomAberto = !$("fa-zoom").classList.contains("hidden");
  const fichaAberta = !$("fa-ficha").classList.contains("hidden");
  if (e.key === "Escape") {
    // fecha SÓ a camada mais interna, uma por vez: zoom → ficha → fila
    if (zoomAberto) { $("fa-zoom").classList.add("hidden"); return; }
    if (fichaAberta) { fecharFicha(); return; }
    if (!$("fa-lista").classList.contains("hidden")) { fecharListaFachada(); return; }
  }
  if (!fichaAberta || zoomAberto) return;
  if (e.key === "ArrowRight") selecionarFachada(Math.min(faItens.length - 1, faAtual + 1));
  if (e.key === "ArrowLeft") selecionarFachada(Math.max(0, faAtual - 1));
});
function fecharListaFachada() {
  $("fa-lista").classList.add("hidden");
  document.body.classList.remove("lista-aberta");
  fecharFicha();
}
$("fa-lista-fechar").onclick = fecharListaFachada;
// O Esc da LISTA está no ouvinte único mais abaixo, junto com o da ficha e o do
// zoom. Dois ouvintes separados fechavam tudo de uma vez: um Esc para sair da
// imagem ampliada levava a ficha e a fila junto.

$("btn-av-estimar").onclick = estimarAvaliacao;
$("av-modelo").onchange = estimarAvaliacao;
$("av-refazer").onchange = estimarAvaliacao;

$("btn-iniciar").onclick = async () => {
  let modoJob = modo;
  let opcoes;
  if (!(modo in MODOS_JOB)) {
    toast(`o modo "${modo}" não usa este botão`, "err");
    return;
  }
  if (modo === "planilha") {
    opcoes = {
      workers: parseInt($("op-workers").value) || 10,
      recuperar: $("op-recuperar").checked,
      retry_failed: $("op-retry").checked,
      gemini_direto: $("op-gemini").checked,
      no_proxy: !$("op-proxy").checked,
      cidade: $("op-cidade").value.trim(),
    };
  } else if (modo === "cadastur") {
    const municipio = ($("cad-municipio")?.value || "").trim();
    const uf = ($("cad-uf")?.value || "").trim().toUpperCase();
    // Valida AQUI também, e não só no servidor. O servidor é quem manda — e
    // nega igual —, mas deixar o pedido sair para levar 400 faz o operador
    // esperar o ida-e-volta para descobrir que faltava a UF.
    if (!municipio) return toast("Informe o município.", "err");
    if (uf.length !== 2) return toast("Informe a UF com duas letras.", "err");
    opcoes = { municipio, uf,
               gerar: $("cad-gerar")?.checked !== false,
               encadear: $("cad-encadear")?.checked !== false,
               pular_streetview: !!$("cad-pular-sv")?.checked,
               sem_pessoa_fisica: !!$("cad-sem-pf")?.checked,
               so_carregar: !!$("cad-so-carregar")?.checked };
  } else if (modo === "mineracao") {
    // Sem motor a escolher: `minerar_tudo.py` roda bases públicas e depois
    // captura + OCR. O município e a UF saem do polígono, no servidor — pedir
    // de novo aqui seria uma chance a mais de errar, e errar aqui é rodar a
    // cidade errada inteira.
    opcoes = { sessao: $("op-sessao").value.trim() || "mineracao",
               zoom: parseInt($("op-zoom").value) || 19,
               // Sem o dataset da UF a etapa 1 PARA e diz o comando que o
               // produz. Esta caixa é a saída para quem quer rodar só a captura
               // enquanto o dataset não existe.
               pular_bases: !!$("op-pular-bases")?.checked };
  } else if (modo === "enriquecimento") {  // cascata única Maps→Web→StreetView
    modoJob = "enriquecer_tudo";
    opcoes = {
      workers: parseInt($("op-enr-workers").value) || 6,
      no_proxy: !$("enr-proxy").checked,
      pular_maps: !$("enr-maps").checked,
      pular_cnpj_local: !$("enr-cnpj")?.checked,
      pular_web: !$("enr-web").checked,
      pular_streetview: !$("enr-sv").checked,
      sv_so_pobres: !!$("enr-sv-pobres")?.checked,
      incluir_ja_comerciais: !!$("enr-ja-comerciais")?.checked,
      visivel: !!$("enr-visivel")?.checked,
    };
  } else if (modo === "avaliar") {
    modoJob = "avaliar";
    opcoes = {
      modelo: $("av-modelo").value,
      // `workers` e `teto_usd` saíram com o motor antigo: a concorrência agora
      // é do lado do vLLM (LEITURA_CONCORRENCIA no .env) e não há dólar a
      // limitar num modelo local.
      limit: parseInt($("av-limit").value) || 0,
      refazer: !!$("av-refazer").checked,
      incluir_ja_comerciais: !!$("av-ja-comerciais")?.checked,
    };
  }
  const body = { modo: modoJob, opcoes };
  if (arquivoImportado) body.arquivo = arquivoImportado;

  const r = await pedirJob(body);
  if (!r) return;
  aplicarJob(r);
  $("log-panel").classList.remove("collapsed");
  toast(`Processo ${MODOS_JOB[modo]} iniciado ▶`, "ok");
};

$("btn-parar").onclick = () => confirmar(
  "Parar o processo?",
  "O job atual será encerrado. O que já foi salvo continua no banco e a retomada é idempotente (pode iniciar de novo depois).",
  async () => {
    await fetch("/api/jobs/parar", { method: "POST" });
    toast("Processo interrompido", "ok");
  });

const MODO_LABEL = { planilha: "planilha", mineracao: "mineração", minerar_web: "mineração web",
                     enriquecer_maps: "enriquecimento Maps", streetview: "Street View",
                     enriquecer_tudo: "enriquecimento (cascata)", baixar_imagens: "download de imagens",
                     cnpj_receita: "CNPJ x CNEFE (skill)", extracao_estadual: "extração estadual",
                     base_estadual: "base estadual (no i9)",
                     base_cadastur: "base do Cadastur/MTur",
                     avaliar: "avaliação de fachada" };
/* QUAL PAINEL DA DIREITA APARECE.
 *
 * Na aba "Avaliar candidatos" os cards da leitura substituem os do processo
 * genérico — "Encontrados"/"Sem match" não medem nada ali. Mas o cartão de
 * PROGRESSO precisa voltar assim que uma rodada começa, e essa decisão vivia
 * dentro do clique que troca de aba: quem já estava na aba e disparava o
 * processo dali nunca a executava de novo, e ficava sem barra nenhuma com a
 * carga andando — só reaparecia se saísse da aba e voltasse.
 *
 * Por isso a decisão virou função: o clique de aba chama, e `aplicarJob`
 * também, a cada mudança de estado do job. */
function sincronizarPaineis() {
  const av = modo === "avaliar";
  $("stats-fachada")?.classList.toggle("hidden", !av);
  $("stats-processo")?.classList.toggle("hidden", av && !jobRodando);
}

function aplicarJob(j) {
  jobRodando = j && j.status === "rodando";
  if (j && j.status && j.status !== "ocioso") ultimoJob = j;
  if (jobRodando) agendarStats();   // job em curso mantém o painel do banco vivo
  const labels = { rodando: "⚙️ Rodando", finalizado: "✅ Finalizado", parado: "⏹ Parado", erro: "❌ Erro", ocioso: "" };
  if (j && j.status && j.status !== "ocioso") {
    $("job-status-txt").innerHTML = `${labels[j.status] || j.status} — <b>${MODO_LABEL[j.modo] || j.modo}</b>` +
      (j.arquivo ? ` (${esc(j.arquivo)})` : "") + (j.fim ? ` às ${j.fim.slice(11, 16)}` : "");
  }
  if (j && j.contadores) aplicarContadores(j.contadores, j.total || 0, j.fase,
                                           j.fase_rotulo, j.rotulos);
  if (j && (j.status === "finalizado" || j.status === "parado" || j.status === "erro")) {
    carregarPois(); // sincroniza o mapa com o estado final do banco
    carregarStats();
    if (modo === "avaliar") carregarCardsFachada();  // o placar da leitura fecha
  }
  sincronizarPaineis();
  atualizarBotoes();
}

/* ────────────────────────────────────────────────────────────
   Cards de progresso
──────────────────────────────────────────────────────────── */
function bump(el) {
  const card = el.closest(".stat-card");
  if (!card) return;
  card.classList.remove("bump"); void card.offsetWidth; card.classList.add("bump");
}
function setVal(id, v) {
  const el = $(id);
  const txt = (v || 0).toLocaleString("pt-BR");
  if (el.textContent !== txt) { el.textContent = txt; bump(el); }
}
/* Cards "Dados do banco" (separados do processo em tempo real) */
let statsTimer = null;
/* Cidade em foco para o painel do banco. O foco da ferramenta vem de TRÊS
   lugares — o select de UF+município, o clique num município do mapa e a área
   desenhada — mas isto aqui só enxergava os dois primeiros: quem trabalha com
   polígono desenhado ficava com "DADOS DO BANCO" em traço permanente, que é
   justamente o único painel que lê do banco ao vivo. Sem município escolhido,
   a cidade sai da própria área, pela maioria dos POIs dentro dela. */
function cidadeEmFoco() {
  if (municipioNome) return municipioNome;
  if (!anelArea()) return null;
  const porCidade = new Map();
  for (const p of poisBase()) {
    const nome = (p.cidade || "").trim();
    if (nome) porCidade.set(nome, (porCidade.get(nome) || 0) + 1);
  }
  if (!porCidade.size) return null;
  return [...porCidade].sort((a, b) => b[1] - a[1])[0][0];
}

async function carregarStats() {
  const fmt = (v) => (v || 0).toLocaleString("pt-BR");
  const cidade = cidadeEmFoco();
  if (!cidade) { // sem município e sem área: não há foco, e traço é honesto
    ["db-validos", "db-tel", "db-cnpj", "db-sv", "db-fotos", "db-coments",
     "db-analisados", "db-aprov", "db-reprov", "db-rec"].forEach((id) => { if ($(id)) $(id).textContent = "—"; });
    dbCountLocal = 0;
    return;
  }
  try {
    const s = await (await fetch("/api/stats?cidade=" + encodeURIComponent(cidade))).json();
    const pct = (v) => s.validos ? ` (${Math.round(100 * (v || 0) / s.validos)}%)` : "";
    $("db-validos").textContent = fmt(s.validos);
    $("db-tel").textContent = fmt(s.com_telefone) + pct(s.com_telefone);
    $("db-cnpj").textContent = fmt(s.com_cnpj) + pct(s.com_cnpj);
    $("db-sv").textContent = fmt(s.com_streetview) + pct(s.com_streetview);
    $("db-fotos").textContent = fmt(s.fotos);
    $("db-coments").textContent = fmt(s.comentarios);
    if ($("db-analisados")) {
      const pctA = (v) => s.analisados ? ` (${Math.round(100 * (v || 0) / s.analisados)}%)` : "";
      $("db-analisados").textContent = fmt(s.analisados);
      $("db-aprov").textContent = fmt(s.aprovados) + pctA(s.aprovados);
      $("db-reprov").textContent = fmt(s.reprovados) + pctA(s.reprovados);
      $("db-rec").textContent = fmt(s.recomendar_visita);
    }
    if ($("db-count")) $("db-count").textContent = fmt(s.validos);
    dbCountLocal = s.validos;
  } catch { /* banco indisponível — mantém o último valor */ }
}
function agendarStats() { // pontos chegando ao vivo → atualiza o grupo do banco a cada 10s
  if (statsTimer) return;
  statsTimer = setTimeout(() => {
    statsTimer = null;
    carregarStats();
    // Fase que não manda POI pelo WebSocket não reagendava nada, e o painel
    // parava: a de Street View grava a fachada direto em `streetview_imgs`, sem
    // passar registro nenhum para o mapa. Enquanto houver job rodando, o grupo
    // do banco continua se atualizando por conta própria.
    if (jobRodando) agendarStats();
  }, 10000);
}

// A mineração por captura tem 4 fases e a UNIDADE da barra muda em cada uma:
// tile, recorte, POI. Sem dizer qual é, "90 de 264" parecia POI e o painel
// passava a impressão de que a busca tinha achado pouco — quando ela nem tinha
// começado.
const UNIDADE_FASE = { captura: "tiles", deteccao: "tiles", ocr: "recortes", busca: "POIs" };

// Rótulo padrão de cada cartão, para restaurar quando a fase sai do Street View.
/* OS CINCO cartões que trocam de rótulo conforme a fase — e eram três.
 *
 * `recuperados` e `descobertos` estavam de fora, então na leitura de fachada o
 * painel mostrava o número de revisões pedidas embaixo de "Recuperados (IA)" e
 * o de reprovações embaixo de "Descobertos". É o defeito contra o qual o
 * comentário lá embaixo já avisava: o número certo debaixo da palavra errada é
 * tão ruim quanto o número errado. O `id` de cada um vem do próprio nome da
 * chave (`st-` + chave), por isso `recup` e não `recuperados`. */
const ROTULO_PADRAO = { validos: "Encontrados", recup: "Recuperados (IA)",
                        desc: "Descobertos", semmatch: "Sem match",
                        ingeridos: "Gravados no banco" };
// A chave do rótulo que vem do servidor é o NOME DO CONTADOR; o id do elemento
// é abreviado. Sem esta ponte, `rotulos.recuperados` nunca encontraria `st-recup`.
const CHAVE_CONTADOR = { recup: "recuperados", desc: "descobertos" };

function aplicarContadores(c, total, fase, faseRotulo, rotulos) {
  setVal("st-proc", c.processados);
  setVal("st-validos", c.validos);
  setVal("st-recup", c.recuperados);
  setVal("st-desc", c.descobertos);
  setVal("st-fora", c.fora_area);
  setVal("st-semmatch", c.sem_match + (c.erros || 0));
  setVal("st-ingeridos", c.ingeridos);
  // Cada fase mede uma coisa. Na de fachadas, "Encontrados" e "Sem match" não
  // querem dizer nada — o número certo debaixo da palavra errada é tão ruim
  // quanto o número errado.
  for (const [k, padrao] of Object.entries(ROTULO_PADRAO)) {
    const el = $("st-" + k)?.parentElement?.querySelector(".stat-label");
    if (el) el.textContent = (rotulos && (rotulos[k] || rotulos[CHAVE_CONTADOR[k]])) || padrao;
  }
  const un = UNIDADE_FASE[fase] || "";
  const etapa = faseRotulo ? ` · ${faseRotulo}` : "";
  if (total > 0) {
    const pct = Math.min(100, (c.processados / total) * 100);
    $("st-bar").style.width = pct.toFixed(1) + "%";
    $("st-total-sub").textContent =
      `${(c.processados || 0).toLocaleString("pt-BR")} de ${total.toLocaleString("pt-BR")}`
      + (un ? ` ${un}` : "") + ` (${pct.toFixed(0)}%)${etapa}`;
  } else {
    $("st-bar").style.width = c.processados ? "100%" : "0%";
    $("st-total-sub").textContent = jobRodando
      ? (faseRotulo || "processando…") : "aguardando processo…";
  }
}

/* ────────────────────────────────────────────────────────────
   WebSocket — tempo real
──────────────────────────────────────────────────────────── */
let ws = null;
let dbCountLocal = 0;

async function conectarWS() {
  // O token vai na query: o navegador não permite cabeçalho no handshake de
  // WebSocket, então `Authorization` não existe aqui. É o mesmo token da
  // sessão, e o servidor recusa se faltar ou estiver vencido.
  //
  // RENOVA ANTES de apresentar o crachá. Este caminho não passa pelo
  // `window.fetch`, então não herda a renovação automática: sem esta linha, ao
  // vencer a hora o soquete caía e a reconexão insistia com o token morto a
  // cada 2,5 s indefinidamente — barra parada e log mudo com o processo vivo.
  if (window.crVencendo && window.crVencendo()) await window.crRenovar?.();
  const _tok = sessionStorage.getItem("cr_token") || "";
  if (!_tok) {
    // SEM SESSÃO, não tenta. O `onclose` reagenda a cada 2,5 s, então tentar
    // antes do login enchia o servidor de 403 num laço que só parava quando
    // alguém entrasse — e o log ficava ilegível justamente na hora em que se
    // quer ler o log. Em vez de sondar, espera o aviso de que a sessão existe.
    document.addEventListener("cr:sessao", conectarWS, { once: true });
    return;
  }
  ws = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(_tok)}`);
  ws.onopen = () => $("ws-badge").classList.add("on");
  ws.onclose = () => {
    $("ws-badge").classList.remove("on");
    // Sem token não adianta reagendar: a sessão caiu e quem devolve o WebSocket
    // é o próximo login, pelo evento acima.
    // Na reconexão, tenta renovar: se o soquete caiu POR o token ter vencido,
    // reapresentar o mesmo não vai adiantar nunca.
    if (sessionStorage.getItem("cr_token")) setTimeout(conectarWS, 2500);
    else document.addEventListener("cr:sessao", conectarWS, { once: true });
  };
  ws.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.tipo === "poi" && msg.poi) {
      addPoi(msg.poi, true);
      dbCountLocal += 1;
      $("db-count").textContent = dbCountLocal.toLocaleString("pt-BR");
      agendarStats();
    } else if (msg.tipo === "progresso" && msg.dados) {
      aplicarContadores(msg.dados.contadores || {}, msg.dados.total || 0,
                        msg.dados.fase, msg.dados.fase_rotulo, msg.dados.rotulos);
    } else if (msg.tipo === "job") {
      aplicarJob(msg.dados || {});
    } else if (msg.tipo === "log" && msg.linha) {
      logLinha(msg.linha);
    } else if (msg.tipo === "reload") {
      carregarPois();
      carregarStats();
    }
  };
  // ping para manter a conexão viva
  setInterval(() => { if (ws && ws.readyState === 1) ws.send("ping"); }, 25000);
}

/* ────────────────────────────────────────────────────────────
   Log, confirmação e toasts
──────────────────────────────────────────────────────────── */
const logBody = $("log-body");
function logLinha(l) {
  const atEnd = logBody.scrollTop + logBody.clientHeight >= logBody.scrollHeight - 30;
  logBody.textContent += l + "\n";
  const linhas = logBody.textContent.split("\n");
  if (linhas.length > 400) logBody.textContent = linhas.slice(-300).join("\n");
  if (atEnd) logBody.scrollTop = logBody.scrollHeight;
}
$("log-head").onclick = () => {
  $("log-panel").classList.toggle("collapsed");
  $("log-toggle").textContent = $("log-panel").classList.contains("collapsed") ? "▲" : "▼";
};

let confirmCb = null;
function confirmar(titulo, htmlTexto, cb) {
  $("confirm-title").textContent = titulo;
  $("confirm-text").innerHTML = htmlTexto;
  $("confirm-overlay").classList.remove("hidden");
  confirmCb = cb;
}
$("confirm-no").onclick = () => { $("confirm-overlay").classList.add("hidden"); confirmCb = null; };
$("confirm-yes").onclick = async () => {
  $("confirm-overlay").classList.add("hidden");
  if (confirmCb) { const cb = confirmCb; confirmCb = null; await cb(); }
};

function toast(txt, tipo = "") {
  const t = document.createElement("div");
  t.className = `toast ${tipo}`;
  t.textContent = txt;
  $("toast-root").appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .4s"; }, 3400);
  setTimeout(() => t.remove(), 3900);
}

/* ────────────────────────────────────────────────────────────
   Busca flutuante sobre o mapa (autocomplete ao vivo)
──────────────────────────────────────────────────────────── */
const bWrap = $("busca-wrap"), bInput = $("busca-input"), bResults = $("busca-results"),
      bOverlay = $("busca-overlay"), bClear = $("busca-clear");
let bSel = -1, bLista = [];

function _norm(s) {
  return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

function buscar(termo) {
  const q = _norm(termo).trim();
  if (!q) return [];
  const toks = q.split(/\s+/);
  const out = [];
  for (const p of allPois.values()) {
    const nome = _norm(p.nome), end = _norm(p.endereco), cat = _norm(p.categoria);
    const alvo = nome + " " + end + " " + cat;
    if (!toks.every((t) => alvo.includes(t))) continue;
    // ranking: começa com o termo > nome contém > resto
    let score = 0;
    if (nome.startsWith(q)) score = 100;
    else if (nome.includes(q)) score = 60;
    else if (toks.every((t) => nome.includes(t))) score = 40;
    else if (cat.includes(q)) score = 20;
    else score = 10;
    if (p.tem_cnpj) score += 2;
    out.push({ p, score });
  }
  out.sort((a, b) => b.score - a.score || a.p.nome.length - b.p.nome.length);
  return out.slice(0, 8).map((x) => x.p);
}

function _hl(texto, q) {
  if (!q) return esc(texto);
  const i = _norm(texto).indexOf(_norm(q));
  if (i < 0) return esc(texto);
  return esc(texto.slice(0, i)) + "<mark>" + esc(texto.slice(i, i + q.length)) + "</mark>" + esc(texto.slice(i + q.length));
}

function renderResultados(termo) {
  bLista = buscar(termo);
  bSel = -1;
  if (!termo.trim()) { bResults.classList.remove("show"); return; }
  if (!bLista.length) {
    bResults.innerHTML = `<div class="bres-empty">Nada encontrado para “${esc(termo)}”.</div>`;
    bResults.classList.add("show");
    return;
  }
  bResults.innerHTML = `<div class="bres-head">${bLista.length} resultado${bLista.length > 1 ? "s" : ""}</div>` +
    bLista.map((p, i) => {
      const k = catInfo(p.categoria, p.fonte);
      const sub = [p.categoria, p.endereco].filter(Boolean).join(" · ");
      const tags = [];
      if (p.tem_cnpj) tags.push('<span class="bres-tag" style="background:#e6f4ea;color:#1c853a">CNPJ</span>');
      if (p.status === "recuperado_web") tags.push('<span class="bres-tag" style="background:#fce4ec;color:#c2185b">web</span>');
      return `<div class="bres" data-i="${i}">
        <div class="bres-pin" style="--c:${k.cor}">${k.emo}</div>
        <div class="bres-body">
          <div class="bres-nome">${_hl(p.nome, termo)}</div>
          <div class="bres-sub">${esc(sub)}</div>
        </div>
        <div class="bres-tags">${tags.join("")}</div>
      </div>`;
    }).join("");
  bResults.classList.add("show");
  bResults.querySelectorAll(".bres").forEach((el) => {
    el.onmousedown = (e) => { e.preventDefault(); irParaResultado(+el.dataset.i); };
  });
}

function irParaResultado(i) {
  const p = bLista[i];
  if (!p) return;
  fecharBusca();
  if (p.lat != null) map.setView([p.lat, p.lng], 18, { animate: true });
  setTimeout(() => abrirPoi(p), 250);
}

function abrirBusca() {
  bWrap.classList.add("ativo");
  bOverlay.classList.remove("hidden");
  if (bInput.value.trim()) renderResultados(bInput.value);
}
function fecharBusca() {
  bWrap.classList.remove("ativo");
  bOverlay.classList.add("hidden");
  bResults.classList.remove("show");
  bInput.blur();
}

let bDebounce = null;
bInput.addEventListener("focus", abrirBusca);
bInput.addEventListener("input", () => {
  bClear.classList.toggle("hidden", !bInput.value);
  clearTimeout(bDebounce);
  bDebounce = setTimeout(() => renderResultados(bInput.value), 110);
});
bInput.addEventListener("keydown", (e) => {
  const itens = bResults.querySelectorAll(".bres");
  if (e.key === "ArrowDown") { e.preventDefault(); bSel = Math.min(bSel + 1, itens.length - 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); bSel = Math.max(bSel - 1, 0); }
  else if (e.key === "Enter") { if (bSel >= 0) irParaResultado(bSel); else if (bLista.length) irParaResultado(0); return; }
  else if (e.key === "Escape") { fecharBusca(); return; }
  else return;
  itens.forEach((el, i) => el.classList.toggle("sel", i === bSel));
  if (bSel >= 0) itens[bSel].scrollIntoView({ block: "nearest" });
});
bClear.onclick = () => { bInput.value = ""; bClear.classList.add("hidden"); renderResultados(""); bInput.focus(); };
bOverlay.onclick = fecharBusca;

/* Skill `tratamento-cnpj` — por MUNICÍPIO, à parte da cascata.
   Não é caixa da cascata porque a unidade de trabalho é outra: a cascata anda
   POI a POI, esta roda o município inteiro de uma vez. E como só há um job por
   vez, encaixá-la ali obrigaria a encadear. */
if ($("btn-cnpj-skill")) $("btn-cnpj-skill").onclick = async () => {
  if (jobRodando) { toast("Já há um processo rodando.", "err"); return; }
  const r = await pedirJob({ modo: "cnpj_receita", opcoes: {} });
  if (!r) return;
  aplicarJob(r);
  toast("Cruzando Receita × CNEFE no município da área…");
};

/* Base estadual — produzir/atualizar no i9.
 *
 * Botão próprio e não caixa da mineração: é a MATÉRIA-PRIMA dela, roda noutra
 * máquina e leva horas. Encaixá-lo na cascata obrigaria toda mineração a
 * esperar por ele. */
if ($("btn-base-cadastur")) $("btn-base-cadastur").onclick = async () => {
  if (jobRodando) { toast("Já há um processo rodando.", "err"); return; }
  const uf = ($("bc-uf")?.value || "").trim().toUpperCase();
  if (uf.length !== 2) { toast("Informe a UF com duas letras.", "err"); return; }
  const atualizar = !!$("bc-atualizar")?.checked;
  // O download é NACIONAL — a UF só recorta o que vira POI depois. Dizer isso
  // antes evita a surpresa de ver 26 recursos do país inteiro entrando quando
  // se pediu "RS".
  if (!confirm(`Baixar a base do Cadastur/MTur${atualizar ? " (dado NOVO)" : ""}?

`
             + `O download é do BRASIL inteiro — a UF ${uf} recorta só o que vira POI.
`
             + `Depois disso, toda mineração apenas consulta o que ficou em disco.`)) return;
  const r = await pedirJob({ modo: "base_cadastur", opcoes: { uf, atualizar } });
  if (!r) return;
  aplicarJob(r);
  toast(`🏛️ ${atualizar ? "Atualizando" : "Baixando"} o Cadastur — acompanhe pelo log.`, "ok");
};

if ($("btn-base-estadual")) $("btn-base-estadual").onclick = async () => {
  if (jobRodando) { toast("Já há um processo rodando.", "err"); return; }
  const uf = ($("be-uf")?.value || "").trim().toUpperCase();
  // Confere ANTES do ida-e-volta: esperar o servidor para descobrir que faltava
  // a UF é o tipo de espera que não ensina nada.
  if (uf.length !== 2) { toast("Informe a UF com duas letras.", "err"); return; }
  const atualizar = !!$("be-atualizar")?.checked;
  if (atualizar &&
      !confirm(`Atualizar a base de ${uf} busca dado NOVO e leva horas no i9.\n\n`
               + `Sem isto, uma base já pronta é reaproveitada. Continuar?`)) return;
  const r = await pedirJob({ modo: "base_estadual", opcoes: { uf, atualizar } });
  if (!r) return;
  aplicarJob(r);
  toast(`📚 ${atualizar ? "Atualizando" : "Produzindo"} a base de ${uf} no i9 — `
        + `acompanhe pelo log.`, "ok");
};

/* Botão "Baixar imagens" (passo pós, à parte da cascata) */
$("btn-baixar-imgs").onclick = async () => {
  if (jobRodando) { toast("Já há um processo rodando.", "err"); return; }
  try {
    if (!await pedirJob({ modo: "baixar_imagens", opcoes: { workers: 8 } })) return;
    toast("📥 Baixando imagens para o banco…", "ok");
  } catch { toast("Falha ao iniciar o download", "err"); }
};

/* ────────────────────────────────────────────────────────────
   Boot
──────────────────────────────────────────────────────────── */
(async function boot() {
  await carregarArea();
  await carregarPois();            // carrega todos em allPois; nada aparece até clicar num município
  carregarStats();                 // painel em branco até selecionar
  carregarMalha();                 // divisas municipais (IBGE) — enquadra na malha; clique seleciona
  map.on("moveend", malhaSegueMapa);   // navegou para outro estado? baixa a malha de lá
  atualizarBannerMunicipio();      // badge "clique num município"
  aplicarNivel();                  // esconde o que este nível não executa
  conectarWS();
  try { aplicarJob(await (await fetch("/api/jobs/atual")).json()); } catch { /* ok */ }
  atualizarBotoes();
  toast("Clique num município no mapa para ver os dados 🗺️", "ok");
})();

/* ────────────────────────────────────────────────────────────
   QUADRAS — área → vias → quadras → pontos → faces
   O ponto é desenhado na coordenada ORIGINAL do CNEFE. Nada é
   deslocado: a cor diz a que face ele pertence e se a numeração
   dele condiz com a paridade daquela face.
──────────────────────────────────────────────────────────── */
const COR_FACE = ["#00f5ff", "#ff9628", "#7c5cff", "#3ea64a", "#ff4d6d", "#ffd166",
                  "#22d3ee", "#f472b6", "#a3e635", "#fb923c", "#60a5fa", "#e879f9",
                  "#2dd4bf", "#facc15", "#c084fc", "#4ade80"];
/* Quando UMA via separa duas quadras, cada uma tem a sua face ali. Colorir por
   `face_idx` dava a mesma cor às duas (o índice reinicia em cada quarteirão) e as
   linhas caíam uma sobre a outra — não dava para saber de quem era cada ponto.
   A cor passa a ser por (quadra, face), única na sessão, e a linha é DESLOCADA
   para o lado da sua quadra, então a rua compartilhada mostra as duas. */
let qCorFace = new Map();
const chaveQF = (qid, fi) => `${qid}:${fi}`;

function mapearCoresFaces(faces) {
  qCorFace = new Map();
  faces.slice()
    .sort((a, b) => (a.properties.quadra_id - b.properties.quadra_id)
                 || (a.properties.face_idx - b.properties.face_idx))
    .forEach((f, i) => qCorFace.set(
      chaveQF(f.properties.quadra_id, f.properties.face_idx),
      COR_FACE[i % COR_FACE.length]));
}

/* Desloca a linha da face ~3 m para dentro da sua quadra, para que duas faces
   sobre a mesma rua não se sobreponham. Só o desenho muda; o dado não. */
function deslocarParaDentro(coords, centro, metros = 3) {
  if (!coords || coords.length < 2 || !centro) return coords;
  const [x0, y0] = coords[0], [x1, y1] = coords[coords.length - 1];
  const k = Math.cos((y0 * Math.PI) / 180);
  let dx = (x1 - x0) * k, dy = y1 - y0;
  const n = Math.hypot(dx, dy) || 1e-9;
  let nx = -dy / n, ny = dx / n;                       // normal da linha
  const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
  if ((centro[0] - mx) * k * nx + (centro[1] - my) * ny < 0) { nx = -nx; ny = -ny; }
  const g = metros / 111320;
  return coords.map(([x, y]) => [x + (nx * g) / k, y + ny * g]);
}
const COR_DESTOA = "#ef4444";      // numeração do outro lado da rua
const COR_INDEF = "#9aa4b0";       // sem número ou face sem paridade

let qCamadas = [];                 // tudo que a sessão desenhou, para limpar
let qSessaoAtual = null;
let qFaceFiltro = null;
let qDados = null;

/* Cada camada tem NOME para o modal de visualização poder ligar e desligar uma a
   uma. `qVis` guarda a escolha do usuário e sobrevive ao recarregar a sessão. */
let qLayers = {};
const qVis = { area: true, vias: true, quadraOsm: true, quadraReal: true,
               faces: true, faceReal: true, ligacoes: true, alinhados: true,
               origens: true, telhados: true };
/* filtros por CLASSE do ponto, aplicados dentro das camadas de pontos */
const qMostra = { aprovado: true, resgatado: true, reprovado: true, semJulg: true };

function addCamada(nome, layer) {
  qCamadas.push(layer);
  qLayers[nome] = layer;
  if (qVis[nome] === false) { try { map.removeLayer(layer); } catch { /* ok */ } }
  return layer;
}

/* Fecha qualquer tooltip aberto.

   Leaflet só fecha o tooltip no `mouseout` do elemento. Esconder um <path> com
   `display:none` (é o que o modal de visualização faz) NÃO dispara mouseout, e
   remover a camada com o tooltip aberto também não — nos dois casos ele fica
   órfão na tela, preso até a página recarregar. O tooltip entra no mapa como
   camada, então dá para varrer e remover. */
function fecharTooltips() {
  try {
    map.eachLayer((l) => { if (l instanceof L.Tooltip) map.removeLayer(l); });
  } catch { /* mapa ainda não montado */ }
}


function classeDoPonto(p) {
  if (p.canonico === true) return p.resgate ? "resgatado" : "aprovado";
  if (p.canonico === false) return "reprovado";
  return "semJulg";
}

function porCamadaQ(nome) {
  return (qDados?.features || []).filter((f) => f.properties.camada === nome);
}

/* Uma cor por face DENTRO de cada quadra: o índice reinicia a cada quarteirão,
   senão duas quadras vizinhas ficariam com a mesma paleta deslocada. */
function corDaFace(qid, fi) {
  return qCorFace.get(chaveQF(qid, fi)) || COR_FACE[(fi ?? 0) % COR_FACE.length];
}

/* Escurece a cor da face para o ponto que entrou por RESGATE (coordenada nível 1
   perto da quadra, com a numeração contra). É aprovação por outra evidência, e
   isso precisa se distinguir de quem passou pela regra principal. */
function escurecer(hex, fator = 0.55) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const r = Math.round(((n >> 16) & 255) * fator);
  const g = Math.round(((n >> 8) & 255) * fator);
  const b = Math.round((n & 255) * fator);
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`;
}


/* Clicar numa face isola os pontos dela — é assim que se confere, olhando um
   lado da rua de cada vez, se a paridade separou certo. */
/* A face é identificada por QUADRA + índice: o `face_idx` reinicia em cada
   quarteirão, então usá-lo sozinho junta a face 0 de 19 quadras num item só. */
function chaveFace(p) {
  return p.face_idx == null ? null : `${p.quadra_id}:${p.face_idx}`;
}

function aplicarFiltroFace() {
  qCamadas.forEach((g) => g.eachLayer && g.eachLayer((l) => {
    const p = l.feature?.properties;
    if (!p || p.camada !== "ponto" || !l.setStyle) return;
    const ok = qFaceFiltro === null || chaveFace(p) === qFaceFiltro;
    l.setStyle({ opacity: ok ? 0.9 : 0.06, fillOpacity: ok ? 0.95 : 0.04 });
  }));
}

/* Legenda por QUADRA: cada quarteirão com as suas faces, a via canônica, a
   paridade que venceu e a contagem (condiz / destoa). Clicar isola a face. */
function montarLegendaQuadras() {
  const el = $("q-legenda");
  if (!el) return;
  const cont = new Map();
  porCamadaQ("ponto").forEach((f) => {
    const k = chaveFace(f.properties);
    if (!k) return;
    const c = cont.get(k) || { ok: 0, destoa: 0, indef: 0 };
    if (f.properties.canonico === true) c.ok++;
    else if (f.properties.canonico === false) c.destoa++;
    else c.indef++;
    cont.set(k, c);
  });
  const faces = porCamadaQ("face").map((f) => f.properties);
  if (!faces.length) { el.innerHTML = `<div class="hint">sem faces classificadas</div>`; return; }
  const porQuadra = new Map();
  faces.forEach((p) => {
    if (!porQuadra.has(p.quadra_id)) porQuadra.set(p.quadra_id, []);
    porQuadra.get(p.quadra_id).push(p);
  });
  el.innerHTML = [...porQuadra.entries()].sort((a, b) => a[0] - b[0]).map(([qid, fs]) =>
    `<div class="q-grupo"><div class="q-grupo-tit">quadra ${qid}</div>` +
    fs.sort((a, b) => a.face_idx - b.face_idx).map((p) => {
      const k = `${p.quadra_id}:${p.face_idx}`;
      const c = cont.get(k) || { ok: 0, destoa: 0 };
      return `<button class="conf-item${qFaceFiltro === k ? " on" : ""}" data-face="${k}"
                title="${esc(p.paridade_por || "")}">
         <i style="background:${corDaFace(p.quadra_id, p.face_idx)}"></i>
         <span class="conf-nome">${esc(p.nome_canonico || p.nome_osm || "via não lida")}
           — ${p.paridade || "indefinida"}</span>
         <b>${c.ok}</b><span style="color:${COR_DESTOA};margin-left:6px">${c.destoa}</span>
       </button>`;
    }).join("") + `</div>`).join("");
  el.querySelectorAll(".conf-item").forEach((b) => {
    b.onclick = () => {
      qFaceFiltro = qFaceFiltro === b.dataset.face ? null : b.dataset.face;
      aplicarFiltroFace();
      montarLegendaQuadras();
    };
  });
}

/* Quadras tratadas — MODAL, não lista no painel: são dezenas de linhas com rua,
   contagem e paridade, e o painel lateral é estreito demais para isso. Clicar
   numa carrega a sessão dela, leva o mapa até ela e fecha o modal. */
let qListaCache = [];
let qQuadraFoco = null;

async function abrirQuadrasTratadas() {
  abrirModal(`<div class="mdl-tit">📋 Quadras tratadas</div>
    <div class="hint">carregando…</div>`);
  try {
    const r = await fetch("/api/quadras/lista");
    const j = await r.json();
    if (j.erro) throw new Error(j.erro);
    if (!Array.isArray(j)) throw new Error("resposta inesperada da API");
    qListaCache = j;
  } catch (e) {
    // erro VISÍVEL: antes eu engolia a falha e o painel só ficava vazio, o que
    // parecia "não há quadras" quando na verdade a rota nem existia.
    // "sessão não encontrada" aqui é assinatura de SERVIDOR ANTIGO: sem a rota
    // /api/quadras/lista, o /api/quadras/{sid} captura a palavra "lista".
    const msg = String(e.message || e);
    const velho = /sess[aã]o n[aã]o encontrada|desatualizado|rota, n[aã]o uma sess/i.test(msg);
    abrirModal(`<div class="mdl-tit">📋 Quadras tratadas</div>` +
      (velho
        ? `<div class="hint warn">O servidor em execução é anterior a esta tela.</div>
           <div class="hint">Feche a janela do <code>python server.py</code> e suba de novo —
             a rota <code>/api/quadras/lista</code> só existe depois de reiniciar.
             As quadras já tratadas continuam no banco.</div>`
        : `<div class="hint warn">não consegui ler a lista: ${esc(msg)}</div>`) +
      `<div class="row right"><button class="btn" onclick="fecharModal()">Fechar</button></div>`);
    return;
  }
  desenharQuadrasTratadas();
}

function desenharQuadrasTratadas() {
  const b = (window._qFiltro || "").trim().toLowerCase();
  const itens = qListaCache.filter((q) => !b ||
    String(q.id).includes(b) || (q.vias || "").toLowerCase().includes(b) ||
    (q.municipio || "").toLowerCase().includes(b));
  const linhas = itens.map((q) => `
    <button class="q-item${qQuadraFoco === q.id ? " on" : ""}" data-q="${q.id}"
            data-s="${esc(q.sessao_id)}" data-lat="${q.lat}" data-lng="${q.lng}">
      <b>quadra ${q.id}</b> · ${esc(q.municipio || "?")}/${esc(q.uf || "?")}
      · <b>${q.canonicos}</b>/${q.pontos} pontos canônicos
      <small>${esc(q.vias || "— sem via identificada —")}</small>
      <small>${q.faces_com_paridade}/${q.faces} faces com paridade${
        q.area_m2 ? ` · ${Math.round(q.area_m2).toLocaleString("pt-BR")} m²` : ""}
        · ${esc((q.criado_em || "").replace("T", " ").slice(0, 16))}</small>
    </button>`).join("");
  abrirModal(`<div class="mdl-tit">📋 Quadras tratadas
      <small style="font-weight:400;color:var(--ink-2)"> — ${qListaCache.length}</small></div>
    <input id="q-busca-modal" class="sel-larga" placeholder="filtrar por rua, cidade ou id…"
           value="${esc(window._qFiltro || "")}">
    <div class="q-lista">${linhas || `<div class="hint">${
      qListaCache.length ? "nada com esse filtro" : "nenhuma quadra tratada ainda"}</div>`}</div>
    <div class="row right"><button class="btn" onclick="fecharModal()">Fechar</button></div>`);
  const inp = $("q-busca-modal");
  if (inp) {
    inp.oninput = () => { window._qFiltro = inp.value; desenharQuadrasTratadas();
                          $("q-busca-modal").focus(); };
    if (b) inp.setSelectionRange(inp.value.length, inp.value.length);
  }
  document.querySelectorAll("#modal-card .q-item").forEach((bt) => {
    bt.onclick = async () => {
      qQuadraFoco = Number(bt.dataset.q);
      fecharModal();
      await carregarQuadras(bt.dataset.s, true);
      map.setView([Number(bt.dataset.lat), Number(bt.dataset.lng)], 18);
    };
  });
}

/* Modal de VISUALIZAÇÃO — liga e desliga cada camada e cada classe de ponto.
   O mapa acumula muita coisa (vias, quadra do OSM, borda real, faces, trilho,
   origem, alinhado e a ligação entre os dois); ver um recorte de cada vez é o
   que torna possível conferir. A escolha fica em `qVis`/`qMostra` e sobrevive ao
   recarregar a sessão. */
const VIS_CAMADAS = [
  ["Geometria", [
    ["area", "área desenhada", "#ffffff", 1],
    ["quadraOsm", "quadra do OSM (eixo das vias)", "#ffffff", 1],
    ["quadraReal", "borda real do quarteirão", "#ffffff", 1],
    ["vias", "vias do OSM", "#ffd400", 1],
    ["faces", "faces (cor por face)", "#7c5cff", 1],
    ["faceReal", "borda real de cada face (trilho)", "#22d3ee", 1],
    ["telhados", "telhados do Overture", "#7c5cff", 0],
  ]],
  ["Pontos", [
    ["origens", "posição original do CNEFE", "#9aa4b0", 0],
    ["alinhados", "posição alinhada na face", "#3ea64a", 0],
    ["ligacoes", "linha origem → alinhado", "#9aa4b0", 1],
  ]],
];
const VIS_CLASSES = [
  ["aprovado", "aprovados pela regra da face", "#3ea64a"],
  ["resgatado", "aprovados por outra evidência (tom escuro)", "#225b29"],
  ["reprovado", "do outro lado da via / numeração destoa", "#ef4444"],
  ["semJulg", "sem julgamento", "#9aa4b0"],
];
/* pontos que a régua levaria além do teto e por isso FICARAM onde estavam —
   anel tracejado no mapa. Não é classe: é um estado do alinhamento. */
function naoAlinhados() {
  return porCamadaQ("ponto")
    .filter((f) => f.properties.alinhado_modo === "perpendicular");
}

function contarClasses() {
  const c = { aprovado: 0, resgatado: 0, reprovado: 0, semJulg: 0 };
  porCamadaQ("ponto").forEach((f) => { c[classeDoPonto(f.properties)]++; });
  return c;
}

function abrirVisualizacao() {
  if (!qDados) { toast("carregue uma sessão primeiro", "err"); return; }
  const cont = contarClasses();
  const nCam = (k) => {
    const l = qLayers[k];
    if (!l) return 0;
    let n = 0; l.eachLayer(() => n++); return n;
  };
  abrirModal(`<div class="mdl-tit">👁 Visualização</div>
    <div class="hint">Clique para ligar ou desligar. Serve para ver um recorte de
      cada vez — por exemplo só os alinhados, ou só o que foi reprovado.</div>
    ${VIS_CAMADAS.map(([g, itens]) => `<div class="vis-grupo">${g}</div>` +
      itens.map(([k, rot, cor, linha]) => `
        <div class="vis-item${qVis[k] ? "" : " off"}" data-cam="${k}">
          <i class="${linha ? "linha" : ""}" style="background:${cor}"></i>
          <span>${rot}</span><span class="vis-n">${nCam(k)}</span>
        </div>`).join("")).join("")}
    <div class="vis-grupo">Classes de ponto</div>
    ${VIS_CLASSES.map(([k, rot, cor]) => `
      <div class="vis-item${qMostra[k] ? "" : " off"}" data-classe="${k}">
        <i style="background:${cor}"></i><span>${rot}</span>
        <span class="vis-n">${cont[k]}</span>
      </div>`).join("")}
    <div class="vis-grupo">Alinhamento</div>
    <div class="hint" style="margin:2px 0 6px">
      <b>Anel tracejado</b> = a régua o levaria a mais de 10 m, então ele foi só
      projetado <b>perpendicular à testada</b> — o movimento mínimo, sem entrar na
      ordem da numeração. Ali as duas fontes discordam de verdade.
      <span class="vis-n"><b>${naoAlinhados().length}</b> pontos</span>
    </div>
    <div class="row right" style="margin-top:10px">
      <button class="btn ghost" id="vis-tudo">tudo</button>
      <button class="btn ghost" id="vis-so-alinhados">só alinhados</button>
      <button class="btn" onclick="fecharModal()">Fechar</button>
    </div>`);

  document.querySelectorAll("#modal-card .vis-item[data-cam]").forEach((el) => {
    el.onclick = () => { qVis[el.dataset.cam] = !qVis[el.dataset.cam];
                         aplicarVisualizacao(); abrirVisualizacao(); };
  });
  document.querySelectorAll("#modal-card .vis-item[data-classe]").forEach((el) => {
    el.onclick = () => { qMostra[el.dataset.classe] = !qMostra[el.dataset.classe];
                         aplicarVisualizacao(); abrirVisualizacao(); };
  });
  $("vis-tudo").onclick = () => {
    Object.keys(qVis).forEach((k) => { qVis[k] = true; });
    Object.keys(qMostra).forEach((k) => { qMostra[k] = true; });
    aplicarVisualizacao(); abrirVisualizacao();
  };
  $("vis-so-alinhados").onclick = () => {
    Object.keys(qVis).forEach((k) => { qVis[k] = false; });
    qVis.alinhados = qVis.faceReal = qVis.quadraReal = true;
    aplicarVisualizacao(); abrirVisualizacao();
  };
}

function aplicarVisualizacao() {
  fecharTooltips();       // o que vai sumir agora não pode deixar tooltip preso
  Object.entries(qLayers).forEach(([k, l]) => {
    if (!l) return;
    const quer = qVis[k] !== false;
    const tem = map.hasLayer(l);
    if (quer && !tem) map.addLayer(l);
    if (!quer && tem) map.removeLayer(l);
  });
  // classes: esconde o elemento, sem desmontar a camada
  ["origens", "alinhados", "ligacoes"].forEach((k) => {
    const l = qLayers[k];
    if (!l || !l.eachLayer) return;
    l.eachLayer((m) => {
      const p = m.feature?.properties;
      if (!p) return;
      const el = m.getElement ? m.getElement() : null;
      if (el) el.style.display = qMostra[classeDoPonto(p)] ? "" : "none";
    });
  });
}

$("btn-q-ver") && ($("btn-q-ver").onclick = abrirVisualizacao);
$("btn-q-tratadas") && ($("btn-q-tratadas").onclick = abrirQuadrasTratadas);
$("btn-q-limpar") && ($("btn-q-limpar").onclick = () => {
  limparQuadras();
  qDados = null; qSessaoAtual = null; qFaceFiltro = null; qQuadraFoco = null;
  $("q-legenda").innerHTML = "";
  $("q-estado").textContent = "mapa limpo";
});

/* Carrega a sessão MAIS RECENTE. Não há escolha de sessão nem de passo na tela:
   o botão azul roda o processo inteiro, como nos outros modos. Rodar passo a
   passo é coisa do terminal (`quadras.py vias|borda|pontos|faces --sessao S`),
   que é onde se conserta uma execução que quebrou no meio. */

/* Enquanto o job de quadras roda, o mapa vai preenchendo a cada passo. */
let qTimer = null;

/* ────────────────────────────────────────────────────────────
   Clique LONGO no mapa copia a coordenada
   Pressione ~600 ms em qualquer ponto: copia "lat, lng" para a
   área de transferência, no formato que o Google Maps aceita.
──────────────────────────────────────────────────────────── */
(function copiarCoordNoCliqueLongo() {
  const MS = 600, TOLERANCIA_PX = 8;
  let timer = null, inicio = null;

  async function copiar(txt) {
    try {
      await navigator.clipboard.writeText(txt);
      return true;
    } catch {
      // clipboard bloqueado (http, permissão): cai no truque do textarea
      try {
        const ta = document.createElement("textarea");
        ta.value = txt;
        ta.style.cssText = "position:fixed;opacity:0;pointer-events:none";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        return ok;
      } catch { return false; }
    }
  }

  function cancelar() {
    if (timer) { clearTimeout(timer); timer = null; }
    inicio = null;
  }

  map.on("mousedown", (e) => {
    if (e.originalEvent.button !== 0) return;      // só botão esquerdo
    if (drawer && drawer._enabled) return;              // está desenhando a área
    inicio = e.containerPoint;
    timer = setTimeout(async () => {
      const lat = e.latlng.lat.toFixed(6), lng = e.latlng.lng.toFixed(6);
      const txt = `${lat}, ${lng}`;
      const ok = await copiar(txt);
      toast(ok ? `📋 ${txt} copiado` : `Não consegui copiar: ${txt}`, ok ? "ok" : "err");
      // pisca no lugar clicado, para não restar dúvida de onde copiou
      const pulso = L.circleMarker(e.latlng, {
        pane: "paneMarcadores", radius: 6, weight: 3,
        color: "#7c3aed", fillColor: "#c4b5fd", fillOpacity: 0.9,
      }).addTo(map);
      setTimeout(() => map.removeLayer(pulso), 1200);
      cancelar();
    }, MS);
  });

  // andar o mouse ou soltar antes da hora cancela (é arrasto do mapa, não long-press)
  map.on("mousemove", (e) => {
    if (!inicio) return;
    if (e.containerPoint.distanceTo(inicio) > TOLERANCIA_PX) cancelar();
  });
  map.on("mouseup dragstart zoomstart movestart", cancelar);
})();
/* ── Escolher o município inteiro como área de trabalho ────────────────────────
   Desenhar a divisa de um município ponto a ponto é inviável: Canoas tem
   centenas de vértices e o polígono desenhado à mão sempre corta bairro. Aqui a
   divisa vem da malha oficial do IBGE (tabela `ibge_malha`) e é gravada no MESMO
   `areas/area_atual.json` que o desenho manual usa — o resto do sistema não sabe
   a diferença. */
(async function seletorMunicipio() {
  const selUF = $("mun-uf"), selMun = $("mun-cod");
  if (!selUF || !selMun) return;
  try {
    const ufs = await (await fetch("/api/ufs")).json();
    ufs.forEach((u) => selUF.insertAdjacentHTML("beforeend",
      `<option value="${esc(u.uf)}">${esc(u.uf)} (${u.n})</option>`));
  } catch { return; }

  selUF.onchange = async () => {
    selMun.innerHTML = `<option value="">município…</option>`;
    selMun.disabled = true;
    if (!selUF.value) return;
    try {
      const ms = await (await fetch(`/api/municipios?uf=${selUF.value}`)).json();
      ms.forEach((m) => selMun.insertAdjacentHTML("beforeend",
        `<option value="${esc(m.cod)}">${esc(m.nome)}</option>`));
      selMun.disabled = false;
    } catch { toast("não consegui listar os municípios", "err"); }
  };

  selMun.onchange = async () => {
    if (!selMun.value) return;
    try {
      const r = await fetch(`/api/area/municipio?cod=${selMun.value}`, { method: "POST" });
      // `r.ok` ANTES do corpo: um 500 vem sem `erro` no JSON, e olhar só o campo
      // deixava passar como sucesso — seguia para `setAreaLayer(undefined)` e a
      // tela dizia "nenhuma área", com a área ANTIGA intacta no banco.
      let d = {};
      try { d = await r.json(); } catch { /* corpo vazio */ }
      if (!r.ok || d.erro) {
        toast(d.erro || `O município NÃO foi aplicado (HTTP ${r.status}).`, "err");
        await carregarArea();
        return;
      }
      setAreaLayer(d.polygon);                 // mesma camada do desenho manual
      map.fitBounds(areaLayer.getBounds().pad(0.05));
      toast(`Área: ${d.municipio}/${d.uf} — ${d.vertices} vértices ✔`, "ok");
    } catch (e) { toast("falhou ao aplicar o município", "err"); }
  };
})();

/* ────────────────────────────────────────────────────────────
   DASHBOARD
   Uma cidade por vez na coluna da esquerda; à direita, o retrato dela.
   Os cards respondem perguntas, não despejam colunas: "quanto da base tem
   telefone", "quanto do CNPJ é confiável", "o que a equipe faz amanhã".
──────────────────────────────────────────────────────────── */
let dashCidade = "";
let dashDados = null;
let dashGrupo = "tudo";

const nfmt = (v) => (v || 0).toLocaleString("pt-BR");
const pctd = (v, t) => (t ? Math.round((100 * (v || 0)) / t) : 0);

async function carregarDashboard(cidade) {
  if (cidade !== undefined) dashCidade = cidade;
  $("dash-grid").innerHTML = `<div class="d-card"><div class="d-nota">carregando…</div></div>`;
  try {
    const r = await fetch("/api/dashboard?cidade=" + encodeURIComponent(dashCidade));
    dashDados = await r.json();
  } catch {
    $("dash-grid").innerHTML = `<div class="d-card g-vermelho"><div class="d-nota">
      Não consegui falar com o servidor.</div></div>`;
    return;
  }
  renderCidades();
  renderDashboard();
}

function renderCidades() {
  if (!dashDados) return;
  const busca = ($("dash-busca").value || "").toLowerCase();
  const lista = (dashDados.cidades || []).filter((c) =>
    !busca || c.cidade.toLowerCase().includes(busca));
  const total = (dashDados.cidades || []).reduce((s, c) => s + c.pois, 0);
  let html = `<div class="cid-item ${dashCidade ? "" : "active"}" data-cid="">
      <b>Todas as cidades</b><span>${nfmt(total)}</span></div>`;
  for (const c of lista) {
    const on = c.cidade.toLowerCase() === dashCidade.toLowerCase();
    html += `<div class="cid-item ${on ? "active" : ""}" data-cid="${esc(c.cidade)}">
      <b>${esc(c.cidade)}</b><span>${nfmt(c.pois)}</span></div>`;
  }
  $("dash-cidades").innerHTML = html;
  document.querySelectorAll("#dash-cidades .cid-item").forEach((el) => {
    el.onclick = () => carregarDashboard(el.dataset.cid);
  });
}

function cardCobertura(cob) {
  // A ordem é a da utilidade comercial, não a do banco: telefone e CNPJ vêm
  // primeiro porque são o que permite ligar e faturar.
  const linhas = [
    ["telefone", cob.telefone], ["CNPJ", cob.cnpj], ["endereço", cob.endereco],
    ["Street View", cob.streetview], ["facebook", cob.facebook],
    ["website", cob.website], ["e-mail", cob.email],
    ["avaliações", cob.avaliacoes], ["fotos do Maps", cob.fotos],
    ["instagram", cob.instagram],
  ];
  const t = cob.total || 1;
  return `<div class="d-card wide g-verde">
    <div class="d-tit">Cobertura de dados</div>
    <div class="d-num">${nfmt(cob.total)}<small>POIs</small></div>
    ${linhas.map(([rot, v]) => {
      const p = pctd(v, t);
      const cls = p >= 70 ? "alta" : p < 40 ? "baixa" : "";
      return `<div class="d-lin ${cls}"><span class="rot">${rot}</span>
        <span class="barra"><i style="width:${p}%"></i></span>
        <span class="val">${nfmt(v)} <em>${p}%</em></span></div>`;
    }).join("")}
    <div class="d-nota">${nfmt(cob.importados)} vieram de planilha externa —
      esses nunca passaram pelo Maps, por isso não têm foto nem avaliação.</div>
  </div>`;
}

// A tabela vinha ordenada por quantidade, alternando 2/4, 4/4, 4/4, sem rótulo,
// 2/4 — uma escada sem degrau. Agora desce por NOTA, cada linha traz o critério
// por extenso, e as notas iguais são AGRUPADAS: "4/4" aparece uma vez, com os
// dois caminhos que levam a ela embaixo. O que interessa ler primeiro é quanto
// dá para faturar sem discussão, e isso é a soma do 4/4, não uma linha dele.
function cardCnpj(conf, cob) {
  const cls = (nota) => nota === "4/4" ? "ok" : /^[23]\//.test(nota) ? "med" : "baixo";
  const porNota = new Map();
  for (const c of conf) {
    if (!porNota.has(c.nota)) porNota.set(c.nota, { nota: c.nota, n: 0, itens: [] });
    const g = porNota.get(c.nota);
    g.n += c.n;
    g.itens.push(c);
  }
  // TRÊS FAIXAS, não seis notas.
  //
  // A versão anterior mostrava 4/4, 3/4, 2/4, 1/4, 0/4 e "?" com o critério
  // técnico de cada uma — "dígito verificador · existe na Receita · UF confere".
  // É a informação certa para depurar o casamento e a errada para decidir o que
  // fazer: quem lê o painel quer saber em quais pode cobrar, quais valem
  // visitar e quais ignorar. O detalhe continua acessível ao expandir.
  const soma = (p) => [...porNota.values()]
    .filter((g) => p.test(g.nota)).reduce((a, g) => a + g.n, 0);
  const confirmado = soma(/^4\//);
  const provavel = soma(/^[23]\//);
  const fraco = soma(/^[01]\//) + soma(/^\?/);

  const faixa = (rot, n, explica, cls) => `<div class="conf-faixa ${cls}">
      <div class="conf-faixa-top"><b>${nfmt(n)}</b>
        <span>${pctd(n, cob.cnpj)}% dos CNPJs</span></div>
      <div class="conf-faixa-rot">${rot}</div>
      <small>${explica}</small></div>`;

  const detalhe = [...porNota.values()].map((g) => `<div class="conf-grupo">
      <div class="conf-cab">
        <span class="tag ${cls(g.nota)}">${esc(g.nota)}</span>
        <span class="conf-n">${nfmt(g.n)}</span>
      </div>
      ${g.itens.map((i) => `<div class="conf-lin">
        <span>${esc(i.criterio)}</span><b>${nfmt(i.n)}</b></div>`).join("")}
    </div>`).join("");

  return `<div class="d-card g-roxo">
    <div class="d-tit">CNPJ por confiança</div>
    <div class="d-num">${nfmt(cob.cnpj)}<small>de ${nfmt(cob.total)} POIs têm CNPJ</small></div>
    ${faixa("Confirmado — pode cobrar", confirmado,
            "dígito verificador confere, existe na Receita, e UF e município batem", "ok")}
    ${faixa("Provável — vale visitar", provavel,
            "existe na Receita, mas falta confirmar endereço ou município", "med")}
    ${faixa("Fraco — não sustenta decisão", fraco,
            "só a forma do número, ou origem não registrada", "baixo")}
    <details class="conf-det">
      <summary>Ver o critério de cada faixa</summary>
      ${detalhe}
    </details>
  </div>`;
}

/* O FOCO É O COMERCIAL, NÃO O TAMANHO DA BASE.
   Este card abria com "102.065 imóveis na base" — o número que menos importa
   aqui. A carteira inteira é majoritariamente residência, e o produto não
   promete cobrir residência: promete dizer quanto da base é comércio, quanto
   disso nós confirmamos e quanto nós acrescentamos. O total continua visível,
   em linha discreta, porque serve de denominador — não de manchete. */
function cardCadastro(cad, v) {
  if (!cad) {
    return `<div class="d-card g-laranja">
      <div class="d-tit">Cadastro do cliente</div>
      <div class="d-nota">Nenhuma base de cadastro importada para esta cidade.
        Importe na aba <b>Importar planilha</b> para ver o que visitar.</div></div>`;
  }
  const cor = { ja_cadastrado: "baixo", reclassificar_alta: "ok",
                reclassificar_media: "med", reclassificar_baixa: "med",
                novo_comercial: "ok", sem_poi: "" };
  const total = cad.total || 0;
  // Um denominador só — a base inteira — para as quatro linhas somarem entre si.
  // Misturar "% do comercial" com "% da base" na mesma coluna produz números que
  // parecem comparáveis e não são.
  const pct = (n) => (total ? 100 * n / total : 0);
  const fp = (n) => pct(n).toFixed(1).replace(".", ",") + "%";
  const porFlag = Object.fromEntries((cad.flags || []).map((f) => [f.flag, f.n]));
  const reclass = (porFlag.reclassificar_alta || 0) + (porFlag.reclassificar_media || 0)
                + (porFlag.reclassificar_baixa || 0);
  // `cad_comercial_total` é o recorte comercial declarado pelo próprio cliente;
  // sem ele (payload antigo) cai para o que o cruzamento conseguiu ver.
  const comHoje = (v && v.cad_comercial_total) || porFlag.ja_cadastrado || 0;
  const confirmado = (v && v.cad_comercial_casado) || porFlag.ja_cadastrado || 0;
  const possivel = comHoje + reclass;
  const ganhoPp = (pct(possivel) - pct(comHoje)).toFixed(1).replace(".", ",");
  const lin = (rot, n, cls, nota) => `<div class="d-lin d-lin-larga ${cls || ""}">
      <span class="rot">${rot}</span>
      <span class="barra"><i style="width:${pct(n).toFixed(1)}%"></i></span>
      <span class="val">${nfmt(n)} <em>${fp(n)}</em></span>
    </div>${nota ? `<div class="d-lin-nota">${nota}</div>` : ""}`;
  return `<div class="d-card wide g-laranja">
    <div class="d-tit">Cadastro do cliente — o que é comércio</div>
    <div class="d-num">${fp(possivel)}<small>da base é comércio depois do processamento</small></div>
    <div class="d-delta">o cliente hoje classifica <b>${fp(comHoje)}</b> como comercial
      · <b class="up">+${ganhoPp} pp</b> que o processo acrescenta</div>

    ${lin("Comercial segundo o cadastro", comHoje, "")}
    ${lin("Confirmado pela mineração", confirmado, "alta",
          `${total ? Math.round(100 * confirmado / (comHoje || 1)) : 0}% do comercial que o
           cliente já conhece — não é ganho, é complemento de informação`)}
    ${lin("Acrescentado pela mineração", reclass, "baixa",
          "está na base como não-comercial e há estabelecimento identificado no local —"
          + " é a reclassificação, o ganho de fato")}
    ${lin("Total possível pós-processamento", possivel, "alta")}

    <div class="d-nota">${nfmt(total)} registros na base
      · ${nfmt(porFlag.sem_poi || 0)} sem POI correspondente.
      O total serve de denominador; o que se mede aqui é o comércio.</div>
    <details class="conf-det">
      <summary>Ver o cruzamento registro a registro</summary>
      <table class="d-tab">${cad.flags.map((f) => `<tr>
        <td><span class="tag ${cor[f.flag] || ""}">${esc(f.flag)}</span></td>
        <td style="text-align:left;color:var(--ink-2);font-size:12px">
          ${esc(cad.descricoes[f.flag] || "")}</td>
        <td>${nfmt(f.n)}</td></tr>`).join("")}</table>
    </details>
  </div>`;
}

/* Valores que o usuário digita (assinatura de IA, câmbio, preço por faixa,
   deduções). Ficam no navegador: são premissas de negócio dele, mudam de mês
   para mês, e não são dado coletado para virar tabela no banco. */
const PARAM = {
  ler: (k, padrao) => {
    const v = parseFloat(localStorage.getItem("radar.param." + k));
    return Number.isFinite(v) ? v : padrao;
  },
  gravar: (k, v) => localStorage.setItem("radar.param." + k, String(v)),
};

const brl = (v) => "R$ " + (v || 0).toLocaleString("pt-BR",
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/* CUSTO em duas naturezas, que antes estavam somadas e não deviam estar:
   - VARIÁVEL: só existe porque este volume rodou (LLM por POI).
   - ASSINATURA: corre no mês inteiro, rodando ou parado. Ratear os US$ 45 do
     Webshare pelas horas usadas dava "US$ 1,68" e sugeria que rodar mais sairia
     mais caro — é o contrário: a mensalidade é a mesma, então quanto mais roda,
     menor o custo por POI.
   E o cenário Google, que é o custo NÃO pago — nunca somado ao resto. */
/* CONVERGÊNCIA — a pergunta que o usuário fez: onde as duas bases se encontram,
   onde cada uma viu o que a outra não viu, e o que cada lado acrescentou. O
   total de cada base isolado não responde nada; a interseção responde tudo. */
function cardConvergencia(v) {
  if (!v || !v.cad_total) {
    return `<div class="d-card g-laranja"><div class="d-tit">Meu mapeamento × cadastro</div>
      <div class="d-nota">Nenhuma base de cadastro importada para esta cidade.</div></div>`;
  }
  const pct = (a, b) => b ? Math.round(100 * a / b) : 0;
  const barra = (rot, n, tot, cls) => `<div class="d-lin ${cls}">
      <span class="rot">${rot}</span>
      <span class="barra"><i style="width:${pct(n, tot)}%"></i></span>
      <span class="val">${nfmt(n)} <em>${pct(n, tot)}%</em></span></div>`;
  // O DENOMINADOR é o cadastro COMERCIAL, não a base inteira.
  //
  // Comparar 22 mil POIs — que são comércio — com 102 mil imóveis, a maioria
  // residências, produz "22% de cobertura" que não mede cobertura de nada: o
  // mapeamento nunca teve por objetivo achar casa. Contra o recorte comercial
  // a conta passa a responder a pergunta certa: do comércio que o cliente já
  // conhece, quanto eu encontrei — e o que encontrei que ele não tem.
  const comTot = v.cad_comercial_total || 0;
  const semPoi = v.cad_comercial_sem_poi || 0;
  return `<div class="d-card wide g-laranja">
    <div class="d-tit">Meu mapeamento × cadastro COMERCIAL do cliente</div>
    <div class="conv-par">
      <div class="conv-lado">
        <div class="conv-rot">Meu mapeamento</div>
        <div class="conv-n">${nfmt(v.poi_total)}</div>
        <small>POIs válidos</small>
      </div>
      <div class="conv-meio">
        <div class="conv-n">${nfmt(v.poi_casado)}</div>
        <small>casaram com um imóvel</small>
      </div>
      <div class="conv-lado">
        <div class="conv-rot">Comercial no cadastro</div>
        <div class="conv-n">${nfmt(comTot)}</div>
        <small>de ${nfmt(v.cad_total)} imóveis na base</small>
      </div>
    </div>

    <div class="d-sub">O que isto vale, em ordem de retorno</div>
    <table class="d-tab d-tab-destaque">
      <tr class="ganho">
        <td><b>Reclassificar</b> — cadastrado como não-comercial, e há estabelecimento
            identificado no local</td>
        <td>${nfmt(v.cad_nao_comercial_casado)}</td></tr>
      <tr>
        <td><b>Acrescer</b> — achei e não existe no cadastro</td>
        <td>${nfmt(v.poi_so_meu)}</td></tr>
      <tr class="neutro">
        <td>Já é comercial no cadastro — não é ganho, é complemento de informação</td>
        <td>${nfmt(v.cad_comercial_casado)}</td></tr>
      <tr class="neutro">
        <td>Comercial no cadastro que eu ainda não encontrei</td>
        <td>${nfmt(semPoi)}</td></tr>
    </table>
    <div class="d-nota">A primeira linha é a de maior retorno: o cliente cobra como
      não-comercial e há comércio ali. A terceira <b>não é ganho</b> — o cliente já
      sabe; o que agregamos é dado, não a descoberta.</div>

    <div class="d-sub">Cobertura do comércio que o cliente já conhece</div>
    ${barra("encontrei", comTot - semPoi, comTot, "")}
    ${barra("ainda não encontrei", semPoi, comTot, "baixa")}

    <div class="d-sub">O que eu acrescentei ao imóvel casado</div>
    <table class="d-tab">
      <tr><td>telefone que o cadastro não tinha</td><td>${nfmt(v.eu_dei_telefone)}</td></tr>
      <tr><td>CNPJ</td><td>${nfmt(v.eu_dei_cnpj)}</td></tr>
      <tr><td>foto de fachada</td><td>${nfmt(v.eu_dei_fachada)}</td></tr>
    </table>
    <div class="d-nota">Os ${nfmt(v.cad_so_deles)} imóveis da base sem POI ficam de
      fora desta conta de propósito: são majoritariamente residências, e residência
      não gera POI comercial.</div>
  </div>`;
}

/* LEITURA DE FACHADA no agregado — o que a IA viu na cidade inteira. */
function cardFachada(f) {
  if (!f || !f.lidas) {
    return `<div class="d-card g-azul"><div class="d-tit">Leitura de fachada</div>
      <div class="d-nota">Nenhuma fachada lida nesta cidade. Rode a aba
        <b>Avaliar candidatos</b>.</div></div>`;
  }
  const lista = (arr, vazio) => (arr || []).length
    ? `<table class="d-tab">${arr.map((x) => `<tr><td>${esc(String(x.v).replace(/_/g, " "))}</td>
        <td>${nfmt(x.n)}</td></tr>`).join("")}</table>`
    : `<div class="d-nota">${vazio}</div>`;
  return `<div class="d-card g-azul">
    <div class="d-tit">Leitura de fachada</div>
    <div class="d-num">${nfmt(f.lidas)}<small>fachadas lidas</small></div>
    <table class="d-tab">
      <tr><td>aprovadas no gate</td><td>${nfmt(f.aprovadas)}</td></tr>
      <tr><td>imagem não é imóvel</td><td>${nfmt(f.fora_escopo)}</td></tr>
      <tr><td>imagem inapta</td><td>${nfmt(f.inaptas)}</td></tr>
    </table>
    <div class="d-sub">Estado de conservação</div>
    ${lista(f.conservacao, "nada lido ainda")}
    <div class="d-sub">Tipo de edificação</div>
    ${lista(f.tipos, "nada lido ainda")}
    ${f.pavimentos_medio ? `<div class="d-nota">Média de <b>${f.pavimentos_medio}</b>
      pavimentos por imóvel lido.</div>` : ""}
  </div>`;
}

/* MEDIÇÃO — a tampa, a bateria e o acesso. É a parte que serve a QUALQUER
   concessionária, e a que vira ordem de serviço em vez de fila de receita. */
function cardMedicao(f) {
  // A condição certa é ter DADO DE MEDIÇÃO, não ter fachada lida. Com o guarda
  // antigo (`!f.lidas`) o cartão aparecia sempre que houvesse qualquer leitura,
  // exibindo uma coluna de zeros — e zero aqui não é informação: significa que
  // o modelo não olhou o hidrômetro, não que não existe bateria coletiva.
  // Cartão de zeros ocupa a tela e ensina a ignorar o painel.
  if (!f || !f.lidas) return "";
  // Só MEDIÇÃO conta para decidir se o cartão existe. `numero_diverge` e
  // `unidades_acima` são divergência cadastral — moram no cartão de fachada, e
  // usá-las aqui fazia o cartão aparecer inteiro de zeros só porque alguma
  // fachada tinha número diferente do cadastro.
  const temMedicao = (f.medicao_coletiva || 0) + (f.tampa_problema || 0)
    + (f.acesso_obstruido || 0) + (f.abrigos || []).length;
  if (!temMedicao) return "";
  const alto = (n) => n > 0 ? "med" : "";
  return `<div class="d-card">
    <div class="d-tit">Medição e acesso</div>
    <div class="d-num">${nfmt(f.medicao_coletiva)}<small>com bateria coletiva</small></div>
    <div class="d-nota">Medição agrupada só existe onde há várias unidades — é o
      sinal mais forte de economia oculta que a fachada dá.</div>
    <div class="d-sub">Ocorrências de rota</div>
    <table class="d-tab">
      <tr><td><span class="tag ${alto(f.tampa_problema)}">tampa</span>
        ausente, quebrada ou soterrada</td><td>${nfmt(f.tampa_problema)}</td></tr>
      <tr><td><span class="tag ${alto(f.acesso_obstruido)}">acesso</span>
        obstruído ou interno</td><td>${nfmt(f.acesso_obstruido)}</td></tr>
    </table>
    <div class="d-sub">Abrigo do medidor</div>
    ${(f.abrigos || []).length
      ? `<table class="d-tab">${f.abrigos.map((x) => `<tr>
          <td>${esc(String(x.v).replace(/_/g, " "))}</td><td>${nfmt(x.n)}</td></tr>`).join("")}</table>`
      : `<div class="d-nota">nada lido ainda</div>`}
    <div class="d-sub">Divergências</div>
    <table class="d-tab">
      <tr><td>número da fachada diverge do cadastro</td><td>${nfmt(f.numero_diverge)}</td></tr>
      <tr><td>unidades físicas acima das economias</td><td>${nfmt(f.unidades_acima)}</td></tr>
    </table>
  </div>`;
}

function cardCusto(c) {
  const ia = PARAM.ler("ia_usd", c.assinaturas.find((a) => a.editavel)?.usd || 0);
  const cambio = PARAM.ler("usd_brl", c.usd_brl);
  const assin = c.assinaturas.reduce((s, a) => s + (a.editavel ? ia : a.usd), 0);
  const varUsd = c.variavel_usd;
  const mesUsd = assin + varUsd;
  const porPoi = (mesUsd * cambio) / Math.max(1, c.pois);
  const economia = c.google_total_usd - varUsd;
  return `<div class="d-card wide g-teal">
    <div class="d-tit">Quanto custou</div>
    <div class="d-num">${brl(mesUsd * cambio)}<small>no mês · ≈ US$ ${mesUsd.toFixed(2)}
      · ${brl(porPoi)} por POI</small></div>

    <div class="d-sub">Gasto variável <em>— só existe porque rodou</em></div>
    <table class="d-tab">
      <tr><td>LLM (extração na fase Web)</td><td>US$ ${c.llm_usd.toFixed(4)}</td></tr>
      <tr><td>Places API (motor pago)</td>
        <td>${c.places_chamadas ? "US$ " + c.places_usd.toFixed(2) : "não usado"}</td></tr>
    </table>

    <div class="d-sub">Assinaturas <em>— correm no mês inteiro, rodando ou não</em></div>
    <table class="d-tab">
      ${c.assinaturas.map((a) => `<tr><td>${esc(a.rotulo)}</td><td>${a.editavel
        ? `US$ <input class="in-num" id="par-ia" type="number" step="1" min="0"
             value="${ia}">`
        : "US$ " + a.usd.toFixed(2)}</td></tr>`).join("")}
      <tr><td>Câmbio (US$ → R$)</td><td>R$ <input class="in-num" id="par-cambio"
        type="number" step="0.01" min="0" value="${cambio}"></td></tr>
    </table>

    <div class="d-sub">Se fosse tudo pela API do Google</div>
    <table class="d-tab">
      <tr><td>Nearby Search (descoberta)</td><td>US$ ${c.google.nearby.toFixed(2)}</td></tr>
      <tr><td>Place Details (telefone, endereço)</td><td>US$ ${c.google.details.toFixed(2)}</td></tr>
      <tr><td>Place Photos</td><td>US$ ${c.google.foto.toFixed(2)}</td></tr>
      <tr><td>Street View Static</td><td>US$ ${c.google.streetview.toFixed(2)}</td></tr>
      <tr class="tr-forte"><td>total pela API</td>
        <td>US$ ${c.google_total_usd.toFixed(2)} · ${brl(c.google_total_usd * cambio)}</td></tr>
    </table>
    <div class="d-destaque verde">
      <b>${brl(economia * cambio)}</b> foi o que a captura+OCR deixou de gastar
      <span>preço de tabela do Google, não somado acima</span>
    </div>

    <div class="d-sub">Horas de máquina</div>
    <table class="d-tab">
      ${Object.entries(c.horas).map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td>${v} h</td></tr>`).join("")}
      <tr class="tr-forte"><td>total</td><td>${c.horas_total} h</td></tr>
    </table>
    <div class="d-nota">Captura+OCR, Receita local e Street View não têm preço por
      chamada — rodam aqui. O que elas custam é tempo, e tempo está acima.</div>
  </div>`;
}

/* RETORNO DIRETO: quanto a base vale, pelo preço que VOCÊ atribui a cada faixa
   de qualidade. As faixas são exclusivas — um POI cai numa só —, então a soma
   fecha com o total da cidade e não há dado contado duas vezes. */
function cardRetorno(faixas, c) {
  const cambio = PARAM.ler("usd_brl", c.usd_brl);
  const ia = PARAM.ler("ia_usd", c.assinaturas.find((a) => a.editavel)?.usd || 0);
  const custoMes = (c.assinaturas.reduce((s, a) => s + (a.editavel ? ia : a.usd), 0)
                    + c.variavel_usd) * cambio;
  const bruto = faixas.reduce((s, f) => s + f.n * PARAM.ler("val_" + f.chave, 0), 0);
  const ded = PARAM.ler("deducoes", 0);
  const liq = bruto - ded - custoMes;
  return `<div class="d-card wide g-azul">
    <div class="d-tit">Cálculo de retorno direto</div>
    <div class="d-num"><span id="ret-bruto">${brl(bruto)}</span><small>valor bruto da base</small></div>
    <table class="d-tab tab-ret">
      <thead><tr><th>Faixa de qualidade</th><th>POIs</th><th>R$ por POI</th>
        <th>Subtotal</th></tr></thead>
      <tbody>${faixas.map((f) => `<tr>
        <td>${esc(f.rotulo)}</td>
        <td class="c-n">${nfmt(f.n)}</td>
        <td><input class="in-num" data-faixa="${f.chave}" type="number" step="0.01"
             min="0" value="${PARAM.ler("val_" + f.chave, 0)}"></td>
        <td class="c-sub" data-sub="${f.chave}">${brl(f.n * PARAM.ler("val_" + f.chave, 0))}</td>
      </tr>`).join("")}</tbody>
    </table>
    <table class="d-tab">
      <tr><td>Deduções (impostos, comissão, retrabalho)</td>
        <td>R$ <input class="in-num" id="par-deducoes" type="number" step="0.01"
             min="0" value="${ded}"></td></tr>
      <tr><td>Custo do processo (do card ao lado)</td>
        <td id="ret-custo">${brl(custoMes)}</td></tr>
    </table>
    <div class="d-destaque ${liq >= 0 ? "verde" : "vermelho"}" id="ret-liq-box">
      <b id="ret-liq">${brl(liq)}</b> retorno líquido
      <span id="ret-liq-poi">${brl(liq / Math.max(1, c.pois))} por POI</span>
    </div>
    <div class="d-nota">As faixas são exclusivas: cada POI entra em uma só, então
      a soma fecha com o total da cidade. Os valores digitados ficam salvos neste
      navegador e valem para todas as cidades.</div>
  </div>`;
}

function cardImagens(sv, cob) {
  // IMAGEM é o dado mais caro de produzir: cada fachada custou uma sessão de
  // navegador; cada foto, a abertura de uma ficha no Maps. E é a evidência que
  // sustenta o dossiê — sem ela, a divergência de uso é afirmação sem prova.
  // Por isso o cartão é largo e mostra COBERTURA, não só contagem.
  const tam = (b) => {
    const m = (b || 0) / 1048576;
    return m > 1024 ? (m / 1024).toFixed(1) + " GB" : m.toFixed(0) + " MB";
  };
  const tot = cob.total || 0;
  const pct = (n) => tot ? Math.round(100 * n / tot) : 0;
  const barra = (rot, n, cls = "") => `<div class="d-lin ${cls}">
      <span class="rot">${rot}</span>
      <span class="barra"><i style="width:${pct(n)}%"></i></span>
      <span class="val">${nfmt(n)} <em>${pct(n)}%</em></span></div>`;

  const semPano = cob.sem_panorama || 0;
  const semNada = Math.max(0, tot - (sv.pois || 0) - (sv.fotos_pois || 0));
  return `<div class="d-card wide g-azul">
    <div class="d-tit">Evidência visual</div>
    <div class="conv-par">
      <div class="conv-lado">
        <div class="conv-rot">Fachadas</div>
        <div class="conv-n">${nfmt(sv.imagens)}</div>
        <small>${nfmt(sv.pois)} POIs · ${tam(sv.bytes)}</small>
      </div>
      <div class="conv-lado">
        <div class="conv-rot">Fotos do Maps</div>
        <div class="conv-n">${nfmt(sv.fotos)}</div>
        <small>${nfmt(sv.fotos_pois)} POIs · ${tam(sv.fotos_bytes)}</small>
      </div>
      <div class="conv-lado">
        <div class="conv-rot">Total</div>
        <div class="conv-n">${nfmt((sv.imagens || 0) + (sv.fotos || 0))}</div>
        <small>${tam((sv.bytes || 0) + (sv.fotos_bytes || 0))}</small>
      </div>
    </div>

    <div class="d-sub">Cobertura sobre ${nfmt(tot)} POIs</div>
    ${barra("com fachada do Street View", sv.pois || 0)}
    ${barra("com foto do Maps", sv.fotos_pois || 0)}
    ${semPano ? barra("sem panorama disponível", semPano, "baixa") : ""}
    ${semNada ? barra("sem imagem nenhuma", semNada, "baixa") : ""}

    ${sv.fotos && sv.fotos_baixadas < sv.fotos ? `<div class="d-nota">
      <b>${nfmt(sv.fotos - sv.fotos_baixadas)}</b> fotos são só URL — o endereço foi
      registrado mas o arquivo nunca foi baixado. Elas somem se o Google trocar o
      link, e não servem para o dossiê, que precisa da imagem embutida.</div>` : ""}
    <div class="d-nota">Os bytes moram no Storage; o banco guarda o caminho. É o que
      mantém backup e replicação leves — imagem não precisa de transação.</div>
  </div>`;
}

function cardOrigem(origem, status) {
  return `<div class="d-card">
    <div class="d-tit">Origem e status</div>
    <table class="d-tab">
      ${/* A CONTAGEM continua visível; o NOME da fonte é que embaça. O cliente
             precisa saber quantos achados existem — não de onde vieram. */""}
      ${origem.slice(0, 6).map((o) => `<tr><td class="fonte-dado">${esc(o.fonte)} / ${esc(o.dado)}</td>
        <td>${nfmt(o.n)}</td></tr>`).join("")}
    </table>
    <div class="d-tit" style="margin-top:13px">Status</div>
    <table class="d-tab">
      ${status.slice(0, 6).map((s) => `<tr><td>${esc(s.status)}</td>
        <td>${nfmt(s.n)}</td></tr>`).join("")}
    </table>
  </div>`;
}

function renderDashboard() {
  const d = dashDados;
  if (!d || d.erro) { $("dash-grid").innerHTML = ""; return; }
  $("dash-titulo").textContent = d.cidade || "Todas as cidades";
  const c = d.cobertura;
  $("dash-sub").textContent =
    `${nfmt(c.total)} POIs · ${nfmt(c.cnpj)} com CNPJ · ${nfmt(c.telefone)} com telefone`
    + (d.cadastro ? ` · ${nfmt(d.cadastro.total)} imóveis no cadastro do cliente` : "");
  const g = dashGrupo;
  let html = "";
  if (g === "tudo" || g === "cobertura") html += cardCobertura(c);
  if (g === "tudo" || g === "cnpj") html += cardCnpj(d.cnpj_confianca, c);
  if (g === "tudo" || g === "cadastro") html += cardCadastro(d.cadastro, d.convergencia);
  if (g === "tudo" || g === "cadastro") html += cardConvergencia(d.convergencia);
  if (g === "tudo" || g === "fachada") html += cardFachada(d.fachada) + cardMedicao(d.fachada);
  if (g === "tudo" || g === "retorno") html += cardRetorno(d.faixas, d.custo);
  if (g === "tudo" || g === "custo" || g === "retorno") html += cardCusto(d.custo);
  if (g === "tudo") html += cardImagens(d.streetview, c) + cardOrigem(d.origem, d.status);
  $("dash-grid").innerHTML = html;
  ligarParametros();
}

/* Os inputs recalculam NA HORA e sem redesenhar o card: um `renderDashboard()`
   a cada tecla recria o <input>, o cursor volta para o começo e digitar "12,50"
   vira "0,5211". Só os números na tela são reescritos. */
function ligarParametros() {
  const d = dashDados;
  if (!d) return;
  const recalc = () => {
    const cambio = PARAM.ler("usd_brl", d.custo.usd_brl);
    const ia = PARAM.ler("ia_usd", d.custo.assinaturas.find((a) => a.editavel)?.usd || 0);
    const custoMes = (d.custo.assinaturas.reduce((s, a) => s + (a.editavel ? ia : a.usd), 0)
                      + d.custo.variavel_usd) * cambio;
    let bruto = 0;
    for (const f of d.faixas || []) {
      const v = PARAM.ler("val_" + f.chave, 0);
      bruto += f.n * v;
      const cel = document.querySelector(`[data-sub="${f.chave}"]`);
      if (cel) cel.textContent = brl(f.n * v);
    }
    const liq = bruto - PARAM.ler("deducoes", 0) - custoMes;
    const set = (id, txt) => { const e = $(id); if (e) e.textContent = txt; };
    set("ret-bruto", brl(bruto));
    set("ret-custo", brl(custoMes));
    set("ret-liq", brl(liq));
    set("ret-liq-poi", brl(liq / Math.max(1, d.custo.pois)) + " por POI");
    const box = $("ret-liq-box");
    if (box) {
      box.classList.toggle("vermelho", liq < 0);
      box.classList.toggle("verde", liq >= 0);
    }
  };
  document.querySelectorAll("#dash-grid .in-num").forEach((el) => {
    el.oninput = () => {
      const v = parseFloat(el.value) || 0;
      if (el.dataset.faixa) PARAM.gravar("val_" + el.dataset.faixa, v);
      else if (el.id === "par-ia") PARAM.gravar("ia_usd", v);
      else if (el.id === "par-cambio") PARAM.gravar("usd_brl", v);
      else if (el.id === "par-deducoes") PARAM.gravar("deducoes", v);
      recalc();
      // o card de custo depende de câmbio e assinatura: redesenha só ele
      if (el.id === "par-ia" || el.id === "par-cambio") agendarRedesenhoCusto();
    };
  });
}

/* Câmbio e assinatura mudam o card de custo inteiro, mas redesenhá-lo a cada
   tecla tiraria o foco do campo. Espera a digitação parar. */
let timerCusto = null;
function agendarRedesenhoCusto() {
  clearTimeout(timerCusto);
  timerCusto = setTimeout(() => renderDashboard(), 900);
}

document.querySelectorAll("#dash-filtros .chip-f").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("#dash-filtros .chip-f").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    dashGrupo = b.dataset.grupo;
    renderDashboard();
  };
});
$("dash-busca").oninput = () => renderCidades();

/* ────────────────────────────────────────────────────────────
   IMPORTAÇÃO DO CADASTRO DO CLIENTE
   Duas etapas de propósito: lê e MOSTRA antes de gravar. São 100 mil linhas por
   arquivo — um cabeçalho fora do padrão gravaria a base inteira com as colunas
   trocadas, e só se descobriria no cruzamento.
──────────────────────────────────────────────────────────── */
let cadastroArquivo = null;

$("btn-cadastro").onclick = () => $("file-cadastro").click();
$("file-cadastro").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $("cadastro-status").textContent = `lendo ${f.name}…`;
  const fd = new FormData();
  fd.append("file", f);
  let d;
  try {
    d = await (await fetch("/api/cadastro/previa", { method: "POST", body: fd })).json();
  } catch {
    $("cadastro-status").textContent = "falha ao enviar o arquivo.";
    return;
  }
  if (d.erro) {
    $("cadastro-status").textContent = "⚠️ " + d.erro;
    toast(d.erro, "err");
    return;
  }
  cadastroArquivo = d.arquivo;
  $("cadastro-status").textContent = `${nfmt(d.linhas)} linhas lidas — confira antes de gravar.`;
  abrirModalCadastro(d);
  e.target.value = "";
};

function abrirModalCadastro(d) {
  const cols = ["num_ligacao", "cidade", "categoria", "endereco",
                "situacao_ligacao", "e_comercial", "lat", "lng"];
  $("mc-titulo").textContent = `Conferir importação — ${d.arquivo}`;
  $("mc-acoes").classList.remove("hidden");
  $("mc-corpo").innerHTML =
    (d.avisos || []).map((a) => `<div class="mc-aviso">⚠️ ${esc(a)}</div>`).join("")
    + `<div class="mc-resumo">
        <div><b>${nfmt(d.linhas)}</b>linhas válidas</div>
        <div><b>${nfmt(d.comerciais)}</b>comerciais</div>
        <div><b>${nfmt(d.com_coordenada)}</b>com coordenada</div>
        <div><b>${esc(d.referencia || "—")}</b>competência</div>
        <div><b>${d.cidades.map((c) => esc(c[0])).slice(0, 3).join(", ")}</b>cidades</div>
      </div>
      <div class="mc-scroll"><table class="mc-tab">
        <thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
        <tbody>${d.amostra.map((r) => `<tr>${cols.map((c) =>
          `<td>${esc(String(r[c] === null || r[c] === undefined ? "" : r[c]))}</td>`
        ).join("")}</tr>`).join("")}</tbody>
      </table></div>
      <div class="hint" style="margin-top:9px">Amostra das ${d.amostra.length}
        primeiras linhas, já com os nomes de coluna padronizados do banco.
        A chave é <code>num_ligacao</code>: reimportar o mesmo arquivo atualiza,
        não duplica.</div>`;
  $("modal-cadastro").classList.remove("hidden");
}

function fecharModalCadastro() { $("modal-cadastro").classList.add("hidden"); }
$("mc-fechar").onclick = fecharModalCadastro;
$("mc-cancelar").onclick = fecharModalCadastro;

$("mc-confirmar").onclick = async () => {
  if (!cadastroArquivo) return;
  const btn = $("mc-confirmar");
  btn.disabled = true;
  btn.textContent = "importando…";
  try {
    const r = await (await fetch("/api/cadastro/confirmar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ arquivo: cadastroArquivo, cruzar: $("mc-cruzar").checked }),
    })).json();
    if (r.erro) { toast(r.erro, "err"); return; }
    const i = r.importacao;
    let msg = `${nfmt(i.novas)} novas · ${nfmt(i.atualizadas)} atualizadas`;
    if (r.cruzamento) {
      const cz = r.cruzamento;
      const rec = cz.reclassificar_alta + cz.reclassificar_media + cz.reclassificar_baixa;
      msg += ` · cruzado: ${nfmt(cz.ja_cadastrado)} já cadastrados, `
           + `${nfmt(rec)} a reclassificar, ${nfmt(cz.novo_comercial)} novos`;
    }
    $("cadastro-status").textContent = msg;
    toast("Cadastro importado.", "ok");
    fecharModalCadastro();
    if (modo === "dashboard") carregarDashboard();
  } finally {
    btn.disabled = false;
    btn.textContent = "Confirmar importação";
  }
};

/* Modelos de planilha aceitos — todos num lugar só, de qualquer aba. */
$("btn-modelos").onclick = async () => {
  const d = await (await fetch("/api/modelos")).json();
  $("mc-titulo").textContent = "Modelos de planilha aceitos";
  $("mc-acoes").classList.add("hidden");   // aqui não há nada a confirmar
  $("mc-corpo").innerHTML = d.modelos.map((m) => `
    <div style="padding:11px 0;border-bottom:1px solid var(--line)">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
        <div><b>${esc(m.nome)}</b>
          <div class="hint">${esc(m.descricao)}</div></div>
        <a class="btn ghost" href="${m.url}" download>⬇️ .${m.formato}</a>
      </div>
      <div class="hint" style="margin-top:6px"><code>${
        m.colunas.slice(0, 14).map(esc).join(", ")
      }${m.colunas.length > 14 ? `, … (+${m.colunas.length - 14})` : ""}</code></div>
    </div>`).join("");
  $("modal-cadastro").classList.remove("hidden");
};
