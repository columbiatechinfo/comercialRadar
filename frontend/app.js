/* ComercialRadar — frontend (mapa + polígono + tempo real) */
"use strict";

const $ = (id) => document.getElementById(id);

/* ────────────────────────────────────────────────────────────
   Mapa base (tiles claros — mapa limpo)
──────────────────────────────────────────────────────────── */
const map = L.map("map", { zoomControl: false, attributionControl: false })
  .setView([-2.9055, -41.7734], 13); // Parnaíba-PI como partida

L.control.zoom({ position: "bottomright" }).addTo(map);
L.control.attribution({ position: "bottomright", prefix: false })
  .addAttribution('&copy; OpenStreetMap &middot; CARTO').addTo(map);

L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  maxZoom: 20, subdomains: "abcd",
}).addTo(map);

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
let malhaLayer = null;
let malhaVisivel = true;

const MALHA_STYLE = { color: "#5b6b80", weight: 1.2, opacity: 0.9, fillColor: "#5b6b80", fillOpacity: 0.03 };

async function carregarMalha() {
  try {
    const gj = await (await fetch("/api/malha")).json();
    if (gj.erro || !gj.features) return;
    malhaLayer = L.geoJSON(gj, {
      style: MALHA_STYLE,
      onEachFeature: (f, l) => {
        l.bindTooltip(f.properties?.nome || "", { sticky: true, direction: "top", className: "muni-tip" });
        l.on("mouseover", () => { if (l !== municipioLayer) l.setStyle({ weight: 2.4, color: "#334155", fillOpacity: 0.08 }); });
        l.on("mouseout", () => { if (l !== municipioLayer) malhaLayer.resetStyle(l); });
        l.on("click", () => selecionarMunicipio(f.properties?.nome, l));
      },
    });
    if (malhaVisivel) malhaLayer.addTo(map);
    // sem área/município: enquadra na malha (não nos POIs, que só aparecem ao clicar)
    if (!areaLayer && !municipioSel) {
      try { map.fitBounds(malhaLayer.getBounds().pad(0.05)); } catch { /* ok */ }
    }
    renderChips();
  } catch { /* IBGE fora do ar — segue sem a malha */ }
}

/* Seleção de município por clique no polígono da malha */
const MUNI_STYLE_SEL = { color: "#0e7490", weight: 3, opacity: 1, fillColor: "#0e7490", fillOpacity: 0.07 };

function selecionarMunicipio(nome, layer) {
  const norm = _normCidade(nome);
  if (!norm) return;
  if (municipioSel === norm) return limparMunicipio();   // clicar de novo desmarca
  if (municipioLayer && malhaLayer) malhaLayer.resetStyle(municipioLayer);
  municipioSel = norm; municipioNome = nome; municipioLayer = layer;
  layer.setStyle(MUNI_STYLE_SEL); layer.bringToFront();
  aplicarFiltro();
  try { map.fitBounds(layer.getBounds().pad(0.12), { maxZoom: 15 }); } catch { /* ok */ }
  carregarStats();
  atualizarBannerMunicipio();
}

window.limparMunicipio = function () {
  if (municipioLayer && malhaLayer) malhaLayer.resetStyle(municipioLayer);
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
  if (malhaLayer) {
    if (malhaVisivel) malhaLayer.addTo(map); else map.removeLayer(malhaLayer);
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
  html += `<div class="m-head"><div class="m-nome">${esc(poi.nome)}</div>
           <button class="m-close" onclick="fecharModal()">✕</button></div>`;
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
window.fecharModal = () => $("modal-overlay").classList.add("hidden");
$("modal-overlay").addEventListener("click", (e) => { if (e.target.id === "modal-overlay") fecharModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { fecharModal(); $("confirm-overlay").classList.add("hidden"); } });

/* ────────────────────────────────────────────────────────────
   Polígono da área
──────────────────────────────────────────────────────────── */
let areaLayer = null;
let drawer = null;
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

function atualizarBotoes() {
  const temArea = !!areaLayer;
  const pronto = temArea && !jobRodando && (modo !== "planilha" || !!arquivoImportado);
  $("btn-iniciar").disabled = !pronto;
  $("btn-iniciar").classList.toggle("hidden", jobRodando);
  $("btn-parar").classList.toggle("hidden", !jobRodando);
  if (!temArea) $("job-status-txt").textContent = "Defina a área (passo 1) para liberar o início.";
  else if (modo === "planilha" && !arquivoImportado && !jobRodando) $("job-status-txt").textContent = "Importe a planilha (passo 2).";
  else if (!jobRodando) $("job-status-txt").textContent = "Pronto para iniciar.";
}

$("btn-iniciar").onclick = async () => {
  let modoJob = modo;
  let opcoes;
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
  } else { // enriquecimento: cascata única Maps→Web→StreetView
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
  toast(`Processo ${modo === "planilha" ? "da planilha" : "de mineração"} iniciado ▶`, "ok");
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
  atualizarBannerMunicipio();      // badge "clique num município"
  conectarWS();
  try { aplicarJob(await (await fetch("/api/jobs/atual")).json()); } catch { /* ok */ }
  atualizarBotoes();
  toast("Clique num município no mapa para ver os dados 🗺️", "ok");
})();
