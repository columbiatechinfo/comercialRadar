import { chromium, Browser, BrowserContext, Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';
import { CaptureConfig, TileCoord, CaptureSession } from './types';
import { calculateGrid } from './geo';

const WORKERS               = Number(process.env.CAPTURE_WORKERS || 10);
// A chave vem SÓ do ambiente (.env → MAPS_JS_KEY ou MAPS_API_KEY). Esta é a Maps
// JavaScript API, não a Places. Havia um literal aqui como fallback, e ele saiu:
// o arquivo é versionado, então cada commit reexpunha a chave. A que estava no
// código continua no histórico (commit 1c7f081) e PRECISA ser girada — trocar
// aqui não desfaz o que já foi publicado.
const MAPS_API_KEY          = process.env.MAPS_JS_KEY || process.env.MAPS_API_KEY || '';
const MAPS_MAP_ID           = '33696f50cbe8e2d228094f61'; // estilo vetorial clean (só POIs)
const TILE_WAIT_MS          = 6000;  // espera extra para labels/POIs depois do carregamento
const EXTRA_WAIT_MS         = 2500;  // colchão adicional antes do screenshot
const TILE_LOAD_TIMEOUT_MS  = 12000; // tempo máximo aguardando estabilidade visual
const MAP_READY_TIMEOUT_MS  = 40000; // tempo máximo para o mapa inicializar

// HTML da página Maps JS — carrega UMA vez, navega por setCenter()
function buildMapHtml(apiKey: string, mapId: string, zoom: number): string {
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; }
  html, body, #map { width: 100vw; height: 100vh; overflow: hidden; }
</style>
</head>
<body>
<div id="map"></div>
<script>
let map;
let ready = false;

function initMap() {
  map = new google.maps.Map(document.getElementById('map'), {
    center: { lat: -29.7, lng: -53.8 },
    zoom: ${zoom},
    disableDefaultUI: true,
    gestureHandling: 'none',
    keyboardShortcuts: false,
    mapTypeId: 'roadmap',
    mapId: '${mapId}',  // estilo vetorial clean
  });

  google.maps.event.addListenerOnce(map, 'idle', () => {
    // Não marca como pronto imediatamente; dá um pequeno tempo
    setTimeout(() => { ready = true; }, 1200);
  });
}

// Navega para lat/lng e retorna quando o mapa parar de carregar
window.goTo = function(lat, lng) {
  return new Promise((resolve) => {
    map.setCenter({ lat, lng });

    google.maps.event.addListenerOnce(map, 'idle', () => {
      // Colchão maior para o renderer vetorial e labels aparecerem
      setTimeout(() => resolve(true), 1800);
    });
  });
};

// Verifica se o mapa está visualmente carregado.
// Não retorna true cedo demais quando ainda não existem elementos relevantes.
window.tilesLoaded = function() {
  const mapEl = document.getElementById('map');
  if (!mapEl) return false;

  const imgs = Array.from(mapEl.querySelectorAll('img'));
  const relevant = imgs.filter((img) => {
    const src = img.getAttribute('src') || '';
    return (
      src.includes('googleapis.com') ||
      src.includes('gstatic.com') ||
      src.includes('googleusercontent.com')
    );
  });

  if (relevant.length === 0) {
    // Em mapa vetorial puro, pode não haver img suficiente.
    // Então verifica se o container já tem conteúdo desenhado.
    const hasCanvas = mapEl.querySelectorAll('canvas').length > 0;
    const hasVectorNodes = mapEl.querySelectorAll('[aria-label], [role="img"], .gm-style').length > 0;
    return hasCanvas && hasVectorNodes;
  }

  for (const img of relevant) {
    if (!img.complete) return false;
    if ((img.naturalWidth || 0) === 0) return false;
  }

  return true;
};

window.isReady = function() { return ready; };
</script>
<script src="https://maps.googleapis.com/maps/api/js?key=${apiKey}&callback=initMap&loading=async" async defer></script>
</body>
</html>`;
}

function humanDelay(baseMs: number): number {
  const jitter = baseMs * 0.25;
  return Math.round(baseMs + (Math.random() * jitter * 2 - jitter));
}

async function waitMapReady(page: Page, timeout = MAP_READY_TIMEOUT_MS): Promise<boolean> {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const ready = await page
      .evaluate(() => {
        const w = globalThis as any;
        return w.isReady?.() ?? false;
      })
      .catch(() => false);

    if (ready) return true;
    await page.waitForTimeout(300);
  }
  return false;
}

async function navigateAndCapture(
  page: Page,
  tile: TileCoord,
  outputDir: string,
): Promise<'ok' | 'error'> {
  try {
    // Move o mapa para a coordenada — sem recarregar a página
    const moved = await page.evaluate(
      ({ lat, lng }: { lat: number; lng: number }) => {
        const w = globalThis as any;
        return w.goTo(lat, lng);
      },
      { lat: tile.lat, lng: tile.lng }
    );

    if (!moved) return 'error';

    // Aguarda estabilidade real do mapa, não só o primeiro idle
    const t0 = Date.now();
    let stableCount = 0;

    while (Date.now() - t0 < TILE_LOAD_TIMEOUT_MS) {
      const loaded = await page
        .evaluate(() => {
          const w = globalThis as any;
          return w.tilesLoaded?.() ?? false;
        })
        .catch(() => false);

      if (loaded) {
        stableCount++;
        if (stableCount >= 4) break;
      } else {
        stableCount = 0;
      }

      await page.waitForTimeout(500);
    }

    // Delay final mais generoso para POIs e labels renderizarem
    await page.waitForTimeout(humanDelay(TILE_WAIT_MS + EXTRA_WAIT_MS));

    const filepath = path.join(outputDir, tile.filename);
    await page.screenshot({ path: filepath, type: 'png' });
    return 'ok';
  } catch (err: any) {
    return 'error';
  }
}

async function runWorker(
  workerId: number,
  queue: TileCoord[],
  queueIndex: { value: number },
  session: CaptureSession,
  sessionFile: string,
  lock: { writing: boolean },
  config: CaptureConfig,
  stats: { done: number; failed: number; t0: number },
  htmlPath: string,
): Promise<void> {
  // Mantém 10 workers, mas abre de forma escalonada para reduzir estouro no início
  await new Promise(r => setTimeout(r, workerId * 1500));

  const browser: Browser = await chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      `--window-size=${config.viewportWidth},${config.viewportHeight}`,
    ],
  });

  const ctx: BrowserContext = await browser.newContext({
    viewport: { width: config.viewportWidth, height: config.viewportHeight },
    deviceScaleFactor: 1,
    locale: 'pt-BR',
  });

  const page: Page = await ctx.newPage();

  // Abre a página HTML local com Maps JS — ÚNICA vez durante toda a sessão
  await page.goto(`file://${htmlPath}`, { waitUntil: 'domcontentloaded', timeout: 30000 });

  // Aguarda o mapa inicializar
  const ready = await waitMapReady(page, MAP_READY_TIMEOUT_MS);
  if (!ready) {
    console.log(`\n  [W${workerId}] ⚠️ Mapa não inicializou — encerrando worker`);
    await browser.close();
    return;
  }

  console.log(`\n  [W${workerId}] ✅ Mapa pronto`);

  while (true) {
    const myIndex = queueIndex.value++;
    if (myIndex >= queue.length) break;

    const tile = queue[myIndex];
    const result = await navigateAndCapture(page, tile, config.outputDir);

    if (result === 'ok') {
      stats.done++;
      session.completedTiles++;
    } else {
      stats.failed++;
      session.failedTiles.push(tile);
    }

    // Log de progresso
    const total = queue.length;
    const done = stats.done + stats.failed;
    const pct = ((done / total) * 100).toFixed(1);
    const elapsed = (Date.now() - stats.t0) / 1000;
    const rate = done > 0 ? elapsed / done : 2;
    const remaining = Math.round((total - done) * rate / 60);
    process.stdout.write(
      `\r📸 [${String(done).padStart(6)}/${total}] ${pct}% | ✔${stats.done} ✗${stats.failed} | ~${remaining}min   `
    );

    if (done % 50 === 0 && !lock.writing) {
      lock.writing = true;
      fs.writeFileSync(sessionFile, JSON.stringify(session, null, 2));
      lock.writing = false;
    }
  }

  await ctx.close();
  await browser.close();
}

