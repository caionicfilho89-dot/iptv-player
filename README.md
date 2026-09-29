# 📺 IPTV Player

Player de IPTV para Windows com visual moderno, feito em Python (PyQt6) com o VLC embutido.
Vem com milhares de canais abertos que se atualizam sozinhos, mostra o que está passando agora
e troca de canal sozinho: pula os canais fora do ar e tem **zapping automático**.

![IPTV Player](docs/screenshot.png)

## ⬇️ Download

Baixe na página de **[Releases](https://github.com/caionicfilho89-dot/iptv-player/releases/latest)**:

| Arquivo | Para quem |
|---|---|
| **`IPTV-Player-Setup-v….exe`** | Instalador: cria atalho na área de trabalho e no menu Iniciar. Não pede senha de administrador. |
| **`IPTV-Player-v…-Portatil.zip`** | Versão portátil: extraia e abra `IPTV Player.exe`, sem instalar nada. |

**Não precisa instalar Python nem VLC**: tudo já vem junto.

> Se o Windows mostrar *"O Windows protegeu o computador"*, clique em **Mais informações → Executar assim mesmo**.
> Isso acontece com programas novos sem assinatura digital.

## ✨ Recursos

**Canais**
- Listas por categoria (Brasil, Português, Filmes, Séries, Esportes, Notícias, Infantil…) **atualizadas
  automaticamente** a partir do [iptv-org](https://github.com/iptv-org/iptv)
- **Adicione suas próprias listas** por link (ex.: a da sua operadora de IPTV) ou arquivo `.m3u`
- **Guia de programação (EPG)**: o que está passando agora e a seguir, com barra de progresso
- Busca, filtro por grupo, favoritos ⭐ e recentes
- **Listas atualizadas todo dia**, com os canais que chegaram marcados como **NOVO** e reunidos na categoria **Novos** 🆕
- **Links quebrados trocados sozinhos** 🆕: se um canal cai, o app procura o mesmo canal em outro link (em qualquer lista), usa o que funcionar e lembra dele

**Troca automática**
- **Pular canais offline**: se o canal não abrir, der erro ou travar, vai para o próximo sozinho
- **Zapping automático** a cada N segundos, em ordem ou aleatório, também **só pelos favoritos**
- **Explorar** 🆕: passa sozinho por **todos os canais de todas as listas**, pulando os offline e os que você já viu; a lista rola acompanhando
- **Testar lista**: descobre em segundo plano quais canais estão no ar
- **Digite o número do canal** (como no controle remoto) para ir direto a ele

**Assistir**
- **Tela cheia com controles flutuantes** que aparecem ao mexer o mouse
- **Janela flutuante (PiP)**: vídeo pequeno sempre visível enquanto você usa o PC
- **Mosaico**: 2, 4 ou 9 canais ao mesmo tempo
- **Gravar canal** e **tirar foto da tela**
- **Timer para desligar** (ou ao fim do programa atual)
- **Legendas traduzidas por IA** 🆕: canais em inglês, espanhol, francês e outros idiomas ganham legenda em
  português ao vivo, como a tradução automática do YouTube (botão **CC** ou `Ctrl+T`)

**Visual**
- Modo **lista** ou **grade**, tema **escuro** ou **claro** e 6 cores de destaque
- Aviso quando sair uma versão nova

![Modo grade e tema claro](docs/screenshot-grade.png)

## ⌨️ Atalhos

| Tecla | Ação |
|---|---|
| `0`–`9` | Ir para o canal pelo número |
| `PgUp` / `PgDn` | Canal anterior / próximo |
| `F11` ou duplo clique | Tela cheia (`Esc` para sair) |
| `↑ ↓` / `Espaço` (em tela cheia) | Trocar canal / pausar |
| `Ctrl+F` | Buscar |
| `Ctrl+D` | Favoritar canal atual |
| `Ctrl+M` | Mudo |
| `Ctrl+Z` | Liga/desliga o zapping |
| `Ctrl+E` | Liga/desliga o modo Explorar |
| `Ctrl+G` | Guia de programação |
| `Ctrl+S` | Foto da tela |
| `Ctrl+R` | Gravar |
| `Ctrl+P` | Janela flutuante |
| `Ctrl+L` | Alternar lista / grade |
| `Ctrl+T` | Legendas traduzidas por IA |

Fotos vão para **Imagens\IPTV Player** e gravações para **Vídeos\IPTV Player**.

## 💬 Legendas traduzidas por IA

1. A IA ouve o som que está saindo do computador (por isso o som do player precisa estar ligado)
2. O **[Whisper](https://github.com/SYSTRAN/faster-whisper)** reconhece a fala **no seu próprio PC**, sem enviar o áudio para a internet
3. Só o texto reconhecido é enviado ao **Google Tradutor** para virar português

Na primeira vez que você liga, o modelo de IA é baixado (cerca de 480 MB). Em **Configurações → Legendas
traduzidas por IA** dá para escolher a qualidade (Rápido / Equilibrado / Preciso), fixar o idioma do canal,
mudar o tamanho da legenda e mostrar também a frase original.

## 🐍 Rodando pelo código-fonte

1. Instale o **[Python 3.10+](https://www.python.org/downloads/)** (marque *"Add Python to PATH"*) e o **[VLC 64 bits](https://www.videolan.org/vlc/)**
2. Baixe este projeto (**Code → Download ZIP**) e extraia
3. Dê dois cliques em **`abrir_iptv_player.bat`** (na primeira vez ele instala as dependências)

Ou pelo terminal:

```bash
pip install -r requirements.txt
python iptv_player.py
```

Para gerar o instalador e a versão portátil: `pip install pyinstaller` e `python packaging/build.py`
(requer [Inno Setup 6](https://jrsoftware.org/isinfo.php)).

## 📜 Créditos

- Playlists do projeto [iptv-org/iptv](https://github.com/iptv-org/iptv), que reúne canais **abertos e gratuitos**
  publicamente disponíveis. Este projeto não hospeda nenhum conteúdo; os links apontam para as transmissões dos
  próprios canais, que podem sair do ar ou ser bloqueadas por região a qualquer momento.
- Guia de programação padrão do [epgshare01](https://epgshare01.online/).
- Reprodução pelo [VLC / libVLC](https://www.videolan.org/) (LGPL/GPL), incluído nas versões para download.
