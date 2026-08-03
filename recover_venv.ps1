# recover_venv.ps1 — recupera el entorno tras la corrupcion de Python 3.12 (Lib borrada, 2026-08-02).
#
# ORDEN DE RECUPERACION:
#   1) Reparar Python 3.12.6 PRIMERO:  ejecuta  C:\Users\tojap\Downloads\python-3.12.6-amd64.exe
#      -> elige "Repair" (repara la instalacion y restaura la carpeta Lib). NO borra tu proyecto.
#   2) Luego corre ESTE script:  powershell -ExecutionPolicy Bypass -File recover_venv.ps1
#      -> verifica Python, detiene los bots, recrea el .venv limpio, reinstala dependencias y relanza.
#
# NOTA: recrear el venv requiere detener los bots (liberan los archivos). Es un stop breve de la demo.

$ErrorActionPreference = "Stop"
$root = "C:\Users\tojap\PycharmProjects\trading_bandit"
$py   = "C:\Users\tojap\AppData\Local\Programs\Python\Python312\python.exe"

Write-Host "== [1/5] Verificando que Python base este REPARADO ==" -ForegroundColor Cyan
try {
    & $py -c "import encodings, ssl, sqlite3, ctypes; print('Python base OK:', __import__('sys').version.split()[0])"
} catch {
    Write-Host "`n[X] Python AUN NO esta reparado (falla la stdlib)." -ForegroundColor Red
    Write-Host "    Ejecuta primero el instalador:  C:\Users\tojap\Downloads\python-3.12.6-amd64.exe  -> Repair"
    Write-Host "    y vuelve a correr este script."
    exit 1
}
if (-not (Test-Path "$py\..\Lib\encodings" ) -and -not (Test-Path "C:\Users\tojap\AppData\Local\Programs\Python\Python312\Lib\encodings")) {
    Write-Host "[X] Falta Python312\Lib\encodings. Repara Python primero." -ForegroundColor Red; exit 1
}

Write-Host "`n== [2/5] Deteniendo bots (para liberar el .venv) ==" -ForegroundColor Cyan
$scripts = @("dashboard.py","main_live_v2.py","rsi2_live.py","intraday_live.py","stf_live.py",
             "learning_collector.py","meta_observer.py","meta_retrain.py","data_backup.py","michaelfx_cockpit.py")
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
foreach ($s in $scripts) {
    $m = @($procs | Where-Object { $_.CommandLine -match [Regex]::Escape($s) })
    if ($m) { Stop-Process -Id ($m | ForEach-Object { $_.ProcessId }) -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 3
Write-Host "  bots detenidos."

Write-Host "`n== [3/5] Recreando .venv limpio ==" -ForegroundColor Cyan
if (Test-Path "$root\.venv") {
    Remove-Item "$root\.venv" -Recurse -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    if (Test-Path "$root\.venv") { Write-Host "  (aviso: no se pudo borrar todo el .venv; intento continuar)" -ForegroundColor Yellow }
}
& $py -m venv "$root\.venv"
$venvpy = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $venvpy)) { Write-Host "[X] No se creo el venv." -ForegroundColor Red; exit 1 }

Write-Host "`n== [4/5] Instalando dependencias ==" -ForegroundColor Cyan
& $venvpy -m pip install --upgrade pip --quiet
& $venvpy -m pip install pandas numpy MetaTrader5 plotly dash psutil python-json-logger pytest matplotlib pypdf pymupdf
Write-Host "  dependencias instaladas."

Write-Host "`n== [5/5] Relanzando el sistema ==" -ForegroundColor Cyan
& "$root\start_bots.ps1"

Write-Host "`n== RECUPERACION COMPLETA ==" -ForegroundColor Green
Write-Host "Verifica: dashboard http://localhost:8050  ·  cockpit http://localhost:8051"
Write-Host "Si algo no levanta, revisa src\logs\*.err"
