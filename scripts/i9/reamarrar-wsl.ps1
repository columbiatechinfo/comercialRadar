# reamarrar-wsl.ps1 — reaplica o encaminhamento Windows -> WSL apos o boot.
#
# POR QUE ISTO EXISTE
#
# Em 24/08/2026, depois de um reinicio, os dois Postgres ficaram inalcancaveis
# de fora. Os containers estavam `healthy`, as portas escutavam dentro do WSL, e
# as regras `netsh portproxy` existiam apontando para o IP certo — mas o Windows
# as amarrou em 127.0.0.1. Quem escutava era o `wslrelay.exe`, que so faz
# loopback por desenho.
#
# `Restart-Service iphlpsvc` NAO resolve — testado. A regra precisa ser
# REINSERIDA: delete e add.
#
# COMO ELE ACHA O IP DO WSL SEM O `wsl.exe`
#
# A tarefa roda como SYSTEM, porque `netsh portproxy` exige elevacao e o boot
# acontece sem ninguem logado. E SYSTEM nao enxerga a distro: `wsl -d Ubuntu`
# simplesmente pendura. A primeira versao ficou presa nesse laco.
#
# Entao os candidatos saem da tabela de vizinhos do adaptador do WSL — e cada um
# e TESTADO numa porta que so o WSL serve. Ha entradas obsoletas de boots
# anteriores ali, todas marcadas `Stale` igual a atual: escolher pelo estado
# seria chute, e chutar aqui manda o trafego para lugar nenhum sem erro nenhum.

$ErrorActionPreference = 'Continue'
$log = 'C:\Windows\Temp\reamarrar-wsl.log'
function Registrar($msg) {
  "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg" | Out-File -Append -Encoding utf8 $log
}

function TestarPorta($ip, $porta, $ms = 1500) {
  $c = New-Object System.Net.Sockets.TcpClient
  try {
    $r = $c.BeginConnect($ip, $porta, $null, $null)
    if (-not $r.AsyncWaitHandle.WaitOne($ms)) { return $false }
    $c.EndConnect($r); return $true
  } catch { return $false } finally { $c.Close() }
}

Registrar '--- inicio ---'

# ── 1. Espera o IP do Tailscale ─────────────────────────────────────────────
#
# As regras que escutam num IP especifico so amarram se aquele endereco ja
# existir. No boot o Tailscale sobe depois da rede — inserir antes disso e
# escrever regra que nao vai valer.
$tailscale = '100.115.117.49'
$prazo = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $prazo) {
  if (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -eq $tailscale }) { break }
  Start-Sleep -Seconds 10
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
          Where-Object { $_.IPAddress -eq $tailscale })) {
  Registrar "ABORTADO: $tailscale nao apareceu em 5 min"
  exit 1
}
Registrar "tailscale presente: $tailscale"

# ── 2. Descobre o IP do WSL testando os candidatos ──────────────────────────
#
# 5443 e o Postgres de referencia, que vive dentro do WSL e sobe com o Docker.
# Se ele responde num candidato, aquele e o WSL vivo.
$PORTA_PROVA = 5443
$ipWsl = $null
$prazo = (Get-Date).AddMinutes(6)
while ((Get-Date) -lt $prazo -and -not $ipWsl) {
  $candidatos = Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {
      $_.InterfaceAlias -like '*WSL*' -and
      $_.IPAddress -match '^192\.168\.' -and
      $_.IPAddress -notmatch '\.255$' -and
      $_.IPAddress -ne '192.168.224.1'          # o lado Windows do par
    } | Select-Object -ExpandProperty IPAddress -Unique

  foreach ($c in $candidatos) {
    if (TestarPorta $c $PORTA_PROVA) { $ipWsl = $c; break }
  }
  if (-not $ipWsl) { Start-Sleep -Seconds 15 }
}
if (-not $ipWsl) {
  Registrar "ABORTADO: nenhum candidato respondeu na $PORTA_PROVA em 6 min"
  exit 1
}
Registrar "wsl vivo em: $ipWsl"

# ── 3. Reaplica TODAS as regras ─────────────────────────────────────────────
#
# As que escutam em 0.0.0.0 tambem apontam para o IP do WSL: se ele mudar, elas
# quebram junto. Reaplicar todas e mais simples e mais seguro que decidir quais
# precisam.
$regras = @(
  @{ escuta = $tailscale; porta = 5442 },   # supabase pooler
  @{ escuta = $tailscale; porta = 5443 },   # postgres de referencia
  @{ escuta = $tailscale; porta = 5444 },   # postgres do produto
  @{ escuta = $tailscale; porta = 6543 },   # pooler transacional
  @{ escuta = $tailscale; porta = 8000 },   # kong / api
  @{ escuta = '0.0.0.0';  porta = 2322 },   # photon
  @{ escuta = '0.0.0.0';  porta = 5000 },   # osrm carro
  @{ escuta = '0.0.0.0';  porta = 5001 },   # osrm a pe
  @{ escuta = '0.0.0.0';  porta = 5002 },
  @{ escuta = '0.0.0.0';  porta = 5003 },
  @{ escuta = '0.0.0.0';  porta = 8080 },   # nominatim
  @{ escuta = '0.0.0.0';  porta = 8888 },   # searxng
  @{ escuta = '0.0.0.0';  porta = 9201 },
  @{ escuta = '0.0.0.0';  porta = 11434 }   # ollama
)

foreach ($r in $regras) {
  netsh interface portproxy delete v4tov4 `
    listenaddress=$($r.escuta) listenport=$($r.porta) 2>&1 | Out-Null
  netsh interface portproxy add v4tov4 `
    listenaddress=$($r.escuta) listenport=$($r.porta) `
    connectaddress=$ipWsl connectport=$($r.porta) 2>&1 | Out-Null
}
Registrar "$($regras.Count) regras reaplicadas para $ipWsl"

# ── 4. CONFERE. Regra escrita nao e regra amarrada — foi exatamente essa a
#      diferenca que custou meia hora de investigacao.
Start-Sleep -Seconds 3
$saida = netstat -an
$presos = @()
foreach ($r in $regras) {
  $alvo = if ($r.escuta -eq '0.0.0.0') { '0.0.0.0' } else { $tailscale }
  if (-not ($saida | Select-String ("\s{0}:{1}\s" -f [regex]::Escape($alvo), $r.porta))) {
    $presos += $r.porta
  }
}
if ($presos.Count) {
  Registrar ("NAO AMARRARAM: " + ($presos -join ', '))
  exit 2
}
Registrar 'todas amarradas · OK'
exit 0
