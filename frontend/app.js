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
   malha (baixo) < quadras < faces/vias < marcadores (topo, sempre clicáveis). */
map.createPane("paneMalha").style.zIndex = 410;
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
  { re: /restaurante|lanchonete|pizzari|hamburg|churrasc|comida|alimenta|café|cafeteria|padaria|sorveter|açai|acai|bar\b|petiscaria|self service|marmita/i, cor: "#e8710a", emo: "🍽️" },
  { re: /supermercado|mercado|mercearia|mercadinho|atacad|hortifruti|conveni|frios|distribuidora de bebidas|bebidas/i, cor: "#1e8e3e", emo: "🛒" },
  { re: /farm[aá]cia|drogaria|hospital|cl[ií]nica|laborat[oó]rio|dentista|odonto|m[eé]dic|sa[uú]de|fisioter|psicol|veterin|pet/i, cor: "#d93025", emo: "💊" },
  { re: /escola|col[eé]gio|creche|faculdade|universi|curso|educa/i, cor: "#5b3cc4", emo: "🎓" },
  { re: /hotel|pousada|hostel|motel|hospedagem/i, cor: "#12805c", emo: "🛏️" },
  { re: /banco|caixa eletr|lot[eé]rica|financ|cr[eé]dito|seguros/i, cor: "#186a3b", emo: "🏦" },
  { re: /oficina|mec[aâ]nica|auto ?pe[cç]as|autope[cç]as|borracharia|lava.?jato|concession|moto|el[eé]trica automotiva|posto de (comb|gas)/i, cor: "#455a75", emo: "🔧" },
  { re: /sal[aã]o|barbearia|beleza|est[eé]tica|manicure|cabele/i, cor: "#d6408b", emo: "✂️" },
  { re: /academia|gym|crossfit|esporte|fitness/i, cor: "#00838f", emo: "💪" },
  { re: /igreja|templo|par[oó]quia|assembleia/i, cor: "#8d6e63", emo: "⛪" },
  { re: /constru|madeirei|ferragem|material|tinta|vidra[cç]|serralheria|marmoraria/i, cor: "#a05c10", emo: "🧱" },
  { re: /loja|boutique|magazine|variedade|utilidade|presente|papelaria|livraria|cal[cç]ado|roupa|confec|m[oó]veis|eletro|celular|inform[aá]tica|[oó]tica|joalheria|relojoaria|shopping/i, cor: "#8430ce", emo: "🛍️" },
];
function catInfo(cat, fonte) {
  const c = (cat || "").toString();
  for (const k of CATS) if (k.re.test(c)) return k;
  if (fonte === "descoberto") return { cor: "#6d28d9", emo: "✨" };
  return { cor: "#1a73e8", emo: "📍" };
}

