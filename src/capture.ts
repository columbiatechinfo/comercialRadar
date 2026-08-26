import { chromium, Browser, BrowserContext, Page } from 'playwright';
import * as fs from 'fs';
import * as http from 'http';
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
// `mapa_pois` — sem nomes de rua, o mais limpo possível, só os markers de POI.
// É ele que faz o OCR ler estabelecimento em vez de rótulo de rua, e por isso
// NÃO é o mesmo mapa do painel (GOOGLE_MAP_ID no .env, que é o mapa de leitura
// humana e mostra ruas de propósito). Trocar um pelo outro não quebra nada e
// piora tudo em silêncio — já foi feito por engano em 13/08/2026.
//
// O console avisa "Attempted to load a Vector Map, but failed. Falling back to
// Raster" com este ID. É só aviso: a estilização da nuvem vale igual no raster,
// e é a estilização que importa aqui. Não troque o mapa por causa desse aviso.
const MAPS_MAP_ID           = process.env.CAPTURE_MAP_ID || '33696f50cbe8e2d228094f61';
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

// O Google chama esta funcao quando RECUSA a chave: referrer nao autorizado,
// chave invalida, API desativada ou faturamento ausente. Sem ela a recusa
// aparece so como um mapa que nunca fica pronto, e a captura salva a tela de
// erro como se fosse um bairro sem comercio. Quem le a bandeira e o
// waitMapReady, que interrompe a rodada.
window.gm_authFailure = function () { window.__authFailure = true; };

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