type LatLng = { lat: number; lng: number };

function pointInPolygon(lat: number, lng: number, poly: LatLng[]): boolean {
  let dentro = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const yi = poly[i].lat, xi = poly[i].lng;
    const yj = poly[j].lat, xj = poly[j].lng;
    if ((yi > lat) !== (yj > lat) &&
        lng < ((xj - xi) * (lat - yi)) / ((yj - yi) || 1e-12) + xi) {
      dentro = !dentro;
    }
  }
  return dentro;
}

// O tile é um retângulo, não um ponto: basta ele ENCOSTAR na área para valer a
// captura. Testa o centro, os quatro cantos e os vértices do polígono que caem
// dentro do tile. Escapa só o sliver que atravessa o tile sem vértice nem canto
// dentro — geometria que uma área desenhada à mão não produz.
function tileNaArea(lat: number, lng: number, dLat: number, dLng: number,
                    poly: LatLng[]): boolean {
  if (pointInPolygon(lat, lng, poly)) return true;
  const h = dLat / 2, w = dLng / 2;
  for (const [sy, sx] of [[-1, -1], [-1, 1], [1, -1], [1, 1]]) {
    if (pointInPolygon(lat + sy * h, lng + sx * w, poly)) return true;
  }
  return poly.some(p => Math.abs(p.lat - lat) <= h && Math.abs(p.lng - lng) <= w);
}

