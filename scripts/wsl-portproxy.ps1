# Reconstroi o encaminhamento de portas do WSL com o IP ATUAL da distro.
#
# ARQUIVO EM ASCII DE PROPOSITO: acento e travessao viram lixo quando o
# PowerShell le como ANSI, e o erro aparece como "cadeia sem terminador" numa
# linha que parece correta.
#
# Por que existe: o IP da distro WSL muda a cada reinicio, e as regras de
# netsh portproxy guardam o endereco antigo. O sintoma e cruel: os servicos
# sobem normalmente, os logs ficam limpos, e nada responde de fora.
#
# Roda na inicializacao do Windows (tarefa agendada) e pode ser chamado a mao:
#   powershell -ExecutionPolicy Bypass -File C:\Users\OrbisGrid\wsl-portproxy.ps1
#
# Tentamos networkingMode=mirrored em 12/08/2026, que dispensaria tudo isto.
# Nao funcionou nesta maquina: em modo espelhado a entrada passa pelo firewall
# do Hyper-V, e das 10 portas so 2 respondiam. Revertido.

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'
$log = "$env:USERPROFILE\wsl-portproxy.log"

function Registrar($m) {
  "$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))  $m" | Out-File $log -Append -Encoding utf8
}

# Esperar o WSL responder: numa inicializacao a tarefa dispara antes de a
# distro existir, e ai o IP volta vazio e as regras nascem quebradas.
$wsl = $null
foreach ($i in 1..30) {
  $r = (wsl.exe -e bash -lc "hostname -I" 2>$null)
  if ($r) { $wsl = $r.Trim().Split(' ')[0] }
  if ($wsl -match '^\d+\.\d+\.\d+\.\d+$') { break }
  Start-Sleep -Seconds 5
}
if (-not $wsl) { Registrar "ERRO: WSL nao respondeu em 150s"; exit 1 }

netsh interface portproxy reset | Out-Null

# Servicos geo, como sempre estiveram: qualquer interface.
foreach ($p in 5000,5001,5002,5003,8080,2322,9201,11434,8888) {
  netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=$p connectaddress=$wsl connectport=$p | Out-Null
}
# Pilha Supabase: so pela interface do Tailscale. A 5442 evita o
# PostgreSQL 14 nativo do Windows, que ja ocupa a 5432.
foreach ($p in 8000,5442,6543) {
  netsh interface portproxy add v4tov4 listenaddress=100.115.117.49 listenport=$p connectaddress=$wsl connectport=$p | Out-Null
}

$n = (netsh interface portproxy show v4tov4 | Select-String '\d+\.\d+\.\d+\.\d+').Count
Registrar "regras reconstruidas para a distro em $wsl ($n linhas)"
Write-Output "  distro em $wsl, $n regras ativas"
