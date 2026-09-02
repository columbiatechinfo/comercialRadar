() => {
  let node = null, porId = {};
  for (const s of document.querySelectorAll('script[id^="data-deferred-state"]')) {
    let d; try { d = JSON.parse(s.textContent || ''); } catch (e) { continue; }
    const cacar = (no, prof) => {
      if (!no || typeof no !== 'object' || prof > 14) return;
      if (Array.isArray(no)) { for (const x of no.slice(0, 30)) cacar(x, prof + 1); return; }
      if (!node && no.pdpPresentation) node = no;
      if (Array.isArray(no.sections))
        for (const sec of no.sections) if (sec && sec.sectionId)
          porId[sec.sectionId] = sec.section || sec;
      for (const k of Object.keys(no)) cacar(no[k], prof + 1);
    };
    cacar(d, 0);
  }
  if (!node) return {erro: 'sem payload'};
  const pp = node.pdpPresentation || {};
  const t = (v) => {
    if (v == null) return null;
    if (typeof v === 'string') return v;
    if (typeof v !== 'object') return String(v);
    return v.localizedStringWithTranslationPreference || v.localizedContent
        || v.text || v.title || (v.content ? t(v.content) : null);
  };
  const coord = (node.location && node.location.coordinate)
             || (pp.location && pp.location.coordinate) || {};
  const comod = [];
  for (const g of ((pp.amenities || {}).seeAllAmenitiesGroups || []))
    for (const a of (g.amenities || []))
      if (a && a.title && a.available !== false) comod.push(a.title);
  const regras = [];
  for (const g of ((pp.rules || {}).groupItems || []))
    for (const i of (g.items || [])) if (i && i.title) regras.push(i.title);
  const fotos = new Set();
  const bf = (no, prof) => {
    if (!no || typeof no !== 'object' || prof > 10 || fotos.size > 60) return;
    if (Array.isArray(no)) { for (const x of no) bf(x, prof + 1); return; }
    if (typeof no.baseUrl === 'string') fotos.add(no.baseUrl);
    for (const k of Object.keys(no)) bf(no[k], prof + 1);
  };
  bf(porId.HERO_DEFAULT, 0); bf(porId.PHOTO_TOUR_SCROLLABLE_MODAL, 0);
  return {
    titulo: t(node.description && node.description.name),
    tipo_resumo: t(pp.overview && pp.overview.title),
    resumo_curto: t(pp.sharingConfig && pp.sharingConfig.ugcTitle),
    localidade: t(pp.localizedLocation),
    local_subtitulo: t(pp.location && pp.location.subtitle),
    lat: coord.latitude, lng: coord.longitude,
    descricao: t((pp.descriptions || {}).longDescriptionHtml),
    destaques: (pp.highlights || []).map(h => t(h.headline)).filter(Boolean),
    comodidades: comod, regras: regras, fotos: [...fotos],
    anfitriao: {
      nome: ((pp.hostInfo || {}).passportData || {}).name || null,
      taxa_resposta: (pp.hostInfo || {}).responseRateText || null,
      tempo_resposta: (pp.hostInfo || {}).responseTimeText || null,
    },
    coanfitrioes: (node.cohosts || []).map(c => c.displayFirstName),
  };
}
