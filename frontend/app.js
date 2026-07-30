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
const BASES = {
  limpo: {
    nome: "Mapa limpo", ico: "🗺️",
    layer: L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 20, subdomains: "abcd" }),
  },
  satelite: {
    nome: "Satélite", ico: "🛰️",
    layer: L.tileLayer("https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
      { maxZoom: 21, maxNativeZoom: 21 }),
  },
  hibrido: {
    nome: "Satélite + ruas", ico: "🛣️",
    layer: L.tileLayer("https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
      { maxZoom: 21, maxNativeZoom: 21 }),
  },
  escuro: {
    nome: "Escuro", ico: "🌙",
    layer: L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 20, subdomains: "abcd" }),
  },
};
let baseAtual = localStorage.getItem("cr_base") || "limpo";
if (!BASES[baseAtual]) baseAtual = "limpo";
BASES[baseAtual].layer.addTo(map);

function trocarBase(chave) {
  if (!BASES[chave] || chave === baseAtual) return;
  map.removeLayer(BASES[baseAtual].layer);
  BASES[chave].layer.addTo(map);
  BASES[chave].layer.bringToBack();          // nunca por cima dos desenhos
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
function poisBase() {              // POIs do município selecionado (base dos filtros e contagens)
  if (!municipioSel) return [];
  return [...allPois.values()].filter((p) => _normCidade(p.cidade) === municipioSel);
}

function origemDe(p) {
  if (p.fonte === "pipeline") return "outros";
  for (const o of ORIGENS) if (o.teste(p)) return o.key;
  return "outros";
}

function passaAtributos(p) {
  for (const a of ATRIBUTOS) if (atributosAtivos.has(a.key) && !a.teste(p)) return false;
  return true;
}

function visivel(p) {
  if (!municipioSel || _normCidade(p.cidade) !== municipioSel) return false; // só o município selecionado
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
  allPois.set(poi.id, poi);
  if (markers.has(poi.id)) {
    cluster.removeLayer(markers.get(poi.id));
    markers.delete(poi.id);
  }
  if (visivel(poi)) _criarMarker(poi, novo);
  if (novo) renderChips();
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
    badge.classList.remove("sel");
    badge.innerHTML = `<span class="dot"></span>Clique num <b>município</b> no mapa`;
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
  const sv = poi.streetview_path && poi.streetview_path !== "NA" ? `/streetview/${esc(poi.streetview_path)}` : null;
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

function setAreaLayer(latlngs) {
  if (areaLayer) { map.removeLayer(areaLayer); areaLayer = null; }
  if (latlngs && latlngs.length >= 3) {
    areaLayer = L.polygon(latlngs, AREA_STYLE).addTo(map);
    $("area-status").innerHTML = `Área definida <b>(${latlngs.length} vértices)</b> — pontos fora dela serão rejeitados.`;
    $("area-status").className = "hint ok";
  } else {
    $("area-status").textContent = "Nenhuma área definida — desenhe o polígono no mapa.";
    $("area-status").className = "hint warn";
  }
  atualizarBotoes();
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
    if (modo === "quadras") carregarUltimaQuadra();
    else { limparQuadras(); }
    atualizarBotoes();
  };
});

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
    opcoes = {
      sessao: $("op-sessao").value.trim() || "mineracao",
      step: parseFloat($("op-step").value) || 150,
      details: $("op-details").checked,
    };
  } else if (modo === "quadras") {
    opcoes = { com_maps: !!$("op-q-maps")?.checked,
               sem_proxy: !$("op-q-proxy")?.checked };
  } else if (modo === "enriquecimento") {  // cascata única Maps→Web→StreetView
    modoJob = "enriquecer_tudo";
    opcoes = {
      workers: parseInt($("op-enr-workers").value) || 6,
      no_proxy: !$("enr-proxy").checked,
      pular_maps: !$("enr-maps").checked,
      pular_web: !$("enr-web").checked,
      pular_streetview: !$("enr-sv").checked,
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
  const labels = { rodando: "⚙️ Rodando", finalizado: "✅ Finalizado", parado: "⏹ Parado", erro: "❌ Erro", ocioso: "" };
  if (j && j.status && j.status !== "ocioso") {
    $("job-status-txt").innerHTML = `${labels[j.status] || j.status} — <b>${MODO_LABEL[j.modo] || j.modo}</b>` +
      (j.arquivo ? ` (${esc(j.arquivo)})` : "") + (j.fim ? ` às ${j.fim.slice(11, 16)}` : "");
  }
  if (j && j.contadores) aplicarContadores(j.contadores, j.total || 0);
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
async function carregarStats() {
  const fmt = (v) => (v || 0).toLocaleString("pt-BR");
  if (!municipioNome) { // nada selecionado → painel em branco até clicar num município
    ["db-validos", "db-tel", "db-cnpj", "db-sv", "db-fotos", "db-coments",
     "db-analisados", "db-aprov", "db-reprov", "db-rec"].forEach((id) => { if ($(id)) $(id).textContent = "—"; });
    dbCountLocal = 0;
    return;
  }
  try {
    const s = await (await fetch("/api/stats?cidade=" + encodeURIComponent(municipioNome))).json();
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
  statsTimer = setTimeout(() => { statsTimer = null; carregarStats(); }, 10000);
}

function aplicarContadores(c, total) {
  setVal("st-proc", c.processados);
  setVal("st-validos", c.validos);
  setVal("st-recup", c.recuperados);
  setVal("st-desc", c.descobertos);
  setVal("st-fora", c.fora_area);
  setVal("st-semmatch", c.sem_match + (c.erros || 0));
  setVal("st-ingeridos", c.ingeridos);
  if (total > 0) {
    const pct = Math.min(100, (c.processados / total) * 100);
    $("st-bar").style.width = pct.toFixed(1) + "%";
    $("st-total-sub").textContent = `${(c.processados || 0).toLocaleString("pt-BR")} de ${total.toLocaleString("pt-BR")} (${pct.toFixed(0)}%)`;
  } else {
    $("st-bar").style.width = c.processados ? "100%" : "0%";
    $("st-total-sub").textContent = jobRodando ? "processando…" : "aguardando processo…";
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
      aplicarContadores(msg.dados.contadores || {}, msg.dados.total || 0);
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
    style: { color: "#ffffff", weight: 2, opacity: 0.9, fill: false },
    onEachFeature: (f, l) => l.bindTooltip(
      `Quadra ${f.properties.id}<br>${Math.round(f.properties.area_m2 || 0)} m²` +
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
        `<b>quadra ${p.quadra_id} · face ${p.face_idx}</b> · ` +
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
         style: { color: "#9aa4b0", weight: 1, opacity: 0.55, dashArray: "3,4" } })
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
        // distribuído na PRÓPRIA rua que não fecha quadra: anel âmbar, para se
        // distinguir de quem foi para a testada de um quarteirão
        const rua = p.alinhado_modo === "via_aberta"
                 || p.alinhado_modo === "via_aberta_perp";
        const ruaPerp = p.alinhado_modo === "via_aberta_perp";
        return L.circleMarker(latlng, {
          pane: "paneMarcadores", radius: 5 + Math.min(6, Math.sqrt(n - 1) * 3),
          weight: perp || preso || rua ? 2.4 : (n > 1 ? 2.2 : 1.6),
          dashArray: perp || ruaPerp ? "3,3" : null,
          color: preso ? "#38bdf8" : rua ? "#f59e0b"
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