function makeIcon(poi, novo) {
  const k = catInfo(poi.categoria, poi.fonte);
  const ver = poi.veredito === "aprovado" ? "v-ok"
            : poi.veredito === "reprovado" ? "v-no" : "";
  const rec = poi.recomendar_visita ? " rec" : "";
  return L.divIcon({
    className: "pin-wrap",
    html: `<div class="pin ${novo ? "novo" : ""} ${ver}${rec}" style="--c:${k.cor}">
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
  { key: "ia",      label: "OpenAI",       cor: "#10a37f", teste: (p) => p.status === "recuperado_ia" },
  { key: "gemini",  label: "Gemini",       cor: "#a142f4", teste: (p) => p.status === "recuperado_gemini" },
  { key: "web",     label: "Web",          cor: "#d81b60", teste: (p) => p.status === "recuperado_web" },
  { key: "desc",    label: "Descobertos",  cor: "#e8710a", teste: (p) => p.status === "descoberto" || p.status === "minerado" },
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
];
const atributosAtivos = new Set(); // vazio = não filtra por atributo

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
    html += `<button class="fchip ia ${flagsIA.has("recomendar") ? "on" : ""}" data-f="recomendar" style="--c:#b8860b">⭐ Recomendar visita <span class="n">${cRec.toLocaleString("pt-BR")}</span></button>`;
    if (cRev) html += `<button class="fchip ia ${flagsIA.has("revisar") ? "on" : ""}" data-f="revisar" style="--c:#e8710a">🔍 Revisar manual <span class="n">${cRev}</span></button>`;
  }
  html += '</div><div class="frow frow-attr">';
  for (const a of ATRIBUTOS) {
    const on = atributosAtivos.has(a.key);
    html += `<button class="fchip attr ${on ? "on" : ""}" data-a="${a.key}" style="--c:#1f7a4d">
               ${a.label} <span class="n">${(attrCounts[a.key] || 0).toLocaleString("pt-BR")}</span></button>`;
  }
  html += "</div>";
  box.innerHTML = html;
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

async function abrirPoi(poiLeve) {
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
  const sv = poi.streetview_path && poi.streetview_path !== "NA" && poi.id != null
    ? `/api/sv/${poi.id}/facade` : null;
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

  html += `<div class="m-rows">`;
  if (poi.endereco) {
    const fonte = FONTE_END[poi.endereco_fonte] || FONTE_END[poi.fonte_dado] || FONTE_END[poi.status] || null;
    html += `<div class="m-row"><span class="ico">📍</span><span>${esc(poi.endereco)}` +
      (fonte ? ` <span class="src-tag" style="--c:${fonte.cor}">${fonte.label}</span>` : "") + `</span></div>`;
  }
  if (poi.telefone) html += `<div class="m-row"><span class="ico">📞</span><a href="tel:${esc(poi.telefone)}">${esc(poi.telefone)}</a></div>`;
  if (poi.website) html += `<div class="m-row"><span class="ico">🌐</span><a href="${esc(poi.website)}" target="_blank" rel="noopener">${esc(poi.website.replace(/^https?:\/\//, "").slice(0, 48))}</a></div>`;
  if (poi.status_horario) html += `<div class="m-row"><span class="ico">🕒</span><span>${esc(poi.status_horario)}</span></div>`;
  if (poi.instagram) html += `<div class="m-row"><span class="ico">📷</span><a href="${esc(poi.instagram)}" target="_blank" rel="noopener">@${esc(poi.instagram.replace(/\/$/, "").split("/").pop())}</a></div>`;
  if (poi.email) html += `<div class="m-row"><span class="ico">✉️</span><a href="mailto:${esc(poi.email.split(",")[0].trim())}">${esc(poi.email.slice(0, 48))}</a></div>`;
  if (poi.preco_medio) html += `<div class="m-row"><span class="ico">💰</span><span>Preço médio: ${esc(poi.preco_medio)}</span></div>`;
  html += `</div>`;

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
        `<figure><img src="/api/sv/${poi.id}/${esc(g)}" loading="lazy" onerror="this.closest('figure').remove()"><figcaption>${ROT[g] || g}</figcaption></figure>`).join("") + `</div>`;
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
        ` <span class="src-tag" style="--c:#8a6d00">planilha</span>` : "") +
      (diverge ? `<br><span class="src-tag" style="--c:#e8710a">⚠️ número da via difere do obtido — confira</span>` : "") +
      (poi.distancia_m != null ? `<br><small>Distância planilha ↔ ponto: ${Math.round(poi.distancia_m)} m</small>` : "") + `</div>`;
  }
  html += `<div class="m-actions">`;
  if (poi.maps_url) html += `<a class="btn primary" href="${esc(poi.maps_url)}" target="_blank" rel="noopener">Abrir no Google Maps</a>`;
  const la = poi.maps_lat ?? poi.lat, lo = poi.maps_lng ?? poi.lng;
  if (la != null) html += `<button class="btn ghost" onclick="map.setView([${la},${lo}],18);fecharModal()">🎯 Centralizar</button>`;
  html += `</div>`;

  $("modal-card").innerHTML = html;
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
const AREA_STYLE = { color: "#1a73e8", weight: 2.5, dashArray: "6 6", fillColor: "#1a73e8", fillOpacity: 0.06, className: "area-poly" };
// acima disto a ligação até a coordenada original vira alerta no mapa: o teto
// da régua é 10 m, então 25 m é o dobro e meio do que se considera aceitável
const DESLOC_DESTAQUE_M = 25;

function setAreaLayer(latlngs) {
  if (areaLayer) { map.removeLayer(areaLayer); areaLayer = null; }
  if (latlngs && latlngs.length >= 3) {
    areaLayer = L.polygon(latlngs, AREA_STYLE).addTo(map);
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

async function salvarArea(latlngs) {
  await fetch("/api/area", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ polygon: latlngs || [] }),
  });
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
  await salvarArea(latlngs);
  toast("Área salva ✔", "ok");
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

document.querySelectorAll(".mode").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".mode").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    modo = b.dataset.mode;
    $("sec-planilha").classList.toggle("hidden", modo !== "planilha");
    $("sec-mineracao").classList.toggle("hidden", modo !== "mineracao");
    $("sec-enriquecimento").classList.toggle("hidden", modo !== "enriquecimento");
    $("sec-quadras").classList.toggle("hidden", modo !== "quadras");
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
      $(id)?.classList.toggle("hidden", dash);
    }
    if (modo === "quadras") carregarUltimaQuadra();
    else { limparQuadras(); }
    if (dash) carregarDashboard();
    atualizarBotoes();
  };
});

