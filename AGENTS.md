# Instruções para agentes

Leia o [README.md](README.md) antes de mexer no código. O app inteiro é um arquivo,
[adon_studio.py](adon_studio.py) (Python + GTK4/libadwaita), dividido em seções:
configurações (`Desktop`), descoberta de temas, Loja (`Store`), desenho das prévias,
combos e interface (`Window`).

## Regra nº 1: todo bug vira teste automatizado

Encontrou um bug, seja reportado pelo usuário, visto num screenshot, num log ou
enquanto você trabalhava em outra coisa? Ele **só está resolvido quando existe um
teste que falharia sem a correção**.

1. **Reproduza com um teste que falha.** Escreva o teste antes da correção e rode para
   confirmar que ele está vermelho pelo motivo certo.
2. **Corrija.**
3. **Rode o teste de novo** e confirme que passou. Depois rode `./studio test` inteiro.
4. **No docstring da classe de teste, um parágrafo `Regressão:`** dizendo o sintoma real
   (o que o usuário via) e a causa. Exemplo:

   ```python
   class Cursor(unittest.TestCase):
       """
       Regressão: com qualquer cursor que não fosse o Adwaita, o mouse virava um quadrado
       cinza sobre a barra de tarefas. O GNOME Shell só procura cursores em ~/.icons e
       /usr/share/icons, e eles eram instalados em ~/.local/share/icons.
       """
   ```

Se for inviável automatizar (ex.: só aparece no GNOME Shell de verdade), diga isso
explicitamente ao usuário, explique por quê e registre o caso em "Não automatizado"
no fim deste arquivo. Nunca deixe isso passar em silêncio.

### Onde cada teste vai

| Tipo de bug | Onde | Como rodar |
|---|---|---|
| Lógica: configurações, barra, cursor, restaurar, Loja, descompactar | `tests/test_regressoes.py` | `./studio test` |
| Pacote da Loja incompleto, atalho quebrado, catálogo | `tests/test_regressoes.py` (usa `packaging/build_bundles.py`) | `./studio test` |
| Interface: tamanho, layout, widget | `tests/test_ui.py` (pulado sem tela) | `./studio test` numa sessão gráfica |
| Não instala ou não abre num Ubuntu limpo, dependência faltando, API de GTK/libadwaita antiga | `packaging/test/teste_app.py` no Docker | `./studio docker` |

Dicas:
- `tests/base.py` isola tudo: HOME e pastas XDG temporárias, GSettings em memória e um
  esquema falso da barra. **Importe `base` antes do `adon_studio`.** Nunca teste
  mexendo no tema do computador de verdade.
- `monitor_ids()` fala com o Mutter por D-Bus: nos testes, substitua por uma lambda.
- A Loja aceita `ADON_STUDIO_CATALOG` com `file://` ou `http://localhost` — nunca
  dependa do GitHub num teste.

## Antes de dizer que terminou

- `./studio test` passa por inteiro.
- Mudou interface ou dependência? `./studio docker` (Ubuntu 22.04 e 24.04) passa e você
  olhou os prints em `packaging/test/out/`.
- Relate o que foi testado e o que não foi. Se algo falhou, mostre a saída.

## Regras do código

- **Precisa rodar em libadwaita 1.1 / GTK 4.6 / Python 3.10** (Ubuntu 22.04, Zorin 17,
  Pop!_OS 22.04). Nada de `Adw.SwitchRow`, `Adw.SpinRow`, `Adw.ToolbarView`,
  `Gtk.FileDialog` etc. sem alternativa: use os atalhos `switch_row`, `spin_row`,
  `picture`, `confirm` ou um `hasattr`.
- **Tudo na pasta do usuário, sem sudo.** Temas em `~/.local/share/themes`, ícones e
  cursores em `~/.local/share/icons` (cursores também ganham atalho em `~/.icons`).
- **A barra guarda ajustes por monitor** (`fabricante-serial`). Use `bar_set`, que aplica
  em todos os monitores conectados; nunca grave o JSON direto.
- **Pacote novo na Loja**: entra em `packaging/sources.json` com autor, licença e fonte.
  Só redistribua o que tem licença que permite (GPL, MIT…). `./studio bundles` recusa
  pacote com pasta faltando.
- Nome, id e ícone seguem a marca **adon**: "Adon Studio", `com.adonay.studio`, "A" sem
  a barra do meio com uma gota de tinta no lugar.

## Não automatizado

- **Instalador do WhiteSur aborta em silêncio** (bug do script deles ao instalar o
  `gnome-theme-switcher`). Usamos os `.tar.xz` prontos da pasta `release/` do repositório
  deles. Não é código nosso.
- **O quadrado cinza em si** só aparece no GNOME Shell de verdade. O teste cobre a causa
  (cursor fora de `~/.icons`); confirmação visual só numa sessão GNOME.
