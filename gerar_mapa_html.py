"""
gerar_mapa_html.py — Mapa HTML consolidado a partir do banco (PostgreSQL)

Gera um HTML autossuficiente com:
  - Mapa OpenStreetMap (Leaflet) + agrupamento de marcadores (markercluster)
  - Um marcador por POI do banco
  - Ao clicar: modal com TODOS os dados coletados (Google) + dados iniciais
    fornecidos (planilha: nome/endereço originais) + OCR (pipeline) + fotos + avaliações

USO:
  py gerar_mapa_html.py [--fonte planilha|pipeline|todos] [--out mapa_pois.html]
"""

import json
import argparse
import html
from pathlib import Path
from collections import defaultdict

import config


def _conn():
    """A MESMA conexão do resto do sistema.

    Este arquivo lia o `.env` por conta própria e montava a conexão com as
    variáveis `POSTGRES_*`, que apontavam para o Postgres do notebook. Quando o
    banco local foi aposentado (13/08/2026) ele passou a apontar para um banco
    que não existe mais — e o sintoma seria um erro de conexão num script de
    geração de mapa, longe da causa. Conexão duplicada é assim: ela não acompanha
    a mudança porque ninguém lembra que ela existe.
    """
    import realtime_ingest
    return realtime_ingest.conectar()


