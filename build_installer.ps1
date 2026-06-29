[CmdletBinding()]
param(
    [switch]$ForceRuntime,
    [switch]$ForceExe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InnoScript = Join-Path $ProjectDir "installer.iss"
$ExePath = Join-Path $ProjectDir "dist\C2VideoDownloader.exe"
$RuntimeYtDlp = Join-Path $ProjectDir "runtime_seed\yt-dlp.exe"
$RuntimeDeno = Join-Path $ProjectDir "runtime_seed\deno.exe"
$InstallerDir = Join-Path $ProjectDir "installer"
$InstallerPath = Join-Path $InstallerDir "C2VideoDownloaderSetup.exe"

Set-Location $ProjectDir

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-ProjectScript {
    param([Parameter(Mandatory)][string]$ScriptName)

    $ScriptPath = Join-Path $ProjectDir $ScriptName
    if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
        throw "Script obrigatorio nao encontrado: $ScriptPath"
    }

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $ScriptPath
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao executar $ScriptName. Codigo de saida: $LASTEXITCODE"
    }
}

function Resolve-InnoCompiler {
    $Command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    $Candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )

    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            return $Candidate
        }
    }

    throw "Inno Setup 6 nao encontrado. Instale o Inno Setup 6 e execute COMPILAR_INSTALADOR.cmd novamente."
}

try {
    Write-Host "C2 Video Downloader - compilacao completa" -ForegroundColor Green
    Write-Host "Projeto: $ProjectDir"

    $RuntimeMissing = -not (Test-Path -LiteralPath $RuntimeYtDlp -PathType Leaf) -or
                      -not (Test-Path -LiteralPath $RuntimeDeno -PathType Leaf)

    if ($ForceRuntime -or $RuntimeMissing) {
        Write-Step "Preparando yt-dlp e Deno"
        Invoke-ProjectScript "prepare_runtime.ps1"
    }
    else {
        Write-Step "Runtime ja preparado"
        Write-Host $RuntimeYtDlp
        Write-Host $RuntimeDeno
    }

    if ($ForceExe -or -not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
        Write-Step "Gerando C2VideoDownloader.exe com PyInstaller"
        Invoke-ProjectScript "build_exe.ps1"
    }
    else {
        Write-Step "Executavel ja existente"
        Write-Host $ExePath
    }

    if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
        throw "O executavel nao foi gerado: $ExePath"
    }
    if (-not (Test-Path -LiteralPath $RuntimeYtDlp -PathType Leaf)) {
        throw "Componente nao encontrado: $RuntimeYtDlp"
    }
    if (-not (Test-Path -LiteralPath $RuntimeDeno -PathType Leaf)) {
        throw "Componente nao encontrado: $RuntimeDeno"
    }

    Write-Step "Compilando o instalador com Inno Setup"
    $IsccPath = Resolve-InnoCompiler

    $AppConfig = Get-Content -LiteralPath (Join-Path $ProjectDir "app_config.py") -Raw
    $VersionMatch = [regex]::Match($AppConfig, 'APP_VERSION\s*=\s*"([^"]+)"')
    if (-not $VersionMatch.Success) {
        throw "APP_VERSION nao encontrado em app_config.py."
    }
    $AppVersion = $VersionMatch.Groups[1].Value

    New-Item -ItemType Directory -Force -Path $InstallerDir | Out-Null
    & $IsccPath "/DMyAppVersion=$AppVersion" $InnoScript
    if ($LASTEXITCODE -ne 0) {
        throw "O Inno Setup encerrou com codigo $LASTEXITCODE."
    }

    if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
        throw "O instalador nao foi gerado: $InstallerPath"
    }

    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerPath
    $HashLine = "$($Hash.Hash.ToLower())  C2VideoDownloaderSetup.exe"
    Set-Content -LiteralPath (Join-Path $InstallerDir "SHA256SUMS.txt") -Value $HashLine -Encoding ASCII

    Write-Host ""
    Write-Host "COMPILACAO CONCLUIDA" -ForegroundColor Green
    Write-Host "Instalador: $InstallerPath"
    Write-Host "SHA-256: $($Hash.Hash)"
}
catch {
    Write-Host ""
    Write-Host "FALHA NA COMPILACAO" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Nao abra installer.iss diretamente. Execute COMPILAR_INSTALADOR.cmd." -ForegroundColor Yellow
    exit 1
}