export function generateTiles(config: CaptureConfig): TileCoord[] {
  const { boundingBox, zoomLevel, tileOverlapPercent, polygon } = config;
  const { rows, cols, latStep, lngStep } = calculateGrid(boundingBox, zoomLevel, tileOverlapPercent);
  const tiles: TileCoord[] = [];

  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < cols; col++) {
      const actualCol = row % 2 === 0 ? col : cols - 1 - col;
      let lat = boundingBox.north - row * latStep - latStep / 2;
      let lng = boundingBox.west + actualCol * lngStep + lngStep / 2;
      lat = Math.max(boundingBox.south, Math.min(boundingBox.north, lat));
      lng = Math.max(boundingBox.west, Math.min(boundingBox.east, lng));
      if (polygon && polygon.length >= 3 &&
          !tileNaArea(lat, lng, latStep, lngStep, polygon)) {
        continue;
      }
      tiles.push({
        row, col: actualCol,
        lat: parseFloat(lat.toFixed(7)),
        lng: parseFloat(lng.toFixed(7)),
        filename: `tile_r${String(row).padStart(3,'0')}_c${String(actualCol).padStart(3,'0')}_${lat.toFixed(5)}_${lng.toFixed(5)}.png`,
      });
    }
  }
  return tiles;
}

export async function runCaptureSession(config: CaptureConfig): Promise<CaptureSession> {
  const sessionFile = path.join(config.outputDir, 'session.json');

  // Gera tiles — pula os já capturados
  const allTiles = generateTiles(config);
  const existing = new Set(
    fs.existsSync(config.outputDir)
      ? fs.readdirSync(config.outputDir).filter(f => f.endsWith('.png'))
      : []
  );
  const tiles = allTiles.filter(t => !existing.has(t.filename));

  const session: CaptureSession = {
    sessionName: config.sessionName,
    startedAt: new Date().toISOString(),
    config,
    totalTiles: allTiles.length,
    completedTiles: existing.size,
    failedTiles: [],
    status: 'running',
  };

  // PARA AQUI se não houver chave. Sem ela o mapa carrega cinza e a captura
  // termina "com sucesso" gerando centenas de PNGs vazios — o OCR não acha nada
  // e o erro só aparece horas depois, como "0 POIs". É o mesmo silêncio que a
  // Places API produzia antes de o servidor passar a exigir MAPS_API_KEY.
  if (!MAPS_API_KEY) {
    throw new Error(
      'Sem chave da Maps JavaScript API. Ponha MAPS_JS_KEY (ou MAPS_API_KEY) ' +
      'no .env — sem ela o mapa carrega em branco e a captura gera imagens vazias.');
  }

  fs.mkdirSync(config.outputDir, { recursive: true });
  fs.writeFileSync(sessionFile, JSON.stringify(session, null, 2));

  // Gera página HTML temporária com a chave da API
  const htmlPath = path.join(config.outputDir, '_map.html');
  fs.writeFileSync(htmlPath, buildMapHtml(MAPS_API_KEY, MAPS_MAP_ID, config.zoomLevel));

  const workers = WORKERS;
  const secsPerTile = (TILE_WAIT_MS + EXTRA_WAIT_MS + 500) / 1000;
  const estMin = Math.ceil(tiles.length * secsPerTile / workers / 60);

  console.log(`\n🚀 Iniciando captura: ${config.sessionName}`);
  console.log(`📦 Tiles restantes  : ${tiles.length} de ${allTiles.length}`);
  console.log(`🗺  Modo            : Maps JavaScript API (sem proxy, sem CAPTCHA)`);
  console.log(`🔌 Workers          : ${workers} browsers paralelos`);
  console.log(`⏱  Estimativa       : ~${estMin} min\n`);

  if (tiles.length === 0) {
    console.log('✅ Todos os tiles já foram capturados!');
    session.status = 'completed';
    fs.writeFileSync(sessionFile, JSON.stringify(session, null, 2));
    return session;
  }

  const queueIndex = { value: 0 };
  const lock = { writing: false };
  const stats = { done: existing.size, failed: 0, t0: Date.now() };

  await Promise.all(
    Array.from({ length: workers }, (_, i) =>
      runWorker(i, tiles, queueIndex, session, sessionFile, lock, config, stats, htmlPath)
    )
  );

  // Remove HTML temporário
  try { fs.unlinkSync(htmlPath); } catch {}

  session.status = 'completed';
  fs.writeFileSync(sessionFile, JSON.stringify(session, null, 2));

  const elapsed = Math.round((Date.now() - stats.t0) / 60000);
  console.log(`\n\n✅ Captura concluída em ${elapsed} min!`);
  console.log(`   ✔ Sucesso : ${session.completedTiles}/${session.totalTiles}`);
  console.log(`   ✗ Falhos  : ${session.failedTiles.length}`);

  return session;
}
