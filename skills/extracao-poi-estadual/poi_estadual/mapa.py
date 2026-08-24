# -*- coding: utf-8 -*-
"""Mapa A2L de POIs: HTML unico, seletor de cidade, cor por confianca, Street View.

Correcao de seguranca v2. Na v1 os campos `nome`, `endereco`, `telefone`, `site`,
`categoria` e `data` iam direto para `innerHTML`/`setContent` sem escape. Nome e
site vem de OSM/Overture/FSQ — campos livres, editaveis por qualquer colaborador —
entao um valor com `<img onerror=...>` executava script no navegador de quem
abrisse o mapa. Aqui:

  - `esc()` neutraliza & < > " ' em TODO texto interpolado;
  - `safeUrl()` so aceita http/https (bloqueia `javascript:`);
  - o `<option>` do seletor e escapado no lado Python.

Continua dependendo de Leaflet (CDN) e dos tiles Google — nao e offline. Declarado
no rodape do proprio arquivo.
"""
import base64
import gzip
import html
import json
import os
import time

import pandas as pd

COLS = ["nome", "segmento", "categoria_pt", "confianca_classe", "endereco_completo",
        "telefone", "site", "data_atualizacao", "lat", "lon", "NOME_MUNICIPIO"]

TEMPLATE = r'''<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mapa POIs - __UF__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter','Segoe UI',sans-serif;background:#0a1220;overflow:hidden}
#map{position:absolute;inset:0}
.topbar{position:absolute;top:0;left:0;right:0;z-index:1000;height:56px;background:linear-gradient(135deg,rgba(16,28,48,.97),rgba(15,40,70,.97));border-bottom:1px solid #1e3a5f;display:flex;align-items:center;padding:0 18px;gap:18px;backdrop-filter:blur(10px)}
.logo{font-family:Georgia,serif;font-weight:700;font-size:20px;color:#fff;letter-spacing:-.5px}
.logo span{color:#1B9AAA}
.proj{font-size:10px;color:#C9963B;font-weight:700;letter-spacing:1.5px;border:1px solid rgba(201,150,59,.4);padding:3px 9px;border-radius:12px;background:rgba(201,150,59,.12)}
.city-sel{display:flex;align-items:center;gap:8px;margin-left:8px}
.city-sel label{font-size:11px;color:#8aa6c8;text-transform:uppercase;letter-spacing:.5px;font-weight:600}
.city-sel select{background:#0a1628;color:#fff;border:1px solid #1e3a5f;border-radius:8px;padding:8px 12px;font-size:13px;font-family:inherit;font-weight:600;cursor:pointer;min-width:210px;outline:none}
.city-sel select:focus{border-color:#1B9AAA}
.stats{display:flex;gap:20px;margin-left:auto;align-items:center}
.stat{text-align:right}.stat b{font-family:Georgia,serif;font-size:17px;color:#fff;font-weight:700;display:block;line-height:1}.stat span{font-size:9px;color:#8aa6c8;text-transform:uppercase;letter-spacing:.5px}
.cmctl{display:flex;background:#0a1628;border-radius:7px;padding:3px;border:1px solid #1e3a5f}
.cmctl button{background:transparent;border:none;color:#8aa6c8;font-family:inherit;font-size:11px;font-weight:600;padding:6px 11px;border-radius:5px;cursor:pointer}
.cmctl button.active{background:#1B9AAA;color:#fff}
.sidebar{position:absolute;top:56px;left:0;bottom:0;width:240px;background:rgba(16,28,48,.96);border-right:1px solid #1e3a5f;z-index:900;display:flex;flex-direction:column;backdrop-filter:blur(10px)}
.sb-block{display:flex;flex-direction:column;overflow:hidden}
#segBlock{flex:1;min-height:120px}
#confBlock{flex:0 0 auto;height:26%;min-height:84px}
.splitter{height:7px;cursor:ns-resize;flex-shrink:0;background:#1e3a5f;position:relative}
.splitter:hover,.splitter.dragging{background:#1B9AAA}
.splitter::after{content:'';position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:28px;height:2px;background:rgba(255,255,255,.3);border-radius:2px}
.sb-h{padding:11px 16px;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#8aa6c8;font-weight:700;border-bottom:1px solid #1e3a5f;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
.sb-h a{color:#1B9AAA;font-size:10px;cursor:pointer;text-decoration:none}
.gf-list{flex:1;overflow-y:auto;padding:8px}
.gf-item{display:flex;align-items:center;gap:9px;padding:6px 8px;border-radius:6px;cursor:pointer;font-size:12.5px;color:#cfe0f0}
.gf-item:hover{background:rgba(255,255,255,.05)}
.gf-sw{width:14px;height:14px;border-radius:3px;flex-shrink:0;border:1.5px solid rgba(255,255,255,.3)}
.gf-n{margin-left:auto;font-size:11px;color:#8aa6c8;font-variant-numeric:tabular-nums}
.gf-item.off{opacity:.32}
.basemap{position:absolute;top:64px;right:12px;z-index:800;display:flex;background:rgba(16,28,48,.95);border-radius:7px;padding:3px;border:1px solid #1e3a5f}
.basemap button{background:transparent;border:none;color:#8aa6c8;font-family:inherit;font-size:11px;font-weight:600;padding:6px 10px;border-radius:5px;cursor:pointer}
.basemap button.active{background:#0077B6;color:#fff}
.foot{position:absolute;bottom:0;left:240px;z-index:700;font-size:10px;color:#5a7494;background:rgba(10,18,32,.8);padding:4px 12px;border-top-right-radius:6px}
.loading{position:absolute;inset:56px 0 0 240px;z-index:600;display:flex;align-items:center;justify-content:center;color:#8aa6c8;font-size:14px;background:#0a1220}
.leaflet-popup-content-wrapper{background:#fff;border-radius:10px}
.leaflet-popup-content{margin:14px 16px}
@media(max-width:768px){.sidebar{width:168px}.foot{left:168px}.loading{left:168px}.stats{display:none}}
</style></head><body>
<div class="topbar">
<div class="logo">A2<span>L</span></div>
<div class="proj">__PROJETO__</div>
<div class="city-sel"><label>Cidade</label><select id="citySel">__OPCOES__</select></div>
<div class="cmctl"><button id="cmConf" class="active" onclick="setColorMode('conf')">Cor: Confianca</button><button id="cmSeg" onclick="setColorMode('seg')">Segmento</button></div>
<div class="stats">
<div class="stat"><b id="stPts">-</b><span>POIs</span></div>
<div class="stat"><b id="stSeg">-</b><span>Segmentos</span></div>
</div></div>
<div class="sidebar">
<div class="sb-block" id="segBlock">
<div class="sb-h"><span>Segmentos</span><a onclick="toggleAllSeg()">todos</a></div>
<div class="gf-list" id="segList"></div>
</div>
<div class="splitter" id="splitter"></div>
<div class="sb-block" id="confBlock">
<div class="sb-h"><span>Nivel de Confianca</span><a onclick="toggleAllConf()">todos</a></div>
<div class="gf-list" id="confList"></div>
</div>
</div>
<div class="basemap">
<button onclick="setBase('sat')" class="active" id="bSat">SAT</button>
<button onclick="setBase('hib')" id="bHib">HIB</button>
<button onclick="setBase('ruas')" id="bRuas">RUAS</button>
</div>
<div id="map"></div>
<div class="loading" id="loading">Descomprimindo dados...</div>
<div class="foot">Elaborado por A2L Engenharia e Consultoria &middot; POIs multifonte: Overture (CDLA) &middot; OpenStreetMap (ODbL) &middot; Foursquare (Apache-2.0) &middot; requer internet (Leaflet + tiles)</div>
<script>
// --- Escape obrigatorio: todo texto abaixo vem de fonte aberta e e editavel por terceiros.
function esc(v){if(v===null||v===undefined)return '';return String(v).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function safeUrl(u){if(!u)return '';var s=String(u).trim();if(/^https?:\/\//i.test(s))return s;if(/^[a-z][a-z0-9+.-]*:/i.test(s))return '';return 'https://'+s;}
function telHref(t){return String(t||'').replace(/[^0-9+]/g,'');}

const COLORS=['#E6194B','#3CB44B','#4363D8','#F58231','#911EB4','#42D4F4','#F032E6','#1B9AAA','#BFEF45','#C9963B','#469990','#9A6324','#800000','#0077B6','#A9A9A9','#FABED4','#808000','#000075'];
const CONFCOL=['#2e7d54','#C9963B','#b04632','#6b7280'];
function segColor(i){return COLORS[((i<0?0:i))%COLORS.length];}
function confColor(i){return CONFCOL[((i<0?3:i))%CONFCOL.length];}

const map=L.map('map',{preferCanvas:true,zoomControl:true}).setView([__LAT0__,__LON0__],7);
const bases={
 sat:L.tileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',{maxZoom:21}),
 hib:L.tileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',{maxZoom:21}),
 ruas:L.tileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',{maxZoom:21})
};
bases.sat.addTo(map);
let curBase='sat';
function setBase(b){bases[curBase].remove();bases[b].addTo(map);curBase=b;['Sat','Hib','Ruas'].forEach(x=>document.getElementById('b'+x).classList.toggle('active',x.toLowerCase()===b));}

let DATA=null, curCity=null, colorMode='conf';
let ptLayer=L.layerGroup().addTo(map);
let activeSeg=new Set(), activeConf=new Set();

const B64="__PAYLOAD__";
const bytes=Uint8Array.from(atob(B64),c=>c.charCodeAt(0));
new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).text().then(txt=>{
  DATA=JSON.parse(txt);
  document.getElementById('loading').style.display='none';
  loadCity(document.getElementById('citySel').value);
});
document.getElementById('citySel').addEventListener('change',e=>loadCity(e.target.value));

function loadCity(city){
  curCity=city; const cd=DATA.cidades[city]; if(!cd)return;
  activeSeg=new Set(cd.segs); activeConf=new Set(cd.confs);
  renderPoints(); buildLists();
  const b=cd.base, pts=cd.pts;
  let mLa=9e9,xLa=-9e9,mLo=9e9,xLo=-9e9;
  for(const p of pts){const la=b[0]+p[0]/1e6,lo=b[1]+p[1]/1e6;if(la<mLa)mLa=la;if(la>xLa)xLa=la;if(lo<mLo)mLo=lo;if(lo>xLo)xLo=lo;}
  map.fitBounds([[mLa,mLo],[xLa,xLo]],{padding:[40,40]});
  document.getElementById('stPts').textContent=pts.length.toLocaleString('pt-BR');
  document.getElementById('stSeg').textContent=cd.segs.length;
}
function renderPoints(){
  ptLayer.clearLayers();
  const cd=DATA.cidades[curCity], b=cd.base;
  for(let i=0;i<cd.pts.length;i++){
    const p=cd.pts[i], seg=p[2], cf=p[3];
    if(!activeSeg.has(seg))continue;
    if(!activeConf.has(cf))continue;
    const la=b[0]+p[0]/1e6, lo=b[1]+p[1]/1e6;
    const col=colorMode==='conf'?confColor(cf):segColor(seg);
    const m=L.circleMarker([la,lo],{radius:4,fillColor:col,fillOpacity:.85,stroke:false,interactive:true,bubblingMouseEvents:false});
    m._idx=i; m.on('click',onPointClick); m.addTo(ptLayer);
  }
}
function row(lbl,val){return val&&val!==''?'<div style="display:flex;justify-content:space-between;gap:10px"><span style="color:#64748b;flex-shrink:0">'+esc(lbl)+'</span><b style="color:#1B2A4A;text-align:right;word-break:break-word">'+esc(val)+'</b></div>':'';}
function onPointClick(e){
  const m=e.target, i=m._idx, cd=DATA.cidades[curCity];
  const p=cd.pts[i], inf=cd.info[i], b=cd.base;
  const la=(b[0]+p[0]/1e6), lo=(b[1]+p[1]/1e6);
  const nome=inf[0]||'(sem nome)', cat=DATA.cat[inf[1]]||'-';
  const end=inf[2]||'', tel=inf[3]||'', site=inf[4]||'', data=inf[5]||'';
  const seg=DATA.segNames[p[2]]||'-', conf=DATA.confNames[p[3]]||'-';
  const segc=segColor(p[2]), cfc=confColor(p[3]);
  const siteHref=safeUrl(site);
  const sv='https://www.google.com/maps/@?api=1&map_action=pano&viewpoint='+la+','+lo;
  const html='<div style="font-family:Inter,Segoe UI,sans-serif;min-width:210px;max-width:280px">'+
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #e2e8f0">'+
      '<span style="width:11px;height:11px;border-radius:50%;background:'+cfc+';flex-shrink:0"></span>'+
      '<span style="font-family:Georgia,serif;font-size:15px;font-weight:700;color:#1B2A4A;line-height:1.15">'+esc(nome)+'</span></div>'+
    '<div style="display:flex;flex-direction:column;gap:5px;font-size:12.5px">'+
      '<div style="display:flex;justify-content:space-between;align-items:center"><span style="color:#64748b">Segmento</span><span style="background:'+segc+'18;color:'+segc+';padding:1px 8px;border-radius:10px;font-weight:700;font-size:11px">'+esc(seg)+'</span></div>'+
      row('Categoria',cat)+
      row('Endereco',end)+
      (tel?'<div style="display:flex;justify-content:space-between"><span style="color:#64748b">Telefone</span><a href="tel:'+esc(telHref(tel))+'" style="color:#0077B6;font-weight:700;text-decoration:none">'+esc(tel)+'</a></div>':'')+
      (siteHref?'<div style="display:flex;justify-content:space-between;gap:10px"><span style="color:#64748b">Site</span><a href="'+esc(siteHref)+'" target="_blank" rel="noopener noreferrer" style="color:#0077B6;font-weight:600;text-decoration:none;word-break:break-all;text-align:right">'+esc(site)+'</a></div>':'')+
      '<div style="display:flex;justify-content:space-between;align-items:center"><span style="color:#64748b">Confianca</span><span style="background:'+cfc+'18;color:'+cfc+';padding:1px 8px;border-radius:10px;font-weight:700;font-size:11px">'+esc(conf)+'</span></div>'+
      row('Atualizacao',data)+
    '</div>'+
    '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #e2e8f0;font-size:11px;color:#94a3b8;font-variant-numeric:tabular-nums">'+la.toFixed(6)+', '+lo.toFixed(6)+'</div>'+
    '<a href="'+sv+'" target="_blank" rel="noopener noreferrer" style="display:block;margin-top:6px;text-align:center;background:#0077B6;color:#fff;padding:6px;border-radius:6px;font-size:12px;font-weight:600;text-decoration:none">Abrir Street View</a>'+
    '</div>';
  L.popup({maxWidth:300}).setLatLng([la,lo]).setContent(html).openOn(map);
}
function counts(){
  const cd=DATA.cidades[curCity]; const sC={},cC={};
  for(const p of cd.pts){sC[p[2]]=(sC[p[2]]||0)+1;cC[p[3]]=(cC[p[3]]||0)+1;}
  return {sC,cC};
}
function buildLists(){
  const cd=DATA.cidades[curCity]; const {sC,cC}=counts();
  const sel=document.getElementById('segList'); sel.innerHTML='';
  cd.segs.slice().sort((a,b)=>(sC[b]||0)-(sC[a]||0)).forEach(s=>{
    const on=activeSeg.has(s); const d=document.createElement('div');
    d.className='gf-item'+(on?'':' off');
    d.innerHTML='<span class="gf-sw" style="background:'+segColor(s)+'"></span>'+esc(DATA.segNames[s]||'-')+'<span class="gf-n">'+(sC[s]||0).toLocaleString('pt-BR')+'</span>';
    d.onclick=()=>{on?activeSeg.delete(s):activeSeg.add(s);renderPoints();buildLists();};
    sel.appendChild(d);
  });
  const col=document.getElementById('confList'); col.innerHTML='';
  cd.confs.slice().sort((a,b)=>a-b).forEach(c=>{
    const on=activeConf.has(c); const d=document.createElement('div');
    d.className='gf-item'+(on?'':' off');
    d.innerHTML='<span class="gf-sw" style="background:'+confColor(c)+'"></span>'+esc(DATA.confNames[c]||'-')+'<span class="gf-n">'+(cC[c]||0).toLocaleString('pt-BR')+'</span>';
    d.onclick=()=>{on?activeConf.delete(c):activeConf.add(c);renderPoints();buildLists();};
    col.appendChild(d);
  });
}
function toggleAllSeg(){const cd=DATA.cidades[curCity];if(activeSeg.size===cd.segs.length)activeSeg.clear();else activeSeg=new Set(cd.segs);renderPoints();buildLists();}
function toggleAllConf(){const cd=DATA.cidades[curCity];if(activeConf.size===cd.confs.length)activeConf.clear();else activeConf=new Set(cd.confs);renderPoints();buildLists();}
function setColorMode(m){colorMode=m;document.getElementById('cmConf').classList.toggle('active',m==='conf');document.getElementById('cmSeg').classList.toggle('active',m==='seg');renderPoints();buildLists();}
(function(){const sp=document.getElementById('splitter'),gb=document.getElementById('segBlock');if(!sp||!gb)return;let drag=false;
 function move(y){const sb=gb.parentElement.getBoundingClientRect();let h=y-sb.top;h=Math.max(120,Math.min(h,sb.height-100));gb.style.height=h+'px';gb.style.flex='0 0 auto';}
 sp.addEventListener('mousedown',e=>{drag=true;sp.classList.add('dragging');e.preventDefault();});
 window.addEventListener('mousemove',e=>{if(drag)move(e.clientY);});
 window.addEventListener('mouseup',()=>{drag=false;sp.classList.remove('dragging');});})();
</script></body></html>'''


