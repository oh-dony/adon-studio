#!/usr/bin/env python3
# Adon Studio — personalize o visual do GNOME com um clique.
import hashlib
import json
import os
import re
import shutil
import struct
import tarfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from math import pi
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk  # noqa: E402

APP_ID = "com.adonay.studio"
APP_NAME = "Adon Studio"
VERSION = "0.2.0"

HOME = Path.home()
USER_DATA = Path(GLib.get_user_data_dir())
SYS_DATA = [Path(d) for d in GLib.get_system_data_dirs()]
DATA = USER_DATA / "adon-studio"
BACKUP = DATA / "original"
WALLS = USER_DATA / "backgrounds" / "adon-studio"
GTK4_CFG = Path(GLib.get_user_config_dir()) / "gtk-4.0"
GTK4_FILES = ("gtk.css", "gtk-dark.css", "assets")
CURSOR_DEFAULT = HOME / ".icons" / "default"


def _dirs(paths):
    out = []
    for p in paths:
        if p.is_dir() and p.resolve() not in [o.resolve() for o in out]:
            out.append(p)
    return out


for _d in (USER_DATA / "themes", USER_DATA / "icons"):
    _d.mkdir(parents=True, exist_ok=True)
THEME_DIRS = _dirs([HOME / ".themes", USER_DATA / "themes"] + [d / "themes" for d in SYS_DATA])
ICON_DIRS = _dirs([USER_DATA / "icons", HOME / ".icons"] + [d / "icons" for d in SYS_DATA])
WALL_DIRS = _dirs([WALLS, USER_DATA / "backgrounds"] + [d / "backgrounds" for d in SYS_DATA])
IMG_EXT = {".jpg", ".jpeg", ".png", ".svg", ".webp"}

TASKBAR_SCHEMAS = ["org.gnome.shell.extensions.zorin-taskbar", "org.gnome.shell.extensions.dash-to-panel"]
TASKBAR_KEYS = ["panel-lengths", "panel-anchors", "panel-sizes", "panel-positions", "panel-margin",
                "global-border-radius", "trans-use-custom-opacity", "trans-panel-opacity", "intellihide"]

POOL = ThreadPoolExecutor(max_workers=4)

# Loja: catálogo e pacotes de tema publicados nas Releases do GitHub
CATALOG_URL = os.environ.get("ADON_STUDIO_CATALOG",
                             "https://github.com/oh-dony/adon-studio/releases/download/temas/catalog.json")
CACHE = Path(GLib.get_user_cache_dir()) / "adon-studio"
STORE_DIRS = {"gtk": USER_DATA / "themes", "icons": USER_DATA / "icons", "cursors": USER_DATA / "icons"}
USER_THEME_UUID = "user-theme@gnome-shell-extensions.gcampax.github.com"


# ---------------------------------------------------------------- configurações

def settings_for(schema_id):
    """Gio.Settings do schema, procurando também nas pastas de extensões do GNOME Shell."""
    default = Gio.SettingsSchemaSource.get_default()
    if default and default.lookup(schema_id, True):
        return Gio.Settings.new(schema_id)
    for base in [USER_DATA] + SYS_DATA:
        for d in (base / "gnome-shell" / "extensions").glob("*/schemas"):
            try:
                src = Gio.SettingsSchemaSource.new_from_directory(str(d), default, False)
            except GLib.Error:
                continue
            schema = src.lookup(schema_id, False)
            if schema:
                return Gio.Settings.new_full(schema, None, None)
    return None


def has_key(s, key):
    return s is not None and s.props.settings_schema.has_key(key)


def monitor_ids():
    """Ids dos monitores conectados no formato usado pela barra (fabricante-serial)."""
    ids = []
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        res = bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                            "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None, None,
                            Gio.DBusCallFlags.NONE, -1, None)
        for i, ((connector, vendor, _product, serial), _modes, _props) in enumerate(res.unpack()[1]):
            ids.append(f"{vendor}-{serial}" if vendor and serial else str(i))
    except GLib.Error:
        pass
    return ids


class Desktop:
    """Lê e aplica as configurações de aparência, e guarda o ponto de restauração."""

    def __init__(self):
        self.iface = settings_for("org.gnome.desktop.interface")
        self.bg = settings_for("org.gnome.desktop.background")
        self.lock = settings_for("org.gnome.desktop.screensaver")
        self.shell = settings_for("org.gnome.shell.extensions.user-theme")
        self.bar = next((s for s in map(settings_for, TASKBAR_SCHEMAS) if s), None)
        self.tracked = {
            "interface": (self.iface, ["gtk-theme", "icon-theme", "cursor-theme", "color-scheme"]),
            "background": (self.bg, ["picture-uri", "picture-uri-dark", "picture-options"]),
            "screensaver": (self.lock, ["picture-uri"]),
            "user-theme": (self.shell, ["name"]),
            "taskbar": (self.bar, TASKBAR_KEYS),
        }

    # --- ponto de restauração
    def ensure_backup(self):
        if (BACKUP / "settings.json").exists():
            return
        BACKUP.mkdir(parents=True, exist_ok=True)
        values = {}
        for group, (s, keys) in self.tracked.items():
            if s:
                values[group] = {k: s.get_value(k).print_(True) for k in keys if has_key(s, k)}
        gtk4 = {}
        for name in GTK4_FILES:
            p = GTK4_CFG / name
            if p.is_symlink():
                gtk4[name] = {"link": str(p.readlink())}
            elif p.is_file():
                shutil.copy2(p, BACKUP / name)
                gtk4[name] = {"file": name}
            elif p.is_dir():
                shutil.copytree(p, BACKUP / name, dirs_exist_ok=True)
                gtk4[name] = {"dir": name}
        values["gtk4"] = gtk4
        values["blur"] = Blur().snapshot() if Blur.installed() else None
        values["cursor-default"] = CURSOR_DEFAULT.exists()
        if CURSOR_DEFAULT.exists():
            shutil.copytree(CURSOR_DEFAULT, BACKUP / "cursor-default", dirs_exist_ok=True)
        (BACKUP / "settings.json").write_text(json.dumps(values, indent=2))

    def restore(self):
        values = json.loads((BACKUP / "settings.json").read_text())
        for group, (s, keys) in self.tracked.items():
            if not s:
                continue
            saved = values.get(group, {})
            for k in keys:
                if not has_key(s, k):
                    continue
                if k in saved:
                    kind = GLib.VariantType(s.get_value(k).get_type_string())
                    s.set_value(k, GLib.Variant.parse(kind, saved[k], None, None))
                else:
                    s.reset(k)
        self._clear_gtk4()
        for name, info in values.get("gtk4", {}).items():
            dest = GTK4_CFG / name
            GTK4_CFG.mkdir(parents=True, exist_ok=True)
            if "link" in info:
                dest.symlink_to(info["link"])
            elif "file" in info:
                shutil.copy2(BACKUP / name, dest)
            elif "dir" in info:
                shutil.copytree(BACKUP / name, dest)
        blur = Blur()
        if blur.root:
            blur.restore(values.get("blur"))
        shutil.rmtree(CURSOR_DEFAULT, ignore_errors=True)
        if values.get("cursor-default"):
            shutil.copytree(BACKUP / "cursor-default", CURSOR_DEFAULT)
        Gio.Settings.sync()

    # --- aplicar
    @staticmethod
    def _clear_gtk4():
        for name in GTK4_FILES:
            p = GTK4_CFG / name
            if p.is_symlink() or p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)

    def ensure_user_theme(self):
        """Liga a extensão User Themes (sem ela o tema não chega ao painel). True se está ativa."""
        if not extension_installed(USER_THEME_UUID) or not enable_extension(USER_THEME_UUID):
            return False
        self.shell = self.shell or settings_for("org.gnome.shell.extensions.user-theme")
        return True

    def apply_gtk(self, theme):
        self.iface.set_string("color-scheme", "prefer-light" if "light" in theme.name.lower() else "prefer-dark")
        self.iface.set_string("gtk-theme", theme.name)
        if self.shell:
            self.shell.set_string("name", theme.name if (theme.path / "gnome-shell").is_dir() else "")
        # apps GTK4/libadwaita só seguem o tema pelos arquivos em ~/.config/gtk-4.0
        self._clear_gtk4()
        src = theme.path / "gtk-4.0"
        if src.is_dir() and not any(theme.path.is_relative_to(d) for d in SYS_DATA):
            GTK4_CFG.mkdir(parents=True, exist_ok=True)
            # @import em vez de atalho: caminhos relativos do tema (url("../assets/...")) passam a
            # valer a partir da pasta do tema, e não de ~/.config (sem isso o Dracula some com os
            # interruptores de todos os apps GTK4)
            for name in ("gtk.css", "gtk-dark.css"):
                if (src / name).exists():
                    (GTK4_CFG / name).write_text(f"/* {APP_NAME}: tema {theme.name} */\n"
                                                 f'@import url("{(src / name).as_uri()}");\n')
            if (src / "assets").is_dir():
                (GTK4_CFG / "assets").symlink_to(src / "assets")

    def apply_icons(self, name):
        self.iface.set_string("icon-theme", name)

    @staticmethod
    def link_cursor_themes():
        """O GNOME Shell só procura cursores em ~/.icons e nas pastas do sistema; um cursor que
        está só em ~/.local/share/icons vira um quadrado cinza na barra e no painel."""
        icons = HOME / ".icons"
        for name, path in list_cursor_themes():
            link = icons / name
            if path.parent == icons or link.exists() or link.is_symlink() or \
                    any(path.is_relative_to(d) for d in SYS_DATA):
                continue
            icons.mkdir(exist_ok=True)
            link.symlink_to(path)

    def apply_cursor(self, name):
        self.link_cursor_themes()
        self.iface.set_string("cursor-theme", name)
        # apps X11/XWayland (Chrome, VS Code...) leem o cursor daqui
        CURSOR_DEFAULT.mkdir(parents=True, exist_ok=True)
        (CURSOR_DEFAULT / "index.theme").write_text(f"[Icon Theme]\nName=Default\nInherits={name}\n")

    def apply_wallpaper(self, path):
        uri = Gio.File.new_for_path(str(path)).get_uri()
        self.bg.set_string("picture-uri", uri)
        if has_key(self.bg, "picture-uri-dark"):
            self.bg.set_string("picture-uri-dark", uri)
        self.bg.set_string("picture-options", "zoom")
        if self.lock:
            self.lock.set_string("picture-uri", uri)

    # --- barra de tarefas (valores por monitor guardados em JSON)
    def bar_get(self, key, default):
        if not has_key(self.bar, key):
            return default
        d = json.loads(self.bar.get_string(key) or "{}")
        for i in monitor_ids():
            if i in d:
                return d[i]
        return next(iter(d.values()), default)

    def bar_set(self, key, value):
        if not has_key(self.bar, key):
            return
        d = json.loads(self.bar.get_string(key) or "{}")
        for i in set(d) | set(monitor_ids()):
            d[i] = value
        self.bar.set_string(key, json.dumps(d))

    def bar_set_plain(self, key, value):
        if has_key(self.bar, key):
            self.bar.set_value(key, GLib.Variant(self.bar.get_value(key).get_type_string(), value))


