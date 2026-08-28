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

## Vídeos do Kanal D

A partir da versão **1.3.0**, o aplicativo reconhece páginas de episódios e
clipes de `kanald.com.tr`. O resolvedor lê a fonte HLS oficial publicada na
página e preserva o título do conteúdo no nome do arquivo.

O exemplo abaixo pode ser colado diretamente no campo de URLs:

```text
https://www.kanald.com.tr/uzak-sehir/bolumler/uzak-sehir-22-bolum
```

As opções de qualidade da interface continuam válidas. No episódio acima, o
site disponibiliza versões de 360p, 480p, 720p e 1080p. O resolvedor aceita
somente fontes HTTPS de domínios de mídia conhecidos do Kanal D/Dailymotion e
não tenta contornar DRM ou bloqueios geográficos.

Listas de temporada também são aceitas. Mantenha **Baixar playlist/álbum**
marcado para baixar todos os episódios retornados pela página, na ordem em que
ela os apresenta. Os arquivos recebem numeração sequencial. Como as fontes HLS
do Kanal D são públicas, cookies selecionados para outros sites são ignorados
nesse fluxo; isso evita falhas quando o Chrome está aberto.

## Categorias de vídeos do JW.ORG

A partir da versão **1.2.0**, o aplicativo reconhece endereços de categorias em português como:

```text
https://www.jw.org/pt/biblioteca/videos/#pt/categories/StudioMonthlyPrograms
```

Para baixar a lista:

1. cole o endereço da categoria no campo de URLs;
2. mantenha **Baixar playlist/álbum** marcado para também percorrer subcategorias;
3. escolha a qualidade desejada;
4. selecione a pasta e clique em **Baixar**.

O suporte especial ao JW.ORG:

- consulta o catálogo público oficial de mídia;
- aceita **Melhor qualidade**, **Melhor MP4 compatível**, 1080p, 720p, 480p e 360p;
- pode gerar áudio M4A;
- usa títulos legíveis e numeração sequencial nos arquivos;
- ignora arquivos completos que já estejam na pasta;
- tenta novamente downloads interrompidos ou com tamanho incorreto;
- remove mídias duplicadas encontradas em subcategorias.

Atualmente, a detecção automática de categorias está configurada para endereços em português do Brasil. Links individuais do JW.ORG continuam sendo processados pelo fluxo normal do `yt-dlp`.

## Preferências do usuário

A última pasta de download, o formato escolhido, a opção de playlist/álbum e o navegador de cookies são gravados em:

```text
%LOCALAPPDATA%\C2 Sistemas\C2 Video Downloader\settings.json
```

A pasta selecionada é salva imediatamente ao usar **Escolher**, novamente ao iniciar um download e ao fechar o aplicativo.

## Versão 1.2.0

- adiciona download em lote de categorias de vídeos do JW.ORG em português;
- percorre subcategorias quando a opção de playlist/álbum está ativa;
- seleciona automaticamente a versão correspondente à qualidade escolhida;
- mantém o fluxo atual para YouTube, Instagram, Facebook, TikTok e demais sites;
- inclui testes automatizados de reconhecimento de URL e seleção de resolução.

## Versão 1.3.0

- adiciona suporte nativo às páginas de vídeo do Kanal D;
- resolve o HLS oficial publicado no `VideoObject` da página;
- preserva título e identificador do conteúdo no nome do arquivo;
- inclui testes para domínio, metadados, fonte permitida e página inválida.

## Versão 1.3.1

- adiciona download de listas de temporada do Kanal D;
- numera os episódios na ordem apresentada pela página;
- não tenta copiar cookies do Chrome para fontes públicas do Kanal D;
- mantém cookies ativos para os demais sites que precisem de autenticação.

## Versão 1.3.2

- troca a animação contínua por progresso percentual durante os downloads;
- mostra tamanho recebido e total estimado do arquivo atual;
- exibe velocidade instantânea, velocidade média e ETA do arquivo;
- calcula a porcentagem e a previsão de término do trabalho completo;
- atualiza a estimativa global conforme cada item da playlist avança.

## Versão 1.3.3

- corrige a identificação de vídeos em páginas recentes do Kanal D;
- tolera blocos `VideoObject` malformados pelo próprio site;
- valida os episódios 37 e 38 de Uzak Şehir em HLS até 1080p.

## Versão 1.3.4

- corrige a ausência de progresso causada pela combinação de --print sem --progress;
- mostra recebido, total exato/estimado, velocidade atual/média em KB/s ou MB/s e tempo restante;
- baixa até 4 fragmentos HLS/DASH simultaneamente, ajustáveis para 1, 2, 4 ou 8;
- inclui o botão **Pausar / Continuar**, inclusive durante a finalização pelo FFmpeg;
- exclui o tempo pausado e os bytes já existentes da média de transferência;
- preserva vídeo/áudio compatíveis sem recodificação e mostra a etapa de finalização separadamente;
- preserva o arquivo original quando uma conversão falha e evita sobrescrever outro MP4;
- testa a linha de comando real do yt-dlp, a transferência paralela e a pausa/retomada.

### Desempenho e pausa

O número de fragmentos controla partes de um mesmo vídeo, não episódios simultâneos.
Comece com 4; use 1 ou 2 se o servidor limitar conexões ou ocorrerem falhas. A melhora
depende da conexão e do servidor. Em HLS, o tamanho total pode ser apenas estimado
e é refinado durante a transferência. A estimativa da fila não inclui com precisão
conversões futuras, pois os arquivos restantes ainda não foram analisados.

**Pausar** mantém a sessão e os arquivos abertos; **Continuar** retoma o trabalho.
Pausas longas podem exigir novas tentativas de conexão. Para preservar essa sessão,
mantenha o aplicativo aberto. Se sair, confirme a interrupção: os arquivos parciais
do yt-dlp ficam na pasta. Ao iniciar novamente com a mesma URL, formato e pasta,
o motor tenta continuar onde o servidor e o formato permitirem. Não há persistência
automática da fila entre aberturas.

Taxas são exibidas em bytes por segundo: KB/s e MB/s, não em kilobits por segundo (kbps).
