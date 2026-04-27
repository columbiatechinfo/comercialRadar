/**
 * retry-failed.ts
 * Reprocessa os tiles que falharam em uma sessão anterior.
 * Uso: ts-node src/retry-failed.ts <caminho/para/session.json>
 */
import * as fs from 'fs';
import * as path from 'path';
import chalk from 'chalk';
import { chromium } from 'playwright';
import { CaptureSession } from './types';

async function retryFailed(sessionJsonPath: string) {
  if (!fs.existsSync(sessionJsonPath)) {
    console.error(chalk.red(`Arquivo não encontrado: ${sessionJsonPath}`));
    process.exit(1);
  }

  const session: CaptureSession = JSON.parse(fs.readFileSync(sessionJsonPath, 'utf-8'));

  if (session.failedTiles.length === 0) {
    console.log(chalk.green('✔ Nenhum tile com falha nesta sessão.'));
    return;
  }

  console.log(chalk.yellow(`\n🔁 Retentando ${session.failedTiles.length} tiles falhos...\n`));

  const { capture } = await import('./capture');
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: session.config.viewportWidth, height: session.config.viewportHeight },
    locale: 'pt-BR',
  });
  const page = await context.newPage();

  let recovered = 0;
  const stillFailed = [];

  for (const tile of session.failedTiles) {
    const url = `https://www.google.com/maps/@${tile.lat},${tile.lng},${session.config.zoomLevel}z`;
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(4000);
      const filepath = path.join(session.config.outputDir, tile.filename);
      await page.screenshot({ path: filepath, type: 'png' });
      recovered++;
      console.log(chalk.green(`  ✔ Recuperado: ${tile.filename}`));
    } catch {
      stillFailed.push(tile);
      console.log(chalk.red(`  ✗ Ainda falhou: ${tile.filename}`));
    }
  }

  await browser.close();

  session.failedTiles = stillFailed;
  session.completedTiles += recovered;
  fs.writeFileSync(sessionJsonPath, JSON.stringify(session, null, 2));

  console.log(chalk.cyan(`\n✅ Recuperados: ${recovered} | Ainda falhos: ${stillFailed.length}\n`));
}

const arg = process.argv[2];
if (!arg) {
  console.error(chalk.red('Uso: ts-node src/retry-failed.ts <caminho/para/session.json>'));
  process.exit(1);
}

retryFailed(arg);
