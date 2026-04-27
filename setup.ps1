# ============================================================
#  comercialRadar - Setup automatico Windows
#  Execute com: PowerShell -ExecutionPolicy Bypass -File setup.ps1
# ============================================================

$ErrorActionPreference = "Stop"
$ProjectDir = "C:\Users\ceo\Documents\Sistemas\comercialRadar"

Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║       🗺  COMERCIAL RADAR - SETUP        ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── 1. Verifica Node.js ──────────────────────────────────────
Write-Host "🔍 Verificando Node.js..." -ForegroundColor Yellow
$nodeOk = $false
try {
    $nodeVersion = & node --version 2>&1
    Write-Host "   ✔ Node.js $nodeVersion encontrado" -ForegroundColor Green
    $nodeOk = $true
} catch {
    $nodeOk = $false
}

if (-not $nodeOk) {
    Write-Host "   ✗ Node.js nao encontrado!" -ForegroundColor Red
    Write-Host "   Baixe em: https://nodejs.org" -ForegroundColor Gray
    exit 1
}

# ── 2. Verifica/instala n8n ──────────────────────────────────
Write-Host ""
Write-Host "🔍 Verificando n8n..." -ForegroundColor Yellow
$n8nOk = $false
try {
    $n8nVersion = & n8n --version 2>&1
    Write-Host "   ✔ n8n $n8nVersion ja instalado" -ForegroundColor Green
    $n8nOk = $true
} catch {
    $n8nOk = $false
}

if (-not $n8nOk) {
    Write-Host "   Instalando n8n globalmente (pode demorar ~2min)..." -ForegroundColor Yellow
    & npm install -g n8n
    Write-Host "   ✔ n8n instalado!" -ForegroundColor Green
}

# ── 3. Cria pastas ───────────────────────────────────────────
Write-Host ""
Write-Host "📁 Criando estrutura de pastas..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $ProjectDir | Out-Null
New-Item -ItemType Directory -Force -Path "$ProjectDir\capturas" | Out-Null
New-Item -ItemType Directory -Force -Path "$ProjectDir\src" | Out-Null
Write-Host "   ✔ Pastas criadas em $ProjectDir" -ForegroundColor Green

# ── 4. Instala dependencias npm ──────────────────────────────
Write-Host ""
Write-Host "📦 Instalando dependencias npm..." -ForegroundColor Yellow
Set-Location $ProjectDir
& npm install
Write-Host "   ✔ Dependencias instaladas" -ForegroundColor Green

# ── 5. Instala Chromium (Playwright) ─────────────────────────
Write-Host ""
Write-Host "🌐 Instalando Chromium (Playwright)..." -ForegroundColor Yellow
& npx playwright install chromium
Write-Host "   ✔ Chromium instalado" -ForegroundColor Green

# ── 6. Cria atalhos na Area de Trabalho ──────────────────────
Write-Host ""
Write-Host "🖥  Criando atalhos..." -ForegroundColor Yellow

$DesktopPath = [System.Environment]::GetFolderPath('Desktop')
$WshShell = New-Object -comObject WScript.Shell

$ShortcutScript = $WshShell.CreateShortcut("$DesktopPath\ComercialRadar - Captura.lnk")
$ShortcutScript.TargetPath = "cmd.exe"
$ShortcutScript.Arguments = '/k cd /d "' + $ProjectDir + '" && npx ts-node src/index.ts'
$ShortcutScript.WorkingDirectory = $ProjectDir
$ShortcutScript.IconLocation = "shell32.dll,13"
$ShortcutScript.Description = "Iniciar captura de mapas"
$ShortcutScript.Save()

$ShortcutN8n = $WshShell.CreateShortcut("$DesktopPath\ComercialRadar - n8n.lnk")
$ShortcutN8n.TargetPath = "cmd.exe"
$ShortcutN8n.Arguments = "/k n8n start"
$ShortcutN8n.WorkingDirectory = $ProjectDir
$ShortcutN8n.IconLocation = "shell32.dll,14"
$ShortcutN8n.Description = "Iniciar servidor n8n"
$ShortcutN8n.Save()

Write-Host "   ✔ Atalhos criados na Area de Trabalho" -ForegroundColor Green

# ── Resumo ───────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║          ✅  SETUP CONCLUIDO!            ║" -ForegroundColor Green
Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "Como usar:" -ForegroundColor White
Write-Host ""
Write-Host "  1. CAPTURA DIRETA (mais simples):" -ForegroundColor Cyan
Write-Host "     Clique no atalho 'ComercialRadar - Captura' na Area de Trabalho" -ForegroundColor Gray
Write-Host "     ou no terminal: npx ts-node src/index.ts" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. COM n8n (agendamento automatico):" -ForegroundColor Cyan
Write-Host "     a) Clique no atalho 'ComercialRadar - n8n' na Area de Trabalho" -ForegroundColor Gray
Write-Host "     b) Abra no browser: http://localhost:5678" -ForegroundColor Gray
Write-Host "     c) Importe o arquivo: n8n-workflow.json" -ForegroundColor Gray
Write-Host ""
Write-Host "  3. REPROCESSAR tiles falhos:" -ForegroundColor Cyan
Write-Host "     npx ts-node src/retry-failed.ts capturas\nome-sessao\session.json" -ForegroundColor Gray
Write-Host ""
