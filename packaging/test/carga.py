# Teste de carga: abre o app num HOME isolado cheio de temas, ícones, cursores e papéis de parede
# de verdade (os deste computador, multiplicados), passa por todas as abas, clica, busca e reconstrói.
# Mede quanto tempo cada passo leva, quanto a janela fica parada (travada) e a memória.
# Uso: ./studio carga [multiplicador]   (padrão 3; roda num Broadway, sem abrir janela na sua tela)
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MULT = int(next((a for a in sys.argv[1:] if a.isdigit()), "3"))
MAX_TRAVADA_MS, MAX_RSS_MB = 250, 400   # acima disso o comando falha

# de onde copiar a carga: lido antes do base, que troca o HOME
REAL = Path.home()
FONTES_ICONES = [REAL / ".local/share/icons", REAL / ".icons", Path("/usr/share/icons")]
FONTES_TEMAS = [REAL / ".local/share/themes", REAL / ".themes", Path("/usr/share/themes")]
FONTES_FOTOS = [REAL / "Imagens", REAL / "Pictures", Path("/usr/share/backgrounds")]

sys.path.insert(0, str(ROOT / "tests"))
import base  # noqa: E402  (isola HOME, XDG e GSettings antes do app)

catalogo = ROOT / "dist/bundles/catalog.json"
if catalogo.exists():
    os.environ["ADON_STUDIO_CATALOG"] = catalogo.as_uri()


def encher():
    icons, themes = base.HOME / ".local/share/icons", base.HOME / ".local/share/themes"
    fotos = base.HOME / ".local/share/backgrounds/fotos"
    for d in (icons, themes, fotos):
        d.mkdir(parents=True, exist_ok=True)
    vistos = set()
    pastas_i = [d for f in FONTES_ICONES if f.is_dir() for d in sorted(f.iterdir())
                if d.is_dir() and d.name not in ("hicolor", "default") and not (d.name in vistos or vistos.add(d.name))]
    vistos = set()
    pastas_t = [d for f in FONTES_TEMAS if f.is_dir() for d in sorted(f.iterdir())
                if (d / "gtk-3.0").is_dir() and not (d.name in vistos or vistos.add(d.name))]
    imgs = [f for base_ in FONTES_FOTOS if base_.is_dir() for f in sorted(base_.rglob("*"))
            if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and f.is_file()][:60]
    for k in range(MULT):
        suf = "" if k == 0 else f"-copia{k}"
        for d in pastas_i:
            (icons / (d.name + suf)).symlink_to(d)
        for d in pastas_t:
            (themes / (d.name + suf)).symlink_to(d)
        for f in imgs:  # cópia, não atalho: o app ignora o mesmo arquivo repetido
            shutil.copyfile(f, fotos / f"{f.stem}-{k}{f.suffix}")
    return len(pastas_i) * MULT, len(pastas_t) * MULT, len(imgs) * MULT


n_icones, n_temas, n_fotos = encher()
print(f"carga x{MULT}: {n_icones} temas de ícones/cursores, {n_temas} temas GTK, {n_fotos} fotos")

t = time.perf_counter()
import adon_studio as A  # noqa: E402
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

T_IMPORT = time.perf_counter() - t
A.monitor_ids = lambda: ["carga-1"]