def carregar_pois(fonte: str) -> list:
    c = _conn()
    cur = c.cursor()
    where = "" if fonte == "todos" else "WHERE fonte = %s"
    params = () if fonte == "todos" else (fonte,)

    cur.execute(f"""
        SELECT id, fonte, nome, categoria, endereco, telefone, website, avaliacao,
               total_avaliacoes, plus_code, status_horario, lat_origem, lng_origem,
               maps_lat, maps_lng, maps_url, place_id, status, distancia_m,
               similaridade, ocr_texto, nome_original, endereco_original, sessao
        FROM pois {where}
    """, params)
    cols = [d[0] for d in cur.description]
    pois = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}

    # Derivadas
    cur.execute("SELECT poi_id, url FROM images_urls ORDER BY poi_id, ordem NULLS LAST, id")
    fotos = defaultdict(list)
    for pid, url in cur.fetchall():
        fotos[pid].append(url)

    cur.execute("SELECT poi_id, autor, nota, texto, data FROM comentarios ORDER BY poi_id, id")
    coment = defaultdict(list)
    for pid, autor, nota, texto, data in cur.fetchall():
        coment[pid].append({"autor": autor, "nota": nota, "texto": texto, "data": data})

    cur.execute("SELECT poi_id, dia, horario FROM horario_funcionamento ORDER BY poi_id, id")
    horarios = defaultdict(list)
    for pid, dia, hora in cur.fetchall():
        horarios[pid].append({"dia": dia, "horario": hora})

    c.close()

    out = []
    for pid, p in pois.items():
        lat = p["maps_lat"] if p["maps_lat"] is not None else p["lat_origem"]
        lng = p["maps_lng"] if p["maps_lng"] is not None else p["lng_origem"]
        if lat is None or lng is None:
            continue
        out.append({
            "id": pid, "fonte": p["fonte"], "nome": p["nome"], "categoria": p["categoria"],
            "endereco": p["endereco"], "telefone": p["telefone"], "website": p["website"],
            "avaliacao": p["avaliacao"], "total_avaliacoes": p["total_avaliacoes"],
            "plus_code": p["plus_code"], "status_horario": p["status_horario"],
            "lat": lat, "lng": lng,
            "lat_origem": p["lat_origem"], "lng_origem": p["lng_origem"],
            "maps_url": p["maps_url"], "place_id": p["place_id"], "status": p["status"],
            "distancia_m": p["distancia_m"], "similaridade": p["similaridade"],
            "ocr_texto": p["ocr_texto"], "nome_original": p["nome_original"],
            "endereco_original": p["endereco_original"], "sessao": p["sessao"],
            "fotos": fotos.get(pid, []), "comentarios": coment.get(pid, []),
            "horarios": horarios.get(pid, []),
        })
    return out


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ComercialRadar — Mapa de POIs</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css"/>
<style>
  :root{--bg:#0f1420;--card:#1a2130;--ink:#e8ecf3;--mut:#93a0b5;--acc:#4fc3f7;--ok:#38d39f;--warn:#ffb64c}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
  #topbar{position:fixed;z-index:1000;top:0;left:0;right:0;height:52px;background:#131a27;display:flex;align-items:center;
          gap:14px;padding:0 16px;border-bottom:1px solid #232c3d}
  #topbar h1{font-size:16px;margin:0;color:#fff}
  #topbar .stat{font-size:13px;color:var(--mut)}
  #topbar input{margin-left:auto;background:#0f1420;border:1px solid #2a3547;color:var(--ink);
                padding:7px 12px;border-radius:8px;width:280px;font-size:13px}
  #map{position:absolute;top:52px;bottom:0;left:0;right:0}
  #modal{position:fixed;z-index:2000;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;padding:20px}
  #modal.on{display:flex}
  .box{background:var(--card);border:1px solid #2a3547;border-radius:14px;max-width:760px;width:100%;max-height:88vh;
       overflow:auto;box-shadow:0 20px 60px rgba(0,0,0,.5)}
  .box header{position:sticky;top:0;background:var(--card);padding:16px 20px;border-bottom:1px solid #2a3547;display:flex;
              align-items:flex-start;gap:10px}
  .box header h2{margin:0;font-size:19px;color:#fff;flex:1}
  .box header .x{cursor:pointer;color:var(--mut);font-size:24px;line-height:1;border:none;background:none}
  .box .body{padding:16px 20px}
  .sec{margin-bottom:18px}
  .sec h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--acc)}
  .kv{display:grid;grid-template-columns:130px 1fr;gap:4px 10px;font-size:14px}
  .kv b{color:var(--mut);font-weight:500}
  .chip{display:inline-block;background:#0f1420;border:1px solid #2a3547;border-radius:20px;padding:3px 10px;font-size:12px;margin:2px 4px 2px 0}
  .fotos{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px}
  .fotos img{height:110px;border-radius:8px;flex:0 0 auto;border:1px solid #2a3547}
  .rev{border-left:3px solid #2a3547;padding:6px 0 6px 12px;margin-bottom:10px}
  .rev .top{font-size:13px;color:#fff}
  .rev .stars{color:var(--warn)}
  .rev .meta{font-size:11px;color:var(--mut)}
  .rev .txt{font-size:13px;color:var(--ink);margin-top:3px}
  .orig{background:#12281f;border:1px solid #1f4a38;border-radius:10px;padding:10px 12px}
  .orig h3{color:var(--ok)}
  a{color:var(--acc)}
  .badge{font-size:11px;padding:2px 8px;border-radius:6px;background:#0f1420;border:1px solid #2a3547;color:var(--mut)}
</style>
</head>
<body>
<div id="topbar">
  <h1>🗺️ ComercialRadar</h1>
  <span class="stat" id="stat"></span>
  <input id="busca" placeholder="Filtrar por nome..." autocomplete="off">
</div>
<div id="map"></div>
<div id="modal"><div class="box"><header><h2 id="m-nome"></h2><button class="x" onclick="fecha()">&times;</button></header><div class="body" id="m-body"></div></div></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js"></script>
<script>
const POIS = __DATA__;
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fotoURL = u => u && u.includes('=') ? u : (u ? u+'=w320-h240' : '');

const map = L.map('map',{preferCanvas:true}).setView([-2.905,-41.77], 13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
  maxZoom:19, attribution:'© OpenStreetMap'}).addTo(map);

const cluster = L.markerClusterGroup({chunkedLoading:true, maxClusterRadius:50});
const markers = [];
POIS.forEach(p=>{
  const cor = p.fonte==='pipeline' ? '#ffb64c' : '#4fc3f7';
  const m = L.circleMarker([p.lat,p.lng],{radius:6,color:'#0b0f16',weight:1,fillColor:cor,fillOpacity:.9});
  m.on('click',()=>abre(p));
  m.poi = p;
  markers.push(m); cluster.addLayer(m);
});
map.addLayer(cluster);
try{ map.fitBounds(cluster.getBounds().pad(0.05)); }catch(e){}
document.getElementById('stat').textContent = POIS.length + ' pontos';

function estrelas(n){ n=Math.round(n||0); return '★'.repeat(n)+'☆'.repeat(Math.max(0,5-n)); }

function abre(p){
  document.getElementById('m-nome').innerHTML = esc(p.nome) +
     ' <span class="badge">'+esc(p.fonte)+'</span>';
  let h = '';

  // Dados coletados (Google)
  h += '<div class="sec"><h3>Dados coletados (Google Maps)</h3><div class="kv">';
  const kv = [['Categoria',p.categoria],['Avaliação', p.avaliacao? (p.avaliacao+' ('+(p.total_avaliacoes||0)+' avaliações)'):''],
    ['Endereço',p.endereco],['Telefone',p.telefone],
    ['Website', p.website? '<a href="'+esc(p.website)+'" target="_blank">'+esc(p.website)+'</a>':''],
    ['Situação',p.status_horario],['Plus Code',p.plus_code]];
  kv.forEach(([k,v])=>{ if(v) h+='<b>'+k+'</b><span>'+(k==='Website'?v:esc(v))+'</span>'; });
  if(p.maps_url) h+='<b>Maps</b><span><a href="'+esc(p.maps_url)+'" target="_blank">abrir no Google Maps</a></span>';
  h += '</div></div>';

  // Horários
  if(p.horarios && p.horarios.length){
    h += '<div class="sec"><h3>Horário de funcionamento</h3><div class="kv">';
    p.horarios.forEach(x=> h+='<b>'+esc(x.dia)+'</b><span>'+esc(x.horario||'')+'</span>');
    h += '</div></div>';
  }

  // Fotos
  if(p.fotos && p.fotos.length){
    h += '<div class="sec"><h3>Fotos ('+p.fotos.length+')</h3><div class="fotos">';
    p.fotos.forEach(u=> h+='<a href="'+esc(fotoURL(u))+'" target="_blank"><img loading="lazy" src="'+esc(fotoURL(u))+'"></a>');
    h += '</div></div>';
  }

  // Avaliações
  if(p.comentarios && p.comentarios.length){
    h += '<div class="sec"><h3>Avaliações ('+p.comentarios.length+')</h3>';
    p.comentarios.forEach(c=>{
      h += '<div class="rev"><div class="top">'+esc(c.autor||'Anônimo')+
           ' <span class="stars">'+(c.nota?estrelas(c.nota):'')+'</span></div>'+
           '<div class="meta">'+esc(c.data||'')+'</div>'+
           (c.texto?'<div class="txt">'+esc(c.texto)+'</div>':'')+'</div>';
    });
    h += '</div>';
  }

  // Dados iniciais / OCR
  const temOrig = p.nome_original || p.endereco_original || p.ocr_texto;
  if(temOrig){
    h += '<div class="sec orig"><h3>Dados de origem (antes do processo)</h3><div class="kv">';
    if(p.nome_original) h+='<b>Nome fornecido</b><span>'+esc(p.nome_original)+'</span>';
    if(p.endereco_original) h+='<b>Endereço fornecido</b><span>'+esc(p.endereco_original)+'</span>';
    if(p.ocr_texto) h+='<b>OCR (leitura)</b><span>'+esc(p.ocr_texto)+'</span>';
    h+='</div></div>';
  }

  // Meta técnica
  h += '<div class="sec"><h3>Metadados</h3><div class="kv">';
  h += '<b>Coord. Maps</b><span>'+p.lat+', '+p.lng+'</span>';
  if(p.lat_origem) h+='<b>Coord. origem</b><span>'+p.lat_origem+', '+p.lng_origem+'</span>';
  if(p.distancia_m!=null) h+='<b>Distância</b><span>'+p.distancia_m+' m</span>';
  if(p.similaridade!=null) h+='<b>Similaridade</b><span>'+p.similaridade+'</span>';
  if(p.place_id) h+='<b>Place ID</b><span>'+esc(p.place_id)+'</span>';
  if(p.sessao) h+='<b>Sessão</b><span>'+esc(p.sessao)+'</span>';
  h += '</div></div>';

  document.getElementById('m-body').innerHTML = h;
  document.getElementById('modal').classList.add('on');
}
function fecha(){ document.getElementById('modal').classList.remove('on'); }
document.getElementById('modal').addEventListener('click',e=>{ if(e.target.id==='modal') fecha(); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape') fecha(); });

// Filtro por nome
document.getElementById('busca').addEventListener('input',function(){
  const q = this.value.toLowerCase().trim();
  cluster.clearLayers();
  const vis = markers.filter(m=> !q || (m.poi.nome||'').toLowerCase().includes(q) || (m.poi.nome_original||'').toLowerCase().includes(q));
  cluster.addLayers(vis);
  document.getElementById('stat').textContent = vis.length + ' / ' + POIS.length + ' pontos';
});
</script>
</body>
</html>
"""


def gerar(fonte: str = "todos", out: str = "mapa_pois.html") -> Path:
    """Gera o HTML do mapa a partir do banco. Reutilizável pelos fluxos de coleta."""
    pois = carregar_pois(fonte)
    tot_f = sum(len(p["fotos"]) for p in pois)
    tot_c = sum(len(p["comentarios"]) for p in pois)
    data = json.dumps(pois, ensure_ascii=False, default=str)
    html_out = HTML_TEMPLATE.replace("__DATA__", data)
    out_path = Path(out)
    out_path.write_text(html_out, encoding="utf-8")
    mb = out_path.stat().st_size / (1024 * 1024)
    print(f"🗺️  Mapa gerado: {out_path.resolve()}")
    print(f"   {len(pois)} pontos | {tot_f} fotos | {tot_c} avaliações | {mb:.1f} MB")
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fonte", choices=["planilha", "pipeline", "todos"], default="todos")
    parser.add_argument("--out", default="mapa_pois.html")
    args = parser.parse_args()
    gerar(args.fonte, args.out)


if __name__ == "__main__":
    main()
