<p align="center"><img src="icons/hicolor/scalable/apps/com.adonay.studio.svg" alt="adon studio" height="120"></p>

# adon studio · deixe seu Linux com a sua cara

Troque o tema, os ícones, o cursor, o wallpaper e a barra de tarefas do seu computador vendo
uma prévia de cada opção. Sem terminal e sem precisar entender de Linux.

- **Combos**: visuais completos (tema + ícones + cursor + wallpaper) aplicados com um clique
- **Loja**: dezenas de temas, ícones e cursores; clique em "Instalar" e pronto
- **Barra**: dock flutuante, barra flutuante ou clássica, em todos os monitores
- **Restaurar**: o app guarda o seu visual na primeira vez que abre; o botão ↶ volta para ele

## Instalar

1. Baixe o arquivo `adon-studio_..._all.deb` na página de
   [Releases](https://github.com/oh-dony/adon-studio/releases/latest).
2. Dê dois cliques nele e clique em **Instalar**. A loja de aplicativos instala tudo o que
   for preciso.
3. Procure **Adon Studio** no menu de aplicativos.

Funciona no **Zorin OS 17 e 18**, **Ubuntu 22.04 ou mais novo** e **Pop!_OS 22.04**, com o
GNOME. (O Pop!_OS 24.04 usa o COSMIC no lugar do GNOME e não é compatível.)

## Créditos

Os temas, ícones e cursores da Loja são de seus autores, redistribuídos conforme as licenças
de cada um. A lista completa, com links, está em `Sobre` dentro do app e em
[packaging/sources.json](packaging/sources.json). Entre eles: Catppuccin, Dracula, Nordic,
Papirus, Bibata, WhiteSur, Colloid, Fluent, Orchis, Qogir, Graphite, Gruvbox, Everforest,
Rosé Pine, Kanagawa e Tokyo Night.

## Desenvolvimento

```bash
./studio run      # abre o app direto do código
./studio test     # testes automatizados
./studio docker   # instala o .deb num Ubuntu 22.04 e 24.04 limpos e testa
./studio bundles  # gera os pacotes da Loja
./studio deb      # gera o .deb
```

Regras do projeto (todo bug vira teste): [AGENTS.md](AGENTS.md). Pendências antes de divulgar: [docs/PUBLICACAO.md](docs/PUBLICACAO.md).

## Licença

GPL-3.0