def rss():
    return next(int(l.split()[1]) // 1024 for l in open("/proc/self/status") if l.startswith("VmRSS"))


class Batimento:
    """Um batimento a cada 4 ms na tela principal: o atraso dele é quanto a janela ficou parada."""

    def __init__(self):
        self.last, self.gaps = time.perf_counter(), []
        GLib.timeout_add(4, self._tick, priority=GLib.PRIORITY_HIGH)

    def _tick(self):
        now = time.perf_counter()
        self.gaps.append((now - self.last) * 1000 - 4)
        self.last = now
        return True

    def take(self):
        g, self.gaps, self.last = self.gaps, [], time.perf_counter()
        return g


def pump(seconds=None, until=None, settle=0.4, limit=60):
    """Roda o laço por `seconds`, ou até `until()` (e mais `settle` s); devolve o tempo até a condição."""
    ctx, start, hit = GLib.MainContext.default(), time.perf_counter(), None
    while True:
        if not ctx.iteration(False):
            time.sleep(0.0005)
        now = time.perf_counter()
        if seconds is not None:
            if now - start >= seconds:
                return now - start
            continue
        if hit is None and (until is None or until()):
            hit = now
        if hit is not None and now - hit >= settle:
            return hit - start
        if now - start > limit:
            print("  !! tempo esgotado")
            return now - start


linhas = []


def passo(nome, segundos, gaps):
    maior = max(gaps, default=0)
    lentas = [g for g in gaps if g > 50]
    linhas.append((nome, maior))
    print(f"{nome:38s} {segundos * 1000:7.0f} ms   maior travada {maior:5.0f} ms   "
          f"travadas >50 ms: {len(lentas):2d}   memória {rss():4d} MB", flush=True)


app = Adw.Application(application_id="com.adonay.studio.carga")
app.register(None)
css = Gtk.CssProvider()
css.load_from_data(A.CSS.encode())
Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

bat = Batimento()
t = time.perf_counter()
win = A.Window(app)
win.present()
passo("abrir a janela", time.perf_counter() - t, bat.take())
el = pump(until=lambda: not win.store_loading and not win.grids["estilos"].loading)
passo("estilos e catálogo prontos", el, bat.take())

for page in ["cores", "temas", "icones", "cursores", "wallpaper", "barra", "efeitos", "explorar", "meus"]:
    win.go(page)
    el = pump(until=lambda p=page: p not in win.grids or not win.grids[p].loading)
    n = len(win.grids[page].cards) if page in win.grids else 0
    passo(f"aba {page} ({n} cartões)", el, bat.take())

for page in ["estilos", "temas", "icones", "cursores", "wallpaper", "explorar"]:
    win.go(page)
    pump(0.3)
    bat.take()
    keys = list(win.grids[page].cards)
    t = time.perf_counter()
    for k in keys[:: max(1, len(keys) // 5)][:5]:
        win.grids[page].cards[k].emit("clicked")
        pump(0.05)
    passo(f"clicar em 5 cartões: {page}", time.perf_counter() - t, bat.take())

for page in ["icones", "wallpaper"]:
    win.go(page)
    pump(0.3)
    bat.take()
    t = time.perf_counter()
    for txt in ["p", "pa", "pap", "papi", "", "q", "qo", ""]:
        win.grids[page].set_query(txt)
        pump(0.02)
    passo(f"digitar na busca: {page}", time.perf_counter() - t, bat.take())

t = time.perf_counter()
for page in ["estilos", "cores", "temas", "icones", "cursores", "wallpaper", "explorar"] * 2:
    win.go(page)
    pump(0.03)
passo("trocar de aba 14 vezes", time.perf_counter() - t, bat.take())

t = time.perf_counter()
win.rebuild()
el = pump(until=lambda: all(not g.loading for g in win.grids.values()))
passo("reler tudo (depois de instalar)", el, bat.take())

maior = max(linhas, key=lambda x: x[1])
final = rss()
print(f"\nimport do app {T_IMPORT * 1000:.0f} ms · pior travada {maior[1]:.0f} ms ({maior[0]}) · memória final {final} MB")
shutil.rmtree(base.TMP, ignore_errors=True)
falhas = ([f"travada de {maior[1]:.0f} ms em “{maior[0]}” (limite {MAX_TRAVADA_MS})"] if maior[1] > MAX_TRAVADA_MS else []) + \
    ([f"memória final {final} MB (limite {MAX_RSS_MB})"] if final > MAX_RSS_MB else [])
for f in falhas:
    print("FALHOU:", f)
sys.stdout.flush()
os._exit(1 if falhas else 0)  # sem esperar as threads de fundo
