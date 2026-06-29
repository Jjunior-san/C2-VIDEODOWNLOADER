$ErrorActionPreference = "Stop"

$CurrentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($CurrentIdentity)
$IsAdmin = $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath)
    )
    exit
}

$AppName = "C² - Video Downloader"
$ExeName = "C2VideoDownloader.exe"
$InstallDir = Join-Path $env:ProgramFiles "C2 Sistemas\C2 Video Downloader"
$RuntimeDir = Join-Path $InstallDir "runtime"

New-Item -ItemType Directory -Force -Path $InstallDir, $RuntimeDir | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot $ExeName) -Destination (Join-Path $InstallDir $ExeName) -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "yt-dlp.exe") -Destination (Join-Path $RuntimeDir "yt-dlp.exe") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "deno.exe") -Destination (Join-Path $RuntimeDir "deno.exe") -Force

$Shell = New-Object -ComObject WScript.Shell
$StartMenuDir = Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs\C2 Sistemas"
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
$Shortcut = $Shell.CreateShortcut((Join-Path $StartMenuDir "$AppName.lnk"))
$Shortcut.TargetPath = Join-Path $InstallDir $ExeName
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.IconLocation = Join-Path $InstallDir $ExeName
$Shortcut.Save()

Start-Process -FilePath (Join-Path $InstallDir $ExeName)