# ---------------------------------------------------------------- descoberta

class Theme:
    def __init__(self, name, path):
        self.name, self.path = name, path


def list_gtk_themes():
    found = {}
    for base in THEME_DIRS:
        for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            n = d.name
            if n in found or n in {"Default", "Emacs", "Raleigh"} or re.search(r"-x?hdpi$", n):
                continue
            if (d / "gtk-3.0" / "gtk.css").exists():
                found[n] = Theme(n, d)
    return sorted(found.values(), key=lambda t: t.name.lower())


def _index(d):
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(str(d / "index.theme"), GLib.KeyFileFlags.NONE)
        return kf
    except GLib.Error:
        return None


def list_icon_themes():
    found = {}
    for base in ICON_DIRS:
        for d in base.iterdir():
            if d.name in found or d.name.startswith(".") or d.name in {"hicolor", "default", "locolor"}:
                continue
            kf = _index(d)
            try:
                if kf and kf.get_string("Icon Theme", "Directories"):
                    found[d.name] = d
            except GLib.Error:
                pass
    return sorted(found, key=str.lower)


def list_cursor_themes():
    found = {}
    for base in ICON_DIRS:
        for d in base.iterdir():
            if d.name not in found and d.name != "default" and (d / "cursors").is_dir():
                found[d.name] = d
    return sorted(found.items(), key=lambda t: t[0].lower())


def list_wallpapers():
    seen, out = set(), []
    for base in WALL_DIRS:
        for f in sorted(base.rglob("*")):
            if f.suffix.lower() in IMG_EXT and f.is_file() and f.resolve() not in seen:
                seen.add(f.resolve())
                out.append(f)
    return out[:300]


# ---------------------------------------------------------------- loja

KIND_LABEL = {"gtk": "Tema", "icons": "Ícones", "cursors": "Cursor"}


