@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title C2 Video Downloader - Compilar instalador

cls
echo ============================================================
echo   C2 VIDEO DOWNLOADER - COMPILACAO COMPLETA
echo ============================================================
echo.
echo Este processo ira:
echo   1. Baixar yt-dlp e Deno
echo   2. Criar/atualizar o ambiente Python
echo   3. Gerar dist\C2VideoDownloader.exe
echo   4. Gerar installer\C2VideoDownloaderSetup.exe
echo.

echo Nao feche esta janela durante a compilacao.
echo.

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo ERRO: PowerShell nao encontrado.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_installer.ps1"
set "RESULT=%ERRORLEVEL%"

echo.
if not "%RESULT%"=="0" (
    echo ============================================================
    echo   A COMPILACAO FALHOU
    echo ============================================================
    echo Consulte a mensagem acima.
    echo Nao compile installer.iss diretamente.
    pause
    exit /b %RESULT%
)

echo ============================================================
echo   INSTALADOR GERADO COM SUCESSO
echo ============================================================
echo.
echo Arquivo:
echo %~dp0installer\C2VideoDownloaderSetup.exe
echo.
pause
exit /b 0
