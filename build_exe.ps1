$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$Python = if (Get-Command "py.exe" -ErrorAction SilentlyContinue) { "py.exe" } else { "python.exe" }

Set-Location $ProjectDir

if (-not (Test-Path $VenvDir)) {
    & $Python -m venv $VenvDir
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip wheel setuptools
& $VenvPython -m pip install --upgrade -r requirements.txt

Remove-Item -Recurse -Force (Join-Path $ProjectDir "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectDir "dist") -ErrorAction SilentlyContinue

& $VenvPython -m PyInstaller --clean "C2VideoDownloader.spec"

$ExePath = Join-Path $ProjectDir "dist\C2VideoDownloader.exe"
if (-not (Test-Path $ExePath)) {
    throw "O executavel nao foi gerado."
}

Write-Host ""
Write-Host "EXE gerado em:" -ForegroundColor Green
Write-Host $ExePath
