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

Na atualização do aplicativo, autorize o aviso do Windows/SmartScreen. O instalador
é executado em modo silencioso, grava o diagnóstico em
`%LOCALAPPDATA%\C2 Sistemas\C2 Video Downloader\installer-update.log` e reabre o
programa ao terminar, exibindo a versão instalada.

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
o motor tenta continuar onde o servidor e o formato permitirem. A partir da versão
1.4.0, a fila também é salva e recuperada automaticamente entre aberturas.

Taxas são exibidas em bytes por segundo: KB/s e MB/s, não em kilobits por segundo (kbps).

## Versão 1.3.5

- cabeçalho compacto, logo menor e remoção da lista de sites, slogan e textos repetidos;
- abas **Downloads** e **Configurações**, mantendo progresso, pausa e atividade juntos;
- janela inicial limitada à área útil do monitor, com rolagem em telas pequenas ou com escala de texto alta;
- playlists continuam depois de vídeos privados, removidos ou indisponíveis, com avisos na atividade;
- o YouTube não inclui na fila os itens indisponíveis que a própria playlist oculta;
- os arquivos baixados de uma playlist parcial também passam pela finalização de compatibilidade;
- o resumo diferencia conclusão parcial, falha total e ausência de arquivos disponíveis;
- testes com a CLI real do yt-dlp para uma playlist com dois vídeos válidos, um privado e um removido, além de testes de layout em diferentes tamanhos e escalas.

As opções de cookies, fragmentos simultâneos e atualização agora ficam em **Configurações**.
O aplicativo usa as opções da interface, sem carregar configurações externas do yt-dlp.
Vídeos não listados, mas acessíveis pelo link, continuam elegíveis. Conteúdos privados ou
removidos não são desbloqueados. Falhas reais de rede ou conversão permanecem no registro
para nova tentativa; fragmentos ausentes de um vídeo não são ignorados para produzir um
arquivo incompleto.

## Versão 1.9.0 — área de trabalho contextual e pesquisa mais rápida

- mantém duas conexões de catálogo reutilizáveis e cacheia pesquisas Deezer por 10 minutos;
- reduz a espera antes da pesquisa e informa o tempo da consulta;
- mostra a fila e o progresso diretamente nas abas Música e Vídeo;
- mantém a aba atual depois de listar as mídias; a aba Fila passa a ser uma visão geral;
- substitui os textos dos controles do player por ícones com dicas ao passar o mouse;
- move as opções secundárias para uma janela compacta aberta pela engrenagem;
- permite salvar ou cancelar as alterações feitas na janela de configurações;
- torna as abas Música e Vídeo roláveis para preservar os controles em telas pequenas.

## Versão 1.8.3 — conclusão confiável da atualização interna

- corrige a atualização que instalava silenciosamente, mas não reabria o programa;
- identifica explicitamente a execução iniciada pelo atualizador no Inno Setup;
- mantém compatibilidade com a atualização silenciosa iniciada pelas versões antigas;
- salva as preferências antes de fechar o aplicativo para a instalação;
- registra o processo do instalador em `installer-update.log` para diagnóstico;
- reabre o aplicativo e confirma visualmente a versão instalada.

## Versão 1.8.1 — isolamento das pastas de Música e Vídeo

- corrige a troca de abas que copiava a pasta e o formato de Música sobre os de Vídeo, ou vice-versa;
- cada aba restaura sempre sua própria pasta e seu próprio formato ao ser selecionada;
- escolher a pasta da aba inativa não altera o destino atualmente ativo;
- filas recuperadas restauram somente as preferências do tipo de mídia correspondente;
- alterar a pasta ou o formato da aba inativa não impede a continuação de uma fila existente;
- pastas vazias usam padrões independentes (`Downloads` para Vídeo e `Music`/`Downloads\\Músicas` para Música);
- desacopla recursos gerais das dependências opcionais de autenticação;
- adiciona testes de regressão de interface, persistência, roteamento de arquivos e desempenho da fila.

## Versão 1.8.0 — controles de player estilo VLC, pastas e formatos separados e prévia de vídeo

- **Controles de player estilo VLC**: o botão de reproduzir agora alterna entre reprodução e pausa (`▶ Reproduzir` / `⏸ Pausar`), com botão dedicado `⏹ Parar` disponível tanto na tela de detalhes/pesquisa quanto na aba **Concluídos**;
- **Pastas separadas para música e vídeo**: agora é possível configurar um diretório de download exclusivo para arquivos de música e outro específico para vídeos, mantendo a organização de pastas independente;
- **Memorização de formatos separados**: os formatos e resoluções selecionados para downloads de música (ex: MP3, FLAC, M4A) e downloads de vídeo (ex: 1080p, Melhor qualidade) são salvos de forma independente e não se sobrescrevem ao alternar de aba;
- **Prévia automática com capa e título de vídeos**: ao colar ou digitar um link de vídeo na aba **Vídeo**, o aplicativo exibe instantaneamente um cartão com miniatura/capa em alta qualidade, título do vídeo e nome do canal/autor (via oEmbed ultra-rápido com fallback para o mecanismo do aplicativo);
- Atualização e expansão da suíte de testes automatizados com cobertura para todos os novos recursos.

