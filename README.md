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

> **Não abra `installer.iss` e clique em Compile diretamente.** Esse arquivo é apenas a etapa final e exige que `dist\C2VideoDownloader.exe`, `runtime_seed\yt-dlp.exe` e `runtime_seed\deno.exe` já existam.

A forma recomendada é dar dois cliques em:

```text
COMPILAR_INSTALADOR.cmd
```

Ou executar no PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_installer.ps1
```

O script executa automaticamente todas as etapas na ordem correta.

Pré-requisitos:

- Windows 10/11 64 bits;
- Python 3.13 ou superior;
- Inno Setup 6.

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

## Compatibilidade dos vídeos

A partir da versão **1.1.2**, o formato padrão é **Melhor MP4 compatível**. O aplicativo:

- prioriza vídeo H.264/AVC e áudio AAC/M4A durante a seleção do `yt-dlp`;
- identifica o caminho final devolvido pelo `yt-dlp` após a pós-produção;
- verifica o codec real do arquivo, não apenas a extensão `.mp4`;
- converte automaticamente VP9, AV1, HEVC e áudio HE-AAC para MP4 com H.264, AAC-LC e `yuv420p` quando necessário;
- aplica `faststart` para melhorar a abertura do vídeo em players, navegadores, TVs e aplicativos de mensagens.

A conversão somente acontece quando o arquivo não está em um perfil amplamente compatível. Arquivos H.264/AAC adequados não são recodificados.

## Preferências do usuário

A última pasta de download, o formato escolhido, a opção de playlist/álbum e o navegador de cookies são gravados em:

```text
%LOCALAPPDATA%\C2 Sistemas\C2 Video Downloader\settings.json
```

A pasta selecionada é salva imediatamente ao usar **Escolher**, novamente ao iniciar um download e ao fechar o aplicativo.

## Versão 1.1.3

- usa no cabeçalho a identidade visual do site **c2sistemas.com**;
- corrige a imagem que ficava congelada em `C_`;
- reproduz no aplicativo o efeito de digitação de **SISTEMAS**, com cursor e slogan;
- mantém as correções de MP4 H.264/AAC e memorização da última pasta.
