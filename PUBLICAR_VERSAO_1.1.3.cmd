@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title C2 Video Downloader - Publicar versao 1.1.3

echo ============================================================
echo   C2 VIDEO DOWNLOADER - PUBLICAR VERSAO 1.1.3
echo ============================================================
echo.
echo Este processo envia o codigo ao GitHub e inicia a compilacao
echo automatica do instalador pelo GitHub Actions.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_github.ps1"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo A publicacao falhou. Leia a mensagem acima.
) else (
  echo Publicacao concluida. O GitHub Actions esta compilando o instalador.
  start "" "https://github.com/Jjunior-san/C2-VIDEODOWNLOADER/actions"
)
echo.
pause
exit /b %EXITCODE%
