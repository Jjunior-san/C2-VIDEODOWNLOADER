Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$SpecPath = Join-Path $ProjectDir "C2VideoDownloader.spec"
$ExePath = Join-Path $ProjectDir "dist\C2VideoDownloader.exe"

Set-Location $ProjectDir

$PythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
if (-not $PythonLauncher) {
    $PythonLauncher = Get-Command "python.exe" -ErrorAction SilentlyContinue
}
if (-not $PythonLauncher) {
    throw "Python nao encontrado. Instale Python 3.13 ou superior e habilite a opcao Add Python to PATH."
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Host "Criando ambiente virtual..." -ForegroundColor Cyan
    & $PythonLauncher.Source -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel criar o ambiente virtual."
    }
}

Write-Host "Atualizando ferramentas de compilacao..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade pip wheel setuptools
if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar pip, wheel e setuptools." }

Write-Host "Instalando dependencias do projeto..." -ForegroundColor Cyan
& $VenvPython -m pip install --upgrade -r (Join-Path $ProjectDir "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependencias do projeto." }

Remove-Item -Recurse -Force (Join-Path $ProjectDir "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $ProjectDir "dist") -ErrorAction SilentlyContinue

Write-Host "Executando PyInstaller..." -ForegroundColor Cyan
& $VenvPython -m PyInstaller --clean --noconfirm $SpecPath
if ($LASTEXITCODE -ne 0) { throw "PyInstaller encerrou com codigo $LASTEXITCODE." }

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "O executavel nao foi gerado: $ExePath"
}

Write-Host ""
Write-Host "EXE gerado em:" -ForegroundColor Green
Write-Host $ExePath