def _http_get(url, dest=None, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        if dest is None:
            return resp.read()
        total, done = int(resp.headers.get("Content-Length") or 0), 0
        with open(dest, "wb") as out:
            while chunk := resp.read(1 << 16):
                out.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
    return dest


def _safe_members(tf, dest):
    """Só o que cai dentro de `dest` (nada de caminhos absolutos, '..' ou links para fora)."""
    dest = dest.resolve()
    for m in tf.getmembers():
        target = (dest / m.name).resolve()
        if not target.is_relative_to(dest) or not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
            continue
        if m.issym():
            link = Path(m.linkname) if os.path.isabs(m.linkname) else (dest / m.name).parent / m.linkname
            if not link.resolve().is_relative_to(dest):
                continue
        if m.islnk() and not (dest / m.linkname).resolve().is_relative_to(dest):
            continue
        yield m


class Store:
    def __init__(self):
        self.items = {}
        CACHE.mkdir(parents=True, exist_ok=True)
        self._base = CATALOG_URL.rsplit("/", 1)[0] + "/"

    def fetch(self, done):
        """Baixa o catálogo em segundo plano; sem internet usa a última cópia salva."""
        def work():
            cached = CACHE / "catalog.json"
            try:
                data = _http_get(CATALOG_URL)
                json.loads(data)
                cached.write_bytes(data)
            except (OSError, ValueError):
                data = cached.read_bytes() if cached.exists() else None
            self.items = {i["id"]: i for i in json.loads(data)["items"]} if data else {}
            GLib.idle_add(lambda: done(bool(self.items)) or False)
        POOL.submit(work)

    @staticmethod
    def is_installed(item):
        bases = THEME_DIRS if item["kind"] == "gtk" else ICON_DIRS
        return all(any((b / d).is_dir() for b in bases) for d in item["dirs"])

    def provider(self, dirname):
        """Item do catálogo que instala a pasta `dirname`."""
        return next((i for i in self.items.values() if dirname in i["dirs"]), None)

    def with_needs(self, items):
        out = []
        for i in items:
            for n in i.get("needs", []):
                if n in self.items and not self.is_installed(self.items[n]) and self.items[n] not in out:
                    out.append(self.items[n])
            if i not in out:
                out.append(i)
        return out

    def preview(self, item, done):
        def work():
            f = CACHE / "previews" / item["preview"]
            if not f.exists():
                f.parent.mkdir(exist_ok=True)
                try:
                    _http_get(self._base + item["preview"], f.with_suffix(".part")).rename(f)
                except OSError:
                    return
            GLib.idle_add(lambda: done(f) or False)
        POOL.submit(work)

    def install(self, items, progress, done):
        """Instala uma lista de itens em sequência. progress(fração 0..1), done(erro ou None)."""
        def work():
            try:
                total = sum(i["bytes"] for i in items) or 1
                before = 0
                for item in items:
                    part = CACHE / (item["file"] + ".part")
                    _http_get(self._base + item["file"], part,
                              lambda f, b=before, s=item["bytes"]: GLib.idle_add(progress, (b + f * s) / total))
                    h = hashlib.sha256()
                    with open(part, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    if h.hexdigest() != item["sha256"]:
                        part.unlink()
                        raise OSError(f"o arquivo de {item['title']} veio corrompido")
                    dest = STORE_DIRS[item["kind"]]
                    dest.mkdir(parents=True, exist_ok=True)
                    with tarfile.open(part, "r:xz") as tf:
                        members = list(_safe_members(tf, dest))
                        kw = {"filter": "fully_trusted"} if hasattr(tarfile, "data_filter") else {}
                        tf.extractall(dest, members=members, **kw)
                    part.unlink()
                    before += item["bytes"]
                err = None
            except (OSError, tarfile.TarError) as e:
                err = str(e)
            GLib.idle_add(lambda: done(err) or False)
        POOL.submit(work)

    @staticmethod
    def remove(item):
        dest = STORE_DIRS[item["kind"]]
        for d in item["dirs"]:
            shutil.rmtree(dest / d, ignore_errors=True)
            link = HOME / ".icons" / d
            if link.is_symlink():
                link.unlink()


# ---------------------------------------------------------------- efeitos (vidro)

BLUR_UUID = "blur-my-shell@aunetx"
BLUR_SCHEMA = "org.gnome.shell.extensions.blur-my-shell"
ADON_PIPELINE = "pipeline_adon_studio"
NAUTILUS = ["org.gnome.Nautilus", "nautilus"]
GLASS_OPACITY = 170  # de 255; o padrão da extensão (215) quase não deixa ver o vidro
BLUR_PARTS = [  # componente da Blur my Shell, título, explicação
    ("popup", "Menus e janelinhas", "Menu de aplicativos, menus do sistema e avisos"),
    ("overview", "Visão geral", "Tela com as janelas e os aplicativos (Super+A)"),
    ("appfolder", "Pastas de aplicativos", None),
    ("lockscreen", "Tela de bloqueio", None),
    ("panel", "Barra superior", "A barra no topo da tela do GNOME"),
]
WINDOW_MODES = [("nenhuma", "Desligado"), ("arquivos", "Só no gerenciador de arquivos"),
                ("todas", "Todas as janelas (experimental)")]
BLUR_PRESETS = {
    "Sem vidro": dict(parts=[], radius=30, brightness=.6, windows="nenhuma"),
    "Vidro suave": dict(parts=["popup", "overview", "appfolder", "lockscreen", "panel"], radius=20,
                        brightness=.85, windows="nenhuma"),
    "Vidro forte": dict(parts=["popup", "overview", "appfolder", "lockscreen", "panel"], radius=50,
                        brightness=.6, windows="arquivos"),
    "Vidro total": dict(parts=["popup", "overview", "appfolder", "lockscreen", "panel"], radius=40,
                        brightness=.7, windows="todas"),
}


def enable_extension(uuid):
    """Liga uma extensão do GNOME Shell já instalada. True se ficou ligada."""
    shell = settings_for("org.gnome.shell")
    if shell and uuid in shell.get_strv("enabled-extensions"):
        return True
    try:
        Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
            "org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions", "org.gnome.Shell.Extensions",
            "EnableExtension", GLib.Variant("(s)", (uuid,)), None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error:
        if not shell:
            return False
        shell.set_strv("enabled-extensions", shell.get_strv("enabled-extensions") + [uuid])
        shell.set_strv("disabled-extensions", [u for u in shell.get_strv("disabled-extensions") if u != uuid])
    return True


def extension_installed(uuid):
    return any((d / "gnome-shell" / "extensions" / uuid).is_dir() for d in [USER_DATA] + SYS_DATA)


class Blur:
    """Controle do vidro via extensão Blur my Shell. A versão nova (GNOME 45+) guarda a intensidade
    num 'pipeline'; a antiga (GNOME 42, Zorin 17) em sigma/brightness."""

    def __init__(self, schema=BLUR_SCHEMA):
        self.root = settings_for(schema)
        names = [p for p, _t, _s in BLUR_PARTS] + ["applications"]
        self.parts = {p: s for p in names if (s := settings_for(f"{schema}.{p}"))} if self.root else {}

    @staticmethod
    def installed():
        return extension_installed(BLUR_UUID)

    @staticmethod
    def install(done):
        """Pede ao GNOME para instalar a extensão (ele mostra a confirmação). done('ok' ou erro)."""
        def finished(bus, res):
            try:
                answer = bus.call_finish(res).unpack()[0]
                done("ok" if answer == "successful" else answer)
            except GLib.Error as e:
                # o Shell às vezes encerra a chamada sem responder mesmo tendo instalado
                err = e.message
                GLib.timeout_add(1500, lambda: done("ok" if Blur.installed() else err) or False)
        Gio.bus_get_sync(Gio.BusType.SESSION).call(
            "org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions", "org.gnome.Shell.Extensions",
            "InstallRemoteExtension", GLib.Variant("(s)", (BLUR_UUID,)), GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE, -1, None, finished)

    @property
    def modern(self):
        return has_key(self.root, "pipelines")

    def get(self, part):
        s = self.parts.get(part)
        return bool(s) and s.get_boolean("blur")

    def set(self, part, on):
        if part in self.parts:
            self.parts[part].set_boolean("blur", on)

    def adapt(self, zorin):
        """No Zorin a barra superior do GNOME fica escondida: vidro nela vira um bloco solto na tela."""
        if zorin:
            self.set("panel", False)

    def windows(self):
        s = self.parts.get("applications")
        if not s or not s.get_boolean("blur"):
            return "nenhuma"
        return "todas" if s.get_boolean("enable-all") else "arquivos"

    def set_windows(self, mode):
        s = self.parts.get("applications")
        if not s:
            return
        if mode != "nenhuma" and self.windows() == "nenhuma" and has_key(s, "opacity"):
            s.set_int("opacity", min(s.get_int("opacity"), GLASS_OPACITY))
        others = [a for a in s.get_strv("whitelist") if a not in NAUTILUS]
        s.set_strv("whitelist", others + (NAUTILUS if mode == "arquivos" else []))
        s.set_boolean("enable-all", mode == "todas")
        # a extensão deixa opaca a janela em foco; aí a janela que você está usando nunca fica de vidro
        if has_key(s, "dynamic-opacity") and mode != "nenhuma":
            s.set_boolean("dynamic-opacity", False)
        s.set_boolean("blur", mode != "nenhuma")

    def strength(self):
        """(raio do desfoque, brilho)"""
        if not self.modern:
            return self.root.get_int("sigma"), round(self.root.get_double("brightness"), 2)
        pipes = self.root.get_value("pipelines").unpack()
        for name in (ADON_PIPELINE, "pipeline_default"):
            for effect in pipes.get(name, {}).get("effects", []):
                params = effect.get("params", {})
                if "unscaled_radius" in params:
                    return params["unscaled_radius"], round(params.get("brightness", .6), 2)
        return 30, .6

    def set_strength(self, radius, brightness):
        radius, brightness = int(radius), round(float(brightness), 2)
        if not self.modern:
            self.root.set_int("sigma", radius)
            self.root.set_double("brightness", brightness)
            for s in self.parts.values():
                if has_key(s, "customize"):
                    s.set_boolean("customize", False)
            return
        # mantém os pipelines que já existem e grava/atualiza só o nosso
        old, entries = self.root.get_value("pipelines"), []
        for i in range(old.n_children()):
            kv = old.get_child_value(i)
            name = kv.get_child_value(0).get_string()
            if name != ADON_PIPELINE:
                entries.append(f"{GLib.Variant('s', name).print_(False)}: {kv.get_child_value(1).print_(True)}")
        entries.append(f"'{ADON_PIPELINE}': {{'name': <'Adon Studio'>, 'effects': <[<{{'type': <'dual_kawase_blur'>, "
                       f"'id': <'effect_adonstudio0'>, 'params': <{{'unscaled_radius': <{radius}>, "
                       f"'brightness': <{brightness:.2f}>}}>}}>]>}}")
        self.root.set_value("pipelines", GLib.Variant.parse(GLib.VariantType("a{sa{sv}}"),
                                                            "{" + ", ".join(entries) + "}", None, None))
        for s in self.parts.values():
            if has_key(s, "pipeline"):
                s.set_string("pipeline", ADON_PIPELINE)

    def apply_preset(self, p, zorin):
        for part, _t, _s in BLUR_PARTS:
            self.set(part, part in p["parts"])
        self.set_windows(p["windows"])
        self.set_strength(p["radius"], p["brightness"])
        self.adapt(zorin)

    def snapshot(self):
        return {p: self.get(p) for p in self.parts} | {"_windows": self.windows()}

    def restore(self, saved):
        """Volta ao que estava no backup; sem backup do vidro, desliga tudo."""
        saved = saved or {}
        for p in self.parts:
            if p != "applications":
                self.set(p, saved.get(p, False))
        self.set_windows(saved.get("_windows", "nenhuma"))


# ---------------------------------------------------------------- cores e desenho

def rgba(text):
    c = Gdk.RGBA()
    return c if text and c.parse(text.strip()) else None


def mix(a, b, t):
    c = Gdk.RGBA()
    c.red, c.green, c.blue = (a.red + (b.red - a.red) * t, a.green + (b.green - a.green) * t,
                              a.blue + (b.blue - a.blue) * t)
    c.alpha = 1
    return c


NAMED_ACCENTS = {"blue": "#3584e4", "brown": "#986a44", "green": "#2ec27e", "grey": "#8a8f98", "orange": "#ff7800",
                 "purple": "#9141ac", "red": "#e01b24", "yellow": "#f6d32d", "teal": "#2190a4", "pink": "#e66197",
                 "mauve": "#cba6f7", "peach": "#fab387", "lavender": "#b4befe"}
DEFINE = re.compile(r"@define-color\s+([\w-]+)\s+([^;]+);")
BG_RULE = re.compile(r"(?:^|[\s,}])window(?:\.background)?\s*\{[^}]*?background-color:\s*([^;]+);", re.S)


def theme_palette(theme):
    """(fundo, conteúdo, texto, destaque) lidos do CSS do tema, com chute pelo nome se não der."""
    css = ""
    for f in (theme.path / "gtk-4.0" / "gtk.css", theme.path / "gtk-3.0" / "gtk.css"):
        if f.exists():
            css = f.read_text(errors="ignore")
            if "@define-color" in css:
                break
    defs = dict(DEFINE.findall(css))

    def get(*names):
        for n in names:
            v, hops = defs.get(n), 0
            while v and v.strip().startswith("@") and hops < 6:
                v, hops = defs.get(v.strip()[1:]), hops + 1
            c = rgba(v)
            if c:
                return c
        return None

    dark = "light" not in theme.name.lower()
    bg = get("window_bg_color", "theme_bg_color", "bg_color")
    if not bg:
        m = BG_RULE.search(css)
        bg = rgba(m.group(1)) if m else None
    bg = bg or rgba("#2b2b2b" if dark else "#f2f2f2")
    fg = get("window_fg_color", "theme_fg_color", "fg_color") or rgba("#eeeeee" if dark else "#222222")
    base = get("view_bg_color", "theme_base_color", "base_color") or mix(bg, rgba("#000000" if dark else "#ffffff"), .25)
    acc = get("accent_bg_color", "accent_color", "theme_selected_bg_color", "selected_bg_color")
    if not acc:
        word = next((w for w in NAMED_ACCENTS if w in theme.name.lower()), "blue")
        acc = rgba(NAMED_ACCENTS[word])
    return bg, base, fg, acc


def _src(cr, c, a=1.0):
    cr.set_source_rgba(c.red, c.green, c.blue, c.alpha * a)


def _rr(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, pi / 2)
    cr.arc(x + r, y + h - r, r, pi / 2, pi)
    cr.arc(x + r, y + r, r, pi, 3 * pi / 2)
    cr.close_path()


def draw_backdrop(cr, w, h, wall):
    import cairo
    c1, c2, g1, g2 = map(rgba, wall)
    _rr(cr, 0, 0, w, h, 12)
    cr.save()
    cr.clip()
    lin = cairo.LinearGradient(0, 0, w, h)
    lin.add_color_stop_rgb(0, c1.red, c1.green, c1.blue)
    lin.add_color_stop_rgb(1, c2.red, c2.green, c2.blue)
    cr.set_source(lin)
    cr.paint()
    for (cx, cy, c) in ((.8, .2, g1), (.15, .9, g2)):
        rad = cairo.RadialGradient(w * cx, h * cy, 0, w * cx, h * cy, w * .6)
        rad.add_color_stop_rgba(0, c.red, c.green, c.blue, .5)
        rad.add_color_stop_rgba(1, c.red, c.green, c.blue, 0)
        cr.set_source(rad)
        cr.paint()
    cr.restore()


def draw_window(cr, x, y, w, h, pal):
    """Janelinha de mentira com as cores do tema."""
    bg, base, fg, acc = pal
    _src(cr, rgba("#000000"), .25)
    _rr(cr, x + 1, y + 3, w, h, 9)
    cr.fill()
    _rr(cr, x, y, w, h, 9)
    cr.save()
    cr.clip()
    _src(cr, base)
    cr.paint()
    side = w * .32
    _src(cr, mix(bg, fg, .03))
    cr.rectangle(x, y, side, h)
    cr.fill()
    _src(cr, mix(bg, fg, .06))
    cr.rectangle(x, y, w, 22)
    cr.fill()
    for i, c in enumerate((fg, fg, acc)):
        _src(cr, c, .35 if i < 2 else 1)
        cr.arc(x + w - 12 - (2 - i) * 13, y + 11, 4, 0, 2 * pi)
        cr.fill()
    for i in range(4):
        _src(cr, acc if i == 0 else fg, .45 if i == 0 else .22)
        _rr(cr, x + 8, y + 32 + i * 16, side - 16, 10, 4)
        cr.fill()
    cx = x + side + 12
    for i, frac in enumerate((.7, .5, .6)):
        _src(cr, fg, .5 if i == 0 else .25)
        _rr(cr, cx, y + 34 + i * 14, (w - side - 24) * frac, 7, 3)
        cr.fill()
    _src(cr, acc)
    _rr(cr, cx, y + h - 30, 58, 18, 9)
    cr.fill()
    _src(cr, acc)
    _rr(cr, x + w - 46, y + h - 28, 32, 15, 7.5)
    cr.fill()
    _src(cr, rgba("#ffffff"))
    cr.arc(x + w - 21.5, y + h - 20.5, 5.5, 0, 2 * pi)
    cr.fill()
    cr.restore()


def drawing(width, height, func):
    area = Gtk.DrawingArea(content_width=width, content_height=height)
    area.set_draw_func(lambda _a, cr, w, h: func(cr, w, h))
    return area


# ---------------------------------------------------------------- prévias de ícones e cursores

ICON_SAMPLES = [["folder", "inode-directory"], ["user-home", "folder-home"],
                ["web-browser", "firefox", "google-chrome", "brave-browser"],
                ["utilities-terminal", "org.gnome.Terminal", "terminal"],
                ["org.gnome.Settings", "preferences-system", "gnome-control-center"],
                ["accessories-text-editor", "org.gnome.TextEditor", "text-editor"],
                ["user-trash-full", "user-trash"]]


def icon_preview(name, size=34):
    theme = Gtk.IconTheme()
    theme.set_search_path([str(d) for d in ICON_DIRS] + ["/usr/share/pixmaps"])
    theme.set_theme_name(name)
    box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=10)
    for names in ICON_SAMPLES:
        paint = theme.lookup_icon(names[0], names[1:], size, 1, Gtk.TextDirection.NONE, 0)
        box.append(Gtk.Image(paintable=paint, pixel_size=size))
    return box


CURSOR_SAMPLES = [["left_ptr", "default", "arrow"], ["hand2", "pointer", "pointing_hand"],
                  ["xterm", "text", "ibeam"], ["watch", "wait"], ["fleur", "move", "grabbing"]]


def load_xcursor(path, want=32):
    """Lê a primeira imagem (do tamanho mais próximo de `want`) de um arquivo Xcursor."""
    data = path.read_bytes()
    if data[:4] != b"Xcur":
        return None
    hsize, _ver, ntoc = struct.unpack_from("<III", data, 4)
    imgs = [struct.unpack_from("<III", data, hsize + i * 12) for i in range(ntoc)]
    imgs = [t for t in imgs if t[0] == 0xFFFD0002]
    if not imgs:
        return None
    pos = min(imgs, key=lambda t: abs(t[1] - want))[2]
    w, h = struct.unpack_from("<II", data, pos + 16)
    px = GLib.Bytes.new(data[pos + 36:pos + 36 + w * h * 4])
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED, px, w * 4)


