import * as readline from 'readline';
import * as path from 'path';
import * as fs from 'fs';
import chalk from 'chalk';
import { searchBoundsByName, estimateTileCount } from './geo';
import { runCaptureSession } from './capture';
import { BoundingBox, CaptureConfig } from './types';

// ─── Diretório raiz do projeto ──────────────────────────────────────────────
// Windows: C:\Users\ceo\Documents\Sistemas\comercialRadar\capturas
// Adapta automaticamente o separador de path do OS
const BASE_DIR = path.resolve(__dirname, '..', 'capturas');

// ─── Helper: leitura de linha ────────────────────────────────────────────────
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

function ask(question: string): Promise<string> {
  return new Promise((resolve) => rl.question(question, (ans) => resolve(ans.trim())));
}

function askNumber(question: string, defaultVal: number): Promise<number> {
  return ask(`${question} [${defaultVal}]: `).then((ans) => {
    const n = parseFloat(ans);
    return isNaN(n) ? defaultVal : n;
  });
}

// ─── Banner ──────────────────────────────────────────────────────────────────
function printBanner() {
  console.log(chalk.cyan('\n╔══════════════════════════════════════════╗'));
  console.log(chalk.cyan('║') + chalk.bold.white('        🗺️  COMERCIAL RADAR v1.0          ') + chalk.cyan('║'));
  console.log(chalk.cyan('║') + chalk.gray('   Captura automática de POIs no Maps     ') + chalk.cyan('║'));
  console.log(chalk.cyan('╚══════════════════════════════════════════╝\n'));
}

// ─── Fluxo: busca por nome ───────────────────────────────────────────────────
async function flowSearchByName(): Promise<BoundingBox> {
  const query = await ask(chalk.yellow('🔍 Digite o nome da cidade/bairro/região: '));

  console.log(chalk.gray('   Buscando no OpenStreetMap...'));
  const results = await searchBoundsByName(query);

  console.log(chalk.green(`\n   Encontrado(s) ${results.length} resultado(s):\n`));
  results.forEach((r, i) => {
    console.log(`   ${chalk.bold(`[${i + 1}]`)} ${r.displayName}`);
    console.log(
      chalk.gray(
        `       N:${r.bbox.north.toFixed(4)} S:${r.bbox.south.toFixed(4)} L:${r.bbox.east.toFixed(4)} O:${r.bbox.west.toFixed(4)}\n`
      )
    );
  });

  const choice = await askNumber('   Escolha o número do resultado', 1);
  const selected = results[Math.min(Math.max(choice - 1, 0), results.length - 1)];

  console.log(chalk.green(`\n   ✔ Selecionado: ${selected.displayName}\n`));
  return selected.bbox;
}

// ─── Fluxo: coordenadas manuais ──────────────────────────────────────────────
async function flowManualCoords(): Promise<BoundingBox> {
  console.log(chalk.yellow('\n📌 Cole os pontos de canto da area (lat, lng):'));
  console.log(chalk.gray('   Ex: -4.9823, -42.8471'));
  console.log(chalk.gray('   Minimo 2 pontos (canto sup-esq e inf-dir), maximo 4. Enter vazio encerra.\n'));

  const points: { lat: number; lng: number }[] = [];

  for (let i = 1; i <= 4; i++) {
    const prompt = i > 2 ? `   Ponto ${i} (Enter para encerrar): ` : `   Ponto ${i}: `;
    const input = await ask(prompt);

    if (!input && i > 2) break;
    if (!input) {
      console.log(chalk.red('   Informe pelo menos 2 pontos!'));
      i--;
      continue;
    }

    const parts = input.split(/[\s,]+/).map((s: string) => parseFloat(s.trim())).filter((n: number) => !isNaN(n));
    if (parts.length < 2) {
      console.log(chalk.red('   Formato invalido. Use: -4.9823, -42.8471'));
      i--;
      continue;
    }

    points.push({ lat: parts[0], lng: parts[1] });
    console.log(chalk.green(`   Ponto ${i}: lat ${parts[0]}, lng ${parts[1]}`));
  }

  const lats = points.map((p: { lat: number; lng: number }) => p.lat);
  const lngs = points.map((p: { lat: number; lng: number }) => p.lng);
  const bbox: BoundingBox = {
    north: Math.max(...lats),
    south: Math.min(...lats),
    east:  Math.max(...lngs),
    west:  Math.min(...lngs),
  };

  console.log(chalk.cyan('\n   Bounding box calculado:'));
  console.log(`   Norte: ${bbox.north} | Sul:   ${bbox.south}`);
  console.log(`   Leste: ${bbox.east}  | Oeste: ${bbox.west}\n`);

  return bbox;
}

