# Análise e correções aplicadas

## Problemas encontrados no projeto original

1. O instalador Inno Setup apontava para `Program Files`, mas usava `PrivilegesRequired=lowest`. Isso podia redirecionar ou impedir a gravação no diretório desejado.
2. O instalador alternativo `install.ps1` usava `%LOCALAPPDATA%\Programs`, divergindo do caminho oficial solicitado.
3. O `yt-dlp` era importado como módulo Python e congelado dentro do EXE do PyInstaller. Depois da compilação, atualizar o pacote com `pip` no computador do usuário não altera o conteúdo interno do EXE.
4. O build não incluía corretamente o suporte de impersonação necessário a alguns sites, incluindo cenários recentes do Instagram.
5. Não havia runtime JavaScript disponível para os componentes remotos do `yt-dlp`.
6. Não havia mecanismo para atualizar o próprio aplicativo ou suas dependências depois da instalação.
7. A mensagem de conclusão podia aparecer mesmo quando algum download falhava.
8. Não havia automação de build e publicação de releases.

## Solução implementada

### Instalação

- Caminho fixado em `C:\Program Files\C2 Sistemas\C2 Video Downloader`.
- Instalação administrativa com Inno Setup.
- Atualização sobre a instalação anterior usando o mesmo `AppId`.
- Registro em `App Paths`, atalhos e suporte de fechamento do aplicativo durante upgrades.

### Dependências atualizáveis

O aplicativo principal continua instalado em `Program Files`, mas os componentes mutáveis são copiados para:

```text
%LOCALAPPDATA%\C2 Sistemas\C2 Video Downloader\runtime
```

Esse desenho evita solicitar elevação administrativa para cada atualização de componente.

Componentes gerenciados:

- `yt-dlp.exe`: canal nightly, atualização diária por meio do atualizador oficial do próprio binário;
- `deno.exe`: consulta semanal ao release oficial e substituição atômica quando houver versão mais nova.

O instalador inclui cópias iniciais dos dois componentes para permitir a primeira execução mesmo antes da atualização online.

### Canal interno pelo GitHub

O programa consulta:

```text
https://raw.githubusercontent.com/Jjunior-san/C2-VIDEODOWNLOADER/main/update-manifest.json
```

Esse manifesto controla os canais, URLs e intervalos de atualização. O próprio aplicativo consulta os releases deste repositório para atualizar o instalador.

Link permanente do instalador mais recente:

```text
https://github.com/Jjunior-san/C2-VIDEODOWNLOADER/releases/latest/download/C2VideoDownloaderSetup.exe
```

### Publicação

O workflow do GitHub Actions:

1. executa os testes;
2. instala o Inno Setup;
3. baixa os componentes oficiais atuais;
4. compila o aplicativo;
5. gera o instalador e o SHA-256;
6. cria ou atualiza o GitHub Release da versão definida em `app_config.py`.

## Arquivos principais

- `app_config.py`: versão, nome e URLs oficiais;
- `c2_update.py`: manifesto, componentes e atualização do aplicativo;
- `youtube_downloader_app.py`: interface e execução do motor externo;
- `update-manifest.json`: política remota de atualizações;
- `prepare_runtime.ps1`: prepara `yt-dlp` e Deno para o instalador;
- `installer.iss`: instalação administrativa em Program Files;
- `.github/workflows/build-release.yml`: build e release automáticos;
- `publish_github.ps1`: primeira publicação do projeto.


## Correção 1.1.1 — ordem de compilação

O arquivo `installer.iss` não deve ser compilado antes da geração de `dist\C2VideoDownloader.exe`. A versão 1.1.1 adiciona `COMPILAR_INSTALADOR.cmd`, reforça as validações dos scripts e apresenta uma mensagem objetiva quando uma etapa anterior falha.

## Correção 1.1.2 — compatibilidade de vídeo e preferências

O arquivo de exemplo do Instagram estava em contêiner MP4, mas usava vídeo **VP9** e áudio **HE-AAC**. A extensão `.mp4` não garante compatibilidade de codec; por isso alguns players reproduziam somente o áudio.

A versão 1.1.2 aplica duas camadas de correção:

1. prioriza H.264/AVC com AAC/M4A na seleção de formatos;
2. depois do download, obtém o caminho definitivo com `--print after_move:filepath`, verifica os streams com FFmpeg e converte apenas os arquivos incompatíveis para H.264 High, `yuv420p`, AAC-LC e MP4 com `faststart`.

Também foi criado um arquivo persistente de preferências em `%LOCALAPPDATA%\C2 Sistemas\C2 Video Downloader\settings.json`. A última pasta é restaurada na próxima abertura e salva ao selecionar a pasta, iniciar o download e fechar o aplicativo.

O erro HTTP 404 da consulta de atualização do aplicativo também passa a ser tratado como ausência normal de um GitHub Release, sem exibir uma falha genérica.

## 9. Identidade visual do site — versão 1.1.3

A imagem anterior havia sido capturada durante o primeiro quadro da animação do site e, por isso, mostrava apenas o símbolo `C_`. A versão 1.1.3 separa o símbolo oficial do cursor congelado e reproduz no próprio Tkinter o efeito de digitação de `SISTEMAS`, incluindo o expoente 2 e o slogan. Assim a logo permanece nítida, leve e animada sem depender de navegador ou GIF externo.
