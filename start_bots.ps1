# start_bots.ps1 — arranca todo el sistema de trading como procesos INDEPENDIENTES.
# Sobrevive al cierre del agente y a reinicios (via tarea programada al iniciar sesion).
# Idempotente: si un componente ya corre, lo salta (no duplica).
$ErrorActionPreference = "SilentlyContinue"
$root = "C:\Users\tojap\PycharmProjects\trading_bandit"
$src  = Join-Path $root "src"
$py   = Join-Path $root ".venv\Scripts\python.exe"
$logs = Join-Path $src "logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# Orden: dashboard, bandit (solo-aprende), las 3 estrategias, y el pipeline meta.
$scripts = @(
  "dashboard.py", "main_live_v2.py",
  "rsi2_live.py", "intraday_live.py", "stf_live.py", "svxy_live.py",
  "learning_collector.py", "meta_observer.py", "meta_retrain.py",
  "data_backup.py", "michaelfx_cockpit.py"
)

foreach ($s in $scripts) {
  $running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
             Where-Object { $_.CommandLine -match [Regex]::Escape($s) }
  if ($running) { Write-Host "ya corre: $s"; continue }
  $name = [IO.Path]::GetFileNameWithoutExtension($s)
  $out  = Join-Path $logs "$name.out"
  Start-Process -FilePath $py -ArgumentList @("-u", $s) -WorkingDirectory $src `
    -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError "$out.err"
  Write-Host "lanzado: $s"
  Start-Sleep -Milliseconds 800
}
Write-Host "start_bots.ps1 completado."