def _s(v):
    return v if isinstance(v, str) and v.strip() not in ("", "nan", "None", "NaN") else ""


def construir(df):
    """Monta o payload comprimido. Separado de `executar` para poder testar sozinho."""
    df = df.copy()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon", "NOME_MUNICIPIO"])
    segs = sorted(x for x in df["segmento"].dropna().unique())
    cats = sorted(x for x in df["categoria_pt"].dropna().unique())
    seg_i = {v: i for i, v in enumerate(segs)}
    cat_i = {v: i for i, v in enumerate(cats)}
    conf_nomes = ["alta", "média", "baixa", "sem"]
    conf_i = {"alta": 0, "média": 1, "media": 1, "baixa": 2, "sem": 3}
    cidades = {}
    for cid, grp in df.groupby("NOME_MUNICIPIO"):
        la0 = float(grp["lat"].min())
        lo0 = float(grp["lon"].min())
        pts, info = [], []
        for r in grp.itertuples(index=False):
            pts.append([int(round((r.lat - la0) * 1e6)), int(round((r.lon - lo0) * 1e6)),
                        seg_i.get(r.segmento, -1), conf_i.get(str(r.confianca_classe), 3)])
            info.append([_s(r.nome), cat_i.get(r.categoria_pt, -1), _s(r.endereco_completo),
                         _s(r.telefone), _s(r.site), _s(r.data_atualizacao)[:10]])
        cidades[str(cid)] = {"pts": pts, "info": info, "base": [round(la0, 6), round(lo0, 6)],
                             "segs": sorted(set(p[2] for p in pts if p[2] >= 0)),
                             "confs": sorted(set(p[3] for p in pts))}
    payload = {"cidades": cidades, "segNames": segs, "confNames": conf_nomes, "cat": cats}
    bruto = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return df, base64.b64encode(gzip.compress(bruto.encode("utf-8"))).decode()


