$ErrorActionPreference = "Stop"

# No PowerShell 7, comandos nativos que escrevem em STDERR podem ser
# convertidos em erros do PowerShell. O script valida $LASTEXITCODE de forma
# explicita, portanto desabilitamos esse comportamento durante a publicacao.
$PreviousNativeErrorPreference = $null
$HasNativeErrorPreference = Test-Path Variable:PSNativeCommandUseErrorActionPreference
if ($HasNativeErrorPreference) {
    $PreviousNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
}

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repository = "https://github.com/Jjunior-san/C2-VIDEODOWNLOADER.git"

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments,

        [string] $FailureMessage = "O comando Git falhou."
    )

    & git.exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Codigo de saida: $LASTEXITCODE."
    }
}

try {
    Set-Location $ProjectDir

    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
        throw "Git nao encontrado. Instale o Git for Windows antes de publicar."
    }

    Write-Host "=== Preparando repositorio local ===" -ForegroundColor Cyan

    if (-not (Test-Path (Join-Path $ProjectDir ".git"))) {
        Invoke-Git -Arguments @("init") -FailureMessage "Nao foi possivel inicializar o repositorio."
    }

    # Configuracao local do repositorio. Nao altera a configuracao global do Git.
    Invoke-Git -Arguments @("config", "user.name", "Jjunior-san") `
        -FailureMessage "Nao foi possivel configurar o nome do autor."
    Invoke-Git -Arguments @("config", "user.email", "jjunior.ssan@gmail.com") `
        -FailureMessage "Nao foi possivel configurar o e-mail do autor."

    # Nao executamos 'git remote get-url origin' antes de confirmar que o remoto existe.
    $RemoteNames = @(& git.exe remote)
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel consultar os remotos do repositorio."
    }

    if ($RemoteNames -notcontains "origin") {
        Write-Host "Adicionando remoto origin..."
        Invoke-Git -Arguments @("remote", "add", "origin", $Repository) `
            -FailureMessage "Nao foi possivel adicionar o remoto origin."
    }
    else {
        $RemoteOutput = @(& git.exe remote get-url origin)
        if ($LASTEXITCODE -ne 0) {
            throw "O remoto origin existe, mas sua URL nao pôde ser consultada."
        }

        $CurrentRemote = ([string]::Join("", $RemoteOutput)).Trim()
        if ($CurrentRemote -ne $Repository) {
            Write-Host "Atualizando URL do remoto origin..."
            Invoke-Git -Arguments @("remote", "set-url", "origin", $Repository) `
                -FailureMessage "Nao foi possivel atualizar o remoto origin."
        }
    }

    Write-Host "=== Criando commit ===" -ForegroundColor Cyan

    Invoke-Git -Arguments @("add", "--all") `
        -FailureMessage "Nao foi possivel adicionar os arquivos ao Git."

    $StatusLines = @(& git.exe status --porcelain=v1)
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel consultar o estado dos arquivos."
    }

    if ($StatusLines.Count -gt 0) {
        Invoke-Git -Arguments @("commit", "-m", "release: C2 Video Downloader 1.1.3") `
            -FailureMessage "Nao foi possivel criar o commit inicial."
    }
    else {
        Write-Host "Nenhuma alteracao nova para registrar."
    }

    Invoke-Git -Arguments @("branch", "-M", "main") `
        -FailureMessage "Nao foi possivel definir a branch main."

    Write-Host "=== Publicando no GitHub ===" -ForegroundColor Cyan
    & git.exe push -u origin main
    if ($LASTEXITCODE -ne 0) {
        throw @"
Nao foi possivel enviar os arquivos ao GitHub.

Verifique se sua conta esta autenticada no Git Credential Manager. Como alternativa, execute:
    gh auth login --web

Depois execute novamente:
    .\publish_github.ps1
"@
    }

    Write-Host ""
    Write-Host "Projeto publicado com sucesso." -ForegroundColor Green
    Write-Host "Build: https://github.com/Jjunior-san/C2-VIDEODOWNLOADER/actions"
    Write-Host "Instalador: https://github.com/Jjunior-san/C2-VIDEODOWNLOADER/releases/latest/download/C2VideoDownloaderSetup.exe"
}
finally {
    if ($HasNativeErrorPreference) {
        $PSNativeCommandUseErrorActionPreference = $PreviousNativeErrorPreference
    }
}
