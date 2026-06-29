$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InnoScript = Join-Path $ProjectDir "installer.iss"
$ExePath = Join-Path $ProjectDir "dist\C2VideoDownloader.exe"
$RuntimeYtDlp = Join-Path $ProjectDir "runtime_seed\yt-dlp.exe"
$RuntimeDeno = Join-Path $ProjectDir "runtime_seed\deno.exe"

Set-Location $ProjectDir

if (-not (Test-Path $RuntimeYtDlp) -or -not (Test-Path $RuntimeDeno)) {
    & (Join-Path $ProjectDir "prepare_runtime.ps1")
}

if (-not (Test-Path $ExePath)) {
    & (Join-Path $ProjectDir "build_exe.ps1")
}

$Iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if (-not $Iscc) {
    $CommonPaths = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    foreach ($Path in $CommonPaths) {
        if ($Path -and (Test-Path $Path)) {
            $Iscc = Get-Item $Path
            break
        }
    }
}

if (-not $Iscc) {
    throw "Inno Setup 6 nao encontrado. Instale-o e execute novamente."
}

$IsccPath = if ($Iscc.Source) { $Iscc.Source } else { $Iscc.FullName }
$AppConfig = Get-Content -LiteralPath (Join-Path $ProjectDir "app_config.py") -Raw
$VersionMatch = [regex]::Match($AppConfig, 'APP_VERSION\s*=\s*"([^"]+)"')
if (-not $VersionMatch.Success) {
    throw "APP_VERSION nao encontrado em app_config.py."
}
$AppVersion = $VersionMatch.Groups[1].Value
& $IsccPath "/DMyAppVersion=$AppVersion" $InnoScript

$InstallerPath = Join-Path $ProjectDir "installer\C2VideoDownloaderSetup.exe"
if (-not (Test-Path $InstallerPath)) {
    throw "O instalador nao foi gerado."
}

$Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerPath
$HashLine = "$($Hash.Hash.ToLower())  C2VideoDownloaderSetup.exe"
Set-Content -LiteralPath (Join-Path $ProjectDir "installer\SHA256SUMS.txt") -Value $HashLine -Encoding ASCII

Write-Host ""
Write-Host "Instalador gerado em:" -ForegroundColor Green
Write-Host $InstallerPath
Write-Host "SHA-256: $($Hash.Hash)"