## Versão 1.7.1 — correção no retorno de progresso Deezer

- corrige o encaminhamento de argumentos na emissão de progresso durante o download e decifração de faixas Deezer (`_report_direct_progress`);
- adiciona suporte a argumentos flexíveis para evitar exceções de argumentos inesperados.

## Versão 1.7.0 — autenticação Deezer com ARL e faixas completas

- adiciona seção de autenticação Deezer na aba **Configurações** com suporte a cookie `arl`;
- validação de login e identificação do plano da conta (HiFi / Lossless, Premium ou Gratuito) diretamente pela interface;
- permite download de faixas completas em FLAC Lossless (1411 kbps) ou MP3 (320 kbps / 128 kbps);
- descriptografia Blowfish em tempo real (`BF_CBC_STRIPE`) para fluxos de áudio protegidos;
- preserva o modo público como fallback seguro (quando sem ARL configurado);
- tagueamento nativo com metadados e capas em alta resolução para arquivos FLAC e MP3;
- adiciona opção para compactar automaticamente álbuns e playlists em arquivo `.zip` com playlist `.m3u8` inclusa;
- inclui testes automatizados para chave Blowfish, decodificação, persistência de preferências e geração de arquivo `.zip`.

## Versão 1.6.0 — pesquisa integrada no catálogo Deezer

- reorganiza a interface em abas principais **Música**, **Vídeo**, **Fila**, **Concluídos**, **Configurações** e **Atividade**, reduzindo a necessidade de rolagem;
- a aba **Música** faz pesquisa instantânea enquanto digita, com atraso curto para evitar consultas excessivas;
- permite escolher o escopo da pesquisa entre **Música**, **Artista**, **Álbum** e **Playlist**;
- resultados aparecem imediatamente com tipo, título, artista/álbum/autor, capa e ações para carregar, reproduzir prévia quando disponível ou abrir na Deezer;
- a aba **Música** concentra pesquisa/links da Deezer e restringe o seletor aos formatos de áudio;
- a aba **Vídeo** mantém os fluxos de YouTube, Kanal D, JW.ORG e demais sites, incluindo extração de áudio quando desejada;
- permite pesquisar faixas diretamente pelo campo principal usando `deezer: artista música`;
- consulta somente o catálogo público oficial e transforma os resultados em itens da fila existente;
- com **Baixar playlist/álbum** marcado, lista até 25 resultados para seleção; desmarcado, usa somente o primeiro resultado;
- mantém metadados, capa, formatos de áudio, taxa de bits, histórico, pausa e fila persistente já existentes;
- resultados sem prévia pública continuam visíveis como indisponíveis, sem interromper os demais;
- exibe capa, artista, álbum, faixa, disco e ano nos detalhes da música;
- permite editar metadados e trocar a capa de arquivos concluídos;
- reproduz arquivos locais e, antes do download, a prévia pública oficial da Deezer diretamente no aplicativo;
- permite organizar automaticamente a biblioteca em pasta raiz, `Artista` ou `Artista\\Álbum`;
- permite personalizar o nome do arquivo com `{faixa:02}`, `{titulo}`, `{artista}`, `{album}`, `{ano}` e `{id}`;
- não solicita cookie `arl`, não acessa mídia protegida e não realiza descriptografia.

Exemplo:

```text
deezer: Serhat Durmus La Câlin
```

## Versão 1.5.1 — formato original e taxa de bits configurável

- adiciona **Áudio original (sem conversão)** para preservar o codec, o contêiner e a taxa da fonte quando disponível;
- permite escolher 64, 96, 128, 160, 192, 256 ou 320 kbps para M4A, MP3 e Opus;
- oferece taxa personalizada entre 32 e 320 kbps, com validação antes do início da fila;
- mantém a opção **Original / automática**, que evita recodificação quando o formato já corresponde e usa a melhor qualidade automática nas conversões;
- salva as escolhas nas preferências e na fila persistente, incluindo compatibilidade com filas criadas em versões anteriores;
- aplica formato e taxa escolhidos também às prévias oficiais da Deezer e aos áudios do JW.ORG.

## Versão 1.5.0 — biblioteca de áudio e catálogo Deezer