def cursor_preview(path):
    box = Gtk.Box(spacing=14, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=10)
    for names in CURSOR_SAMPLES:
        f = next((path / "cursors" / n for n in names if (path / "cursors" / n).exists()), None)
        try:
            tex = load_xcursor(f.resolve()) if f else None
        except (OSError, struct.error):
            tex = None
        if tex:
            box.append(Gtk.Image(paintable=tex, pixel_size=32))
    return box


# ---------------------------------------------------------------- combos prontos e wallpapers gerados

# wallpaper: (cor inicial, cor final, brilho 1, brilho 2)
WALL_PALETTES = {
    "catppuccin": ("#1e1e2e", "#11111b", "#cba6f7", "#f5c2e7"),
    "catppuccin-rosa": ("#1e1e2e", "#11111b", "#f5c2e7", "#fab387"),
    "tokyonight": ("#1a1b26", "#0f0f17", "#7aa2f7", "#bb9af7"),
    "nord": ("#2e3440", "#1f232b", "#88c0d0", "#81a1c1"),
    "dracula": ("#282a36", "#191a21", "#bd93f9", "#ff79c6"),
    "gruvbox": ("#282828", "#1d2021", "#fe8019", "#fabd2f"),
    "everforest": ("#2d353b", "#1e2326", "#a7c080", "#83c092"),
    "rosepine": ("#191724", "#100e17", "#ebbcba", "#c4a7e7"),
    "kanagawa": ("#1f1f28", "#16161d", "#7e9cd8", "#957fb8"),
    "colloid": ("#242424", "#161616", "#a07cf0", "#5eadf2"),
    "fluent": ("#1c1c2b", "#0c0c14", "#4cc2ff", "#8a63ff"),
    "orchis": ("#212121", "#121212", "#1a73e8", "#34a853"),
    "graphite": ("#1a1a1a", "#0b0b0b", "#9e9e9e", "#5c5c5c"),
    "whitesur": ("#1c1c1e", "#0a0a0c", "#0a84ff", "#bf5af2"),
    "por-do-sol": ("#1b1029", "#0d0716", "#ff6b6b", "#feca57"),
    "oceano": ("#0b1d2a", "#04101a", "#00b4d8", "#48cae4"),
    "aurora": ("#0f1a1f", "#060d10", "#2ee6a6", "#7b61ff"),
    "neon": ("#120d1d", "#07040d", "#ff2e97", "#00e5ff"),
}

PACKS = [
    # nome, tema GTK, ícones, cursor, wallpaper (cada item: candidatos em ordem de preferência)
    ("Catppuccin Mocha", ["Catppuccin-Mauve-Dark", "Catppuccin-Dark"], ["Catppuccin-Mocha", "Papirus-Dark"],
     ["catppuccin-mocha-mauve-cursors", "Bibata-Modern-Classic"], "catppuccin"),
    ("Catppuccin Rosa", ["Catppuccin-Pink-Dark"], ["Catppuccin-Mocha", "Papirus-Dark"],
     ["catppuccin-mocha-pink-cursors", "Bibata-Modern-Classic"], "catppuccin-rosa"),
    ("Tokyo Night", ["Tokyonight-Dark"], ["Tokyonight-Dark", "Papirus-Dark"], ["Bibata-Modern-Ice"], "tokyonight"),
    ("Nord", ["Nordic"], ["Nordzy-dark", "Papirus-Dark"], ["Bibata-Modern-Ice"], "nord"),
    ("Dracula", ["Dracula"], ["Dracula", "Papirus-Dark"], ["Bibata-Modern-Classic"], "dracula"),
    ("Gruvbox", ["Gruvbox-Dark"], ["Gruvbox-Dark", "Papirus-Dark"], ["Bibata-Modern-Amber"], "gruvbox"),
    ("Everforest", ["Everforest-Dark"], ["Everforest-Dark", "Papirus-Dark"], ["Bibata-Modern-Classic"], "everforest"),
    ("Rosé Pine", ["Rosepine-Dark"], ["Papirus-Dark"],
     ["catppuccin-mocha-pink-cursors", "Bibata-Modern-Classic"], "rosepine"),
    ("Kanagawa", ["Kanagawa-Dark"], ["Kanagawa", "Papirus-Dark"], ["Bibata-Modern-Classic"], "kanagawa"),
    ("Colloid Roxo", ["Colloid-Purple-Dark", "Colloid-Dark"], ["Colloid-Dark", "Papirus-Dark"],
     ["Colloid-dark-cursors", "Bibata-Modern-Classic"], "colloid"),
    ("Fluent", ["Fluent-Dark"], ["Fluent-dark", "Papirus-Dark"], ["Bibata-Modern-Classic"], "fluent"),
    ("Orchis", ["Orchis-Dark"], ["Colloid-Dark", "Papirus-Dark"], ["Bibata-Modern-Classic"], "orchis"),
    ("Graphite", ["Graphite-Dark"], ["Colloid-Dark", "Papirus-Dark"], ["Bibata-Modern-Classic"], "graphite"),
    ("Qogir", ["Qogir-Dark"], ["Qogir-dark", "Qogir", "Papirus-Dark"], ["Qogir-Dark", "Bibata-Modern-Classic"], "oceano"),
    ("Estilo macOS", ["WhiteSur-Dark"], ["WhiteSur-dark", "Papirus-Dark"], ["WhiteSur-cursors"], "whitesur"),
]


def wall_svg(c1, c2, g1, g2):
    waves = "".join(
        f'<path d="M0 {1500 + i * 120} Q960 {1150 + i * 150} 1920 {1450 + i * 130} T3840 {1300 + i * 140}"/>'
        for i in range(4))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="3840" height="2160" viewBox="0 0 3840 2160">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>
