# Publicação do adon studio

Plano para levar o app às lojas dos sistemas que ele suporta (Zorin OS 17/18, Ubuntu 22.04+,
Pop!_OS 22.04). Hoje ele é distribuído só como `.deb` nas
[Releases do GitHub](https://github.com/oh-dony/adon-studio/releases) (v0.3.0).

Não é parecer jurídico: se o app for gerar receita, passe a parte de licenças e marca por um
advogado de propriedade intelectual.

## Onde cada sistema procura apps

| Sistema | Loja que vem instalada | Aceita |
|---|---|---|
| Zorin OS 17/18 | Zorin Software Store | apt, **Flathub**, Snap |
| Pop!_OS 22.04 | Pop!_Shop | apt, **Flathub** |
| Ubuntu 22.04/24.04 | Ubuntu Software / App Center | **Snap** (Flathub só ativando à mão) |

**Decisão:** o Flathub é o destino principal (cobre Zorin e Pop!_OS direto, e o Ubuntu com o
Flatpak ativado). Um PPA cobre todo mundo pelo apt sem mexer no código. O Snap fica para depois
(ver fase 5).

**Ordem:** 1. licenças → 2. página da loja → 3. PPA → 4. Flatpak e Flathub. A fase 1 vem
primeiro porque, numa loja, o app fica muito mais visível, e um pedido de remoção (DMCA) no
GitHub derruba a Loja interna (aba Explorar) para todo mundo.

---

## Fase 1 — Licenças dos pacotes do Explorar (bloqueia o resto)

O Explorar redistribui 30 pacotes de terceiros (28 GPL-3.0, 1 GPL-2.0, 1 MIT). Redistribuir é
permitido, desde que cumpridas as condições, e hoje **não cumprimos todas**.

- [ ] **Texto da licença dentro de cada pacote.** 12 dos 30 `.tar.xz` não levam o
      LICENSE/COPYING do autor. Correção: `build_bundles.py` copia o arquivo do repositório de
      origem para dentro de cada pasta e **recusa** gerar pacote sem ele (como já recusa pasta
      faltando). Teste em `tests/test_regressoes.py`.
- [ ] **Código-fonte correspondente (GPL).** Temas como Catppuccin, Colloid e Orchis são CSS
      gerado a partir de SCSS; a GPL exige oferecer a fonte, não só o resultado, e um link para
      o repositório do autor não basta se ele sumir. Hoje nenhum item do `sources.json` tem
      o commit de origem registrado. Correção: campo `commit` obrigatório no `sources.json`, e
      o `git archive` de cada um publicado na Release `temas`.
- [ ] **Avisos de copyright (MIT do WhiteSur).** Manter o arquivo de copyright no pacote
      (sai junto com o primeiro item).
- [ ] **WhiteSur e "estilo macOS" (decidir).** Os ícones WhiteSur imitam de perto os ícones do
      próprio macOS (Finder, Ajustes); copiar o design de outro sistema é o ponto sensível. O
      estilo pronto se chama "Estilo macOS" (`adon_studio.py`, lista `PACKS`), um uso descritivo
      da marca da Apple que é tolerado mas desnecessário.
      Opção segura: renomear para "Estilo Mac" ou "WhiteSur", tirar o `whitesur-icons` do
      Explorar e manter o tema de janelas e o cursor.
- [ ] **Marca "adon".** Pesquisar no INPI (e no USPTO, se for publicar fora) se "adon" não
      colide com marca registrada na classe de software. Vale para toda a família adon.

## Fase 2 — Página do app na loja (metainfo)

As lojas montam a página do app a partir de
[packaging/com.adonay.studio.metainfo.xml](../packaging/com.adonay.studio.metainfo.xml).
Validar com `appstreamcli validate --pedantic` (o Flathub usa a mesma checagem).

- [x] Descrição com os nomes atuais (Estilos prontos, Cores, Explorar) e endereço do projeto
      e de onde relatar problemas.
- [ ] **Prints** (o Flathub exige pelo menos um). PNG 16:9, tema padrão do sistema, sem dados
      pessoais, em endereço fixo (ex.: `docs/prints/` no `main`, link pelo
      `raw.githubusercontent.com`). Sugestão: Estilos prontos, Cores, aba com prévia aberta e
      Explorar.
- [ ] **Notas de cada versão** dentro de `<release>`: a loja mostra "o que há de novo".
- [ ] **Classificação etária (OARS)** preenchida de verdade: o app baixa conteúdo da internet
      (o catálogo do Explorar), sem bate-papo nem compras.
- [ ] Cores da marca (`<branding>`) para o fundo da página na loja.
- [ ] Ícone: conferir o SVG em 128 px e 64 px (o Flathub pede um ícone legível nos dois).

## Fase 3 — PPA (apt para Ubuntu, Zorin e Pop!_OS)

Instala com `sudo add-apt-repository ppa:<conta>/adon-studio` e atualiza sozinho pelo apt. Não
muda nada no código.

- [ ] Conta no Launchpad com chave GPG e código de conduta assinado.
- [ ] Pacote-fonte: o Launchpad não aceita `.deb` pronto, compila a partir de um pacote-fonte
      (`debian/` com `control`, `rules`, `changelog`, `copyright`). Hoje o `.deb` sai de
      `packaging/build-deb.sh`; criar `debian/` a partir dele e um `./studio ppa` que gera e
      envia (`debuild -S` + `dput`).
- [ ] Uma série por versão do Ubuntu suportada (jammy 22.04, noble 24.04; Zorin 17 e Pop!_OS
      22.04 usam a jammy).
- [ ] Instalar do PPA num Ubuntu limpo e passar o `./studio docker` nele.

## Fase 4 — Flatpak e Flathub

O Flatpak roda o app isolado do sistema, e o adon studio precisa justamente mexer fora dele.
Tudo o que ele usa dá para liberar no manifesto, mas o código precisa se adaptar, e cada
permissão precisa de justificativa no pedido. Precedentes no Flathub: Gradience (mexia em
`gtk-4.0`) e Extension Manager (fala com o GNOME Shell). A aprovação não é garantida.

**O que o app precisa e a permissão correspondente:**

| O app faz | Permissão no manifesto |
|---|---|
| Instala temas, ícones e cursores | `--filesystem=~/.local/share/themes`, `~/.local/share/icons`, `~/.icons`, `~/.themes` |
| Muda tema dos apps GTK 4 | `--filesystem=xdg-config/gtk-4.0` |
| Grava papéis de parede | `--filesystem=xdg-data/backgrounds` |
| Muda configurações do GNOME (GSettings) | `--filesystem=xdg-run/dconf`, `~/.config/dconf:ro`, `--talk-name=ca.desrt.dconf`, `--env=DCONF_USER_CONFIG_DIR=.config/dconf` |
| Lê os temas e esquemas do sistema | `--filesystem=host-os:ro` (aparecem em `/run/host/usr/share`) |
| Barra e vidro (extensões) | `--filesystem=xdg-data/gnome-shell/extensions:ro` (esquemas) e `--talk-name=org.gnome.Shell.Extensions` |
| Monitores (barra por monitor) | `--talk-name=org.gnome.Mutter.DisplayConfig` |
| Reabre o Arquivos com o tema novo | `--talk-name=org.gnome.Nautilus` |
| Baixa o catálogo do Explorar | `--share=network` |
| Janela | `--socket=wayland`, `--socket=fallback-x11`, `--device=dri` |

**Passos:**

- [ ] Manifesto `packaging/flatpak/com.adonay.studio.json` com o runtime GNOME (versão que
      ainda receba atualização) e o app instalado em `/app`.
- [ ] Adaptar o código: pastas do sistema (`/usr/share/themes`, `/usr/share/icons`,
      `/usr/share/backgrounds`) também em `/run/host/usr/share`; esquemas GSettings das
      extensões lidos das pastas delas; testes do que muda dentro do Flatpak.
- [ ] `./studio flatpak`: gera com `flatpak-builder`, instala localmente e roda o
      `packaging/test/teste_app.py` dentro do Flatpak.
- [ ] Rodar o `./studio gnome` com a versão Flatpak (o combo pega no painel, Nautilus,
      extensão e wallpaper?) e o `./studio carga`.
- [ ] Linha "Flatpak" na tabela de testes do [AGENTS.md](../AGENTS.md).
- [ ] Pedido no Flathub: PR em `github.com/flathub/flathub` (branch `new-pr`) com o manifesto
      e a justificativa de cada permissão. Depois de aprovado, o Flathub cria o repositório
      `flathub/com.adonay.studio`; cada versão nova é um PR atualizando a tag e o hash.
- [ ] Verificação do desenvolvedor no Flathub (selo "verificado"): exige o id ligado a um
      domínio ou conta que você controla (ex.: `io.github.oh_dony.AdonStudio`, ou manter
      `com.adonay.studio` se tiver o domínio `adonay.com`). Decidir **antes** de enviar: trocar
      o id depois é trabalhoso.

## Fase 5 — Snap (adiada)

No isolamento padrão do Snap o app não funciona: o acesso à pasta pessoal não inclui pastas
ocultas como `~/.icons` e `~/.themes`, e falar com o GNOME Shell não é permitido. Precisaria do
modo "classic", que exige aprovação manual da Canonical e costuma ser negado para esse tipo de
app. Reavaliar depois do Flathub, se muita gente do Ubuntu pedir.

## A cada versão nova

- [ ] `VERSION` no `adon_studio.py`, `<release>` com as notas no metainfo e `debian/changelog`.
- [ ] `./studio test`, `./studio docker`, `./studio gnome` e `./studio carga` passando.
- [ ] Tag `vX.Y.Z`, `.deb` na Release do GitHub, envio ao PPA e PR no `flathub/com.adonay.studio`.

## Já resolvido

- Tela-circle ficou fora do Explorar: não declara licença, então não pode ser redistribuído.
- Os papéis de parede inclusos são gerados pelo próprio app.
- O app não coleta dados pessoais; só baixa arquivos do GitHub.
- A GPL-3.0 do app já traz a cláusula de "sem garantia"; o botão restaurar desfaz as mudanças.
- O metainfo passa no `appstreamcli validate --pedantic` (página do projeto incluída).
