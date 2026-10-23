# Pendências para divulgar o adon studio

O app já está publicado (v0.2.0), mas antes de divulgar para muita gente há pontos de
licença e marca a resolver. Não é parecer jurídico: se o app for gerar receita, passe isto
por um advogado de propriedade intelectual.

## 1. Licenças dos pacotes da Loja (resolver antes de divulgar)

A Loja redistribui temas de terceiros (quase todos GPL-2.0/GPL-3.0, o WhiteSur GTK é MIT).
Redistribuir é permitido, desde que cumpridas as condições, e hoje **não cumprimos todas**:

- [ ] **Texto da licença dentro de cada pacote.** Muitos `.tar.xz` não levam o LICENSE do
      autor. Correção: `build_bundles.py` copia o LICENSE/COPYING do repositório de origem
      para dentro de cada pasta do pacote.
- [ ] **Código-fonte correspondente (GPL).** Temas como Catppuccin, Colloid e Orchis são CSS
      compilado de SCSS; a GPL exige oferecer a fonte (o SCSS), não só o resultado. Um link
      para o repositório do autor não basta se ele sumir. Correção: registrar o commit exato
      de cada tema em `sources.json` e publicar o `git archive` de cada um na Release `temas`.
- [ ] **Avisos de copyright (MIT do WhiteSur).** Manter o arquivo de copyright no pacote
      (resolvido junto com o primeiro item).

Risco se não fizer: na prática, um pedido de remoção (DMCA) no GitHub, que tira os pacotes
do ar. Autor de tema raramente processa.

## 2. WhiteSur e "estilo macOS" (decidir)

- Os ícones WhiteSur imitam de perto os ícones do próprio macOS (Finder, Ajustes). Logos de
  apps (Chrome, Spotify…) em pacotes de ícones são prática aceita (o Papirus está nos
  repositórios do Ubuntu); copiar o design de outro sistema é o ponto sensível.
- "macOS" no nome do combo é uso descritivo de marca da Apple, tolerado mas desnecessário.
- Opção segura: renomear para "Estilo Mac"/"WhiteSur" e tirar o pacote `whitesur-icons` da
  Loja, mantendo o tema de janelas.

## 3. Marca "adon"

- [ ] Pesquisar no INPI (e no USPTO, se for publicar fora) se "adon" não colide com marca
      registrada na classe de software. Vale para toda a família adon (o IPTV tem a mesma
      pendência em `docs/PUBLICACAO.md` dele).

## Já resolvido

- Tela-circle ficou fora da Loja: não declara licença, então não pode ser redistribuído.
- Wallpapers inclusos são gerados pelo próprio app.
- O app não coleta dados pessoais; só baixa arquivos do GitHub.
- GPL-3.0 do app já traz a cláusula de "sem garantia"; o botão restaurar desfaz as mudanças.
