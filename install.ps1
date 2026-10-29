# Установка local-ai-reels на Windows: виртуальное окружение, PyTorch под ваше железо, пакет, проверка.
#   powershell -ExecutionPolicy Bypass -File install.ps1           — сам определит видеокарту NVIDIA
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Cpu      — без видеокарты
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Gpu      — с видеокартой NVIDIA
#   ... -Full (все ML-зависимости и на CPU)  -Models (скачать модели)  -NoVenv (без .venv)
# Linux и macOS: install.sh. Повторный запуск безопасен.
param([switch]$Cpu, [switch]$Gpu, [switch]$Full, [switch]$Models, [switch]$NoVenv)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"

function Say($m) { Write-Host "> $m" -ForegroundColor Yellow }
function Warn($m) { Write-Host "! $m" -ForegroundColor Red }

$py = (Get-Command py -ErrorAction SilentlyContinue) ?? (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { Warn "нет Python 3.10+: winget install Python.Python.3.12"; exit 1 }
$pyExe = if ($py.Name -eq "py.exe") { "py" } else { "python" }
& $pyExe -c "import sys; sys.exit(sys.version_info < (3, 10))"
if ($LASTEXITCODE -ne 0) { Warn "нужен Python 3.10+: winget install Python.Python.3.12"; exit 1 }

# --- железо -------------------------------------------------------------------------------------------------
$mode = "cpu"
if ($Gpu) { $mode = "cuda" }
elseif (-not $Cpu -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    nvidia-smi *> $null
    if ($LASTEXITCODE -eq 0) { $mode = "cuda" }
}
$torchIndex = "https://download.pytorch.org/whl/cpu"
if ($mode -eq "cuda") {
    $cuda = (nvidia-smi | Select-String "CUDA Version: (\d+)\.(\d+)").Matches.Groups
    $ver = if ($cuda) { [int]("$($cuda[1])$($cuda[2])") } else { 0 }
    $torchIndex = if ($ver -ge 126) { "https://download.pytorch.org/whl/cu126" } else { "https://download.pytorch.org/whl/cu124" }
    Say "видеокарта NVIDIA (CUDA $ver) -> PyTorch из $torchIndex"
} else { Say "без видеокарты -> CPU-сборки, лёгкие модели" }

# --- окружение ----------------------------------------------------------------------------------------------
if (-not $NoVenv) {
    if (-not (Test-Path .venv)) { Say "создаю .venv"; & $pyExe -m venv .venv }
    $pyExe = ".\.venv\Scripts\python.exe"
}
& $pyExe -m pip install -q --upgrade pip

$extras = "asr"
if ($mode -eq "cuda" -or $Full) {
    Say "PyTorch"
    & $pyExe -m pip install -q torch torchaudio --index-url $torchIndex
    $extras = "ml"
}
if ($mode -eq "cpu") { $extras = "$extras,piper" }
Say "local-ai-reels [$extras]"
& $pyExe -m pip install -q -e ".[$extras]"

# --- внешние программы --------------------------------------------------------------------------------------
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Warn "нет ffmpeg: winget install Gyan.FFmpeg" }
if (Get-Command node -ErrorAction SilentlyContinue) {
    $major = [int](node -p "process.versions.node.split('.')[0]")
    if ($major -lt 22) { Warn "Node.js $major, нужен 22+: winget install OpenJS.NodeJS.LTS" }
} else { Warn "нет Node.js 22+ (нужен для рендера): winget install OpenJS.NodeJS.LTS" }
Warn "если HyperFrames не скачает Chrome (ошибка 403): `$env:HYPERFRAMES_BROWSER_PATH = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'"

if ($Models) { Say "модели"; & $pyExe tools/fetch_models.py }

Say "проверка"
& $pyExe -m reels doctor
if ($LASTEXITCODE -ne 0) { & $pyExe -m reels scenes }
Write-Host ""
Write-Host "Готово. Дальше:"
if (-not $NoVenv) { Write-Host "  .\.venv\Scripts\Activate.ps1" }
Write-Host "  python -m reels run examples/povodok-templates --yes     # пример без GPU и без моделей"
Write-Host "  python -m reels new my-reel; python -m reels run my-reel"