- adiciona os formatos de áudio MP3 e Opus, além do M4A existente;
- incorpora metadados e miniatura usando o pós-processamento do `yt-dlp`;
- reconhece links públicos de faixa, álbum e playlist da Deezer;
- baixa exclusivamente a prévia oficial disponibilizada pelo catálogo público, identificada como prévia na fila e no nome do arquivo;
- atualiza a URL temporária da prévia somente no momento do download e nunca grava essa URL no banco da fila;
- aplica título, artista, álbum, número da faixa, ano e capa às prévias concluídas;
- cria automaticamente uma playlist local M3U8 para álbuns e playlists com duas ou mais prévias disponíveis;
- mantém itens sem prévia visíveis como indisponíveis e continua os demais;
- não solicita nem armazena cookie `arl`, não acessa mídia protegida e não realiza descriptografia.

## Versão 1.4.2 — proteção contra travamentos do YouTube

- a análise do YouTube é interrompida se o mecanismo ficar 60 segundos sem responder e repetida uma vez automaticamente;
- o início do download também possui detector de inatividade e retomada automática do arquivo parcial;
- o aplicativo solicita somente os metadados necessários, evitando JSONs enormes com formatos e legendas não utilizados;
- Deno e os componentes EJS são informados explicitamente ao yt-dlp para os desafios JavaScript do YouTube;
- conexões de download possuem limite de 30 segundos e as tentativas foram reduzidas para evitar esperas excessivas;
- a aba **Atividade** passa a ser gravada em `activity.log`, com rotação automática para diagnósticos futuros.

## Versão 1.4.1 — concluídos e limpeza segura

- nova aba **Concluídos**, separada da fila ativa, com nome do vídeo, qualidade e arquivo salvo;
- **Remover selecionados** e **Limpar concluídos** retiram apenas registros do aplicativo;
- **Remover da fila** exclui individualmente itens pendentes, interrompidos, cancelados ou com falha;
- **Limpar fila** remove todos os registros da fila atual e dos concluídos;
- nenhuma dessas operações apaga vídeos baixados nem arquivos parciais existentes.

## Versão 1.4.0 — fila persistente e San Francisco

- **Listar vídeos** expande playlists e temporadas em uma tabela com seleção individual, título, qualidade solicitada, situação e progresso;
- **Continuar fila** baixa apenas os itens marcados e pendentes, sem repetir os concluídos;
- **Parar fila** conserva a lista e os arquivos parciais; ao reabrir, a retomada depende de um clique, nunca começa sozinha;
- **Cancelar selecionados** cancela vídeos específicos e continua os demais; **Repetir falhas** recoloca itens selecionados com falha/cancelados na fila, ou todas as falhas quando nenhuma linha está selecionada;
- porcentagem e ETA global usam os vídeos selecionados, inclusive nas playlists genéricas, e excluem o trabalho concluído antes da sessão atual do cálculo de velocidade da fila;
- finalização/conversão não leva prematuramente o progresso global a 100%; pausa e parada preservam a posição da barra;
- fontes locais **SF Pro Text/Display** ou **SF UI Text/Display** são priorizadas em toda a interface; computadores sem elas usam Segoe UI/Arial;
- atividade detalhada em uma aba própria, mantendo a lista e o progresso na tela principal;
- URLs de mídia temporárias do Kanal D são renovadas ao retomar; o nome de saída fica fixo para localizar os arquivos parciais;
- downloads diretos do JW.ORG podem continuar com HTTP Range e validação de ETag/Last-Modified; se o conteúdo mudou ou o servidor não permite retomada segura, o arquivo reinicia.

### Uso da fila

1. Escolha pasta, formato e opções. Cole os links e clique em **Listar vídeos**.
2. Marque/desmarque episódios na primeira coluna e clique em **Continuar fila**.
3. Para parar e continuar outro dia, use **Parar fila** ou feche confirmando a interrupção.
4. Ao abrir novamente, confira a lista recuperada e clique em **Continuar fila**.

O botão **Baixar**, quando ainda não existe uma lista, analisa os links e inicia os
itens disponíveis automaticamente. Para escolher episódios antes, use **Listar vídeos**.
As opções de uma fila ficam fixas para preservar a retomada; alterações de pasta,
formato, cookies ou fragmentos exigem listar os links novamente.

A fila fica no arquivo `downloads.sqlite3`, dentro da pasta de dados do aplicativo
em `%LOCALAPPDATA%\C2 Sistemas\C2 Video Downloader`. São salvos links, seleções,
configurações e estados, mas não o conteúdo de cookies. A fila mais recente é
preservada; listar novos links substitui a lista, sem apagar os vídeos baixados.
Um item só é marcado como concluído após a finalização. A retomada de bytes depende
do motor, formato e servidor; conversões interrompidas podem recomeçar.

Nenhum arquivo de fonte da Apple é incluído ou redistribuído no instalador. A seleção
usa fontes já presentes no computador; o uso de San Francisco continua sujeito aos
[termos da Apple](https://developer.apple.com/fonts/). As caixas de diálogo e a barra
de título nativas do Windows seguem a tipografia do sistema operacional.
