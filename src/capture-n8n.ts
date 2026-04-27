/**
 * capture-n8n.ts
 * Versão não-interativa chamada pelo n8n via executeCommand.
 * Uso: ts-node src/capture-n8n.ts "Teresina, PI" 17 600 "sessao_2024"
 */
import * as path from 'path';
import { searchBoundsByName } from './geo';
import { runCaptureSession } from './capture';
import { CaptureConfig } from './types';

const BASE_DIR = path.resolve(__dirname, '..', 'capturas');

async function main() {
  const [, , query, zoomStr, delayStr, sessionName] = process.argv;

  if (!query) {
    console.error('Uso: ts-node src/capture-n8n.ts "<query>" <zoom> <delay_ms> <session_name>');
    process.exit(1);
  }

  const zoom = parseInt(zoomStr || '17', 10);
  const delay = parseInt(delayStr || '600', 10);
  const session = sessionName || `n8n_${Date.now()}`;

  console.log(`🗺️  ComercialRadar — Modo n8n`);
  console.log(`   Query    : ${query}`);
  console.log(`   Zoom     : ${zoom}`);
  console.log(`   Delay    : ${delay}ms`);
  console.log(`   Sessão   : ${session}\n`);

  // Busca bounding box
  const results = await searchBoundsByName(query);
  const bbox = results[0].bbox;
  console.log(`   Área     : ${results[0].displayName}`);
  console.log(`   BBox     : N${bbox.north.toFixed(4)} S${bbox.south.toFixed(4)} L${bbox.east.toFixed(4)} O${bbox.west.toFixed(4)}\n`);

  const config: CaptureConfig = {
    boundingBox: bbox,
    zoomLevel: zoom,
    outputDir: path.join(BASE_DIR, session),
    sessionName: session,
    tileOverlapPercent: 10,
    viewportWidth: 3840,
    viewportHeight: 2160,
    delayBetweenTiles: delay,
  };

  await runCaptureSession(config);
}

main().catch((err) => {
  console.error('❌ Erro fatal:', err.message);
  process.exit(1);
});