// Navega para lat/lng e retorna quando o mapa parar de carregar.
//
// DUAS TRAVAS FORAM CORRIGIDAS AQUI EM 13/08/2026, e as duas produziam o mesmo
// sintoma mudo: worker vivo, CPU perto de zero, nenhum tile em disco, nenhuma
// mensagem de erro. Uma captura de 6 quadras ficou 8 minutos sem escrever nada.
//
//  1. CORRIDA. O setCenter vinha ANTES de registrar o ouvinte. Se o mapa
//     ficasse ocioso nesse intervalo, o evento idle disparava sem ninguem
//     escutando e nunca mais voltava. Agora o ouvinte entra primeiro.
//  2. SEM PRAZO. A Promise so resolvia dentro do idle. Faltando o evento, ela
//     esperava para sempre — e o page.evaluate que a aguarda tambem nao tem
//     timeout, entao o worker parava de vez. Agora ha um teto: se o idle nao
//     vier em 8 s, seguimos assim mesmo. Tile borrado e prejuizo de um tile;
//     worker travado e prejuizo da rodada inteira.
//
//  ATENCAO ao editar daqui para baixo: este bloco vive DENTRO de um template
//  literal — repare na interpolacao de zoom e mapId logo acima. Duas coisas
//  quebram tudo aqui e nao parecem codigo: CRASE, que fecha a string, e cifrao
//  seguido de chave, que vira interpolacao em vez de texto. As duas foram
//  cometidas na primeira tentativa desta correcao.
window.goTo = function(lat, lng) {
  return new Promise((resolve) => {
    let respondido = false;
    const terminar = (viaIdle) => {
      if (respondido) return;
      respondido = true;
      resolve(viaIdle);
    };

    google.maps.event.addListenerOnce(map, 'idle', () => {
      // Colchao maior para o renderer vetorial e labels aparecerem
      setTimeout(() => terminar(true), 1800);
    });

    map.setCenter({ lat, lng });

    setTimeout(() => terminar(true), 8000);
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

/** Espera o mapa inicializar. Lanca se o Google RECUSOU a chave.
 *
 * A recusa precisa interromper, e nao virar mais um "nao ficou pronto". Em
 * 13/08/2026 uma rodada inteira terminou com "Total OCR: 0" numa area cheia de
 * comercio: a chave estava restrita por referrer, o mapa nunca carregou, e cada
 * tile salvo era um retrato da tela cinza "Ops! Algo deu errado". O relatorio
 * final dizia zero POIs — indistinguivel de uma area vazia de verdade.
 *
 * O Google chama `gm_authFailure` quando recusa, e diz o motivo exato no
 * console (RefererNotAllowedMapError, InvalidKeyMapError, ApiNotActivated,
 * BillingNotEnabled). Sem capturar isso, as quatro causas viram o mesmo
 * silencio. */
async function waitMapReady(page: Page, timeout = MAP_READY_TIMEOUT_MS): Promise<boolean> {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const estado = await page
      .evaluate(() => {
        const w = globalThis as any;
        return { pronto: w.isReady?.() ?? false, recusou: !!w.__authFailure };
      })
      .catch(() => ({ pronto: false, recusou: false }));

    if (estado.recusou) {
      throw new Error(
        'O Google RECUSOU a chave da Maps JavaScript API — o mapa não carrega e ' +
        'toda imagem capturada seria a tela de erro. Motivo exato no console do ' +
        'navegador (ex.: RefererNotAllowedMapError). Confira MAPS_JS_KEY no .env ' +
        'e, no console do Google, se o referrer http://127.0.0.1:* está autorizado.');
    }
    if (estado.pronto) return true;
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

/** Serve UM arquivo HTML em 127.0.0.1, em porta FIXA.
 *
 * Existe para a página do mapa ter origem e referrer HTTP — sem isso a chave da
 * Maps JavaScript API restrita por referrer recusa a página, que era o caso até
 * 13/08/2026 com `file://`.
 *
 * A porta é fixa de propósito, e essa é a decisão de projeto aqui. Porta
 * sorteada pelo SO nunca colide, mas obriga a autorizar um CURINGA
 * (`http://127.0.0.1:*` /*) no console do Google — e curinga de porta autoriza
 * qualquer processo local, inclusive o que não é nosso. Com porta fixa basta
 * UMA entrada exata na lista de referenciadores, e ela vale para sempre.
 *
 * Se a porta estiver ocupada a captura PARA com a causa dita por extenso, em
 * vez de cair para uma porta aleatória — que renderia justamente o erro de
 * referrer que este servidor existe para evitar. */
const PORTA_MAPA = Number(process.env.CAPTURE_MAP_PORT || 8766);

function servirPagina(htmlPath: string): Promise<{ url: string; fechar: () => void }> {
  const html = fs.readFileSync(htmlPath);
  return new Promise((resolve, reject) => {
    const srv = http.createServer((_req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(html);
    });
    srv.on('error', (e: NodeJS.ErrnoException) => {
      reject(e.code === 'EADDRINUSE'
        ? new Error(
            `A porta ${PORTA_MAPA} já está em uso, e é nela que a página do mapa ` +
            `precisa ser servida para o referrer bater com o autorizado no Google. ` +
            `Feche o processo que a ocupa, ou defina CAPTURE_MAP_PORT no .env com ` +
            `outra porta — e autorize a nova em Referenciadores HTTP da chave.`)
        : e);
    });
    srv.listen(PORTA_MAPA, '127.0.0.1', () => {
      resolve({
        url: `http://127.0.0.1:${PORTA_MAPA}/_map.html`,
        fechar: () => { try { srv.close(); } catch { /* já caiu */ } },
      });
    });
  });
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
  mapUrl: string,
): Promise<void> {
  // Mantém 10 workers, mas abre de forma escalonada para reduzir estouro no início
  await new Promise(r => setTimeout(r, workerId * 1500));

  // QUAL CHROMIUM, e por que isto precisa ser escolhível.
  //
  // MEDIDO no i9 em 26/08/2026, quando a captura passou a rodar lá: o build
  // `chromium-1217` de LINUX carrega a Maps JS inteira (todas as requisições
  // 200) e nunca dispara o evento `idle` — o mapa não fica pronto, os workers
  // desistem, a captura termina dizendo "concluída" com 0 tiles e o OCR segue
  // com 0 recortes. Sucesso relatado sobre nada.
  //
  // O mesmo teste, na mesma máquina, no mesmo minuto:
  //
  //     chromium-1217  NAO INICIALIZOU em 45,1 s
  //     chromium-1234  PRONTO em 1,8 s
  //
  // Não é a versão do pacote: o notebook roda o MESMO playwright 1.59.1 com o
  // MESMO 1217 e captura sem problema — no build de WINDOWS. É o binário de
  // Linux que não renderiza o mapa vetorial.
  //
  // `CAPTURE_CHROME` no .env da máquina resolve sem mexer no package.json, que
  // é compartilhado. Vazio (o caso do notebook) = o playwright escolhe.
  const chromeDaMaquina = (process.env.CAPTURE_CHROME || '').trim();

  const browser: Browser = await chromium.launch({
    headless: true,
    executablePath: chromeDaMaquina || undefined,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      // HTTP/2 + Chromium + Google = o mapa nunca fica pronto. Medido no i9 em
      // 26/08/2026, quando a captura passou a rodar lá.
      //
      // O sintoma nao acusa a causa: os quatro workers imprimiam "Mapa nao
      // inicializou", a captura terminava dizendo "concluida" com 0/1 tiles, e
      // o OCR seguia com 0 recortes. Sucesso relatado sobre nada.
      //
      // Nao era a chave (o sha256 do MAPS_JS_KEY e o mesmo nas duas maquinas),
      // nao era referrer (na porta 8766 nao ha erro de referrer), nao era WebGL
      // (o SwiftShader do i9 responde). Era o protocolo: com esta flag o mesmo
      // teste, na mesma porta, na mesma maquina, devolve `pronto: true`.
      //
      // Mesma causa que travava a busca no Maps atraves de proxy — la o efeito
      // aparecia como "IP queimado" e 0/8 IPs abriam a pagina. Ver
      // `human_browser.py` e `tests/test_http2_mata_o_maps.py`.
      '--disable-http2',
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
  await page.goto(mapUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });

  // Aguarda o mapa inicializar
  let ready = false;
  try {
    ready = await waitMapReady(page, MAP_READY_TIMEOUT_MS);
  } catch (e) {
    // Chave recusada não é problema de UM worker: é da rodada inteira. Fecha
    // este browser e propaga, para a captura parar aqui em vez de gerar
    // milhares de retratos da tela de erro e terminar dizendo "0 POIs".
    await browser.close().catch(() => {});
    throw e;
  }
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

  // A página é SERVIDA por HTTP local, não aberta como arquivo.
  //
  // Até 13/08/2026 os workers faziam `page.goto('file://' + htmlPath)`. Página
  // `file://` não envia cabeçalho Referer, e chave da Maps JavaScript API
  // restrita por referrer HTTP recusa exatamente esse caso — devolvendo
  // `RefererNotAllowedMapError`. O efeito era mudo: o mapa não inicializava, o
  // evento `idle` nunca vinha, e cada tile salvo era um retrato da tela cinza
  // "Ops! Algo deu errado". O OCR então lia zero POIs numa área cheia deles.
  //
  // Servindo em 127.0.0.1 a requisição passa a ter origem e referrer reais, e a
  // chave pode continuar RESTRITA no console do Google — que é o ponto: a
  // alternativa seria remover a restrição da chave, trocando um problema de
  // configuração por um de segurança.
  const servidor = await servirPagina(htmlPath);
  console.log(`🔐 Página servida em ${servidor.url} (referrer real; a chave segue restrita)`);

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

  try {
    await Promise.all(
      Array.from({ length: workers }, (_, i) =>
        runWorker(i, tiles, queueIndex, session, sessionFile, lock, config, stats, servidor.url)
      )
    );
  } finally {
    // O servidor precisa cair mesmo se um worker estourar, senão a porta fica
    // presa e a próxima rodada sobe outro por cima.
    servidor.fechar();
  }

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
