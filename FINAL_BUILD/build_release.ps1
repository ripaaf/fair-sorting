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
# Everything is built into build\stage first, so the zip and the installer never
# pick up settings from a copy you ran out of dist\. Publishing to dist\ keeps
# your fair_settings.json / fair_config.txt / output\ there, and if a copy is
# still running it says so instead of failing halfway.
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
$stage = Join-Path $work "stage"
$payloadDir = Join-Path $work "payload"
$payload = Join-Path $payloadDir "payload.zip"
$keep = @("fair_settings.json", "fair_config.txt", "output")
$warnings = @()

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

# Replace the program files in a dist folder but leave the user's data alone.
function Publish-Folder([string]$From, [string]$To) {
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    try {
        Get-ChildItem -LiteralPath $To -Force | Where-Object { $keep -notcontains $_.Name } | Remove-Item -Recurse -Force
        Copy-Item -Path (Join-Path $From "*") -Destination $To -Recurse -Force
        return $true
    } catch {
        $script:warnings += "Couldn't update $To (is Fair Sorting running from there?). The fresh build is in $From."
        return $false
    }
}

function Publish-File([string]$From, [string]$To) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $To) | Out-Null
    try {
        Copy-Item -LiteralPath $From -Destination $To -Force
        return $true
    } catch {
        $script:warnings += "Couldn't replace $To (is it running?). The fresh build is at $From."
        return $false
    }
}

foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $here "ffmpeg\bin\$tool"))) {
        Write-Warning "$tool not found in FINAL_BUILD\ffmpeg\bin. The build will rely on FFmpeg being installed on the user's PC."
    }
}

Write-Host "Cleaning build folders..."
foreach ($target in @($payloadDir, (Join-Path $work "app-folder"), (Join-Path $work "single-exe"), (Join-Path $work "setup"))) {
    Remove-Target $target
}
# A copy started from build\stage keeps its files locked; build next to it instead of failing.
try { Remove-Target $stage } catch {
    $stage = Join-Path $work ("stage-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    $warnings += "build\stage is in use (is Fair Sorting running from there?), so this build used $stage."
}
New-Item -ItemType Directory -Force -Path $stage | Out-Null

Write-Host "Building app folder..."
$env:FAIR_SORTING_ONEFILE = "0"
Invoke-Build $appSpec $stage (Join-Path $work "app-folder")
$stagedFolder = Join-Path $stage "app-folder"
if (-not (Test-Path -LiteralPath (Join-Path $stagedFolder "fair sorting.exe"))) { throw "App folder build is missing fair sorting.exe" }

if (-not $OnlyFolder) {
    Write-Host "Building single exe..."
    $env:FAIR_SORTING_ONEFILE = "1"
    Invoke-Build $appSpec (Join-Path $stage "single-exe") (Join-Path $work "single-exe")
}
Remove-Item Env:\FAIR_SORTING_ONEFILE -ErrorAction SilentlyContinue

if ($Zip -or -not ($OnlyFolder -or $NoInstaller)) {
    Write-Host "Zipping app folder..."
    New-Item -ItemType Directory -Force -Path $payloadDir | Out-Null
    & python -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2])" ($payload -replace '\.zip$', '') $stagedFolder
    if ($LASTEXITCODE -ne 0) { throw "Zipping failed" }
}

if (-not ($OnlyFolder -or $NoInstaller)) {
    Write-Host "Building installer..."
    $env:FAIR_SORTING_PAYLOAD = $payload
    Invoke-Build $setupSpec $stage (Join-Path $work "setup")
    Remove-Item Env:\FAIR_SORTING_PAYLOAD -ErrorAction SilentlyContinue
}

Write-Host "Publishing to dist..."
$appOut = Join-Path $dist "app-folder"
$singleOut = Join-Path $dist "single-exe\fair sorting.exe"
$setupOut = Join-Path $dist "Fair Sorting Setup.exe"
$zipOut = Join-Path $dist "fair-sorting-app.zip"
[void](Publish-Folder $stagedFolder $appOut)
if (-not $OnlyFolder) { [void](Publish-File (Join-Path $stage "single-exe\fair sorting.exe") $singleOut) }
if (-not ($OnlyFolder -or $NoInstaller)) { [void](Publish-File (Join-Path $stage "Fair Sorting Setup.exe") $setupOut) }
if ($Zip) { [void](Publish-File $payload $zipOut) }

Write-Host ""
Write-Host "Build complete."
Write-Host ("App folder : {0} ({1} MB)" -f $appOut, (Get-SizeMb $stagedFolder))
if (-not $OnlyFolder) { Write-Host ("Single exe : {0} ({1} MB)" -f $singleOut, (Get-SizeMb (Join-Path $stage "single-exe\fair sorting.exe"))) }
if (-not ($OnlyFolder -or $NoInstaller)) { Write-Host ("Installer  : {0} ({1} MB)" -f $setupOut, (Get-SizeMb (Join-Path $stage "Fair Sorting Setup.exe"))) }
if ($Zip) { Write-Host ("Zip        : {0} ({1} MB)" -f $zipOut, (Get-SizeMb $payload)) }
foreach ($warning in $warnings) { Write-Warning $warning }
