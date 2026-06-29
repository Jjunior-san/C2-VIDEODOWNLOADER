$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeDir = Join-Path $ProjectDir "runtime_seed"
$YtDlpPath = Join-Path $RuntimeDir "yt-dlp.exe"
$DenoArchive = Join-Path $RuntimeDir "deno.zip"
$DenoPath = Join-Path $RuntimeDir "deno.exe"

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null

Write-Host "Baixando yt-dlp nightly oficial..."
Invoke-WebRequest `
    -Uri "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.exe" `
    -OutFile $YtDlpPath `
    -UseBasicParsing

Write-Host "Baixando Deno oficial..."
Invoke-WebRequest `
    -Uri "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" `
    -OutFile $DenoArchive `
    -UseBasicParsing

$DenoTemp = Join-Path $RuntimeDir "deno_extract"
Remove-Item -Recurse -Force $DenoTemp -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath $DenoArchive -DestinationPath $DenoTemp -Force
$ExtractedDeno = Get-ChildItem -Path $DenoTemp -Recurse -Filter "deno.exe" | Select-Object -First 1
if (-not $ExtractedDeno) {
    throw "deno.exe nao encontrado no pacote baixado."
}
Copy-Item -LiteralPath $ExtractedDeno.FullName -Destination $DenoPath -Force
Remove-Item -LiteralPath $DenoArchive -Force
Remove-Item -Recurse -Force $DenoTemp

Write-Host "Validando componentes..."
& $YtDlpPath --version
& $YtDlpPath --list-impersonate-targets
& $DenoPath --version

Write-Host "Runtime preparado em: $RuntimeDir" -ForegroundColor Green
