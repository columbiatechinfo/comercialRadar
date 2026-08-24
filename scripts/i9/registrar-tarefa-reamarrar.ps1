$nome = 'ReamarrarWSL'
Unregister-ScheduledTask -TaskName $nome -Confirm:$false -ErrorAction SilentlyContinue

$acao = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ferramentas\reamarrar-wsl.ps1'

# NO BOOT, com 1 min de atraso: o script ja espera o Tailscale e o WSL por conta
# propria, mas comecar depois da tempestade de servicos do boot evita disputa.
$gatilho = New-ScheduledTaskTrigger -AtStartup
$gatilho.Delay = 'PT1M'

# SYSTEM porque `netsh portproxy` exige elevacao, e a tarefa roda sem ninguem
# logado.
$conta = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

$cfg = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho `
  -Principal $conta -Settings $cfg `
  -Description 'Reaplica o encaminhamento de portas Windows->WSL apos o boot. As regras netsh existem mas ficam presas em 127.0.0.1; reiniciar o iphlpsvc nao basta, a regra precisa ser reinserida.' | Out-Null

Get-ScheduledTask -TaskName $nome | Format-List TaskName,State
