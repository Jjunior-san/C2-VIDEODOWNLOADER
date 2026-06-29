$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repository = "https://github.com/Jjunior-san/C2-VIDEODOWNLOADER.git"
Set-Location $ProjectDir

if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    throw "Git nao encontrado. Instale o Git for Windows antes de publicar."
}

if (Get-Command gh.exe -ErrorAction SilentlyContinue) {
    & gh.exe auth status
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Entre na sua conta do GitHub:" -ForegroundColor Yellow
        & gh.exe auth login --web
    }
}

if (-not (Test-Path (Join-Path $ProjectDir ".git"))) {
    & git init
}

& git config user.name "Jjunior-san"
if (-not (& git config user.email)) {
    & git config user.email "jjunior.ssan@gmail.com"
}

$CurrentRemote = (& git remote get-url origin 2>$null)
if ($LASTEXITCODE -ne 0) {
    & git remote add origin $Repository
} elseif ($CurrentRemote -ne $Repository) {
    & git remote set-url origin $Repository
}

& git add --all
$HasChanges = & git status --porcelain
if ($HasChanges) {
    & git commit -m "feat: automatic app and dependency updates"
}

& git branch -M main
& git push -u origin main

Write-Host ""
Write-Host "Projeto publicado." -ForegroundColor Green
Write-Host "Acompanhe o build em: https://github.com/Jjunior-san/C2-VIDEODOWNLOADER/actions"
Write-Host "Instalador permanente: https://github.com/Jjunior-san/C2-VIDEODOWNLOADER/releases/latest/download/C2VideoDownloaderSetup.exe"
