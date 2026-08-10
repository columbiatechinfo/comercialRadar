/**
 * capture-cli.ts — captura SEM perguntar nada, para o painel poder chamar.
 *
 * O `index.ts` é interativo (readline): serve para rodar na mão, mas um
 * subprocess do servidor não tem quem responda "Iniciar captura? [s/N]" e
 * ficaria pendurado para sempre. Este arquivo recebe tudo por argumento.
 *
 *   npx ts-node src/capture-cli.ts --sessao NOME --saida DIR \
 *       --poly area.json [--zoom 19] [--overlap 10]
 *
 * `--poly` é o arquivo com os vértices [[lat, lng], ...] da área desenhada no
 * mapa; a caixa envolvente sai dele. Sem `--poly`, usa `--bbox N,S,L,O`.
 *
 * Imprime "— N células" (o total que o painel lê) e repassa o progresso da
 * captura, que o orquestrador traduz para a barra.
 */
import * as fs from 'fs';
import * as path from 'path';
import { runCaptureSession, generateTiles } from './capture';
import { BoundingBox, CaptureConfig } from './types';

function arg(nome: string, padrao?: string): string | undefined {
  const i = process.argv.indexOf(`--${nome}`);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : padrao;
}

function lerPoligono(arquivo: string): { lat: number; lng: number }[] {
  const bruto = JSON.parse(fs.readFileSync(arquivo, 'utf-8'));
  const lista = Array.isArray(bruto) ? bruto : bruto.polygon;
  if (!Array.isArray(lista) || lista.length < 3) {
    throw new Error(`polígono inválido em ${arquivo}`);
  }
  return lista.map((p: any) =>
    Array.isArray(p) ? { lat: Number(p[0]), lng: Number(p[1]) }
                     : { lat: Number(p.lat), lng: Number(p.lng) });
}

async function main() {
  const sessao = arg('sessao');
  const saida = arg('saida');
  if (!sessao || !saida) {
    console.error('uso: --sessao NOME --saida DIR [--poly a.json | --bbox N,S,L,O] '
                  + '[--zoom 19] [--overlap 10]');
    process.exit(2);
  }

  const zoom = Number(arg('zoom', '19'));
  const overlap = Number(arg('overlap', '10'));
  const polyArq = arg('poly');

  let polygon: { lat: number; lng: number }[] | undefined;
  let bbox: BoundingBox;

  if (polyArq) {
    polygon = lerPoligono(polyArq);
    const lats = polygon.map(p => p.lat);
    const lngs = polygon.map(p => p.lng);
    bbox = { north: Math.max(...lats), south: Math.min(...lats),
             east: Math.max(...lngs), west: Math.min(...lngs) };
  } else {
    const b = (arg('bbox') || '').split(',').map(Number);
    if (b.length !== 4 || b.some(isNaN)) {
      console.error('--bbox precisa de N,S,L,O');
      process.exit(2);
    }
    bbox = { north: b[0], south: b[1], east: b[2], west: b[3] };
  }

  const config: CaptureConfig = {
    boundingBox: bbox,
    zoomLevel: zoom,
    outputDir: path.resolve(saida),
    sessionName: sessao,
    tileOverlapPercent: overlap,
    viewportWidth: 3840,
    viewportHeight: 2160,
    delayBetweenTiles: Number(arg('delay', '600')),
    polygon,
  };

  const tiles = generateTiles(config);
  if (polygon) {
    const semRecorte = generateTiles({ ...config, polygon: undefined }).length;
    console.log(`🗺  Área: ${polygon.length} vértices — ${tiles.length} tiles `
                + `dentro dela (a caixa inteira teria ${semRecorte})`);
  }
  console.log(`— ${tiles.length} células`);

  if (tiles.length === 0) {
    console.log('Nenhum tile dentro da área desenhada. Nada a capturar.');
    return;
  }

  // Só contar: a captura é a parte cara em tempo (minutos por centena de
  // tiles), então dá para medir o tamanho da empreitada antes de assumi-la.
  if (process.argv.includes('--so-contar')) {
    const seg = tiles.length * 9 / Number(process.env.CAPTURE_WORKERS || 10);
    console.log(`Estimativa: ~${Math.ceil(seg / 60)} min de captura `
                + `(${process.env.CAPTURE_WORKERS || 10} navegadores).`);
    return;
  }

  await runCaptureSession(config);
}

main().catch((err) => {
  console.error('❌ capture-cli:', err?.message || err);
  process.exit(1);
});
