# Builds every release flavour of Fair Sorting into FINAL_BUILD\dist:
#
#   app-folder\fair sorting.exe      folder version (fastest start)
#   single-exe\fair sorting.exe      one portable file
#   Fair Sorting Setup.exe           per-user installer (Start menu, desktop, Apps & features)
#   fair-sorting-app.zip             the app folder zipped   (-Zip)
#
#   powershell -ExecutionPolicy Bypass -File FINAL_BUILD\build_release.ps1 -Zip
#   powershell -ExecutionPolicy Bypass -File FINAL_BUILD\build_release.ps1 -OnlyFolder
#   powershell -ExecutionPolicy Bypass -File FINAL_BUILD\build_release.ps1 -NoInstaller
#
# Needs: pip install -r requirements.txt pyinstaller
# FFmpeg: put ffmpeg.exe + ffprobe.exe in FINAL_BUILD\ffmpeg\bin (ffplay is not needed).

param(
    [switch]$Zip,
    [switch]$OnlyFolder,
    [switch]$NoInstaller
)

$ErrorActionPreference = "Stop"
$env:SETUPTOOLS_USE_DISTUTILS = "stdlib"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$appSpec = Join-Path $here "fair_sorting.spec"
$setupSpec = Join-Path $here "fair_sorting_setup.spec"
$dist = Join-Path $here "dist"
$work = Join-Path $here "build"
$appFolder = Join-Path $dist "app-folder"
$singleDir = Join-Path $dist "single-exe"
$payloadDir = Join-Path $work "payload"
$payload = Join-Path $payloadDir "payload.zip"
$setupExe = Join-Path $dist "Fair Sorting Setup.exe"

function Remove-Target([string]$PathToRemove) {
    if (Test-Path -LiteralPath $PathToRemove) { Remove-Item -LiteralPath $PathToRemove -Recurse -Force }
}

function Invoke-Build([string]$Spec, [string]$DistPath, [string]$WorkPath) {
    & python -m PyInstaller --noconfirm --clean --log-level WARN --distpath $DistPath --workpath $WorkPath $Spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $Spec" }
}

function Get-SizeMb([string]$PathToMeasure) {
    $item = Get-Item -LiteralPath $PathToMeasure
    if ($item.PSIsContainer) {
        return [math]::Round(((Get-ChildItem -LiteralPath $PathToMeasure -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
    }
    return [math]::Round($item.Length / 1MB, 1)
}

foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $here "ffmpeg\bin\$tool"))) {
        Write-Warning "$tool not found in FINAL_BUILD\ffmpeg\bin. The build will rely on FFmpeg being installed on the user's PC."
    }
}

Write-Host "Cleaning previous output..."
foreach ($target in @($appFolder, $singleDir, $setupExe, $payloadDir, (Join-Path $work "app-folder"), (Join-Path $work "single-exe"), (Join-Path $work "setup"))) {
    Remove-Target $target
}

Write-Host "Building app folder..."
$env:FAIR_SORTING_ONEFILE = "0"
Invoke-Build $appSpec $dist (Join-Path $work "app-folder")
$exe = Join-Path $appFolder "fair sorting.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Missing $exe" }

if (-not $OnlyFolder) {
    Write-Host "Building single exe..."
    $env:FAIR_SORTING_ONEFILE = "1"
    Invoke-Build $appSpec $singleDir (Join-Path $work "single-exe")
}
Remove-Item Env:\FAIR_SORTING_ONEFILE -ErrorAction SilentlyContinue

if ($Zip -or -not ($OnlyFolder -or $NoInstaller)) {
    Write-Host "Zipping app folder..."
    New-Item -ItemType Directory -Force -Path $payloadDir | Out-Null
    & python -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2])" ($payload -replace '\.zip$', '') $appFolder
    if ($LASTEXITCODE -ne 0) { throw "Zipping failed" }
}

if (-not ($OnlyFolder -or $NoInstaller)) {
    Write-Host "Building installer..."
    $env:FAIR_SORTING_PAYLOAD = $payload
    Invoke-Build $setupSpec $dist (Join-Path $work "setup")
    Remove-Item Env:\FAIR_SORTING_PAYLOAD -ErrorAction SilentlyContinue
}

if ($Zip) {
    Copy-Item -LiteralPath $payload -Destination (Join-Path $dist "fair-sorting-app.zip") -Force
}

Write-Host ""
Write-Host "Build complete."
Write-Host ("App folder : {0} ({1} MB)" -f $appFolder, (Get-SizeMb $appFolder))
if (-not $OnlyFolder) { Write-Host ("Single exe : {0} ({1} MB)" -f (Join-Path $singleDir "fair sorting.exe"), (Get-SizeMb (Join-Path $singleDir "fair sorting.exe"))) }
if (Test-Path -LiteralPath $setupExe) { Write-Host ("Installer  : {0} ({1} MB)" -f $setupExe, (Get-SizeMb $setupExe)) }
if ($Zip) { Write-Host ("Zip        : {0} ({1} MB)" -f (Join-Path $dist "fair-sorting-app.zip"), (Get-SizeMb (Join-Path $dist "fair-sorting-app.zip"))) }