<radialGradient id="a" cx=".78" cy=".28" r=".55"><stop offset="0" stop-color="{g1}" stop-opacity=".55"/><stop offset="1" stop-color="{g1}" stop-opacity="0"/></radialGradient>
<radialGradient id="b" cx=".18" cy=".82" r=".6"><stop offset="0" stop-color="{g2}" stop-opacity=".45"/><stop offset="1" stop-color="{g2}" stop-opacity="0"/></radialGradient></defs>
<rect width="3840" height="2160" fill="url(#g)"/><rect width="3840" height="2160" fill="url(#a)"/><rect width="3840" height="2160" fill="url(#b)"/>
<g fill="none" stroke="{g1}" stroke-opacity=".16" stroke-width="3">{waves}</g>
</svg>
'''


def ensure_wallpapers():
    WALLS.mkdir(parents=True, exist_ok=True)
    for name, colors in WALL_PALETTES.items():
        f = WALLS / f"{name}.svg"
        if not f.exists():
            f.write_text(wall_svg(*colors))


# ---------------------------------------------------------------- interface

CSS = """
.studio-card { padding: 10px; border-radius: 16px; }
.studio-card.atual { box-shadow: inset 0 0 0 3px @accent_bg_color; }
.studio-card .titulo { font-weight: 700; }
.studio-card .sub { font-size: 0.85em; opacity: 0.65; }
.studio-thumb { border-radius: 10px; }
.studio-pill { font-size: 0.85em; font-weight: 700; padding: 2px 10px; border-radius: 99px;
               background: alpha(@accent_bg_color, 0.2); color: @accent_color; }
.studio-pill.ok { background: alpha(@success_color, 0.2); color: @success_color; }
.studio-notice { padding: 10px 16px; margin: 12px 18px 0 18px; }
.studio-veil { background: alpha(@window_bg_color, 0.6); border-radius: 12px; }
.studio-done { color: @success_color; background: alpha(@success_color, 0.18); border-radius: 99px;
               padding: 10px; }
"""

# Atalhos que funcionam também na libadwaita 1.1 / GTK 4.6 (Ubuntu 22.04, Zorin 17, Pop!_OS 22.04)


def switch_row(title, subtitle=None):
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    sw = Gtk.Switch(valign=Gtk.Align.CENTER)
    row.add_suffix(sw)
    row.set_activatable_widget(sw)
    return row, sw


def spin_row(title, lo, hi, subtitle=None):
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    spin = Gtk.SpinButton.new_with_range(lo, hi, 1)
    spin.set_valign(Gtk.Align.CENTER)
    row.add_suffix(spin)
    return row, spin


def picture(**kw):
    pic = Gtk.Picture(**kw)
    if hasattr(Gtk, "ContentFit"):
        pic.set_content_fit(Gtk.ContentFit.COVER)
    else:
        pic.set_keep_aspect_ratio(False)
    return pic


class Spinner(Gtk.DrawingArea):
    """Arco girando na cor de destaque. O Gtk.Spinner usa o ícone do tema de ícones ativo e muda
    de cara (ou some) a cada tema; este fica igual em qualquer um."""

    def __init__(self, size=40):
        super().__init__(content_width=size, content_height=size)
        self._angle, self._tick = 0.0, None
        self.set_draw_func(self._draw)

    def start(self):
        if self._tick is None:
            self._tick = self.add_tick_callback(self._step)

    def stop(self):
        if self._tick is not None:
            self.remove_tick_callback(self._tick)
            self._tick = None

    def _step(self, _w, clock):
        self._angle = (clock.get_frame_time() / 1e6 * 2 * pi * 1.2) % (2 * pi)
        self.queue_draw()
        return True

    def _draw(self, _a, cr, w, h):
        ok, acc = False, None
        try:
            ok, acc = self.get_style_context().lookup_color("accent_bg_color")
        except (AttributeError, TypeError):
            pass
        acc = acc if ok else rgba("#cba6f7")
        r, lw = min(w, h) / 2 - 3, 4
        _src(cr, acc, .22)
        cr.set_line_width(lw)
        cr.arc(w / 2, h / 2, r, 0, 2 * pi)
        cr.stroke()
        _src(cr, acc)
        cr.set_line_cap(1)  # redondo
        cr.arc(w / 2, h / 2, r, self._angle, self._angle + pi * 1.4)
        cr.stroke()


def thumb_texture(path, width, height):
    """Imagem já reduzida ao tamanho do cartão: a Gtk.Picture usa o tamanho da imagem como largura
    natural, e uma imagem grande esticaria o cartão (e a grade inteira junto)."""
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), width, height, True)
    return Gdk.Texture.new_for_pixbuf(pb)


def confirm(parent, heading, body, action, on_yes):
    if hasattr(Adw, "MessageDialog"):
        dlg = Adw.MessageDialog(transient_for=parent, heading=heading, body=body)
        dlg.add_response("cancel", "Cancelar")
        dlg.add_response("ok", action)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: r == "ok" and on_yes())
    else:
        dlg = Gtk.MessageDialog(transient_for=parent, modal=True, buttons=Gtk.ButtonsType.OK_CANCEL,
                                text=heading, secondary_text=body)
        dlg.connect("response", lambda d, r: (d.destroy(), r == Gtk.ResponseType.OK and on_yes()))
    dlg.present()


def mb(n):
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


class Grid(Gtk.Box):
    """Grade de cartões clicáveis com busca."""

    def __init__(self, on_pick, extra=None, search=True, scroll=True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_pick, self.cards, self.query, self.kind, self.working = on_pick, {}, "", None, None
        bar = Gtk.Box(spacing=8, margin_start=18, margin_end=18, margin_top=12)
        if search:
            entry = Gtk.SearchEntry(placeholder_text="Buscar…", hexpand=True)
            entry.connect("search-changed", self._search)
            bar.append(entry)
        if extra:
            bar.append(extra)
        if search or extra:
            self.append(bar)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, valign=Gtk.Align.START,
                                max_children_per_line=6, min_children_per_line=1, row_spacing=14,
                                column_spacing=14, margin_start=18, margin_end=18, margin_top=12, margin_bottom=18)
        self.flow.set_filter_func(self._visible)
        if scroll:
            self.append(Gtk.ScrolledWindow(child=self.flow, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        else:  # dentro de uma página que já rola (Adw.PreferencesPage)
            self.flow.set_margin_start(0)
            self.flow.set_margin_end(0)
            self.append(self.flow)

    def _visible(self, child):
        btn = child.get_child()
        return self.query in btn.search_text and (self.kind is None or btn.kind == self.kind)

    def _search(self, entry):
        self.query = entry.get_text().lower()
        self.flow.invalidate_filter()

    def filter_kind(self, kind):
        self.kind = kind
        self.flow.invalidate_filter()

    def add(self, key, preview, title, subtitle=None, extra=None, kind=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(preview)
        # max_width_chars segura a largura natural: nomes compridos não esticam a grade inteira
        box.append(Gtk.Label(label=title, xalign=0, ellipsize=3, max_width_chars=20, css_classes=["titulo"]))
        if subtitle:
            box.append(Gtk.Label(label=subtitle, xalign=0, ellipsize=3, max_width_chars=24, css_classes=["sub"]))
        if extra:
            box.append(extra)
        # véu do "carregando": círculo girando enquanto aplica, ✓ quando termina
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, halign=Gtk.Align.CENTER,
                          valign=Gtk.Align.CENTER, hexpand=True, vexpand=True)
        stack.add_named(Spinner(40), "load")
        stack.add_named(Gtk.Image(icon_name="object-select-symbolic", pixel_size=26,
                                  css_classes=["studio-done"]), "ok")
        veil = Gtk.Box(css_classes=["studio-veil"], visible=False)
        veil.append(stack)
        overlay = Gtk.Overlay(child=box)
        overlay.add_overlay(veil)
        btn = Gtk.Button(child=overlay, css_classes=["card", "studio-card"], tooltip_text=title)
        btn.search_text, btn.kind = f"{title} {subtitle or ''}".lower(), kind
        btn.veil, btn.veil_stack = veil, stack
        btn.connect("clicked", lambda _b: self.working is None and self.on_pick(key))
        self.flow.append(btn)
        self.cards[key] = btn
        return btn

    def start(self, key):
        """Cartão em 'carregando'; cliques na grade ficam bloqueados até finish()."""
        self.working = key
        btn = self.cards.get(key)
        if btn:
            btn.veil_stack.set_visible_child_name("load")
            btn.veil_stack.get_child_by_name("load").start()
            btn.veil.set_visible(True)

    def finish(self, key, ok=True):
        self.working = None
        btn = self.cards.get(key)
        if not btn:
            return
        btn.veil_stack.get_child_by_name("load").stop()
        if not ok:
            btn.veil.set_visible(False)
            return
        btn.veil_stack.set_visible_child_name("ok")
        btn.veil.set_visible(True)  # o cartão pode ter sido recriado depois de um download
        GLib.timeout_add(900, lambda: btn.veil.set_visible(False) or False)

    def mark(self, key):
        for k, btn in self.cards.items():
            (btn.add_css_class if k == key else btn.remove_css_class)("atual")


def pretty(name):
    return re.sub(r"[-_]+", " ", name)


def add_batched(items, fn, done=None):
    """Monta os cartões aos poucos para a janela não travar."""
    it = iter(items)

    def step():
        for _ in range(4):
            try:
                fn(next(it))
            except StopIteration:
                if done:
                    done()
                return False
        return True
    GLib.idle_add(step)


class Window(Adw.ApplicationWindow):
    PAGES = [
        ("combos", "Combos", "view-grid-symbolic", "_build_combos"),
        ("temas", "Temas", "preferences-desktop-appearance-symbolic", "_build_themes"),
        ("icones", "Ícones", "folder-pictures-symbolic", "_build_icons"),
        ("cursores", "Cursores", "input-mouse-symbolic", "_build_cursors"),
        ("wallpaper", "Wallpaper", "preferences-desktop-wallpaper-symbolic", "_build_walls"),
        ("barra", "Barra", "view-continuous-symbolic", "_build_bar"),
        ("efeitos", "Efeitos", "weather-fog-symbolic", "_build_effects"),
        ("loja", "Loja", "system-software-install-symbolic", "_build_store"),
    ]
    KIND_PAGE = {"gtk": "Temas", "icons": "Ícones", "cursors": "Cursores"}

    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=1180, default_height=800)
        self.desk = Desktop()
        self.desk.ensure_backup()
        self.desk.link_cursor_themes()
        self.user_theme_ok = self.desk.ensure_user_theme()
        ensure_wallpapers()
        self.store = Store()
        self.busy = False
        self._scan()

        self.toasts = Adw.ToastOverlay()
        self.stack = Adw.ViewStack(vexpand=True)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if not self.user_theme_ok:
            body.append(Gtk.Label(wrap=True, xalign=0, css_classes=["card", "studio-notice"],
                                  label="O tema das janelas funciona, mas para mudar também o painel do sistema "
                                        "falta a extensão “User Themes” (pacote gnome-shell-extensions)."))
        self.prog = Gtk.ProgressBar(show_text=True, margin_start=18, margin_end=18, margin_top=10)
        self.prog_bar = Gtk.Revealer(child=self.prog)
        body.append(self.prog_bar)
        body.append(self.stack)
        self.toasts.set_child(body)

        header = Adw.HeaderBar(title_widget=Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        restore = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Voltar ao visual original")
        restore.connect("clicked", self._ask_restore)
        header.pack_start(restore)
        about = Gtk.Button(icon_name="help-about-symbolic", tooltip_text=f"Sobre o {APP_NAME}")
        about.connect("clicked", self._about)
        header.pack_end(about)
        if hasattr(Adw, "ToolbarView"):
            view = Adw.ToolbarView(content=self.toasts)
            view.add_top_bar(header)
        else:
            view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            view.append(header)
            self.toasts.set_vexpand(True)
            view.append(self.toasts)
        self.set_content(view)

        self.grids, self.builders = {}, {}
        for name, title, icon, builder in self.PAGES:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.stack.add_titled(holder, name, title).set_icon_name(icon)
            self.builders[name] = (holder, getattr(self, builder))
        self.stack.connect("notify::visible-child-name", lambda *_: self._ensure_built())
        self._ensure_built()
        self.store.fetch(self._on_catalog)

    def _scan(self):
        self.themes = {t.name: t for t in list_gtk_themes()}
        self.icons = list_icon_themes()
        self.cursors = dict(list_cursor_themes())

    def _ensure_built(self):
        name = self.stack.get_visible_child_name()
        holder, builder = self.builders.get(name, (None, None))
        if builder:
            self.builders[name] = (holder, None)
            builder(holder)

    def rebuild(self, *names):
        """Relê o que está instalado e refaz as abas que já foram abertas."""
        self._scan()
        self.desk.link_cursor_themes()
        for name, _t, _i, builder in self.PAGES:
            holder, pending = self.builders[name]
            if (names and name not in names) or pending or name == "barra":
                continue
            while child := holder.get_first_child():
                holder.remove(child)
            self.grids.pop(name, None)
            self.builders[name] = (holder, getattr(self, builder))
        self._ensure_built()

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    MIN_LOAD_MS = 600  # o sistema leva um instante para redesenhar; abaixo disso a animação só pisca

    def _feedback(self, page, key, apply, message):
        """Carregando no cartão → aplica → ✓. O atraso inicial deixa o círculo aparecer antes do trabalho."""
        grid = self.grids.get(page)
        if grid:
            grid.start(key)

        def run():
            try:
                apply()
            except Exception:
                if grid:
                    grid.finish(key, ok=False)
                raise
            GLib.timeout_add(self.MIN_LOAD_MS, done)
            return False

        def done():
            if grid:
                grid.finish(key)
            self.refresh_marks()
            self.toast(message)
            return False
        GLib.timeout_add(80, run)

    def refresh_marks(self):
        i = self.desk.iface
        current = {"temas": i.get_string("gtk-theme"), "icones": i.get_string("icon-theme"),
                   "cursores": i.get_string("cursor-theme"),
                   "wallpaper": Gio.File.new_for_uri(self.desk.bg.get_string("picture-uri")).get_path()}
        for name, grid in self.grids.items():
            if name in current:
                grid.mark(current[name])

    def _download(self, items, label, then, fail=None):
        if self.busy:
            self.toast("Espere o download atual terminar")
            if fail:
                fail()
            return
        self.busy = True
        self.prog.set_text(label)
        self.prog.set_fraction(0)
        self.prog_bar.set_reveal_child(True)

        def done(err):
            self.busy = False
            self.prog_bar.set_reveal_child(False)
            if err:
                self.toast(f"Não deu para baixar: {err}")
                if fail:
                    fail()
                return
            self.rebuild()
            then()
        self.store.install(items, self.prog.set_fraction, done)

    def _on_catalog(self, ok):
        if ok:
            self.rebuild("combos", "loja")

    # --- combos
    def _resolve(self, pack):
        title, gtks, icons, cursors, wall = pack
        need = []

        def pick(cands, installed):
            hit = next((c for c in cands if c in installed), None)
            if hit:
                return hit
            for c in cands:
                item = self.store.provider(c)
                if item:
                    need.append(item)
                    return c
            return None
        gtk = pick(gtks, self.themes)
        if not gtk:
            return None
        return dict(title=title, gtk=gtk, icon=pick(icons, self.icons), cursor=pick(cursors, self.cursors),
                    wall=wall, need=self.store.with_needs(need))

    def _build_combos(self, holder):
        grid = self.grids["combos"] = Grid(self._pick_combo, search=False)
        holder.append(grid)
        self.packs = {}
        for pack in PACKS:
            p = self._resolve(pack)
            if not p:
                continue
            self.packs[p["title"]] = p
            colors = WALL_PALETTES[p["wall"]]
            if p["gtk"] in self.themes:
                pal = theme_palette(self.themes[p["gtk"]])
            else:
                pal = (rgba(colors[0]), mix(rgba(colors[1]), rgba("#000000"), .2), rgba("#e6e6f0"), rgba(colors[2]))

            def paint(cr, w, h, pal=pal, colors=colors):
                draw_backdrop(cr, w, h, colors)
                draw_window(cr, 26, 22, w - 52, h - 44, pal)
            if p["need"]:
                sub = f"Baixa {mb(sum(i['bytes'] for i in p['need']))} ao aplicar"
            else:
                sub = " · ".join(pretty(x) for x in (p["gtk"], p["icon"], p["cursor"]) if x)
            grid.add(p["title"], drawing(260, 160, paint), p["title"], sub)

    def _pick_combo(self, title):
        p = self.packs[title]
        if not p["need"]:
            self._feedback("combos", title, lambda: self._apply_combo(title), f"Combo {title} aplicado")
            return
        self.grids["combos"].start(title)
        # depois do download a aba é refeita (rebuild), então o cartão novo recebe o carregando de novo
        self._download(p["need"], f"Baixando o combo {title}…",
                       lambda: self._feedback("combos", title, lambda: self._apply_combo(title),
                                              f"Combo {title} aplicado"),
                       fail=lambda: self.grids["combos"].finish(title, ok=False))

    def _apply_combo(self, title):
        p = self._resolve(next(x for x in PACKS if x[0] == title))
        if p["gtk"] in self.themes:
            self.desk.apply_gtk(self.themes[p["gtk"]])
        if p["icon"] in self.icons:
            self.desk.apply_icons(p["icon"])
        if p["cursor"] in self.cursors:
            self.desk.apply_cursor(p["cursor"])
        self.desk.apply_wallpaper(WALLS / f"{p['wall']}.svg")

    # --- temas
    def _build_themes(self, holder):
        grid = self.grids["temas"] = Grid(self._pick_theme)
        holder.append(grid)

        def add(theme):
            pal = theme_palette(theme)
            grid.add(theme.name, drawing(240, 140, lambda cr, w, h, pal=pal: draw_window(cr, 6, 6, w - 12, h - 14, pal)),
                     pretty(theme.name))
        add_batched(self.themes.values(), add, self.refresh_marks)

    def _pick_theme(self, name):
        self._feedback("temas", name, lambda: self.desk.apply_gtk(self.themes[name]),
                       f"Tema {pretty(name)} aplicado — apps abertos podem precisar ser reabertos")

    # --- ícones
    def _build_icons(self, holder):
        grid = self.grids["icones"] = Grid(self._pick_icons)
        holder.append(grid)
        add_batched(self.icons, lambda n: grid.add(n, icon_preview(n), pretty(n)), self.refresh_marks)

    def _pick_icons(self, name):
        self._feedback("icones", name, lambda: self.desk.apply_icons(name), f"Ícones {pretty(name)} aplicados")

    # --- cursores
    def _build_cursors(self, holder):
        grid = self.grids["cursores"] = Grid(self._pick_cursor)
        holder.append(grid)
        add_batched(self.cursors.items(), lambda kv: grid.add(kv[0], cursor_preview(kv[1]), pretty(kv[0])),
                    self.refresh_marks)

    def _pick_cursor(self, name):
        self._feedback("cursores", name, lambda: self.desk.apply_cursor(name),
                       f"Cursor {pretty(name)} aplicado — alguns apps só mudam ao reabrir")

    # --- wallpapers
    def _build_walls(self, holder):
        choose = Gtk.Button(label="Escolher imagem…", css_classes=["suggested-action"])
        choose.connect("clicked", self._choose_wall)
        grid = self.grids["wallpaper"] = Grid(self._pick_wall, extra=choose)
        holder.append(grid)

        def add(path):
            pic = picture(width_request=240, height_request=135, css_classes=["studio-thumb"],
                          overflow=Gtk.Overflow.HIDDEN)
            grid.add(str(path), pic, pretty(path.stem))

            def work():
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), 360, 202, True)
                except GLib.Error:
                    return
                GLib.idle_add(lambda: pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb)) or False)
            POOL.submit(work)
        add_batched(list_wallpapers(), add, self.refresh_marks)

    def _pick_wall(self, path):
        self._feedback("wallpaper", str(path), lambda: self.desk.apply_wallpaper(path), "Wallpaper aplicado")

    def _choose_wall(self, _btn):
        filt = Gtk.FileFilter(name="Imagens")
        filt.add_pixbuf_formats()
        if hasattr(Gtk, "FileDialog"):
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(filt)
            dialog = Gtk.FileDialog(title="Escolher wallpaper", filters=filters)

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                self._pick_wall(f.get_path())
            dialog.open(self, None, done)
        else:
            self._chooser = Gtk.FileChooserNative(title="Escolher wallpaper", transient_for=self,
                                                  action=Gtk.FileChooserAction.OPEN)
            self._chooser.add_filter(filt)
            self._chooser.connect("response", lambda d, r: r == Gtk.ResponseType.ACCEPT and
                                  self._pick_wall(d.get_file().get_path()))
            self._chooser.show()

    # --- loja
    def _build_store(self, holder):
        if not self.store.items:
            holder.append(Adw.StatusPage(icon_name="network-offline-symbolic", vexpand=True,
                                         title="Carregando a loja…",
                                         description="Se demorar, confira sua conexão com a internet."))
            return
        kinds = Gtk.Box(spacing=0, css_classes=["linked"])
        first = None
        for label, kind in (("Tudo", None), ("Temas", "gtk"), ("Ícones", "icons"), ("Cursores", "cursors")):
            b = Gtk.ToggleButton(label=label, active=first is None)
            if first:
                b.set_group(first)
            first = first or b
            b.connect("toggled", lambda b, k=kind: b.get_active() and self.grids["loja"].filter_kind(k))
            kinds.append(b)
        everything = Gtk.Button(label="Instalar tudo", css_classes=["suggested-action"])
        everything.connect("clicked", self._install_all)
        extra = Gtk.Box(spacing=8)
        extra.append(kinds)
        extra.append(everything)
        grid = self.grids["loja"] = Grid(self._pick_store, extra=extra)
        holder.append(grid)
        self.store_status = {}
        order = {"gtk": 0, "icons": 1, "cursors": 2}
        for item in sorted(self.store.items.values(), key=lambda i: (order[i["kind"]], i["title"].lower())):
            pic = picture(width_request=240, height_request=135, css_classes=["studio-thumb"],
                          overflow=Gtk.Overflow.HIDDEN)
            pill = Gtk.Label(halign=Gtk.Align.START, margin_top=4, css_classes=["studio-pill"])
            grid.add(item["id"], pic, item["title"],
                     f"{KIND_LABEL[item['kind']]} · {mb(item['bytes'])} · {item['author']}", extra=pill,
                     kind=item["kind"])
            self.store_status[item["id"]] = pill
            self.store.preview(item, lambda f, pic=pic: pic.set_paintable(thumb_texture(f, 240, 135)))
        self._store_refresh()

    def _store_refresh(self):
        for item_id, pill in getattr(self, "store_status", {}).items():
            ok = self.store.is_installed(self.store.items[item_id])
            pill.set_label("Instalado ✓" if ok else "Instalar")
            (pill.add_css_class if ok else pill.remove_css_class)("ok")

    def _pick_store(self, item_id):
        item = self.store.items[item_id]
        if self.store.is_installed(item):
            confirm(self, f"Remover {item['title']}?",
                    f"Ele some da aba {self.KIND_PAGE[item['kind']]}. Dá para instalar de novo quando quiser.",
                    "Remover", lambda: (self.store.remove(item), self.rebuild(), self._store_refresh(),
                                        self.toast(f"{item['title']} removido")))
            return
        self.grids["loja"].start(item_id)
        self._download(self.store.with_needs([item]), f"Baixando {item['title']}…", lambda: (
            self._store_refresh(), self.grids["loja"].finish(item_id),
            self.toast(f"{item['title']} instalado — escolha na aba {self.KIND_PAGE[item['kind']]}")),
            fail=lambda: self.grids["loja"].finish(item_id, ok=False))

    def _install_all(self, _btn):
        todo = [i for i in self.store.items.values() if not self.store.is_installed(i)]
        if not todo:
            self.toast("Tudo já está instalado")
            return
        confirm(self, "Instalar tudo?", f"São {len(todo)} pacotes, cerca de {mb(sum(i['bytes'] for i in todo))}.",
                "Instalar", lambda: self._download(todo, "Baixando todos os pacotes…", lambda: (
                    self._store_refresh(), self.toast("Tudo instalado"))))

    # --- barra
    BAR_PRESETS = {
        "Dock flutuante": dict(length=-1, margin=8, radius=3, size=52, custom=True, opacity=.85),
        "Barra flutuante": dict(length=100, margin=8, radius=3, size=48, custom=True, opacity=.85),
        "Barra clássica": dict(length=100, margin=0, radius=0, size=47, custom=False, opacity=.85),
    }
    POSITIONS = ["BOTTOM", "TOP", "LEFT", "RIGHT"]

    def _build_bar(self, holder):
        if not self.desk.bar:
            holder.append(Adw.StatusPage(icon_name="view-continuous-symbolic", vexpand=True,
                                         title="Barra de tarefas não encontrada",
                                         description="Essa aba funciona com a barra do Zorin OS e com a extensão Dash to Panel."))
            return
        page = Adw.PreferencesPage()
        holder.append(page)

        presets = Adw.PreferencesGroup(title="Estilos prontos",
                                       description="Vale para todos os monitores conectados")
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=3,
                           column_spacing=14, row_spacing=14)
        for name, p in self.BAR_PRESETS.items():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.append(drawing(220, 110, lambda cr, w, h, p=p: self._draw_bar(cr, w, h, p)))
            box.append(Gtk.Label(label=name, xalign=0, css_classes=["titulo"]))
            btn = Gtk.Button(child=box, css_classes=["card", "studio-card"])
            btn.connect("clicked", lambda _b, p=p, name=name: self._bar_preset(name, p))
            flow.append(btn)
        presets.add(flow)
        page.add(presets)

        fine = Adw.PreferencesGroup(title="Ajuste fino")
        self._syncing = False
        d = self.desk
        self.bar_pos = Adw.ComboRow(title="Posição", model=Gtk.StringList.new(["Embaixo", "Em cima", "Esquerda", "Direita"]))
        self.bar_pos.connect("notify::selected", lambda r, _p: self._bar_change(
            lambda: d.bar_set("panel-positions", self.POSITIONS[r.get_selected()])))
        compact_row, self.bar_compact = switch_row(
            "Barra compacta", "Só do tamanho dos ícones, centralizada. Desligada ocupa a tela inteira")
        self.bar_compact.connect("notify::active", lambda s, _p: self._bar_change(
            lambda: (d.bar_set("panel-lengths", -1 if s.get_active() else 100), d.bar_set("panel-anchors", "MIDDLE"))))
        size_row, self.bar_size = spin_row("Altura", 24, 96)
        self.bar_size.connect("value-changed", lambda s: self._bar_change(
            lambda: d.bar_set("panel-sizes", s.get_value_as_int())))
        margin_row, self.bar_margin = spin_row("Distância da borda", 0, 32)
        self.bar_margin.connect("value-changed", lambda s: self._bar_change(
            lambda: d.bar_set_plain("panel-margin", s.get_value_as_int())))
        radius_row, self.bar_radius = spin_row("Arredondamento", 0, 5, "Cada nível arredonda 5px")
        self.bar_radius.connect("value-changed", lambda s: self._bar_change(
            lambda: d.bar_set_plain("global-border-radius", s.get_value_as_int())))
        custom_row, self.bar_custom = switch_row("Transparência personalizada")
        self.bar_custom.connect("notify::active", lambda s, _p: self._bar_change(
            lambda: d.bar_set_plain("trans-use-custom-opacity", s.get_active())))
        self.bar_opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.bar_opacity.set_size_request(220, -1)
        self.bar_opacity.set_valign(Gtk.Align.CENTER)
        self.bar_opacity.connect("value-changed", lambda s: self._bar_change(
            lambda: d.bar_set_plain("trans-panel-opacity", s.get_value() / 100)))
        opacity_row = Adw.ActionRow(title="Opacidade")
        opacity_row.add_suffix(self.bar_opacity)
        self.bar_custom.bind_property("active", opacity_row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        hide_row, self.bar_hide = switch_row("Esconder automaticamente", "Some quando uma janela encosta nela")
        self.bar_hide.connect("notify::active", lambda s, _p: self._bar_change(
            lambda: d.bar_set_plain("intellihide", s.get_active())))
        for row in (self.bar_pos, compact_row, size_row, margin_row, radius_row, custom_row, opacity_row, hide_row):
            fine.add(row)
        page.add(fine)
        self._bar_sync()

    @staticmethod
    def _draw_bar(cr, w, h, p):
        draw_backdrop(cr, w, h, WALL_PALETTES["catppuccin"])
        m = p["margin"] * .9
        bh = 16
        bw = w * .5 if p["length"] == -1 else w - 2 * m
        x, y = (w - bw) / 2, h - bh - m
        _src(cr, rgba("#181825"), p["opacity"] if p["custom"] else 1)
        _rr(cr, x, y, bw, bh, min(p["radius"] * 3, bh / 2) or .1)
        cr.fill()
        icons = 6
        for i in range(icons):
            _src(cr, rgba(["#cba6f7", "#89b4fa", "#a6e3a1", "#f9e2af", "#fab387", "#f38ba8"][i]))
            cr.arc(w / 2 + (i - (icons - 1) / 2) * 13, y + bh / 2, 4, 0, 2 * pi)
            cr.fill()

    def _bar_change(self, fn):
        if not self._syncing:
            fn()

    def _bar_preset(self, name, p):
        d = self.desk
        d.bar_set("panel-lengths", p["length"])
        d.bar_set("panel-anchors", "MIDDLE")
        d.bar_set("panel-sizes", p["size"])
        d.bar_set_plain("panel-margin", p["margin"])
        d.bar_set_plain("global-border-radius", p["radius"])
        d.bar_set_plain("trans-use-custom-opacity", p["custom"])
        d.bar_set_plain("trans-panel-opacity", p["opacity"])
        self._bar_sync()
        self.toast(f"{name} aplicado em todos os monitores")

    def _bar_sync(self):
        d, b = self.desk, self.desk.bar
        self._syncing = True
        pos = d.bar_get("panel-positions", "BOTTOM")
        self.bar_pos.set_selected(self.POSITIONS.index(pos) if pos in self.POSITIONS else 0)
        self.bar_compact.set_active(d.bar_get("panel-lengths", 100) == -1)
        self.bar_size.set_value(d.bar_get("panel-sizes", 48))
        self.bar_margin.set_value(b.get_int("panel-margin") if has_key(b, "panel-margin") else 0)
        self.bar_radius.set_value(b.get_int("global-border-radius") if has_key(b, "global-border-radius") else 0)
        self.bar_custom.set_active(has_key(b, "trans-use-custom-opacity") and b.get_boolean("trans-use-custom-opacity"))
        self.bar_opacity.set_value(b.get_double("trans-panel-opacity") * 100 if has_key(b, "trans-panel-opacity") else 100)
        self.bar_hide.set_active(has_key(b, "intellihide") and b.get_boolean("intellihide"))
        self._syncing = False

    # --- efeitos (vidro)
    def _is_zorin(self):
        return self.desk.bar is not None and "zorin" in self.desk.bar.props.schema_id

    def _build_effects(self, holder):
        blur = self.blur = Blur()
        if not Blur.installed() or not blur.root:
            status = Adw.StatusPage(
                icon_name="weather-fog-symbolic", vexpand=True, title="Efeito de vidro",
                description="Deixa menus, telas e janelas com aparência de vidro fosco. Usa a extensão gratuita "
                            "Blur my Shell: o sistema vai perguntar se você quer instalar, é só confirmar.")
            btn = Gtk.Button(label="Instalar efeito de vidro", css_classes=["suggested-action", "pill"],
                             halign=Gtk.Align.CENTER)
            btn.connect("clicked", self._install_blur)
            status.set_child(btn)
            holder.append(status)
            return
        enable_extension(BLUR_UUID)
        zorin = self._is_zorin()
        blur.adapt(zorin)
        page = Adw.PreferencesPage()
        holder.append(page)

        presets = Adw.PreferencesGroup(title="Estilos prontos")
        grid = self.grids["efeitos"] = Grid(self._pick_effect, search=False, scroll=False)
        for name, p in BLUR_PRESETS.items():
            grid.add(name, drawing(220, 120, lambda cr, w, h, p=p: self._draw_glass(cr, w, h, p)), name)
        presets.add(grid)
        page.add(presets)

        self._fx_syncing = False
        where = Adw.PreferencesGroup(title="Onde aplicar")
        self.fx_switch = {}
        for part, title, sub in BLUR_PARTS:
            if part not in blur.parts:
                continue
            if part == "panel" and zorin:
                sub = "No Zorin a barra de tarefas é outra; o vidro nela chega numa próxima versão"
            row, sw = switch_row(title, sub)
            row.set_sensitive(not (part == "panel" and zorin))
            sw.connect("notify::active", lambda s, _p, part=part: self._fx_change(lambda: blur.set(part, s.get_active())))
            self.fx_switch[part] = sw
            where.add(row)
        self.fx_windows = Adw.ComboRow(title="Vidro nas janelas",
                                       subtitle="O modo de todas as janelas pesa na placa de vídeo",
                                       model=Gtk.StringList.new([label for _m, label in WINDOW_MODES]))
        self.fx_windows.connect("notify::selected", lambda r, _p: self._fx_change(
            lambda: (blur.set_windows(WINDOW_MODES[r.get_selected()][0]), self._fx_sync())))
        where.add(self.fx_windows)
        page.add(where)

        strength = Adw.PreferencesGroup(title="Intensidade")
        self.fx_radius = self._fx_scale(strength, "Desfoque", 0, 100)
        self.fx_bright = self._fx_scale(strength, "Brilho", 30, 100)
        self.fx_opacity = self._fx_scale(strength, "Opacidade das janelas", 50, 100)
        page.add(strength)
        for sc in (self.fx_radius, self.fx_bright):
            sc.connect("value-changed", lambda _s: self._fx_change(self._fx_save_strength))
        self.fx_opacity.connect("value-changed", lambda s: self._fx_change(
            lambda: blur.parts["applications"].set_int("opacity", int(s.get_value() * 2.55))))
        self._fx_sync()

    def _fx_scale(self, group, title, lo, hi):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        scale.set_size_request(260, -1)
        scale.set_valign(Gtk.Align.CENTER)
        row = Adw.ActionRow(title=title)
        row.add_suffix(scale)
        group.add(row)
        scale.row = row
        return scale

    def _fx_change(self, fn):
        if not self._fx_syncing:
            fn()

    def _fx_save_strength(self):
        # a extensão redesenha a cada gravação: espera o controle parar de mexer
        if getattr(self, "_fx_timer", None):
            GLib.source_remove(self._fx_timer)

        def save():
            self._fx_timer = None
            self.blur.set_strength(self.fx_radius.get_value() * 0.6, self.fx_bright.get_value() / 100)
            return False
        self._fx_timer = GLib.timeout_add(250, save)

    def _fx_sync(self):
        b = self.blur
        self._fx_syncing = True
        for part, sw in self.fx_switch.items():
            sw.set_active(b.get(part))
        mode = b.windows()
        self.fx_windows.set_selected(next(i for i, (m, _l) in enumerate(WINDOW_MODES) if m == mode))
        radius, bright = b.strength()
        self.fx_radius.set_value(radius / 0.6)
        self.fx_bright.set_value(bright * 100)
        apps = b.parts.get("applications")
        self.fx_opacity.set_value(apps.get_int("opacity") / 2.55 if apps else 85)
        self.fx_opacity.row.set_sensitive(mode != "nenhuma")
        self._fx_syncing = False

    def _pick_effect(self, name):
        self._feedback("efeitos", name, lambda: (self.blur.apply_preset(BLUR_PRESETS[name], self._is_zorin()),
                                                 self._fx_sync()), f"{name} aplicado")

    def _install_blur(self, btn):
        btn.set_sensitive(False)
        btn.set_label("Esperando você confirmar…")

        def done(result):
            if result == "ok":
                Blur().adapt(self._is_zorin())
                self.rebuild("efeitos")
                self.toast("Efeito de vidro instalado")
            else:
                btn.set_sensitive(True)
                btn.set_label("Instalar efeito de vidro")
                self.toast("Instalação cancelada" if result == "cancelled" else f"Não deu para instalar: {result}")
        Blur.install(done)

    @staticmethod
    def _draw_glass(cr, w, h, p):
        draw_backdrop(cr, w, h, WALL_PALETTES["neon"])
        glass = bool(p["parts"])
        strength = p["radius"] / 50 if glass else 0
        x, y, gw, gh = w * .14, h * .18, w * .72, h * .64
        _src(cr, rgba("#ffffff") if glass else rgba("#1e1e2e"), .10 + .12 * strength if glass else 1)
        _rr(cr, x, y, gw, gh, 12)
        cr.fill_preserve()
        _src(cr, rgba("#ffffff"), .35 if glass else .08)
        cr.set_line_width(1.2)
        cr.stroke()
        for i, frac in enumerate((.6, .4, .5)):
            _src(cr, rgba("#ffffff"), .75 if i == 0 else .4)
            _rr(cr, x + 14, y + 16 + i * 14, (gw - 28) * frac, 7, 3.5)
            cr.fill()

    # --- restaurar / sobre
    def _ask_restore(self, _btn):
        confirm(self, "Voltar ao visual original?",
                "Tema, ícones, cursor, wallpaper e barra voltam a ser como eram "
                f"antes de você abrir o {APP_NAME} pela primeira vez.", "Restaurar", self._restore)

    def _restore(self):
        self.desk.restore()
        self.refresh_marks()
        if not self.builders["barra"][1] and self.desk.bar:
            self._bar_sync()
        if not self.builders["efeitos"][1]:
            self.rebuild("efeitos")
        self.toast("Visual original restaurado")

    def _about(self, _btn):
        comments = ("Deixe seu Linux com a sua cara: temas, ícones, cursores, wallpapers e barra "
                    "de tarefas com um clique.")
        credits = sorted({f"{i['title']} — {i['author']} {i['source']}" for i in self.store.items.values()})
        if hasattr(Adw, "AboutWindow"):
            win = Adw.AboutWindow(transient_for=self, application_name=APP_NAME, version=VERSION,
                                  developer_name="Adonay", application_icon=APP_ID,
                                  license_type=Gtk.License.GPL_3_0, comments=comments)
            if credits:
                win.add_credit_section("Temas, ícones e cursores", credits)
        else:
            win = Gtk.AboutDialog(transient_for=self, program_name=APP_NAME, version=VERSION, comments=comments,
                                  logo_icon_name=APP_ID, license_type=Gtk.License.GPL_3_0)
        win.present()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=getattr(Gio.ApplicationFlags, "DEFAULT_FLAGS", Gio.ApplicationFlags.FLAGS_NONE))

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        if hasattr(css, "load_from_string"):
            css.load_from_string(CSS)
        else:
            css.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        icons = Path(__file__).resolve().parent / "icons"
        if icons.is_dir():
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(icons))

    def do_activate(self):
        (self.props.active_window or Window(self)).present()


if __name__ == "__main__":
    import sys
    sys.exit(App().run(sys.argv))
