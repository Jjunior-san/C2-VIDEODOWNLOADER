# C² - Video Downloader

Aplicativo Windows da **C2 Sistemas** para baixar vídeos, posts, reels, playlists e mídias de sites compatíveis com o `yt-dlp`.

> Use apenas para conteúdo próprio, livre ou com autorização do titular.

## Instalação

O instalador oficial é publicado automaticamente em **GitHub Releases**:

```text
https://github.com/Jjunior-san/C2-VIDEODOWNLOADER/releases/latest/download/C2VideoDownloaderSetup.exe
```

O caminho padrão e obrigatório da instalação é:

```text
C:\Program Files\C2 Sistemas\C2 Video Downloader
```

## Atualização automática

O programa possui dois níveis de atualização:

1. **Aplicativo:** consulta o release mais recente deste repositório, baixa o instalador, valida SHA-256 quando disponibilizado pela API do GitHub e solicita elevação do Windows para atualizar em `Program Files`.
2. **Componentes:** mantém cópias atualizáveis em `%LOCALAPPDATA%\C2 Sistemas\C2 Video Downloader\runtime`:
   - `yt-dlp.exe` no canal nightly, verificado a cada 24 horas;
   - `deno.exe`, verificado semanalmente.

As regras ficam centralizadas no arquivo [`update-manifest.json`](update-manifest.json). Assim, intervalos, URLs e canais podem ser alterados neste repositório sem recompilar o aplicativo.

O instalador também leva uma cópia inicial dos componentes em:

```text
C:\Program Files\C2 Sistemas\C2 Video Downloader\runtime
```

Na primeira execução, o programa copia esses arquivos para a área gravável do usuário e passa a atualizá-los sem exigir privilégios administrativos.

## Compilar localmente

Pré-requisitos:

- Windows 10/11 64 bits;
- Python 3.13 ou superior;
- Inno Setup 6.

No PowerShell:

```powershell
.\build_installer.ps1
```

Saída:

```text
installer\C2VideoDownloaderSetup.exe
installer\SHA256SUMS.txt
```

## Publicação automática

O workflow `.github/workflows/build-release.yml` é executado a cada envio para `main`. Ele:

- baixa as versões atuais do `yt-dlp` nightly e do Deno;
- gera o EXE com PyInstaller;
- cria o instalador com Inno Setup;
- publica ou atualiza o release correspondente ao `APP_VERSION`;
- mantém o link permanente `/releases/latest/download/C2VideoDownloaderSetup.exe`.

Para publicar uma nova versão, altere apenas `APP_VERSION` em `app_config.py` e envie para `main`. O script de build repassa automaticamente essa versão ao Inno Setup.

## Primeira publicação do repositório

Caso o repositório ainda esteja vazio, instale o Git for Windows e, preferencialmente, o GitHub CLI. Depois execute:

```powershell
.\publish_github.ps1
```

O envio para `main` inicia automaticamente o workflow de compilação e publicação do instalador.
