# install_autostart.ps1 — registra el AUTO-ARRANQUE robusto de los bots de trading.
#
# Triggers:
#  1) DOMINGO 17:15 hora MX (abre la sesion de Tokio 17:30) -> arranque semanal del sistema.
#  2) Al iniciar sesion (+1min) -> recuperacion tras reinicio entre semana (la clave Run
#     de HKCU no era confiable tras un botonazo, 2026-07-31).
# La tarea corre en contexto de usuario (RunLevel Limited); requiere sesion iniciada.
# Idempotente (-Force). Correr:  powershell -ExecutionPolicy Bypass -File install_autostart.ps1
$ErrorActionPreference = "Stop"
$ps1  = "C:\Users\tojap\PycharmProjects\trading_bandit\start_bots.ps1"
$user = "$env:USERDOMAIN\$env:USERNAME"
$name = "TradingBotsAutostart"

$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
             -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ps1`""
# Trigger 1: domingo 17:15 (Tokio abre 17:30 hora MX). Ajustar la hora si cambia la zona.
$tSun = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At ([datetime]"17:15")
# Trigger 2: al iniciar sesion, +1 min
$tLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$tLogon.Delay = "PT1M"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
             -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $name -Action $action -Trigger @($tSun, $tLogon) -Settings $settings `
  -RunLevel Limited -User $user -Force `
  -Description "Arranca los bots: domingo 17:15 (abre Tokio) + al iniciar sesion (recuperacion)." | Out-Null

Write-Host "Tarea '$name' registrada. Estado:" (Get-ScheduledTask -TaskName $name).State
Write-Host "Proximo disparo:" (Get-ScheduledTaskInfo -TaskName $name).NextRunTime