// ─── Main ────────────────────────────────────────────────────────────────────
async function main() {
  printBanner();

  // 1. Como definir a área?
  console.log(chalk.bold('Como deseja definir a área de captura?\n'));
  console.log('  [1] Buscar automaticamente pelo nome (cidade, bairro, região)');
  console.log('  [2] Informar coordenadas manualmente (lat/lng)\n');

  const mode = await ask(chalk.yellow('Escolha [1/2]: '));

  let bbox: BoundingBox;
  if (mode === '2') {
    bbox = await flowManualCoords();
  } else {
    bbox = await flowSearchByName();
  }

  // 2. Configurações da captura
  console.log(chalk.bold('\n⚙️  Configurações da captura:\n'));

  const zoom = await askNumber(
    '   Zoom do mapa (19 = rua c/ POIs, 18 = quadra, 17 = bairro, 15 = cidade)',
    19
  );

  const delay = await askNumber(
    '   Delay entre tiles em ms (300 = rápido, 1000 = seguro contra bloqueio)',
    600
  );

  const overlap = await askNumber('   Sobreposição entre tiles % (10 recomendado)', 10);

  // 3. Nome da sessão / pasta de saída
  const defaultSession = `sessao_${new Date().toISOString().slice(0, 16).replace(/[T:]/g, '_')}`;
  const sessionName = (await ask(`\n   Nome da sessão [${defaultSession}]: `)) || defaultSession;
  const outputDir = path.join(BASE_DIR, sessionName);

  // 4. Estimativa
  const totalTiles = estimateTileCount(bbox, zoom, overlap);
  const estimatedMinutes = Math.ceil((totalTiles * (delay + 4000)) / 60000);

  console.log(chalk.cyan(`\n📊 Estimativa:`));
  console.log(`   Tiles a capturar : ${chalk.bold(totalTiles)}`);
  console.log(`   Tempo estimado   : ~${chalk.bold(estimatedMinutes)} minutos`);
  console.log(`   Pasta de saída   : ${chalk.bold(outputDir)}\n`);

  if (totalTiles > 2000) {
    console.log(
      chalk.yellow(
        '⚠️  Muitos tiles! Considere aumentar o zoom (menos área) ou diminuir o bounding box.\n'
      )
    );
  }

  const confirm = await ask(chalk.green('▶ Iniciar captura? [s/N]: '));
  if (confirm.toLowerCase() !== 's') {
    console.log(chalk.red('\nCaptura cancelada.\n'));
    rl.close();
    return;
  }

  rl.close();

  // 5. Executa
  const config: CaptureConfig = {
    boundingBox: bbox,
    zoomLevel: zoom,
    outputDir,
    sessionName,
    tileOverlapPercent: overlap,
    viewportWidth: 3840,
    viewportHeight: 2160,
    delayBetweenTiles: delay,
  };

  await runCaptureSession(config);

  console.log(chalk.cyan('\n🏁 Processo finalizado! Imagens salvas em:'));
  console.log(chalk.bold(`   ${outputDir}\n`));
}

main().catch((err) => {
  console.error(chalk.red('\n❌ Erro fatal:'), err.message);
  process.exit(1);
});
