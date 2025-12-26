param(
  [string]$PythonExe = ".\venv\Scripts\python.exe",
  [string]$OutDir = ".\release-out"
)

$ErrorActionPreference = "Stop"

Write-Host "Installing build deps..."
& $PythonExe -m pip install -r requirements.txt -r requirements-dev.txt

Write-Host "Building EXEs (PyInstaller)..."
& $PythonExe -m PyInstaller RunBot.spec
& $PythonExe -m PyInstaller Setup.spec

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Path $OutDir | Out-Null

function Copy-IfExists([string]$Path, [string]$Dest) {
  if (Test-Path $Path) { Copy-Item $Path $Dest -Force }
}

# Support one-file or one-folder outputs
Copy-IfExists ".\dist\RunBot.exe" $OutDir
Copy-IfExists ".\dist\Setup.exe" $OutDir

if (Test-Path ".\dist\RunBot\RunBot.exe") { Copy-Item ".\dist\RunBot\RunBot.exe" $OutDir -Force }
if (Test-Path ".\dist\Setup\Setup.exe") { Copy-Item ".\dist\Setup\Setup.exe" $OutDir -Force }

Copy-Item ".\config.yaml.example" $OutDir -Force
Copy-Item ".\README.md" $OutDir -Force

New-Item -ItemType Directory -Path (Join-Path $OutDir "assets\icons") -Force | Out-Null
Copy-Item ".\assets\icons\README.txt" (Join-Path $OutDir "assets\icons") -Force
Copy-IfExists ".\assets\icons\default_mute.png" (Join-Path $OutDir "assets\icons")
Copy-IfExists ".\assets\icons\default_deaf.png" (Join-Path $OutDir "assets\icons")

Write-Host "Creating zip..."
$zipPath = Join-Path $OutDir "PNGTuberBot-windows.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $OutDir "*") -DestinationPath $zipPath

Write-Host "Done. Release bundle:"
Write-Host "  $zipPath"


