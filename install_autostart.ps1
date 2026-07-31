# install_autostart.ps1 — registra el AUTO-ARRANQUE robusto de los bots de trading.
#
# Por que: la clave Run de HKCU (metodo viejo) NO disparo tras un reinicio por botonazo
# (2026-07-31). Una Tarea Programada "al iniciar sesion" con retraso es mas confiable y el
# retraso deja que el sistema/red se asienten tras un arranque sucio. Idempotente (-Force).
#
# Correr una vez:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1
$ErrorActionPreference = "Stop"
$ps1  = "C:\Users\tojap\PycharmProjects\trading_bandit\start_bots.ps1"
$user = "$env:USERDOMAIN\$env:USERNAME"
$name = "TradingBotsAutostart"

$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
             -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT1M"                       # 1 min de retraso tras el login
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
             -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
  -RunLevel Limited -User $user -Force `
  -Description "Arranca los bots (start_bots.ps1) al iniciar sesion, +1min. Reemplaza la clave Run." | Out-Null

Write-Host "Tarea '$name' registrada. Estado:" (Get-ScheduledTask -TaskName $name).State
Write-Host "start_bots.ps1 es idempotente: la clave Run HKCU puede quedar como fallback sin conflicto."
