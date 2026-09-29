# 📺 IPTV Player

Player de IPTV para Windows com visual moderno, feito em Python (PyQt6) com o VLC embutido.
Já vem com playlists de canais abertos separadas por categoria, e troca de canal sozinho:
pula os canais que estão fora do ar e tem um modo **zapping automático**.

![IPTV Player](docs/screenshot.png)

## ✨ Recursos

- **Visual escuro e moderno**, com logos dos canais, qualidade (720p/1080p) e status de cada canal
  (🟢 no ar · 🟡 conectando · 🔴 offline)
- **Categorias**: Português, Filmes, Séries, Esportes, Notícias, Documentários, Música, Infantil e mais
- **Busca**, filtro por grupo e opção de ocultar os canais offline
- **Favoritos ⭐** e **Recentes 🕘**, salvos entre as sessões
- **Pular canais offline automaticamente**: se o canal não abrir, der erro ou travar, o player tenta
  reconectar e, se não der, vai para o próximo sozinho
- **Zapping automático 🔀**: troca de canal a cada N segundos, em ordem ou aleatório
- **🩺 Testar lista**: verifica em segundo plano quais canais da lista estão no ar
- **Tela cheia** com duplo clique ou F11
- Abre de onde parou (último canal, volume, preferências)

## ⬇️ Download (jeito mais fácil)

1. Instale o **[VLC 64 bits](https://www.videolan.org/vlc/)**, se ainda não tiver
2. Baixe o **[IPTV-Player-Windows.zip](https://github.com/caionicfilho89-dot/iptv-player/releases/latest)** na página de Releases
3. Extraia o ZIP e dê dois cliques em **`IPTV Player.exe`**. Não precisa instalar Python.

> Se o Windows mostrar *"O Windows protegeu o computador"*, clique em **Mais informações → Executar assim mesmo**.
> Isso acontece com programas novos sem assinatura digital.

## 🐍 Rodando pelo código-fonte

1. Instale o **[Python 3.10+](https://www.python.org/downloads/)** e marque *"Add Python to PATH"* na instalação
2. Instale o **[VLC 64 bits](https://www.videolan.org/vlc/)**
3. Baixe este projeto (botão verde **Code → Download ZIP**) e extraia
4. Dê dois cliques em **`abrir_iptv_player.bat`**
   (na primeira vez ele instala as dependências automaticamente)

Ou pelo terminal:

```bash
pip install -r requirements.txt
python iptv_player.py
```

## ⌨️ Atalhos

| Tecla | Ação |
|---|---|
| `PgUp` / `PgDn` | Canal anterior / próximo |
| `F11` ou duplo clique | Tela cheia |
| `Esc` | Sair da tela cheia |
| `↑ ↓` / `Espaço` (em tela cheia) | Trocar canal / pausar |
| `Ctrl+F` | Buscar |
| `Ctrl+D` | Favoritar canal atual |
| `Ctrl+M` | Mudo |
| `Ctrl+Z` | Liga/desliga o zapping automático |

## 📂 Usando suas próprias listas

Qualquer arquivo `.m3u` colocado na pasta do programa aparece automaticamente como uma nova categoria na barra lateral.

## 📜 Créditos

As playlists incluídas vêm do projeto [iptv-org/iptv](https://github.com/iptv-org/iptv),
que reúne canais **abertos e gratuitos** publicamente disponíveis. Este projeto não hospeda nenhum
conteúdo; os links apontam para as transmissões oficiais dos próprios canais. Alguns canais podem
sair do ar ou ser bloqueados por região a qualquer momento.

Também inclui o `assistir_iptv.bat`, um menu simples que abre as listas direto no VLC.