// os dois motores de mineração não compartilham opção nenhuma: a captura tem
// zoom, a Places tem passo de grade e coleta profunda paga. Mostrar as duas
// listas ao mesmo tempo sugeriria que a escolha de uma vale para a outra.
function trocarMotorMineracao() {
  const places = ($("op-motor")?.value || "captura") === "places";
  $("op-step-wrap")?.classList.toggle("hidden", !places);
  $("op-details-wrap")?.classList.toggle("hidden", !places);
  $("op-zoom-wrap")?.classList.toggle("hidden", places);
  const h = $("hint-captura");
  if (h) {
    h.innerHTML = places
      ? "Consulta a <b>Places API paga</b> célula a célula. Precisa de "
        + "<code>MAPS_API_KEY</code> no <code>.env</code>; sem ela o Google recusa "
        + "cada chamada e o job termina com 0 POIs."
      : "Fotografa o Maps em tiles 4K com o estilo limpo (só os markers), detecta "
        + "os ícones, lê os nomes por <b>OCR</b> e busca cada um. "
        + "<b>Não custa por chamada</b> — só tempo de captura.";
  }
}
if ($("op-motor")) $("op-motor").onchange = trocarMotorMineracao;
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
                    enriquecimento: "de enriquecimento", quadras: "de quadras" };

function atualizarBotoes() {
  const temArea = !!areaLayer;
  // o modo Quadras tem o seu próprio botão e NÃO passa pelo job de POIs
  const usaJob = modo in MODOS_JOB;
  const pronto = usaJob && temArea && !jobRodando &&
                 (modo !== "planilha" || !!arquivoImportado);
  $("btn-iniciar").disabled = !pronto;
  $("btn-iniciar").classList.toggle("hidden", jobRodando || !usaJob);
  $("btn-parar").classList.toggle("hidden", !jobRodando);
  if (!temArea) $("job-status-txt").textContent = "Defina a área (passo 1) para liberar o início.";
  else if (modo === "planilha" && !arquivoImportado && !jobRodando) $("job-status-txt").textContent = "Importe a planilha (passo 2).";
  else if (!jobRodando) $("job-status-txt").textContent = "Pronto para iniciar.";
}

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
  } else if (modo === "mineracao") {
    const motor = $("op-motor")?.value || "captura";
    opcoes = { motor, sessao: $("op-sessao").value.trim() || "mineracao" };
    if (motor === "places") {
      opcoes.step = parseFloat($("op-step").value) || 150;
      opcoes.details = $("op-details").checked;
    } else {
      opcoes.zoom = parseInt($("op-zoom").value) || 19;
    }
  } else if (modo === "quadras") {
    opcoes = { com_maps: !!$("op-q-maps")?.checked,
               sem_proxy: !$("op-q-proxy")?.checked };
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
      visivel: !!$("enr-visivel")?.checked,
    };
  }
  const body = { modo: modoJob, opcoes };
  if (arquivoImportado) body.arquivo = arquivoImportado;

  const r = await (await fetch("/api/jobs", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })).json();
  if (r.erro) return toast(r.erro, "err");
  aplicarJob(r);
  $("log-panel").classList.remove("collapsed");
  toast(`Processo ${MODOS_JOB[modo]} iniciado ▶`, "ok");
  if (modo === "quadras") acompanharQuadras();
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
                     enriquecer_tudo: "enriquecimento (cascata)", baixar_imagens: "download de imagens" };
function aplicarJob(j) {
  jobRodando = j && j.status === "rodando";
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
  }
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
const ROTULO_PADRAO = { validos: "Encontrados", semmatch: "Sem match",
                        ingeridos: "Gravados no banco" };

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
    if (el) el.textContent = (rotulos && rotulos[k]) || padrao;
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

function conectarWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => $("ws-badge").classList.add("on");
  ws.onclose = () => { $("ws-badge").classList.remove("on"); setTimeout(conectarWS, 2500); };
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
      if (p.tem_cnpj) tags.push('<span class="bres-tag" style="background:#e6f4ea;color:#1e8e3e">CNPJ</span>');
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

/* Botão "Baixar imagens" (passo pós, à parte da cascata) */
$("btn-baixar-imgs").onclick = async () => {
  if (jobRodando) { toast("Já há um processo rodando.", "err"); return; }
  const body = { modo: "baixar_imagens", opcoes: { workers: 8 } };
  try {
    const j = await (await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) })).json();
    if (j.erro) return toast(j.erro, "err");
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

function limparQuadras() {
  fecharTooltips();
  qCamadas.forEach((c) => { try { map.removeLayer(c); } catch { /* ok */ } });
  qCamadas = [];
  qLayers = {};
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

async function carregarQuadras(sid, silencioso = false) {
  if (!sid) return;
  const r = await fetch(`/api/quadras/${encodeURIComponent(sid)}`);
  const gj = await r.json();
  if (gj.erro) { toast(gj.erro, "erro"); return; }
  qDados = gj;
  qSessaoAtual = sid;
  limparQuadras();
  mapearCoresFaces(porCamadaQ("face"));

  // a área desenhada
  addCamada("area", L.geoJSON({ type: "FeatureCollection", features: porCamadaQ("area") }, {
    pane: "paneQuadras", interactive: false,
    style: { color: "#ffffff", weight: 1, opacity: 0.35, fill: false, dashArray: "8,8" },
  }).addTo(map));

  // vias do OSM, com o nome canônico lido no Maps
  addCamada("vias", L.geoJSON({ type: "FeatureCollection", features: porCamadaQ("via") }, {
    pane: "paneFaces",
    style: { color: "#ffd400", weight: 2, opacity: 0.5 },
    onEachFeature: (f, l) => {
      const p = f.properties;
      const dif = p.nome_canonico && p.nome_osm &&
                  _normVia(p.nome_canonico) !== _normVia(p.nome_osm);
      l.bindTooltip(
        `🛣️ <b>${esc(p.nome_canonico || p.nome_osm || "sem nome")}</b>` +
        (dif ? `<br><span style="color:#f0b429">OSM: ${esc(p.nome_osm)}</span>` : "") +
        (!p.nome_canonico ? `<br><small style="color:#f0b429">canônico não lido</small>` : "") +
        `<br><small>${esc(p.tipo || "")} · ${Math.round(p.comprimento_m || 0)} m</small>`,
        { sticky: true });
    },
  }).addTo(map));

  // quadra do OSM (eixo das vias) e a borda REAL
  addCamada("quadraOsm", L.geoJSON({ type: "FeatureCollection", features: porCamadaQ("quadra_osm") }, {
    pane: "paneQuadras", interactive: false,
    style: { color: "#ffffff", weight: 1, opacity: 0.4, fill: false, dashArray: "2,6" },
  }).addTo(map));
  addCamada("quadraReal", L.geoJSON({ type: "FeatureCollection", features: porCamadaQ("quadra_real") }, {
    pane: "paneQuadras",
    // a quadra de rua aberta não é quarteirão: é o corredor de um lado da via.
    // Desenhá-la igual a um quarteirão faria o mapa afirmar o que não existe.
    style: (f) => (f.properties.via_aberta
      ? { color: "#ffffff", weight: 1, opacity: 0.45, fill: false, dashArray: "4,5" }
      : { color: "#ffffff", weight: 2, opacity: 0.9, fill: false }),
    onEachFeature: (f, l) => l.bindTooltip(
      (f.properties.via_aberta
        ? `Rua aberta ${f.properties.id} — um lado da via`
        : `Quadra ${f.properties.id}`) +
      `<br>${Math.round(f.properties.area_m2 || 0)} m²` +
      `<br><small>borda real · recuo ${f.properties.recuo_medio_m} m da meia-via</small>`,
      { sticky: true }),
  }).addTo(map));

  // faces: cada uma com cor própria na sessão e deslocada para o lado da sua
  // quadra — duas faces sobre a mesma rua aparecem lado a lado, não empilhadas
  const centros = {};
  porCamadaQ("quadra_osm").forEach((q) => {
    const a = q.geometry.coordinates[0];
    centros[q.properties.id] = [a.reduce((s, c) => s + c[0], 0) / a.length,
                                a.reduce((s, c) => s + c[1], 0) / a.length];
  });
  const facesDesl = porCamadaQ("face").map((f) => ({
    ...f,
    geometry: { ...f.geometry,
                coordinates: deslocarParaDentro(f.geometry.coordinates,
                                                centros[f.properties.quadra_id]) },
  }));
  addCamada("faces", L.geoJSON({ type: "FeatureCollection", features: facesDesl }, {
    pane: "paneFaces",
    style: (f) => ({ color: corDaFace(f.properties.quadra_id, f.properties.face_idx),
                     weight: 6, opacity: 0.9, lineCap: "round" }),
    onEachFeature: (f, l) => {
      const p = f.properties;
      l.bindTooltip(
        `<b>${p.via_aberta ? "rua aberta" : "quadra"} ${p.quadra_id} · face ` +
        `${p.face_idx}</b> · ` +
        `${Math.round(p.comprimento_m || 0)} m<br>` +
        `${esc(p.nome_canonico || p.nome_osm || "via não identificada")}<br>` +
        `numeração <b>${p.paridade || "indefinida"}</b>` +
        (p.paridade_por ? `<br><small>${esc(p.paridade_por)}</small>` : "") +
        `<br><small>recuo do eixo — par ${p.recuo_par_m ?? "—"} m · ` +
        `ímpar ${p.recuo_impar_m ?? "—"} m</small>`, { sticky: true });
      l.on("click", () => {
        const k = `${p.quadra_id}:${p.face_idx}`;
        qFaceFiltro = qFaceFiltro === k ? null : k;
        aplicarFiltroFace();
        montarLegendaQuadras();
      });
    },
  }).addTo(map));

  // TELHADOS do Overture (passo 7) — referência, não substituem o ponto.
  // Quem tem ÂNCORA confirmada no Maps sai destacado: é o único par
  // (número, posição) medido no chão que a face tem.
  addCamada("telhados", L.geoJSON(
    { type: "FeatureCollection", features: porCamadaQ("telhado") }, {
      pane: "paneQuadras",
      style: (f) => {
        const p = f.properties;
        const c = corDaFace(p.quadra_id, p.face_idx);
        return p.ancora_numero
          ? { color: "#ffffff", weight: 2.5, fillColor: c, fillOpacity: 0.55 }
          : { color: c, weight: 1, opacity: 0.7, fillColor: c, fillOpacity: 0.18 };
      },
      onEachFeature: (f, l) => {
        const p = f.properties;
        l.bindTooltip(
          `🏠 <b>${Math.round(p.area_m2 || 0)} m²</b>` +
          (p.pavimentos ? ` · ${p.pavimentos} pav.` : "") +
          (p.altura_m ? ` · ${p.altura_m} m` : "") +
          `<br><small>quadra ${p.quadra_id} · face ${p.face_idx ?? "—"}` +
          (p.dist_face_m != null ? ` · ${p.dist_face_m} m da face` : "") + `</small>` +
          (p.ancora_via
            ? `<br><small style="color:#22e07a">âncora Maps: ` +
              `${p.ancora_numero ? "<b>nº " + p.ancora_numero + "</b> " : ""}` +
              `${esc(p.ancora_via)}</small>`
            : ""), { sticky: true });
      },
    }));

  // a borda REAL de cada face — o trilho sobre o qual o passo 6 distribui
  addCamada("faceReal", L.geoJSON({ type: "FeatureCollection", features: porCamadaQ("face_real") }, {
    pane: "paneFaces", interactive: false,
    style: (f) => ({ color: corDaFace(f.properties.quadra_id, f.properties.face_idx),
                     weight: 2, opacity: 0.55, dashArray: "6,5" }),
  }).addTo(map));

  // PASSO 6 — a posição alinhada na borda real, ligada à original por tracejado.
  // A original continua desenhada (mais apagada): a correção tem de ser sempre
  // auditável contra o que o cadastro diz.
  // um marcador por PORTA: mesmo logradouro e número já foram para o mesmo
  // lugar, então desenhar um por endereço só empilharia círculos idênticos
  const vistosGrupo = new Set();
  const alinhados = porCamadaQ("ponto").filter((f) => {
    const p = f.properties;
    if (p.lat_alinhado == null) return false;
    if (!p.grupo_id) return true;
    if (vistosGrupo.has(p.grupo_id)) return false;
    vistosGrupo.add(p.grupo_id);
    return true;
  });
  if (alinhados.length) {
    addCamada("ligacoes", L.geoJSON({
      type: "FeatureCollection",
      features: alinhados.map((f) => ({
        type: "Feature", properties: f.properties,
        geometry: { type: "LineString",
                    coordinates: [f.geometry.coordinates,
                                  [f.properties.lng_alinhado, f.properties.lat_alinhado]] },
      })),
    }, { pane: "paneFaces", interactive: false,
         // A ligação até a coordenada original é a prova da correção. Quando o
         // movimento é grande ela deixa de ser um fio cinza: um endereço andar
         // 40 m é coisa para CONFERIR, não para passar batido no mapa.
         style: (f) => {
           const p = f.properties;
           if (p.alinhado_modo === "realocado")
             return { color: "#d97706", weight: 2.6, opacity: 0.95, dashArray: "7,4" };
           if ((p.desloc_m || 0) >= DESLOC_DESTAQUE_M)
             return { color: "#dc2626", weight: 2, opacity: 0.85, dashArray: "5,4" };
           return { color: "#9aa4b0", weight: 1, opacity: 0.55, dashArray: "3,4" };
         } })
      .addTo(map));
    addCamada("alinhados", L.geoJSON({
      type: "FeatureCollection",
      features: alinhados.map((f) => ({
        type: "Feature", properties: f.properties,
        geometry: { type: "Point",
                    coordinates: [f.properties.lng_alinhado, f.properties.lat_alinhado] },
      })),
    }, {
      pane: "paneMarcadores",
      pointToLayer: (f, latlng) => {
        const p = f.properties;
        const c = p.resgate ? escurecer(corDaFace(p.quadra_id, p.face_idx))
                            : corDaFace(p.quadra_id, p.face_idx);
        // porta com vários endereços fica MAIOR — é uma só, mas pesa mais
        const n = p.grupo_n || 1;
        // perpendicular = só projetado na testada, sem entrar na régua da
        // numeração: anel TRACEJADO, para não se confundir com o alinhado
        const perp = p.alinhado_modo === "perpendicular";
        const noTelhado = p.alinhado_modo === "telhado";
        // preservado = via que não fecha quadra, com telhado perto: ficou na
        // coordenada original de propósito. Anel claro, para não parecer que a
        // régua o alcançou nem que ele foi projetado.
        const preso = p.alinhado_modo === "preservado";
        // anel âmbar: só aparece em sessão ANTERIOR à mudança que fez da via
        // aberta uma face de verdade. Hoje esses pontos saem como 'regua' ou
        // 'perpendicular', como os de qualquer face — o modo fica reconhecido
        // para que a sessão antiga continue sendo desenhada pelo que ela é.
        const rua = p.alinhado_modo === "via_aberta"
                 || p.alinhado_modo === "via_aberta_perp";
        const ruaPerp = p.alinhado_modo === "via_aberta_perp";
        // realocado = o número não era da série daquela face e o endereço foi
        // levado para o trecho da rua onde ele se encaixa. Anel âmbar GROSSO:
        // é a maior intervenção que o processo faz num ponto.
        const real = p.alinhado_modo === "realocado";
        return L.circleMarker(latlng, {
          pane: "paneMarcadores", radius: 5 + Math.min(6, Math.sqrt(n - 1) * 3),
          weight: real ? 3.2 : (perp || preso || rua ? 2.4 : (n > 1 ? 2.2 : 1.6)),
          dashArray: perp || ruaPerp ? "3,3" : null,
          color: real ? "#d97706" : preso ? "#38bdf8" : rua ? "#f59e0b"
               : perp ? "#111827" : (noTelhado ? "#ffffff" : "#0b0f14"),
          fillColor: c, fillOpacity: perp ? 0.75 : 1, opacity: 1 });
      },
      onEachFeature: (f, l) => {
        const p = f.properties;
        const perp = p.alinhado_modo === "perpendicular";
        l.bindTooltip(
          `<b>${esc(p.logradouro || "—")}, ${p.numero}</b>` +
          (p.grupo_n > 1 ? ` <b>· ${p.grupo_n} endereços nesta porta</b>` : "") +
          `<br><small>${perp ? "⚠ posto só na perpendicular"
              : p.alinhado_modo === "realocado" ? "⚠ REALOCADO para outro trecho da rua"
              : p.alinhado_modo === "preservado" ? "📌 mantido onde estava"
              : p.alinhado_modo === "telhado" ? "🏠 casado com um telhado"
              : p.alinhado_modo === "interpolado" ? "interpolado entre telhados"
              : "alinhado pela régua da numeração"} · ` +
          `${p.desloc_m} m da coordenada original` +
          `<br>${esc(p.alinhado_por || "")}</small>`, { sticky: true });
      },
    }).addTo(map));
  }

  // os pontos, na coordenada ORIGINAL do CNEFE
  addCamada("origens", L.geoJSON({ type: "FeatureCollection", features: porCamadaQ("ponto") }, {
    pane: "paneMarcadores",
    pointToLayer: (f, latlng) => {
      const p = f.properties;
      // canonico: true = é desta face · false = destoa · null = não dá para julgar
      const base = p.canonico === true ? corDaFace(p.quadra_id, p.face_idx)
        : p.canonico === false ? COR_DESTOA : COR_INDEF;
      // resgatado pela coordenada: mesma face, tom mais escuro
      const cor = (p.canonico === true && p.resgate) ? escurecer(base) : base;
      // quem já tem posição alinhada fica APAGADO aqui: a leitura principal
      // passa a ser a da borda real, e esta vira só a origem, para conferência.
      // O preservado NÃO saiu do lugar — apagá-lo diria o contrário.
      const movido = p.lat_alinhado != null && p.alinhado_modo !== "preservado";
      return L.circleMarker(latlng, {
        pane: "paneMarcadores",
        radius: movido ? 3 : (p.origem === "dentro_quadra" ? 5 : 4),
        weight: movido ? 1 : (p.canonico === false ? 2 : 1),
        color: movido ? "#9aa4b0" : "#0b0f14", fillColor: cor,
        fillOpacity: movido ? 0.25 : (p.canonico === null ? 0.5 : 0.95),
        opacity: movido ? 0.4 : 0.9,
      });
    },
    onEachFeature: (f, l) => {
      const p = f.properties;
      l.bindTooltip(
        `<b>${esc(p.logradouro || "—")}${p.numero ? ", " + p.numero : ""}</b>` +
        (p.estabelecimento ? `<br>${esc(p.estabelecimento)}` : "") +
        `<br><small>face ${p.face_idx ?? "—"} · ` +
        (p.canonico === true
          ? (p.resgate ? `<b>resgatado</b> — ${esc(p.motivo || "")}` : "condiz com a face")
          : p.canonico === false ? `<span style="color:#ff9b9b">${esc(p.motivo || "destoa")}</span>`
          : esc(p.motivo || "sem julgamento")) + `</small>` +
        `<br><small>${p.origem === "dentro_quadra" ? "dentro da quadra"
          : `a ${p.dist_via_m} m do eixo da via`}` +
        (p.cep ? ` · CEP ${esc(p.cep)}` : "") +
        (p.nv_geo ? ` · coord. nível ${esc(p.nv_geo)}` : "") + `</small>` +
        (p.alinhado_modo === "perpendicular"
          ? `<br><small style="color:#f0b429">⚠ ${esc(p.alinhado_por || "")}</small>`
          : ""),
        { sticky: true });
    },
  }).addTo(map));

  aplicarVisualizacao();          // respeita o que o usuário escolheu ver
  const s = gj.sessao || {};
  $("q-estado").innerHTML =
    `<b>${esc(sid)}</b> — passo ${s.passo}/8 · ${esc(s.municipio || "?")}/${esc(s.uf || "?")}` +
    (s.erro ? `<br><span style="color:#ff9b9b">${esc(s.erro)}</span>` : "");
  montarLegendaQuadras();
  if (!silencioso) {
    try { map.fitBounds(qCamadas[0].getBounds().pad(0.05)); } catch { /* ok */ }
    const np = porCamadaQ("ponto").length;
    toast(`${porCamadaQ("quadra_osm").length} quadras · ${porCamadaQ("via").length} vias · ` +
          `${np} pontos`, "ok");
  }
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
async function carregarUltimaQuadra(silencioso = false) {
  try {
    const ss = await (await fetch("/api/quadras/sessoes?limite=1")).json();
    if (ss && ss[0]) await carregarQuadras(ss[0].id, silencioso);
  } catch { /* ok */ }
}

/* Enquanto o job de quadras roda, o mapa vai preenchendo a cada passo. */
let qTimer = null;
function acompanharQuadras() {
  if (qTimer) clearInterval(qTimer);
  qTimer = setInterval(async () => {
    if (!jobRodando) {
      clearInterval(qTimer); qTimer = null;
      await carregarUltimaQuadra(true);
      return;
    }
    await carregarUltimaQuadra(true);
  }, 6000);
}

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
      const d = await r.json();
      if (d.erro) { toast(d.erro, "err"); return; }
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
  const grupos = [...porNota.values()];
  const somaConf = porNota.get("4/4")?.n || 0;
  return `<div class="d-card g-roxo">
    <div class="d-tit">CNPJ por confiança</div>
    <div class="d-num">${nfmt(cob.cnpj)}<small>${pctd(cob.cnpj, cob.total)}% da base</small></div>
    <div class="d-destaque">
      <b>${nfmt(somaConf)}</b> conferidos ponta a ponta
      <span>${pctd(somaConf, cob.cnpj)}% dos CNPJs</span>
    </div>
    ${grupos.map((g) => `<div class="conf-grupo">
      <div class="conf-cab">
        <span class="tag ${cls(g.nota)}">${esc(g.nota)}</span>
        <span class="conf-n">${nfmt(g.n)}</span>
      </div>
      ${g.itens.map((i) => `<div class="conf-lin">
        <span>${esc(i.criterio)}</span><b>${nfmt(i.n)}</b></div>`).join("")}
    </div>`).join("")}
    <div class="d-nota"><b>4/4</b> = dígito verificador + existe na Receita + UF e
      município conferem. É o corte para cobrança; o resto serve para prospecção.</div>
  </div>`;
}

function cardCadastro(cad) {
  if (!cad) {
    return `<div class="d-card g-laranja">
      <div class="d-tit">Cadastro do cliente</div>
      <div class="d-nota">Nenhuma base de cadastro importada para esta cidade.
        Importe na aba <b>Importar planilha</b> para ver o que visitar.</div></div>`;
  }
  const cor = { ja_cadastrado: "baixo", reclassificar_alta: "ok",
                reclassificar_media: "med", reclassificar_baixa: "med",
                novo_comercial: "ok", sem_poi: "" };
  return `<div class="d-card wide g-laranja">
    <div class="d-tit">Cadastro do cliente × POIs</div>
    <div class="d-num">${nfmt(cad.total)}<small>imóveis na base</small></div>
    <table class="d-tab">${cad.flags.map((f) => `<tr>
      <td><span class="tag ${cor[f.flag] || ""}">${esc(f.flag)}</span></td>
      <td style="text-align:left;color:var(--ink-2);font-size:12px">
        ${esc(cad.descricoes[f.flag] || "")}</td>
      <td>${nfmt(f.n)}</td></tr>`).join("")}</table>
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
  const mb = sv.bytes / 1048576;
  return `<div class="d-card">
    <div class="d-tit">Imagens no banco</div>
    <div class="d-num">${nfmt(sv.imagens)}<small>fachadas</small></div>
    <table class="d-tab">
      <tr><td>tamanho</td><td>${mb > 1024 ? (mb / 1024).toFixed(1) + " GB" : mb.toFixed(0) + " MB"}</td></tr>
      <tr><td>sem panorama</td><td>${nfmt(cob.sem_panorama)}</td></tr>
      <tr><td>fotos do Maps</td><td>${nfmt(cob.fotos)}</td></tr>
    </table>
    <div class="d-nota">Gravadas em <code>streetview_imgs</code>, não em pasta.</div>
  </div>`;
}

function cardOrigem(origem, status) {
  return `<div class="d-card">
    <div class="d-tit">Origem e status</div>
    <table class="d-tab">
      ${origem.slice(0, 6).map((o) => `<tr><td>${esc(o.fonte)} / ${esc(o.dado)}</td>
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
  if (g === "tudo" || g === "cadastro") html += cardCadastro(d.cadastro);
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