def executar(cfg, man, df):
    man.iniciar("map")
    t0 = time.time()
    df, comp = construir(df)
    vol = df.groupby("NOME_MUNICIPIO").size().sort_values(ascending=False)
    topo = list(vol.index[:12])
    ordem = topo + sorted(c for c in vol.index if c not in topo)
    opcoes = "\n".join(
        '<option value="%s">%s</option>' % (html.escape(str(c), quote=True),
                                            html.escape(str(c).title(), quote=True))
        for c in ordem)
    projeto = "%s &middot; PONTOS DE INTERESSE" % html.escape(cfg.uf)
    out = cfg.arq_saida("Mapa_POI_%s.html" % cfg.rotulo.lower())
    conteudo = (TEMPLATE
                .replace("__UF__", html.escape(cfg.uf))
                .replace("__PROJETO__", projeto)
                .replace("__OPCOES__", opcoes)
                .replace("__LAT0__", "%.4f" % df["lat"].median())
                .replace("__LON0__", "%.4f" % df["lon"].median())
                .replace("__PAYLOAD__", comp))
    with open(out + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(conteudo)
    os.replace(out + ".tmp", out)
    man.concluir("map", cidades=len(ordem), pontos=len(df), bytes=len(conteudo))
    print("MAPA: %.1f MB | %d cidades | %d POIs | %.0fs -> %s"
          % (len(conteudo) / 1048576, len(ordem), len(df), time.time() - t0, out))
    return out
