#!/usr/bin/env python3
# Adon Studio — personalize o visual do GNOME com um clique.
import colorsys
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
VERSION = "0.3.0"

HOME = Path.home()
USER_DATA = Path(GLib.get_user_data_dir())
SYS_DATA = [Path(d) for d in GLib.get_system_data_dirs()]
DATA = USER_DATA / "adon-studio"
BACKUP = DATA / "original"
PREVIOUS = DATA / "anterior"  # antes da última aplicação, para "Desfazer"
MY_STYLES = DATA / "meus-estilos.json"
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


FILES_APP = "org.gnome.Nautilus"


def session_bus():
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION)
    except GLib.Error:
        return None


def _files_app_running(bus):
    return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                         "NameHasOwner", GLib.Variant("(s)", (FILES_APP,)), GLib.VariantType("(b)"),
                         Gio.DBusCallFlags.NONE, -1, None).unpack()[0]


def reload_files_app(bus=None, wait=200):
    """O Nautilus fica rodando em segundo plano e, como todo app GTK4, só lê ~/.config/gtk-4.0 ao
    abrir. Encerra o Nautilus ("quit" só fecha as janelas; "kill" encerra o serviço) e, se ele
    tinha janela aberta, abre uma nova (já com o tema novo).
    True se ele estava rodando."""
    bus = bus if bus is not None else session_bus()
    if bus is None:
        return False
    try:
        if not _files_app_running(bus):
            return False
        try:
            xml = bus.call_sync(FILES_APP, "/org/gnome/Nautilus/window", "org.freedesktop.DBus.Introspectable",
                                "Introspect", None, GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, -1,
                                None).unpack()[0]
            windows = len(Gio.DBusNodeInfo.new_for_xml(xml).nodes)
        except GLib.Error:
            windows = 0
        bus.call_sync(FILES_APP, "/org/gnome/Nautilus", "org.gtk.Actions", "Activate",
                      GLib.Variant("(sava{sv})", ("kill", [], {})), None, Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error:
        return False
    if not windows:
        return True
    tries = [0]

    def reopen():
        # espera o Nautilus antigo sair do barramento; senão o pedido chega a ele, que está fechando
        tries[0] += 1
        try:
            if _files_app_running(bus) and tries[0] < 25:
                return True
        except GLib.Error:
            pass
        bus.call(FILES_APP, "/org/gnome/Nautilus", "org.freedesktop.Application", "Activate",
                 GLib.Variant("(a{sv})", ({},)), None, Gio.DBusCallFlags.NONE, -1, None, None, None)
        return False
    if wait:
        GLib.timeout_add(wait, reopen)
    else:
        while reopen():
            pass
    return True


class Desktop:
    """Lê e aplica as configurações de aparência, e guarda o ponto de restauração."""

    def __init__(self):
        self.iface = settings_for("org.gnome.desktop.interface")
        self.bg = settings_for("org.gnome.desktop.background")
        self.lock = settings_for("org.gnome.desktop.screensaver")
        self.shell = settings_for("org.gnome.shell.extensions.user-theme")
        self.bar = next((s for s in map(settings_for, TASKBAR_SCHEMAS) if s), None)
        self.tracked = {
            "interface": (self.iface, ["gtk-theme", "icon-theme", "cursor-theme", "color-scheme", "accent-color"]),
            "background": (self.bg, ["picture-uri", "picture-uri-dark", "picture-options"]),
            "screensaver": (self.lock, ["picture-uri"]),
            "user-theme": (self.shell, ["name"]),
            "taskbar": (self.bar, TASKBAR_KEYS),
        }

    # --- pontos de restauração: o original (primeira abertura) e o de antes da última aplicação
    def ensure_backup(self):
        if not (BACKUP / "settings.json").exists():
            self.snapshot(BACKUP)

    def snapshot(self, dest):
        """Guarda em `dest` tudo o que o app pode mudar, para restore_from voltar depois."""
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
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
                shutil.copy2(p, dest / name)
                gtk4[name] = {"file": name}
            elif p.is_dir():
                shutil.copytree(p, dest / name, dirs_exist_ok=True)
                gtk4[name] = {"dir": name}
        values["gtk4"] = gtk4
        values["blur"] = Blur().snapshot() if Blur.installed() and Blur().root else None
        values["cursor-default"] = CURSOR_DEFAULT.exists()
        if CURSOR_DEFAULT.exists():
            shutil.copytree(CURSOR_DEFAULT, dest / "cursor-default", dirs_exist_ok=True)
        (dest / "settings.json").write_text(json.dumps(values, indent=2))

    def restore(self):
        self.restore_from(BACKUP)

    @staticmethod
    def has_previous():
        return (PREVIOUS / "settings.json").exists()

    def undo(self):
        """Volta ao que estava antes da última aplicação (um nível só)."""
        self.restore_from(PREVIOUS)
        shutil.rmtree(PREVIOUS, ignore_errors=True)

    def restore_from(self, src):
        values = json.loads((src / "settings.json").read_text())
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
                shutil.copy2(src / name, dest)
            elif "dir" in info:
                shutil.copytree(src / name, dest)
        blur = Blur()
        if blur.root:
            blur.restore(values.get("blur"))
        shutil.rmtree(CURSOR_DEFAULT, ignore_errors=True)
        if values.get("cursor-default"):
            shutil.copytree(src / "cursor-default", CURSOR_DEFAULT)
        Gio.Settings.sync()
        reload_files_app()

    # --- aplicar
    @staticmethod
    def _clear_gtk4():
        for name in GTK4_FILES:
            p = GTK4_CFG / name
            if p.is_symlink() or p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)

    def ensure_user_theme(self, bus=None):
        """Liga a extensão User Themes (sem ela o tema não chega ao painel).
        'ok': ligada; 'carregar': instalada, mas o Shell aberto ainda não a enxerga (instalada
        depois do login); 'falta': não está instalada."""
        if not extension_installed(USER_THEME_UUID):
            return "falta"
        self.shell = self.shell or settings_for("org.gnome.shell.extensions.user-theme")
        if enable_extension(USER_THEME_UUID, bus):
            return "ok"
        return "carregar" if shell_extension_info(USER_THEME_UUID, bus) == {} else "falta"

    def apply_gtk(self, theme):
        self.iface.set_string("color-scheme", "prefer-dark" if is_dark(theme_palette(theme)) else "prefer-light")
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
        if Blur.installed():
            Blur().keep_theme_colors()
        reload_files_app()

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

    def apply_palette(self, pal):
        """Pinta o sistema com uma paleta da aba Cores: cor de destaque do GNOME (47+), cores dos apps
        GTK4/libadwaita e um wallpaper gerado com as mesmas cores. A paleta troca a base em vez de ir
        por cima do tema: muitos temas (Catppuccin...) têm as cores escritas direto no CSS e ignoram
        @accent_bg_color, e um tema escuro com o modo claro ligado deixa o painel ilegível."""
        dark = pal["dark"]
        self.iface.set_string("color-scheme", "prefer-dark" if dark else "prefer-light")
        # Zorin e Ubuntu trazem temas por cor com painel incluso (ZorinRed-Light, Yaru-red...): usa o
        # mais parecido; sem eles, Adwaita nos apps GTK3 e o Shell padrão, que segue o claro/escuro
        variant = system_variant(pal["accent"], dark, list_gtk_themes())
        self.iface.set_string("gtk-theme", variant or ("Adwaita-dark" if dark else "Adwaita"))
        if self.shell:
            self.shell.set_string("name", variant or "")
        self.iface.set_string("icon-theme", folder_icon_theme(pal, self.iface.get_string("icon-theme")))
        if has_key(self.iface, "accent-color"):
            self.iface.set_string("accent-color", gnome_accent(pal["accent"]))
        self._clear_gtk4()
        GTK4_CFG.mkdir(parents=True, exist_ok=True)
        (GTK4_CFG / "gtk.css").write_text(palette_css(pal))
        WALLS.mkdir(parents=True, exist_ok=True)
        design, seed = pal.get("design"), pal.get("seed", 0)
        wall = WALLS / f"cor-{pal['accent'][1:]}-{pal['key']}{f'-{design}-{seed}' if design else ''}.svg"
        wall.write_text(design_svg(pal["wall"], design, seed) if design else wall_svg(*pal["wall"]))
        self.apply_wallpaper(wall)
        reload_files_app()
        return wall

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

    def _plain(self, key, default):
        return self.bar.get_value(key).unpack() if has_key(self.bar, key) else default

    def bar_read(self):
        """Ajustes da barra como estão no sistema (rascunho inicial da aba Barra e dock)."""
        return dict(position=self.bar_get("panel-positions", "BOTTOM"), length=self.bar_get("panel-lengths", 100),
                    size=self.bar_get("panel-sizes", 48), margin=self._plain("panel-margin", 0),
                    radius=self._plain("global-border-radius", 0),
                    custom=self._plain("trans-use-custom-opacity", False),
                    opacity=round(self._plain("trans-panel-opacity", 1.0), 2), hide=self._plain("intellihide", False))

    def bar_write(self, d):
        """Grava só o que mudou em relação ao sistema, em todos os monitores."""
        cur = self.bar_read()
        per_monitor = {"position": "panel-positions", "length": "panel-lengths", "size": "panel-sizes"}
        plain = {"margin": "panel-margin", "radius": "global-border-radius", "custom": "trans-use-custom-opacity",
                 "opacity": "trans-panel-opacity", "hide": "intellihide"}
        for k, v in d.items():
            if cur.get(k) == v:
                continue
            if k in per_monitor:
                self.bar_set(per_monitor[k], v)
                if k == "length":
                    self.bar_set("panel-anchors", "MIDDLE")  # barra compacta fica centralizada
            elif k in plain:
                self.bar_set_plain(plain[k], v)

    def current(self):
        """O que está em uso agora: tema, ícones, cursor e wallpaper."""
        uri = self.bg.get_string("picture-uri") if self.bg else ""
        return dict(gtk=self.iface.get_string("gtk-theme"), icons=self.iface.get_string("icon-theme"),
                    cursor=self.iface.get_string("cursor-theme"),
                    wallpaper=Gio.File.new_for_uri(uri).get_path() if uri else None)


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


def _hidden(kf):
    """Hidden=true: tema de uso interno (ex.: as pastas coloridas da paleta), não é para escolher."""
    try:
        return kf.get_boolean("Icon Theme", "Hidden")
    except GLib.Error:
        return False


def list_icon_themes():
    found = {}
    for base in ICON_DIRS:
        for d in base.iterdir():
            if d.name in found or d.name.startswith(".") or d.name in {"hicolor", "default", "locolor"}:
                continue
            kf = _index(d)
            try:
                if kf and not _hidden(kf) and kf.get_string("Icon Theme", "Directories"):
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
KIND_ICON = {"gtk": "preferences-desktop-appearance-symbolic", "icons": "folder-pictures-symbolic",
             "cursors": "input-mouse-symbolic"}


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
        self.items, self.failed, self.offline = {}, {}, False
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
                self.offline = False
            except (OSError, ValueError):
                data = cached.read_bytes() if cached.exists() else None
                self.offline = True
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
        """Instala uma lista de itens em sequência; um item com erro não impede os outros.
        progress(fração 0..1), done(None ou texto com o que falhou). Os erros por item ficam em
        self.failed ({id: motivo})."""
        def work():
            total = sum(i["bytes"] for i in items) or 1
            before, failed = 0, {}
            for item in items:
                part = CACHE / (item["file"] + ".part")
                try:
                    _http_get(self._base + item["file"], part,
                              lambda f, b=before, s=item["bytes"]: GLib.idle_add(progress, (b + f * s) / total))
                    h = hashlib.sha256()
                    with open(part, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    if h.hexdigest() != item["sha256"]:
                        part.unlink()
                        raise OSError("o arquivo veio corrompido")
                    dest = STORE_DIRS[item["kind"]]
                    dest.mkdir(parents=True, exist_ok=True)
                    with tarfile.open(part, "r:xz") as tf:
                        members = list(_safe_members(tf, dest))
                        kw = {"filter": "fully_trusted"} if hasattr(tarfile, "data_filter") else {}
                        tf.extractall(dest, members=members, **kw)
                    part.unlink()
                except (OSError, tarfile.TarError) as e:
                    failed[item["id"]] = str(getattr(e, "reason", None) or e)
                    part.unlink(missing_ok=True)
                before += item["bytes"]
            self.failed = failed
            names = {i["id"]: i["title"] for i in items}
            err = "; ".join(f"{names[k]}: {v}" for k, v in failed.items()) or None
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


SHELL_EXT = ("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions", "org.gnome.Shell.Extensions")


def shell_extension_info(uuid, bus=None):
    """O que o GNOME Shell aberto sabe da extensão: {} se não a conhece (ex.: instalada depois do
    login), None se não há Shell para perguntar."""
    bus = bus if bus is not None else session_bus()
    if bus is None:
        return None
    try:
        return bus.call_sync(*SHELL_EXT, "GetExtensionInfo", GLib.Variant("(s)", (uuid,)),
                             GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
    except GLib.Error:
        return None


def enable_extension(uuid, bus=None):
    """Liga uma extensão do GNOME Shell já instalada. True se ficou ligada."""
    shell = settings_for("org.gnome.shell")
    bus = bus if bus is not None else session_bus()
    try:
        if bus is None:
            raise GLib.Error("sem D-Bus")
        ok = bus.call_sync(*SHELL_EXT, "EnableExtension", GLib.Variant("(s)", (uuid,)),
                           GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
        if ok:
            return True
    except GLib.Error:
        ok = None
    # sem Shell para perguntar (ou ele ainda não conhece a extensão): fica gravado para o próximo login
    if shell:
        if uuid not in shell.get_strv("enabled-extensions"):
            shell.set_strv("enabled-extensions", shell.get_strv("enabled-extensions") + [uuid])
        shell.set_strv("disabled-extensions", [u for u in shell.get_strv("disabled-extensions") if u != uuid])
    return ok is None and shell is not None


def install_remote_extension(uuid, done, installed=None):
    """Pede ao GNOME Shell para baixar a extensão do extensions.gnome.org, carregar e ligar na
    hora (ele mostra a confirmação). done('ok' ou o erro)."""
    installed = installed or (lambda: extension_installed(uuid))

    def finished(bus, res):
        try:
            answer = bus.call_finish(res).unpack()[0]
            done("ok" if answer == "successful" else answer)
        except GLib.Error as e:
            # o Shell às vezes encerra a chamada sem responder mesmo tendo instalado
            err = e.message
            GLib.timeout_add(1500, lambda: done("ok" if installed() else err) or False)
    bus = session_bus()
    if bus is None:
        return done("sem conexão com o GNOME Shell")
    bus.call(*SHELL_EXT, "InstallRemoteExtension", GLib.Variant("(s)", (uuid,)), GLib.VariantType("(s)"),
             Gio.DBusCallFlags.NONE, -1, None, finished)


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
        install_remote_extension(BLUR_UUID, done, Blur.installed)

    @property
    def modern(self):
        return has_key(self.root, "pipelines")

    def get(self, part):
        s = self.parts.get(part)
        return bool(s) and s.get_boolean("blur")

    def set(self, part, on):
        if part in self.parts:
            self.parts[part].set_boolean("blur", on)
        if part == "popup" and on:
            self.keep_theme_colors()

    def keep_theme_colors(self):
        """Por padrão a extensão troca o fundo dos pop-ups por um estilo dela que pinta os botões do
        painel (Wi-Fi, Bluetooth...) com -st-accent-color; no GNOME 46 isso não existe e vira azul
        #3584e4, vencendo a cor do tema. Preservar o tema do Shell mantém a cor do tema."""
        s = self.parts.get("popup")
        if has_key(s, "preserve-shell-theme"):
            s.set_boolean("preserve-shell-theme", True)

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

    def read(self):
        """Como o vidro está agora, no formato dos presets (rascunho inicial da aba Efeitos)."""
        radius, bright = self.strength()
        apps = self.parts.get("applications")
        return dict(parts=[p for p, _t, _s in BLUR_PARTS if self.get(p)], windows=self.windows(), radius=radius,
                    brightness=bright, opacity=apps.get_int("opacity") if has_key(apps, "opacity") else 215)

    def write(self, d, zorin):
        self.apply_preset(d, zorin)
        apps = self.parts.get("applications")
        if "opacity" in d and has_key(apps, "opacity") and d["windows"] != "nenhuma":
            apps.set_int("opacity", int(d["opacity"]))

    def snapshot(self):
        s = self.parts.get("popup")
        extra = {"_preserve": s.get_boolean("preserve-shell-theme")} if has_key(s, "preserve-shell-theme") else {}
        apps = self.parts.get("applications")
        if has_key(apps, "opacity"):
            extra["_opacity"] = apps.get_int("opacity")
        return {p: self.get(p) for p in self.parts} | {"_windows": self.windows(),
                                                       "_strength": list(self.strength())} | extra

    def restore(self, saved):
        """Volta ao que estava no backup; sem backup do vidro, desliga tudo."""
        saved = saved or {}
        for p in self.parts:
            if p != "applications":
                self.set(p, saved.get(p, False))
        self.set_windows(saved.get("_windows", "nenhuma"))
        s = self.parts.get("popup")
        if has_key(s, "preserve-shell-theme"):
            s.set_boolean("preserve-shell-theme", saved.get("_preserve", False))
        if "_strength" in saved:
            self.set_strength(*saved["_strength"])
        apps = self.parts.get("applications")
        if "_opacity" in saved and has_key(apps, "opacity"):
            apps.set_int("opacity", saved["_opacity"])


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

    low = theme.name.lower()
    # sem cores no CSS, só o nome ajuda: "dark" no nome é escuro; o resto (Adwaita, Yaru...) é claro
    dark = "dark" in low or ("light" not in low and any(w in low for w in ("dracula", "nord", "noir", "black")))
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


def is_dark(pal):
    """Pelo fundo lido do tema (o Adwaita, por exemplo, é claro sem ter "light" no nome)."""
    bg = pal[0]
    return .2126 * bg.red + .7152 * bg.green + .0722 * bg.blue < .45


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
ICON_LABELS = ["Pasta", "Pasta pessoal", "Navegador", "Terminal", "Configurações", "Editor de texto", "Lixeira"]


def icon_theme(name):
    theme = Gtk.IconTheme()
    theme.set_search_path([str(d) for d in ICON_DIRS] + ["/usr/share/pixmaps"])
    theme.set_theme_name(name)
    return theme


def icon_paintables(name, size, samples=ICON_SAMPLES):
    theme = icon_theme(name)
    return [theme.lookup_icon(n[0], n[1:], size, 1, Gtk.TextDirection.NONE, 0) for n in samples]


def icon_preview(name, size=32, count=5):
    """Sempre os mesmos aplicativos, na mesma ordem, para dar para comparar os temas."""
    box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=10)
    for paint in icon_paintables(name, size, ICON_SAMPLES[:count]):
        box.append(Gtk.Image(paintable=paint, pixel_size=size))
    return box


def labeled_samples(images, labels, size):
    """Grade de amostras grandes com o nome de cada uma embaixo (prévia ampliada)."""
    flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=4,
                       min_children_per_line=2, column_spacing=8, row_spacing=12)
    for img, label in zip(images, labels):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(img or Gtk.Image(icon_name="image-missing-symbolic", pixel_size=size))
        box.append(Gtk.Label(label=label, wrap=True, justify=Gtk.Justification.CENTER, css_classes=["caption"]))
        flow.append(box)
    return flow


# ponteiro, seleção de texto, link e carregando primeiro: são os que a pessoa mais vê
CURSOR_SAMPLES = [["left_ptr", "default", "arrow"], ["xterm", "text", "ibeam"],
                  ["hand2", "pointer", "pointing_hand"], ["watch", "wait", "progress"], ["fleur", "move", "grabbing"]]
CURSOR_LABELS = ["Ponteiro", "Texto", "Link", "Carregando", "Mover"]


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


def cursor_textures(path, want=32, count=4):
    out = []
    for names in CURSOR_SAMPLES[:count]:
        f = next((path / "cursors" / n for n in names if (path / "cursors" / n).exists()), None)
        try:
            out.append(load_xcursor(f.resolve(), want) if f else None)
        except (OSError, struct.error):
            out.append(None)
    return out


def cursor_preview(path, count=4):
    box = Gtk.Box(spacing=14, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=10)
    for tex, label in zip(cursor_textures(path, 32, count), CURSOR_LABELS):
        img = Gtk.Image(paintable=tex, pixel_size=32) if tex else \
            Gtk.Image(icon_name="image-missing-symbolic", pixel_size=24)
        img.set_tooltip_text(label)
        box.append(img)
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


# Modelos de papel de parede desenhados aqui mesmo (sem arquivos de terceiros, sem licença a conferir).
# Cada cor tem um papel: fundo, céu, camadas e brilho saem da paleta, então o mesmo desenho combina
# com qualquer cor. A semente muda o desenho de um jeito reproduzível ("Variar desenho").
WALL_DESIGNS = [("entardecer", "Entardecer"), ("montanhas", "Montanhas"), ("ondas", "Ondas"), ("gradiente", "Gradiente"), ("formas", "Formas")]


def blend(a, b, t):
    ca, cb = rgba(a), rgba(b)
    return "#%02x%02x%02x" % tuple(round((x + (y - x) * t) * 255) for x, y in
                                   ((ca.red, cb.red), (ca.green, cb.green), (ca.blue, cb.blue)))


def design_svg(colors, design="montanhas", seed=0):
    """SVG 4K de um dos WALL_DESIGNS com as cores (fundo 1, fundo 2, destaque, 2º tom)."""
    import random
    if design == "entardecer":
        return scene_dusk(colors, seed)
    c1, c2, g1, g2 = colors
    rnd = random.Random(f"{design}-{seed}")
    W, H = 3840, 2160
    light = luminance(c1) > .4
    shade = "#ffffff" if light else "#000000"
    defs = (f'<linearGradient id="fundo" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{c2}"/>'
            f'<stop offset="1" stop-color="{blend(c1, g1, .35)}"/></linearGradient>')
    body = '<rect width="3840" height="2160" fill="url(#fundo)"/>'
    if design == "montanhas":
        # perspectiva atmosférica: o céu clareia no horizonte, as serras do fundo quase somem nele e
        # a da frente é sempre a mais escura (no claro e no escuro)
        if light:
            sky_top, horizon, deep = blend(c2, g1, .35), blend(c1, "#ffffff", .3), blend(g1, "#000000", .45)
        else:
            sky_top, horizon, deep = blend(c2, "#000000", .3), blend(c1, g1, .4), blend(c1, g1, .1)
        defs = (f'<linearGradient id="fundo" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{sky_top}"/>'
                f'<stop offset=".62" stop-color="{horizon}"/></linearGradient>'
                f'<radialGradient id="luz"><stop offset="0" stop-color="{g2}" stop-opacity=".45"/>'
                f'<stop offset=".35" stop-color="{g2}" stop-opacity=".12"/>'
                f'<stop offset="1" stop-color="{g2}" stop-opacity="0"/></radialGradient>')
        body = '<rect width="3840" height="2160" fill="url(#fundo)"/>'
        sx, sy = rnd.uniform(.18, .82) * W, rnd.uniform(.2, .32) * H
        body += f'<circle cx="{sx:.0f}" cy="{sy:.0f}" r="1100" fill="url(#luz)"/>'
        body += f'<circle cx="{sx:.0f}" cy="{sy:.0f}" r="150" fill="{blend(g2, horizon, .55 if light else .3)}"/>'
        far = blend(horizon, g1, .3)
        layers = 5
        for i in range(layers):
            t = i / (layers - 1)
            top_c = blend(far, deep, .12 + t * .88)
            bottom_c = blend(top_c, deep, .35)
            defs += (f'<linearGradient id="s{i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top_c}"/>'
                     f'<stop offset="1" stop-color="{bottom_c}"/></linearGradient>')
            base, amp = H * (.5 + t * .3), H * (.2 - t * .1)
            # poucos pontos de controle (deslocamento do ponto médio) ligados por curvas: morros
            # suaves, de alturas variadas, sem pontas finas
            n = 16
            ys = [0.0] * (n + 1)
            ys[0], ys[n] = rnd.uniform(-.4, .4), rnd.uniform(-.4, .4)
            step, rough = n, 1.0
            while step > 1:
                half = step // 2
                for k in range(half, n, step):
                    ys[k] = (ys[k - half] + ys[k + half]) / 2 + rnd.uniform(-1, 1) * rough
                step, rough = half, rough * .45
            lo, hi = min(ys), max(ys)
            pts = [(W * k / n, base - (ys[k] - lo) / ((hi - lo) or 1) * amp) for k in range(n + 1)]
            d = f"M{pts[0][0]:.0f} {pts[0][1]:.0f}"
            for k in range(n):  # Catmull-Rom → Bézier cúbica
                p0, p1, p2, p3 = pts[max(k - 1, 0)], pts[k], pts[k + 1], pts[min(k + 2, n)]
                d += (f" C{p1[0] + (p2[0] - p0[0]) / 6:.0f} {p1[1] + (p2[1] - p0[1]) / 6:.0f} "
                      f"{p2[0] - (p3[0] - p1[0]) / 6:.0f} {p2[1] - (p3[1] - p1[1]) / 6:.0f} {p2[0]:.0f} {p2[1]:.0f}")
            body += f'<path class="serra" fill="url(#s{i})" d="{d} L{W} {H} L0 {H} Z"/>'
    elif design == "ondas":
        layers = [blend(g1, c1, .55), blend(g2, c1, .45), g1, blend(c2, shade, .3)]
        for i, fill in enumerate(layers):
            y = 1150 + i * 260 + rnd.uniform(-60, 60)
            a1, a2 = rnd.uniform(120, 320), rnd.uniform(120, 320)
            body += (f'<path d="M0 {y:.0f} C960 {y - a1:.0f} 1920 {y + a2:.0f} 2880 {y - a2 * .6:.0f} '
                     f'S3840 {y + a1 * .4:.0f} 3840 {y:.0f} V2160 H0 Z" fill="{fill}" fill-opacity=".92"/>')
    elif design == "gradiente":
        for i, c in enumerate((g1, g2, g1)):
            defs += (f'<radialGradient id="m{i}"><stop offset="0" stop-color="{c}" stop-opacity="{.6 - i * .12:.2f}"/>'
                     f'<stop offset="1" stop-color="{c}" stop-opacity="0"/></radialGradient>')
            body += (f'<circle cx="{rnd.uniform(.1, .9) * W:.0f}" cy="{rnd.uniform(.15, .85) * H:.0f}" '
                     f'r="{rnd.uniform(900, 1500):.0f}" fill="url(#m{i})"/>')
    else:  # formas
        for i in range(rnd.randint(7, 10)):
            c = (g1, g2, blend(g1, c1, .4))[i % 3]
            x, y, r = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(140, 520)
            op = rnd.uniform(.25, .7)
            if i % 3 == 2:
                body += (f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.0f}" fill="none" stroke="{c}" '
                         f'stroke-width="28" stroke-opacity="{op:.2f}"/>')
            elif i % 3 == 1:
                body += (f'<rect x="{x - r:.0f}" y="{y - r:.0f}" width="{r * 2:.0f}" height="{r * 2:.0f}" '
                         f'rx="{r * .3:.0f}" fill="{c}" fill-opacity="{op:.2f}" '
                         f'transform="rotate({rnd.uniform(-30, 30):.0f} {x:.0f} {y:.0f})"/>')
            else:
                body += f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.0f}" fill="{c}" fill-opacity="{op:.2f}"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'<!-- {APP_NAME}: {design} {seed} --><defs>{defs}</defs>{body}</svg>\n')


# ---------------------------------------------------------------- paisagens ilustradas

# Cada elemento tem um papel na cena e o papel fixa a luminosidade e a saturação; da paleta vem só
# o tom (destaque = tom frio da cena, 2º tom = tom quente da luz). Assim qualquer paleta mantém a
# profundidade: céu e picos distantes claros, montanhas do meio médias, pinheiros escuros.
LANDSCAPE_DEFAULT = ("#f3e6ef", "#d9c8e6", "#9a86d6", "#f6b38c")  # lavanda e pêssego
SCENE_ROLES = {  # papel: (tom: "frio"/"quente"/"meio", luminosidade, saturação)
    "ceu_alto": ("frio", .66, .38), "ceu_meio": ("meio", .78, .50), "horizonte": ("quente", .86, .80),
    "sol": ("quente", .93, .90), "pico": ("frio", .72, .24), "pico_luz": ("quente", .87, .45),
    "serra": ("frio", .60, .26), "meio": ("frio", .44, .30), "perto": ("frio", .30, .30),
    "pinheiro_longe": ("frio", .21, .28), "pinheiro": ("frio", .12, .30),
}


def scene_colors(colors):
    """Cor de cada papel a partir da paleta. Paleta escura vira um entardecer mais tarde (tudo mais
    escuro, na mesma proporção), sem inverter a ordem de claro para escuro."""
    c1, _c2, g1, g2 = colors
    cold, warm = hex_hls(g1)[0], hex_hls(g2)[0]
    if abs(cold - warm) < .04 or abs(cold - warm) > .96:  # paleta de um tom só: a luz puxa para o quente
        warm = (cold + .08) % 1
    mid = (cold + ((warm - cold + .5) % 1 - .5) / 2) % 1
    dim = .62 if luminance(c1) < .4 else 1.0
    tones = {"frio": cold, "quente": warm, "meio": mid}
    out = {role: hls_hex(tones[t], l * (dim if role not in ("sol", "horizonte") else (1 + dim) / 2), s)
           for role, (t, l, s) in SCENE_ROLES.items()}
    # a luz nos picos é a luz do horizonte refletida: puxa para ele, não para um tom saturado
    out["pico_luz"] = blend(out["pico_luz"], out["horizonte"], .5)
    out["noite"] = dim < 1
    return out


def _pine(x, base, h, w):
    """Pinheiro: cinco andares de galhos sobrepostos, com o recorte do galho em cada lado."""
    pts = []
    for t in range(5):
        top = base - h + t * h * .17
        th, tw = h * .3, w * (.35 + t * .16)
        pts.append(f'<polygon points="{x:.0f},{top:.0f} {x + tw * .28:.0f},{top + th * .55:.0f} '
                   f'{x + tw * .2:.0f},{top + th * .6:.0f} {x + tw / 2:.0f},{top + th:.0f} '
                   f'{x - tw / 2:.0f},{top + th:.0f} {x - tw * .2:.0f},{top + th * .6:.0f} '
                   f'{x - tw * .28:.0f},{top + th * .55:.0f}"/>')
    pts.append(f'<rect x="{x - w * .04:.0f}" y="{base - h * .06:.0f}" width="{w * .08:.0f}" height="{h * .08:.0f}"/>')
    return "".join(pts)


def _smooth(points):
    """Caminho suave (Catmull-Rom) pelos pontos."""
    d = f"M{points[0][0]:.0f} {points[0][1]:.0f}"
    n = len(points) - 1
    for k in range(n):
        p0, p1, p2, p3 = points[max(k - 1, 0)], points[k], points[k + 1], points[min(k + 2, n)]
        d += (f" C{p1[0] + (p2[0] - p0[0]) / 6:.0f} {p1[1] + (p2[1] - p0[1]) / 6:.0f} "
              f"{p2[0] - (p3[0] - p1[0]) / 6:.0f} {p2[1] - (p3[1] - p1[1]) / 6:.0f} {p2[0]:.0f} {p2[1]:.0f}")
    return d


def scene_dusk(colors, seed=0):
    """Montanhas ao entardecer. Detalhe nas bordas e embaixo; o centro fica com céu e vale baixo."""
    import random
    rnd = random.Random(f"entardecer-{seed}")
    W, H = 3840, 2160
    c = scene_colors(colors)
    g2 = colors[3]
    sun_x = W * rnd.uniform(.4, .6)
    sun_y = H * .5
    edge = lambda x: abs(x / W - .5) * 2  # 0 no centro, 1 nas bordas  # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
           f'<!-- {APP_NAME}: entardecer {seed} -->',
           '<defs>',
           f'<linearGradient id="ceu" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{c["ceu_alto"]}"/>'
           f'<stop offset=".45" stop-color="{c["ceu_meio"]}"/><stop offset=".62" stop-color="{c["horizonte"]}"/></linearGradient>',
           f'<radialGradient id="brilho"><stop offset="0" stop-color="{c["sol"]}" stop-opacity=".9"/>'
           f'<stop offset=".25" stop-color="{g2}" stop-opacity=".35"/><stop offset="1" stop-color="{g2}" stop-opacity="0"/>'
           '</radialGradient>',
           f'<linearGradient id="nevoa" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{c["horizonte"]}" '
           f'stop-opacity="0"/><stop offset=".6" stop-color="{c["horizonte"]}" stop-opacity=".28"/>'
           f'<stop offset="1" stop-color="{c["horizonte"]}" stop-opacity="0"/></linearGradient>',
           '</defs>']
    # céu
    stars = ""
    if c["noite"]:  # paleta escura: o entardecer já virou noite, com estrelas no alto do céu
        stars = "".join(f'<circle cx="{rnd.uniform(0, W):.0f}" cy="{rnd.uniform(0, H * .4) ** 1.1 / (H * .4) ** .1:.0f}" '
                        f'r="{rnd.uniform(2, 5):.1f}" fill="{c["sol"]}" opacity="{rnd.uniform(.3, .85):.2f}"/>'
                        for _ in range(140))
    out.append(f'<g id="ceu"><rect width="{W}" height="{H}" fill="url(#ceu)"/>{stars}</g><!-- /ceu -->')
    # luz: sol baixo atrás do vale, brilho largo e suave
    out.append(f'<g id="luz"><circle cx="{sun_x:.0f}" cy="{sun_y:.0f}" r="1300" fill="url(#brilho)"/>'
               f'<circle cx="{sun_x:.0f}" cy="{sun_y:.0f}" r="120" fill="{c["sol"]}"/></g><!-- /luz -->')
    # distantes: picos altos nas bordas, baixos no meio; a face virada para o sol é iluminada
    peaks = []
    for fx in (.03, .13, .24, .36, .64, .76, .87, .97):
        x = W * (fx + rnd.uniform(-.02, .02))
        y = H * (.62 - .3 * edge(x) ** 1.3 - rnd.uniform(0, .05))
        peaks.append((x, y))
    ridge, light, rims = [(0, peaks[0][1] + H * .06)], [], []
    valleys = []
    for i, (x, y) in enumerate(peaks):
        nxt = peaks[i + 1] if i + 1 < len(peaks) else (W, H * .6)
        valley = ((x + nxt[0]) / 2 + rnd.uniform(-60, 60), max(y, nxt[1]) + rnd.uniform(90, 200))
        shoulder = (x + (valley[0] - x) * .45, y + (valley[1] - y) * .35 + rnd.uniform(-25, 25))
        ridge += [(x, y), shoulder, valley]
        valleys.append(valley)
    for i, (x, y) in enumerate(peaks):
        # face virada para o sol: um triângulo estreito entre o cume, a encosta e o eixo do pico
        if x < sun_x:
            vx, vy = valleys[i]
        else:
            vx, vy = valleys[i - 1] if i else (0, H * .62)
        sx_, sy_ = x + (vx - x) * .5, y + (vy - y) * .5
        ix, iy = x + (vx - x) * .1, y + (vy - y) * .8
        light.append(f'<polygon fill="{c["pico_luz"]}" points="{x:.0f},{y:.0f} {sx_:.0f},{sy_:.0f} {ix:.0f},{iy:.0f}"/>')
        rims.append(f'<path d="M{x:.0f} {y:.0f} L{sx_:.0f} {sy_:.0f}" stroke="{c["sol"]}" stroke-width="7" '
                    f'stroke-linecap="round" fill="none" opacity=".55"/>')
    ridge.append((W, peaks[-1][1] + H * .06))  # sem degrau reto na borda
    pts = " ".join(f"{x:.0f},{y:.0f}" for x, y in ridge)
    far = f'<polygon fill="{c["pico"]}" points="{pts} {W},{H} 0,{H}"/>' + "".join(light)
    # segunda serra, um pouco mais escura, também mais alta nas bordas
    serra = [(W * k / 12, H * (.7 - .12 * edge(W * k / 12) - rnd.uniform(0, .04))) for k in range(13)]
    far += f'<path fill="{c["serra"]}" d="{_smooth(serra)} L{W} {H} L0 {H} Z"/>'
    far += f'<rect y="{H * .55:.0f}" width="{W}" height="{H * .2:.0f}" fill="url(#nevoa)"/>'
    out.append(f'<g id="distantes">{far}</g><!-- /distantes -->')
    # intermediárias: morros violeta
    mid = [(W * k / 10, H * (.78 - .1 * edge(W * k / 10) - rnd.uniform(0, .04))) for k in range(11)]
    out.append(f'<g id="intermediarias"><path fill="{c["meio"]}" d="{_smooth(mid)} L{W} {H} L0 {H} Z"/>'
               f'<rect y="{H * .7:.0f}" width="{W}" height="{H * .12:.0f}" fill="url(#nevoa)" opacity=".6"/>'
               '</g><!-- /intermediarias -->')
    # primeiro plano: colina escura e pinheiros nos cantos (grandes) e embaixo (pequenos)
    near = [(W * k / 8, H * (.88 - .05 * edge(W * k / 8) - rnd.uniform(0, .02))) for k in range(9)]
    front = [f'<path fill="{c["perto"]}" d="{_smooth(near)} L{W} {H} L0 {H} Z"/>']
    pines_far, pines = [], []
    for side in (0, 1):
        for _ in range(9):
            fx = rnd.uniform(0, .2)
            x = W * (fx if side == 0 else 1 - fx)
            h = H * (.18 + .3 * (1 - fx / .2)) * rnd.uniform(.75, 1.05)
            base = H * rnd.uniform(.9, 1.02)
            (pines if h > H * .3 else pines_far).append((x, base, h))
    for _ in range(26):
        x = W * rnd.uniform(.05, .95)
        pines_far.append((x, H * rnd.uniform(.93, 1.0), H * rnd.uniform(.05, .1)))
    for group, color in ((pines_far, c["pinheiro_longe"]), (pines, c["pinheiro"])):
        for x, base, h in sorted(group, key=lambda t: t[1]):
            front.append(f'<g fill="{color}" data-pinheiro="{x:.0f},{base - h:.0f}">{_pine(x, base, h, h * .42)}</g>')
    out.append(f'<g id="primeiro-plano">{"".join(front)}</g><!-- /primeiro-plano -->')
    # destaques: borda de luz no alto dos picos do lado do sol
    out.append(f'<g id="destaques">{"".join(rims)}</g><!-- /destaques -->')
    out.append("</svg>\n")
    return "".join(out)


def svg_texture(svg, width, height):
    """Desenha o SVG de verdade no tamanho da miniatura (a prévia é o próprio papel de parede)."""
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(width, height)
    loader.write(svg.encode())
    loader.close()
    return Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())


def ensure_wallpapers():
    WALLS.mkdir(parents=True, exist_ok=True)
    dusk = WALLS / "entardecer.svg"  # a paisagem nas cores originais, pronta na seção Papel de parede
    if not dusk.exists():
        dusk.write_text(design_svg(LANDSCAPE_DEFAULT, "entardecer", 0))
    for name, colors in WALL_PALETTES.items():
        f = WALLS / f"{name}.svg"
        if not f.exists():
            f.write_text(wall_svg(*colors))


# ---------------------------------------------------------------- aba Cores: paletas a partir de uma cor

FAVORITE_COLORS = [("Azul", "#3584e4"), ("Roxo", "#9141ac"), ("Rosa", "#e66197"), ("Vermelho", "#e01b24"),
                   ("Laranja", "#ff7800"), ("Amarelo", "#f6d32d"), ("Verde", "#2ec27e"), ("Turquesa", "#2190a4"),
                   ("Cinza", "#8a8f98")]
# cores de destaque que o GNOME 47+ aceita em org.gnome.desktop.interface accent-color
GNOME_ACCENTS = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800",
                 "orange": "#ed5b00", "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396"}
# (chave, nome, explicação com o termo técnico, giro do 2º tom em graus, escura?)
HARMONIES = [
    ("mono", "Um tom só", "Monocromática: variações claras e escuras da mesma cor.", 0, True),
    ("analoga", "Tons vizinhos", "Análoga: a cor e uma vizinha dela no círculo das cores (35°).", 35, True),
    ("complementar", "Contraste", "Complementar: a cor e a oposta a ela no círculo das cores (180°).", 180, True),
    ("triade", "Três tons", "Tríade: cores a um terço do círculo uma da outra (120°).", 120, True),
    ("clara", "Clara", "Análoga com fundo claro.", 35, False),
]
PALETTE_START, PALETTE_END = f"/* {APP_NAME}: paleta */", f"/* {APP_NAME}: fim da paleta */"


def hex_hls(text):
    c = rgba(text)
    return colorsys.rgb_to_hls(c.red, c.green, c.blue)


def hls_hex(h, l, s):
    r, g, b = colorsys.hls_to_rgb(h % 1, min(max(l, 0), 1), min(max(s, 0), 1))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def luminance(text):
    c = rgba(text)
    lin = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in (c.red, c.green, c.blue)]
    return .2126 * lin[0] + .7152 * lin[1] + .0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + .05) / (lb + .05)


def readable_on(bg):
    return max(("#ffffff", "#000000"), key=lambda fg: contrast(fg, bg))


def color_name(text):
    """Nome da cor (das favoritas) mais parecida, para não mostrar só o código."""
    h, l, sat = hex_hls(text)
    if sat < .15 or l < .08 or l > .95:
        return "Cinza" if .08 <= l <= .95 else ("Preto" if l < .08 else "Branco")

    def dist(item):
        d = abs(hex_hls(item[1])[0] - h)
        return min(d, 1 - d)
    return min((c for c in FAVORITE_COLORS if c[0] != "Cinza"), key=dist)[0]


def gnome_accent(text):
    """A cor de destaque do GNOME mais parecida (pelo tom; cinza vira 'slate')."""
    h, _l, s = hex_hls(text)
    if s < .15:
        return "slate"

    def dist(item):
        d = abs(hex_hls(item[1])[0] - h)
        return min(d, 1 - d)
    return min(((k, v) for k, v in GNOME_ACCENTS.items() if k != "slate"), key=dist)[0]


def make_palettes(color):
    """Paletas que combinam com a cor favorita: fundo, conteúdo, texto, destaque e wallpaper."""
    h, l, s = hex_hls(color)
    grey = s < .12
    sat = 0 if grey else max(s, .55)
    tint = 0 if grey else .22
    # sem tom (preto, cinza, branco) girar o círculo das cores não muda nada: a claridade da cor
    # escolhida decide o destaque (preto → prata sobre fundo quase preto; branco → quase branco) e
    # cada harmonia varia o 2º tom pela claridade, para as paletas não saírem iguais
    black, white = grey and l < .25, grey and l > .85
    grey_acc = .8 if black else .9 if white else min(max(l, .5), .64)
    grey_second = {0: .62, 35: .45, 180: .32, 120: .76}
    deep = .055 if black else .11
    out = []
    for key, title, tech, turn, dark in HARMONIES:
        h2 = h + turn / 360
        if dark:
            accent = hls_hex(h, grey_acc if grey else min(max(l, .5), .64), sat)
            accent2 = hls_hex(h, grey_second[turn], 0) if grey else \
                hls_hex(h2, .72 if turn == 0 else .6, sat * (.6 if turn == 0 else 1))
            bg, base, fg = hls_hex(h, deep, tint), hls_hex(h, deep + .035, tint), hls_hex(h, .93, tint * .5)
            wall = (hls_hex(h, deep + .02, tint * 1.3), hls_hex(h, max(deep - .06, .02), tint), accent, accent2)
        else:
            accent = hls_hex(h, .15 if black else min(max(l, .36), .42), sat)
            accent2 = hls_hex(h, .55, 0) if grey else hls_hex(h2, .5, sat)
            bg, base, fg = hls_hex(h, .955, tint * 1.4), hls_hex(h, .99, tint), hls_hex(h, .13, tint)
            wall = (hls_hex(h, .9, tint * 2), hls_hex(h, .76, tint * 2), accent, accent2)
        out.append(dict(key=key, title=title, tech=tech, dark=dark, accent=accent, accent2=accent2, accent_fg=readable_on(accent),
                        bg=bg, base=base, fg=fg, wall=wall))
    return out


SYSTEM_FAMILY = re.compile(r"^(Zorin[A-Z][a-z]+-(Light|Dark)|Yaru(-[a-z]+){0,2})$")


def system_variant(accent, dark, themes):
    """O tema de cor do sistema (com painel) mais parecido com a cor de destaque, ou None."""
    cands = [t for t in themes if SYSTEM_FAMILY.match(t.name) and (t.path / "gnome-shell").is_dir()
             and t.name.lower().endswith("-dark") == dark]
    if not cands:
        return None
    h, _l, s = hex_hls(accent)
    tones = {t.name: hex_hls(theme_palette(t)[3].to_string()) for t in cands}
    if s < .15:
        return min(tones, key=lambda n: tones[n][2])

    def dist(name):
        th, _tl, ts = tones[name]
        d = abs(th - h)
        return min(d, 1 - d) + abs(ts - s) * .15 + (1 if ts < .15 else 0)  # laranja vivo ≠ marrom
    return min(tones, key=dist)


FOLDER_PREFIX = "Adon-Pastas-"
# desenho por cima da pasta (caminhos num quadro de 64x64)
FOLDER_GLYPHS = {
    "folder": "", "inode-directory": "", "folder-open": "", "folder-drag-accept": "", "folder-remote": "",
    "folder-download": '<path d="M32 31v12M26.5 38l5.5 5.5 5.5-5.5M25 48h14" class="l"/>',
    "folder-documents": '<path d="M25 33h14M25 39h14M25 45h9" class="l"/>',
    "folder-music": '<path d="M36 31v13" class="l"/><circle cx="32" cy="45" r="4" class="f"/>',
    "folder-pictures": '<path d="M24 47l6-7 4 4 3-3 5 6z" class="f"/><circle cx="37" cy="34" r="2.5" class="f"/>',
    "folder-videos": '<path d="M29 33v12l10-6z" class="f"/>',
    "folder-publicshare": '<circle cx="32" cy="35" r="3.5" class="f"/><path d="M25 47c0-4 3-6.5 7-6.5s7 2.5 7 6.5" class="l"/>',
    "folder-templates": '<rect x="25" y="31" width="14" height="16" rx="1.5" class="l"/><path d="M28 36h8M28 41h8" class="l"/>',
    "user-home": '<path d="M24.5 40l7.5-7.5 7.5 7.5M27 38v9h10v-9" class="l"/>',
    "user-desktop": '<rect x="24" y="32" width="16" height="11" rx="1.5" class="l"/><path d="M29 47h6" class="l"/>',
}


def folder_svg(front, back, glyph):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
            f'<style>.l{{fill:none;stroke:{back};stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round}}'
            f'.f{{fill:{back}}}</style>'
            f'<path d="M6 14a4 4 0 0 1 4-4h13.5l5.5 5h25a4 4 0 0 1 4 4v31H6z" fill="{back}"/>'
            f'<rect x="6" y="21" width="52" height="34" rx="4" fill="{front}"/>{glyph}</svg>\n')


def folder_icon_theme(pal, current):
    """Tema de ícones com as pastas na cor da paleta; o resto vem do tema de ícones atual. Nome novo
    a cada cor (os apps só recarregam os ícones quando o nome muda) e o anterior é apagado."""
    root = USER_DATA / "icons"
    previous = current
    if current.startswith(FOLDER_PREFIX):  # já é uma pasta colorida: herda do tema de verdade
        index = root / current / "index.theme"
        m = re.search(r"^Inherits=([^,\n]+)", index.read_text(), re.M) if index.exists() else None
        current = m.group(1) if m else "Adwaita"
    name = f"{FOLDER_PREFIX}{pal['accent'][1:]}-{'escuro' if pal['dark'] else 'claro'}"
    for old in root.glob(f"{FOLDER_PREFIX}*"):
        if old.name not in (name, previous):  # a anterior fica para o "desfazer"
            shutil.rmtree(old, ignore_errors=True)
    places = root / name / "scalable" / "places"
    places.mkdir(parents=True, exist_ok=True)
    h, l, s = hex_hls(pal["accent"])
    front, back = pal["accent"], hls_hex(h, l * .62, s)
    for icon, glyph in FOLDER_GLYPHS.items():
        (places / f"{icon}.svg").write_text(folder_svg(front, back, glyph))
    (root / name / "index.theme").write_text(
        f"[Icon Theme]\nName={name}\nComment={APP_NAME}: pastas na cor da paleta\n"
        f"Inherits={current},Adwaita,hicolor\nHidden=true\nDirectories=scalable/places\n\n"
        f"[scalable/places]\nSize=64\nMinSize=8\nMaxSize=512\nType=Scalable\nContext=Places\n")
    return name


def palette_css(pal):
    h, l, s = hex_hls(pal["bg"])
    side = hls_hex(h, l + (.03 if pal["dark"] else -.03), s)
    colors = {
        "accent_color": pal["accent"], "accent_bg_color": pal["accent"],
        "accent_fg_color": pal["accent_fg"], "window_bg_color": pal["bg"], "window_fg_color": pal["fg"],
        "view_bg_color": pal["base"], "view_fg_color": pal["fg"], "headerbar_bg_color": side,
        "headerbar_fg_color": pal["fg"], "sidebar_bg_color": side, "sidebar_fg_color": pal["fg"],
        "popover_bg_color": pal["base"], "popover_fg_color": pal["fg"], "dialog_bg_color": pal["bg"],
        "dialog_fg_color": pal["fg"], "card_fg_color": pal["fg"],
        # sem estas, cartões e listas caem no fundo padrão (branco) com o texto da paleta (claro)
        "card_bg_color": hls_hex(h, l + (.06 if pal["dark"] else .03), s) if pal["dark"] else pal["base"],
        "card_shade_color": "rgba(0, 0, 0, 0.36)" if pal["dark"] else "rgba(0, 0, 0, 0.07)",
        "headerbar_backdrop_color": pal["bg"],
        "thumbnail_bg_color": pal["base"], "thumbnail_fg_color": pal["fg"],
    }
    lines = "".join(f"@define-color {k} {v};\n" for k, v in colors.items())
    return f"{PALETTE_START}\n{lines}{PALETTE_END}\n"


# ---------------------------------------------------------------- seleção pendente: selecionar → ver → aplicar

# na ordem em que são aplicados: os ícones antes da paleta (as pastas coloridas herdam deles) e o
# wallpaper depois dela (o que a pessoa escolheu vence o que a paleta gera)
COMPONENTS = [
    ("icones", "Ícones", "folder-pictures-symbolic"),
    ("cores", "Paleta de cores", "applications-graphics-symbolic"),
    ("tema", "Tema", "preferences-desktop-appearance-symbolic"),
    ("cursor", "Cursor", "input-mouse-symbolic"),
    ("wallpaper", "Papel de parede", "preferences-desktop-wallpaper-symbolic"),
    ("barra", "Barra e dock", "view-continuous-symbolic"),
    ("efeitos", "Efeitos", "weather-fog-symbolic"),
]
COMPONENT_TITLE = {k: t for k, t, _i in COMPONENTS}
READING_ORDER = ["tema", "cores", "icones", "cursor", "wallpaper", "barra", "efeitos"]  # como a pessoa lê
# a paleta troca o tema das janelas: escolher uma tira a outra da seleção
EXCLUSIVE = {"cores": ["tema"], "tema": ["cores"]}


class Change:
    """Uma alteração escolhida e ainda não aplicada."""

    def __init__(self, key, value, label, apply, need=(), source=None):
        self.key, self.value, self.label, self.apply = key, value, label, apply
        self.need = list(need)    # itens da Loja que precisam ser baixados antes
        self.source = source      # (página, cartão) de onde veio, para marcar "Selecionado"
        self.enabled, self.error = True, None

    @property
    def title(self):
        return COMPONENT_TITLE[self.key]


class Selection:
    def __init__(self):
        self.changes = {}

    def set(self, change):
        """Põe na seleção; devolve os títulos do que saiu por conflito."""
        dropped = [COMPONENT_TITLE[k] for k in EXCLUSIVE.get(change.key, []) if self.changes.pop(k, None)]
        self.changes[change.key] = change
        return dropped

    def remove(self, key):
        self.changes.pop(key, None)

    def clear(self):
        self.changes.clear()

    def toggle(self, key, on):
        if key in self.changes:
            self.changes[key].enabled = on

    def ordered(self):
        return [self.changes[k] for k, _t, _i in COMPONENTS if k in self.changes]

    def enabled(self):
        return [c for c in self.ordered() if c.enabled]

    def for_reading(self):
        return sorted(self.changes.values(), key=lambda c: READING_ORDER.index(c.key))

    def selected(self, page):
        return {c.source[1] for c in self.changes.values() if c.source and c.source[0] == page}

    def needs(self):
        out = []
        for c in self.enabled():
            for item in c.need:
                if item not in out and not Store.is_installed(item):
                    out.append(item)
        return out

    def apply(self):
        """Aplica o que está marcado, um componente por vez. Um erro não impede os outros.
        Devolve (títulos aplicados, [(título, motivo)]). O que deu certo sai da seleção; o que
        falhou fica, com o motivo, para tentar de novo."""
        import traceback
        ok, failed = [], []
        for c in self.enabled():
            try:
                c.apply()
            except Exception as e:  # noqa: BLE001 — qualquer falha vira relatório, não trava o resto
                traceback.print_exc()
                c.error = str(e) or type(e).__name__
                failed.append((c.title, c.error))
                continue
            ok.append(c.title)
            self.changes.pop(c.key, None)
        return ok, failed


class NotInstalled(Exception):
    pass


def human_list(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " e " + items[-1] if items else ""


# ---------------------------------------------------------------- meus estilos

def load_styles():
    try:
        data = json.loads(MY_STYLES.read_text())
        return [s for s in data if isinstance(s, dict) and s.get("name")]
    except (OSError, ValueError):
        return []


def save_style(style):
    """Guarda (ou substitui, pelo nome) um estilo."""
    styles = [s for s in load_styles() if s["name"] != style["name"]] + [style]
    MY_STYLES.parent.mkdir(parents=True, exist_ok=True)
    MY_STYLES.write_text(json.dumps(styles, indent=1, ensure_ascii=False))


def delete_style(name):
    MY_STYLES.parent.mkdir(parents=True, exist_ok=True)
    MY_STYLES.write_text(json.dumps([s for s in load_styles() if s["name"] != name], indent=1, ensure_ascii=False))


# ---------------------------------------------------------------- o que o ambiente deixa mudar

def environment(desk, user_theme):
    return dict(user_theme=user_theme, accent=has_key(desk.iface, "accent-color"), bar=desk.bar is not None,
                bar_name="a barra do Zorin OS" if desk.bar and "zorin" in desk.bar.props.schema_id else "o Dash to Panel",
                blur=Blur.installed(), lock=desk.lock is not None,
                animations=desk.iface.get_boolean("enable-animations") if has_key(desk.iface, "enable-animations")
                else True)


def limitations(key, env):
    """Avisos que aparecem junto do componente afetado. Só o que é verdade neste sistema."""
    notes = []
    if key in ("tema", "cores"):
        if env["user_theme"] == "falta":
            notes.append("O painel do GNOME não muda: falta a extensão “User Themes” (pacote "
                         "gnome-shell-extensions). As janelas mudam normalmente.")
        elif env["user_theme"] == "carregar":
            notes.append("O painel do GNOME só muda depois que a extensão “User Themes” carregar "
                         "(confirme a instalação ou saia e entre de novo na sessão).")
    if key == "tema":
        notes.append("Apps GTK 3 usam o tema inteiro. Apps GTK 4 (Arquivos, Configurações…) recebem as cores "
                      "e parte dos detalhes. Apps Flatpak, Qt, Electron e navegadores podem não mudar.")
        notes.append("Apps abertos podem precisar ser reabertos.")
    if key == "cores":
        notes.append("Troca o tema das janelas pelo Adwaita com estas cores, pinta as pastas e gera um papel "
                     "de parede (a menos que você escolha outro).")
        if not env["accent"]:
            notes.append("Este GNOME não tem cor de destaque do sistema (só a partir do GNOME 47): "
                         "a cor vale para os apps, não para o painel.")
    if key == "icones":
        notes.append("Apps Flatpak e apps com ícone próprio podem continuar com o ícone de antes.")
    if key == "cursor":
        notes.append("Alguns apps só trocam o cursor quando são reabertos.")
    if key == "wallpaper" and env["lock"]:
        notes.append("Vale para a área de trabalho e para a tela de bloqueio.")
    if key == "barra":
        notes.append(f"Ajusta {env['bar_name']} em todos os monitores conectados.")
    if key == "efeitos":
        notes.append("Usa a extensão Blur my Shell. O vidro nas janelas depende do app e pesa na placa de vídeo.")
    return notes


# ---------------------------------------------------------------- interface

# A estrutura do app usa só as cores nomeadas da libadwaita: fica neutra, com bordas discretas,
# e não muda enquanto a pessoa só está vendo prévias (nada é aplicado antes do "Aplicar").
CSS = """
.studio-card { padding: 8px; border-radius: 12px; }
.studio-card.selecionado { box-shadow: inset 0 0 0 2px @accent_bg_color; }
.studio-card:focus-visible { outline: 2px solid @accent_color; outline-offset: 2px; }
.studio-card .titulo { font-weight: 700; }
.studio-card .sub { font-size: 0.85em; opacity: 0.75; }
.studio-thumb { border-radius: 8px; }
.studio-badge { font-size: 0.8em; font-weight: 700; padding: 1px 8px; border-radius: 99px;
                background: alpha(@window_fg_color, 0.1); }
.studio-badge.em-uso { background: alpha(@success_color, 0.18); }
.studio-badge.selecionado { background: @accent_bg_color; color: @accent_fg_color; }
.studio-badge.erro { background: alpha(@error_color, 0.18); }
.studio-check { background: @accent_bg_color; color: @accent_fg_color; border-radius: 99px; padding: 3px; }
.studio-pill { font-size: 0.85em; font-weight: 700; padding: 2px 10px; border-radius: 99px;
               background: alpha(@window_fg_color, 0.1); }
.studio-pill.ok { background: alpha(@success_color, 0.18); }
.studio-pill.erro { background: alpha(@error_color, 0.18); }
.studio-notice { padding: 10px 16px; margin: 12px 24px 0 24px; }
.studio-note { padding: 8px 10px; border-radius: 8px; background: alpha(@window_fg_color, 0.05); }
.studio-note.erro { background: alpha(@error_color, 0.12); }
.studio-veil { background: alpha(@window_bg_color, 0.7); border-radius: 12px; }
.studio-done { color: @success_color; background: alpha(@success_color, 0.18); border-radius: 99px;
               padding: 10px; }
.studio-dock { background: alpha(black, 0.45); border-radius: 8px; padding: 3px 6px; }
.studio-swatch { border-radius: 99px; }
"""

# Atalhos que funcionam também na libadwaita 1.1 / GTK 4.6 (Ubuntu 22.04, Zorin 17, Pop!_OS 22.04)


def switch_row(title, subtitle=None):
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    sw = Gtk.Switch(valign=Gtk.Align.CENTER)
    row.add_suffix(sw)
    row.set_activatable_widget(sw)
    return row, sw


def spin_row(title, lo, hi, subtitle=None, unit=None, step=1):
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    spin = Gtk.SpinButton.new_with_range(lo, hi, step)
    spin.set_valign(Gtk.Align.CENTER)
    row.add_suffix(spin)
    if unit:
        row.add_suffix(Gtk.Label(label=unit, css_classes=["dim-label"]))
    set_label(spin, title)
    return row, spin


def scale_row(title, lo, hi, subtitle=None, unit="%"):
    """Controle deslizante com o valor escrito ao lado (não só a posição da bolinha)."""
    row = Adw.ActionRow(title=title, subtitle=subtitle or "")
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
    scale.set_size_request(200, -1)
    scale.set_valign(Gtk.Align.CENTER)
    value = Gtk.Label(width_chars=5, xalign=1)
    scale.connect("value-changed", lambda s: value.set_label(f"{s.get_value():.0f} {unit}"))
    row.add_suffix(scale)
    row.add_suffix(value)
    set_label(scale, title)
    scale.row = row
    return row, scale


def set_label(widget, text):
    """Nome acessível (leitor de tela). PyGObject antigo pode não ter o atalho: aí fica o tooltip."""
    try:
        widget.update_property([Gtk.AccessibleProperty.LABEL], [text])
    except (TypeError, AttributeError, ValueError):
        widget.set_tooltip_text(text)


def picture(fit="cover", **kw):
    pic = Gtk.Picture(**kw)
    if hasattr(Gtk, "ContentFit"):
        pic.set_content_fit(Gtk.ContentFit.COVER if fit == "cover" else Gtk.ContentFit.CONTAIN)
    else:
        pic.set_keep_aspect_ratio(fit != "cover")
    return pic


def animations_enabled():
    """Preferência de movimento reduzido: GNOME → Acessibilidade → Animações."""
    s = Gtk.Settings.get_default()
    return s is None or s.props.gtk_enable_animations


class Spinner(Gtk.DrawingArea):
    """Arco girando na cor de destaque. O Gtk.Spinner usa o ícone do tema de ícones ativo e muda
    de cara (ou some) a cada tema; este fica igual em qualquer um. Com as animações do sistema
    desligadas, fica parado."""

    def __init__(self, size=40, color=None):
        super().__init__(content_width=size, content_height=size)
        self._angle, self._tick, self._color = 0.0, None, color
        self.set_draw_func(self._draw)

    def start(self):
        if self._tick is None and animations_enabled():
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
        ok, acc = bool(self._color), self._color
        try:
            if not ok:
                ok, acc = self.get_style_context().lookup_color("accent_bg_color")
        except (AttributeError, TypeError):
            pass
        acc = acc if ok else rgba("#3584e4")
        lw = max(2, min(w, h) / 10)
        r = min(w, h) / 2 - lw
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


def load_async(pic, path, width, height):
    """Carrega a miniatura fora da tela principal para a janela não travar."""
    def work():
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), width, height, True)
        except GLib.Error:
            return
        GLib.idle_add(lambda: pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb)) or False)
    POOL.submit(work)


def _dialog(parent, heading, body, responses, on_response, extra=None):
    if hasattr(Adw, "MessageDialog"):
        dlg = Adw.MessageDialog(transient_for=parent, heading=heading, body=body)
        for rid, label, style in responses:
            dlg.add_response(rid, label)
            if style:
                dlg.set_response_appearance(rid, style)
        dlg.set_default_response(responses[-1][0])
        dlg.set_close_response(responses[0][0])
        if extra:
            dlg.set_extra_child(extra)
        dlg.connect("response", lambda _d, r: on_response(r))
    else:
        buttons = Gtk.ButtonsType.OK if len(responses) == 1 else Gtk.ButtonsType.OK_CANCEL
        dlg = Gtk.MessageDialog(transient_for=parent, modal=True, buttons=buttons, text=heading, secondary_text=body)
        if extra:
            dlg.get_message_area().append(extra)
        last = responses[-1][0]
        dlg.connect("response", lambda d, r: (d.destroy(), on_response(last if r == Gtk.ResponseType.OK
                                                                        else responses[0][0])))
    dlg.present()
    return dlg


def _appearance(name):
    """Adw.ResponseAppearance só existe a partir da libadwaita 1.2 (junto com o MessageDialog)."""
    kind = getattr(Adw, "ResponseAppearance", None)
    return getattr(kind, name) if kind else None


def confirm(parent, heading, body, action, on_yes, destructive=False):
    style = _appearance("DESTRUCTIVE" if destructive else "SUGGESTED")
    return _dialog(parent, heading, body, [("cancel", "Cancelar", None), ("ok", action, style)],
                   lambda r: r == "ok" and on_yes())


def inform(parent, heading, body):
    return _dialog(parent, heading, body, [("ok", "Entendi", None)], lambda r: None)


def ask_text(parent, heading, body, action, initial, on_ok):
    entry = Gtk.Entry(text=initial, activates_default=True, hexpand=True)
    set_label(entry, heading)
    return _dialog(parent, heading, body, [("cancel", "Cancelar", None),
                                           ("ok", action, _appearance("SUGGESTED"))],
                   lambda r: r == "ok" and entry.get_text().strip() and on_ok(entry.get_text().strip()), entry)


def mb(n):
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def dots(colors, size=12):
    """Bolinhas com as cores principais (complemento visual; o nome vem escrito no subtítulo)."""
    box = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER)
    for c in colors:
        box.append(drawing(size, size, lambda cr, w, h, c=rgba(c) if isinstance(c, str) else c: (
            _src(cr, c), cr.arc(w / 2, h / 2, w / 2 - .5, 0, 2 * pi), cr.fill_preserve(),
            _src(cr, rgba("#808080"), .5), cr.set_line_width(1), cr.stroke())))
    return box


def status_page(icon, title, description=None, button=None, on_click=None):
    page = Adw.StatusPage(icon_name=icon, title=title, description=description or "", vexpand=True)
    if button:
        btn = Gtk.Button(label=button, halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        btn.connect("clicked", lambda _b: on_click())
        page.set_child(btn)
    return page


class Grid(Gtk.Box):
    """Catálogo de cartões com busca, filtros e os estados vazio, carregando e sem resultados.
    Cada cartão separa: passar o mouse (realce do botão), foco do teclado (contorno), selecionado
    (borda + ✓ + texto) e em uso (etiqueta “Em uso”)."""

    def __init__(self, on_pick, extra=None, search=True, scroll=True, filters=None, placeholder="Buscar…",
                 empty=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_pick, self.cards, self.query, self.kind, self.working = on_pick, {}, "", None, None
        self.tag, self.loading, self.in_use, self.picked = None, False, None, set()
        margin = 24 if scroll else 0
        bar = Gtk.Box(spacing=8, margin_start=margin, margin_end=margin, margin_top=12)
        self.search = None
        if search:
            self.search = Gtk.SearchEntry(placeholder_text=placeholder, hexpand=True)
            set_label(self.search, placeholder.rstrip("…"))
            self.search.connect("search-changed", self._search)
            bar.append(self.search)
        if extra:
            bar.append(extra)
        if search or extra:
            self.append(bar)
        if filters:
            fbox = Gtk.Box(spacing=6, margin_start=margin, margin_end=margin, margin_top=10)
            first = None
            for label, tag in filters:
                b = Gtk.ToggleButton(label=label, active=first is None, css_classes=["pill"])
                if first:
                    b.set_group(first)
                first = first or b
                b.connect("toggled", lambda b, t=tag: b.get_active() and self.filter_tag(t))
                fbox.append(b)
            self.append(fbox)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, valign=Gtk.Align.START,
                                max_children_per_line=6, min_children_per_line=1, row_spacing=14,
                                column_spacing=14, margin_start=margin, margin_end=margin, margin_top=12,
                                margin_bottom=18)
        self.flow.set_filter_func(self._visible)
        # setas do teclado param no espaço do cartão; Enter ali aciona o cartão
        self.flow.connect("child-activated", lambda _f, child: self._btn(child).emit("clicked"))
        self.status = Gtk.Stack(vexpand=scroll)
        self.status.add_named(Gtk.ScrolledWindow(child=self.flow, vexpand=True,
                                                 hscrollbar_policy=Gtk.PolicyType.NEVER) if scroll else self.flow,
                              "lista")
        self.status.add_named(status_page("edit-find-symbolic", "Nenhum resultado",
                                          "Tente outra palavra ou outro filtro."), "sem-resultados")
        load = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER,
                       valign=Gtk.Align.CENTER, vexpand=True)
        self._spinner = Spinner(32)
        load.append(self._spinner)
        load.append(Gtk.Label(label="Carregando…", css_classes=["dim-label"]))
        self.status.add_named(load, "carregando")
        self.status.add_named(empty or status_page("folder-symbolic", "Nada por aqui"), "vazio")
        self.append(self.status)

    @staticmethod
    def _btn(child):
        w = child.get_child()
        return getattr(w, "btn", w)

    def _visible(self, child):
        btn = self._btn(child)
        return self.query in btn.search_text and (self.kind is None or btn.kind == self.kind) and \
            (self.tag is None or self.tag in btn.tags)

    def _update_status(self):
        if not self.cards:
            name = "carregando" if self.loading else "vazio"
        elif any(self._visible(c.get_parent()) for c in self._cells()):
            name = "lista"
        else:
            name = "carregando" if self.loading else "sem-resultados"
        self.status.set_visible_child_name(name)
        (self._spinner.start if name == "carregando" else self._spinner.stop)()

    def _cells(self):
        child = self.flow.get_first_child()
        while child:
            yield child.get_child()
            child = child.get_next_sibling()

    def _search(self, entry):
        self.query = entry.get_text().lower()
        self.flow.invalidate_filter()
        self._update_status()

    def set_query(self, text):
        if self.search:
            self.search.set_text(text)
            self._search(self.search)  # o "search-changed" chega com atraso

    def filter_kind(self, kind):
        self.kind = kind
        self.flow.invalidate_filter()
        self._update_status()

    def filter_tag(self, tag):
        self.tag = tag
        self.flow.invalidate_filter()
        self._update_status()

    def set_loading(self, on):
        self.loading = on
        self._update_status()

    def add(self, key, preview, title, subtitle=None, extra=None, kind=None, tags=(), colors=None, below=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(preview)
        head = Gtk.Box(spacing=6)
        # max_width_chars segura a largura natural: nomes compridos não esticam a grade inteira
        head.append(Gtk.Label(label=title, xalign=0, ellipsize=3, max_width_chars=18, hexpand=True,
                              css_classes=["titulo"]))
        badge = Gtk.Label(label="Em uso", css_classes=["studio-badge", "em-uso"], visible=False,
                          valign=Gtk.Align.CENTER)
        head.append(badge)
        box.append(head)
        if subtitle or colors:
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=subtitle or "", xalign=0, ellipsize=3, max_width_chars=22, hexpand=True,
                                 css_classes=["sub"]))
            if colors:
                row.append(dots(colors))
            box.append(row)
        if extra:
            box.append(extra)
        check = Gtk.Image(icon_name="object-select-symbolic", css_classes=["studio-check"], visible=False,
                          halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=6, margin_end=6)
        # véu do "carregando": círculo girando enquanto trabalha, ✓ quando termina
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, halign=Gtk.Align.CENTER,
                          valign=Gtk.Align.CENTER, hexpand=True, vexpand=True)
        stack.add_named(Spinner(40), "load")
        stack.add_named(Gtk.Image(icon_name="object-select-symbolic", pixel_size=26,
                                  css_classes=["studio-done"]), "ok")
        veil = Gtk.Box(css_classes=["studio-veil"], visible=False)
        veil.append(stack)
        overlay = Gtk.Overlay(child=box)
        overlay.add_overlay(check)
        overlay.add_overlay(veil)
        btn = Gtk.Button(child=overlay, css_classes=["card", "studio-card"])
        btn.title, btn.search_text, btn.kind, btn.tags = title, f"{title} {subtitle or ''}".lower(), kind, set(tags)
        btn.veil, btn.veil_stack, btn.badge, btn.check = veil, stack, badge, check
        btn.connect("clicked", lambda _b: self.working is None and self.on_pick(key))
        cell = btn
        if below:
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            cell.append(btn)
            cell.append(below)
            cell.btn = btn
        self.flow.append(cell)
        self.cards[key] = btn
        self._describe(key)
        if self.status.get_visible_child_name() != "lista" and self._visible(cell.get_parent()):
            self._update_status()
        return btn

    def _describe(self, key):
        btn = self.cards[key]
        states = (["selecionado"] if key in self.picked else []) + (["em uso"] if key == self.in_use else [])
        text = ", ".join([btn.title] + states)
        btn.set_tooltip_text(text)
        set_label(btn, text)

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

    def clear(self):
        while child := self.flow.get_first_child():
            self.flow.remove(child)
        self.cards, self.working = {}, None
        self._update_status()

    def mark(self, key):
        """Etiqueta “Em uso” no cartão do que está aplicado no sistema."""
        self.in_use = key
        for k, btn in self.cards.items():
            btn.badge.set_visible(k == key)
            self._describe(k)

    def select(self, keys):
        """Borda e ✓ nos cartões escolhidos e ainda não aplicados."""
        self.picked = set(keys)
        for k, btn in self.cards.items():
            on = k in self.picked
            (btn.add_css_class if on else btn.remove_css_class)("selecionado")
            btn.check.set_visible(on)
            self._describe(k)


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


# ---------------------------------------------------------------- prévias de área de trabalho

DOCK_APPS = [["web-browser", "firefox", "google-chrome"], ["system-file-manager", "org.gnome.Nautilus", "folder"],
             ["utilities-terminal", "org.gnome.Terminal", "terminal"],
             ["accessories-text-editor", "org.gnome.TextEditor", "text-editor"],
             ["org.gnome.Settings", "preferences-system", "gnome-control-center"]]


def desktop_preview(wall, pal, icons, width, height, fallback=WALL_PALETTES["graphite"]):
    """Ilustração de uma área de trabalho: o papel de parede de verdade, uma janela com as cores
    do tema, a barra do topo e um dock com os ícones do tema (quando ele está instalado)."""
    if wall and Path(wall).exists():
        base = picture(width_request=width, height_request=height, css_classes=["studio-thumb"],
                       overflow=Gtk.Overflow.HIDDEN, can_shrink=True)
        load_async(base, wall, width, height)
    else:
        base = drawing(width, height, lambda cr, w, h: draw_backdrop(cr, w, h, fallback))

    def paint(cr, w, h):
        _rr(cr, 0, 0, w, h, 8)
        cr.clip()
        _src(cr, rgba("#000000"), .6)
        cr.rectangle(0, 0, w, max(5, h * .06))
        cr.fill()
        ww = w * .66
        s = ww / 300
        cr.save()
        cr.translate(w * .17, h * .14)
        cr.scale(s, s)
        draw_window(cr, 0, 0, 300, h * .6 / s, pal)
        cr.restore()
    overlay = Gtk.Overlay(child=base, halign=Gtk.Align.CENTER)
    overlay.add_overlay(drawing(width, height, paint))
    size = max(12, round(height * .1))
    dock = Gtk.Box(spacing=max(2, size // 4), halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                   margin_bottom=max(3, round(height * .04)), css_classes=["studio-dock"])
    if icons:
        for paint_ in icon_paintables(icons, size, DOCK_APPS):
            dock.append(Gtk.Image(paintable=paint_, pixel_size=size))
    else:  # tema de ícones ainda não baixado: não finge mostrar os ícones dele
        for c in (pal[3], pal[2], pal[3], pal[2]):
            dock.append(drawing(size, size, lambda cr, w, h, c=c: (_src(cr, c, .8), _rr(cr, 1, 1, w - 2, h - 2, 3),
                                                                    cr.fill())))
    overlay.add_overlay(dock)
    return overlay


def window_preview(pal, width, height):
    """Janela com as cores do tema desenhada em escala (mesma proporção em qualquer tamanho)."""
    def paint(cr, w, h):
        s = (w - 12) / 300
        cr.save()
        cr.translate(6, 6)
        cr.scale(s, s)
        draw_window(cr, 0, 0, 300, (h - 14) / s, pal)
        cr.restore()
    return drawing(width, height, paint)


def draw_bar(cr, w, h, p):
    """Ilustração da barra: posição, comprimento, margem, arredondamento e transparência."""
    draw_backdrop(cr, w, h, WALL_PALETTES["graphite"])
    m = p.get("margin", 0) * .9
    thick = max(10, p.get("size", 48) / 3)
    pos = p.get("position", "BOTTOM")
    vertical = pos in ("LEFT", "RIGHT")
    span = (h if vertical else w)
    length = span * .5 if p.get("length", 100) == -1 else span - 2 * m
    if vertical:
        bw, bh = thick, length
        x = m if pos == "LEFT" else w - thick - m
        y = (h - bh) / 2
    else:
        bw, bh = length, thick
        x = (w - bw) / 2
        y = m if pos == "TOP" else h - thick - m
    _src(cr, rgba("#1e1e1e"), p.get("opacity", 1) if p.get("custom") else 1)
    _rr(cr, x, y, bw, bh, min(p.get("radius", 0) * 3, thick / 2) or .1)
    cr.fill()
    colors = ["#62a0ea", "#57e389", "#f8e45c", "#ffa348", "#dc8add"]
    for i, c in enumerate(colors):
        _src(cr, rgba(c))
        off = (i - 2) * thick * .9
        cx, cy = (x + bw / 2, h / 2 + off) if vertical else (w / 2 + off, y + bh / 2)
        cr.arc(cx, cy, thick * .28, 0, 2 * pi)
        cr.fill()


def draw_glass(cr, w, h, p):
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


BAR_PRESETS = {
    "Dock flutuante": dict(length=-1, margin=8, radius=3, size=52, custom=True, opacity=.85),
    "Barra flutuante": dict(length=100, margin=8, radius=3, size=48, custom=True, opacity=.85),
    "Barra clássica": dict(length=100, margin=0, radius=0, size=47, custom=False, opacity=.85),
}
BAR_POSITIONS = [("BOTTOM", "Embaixo"), ("TOP", "Em cima"), ("LEFT", "À esquerda"), ("RIGHT", "À direita")]
ILLUSTRATIVE = "Ilustração: mostra as cores e a composição, não é uma captura do seu sistema."


def same(a, b):
    return all(a.get(k) == v for k, v in b.items())


class Curtain:
    """Cobre todas as telas com a cor do fundo do visual novo, o logo e um carregando, enquanto o
    sistema troca de tema (o GNOME Shell e os apps dão uma travada ao recarregar o tema)."""

    FADE_MS = 220

    def __init__(self, parent):
        self.parent, self.windows = parent, []

    def cover(self, bg, then, accent=None):
        self.uncover()
        monitors = self.parent.get_display().get_monitors()
        for i in range(monitors.get_n_items()):
            win = Gtk.Window(decorated=False, transient_for=self.parent, title=APP_NAME)
            overlay, spin = self.content(bg, accent)
            win.set_child(overlay)
            set_label(win, "Aplicando o novo visual")
            win.fullscreen_on_monitor(monitors.get_item(i))
            win.present()
            spin.start()
            self.windows.append(win)
        # deixa a cortina aparecer na tela antes do trabalho pesado começar
        GLib.timeout_add(self.FADE_MS + 80, lambda: then() and False)

    @staticmethod
    def content(bg, accent=None):
        """Fundo sólido na cor do visual novo, logo, carregando e o texto em preto ou branco,
        o que der mais contraste com esse fundo (tema claro → cortina clara com texto escuro)."""
        color = rgba(bg) or rgba("#242424")
        ink = readable_on(color.to_string())
        back = drawing(10, 10, lambda cr, w, h, c=color: (_src(cr, c), cr.paint()))
        back.set_hexpand(True)
        back.set_vexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER)
        box.append(Gtk.Image(icon_name=APP_ID, pixel_size=128))
        spin = Spinner(44, rgba(accent) if accent else None)  # na cor de destaque do visual novo
        spin.set_halign(Gtk.Align.CENTER)
        box.append(spin)
        text = Gtk.Label()
        text.set_markup(f'<span foreground="{ink}" size="large">Aplicando o novo visual…</span>')
        box.append(text)
        overlay = Gtk.Overlay(child=back)
        overlay.add_overlay(box)
        return overlay, spin

    def uncover(self):
        wins, self.windows = self.windows, []
        if not wins:
            return
        if not animations_enabled():
            for w in wins:
                w.destroy()
            return
        steps = [0]

        def fade():
            steps[0] += 1
            for w in wins:
                w.set_opacity(max(0, 1 - steps[0] / 10))
            if steps[0] >= 10:
                for w in wins:
                    w.destroy()
                return False
            return True
        GLib.timeout_add(self.FADE_MS // 10, fade)


class Window(Adw.ApplicationWindow):
    # nome, título, grupo, ícone, descrição
    PAGES = [
        ("estilos", "Estilos prontos", "Visual", "view-grid-symbolic",
         "Um visual completo: tema, ícones, cursor e papel de parede."),
        ("cores", "Cores", "Visual", "applications-graphics-symbolic",
         "Escolha uma cor e veja paletas que combinam com ela."),
        ("temas", "Temas", "Visual", "preferences-desktop-appearance-symbolic",
         "Como ficam as janelas, os botões e os menus."),
        ("icones", "Ícones", "Visual", "folder-pictures-symbolic", "Os ícones de pastas e aplicativos."),
        ("cursores", "Cursores", "Visual", "input-mouse-symbolic", "O ponteiro do mouse."),
        ("wallpaper", "Papel de parede", "Visual", "preferences-desktop-wallpaper-symbolic",
         "A imagem da área de trabalho e da tela de bloqueio."),
        ("barra", "Barra e dock", "Desktop", "view-continuous-symbolic",
         "Posição, tamanho e formato da barra de tarefas."),
        ("efeitos", "Efeitos", "Desktop", "weather-fog-symbolic", "Vidro fosco em menus, painéis e janelas."),
        ("explorar", "Explorar", "Biblioteca", "system-software-install-symbolic",
         "Baixe temas, ícones e cursores. Instalar não muda o seu visual: depois você escolhe e aplica."),
        ("meus", "Meus estilos", "Biblioteca", "user-bookmarks-symbolic",
         "Visuais que você salvou para usar de novo."),
    ]
    PAGE = {p[0]: p for p in PAGES}
    KIND_PAGE = {"gtk": "temas", "icons": "icones", "cursors": "cursores"}
    WIDE, NARROW = 1080, 760   # três colunas / detalhes em página própria / lateral recolhida
    MIN_LOAD_MS = 600          # o sistema leva um instante para redesenhar; abaixo disso só pisca
    SETTLE_MS = 1200           # com a cortina: tempo para o Shell e os apps terminarem de trocar
    HEAVY = {"tema", "cores", "icones", "cursor"}  # trocas que fazem o sistema dar uma travada

    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=1240, default_height=820)
        self.set_size_request(360, 480)
        self.desk = Desktop()
        self.desk.ensure_backup()
        self.desk.link_cursor_themes()
        self.user_theme = self.desk.ensure_user_theme()
        ensure_wallpapers()
        self.store = Store()
        self.store_loading = True
        self.sel = Selection()
        self.busy = self.applying = False
        self.mode, self.detail_for, self._nav_sync = None, None, False
        self.theme_css = None
        self.curtain = Curtain(self)
        self.grids, self.builders = {}, {}
        self._scan()
        self._actions()

        # --- lateral
        # margens nos próprios widgets: alguns temas GTK zeram o espaçamento do .navigation-sidebar
        self.nav = Gtk.ListBox(css_classes=["navigation-sidebar"], margin_start=6, margin_end=6, margin_top=4)
        self.nav.set_header_func(self._nav_header)
        self.rows = {}
        for name, title, group, icon, _d in self.PAGES:
            box = Gtk.Box(spacing=12, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            row = Gtk.ListBoxRow(child=box)
            row.page, row.group = name, group
            self.nav.append(row)
            self.rows[name] = row
        self.nav.connect("row-selected", self._nav_selected)
        footer = self.footer = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=10, margin_bottom=10)
        footer.append(Gtk.Image(icon_name="video-display-symbolic", valign=Gtk.Align.CENTER))
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        info.append(Gtk.Label(label="Visual atual", xalign=0, css_classes=["caption", "dim-label"]))
        self.current_label = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=18, css_classes=["heading"])
        info.append(self.current_label)
        footer.append(info)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=220)
        side.append(Gtk.ScrolledWindow(child=self.nav, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        side.append(Gtk.Separator())
        side.append(footer)
        self.sidebar = Gtk.Box()
        self.sidebar.append(side)
        self.sidebar_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self.sidebar.append(self.sidebar_sep)
        self.side = side

        # --- centro: catálogo das páginas + painel de prévia/detalhes
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True, hexpand=True)
        for name, title, _g, _i, _d in self.PAGES:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.stack.add_named(holder, name)
            self.builders[name] = (holder, getattr(self, f"_build_{name}"))
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_start=20, margin_end=20,
                              margin_top=18, margin_bottom=18)
        # o Clamp segura a largura: textos com quebra de linha pedem a largura da frase inteira
        self.detail_clamp = Adw.Clamp(child=self.detail, maximum_size=380, tightening_threshold=380)
        # sem hexpand: senão o GTK divide a sobra da janela entre o catálogo e o painel
        self.detail_scroll = Gtk.ScrolledWindow(child=self.detail_clamp, vexpand=True,
                                                hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.side_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=380)
        self.side_panel = Gtk.Box()
        self.side_panel.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        self.side_panel.append(self.side_slot)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        back = Gtk.Button(css_classes=["flat"], halign=Gtk.Align.START, margin_start=12, margin_top=8)
        self.back_label = Gtk.Label()
        back_box = Gtk.Box(spacing=6)
        back_box.append(Gtk.Image(icon_name="go-previous-symbolic"))
        back_box.append(self.back_label)
        back.set_child(back_box)
        back.connect("clicked", lambda _b: self._close_detail())
        page.append(back)
        self.page_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        page.append(self.page_slot)
        self.view = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, hexpand=True)
        self.view.add_named(self.stack, "catalogo")
        self.view.add_named(page, "detalhe")
        center = Gtk.Box(vexpand=True)
        center.append(self.view)
        center.append(self.side_panel)

        # --- avisos e downloads em cima; aplicar/descartar sempre embaixo, sem cobrir nada
        self.notice = Gtk.Label(wrap=True, xalign=0, css_classes=["card", "studio-notice"])
        self.notice_bar = Gtk.Revealer(child=self.notice, reveal_child=self.user_theme == "carregar")
        if self.user_theme == "carregar":
            # instalada junto com o app, depois do login: o Shell aberto não a enxerga até sair e
            # entrar de novo. Pedir para ele instalá-la faz ele carregar na hora.
            self.notice.set_label("Para o painel do sistema também mudar de tema, confirme a instalação "
                                  "da extensão “User Themes” na janela que o GNOME abriu.")
            install_remote_extension(USER_THEME_UUID, self._user_theme_loaded,
                                     lambda: shell_extension_info(USER_THEME_UUID) not in (None, {}))
        self.prog = Gtk.ProgressBar(show_text=True, margin_start=24, margin_end=24, margin_top=10)
        self.prog_bar = Gtk.Revealer(child=self.prog)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        main.append(self.notice_bar)
        main.append(self.prog_bar)
        main.append(center)
        main.append(Gtk.Separator())
        main.append(self._pending_bar())
        self.main = main
        # rodapé da lateral e barra de Aplicar sempre da mesma altura: as linhas acima delas se alinham
        # em qualquer tema (cada tema muda o tamanho dos títulos e dos botões de um jeito)
        self.footer_sizes = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.VERTICAL)
        self.footer_sizes.add_widget(self.footer)
        self.footer_sizes.add_widget(self.pending_box)

        root = Gtk.Box()
        root.append(self.sidebar)
        root.append(main)
        # sonda de largura: decide o layout sem depender de Adw.Breakpoint (libadwaita 1.4+)
        probe = Gtk.DrawingArea(can_target=False, hexpand=True, vexpand=True)
        probe.connect("resize", lambda _a, w, _h: self._on_width(w))
        overlay = Gtk.Overlay(child=root)
        overlay.add_overlay(probe)
        self.toasts = Adw.ToastOverlay(child=overlay, vexpand=True)

        header = Adw.HeaderBar()
        self.title_widget = Adw.WindowTitle(title=APP_NAME)
        header.set_title_widget(self.title_widget)
        self.menu_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", visible=False,
                                         tooltip_text="Mostrar as seções")
        self.menu_btn.connect("toggled", lambda b: self._show_sidebar(b.get_active()))
        header.pack_start(self.menu_btn)
        self.undo_btn = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Desfazer a última aplicação")
        set_label(self.undo_btn, "Desfazer a última aplicação")
        self.undo_btn.connect("clicked", lambda _b: self._ask_undo())
        header.pack_start(self.undo_btn)
        menu = Gio.Menu()
        menu.append("Voltar ao visual original…", "win.restaurar")
        menu.append(f"Sobre o {APP_NAME}", "win.sobre")
        more = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu")
        set_label(more, "Menu")
        header.pack_end(more)
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        view.append(header)
        view.append(self.toasts)
        self.set_content(view)

        self.stack.connect("notify::visible-child-name", lambda *_: self._page_changed())
        self._set_mode("largo")
        self.nav.select_row(self.rows["estilos"])
        self._page_changed()
        self._refresh_chrome()
        self.store.fetch(self._on_catalog)

    def _actions(self):
        for name, fn in (("restaurar", self._ask_restore), ("sobre", self._about)):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, fn=fn: fn())
            self.add_action(act)

    # ------------------------------------------------------------ navegação e layout
    def _nav_header(self, row, before):
        if before is None or before.group != row.group:
            row.set_header(Gtk.Label(label=row.group, xalign=0, margin_start=14, margin_top=14 if before else 6,
                                     margin_bottom=4, css_classes=["caption-heading", "dim-label"]))
        else:
            row.set_header(None)

    def _nav_selected(self, _box, row):
        if row is None or self._nav_sync:
            return
        self.stack.set_visible_child_name(row.page)
        if self.mode == "estreito":
            self.menu_btn.set_active(False)

    def go(self, page):
        self.stack.set_visible_child_name(page)

    @property
    def page(self):
        return self.stack.get_visible_child_name()

    def _page_changed(self):
        name = self.page
        self._ensure_built()
        self._nav_sync = True
        self.nav.select_row(self.rows[name])
        self._nav_sync = False
        self.title_widget.set_subtitle(self.PAGE[name][1])
        self.back_label.set_label(f"Voltar para {self.PAGE[name][1]}")
        picked = self.sel.selected(name)
        self.detail_for = (name, next(iter(picked), None))
        self.view.set_visible_child_name("catalogo")
        self._render_detail()
        self._refresh_chrome()

    def _ensure_built(self):
        name = self.page
        holder, builder = self.builders.get(name, (None, None))
        if builder:
            self.builders[name] = (holder, None)
            builder(holder)
            self.refresh_marks()

    def rebuild(self, *names):
        """Relê o que está instalado e refaz as abas que já foram abertas."""
        self._scan()
        self.desk.link_cursor_themes()
        for name, *_rest in self.PAGES:
            holder, pending = self.builders[name]
            if (names and name not in names) or pending or (not names and name in ("barra", "efeitos")):
                continue
            while child := holder.get_first_child():
                holder.remove(child)
            self.grids.pop(name, None)
            self.builders[name] = (holder, getattr(self, f"_build_{name}"))
        self._ensure_built()

    @staticmethod
    def mode_for(width):
        return "largo" if width >= Window.WIDE else "medio" if width >= Window.NARROW else "estreito"

    def _on_width(self, width):
        mode = self.mode_for(width)
        if mode != self.mode:
            # mudar a árvore de widgets durante a alocação gera avisos do GTK: deixa para depois
            GLib.idle_add(lambda: self._set_mode(mode) or False)

    def _set_mode(self, mode):
        if mode == self.mode:
            return
        self.mode = mode
        slot = self.side_slot if mode == "largo" else self.page_slot
        if self.detail_scroll.get_parent() is not slot:
            if self.detail_scroll.get_parent():
                self.detail_scroll.get_parent().remove(self.detail_scroll)
            slot.append(self.detail_scroll)
        self.side_panel.set_visible(mode == "largo")
        self.detail_clamp.set_maximum_size(380 if mode == "largo" else 640)
        if mode == "largo":
            self.view.set_visible_child_name("catalogo")
        narrow = mode == "estreito"
        self.menu_btn.set_visible(narrow)
        self.menu_btn.set_active(False)
        self._show_sidebar(not narrow)
        self._refresh_chrome()

    def _show_sidebar(self, on):
        """Na janela estreita a lista de seções vira uma tela própria, em vez de espremer o conteúdo."""
        narrow = self.mode == "estreito"
        self.sidebar.set_visible(on)
        self.side.set_hexpand(narrow)
        self.sidebar_sep.set_visible(not narrow)
        self.main.set_visible(not (narrow and on))

    def _open_detail(self):
        if self.mode != "largo":
            self.view.set_visible_child_name("detalhe")
        self._refresh_chrome()

    def _close_detail(self):
        self.view.set_visible_child_name("catalogo")
        self._refresh_chrome()

    def detail_visible(self):
        return self.mode == "largo" or self.view.get_visible_child_name() == "detalhe"

    # ------------------------------------------------------------ barra de alterações pendentes
    def _pending_bar(self):
        bar = self.pending_box = Gtk.Box(spacing=8, margin_start=18, margin_end=18, margin_top=10, margin_bottom=10)
        self.pending_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        bar.append(self.pending_label)
        self.review_btn = Gtk.Button(label="Revisar")
        self.review_btn.connect("clicked", lambda _b: self._open_detail())
        bar.append(self.review_btn)
        self.discard_btn = Gtk.Button(label="Descartar")
        self.discard_btn.connect("clicked", lambda _b: self.discard())
        bar.append(self.discard_btn)
        self.apply_btn = Gtk.Button(css_classes=["suggested-action"])
        inner = Gtk.Box(spacing=8)
        self.apply_spin = Spinner(16)
        self.apply_spin.set_visible(False)
        inner.append(self.apply_spin)
        self.apply_text = Gtk.Label(label="Aplicar")
        inner.append(self.apply_text)
        self.apply_btn.set_child(inner)
        self.apply_btn.connect("clicked", lambda _b: self.apply())
        bar.append(self.apply_btn)
        return bar

    def _refresh_chrome(self):
        """Texto e botões da barra de baixo, desfazer e “Visual atual”."""
        if not hasattr(self, "apply_btn"):
            return
        on = self.sel.enabled()
        total = len(self.sel.changes)
        if self.applying:
            text = "Aplicando…"
        elif not total:
            text = "Nenhuma alteração pendente. Clique num item para ver a prévia."
        elif not on:
            text = "Nenhum componente marcado para aplicar."
        else:
            text = f"{len(on)} alteração pendente" if len(on) == 1 else f"{len(on)} alterações pendentes"
            text += ": " + human_list(c.title for c in self.sel.for_reading() if c.enabled)
            need = self.sel.needs()
            if need:
                text += f" · baixa {mb(sum(i['bytes'] for i in need))} ao aplicar"
        self.pending_label.set_label(text)
        self.apply_btn.set_sensitive(bool(on) and not self.applying and not self.busy)
        self.discard_btn.set_sensitive(bool(total) and not self.applying)
        self.review_btn.set_visible(bool(total) and not self.detail_visible())
        self.apply_text.set_label("Aplicando…" if self.applying else "Aplicar")
        self.apply_spin.set_visible(self.applying)
        (self.apply_spin.start if self.applying else self.apply_spin.stop)()
        self.undo_btn.set_sensitive(self.desk.has_previous() and not self.applying)
        self.current_label.set_label(self._current_name())

    def _current_name(self):
        cur = self.desk.current()
        style = self._style_in_use()
        if style:
            return style
        if cur["icons"].startswith(FOLDER_PREFIX):
            return "Paleta de cores"
        return pretty(cur["gtk"])

    # ------------------------------------------------------------ seleção → prévia → aplicar
    def _pick(self, page, key):
        """Clicar num cartão seleciona e mostra a prévia. Nada muda no sistema até “Aplicar”."""
        if self.applying:
            return
        dropped = []
        for change in self._changes_for(page, key):
            dropped += self.sel.set(change)
        self.detail_for = (page, key)
        self._selection_changed()
        self._open_detail()
        if dropped:
            self.toast(f"{human_list(dropped)} saiu da seleção: não dá para aplicar junto com "
                       f"{'a paleta' if page == 'cores' else 'este item'}")

    def _changes_for(self, page, key):
        src = (page, key)
        if page == "estilos":
            p = self.packs[key]
            out = [Change("tema", p["gtk"], pretty(p["gtk"]), lambda n=p["gtk"]: self._apply_theme(n),
                          self._need(p["gtk"], self.themes), src)]
            if p["icon"]:
                out.append(Change("icones", p["icon"], pretty(p["icon"]), lambda n=p["icon"]: self._apply_icons(n),
                                  self._need(p["icon"], self.icons), src))
            if p["cursor"]:
                out.append(Change("cursor", p["cursor"], pretty(p["cursor"]),
                                  lambda n=p["cursor"]: self._apply_cursor(n), self._need(p["cursor"], self.cursors),
                                  src))
            wall = WALLS / f"{p['wall']}.svg"
            out.append(Change("wallpaper", str(wall), f"Gerado: {key}", lambda: self.desk.apply_wallpaper(wall),
                              source=src))
            return out
        if page == "cores":
            p = self.palettes[key]
            return [Change("cores", key, f"{p['title']} · {color_name(p['accent'])} · papel de parede "
                                         f"{dict(WALL_DESIGNS)[p['design']].lower()}",
                           lambda: self.desk.apply_palette(p), source=src)]
        if page == "temas":
            return [Change("tema", key, pretty(key), lambda: self._apply_theme(key), source=src)]
        if page == "icones":
            return [Change("icones", key, pretty(key), lambda: self._apply_icons(key), source=src)]
        if page == "cursores":
            return [Change("cursor", key, pretty(key), lambda: self._apply_cursor(key), source=src)]
        if page == "wallpaper":
            path = Path(key)
            return [Change("wallpaper", key, pretty(path.stem), lambda: self._apply_wall(path), source=src)]
        if page == "meus":
            st = next((s for s in load_styles() if s["name"] == key), None)
            out = []
            for comp, field, have, fn in (("tema", "gtk", self.themes, self._apply_theme),
                                          ("icones", "icons", self.icons, self._apply_icons),
                                          ("cursor", "cursor", self.cursors, self._apply_cursor)):
                if st and st.get(field) in have:
                    out.append(Change(comp, st[field], pretty(st[field]), lambda n=st[field], fn=fn: fn(n),
                                      source=src))
            if st and st.get("wallpaper") and Path(st["wallpaper"]).exists():
                w = Path(st["wallpaper"])
                out.append(Change("wallpaper", str(w), pretty(w.stem), lambda: self._apply_wall(w), source=src))
            return out
        return []

    def _need(self, name, installed):
        if name in installed:
            return []
        item = self.store.provider(name)
        return self.store.with_needs([item]) if item else []

    # aplicação de cada componente: confere na hora se ainda está instalado
    def _apply_theme(self, name):
        if name not in self.themes:
            raise NotInstalled(f"o tema {pretty(name)} não está instalado")
        self.desk.apply_gtk(self.themes[name])

    def _apply_icons(self, name):
        if name not in self.icons:
            raise NotInstalled(f"os ícones {pretty(name)} não estão instalados")
        self.desk.apply_icons(name)

    def _apply_cursor(self, name):
        if name not in self.cursors:
            raise NotInstalled(f"o cursor {pretty(name)} não está instalado")
        self.desk.apply_cursor(name)

    def _apply_wall(self, path):
        if not Path(path).exists():
            raise NotInstalled("a imagem não existe mais")
        self.desk.apply_wallpaper(path)

    def _selection_changed(self):
        self.refresh_marks()
        self._render_detail()
        self._refresh_chrome()

    def discard(self):
        """Joga fora as escolhas pendentes; os controles da barra e do vidro voltam ao do sistema."""
        self.sel.clear()
        self._reset_drafts()
        self.detail_for = (self.page, None)
        self._selection_changed()

    def apply(self):
        """Aplica só o que está marcado. Antes, guarda o visual atual para o “Desfazer”."""
        if self.applying or not self.sel.enabled():
            return
        self.applying = True
        self._refresh_chrome()
        need = self.sel.needs()

        def work(download_err, covered):
            try:
                self.desk.snapshot(PREVIOUS)
            except OSError as e:
                self.toast(f"Não deu para guardar o ponto de desfazer: {e}")
            ok, failed = self.sel.apply()

            def done():
                if covered:
                    self.curtain.uncover()
                self._applied(ok, failed, download_err)
                return False
            # com a cortina, espera o sistema redesenhar (Shell, Nautilus, ícones) antes de mostrar
            GLib.timeout_add(self.SETTLE_MS if covered else self.MIN_LOAD_MS, done)
            return False

        def run(download_err=None):
            # depois do download: só então se sabe a cor do tema novo
            if self.HEAVY & {c.key for c in self.sel.enabled()}:
                bg, accent = self._target_colors()
                self.curtain.cover(bg, lambda: work(download_err, True), accent)
            else:
                work(download_err, False)
            return False
        if need:
            self._download(need, "Baixando o que falta para aplicar…", run)
        else:
            GLib.timeout_add(80, run)

    def _target_colors(self):
        """Fundo e destaque do visual que está chegando (a cortina usa os dois, para a troca parecer
        contínua: tema claro → cortina clara)."""
        ch = self.sel.changes
        if "cores" in ch and ch["cores"].enabled and ch["cores"].value in getattr(self, "palettes", {}):
            p = self.palettes[ch["cores"].value]
            return p["bg"], p["accent"]
        if "tema" in ch and ch["tema"].enabled and ch["tema"].value in self.themes:
            bg, _base, _fg, acc = theme_palette(self.themes[ch["tema"].value])
            return bg.to_string(), acc.to_string()
        ok, color = self.get_style_context().lookup_color("window_bg_color")
        return (color.to_string() if ok else "#242424"), None

    def _follow_theme(self):
        """Apps GTK 4 só leem ~/.config/gtk-4.0/gtk.css ao abrir (por isso o Nautilus é reaberto).
        Depois de aplicar, o próprio app relê o arquivo para ficar com as cores do tema novo."""
        display = self.get_display()
        if self.theme_css:
            Gtk.StyleContext.remove_provider_for_display(display, self.theme_css)
        self.theme_css = Gtk.CssProvider()
        css = GTK4_CFG / "gtk.css"
        try:
            if css.exists():
                self.theme_css.load_from_path(str(css))
        except GLib.Error:
            return
        # acima do gtk.css que o GTK leu ao abrir (o do tema antigo)
        Gtk.StyleContext.add_provider_for_display(display, self.theme_css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    def _applied(self, ok, failed, download_err):
        self.applying = False
        if {"Tema", "Paleta de cores"} & set(ok):
            self._follow_theme()
        self._reset_drafts()
        if failed or download_err:
            lines = []
            if ok:
                lines.append("Aplicado: " + human_list(ok) + ".")
            for title, why in failed:
                lines.append(f"Não aplicado — {title}: {why}.")
            if download_err:
                lines.append(f"Download com problema: {download_err}.")
            lines.append("O que falhou continua na seleção para você tentar de novo.")
            inform(self, "Parte das alterações não foi aplicada" if ok else "Nada foi aplicado", "\n".join(lines))
        else:
            hint = " — apps abertos podem precisar ser reabertos" if set(ok) & {"Tema", "Paleta de cores",
                                                                                 "Cursor"} else ""
            self.toast(f"Aplicado: {human_list(ok)}{hint}")
        self.detail_for = (self.page, self.detail_for[1] if self.detail_for else None)
        self._selection_changed()

    def refresh_marks(self):
        cur = self.desk.current()
        in_use = {"temas": cur["gtk"], "icones": cur["icons"], "cursores": cur["cursor"],
                  "wallpaper": cur["wallpaper"], "estilos": self._style_in_use(), "meus": self._my_style_in_use(),
                  "barra": self._preset_in_use("barra"), "efeitos": self._preset_in_use("efeitos")}
        for name, grid in self.grids.items():
            grid.mark(in_use.get(name))
            if name in ("barra", "efeitos"):
                grid.select([k for k in [self._preset_picked(name)] if k and k != in_use[name]])
            else:
                grid.select(self.sel.selected(name))

    def _style_in_use(self):
        cur = self.desk.current()
        for title, p in getattr(self, "packs", {}).items():
            if (p["gtk"], p["icon"], p["cursor"]) == (cur["gtk"], cur["icons"], cur["cursor"]):
                return title
        return None

    def _my_style_in_use(self):
        cur = self.desk.current()
        return next((s["name"] for s in load_styles() if (s.get("gtk"), s.get("icons"), s.get("cursor")) ==
                     (cur["gtk"], cur["icons"], cur["cursor"])), None)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def _user_theme_loaded(self, result):
        self.user_theme = self.desk.ensure_user_theme() if result == "ok" else "carregar"
        if self.user_theme == "ok":
            self.notice_bar.set_reveal_child(False)
            self.toast("Pronto: o painel do sistema agora também muda de tema")
        else:
            self.notice.set_label("O tema das janelas já funciona. O painel do sistema muda de tema depois "
                                  "que você sair e entrar de novo na sessão.")
        self._render_detail()

    def _scan(self):
        self.themes = {t.name: t for t in list_gtk_themes()}
        self.icons = list_icon_themes()
        self.cursors = dict(list_cursor_themes())

    def _download(self, items, label, done):
        """Baixa e instala; done(None ou texto do erro) é chamado sempre, depois de reler o que existe."""
        if self.busy:
            self.toast("Espere o download atual terminar")
            return False
        self.busy = True
        self.prog.set_text(label)
        self.prog.set_fraction(0)
        self.prog_bar.set_reveal_child(True)
        self._refresh_chrome()

        def finished(err):
            self.busy = False
            self.prog_bar.set_reveal_child(False)
            self.rebuild()
            done(err)
            self._refresh_chrome()
        self.store.install(items, self.prog.set_fraction, finished)
        return True

    def _on_catalog(self, ok):
        self.store_loading = False
        self.rebuild("estilos", "explorar")
        if not ok and self.page == "explorar":
            self._render_detail()

    def _retry_catalog(self):
        self.store_loading = True
        self.rebuild("explorar")
        self.store.fetch(self._on_catalog)

    # ------------------------------------------------------------ cabeçalho das páginas
    def _page_header(self, holder, name, extra=None):
        box = Gtk.Box(spacing=12, margin_start=24, margin_end=24, margin_top=20)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        text.append(Gtk.Label(label=self.PAGE[name][1], xalign=0, wrap=True, css_classes=["title-1"]))
        text.append(Gtk.Label(label=self.PAGE[name][4], xalign=0, wrap=True, css_classes=["dim-label"]))
        box.append(text)
        if extra:
            extra.set_valign(Gtk.Align.CENTER)
            box.append(extra)
        holder.append(box)

    @staticmethod
    def _tone_filters(tags):
        """Filtro Escuros/Claros só quando existem os dois (filtro que sempre dá vazio só atrapalha)."""
        if {"escuro", "claro"} <= set(tags):
            return [("Todos", None), ("Escuros", "escuro"), ("Claros", "claro")]
        return None

    # ------------------------------------------------------------ estilos prontos
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

    def _pack_palette(self, p):
        colors = WALL_PALETTES[p["wall"]]
        if p["gtk"] in self.themes:
            return theme_palette(self.themes[p["gtk"]])
        return rgba(colors[0]), mix(rgba(colors[1]), rgba("#000000"), .2), rgba("#e6e6f0"), rgba(colors[2])

    def _build_estilos(self, holder):
        self._page_header(holder, "estilos")
        self.packs = {}
        for pack in PACKS:
            p = self._resolve(pack)
            if p:
                self.packs[p["title"]] = p
        tags = {"escuro" if is_dark(self._pack_palette(p)) else "claro" for p in self.packs.values()}
        empty = status_page("view-grid-symbolic", "Nenhum estilo disponível",
                            "Os estilos usam temas que vêm do catálogo do Explorar." +
                            (" Carregando o catálogo…" if self.store_loading else
                             " Confira sua conexão e tente de novo."),
                            None if self.store_loading else "Tentar de novo", self._retry_catalog)
        grid = self.grids["estilos"] = Grid(lambda k: self._pick("estilos", k), placeholder="Buscar estilos…",
                                            filters=self._tone_filters(tags), empty=empty)
        holder.append(grid)
        grid.set_loading(self.store_loading and not self.packs)
        for title, p in self.packs.items():
            pal = self._pack_palette(p)
            dark = is_dark(pal)
            sub = "Escuro" if dark else "Claro"
            if p["need"]:
                sub += f" · baixa {mb(sum(i['bytes'] for i in p['need']))}"
            colors = WALL_PALETTES[p["wall"]]
            grid.add(title, desktop_preview(WALLS / f"{p['wall']}.svg", pal,
                                            p["icon"] if p["icon"] in self.icons else None, 250, 140),
                     title, sub, tags={"escuro" if dark else "claro"}, colors=[colors[2], colors[3], pal[3]])

    def _detail_estilos(self, key):
        p = self.packs.get(key)
        if not p:
            return None
        rows = [("Tema", pretty(p["gtk"]), p["gtk"] in self.themes), ("Ícones", pretty(p["icon"] or "—"),
                                                                      not p["icon"] or p["icon"] in self.icons),
                ("Cursor", pretty(p["cursor"] or "—"), not p["cursor"] or p["cursor"] in self.cursors),
                ("Papel de parede", f"Gerado: {key}", True)]
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        for title, value, have in rows:
            line = Gtk.Box(spacing=8)
            line.append(Gtk.Label(label=title, xalign=0, hexpand=True, css_classes=["dim-label"]))
            line.append(Gtk.Label(label=value + ("" if have else " (será baixado)"), xalign=1, wrap=True))
            info.append(line)
        notes = []
        if p["need"]:
            notes.append(f"Ao aplicar, baixa {mb(sum(i['bytes'] for i in p['need']))} do catálogo do Explorar "
                         f"({human_list(i['title'] for i in p['need'])}).")
        notes += limitations("tema", self.env())
        return dict(preview=desktop_preview(WALLS / f"{p['wall']}.svg", self._pack_palette(p),
                                            p["icon"] if p["icon"] in self.icons else None, 340, 190),
                    caption=ILLUSTRATIVE + (" Os ícones do dock são do tema escolhido." if p["icon"] in self.icons
                                            else " Os ícones aparecem depois do download."),
                    title=key, subtitle="Escuro" if is_dark(self._pack_palette(p)) else "Claro",
                    extra=info, notes=notes, in_use=self._style_in_use() == key)

    # ------------------------------------------------------------ cores
    def _build_cores(self, holder):
        self._page_header(holder, "cores")
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=24, margin_end=24, margin_top=14)
        top.append(Gtk.Label(label="Sua cor favorita", xalign=0, css_classes=["heading"]))
        row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12, min_children_per_line=3,
                          column_spacing=4, row_spacing=4)
        self.color_buttons, first = {}, None
        for name, hexa in FAVORITE_COLORS:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            dot = drawing(28, 28, lambda cr, w, h, c=rgba(hexa): (
                _src(cr, c), cr.arc(w / 2, h / 2, min(w, h) / 2 - 1, 0, 2 * pi), cr.fill()))
            dot.set_halign(Gtk.Align.CENTER)  # não estica junto com o nome da cor
            box.append(dot)
            box.append(Gtk.Label(label=name, css_classes=["caption"]))
            btn = Gtk.ToggleButton(child=box, css_classes=["flat"])
            if first:
                btn.set_group(first)
            first = first or btn
            set_label(btn, f"Cor {name}")
            btn.connect("toggled", lambda b, c=hexa: b.get_active() and self._fill_palettes(c))
            self.color_buttons[hexa] = btn
            row.append(btn)
        if hasattr(Gtk, "ColorDialogButton"):
            other = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                          rgba=rgba(getattr(self, "fav_color", FAVORITE_COLORS[0][1])))
            other.connect("notify::rgba", lambda b, _p: self._fill_palettes(b.get_rgba().to_string()))
        else:
            other = Gtk.ColorButton(use_alpha=False, rgba=rgba(getattr(self, "fav_color", FAVORITE_COLORS[0][1])))
            other.connect("color-set", lambda b: self._fill_palettes(b.get_rgba().to_string()))
        other.set_valign(Gtk.Align.CENTER)
        other.set_tooltip_text("Escolher outra cor")
        set_label(other, "Escolher outra cor")
        self.color_btn = other
        obox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        obox.append(other)
        obox.append(Gtk.Label(label="Outra…", css_classes=["caption"]))
        row.append(obox)
        top.append(row)
        self.color_chosen = Gtk.Label(xalign=0, css_classes=["dim-label"])
        top.append(self.color_chosen)
        top.append(Gtk.Label(label="Desenho do papel de parede", xalign=0, css_classes=["heading"], margin_top=8))
        designs = Gtk.Box(spacing=8)
        group = Gtk.Box(css_classes=["linked"])
        first = None
        self.wall_design = getattr(self, "wall_design", WALL_DESIGNS[0][0])
        self.wall_seed = getattr(self, "wall_seed", 0)
        for key, label in WALL_DESIGNS:
            b = Gtk.ToggleButton(label=label, active=key == self.wall_design)
            if first:
                b.set_group(first)
            first = first or b
            b.connect("toggled", lambda b, k=key: b.get_active() and self._set_design(k, self.wall_seed))
            group.append(b)
        designs.append(group)
        vary = Gtk.Button(label="Variar desenho", tooltip_text="Outra versão do mesmo desenho, nas mesmas cores")
        vary.connect("clicked", lambda _b: self._set_design(self.wall_design, self.wall_seed + 1))
        designs.append(vary)
        top.append(designs)
        holder.append(top)
        self.grids["cores"] = Grid(lambda k: self._pick("cores", k), search=False)
        holder.append(self.grids["cores"])
        self._fill_palettes(getattr(self, "fav_color", FAVORITE_COLORS[0][1]))

    def _set_design(self, design, seed):
        self.wall_design, self.wall_seed = design, seed
        self._fill_palettes(self.fav_color, force=True)

    def _fill_palettes(self, color, force=False):
        grid = self.grids.get("cores")
        if not grid or grid.working:  # a grade ainda não existe enquanto a aba está sendo montada
            return
        if grid.cards and rgba(color).equal(rgba(self.fav_color)) and not force:
            return
        self.fav_color = color
        if not self.color_btn.get_rgba().equal(rgba(color)):  # o botão mostra a cor escolhida
            self.color_btn.set_rgba(rgba(color))
        # a bolinha marcada é sempre a cor escolhida; cor própria (“Outra…”) não marca nenhuma
        for hexa, btn in self.color_buttons.items():
            should = rgba(hexa).equal(rgba(color))
            if btn.get_active() != should:
                btn.set_active(should)
        self.color_chosen.set_label(f"Cor escolhida: {color_name(color)} ({rgba(color).to_string()})")
        grid.clear()
        self.palettes = {}
        for p in make_palettes(color):
            # a chave leva cor, desenho e semente: trocar a cor não deixa marcado um cartão que já é outro
            p.update(design=self.wall_design, seed=self.wall_seed)
            key = f"{p['key']}|{p['accent']}|{self.wall_design}|{self.wall_seed}"
            self.palettes[key] = p
            grid.add(key, self._palette_preview(p, 250, 140), p["title"],
                     f"{'Escura' if p['dark'] else 'Clara'} · {color_name(p['accent'])} e "
                     f"{color_name(p['accent2']).lower()}", colors=[p["accent"], p["accent2"], p["bg"]])
        self.refresh_marks()

    @staticmethod
    def _palette_preview(p, w, h):
        """O papel de parede de verdade (o SVG que será gravado) com uma janela nas cores da paleta."""
        base = picture(width_request=w, height_request=h, css_classes=["studio-thumb"], overflow=Gtk.Overflow.HIDDEN)
        try:
            base.set_paintable(svg_texture(design_svg(p["wall"], p["design"], p["seed"]), w, h))
        except GLib.Error:
            pass
        pal = tuple(map(rgba, (p["bg"], p["base"], p["fg"], p["accent"])))

        def paint(cr, ww, hh):
            s = (ww * .64) / 300
            cr.save()
            cr.translate(ww * .18, hh * .16)
            cr.scale(s, s)
            draw_window(cr, 0, 0, 300, hh * .64 / s, pal)
            cr.restore()
        overlay = Gtk.Overlay(child=base, halign=Gtk.Align.CENTER)
        overlay.add_overlay(drawing(w, h, paint))
        return overlay

    def _detail_cores(self, key):
        p = getattr(self, "palettes", {}).get(key)
        if not p:
            return None
        sw = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for label, c in (("Destaque", p["accent"]), ("Segundo tom", p["accent2"]), ("Fundo das janelas", p["bg"]),
                         ("Conteúdo", p["base"]), ("Texto", p["fg"])):
            line = Gtk.Box(spacing=8)
            line.append(dots([c], 16))
            line.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            line.append(Gtk.Label(label=f"{color_name(c)} · {c}", css_classes=["dim-label", "caption"]))
            sw.append(line)
        sw.append(Gtk.Label(label=p["tech"], xalign=0, wrap=True, css_classes=["dim-label", "caption"],
                            margin_top=4))
        design = dict(WALL_DESIGNS)[p["design"]]
        return dict(preview=self._palette_preview(p, 340, 190),
                    caption=f"O papel de parede é o de verdade ({design.lower()}, variação {p['seed'] + 1}); "
                            "a janela é uma ilustração com as cores da paleta.",
                    title=f"Paleta {p['title']}",
                    subtitle=f"{'Escura' if p['dark'] else 'Clara'} · a partir de {color_name(self.fav_color)}",
                    extra=sw, notes=limitations("cores", self.env()))

    # ------------------------------------------------------------ temas, ícones, cursores, wallpaper
    def _go_explore(self, kind):
        self.go("explorar")
        grid = self.grids.get("explorar")
        if grid:
            grid.filter_kind(kind)

    def _build_temas(self, holder):
        self._page_header(holder, "temas")
        pals = {n: theme_palette(t) for n, t in self.themes.items()}
        tags = {"escuro" if is_dark(p) else "claro" for p in pals.values()}
        grid = self.grids["temas"] = Grid(
            lambda k: self._pick("temas", k), placeholder="Buscar temas…", filters=self._tone_filters(tags),
            empty=status_page("preferences-desktop-appearance-symbolic", "Nenhum tema instalado",
                              "Baixe temas no Explorar.", "Explorar temas", lambda: self._go_explore("gtk")))
        holder.append(grid)
        grid.set_loading(True)

        def add(theme):
            dark = is_dark(pals[theme.name])
            grid.add(theme.name, window_preview(pals[theme.name], 230, 130), pretty(theme.name),
                     "Escuro" if dark else "Claro", tags={"escuro" if dark else "claro"})
        add_batched(self.themes.values(), add, lambda: (grid.set_loading(False), self.refresh_marks()))

    def _detail_temas(self, key):
        theme = self.themes.get(key)
        if not theme:
            return None
        parts = [n for n, d in (("GTK 3", "gtk-3.0"), ("GTK 4", "gtk-4.0"), ("painel do GNOME", "gnome-shell"))
                 if (theme.path / d).is_dir()]
        notes = limitations("tema", self.env())
        if "painel do GNOME" not in parts:
            notes.insert(0, "Este tema não traz estilo para o painel do GNOME: o painel fica como está.")
        return dict(preview=window_preview(theme_palette(theme), 340, 200),
                    caption="Ilustração com as cores lidas do tema; botões e detalhes reais variam por app.",
                    title=pretty(key), subtitle="Inclui: " + human_list(parts), notes=notes,
                    in_use=self.desk.current()["gtk"] == key)

    def _build_icones(self, holder):
        self._page_header(holder, "icones")
        grid = self.grids["icones"] = Grid(
            lambda k: self._pick("icones", k), placeholder="Buscar ícones…",
            empty=status_page("folder-pictures-symbolic", "Nenhum tema de ícones instalado",
                              "Baixe ícones no Explorar.", "Explorar ícones", lambda: self._go_explore("icons")))
        holder.append(grid)
        grid.set_loading(True)
        add_batched(self.icons, lambda n: grid.add(n, icon_preview(n), pretty(n)),
                    lambda: (grid.set_loading(False), self.refresh_marks()))

    def _detail_icones(self, key):
        imgs = [Gtk.Image(paintable=p, pixel_size=48) for p in icon_paintables(key, 48)]
        return dict(preview=labeled_samples(imgs, ICON_LABELS, 48),
                    caption="Ícones de verdade do tema, sempre os mesmos aplicativos para comparar.",
                    title=pretty(key), subtitle="Tema de ícones", notes=limitations("icones", self.env()),
                    in_use=self.desk.current()["icons"] == key)

    def _build_cursores(self, holder):
        self._page_header(holder, "cursores")
        grid = self.grids["cursores"] = Grid(
            lambda k: self._pick("cursores", k), placeholder="Buscar cursores…",
            empty=status_page("input-mouse-symbolic", "Nenhum cursor instalado", "Baixe cursores no Explorar.",
                              "Explorar cursores", lambda: self._go_explore("cursors")))
        holder.append(grid)
        grid.set_loading(True)
        add_batched(self.cursors.items(), lambda kv: grid.add(kv[0], cursor_preview(kv[1]), pretty(kv[0]),
                                                                "Ponteiro, texto, link e carregando"),
                    lambda: (grid.set_loading(False), self.refresh_marks()))

    def _detail_cursores(self, key):
        path = self.cursors.get(key)
        if not path:
            return None
        imgs = [Gtk.Image(paintable=t, pixel_size=48) if t else None for t in cursor_textures(path, 48, 4)]
        return dict(preview=labeled_samples(imgs, CURSOR_LABELS[:4], 48),
                    caption="Imagens tiradas dos arquivos do cursor (a primeira imagem das animadas).",
                    title=pretty(key), subtitle="Cursor", notes=limitations("cursor", self.env()),
                    in_use=self.desk.current()["cursor"] == key)

    def _build_wallpaper(self, holder):
        choose = Gtk.Button(label="Escolher imagem…")
        choose.connect("clicked", self._choose_wall)
        self._page_header(holder, "wallpaper", choose)
        grid = self.grids["wallpaper"] = Grid(
            lambda k: self._pick("wallpaper", k), placeholder="Buscar papéis de parede…",
            empty=status_page("preferences-desktop-wallpaper-symbolic", "Nenhuma imagem encontrada",
                              "Escolha uma imagem do seu computador.", "Escolher imagem…",
                              lambda: self._choose_wall(None)))
        holder.append(grid)
        grid.set_loading(True)

        def add(path):
            pic = picture("contain", width_request=230, height_request=130, css_classes=["studio-thumb"],
                          overflow=Gtk.Overflow.HIDDEN)
            grid.add(str(path), pic, pretty(path.stem))
            load_async(pic, path, 230, 130)
        add_batched(list_wallpapers(), add, lambda: (grid.set_loading(False), self.refresh_marks()))

    def _detail_wallpaper(self, key):
        path = Path(key)
        pic = picture("contain", width_request=340, height_request=200, css_classes=["studio-thumb"],
                      overflow=Gtk.Overflow.HIDDEN)
        load_async(pic, path, 340, 200)
        zoom = Gtk.Button(label="Ver em tamanho maior", halign=Gtk.Align.START)
        zoom.connect("clicked", lambda _b: self._zoom(path))
        size = ""
        try:
            fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(str(path))
            size = f"{w} × {h} pixels" if fmt else ""
        except (TypeError, GLib.Error):
            pass
        return dict(preview=pic, caption="A imagem inteira, sem cortes (na tela ela preenche o fundo).",
                    title=pretty(path.stem), subtitle=size or str(path.parent), extra=zoom,
                    notes=limitations("wallpaper", self.env()), in_use=self.desk.current()["wallpaper"] == key)

    def _zoom(self, path):
        win = Gtk.Window(transient_for=self, modal=True, title=pretty(Path(path).stem), default_width=1000,
                         default_height=620)
        pic = picture("contain", file=Gio.File.new_for_path(str(path)))
        win.set_child(pic)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: k == Gdk.KEY_Escape and (win.close() or True))
        win.add_controller(keys)
        win.present()

    def _choose_wall(self, _btn):
        filt = Gtk.FileFilter(name="Imagens")
        filt.add_pixbuf_formats()
        pick = lambda path: self._pick("wallpaper", path)  # noqa: E731
        if hasattr(Gtk, "FileDialog"):
            filters = Gio.ListStore.new(Gtk.FileFilter)
            filters.append(filt)
            dialog = Gtk.FileDialog(title="Escolher papel de parede", filters=filters)

            def done(d, res):
                try:
                    f = d.open_finish(res)
                except GLib.Error:
                    return
                pick(f.get_path())
            dialog.open(self, None, done)
        else:
            self._chooser = Gtk.FileChooserNative(title="Escolher papel de parede", transient_for=self,
                                                  action=Gtk.FileChooserAction.OPEN)
            self._chooser.add_filter(filt)
            self._chooser.connect("response", lambda d, r: r == Gtk.ResponseType.ACCEPT and
                                  pick(d.get_file().get_path()))
            self._chooser.show()

    # ------------------------------------------------------------ barra e dock (rascunho até aplicar)
    def _reset_drafts(self):
        if self.desk.bar:
            self.bar_draft = self.desk.bar_read()
            if hasattr(self, "bar_pos"):
                self._bar_sync()
        if getattr(self, "blur", None) and self.blur.root:
            self.fx_draft = self.blur.read()
            if hasattr(self, "fx_radius"):
                self._fx_sync()

    def _preset_in_use(self, page):
        if page == "barra" and self.desk.bar:
            cur = self.desk.bar_read()
            return next((n for n, p in BAR_PRESETS.items() if same(cur, p)), None)
        if page == "efeitos" and getattr(self, "blur", None) and self.blur.root:
            cur = self.blur.read()
            return next((n for n, p in BLUR_PRESETS.items() if self._same_fx(cur, p)), None)
        return None

    def _preset_picked(self, page):
        if page == "barra" and hasattr(self, "bar_draft"):
            return next((n for n, p in BAR_PRESETS.items() if same(self.bar_draft, p)), None)
        if page == "efeitos" and hasattr(self, "fx_draft"):
            return next((n for n, p in BLUR_PRESETS.items() if self._same_fx(self.fx_draft, p)), None)
        return None

    @staticmethod
    def _same_fx(cur, p):
        if not p["parts"]:  # "Sem vidro": tudo desligado, a intensidade não importa
            return not cur["parts"] and cur["windows"] == "nenhuma"
        return set(cur["parts"]) >= set(p["parts"]) - {"panel"} and cur["windows"] == p["windows"] and \
            abs(cur["radius"] - p["radius"]) < 1 and abs(cur["brightness"] - p["brightness"]) < .02

    def _build_barra(self, holder):
        self._page_header(holder, "barra")
        if not self.desk.bar:
            holder.append(status_page("view-continuous-symbolic", "Barra de tarefas não encontrada",
                                      "Esta seção funciona com a barra do Zorin OS e com a extensão Dash to Panel. "
                                      "Nenhuma das duas está instalada neste sistema."))
            return
        self.bar_draft = getattr(self, "bar_draft", None) or self.desk.bar_read()
        page = Adw.PreferencesPage(vexpand=True)
        holder.append(page)
        presets = Adw.PreferencesGroup(title="Modelos", description="Escolha um ponto de partida e ajuste abaixo")
        grid = self.grids["barra"] = Grid(self._bar_preset, search=False, scroll=False)
        for name, p in BAR_PRESETS.items():
            grid.add(name, drawing(200, 100, lambda cr, w, h, p=p: draw_bar(cr, w, h, dict(self.bar_draft, **p))),
                     name)
        presets.add(grid)
        page.add(presets)

        fine = Adw.PreferencesGroup(title="Ajustes", description="Valem para todos os monitores conectados")
        self._syncing = False
        self.bar_pos = Adw.ComboRow(title="Posição", model=Gtk.StringList.new([t for _k, t in BAR_POSITIONS]))
        self.bar_pos.connect("notify::selected", lambda r, _p: self._bar_edit(
            position=BAR_POSITIONS[r.get_selected()][0]))
        compact_row, self.bar_compact = switch_row(
            "Barra compacta", "Só do tamanho dos ícones, centralizada. Desligada, ocupa a borda inteira")
        self.bar_compact.connect("notify::active", lambda s, _p: self._bar_edit(length=-1 if s.get_active() else 100))
        size_row, self.bar_size = spin_row("Altura", 24, 96, "Espessura da barra", "px")
        self.bar_size.connect("value-changed", lambda s: self._bar_edit(size=s.get_value_as_int()))
        margin_row, self.bar_margin = spin_row("Distância da borda", 0, 32, "Espaço entre a barra e a borda da tela",
                                               "px")
        self.bar_margin.connect("value-changed", lambda s: self._bar_edit(margin=s.get_value_as_int()))
        radius_row, self.bar_radius = spin_row("Arredondamento dos cantos", 0, 25, "Em passos de 5 px", "px", 5)
        self.bar_radius.connect("value-changed", lambda s: self._bar_edit(radius=round(s.get_value() / 5)))
        custom_row, self.bar_custom = switch_row("Transparência própria", "Desligada, segue o tema")
        self.bar_custom.connect("notify::active", lambda s, _p: self._bar_edit(custom=s.get_active()))
        opacity_row, self.bar_opacity = scale_row("Opacidade", 0, 100, "100 % é totalmente opaca")
        self.bar_opacity.connect("value-changed", lambda s: self._bar_edit(opacity=round(s.get_value() / 100, 2)))
        self.bar_custom.bind_property("active", opacity_row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        hide_row, self.bar_hide = switch_row("Esconder automaticamente", "Some quando uma janela encosta nela")
        self.bar_hide.connect("notify::active", lambda s, _p: self._bar_edit(hide=s.get_active()))
        for row in (self.bar_pos, compact_row, size_row, margin_row, radius_row, custom_row, opacity_row, hide_row):
            fine.add(row)
        page.add(fine)
        self._bar_sync()

    def _bar_edit(self, **values):
        if self._syncing:
            return
        self.bar_draft.update(values)
        self._bar_pending()

    def _bar_preset(self, name):
        self.bar_draft.update(BAR_PRESETS[name])
        self._bar_sync()
        self._bar_pending()

    def _bar_pending(self):
        """O rascunho vira uma alteração pendente; igual ao sistema, sai da seleção."""
        if same(self.desk.bar_read(), self.bar_draft):
            self.sel.remove("barra")
        else:
            d = dict(self.bar_draft)
            label = next((n for n, p in BAR_PRESETS.items() if same(d, p)), "Ajustes próprios")
            self.sel.set(Change("barra", d, f"{label} · {d['size']} px", lambda: self.desk.bar_write(d),
                                source=("barra", None)))
        self.detail_for = ("barra", None)
        if self.grids.get("barra"):
            self.grids["barra"].flow.queue_draw()
            for btn in self.grids["barra"].cards.values():
                btn.queue_draw()
        self._selection_changed()

    def _bar_sync(self):
        d = self.bar_draft
        self._syncing = True
        keys = [k for k, _t in BAR_POSITIONS]
        self.bar_pos.set_selected(keys.index(d["position"]) if d["position"] in keys else 0)
        self.bar_compact.set_active(d["length"] == -1)
        self.bar_size.set_value(d["size"])
        self.bar_margin.set_value(d["margin"])
        self.bar_radius.set_value(d["radius"] * 5)
        self.bar_custom.set_active(d["custom"])
        self.bar_opacity.set_value(d["opacity"] * 100)
        self.bar_hide.set_active(d["hide"])
        self._syncing = False

    def _detail_barra(self, _key):
        if not self.desk.bar:
            return None
        d = self.bar_draft
        pos = dict(BAR_POSITIONS).get(d["position"], d["position"]).lower()
        return dict(preview=drawing(340, 190, lambda cr, w, h: draw_bar(cr, w, h, self.bar_draft)),
                    caption="Ilustração: mostra a posição e o formato, não a sua barra de verdade.",
                    title="Barra " + ("compacta" if d["length"] == -1 else "inteira"),
                    subtitle=f"{pos} · {d['size']} px de altura · {d['margin']} px da borda · cantos de "
                             f"{d['radius'] * 5} px",
                    notes=limitations("barra", self.env()), in_use=same(self.desk.bar_read(), d))

    # ------------------------------------------------------------ efeitos (rascunho até aplicar)
    def _is_zorin(self):
        return self.desk.bar is not None and "zorin" in self.desk.bar.props.schema_id

    def _build_efeitos(self, holder):
        self._page_header(holder, "efeitos")
        blur = self.blur = Blur()
        if not Blur.installed() or not blur.root:
            status = status_page("weather-fog-symbolic", "Efeito de vidro não instalado",
                                 "Deixa menus, telas e janelas com aparência de vidro fosco. Usa a extensão "
                                 "gratuita Blur my Shell, do extensions.gnome.org: o GNOME vai perguntar se você "
                                 "quer instalar. Instalar não liga nada sozinho.")
            btn = Gtk.Button(label="Instalar efeito de vidro", css_classes=["suggested-action", "pill"],
                             halign=Gtk.Align.CENTER)
            btn.connect("clicked", self._install_blur)
            status.set_child(btn)
            holder.append(status)
            return
        enable_extension(BLUR_UUID)
        zorin = self._is_zorin()
        blur.adapt(zorin)
        self.fx_draft = getattr(self, "fx_draft", None) or blur.read()
        page = Adw.PreferencesPage(vexpand=True)
        holder.append(page)
        if not animations_enabled():
            note = Adw.PreferencesGroup()
            note.add(Adw.ActionRow(title="Animações do sistema desligadas",
                                   subtitle="O vidro é estático e continua disponível. O Adon Studio também não "
                                            "anima nada enquanto essa preferência estiver ligada."))
            page.add(note)

        presets = Adw.PreferencesGroup(title="Modelos")
        grid = self.grids["efeitos"] = Grid(self._fx_preset, search=False, scroll=False)
        for name, p in BLUR_PRESETS.items():
            grid.add(name, drawing(200, 110, lambda cr, w, h, p=p: draw_glass(cr, w, h, p)), name)
        presets.add(grid)
        page.add(presets)

        self._fx_syncing = False
        where = Adw.PreferencesGroup(title="Onde aplicar", description="Cada opção vale só para o lugar descrito")
        self.fx_switch = {}
        explain = {"appfolder": "Fundo das pastas na tela de aplicativos",
                   "lockscreen": "Fundo da tela que pede a senha"}
        for part, title, sub in BLUR_PARTS:
            if part not in blur.parts:
                continue
            sub = sub or explain.get(part)
            if part == "panel" and zorin:
                sub = "No Zorin a barra de tarefas é outra; o vidro nela ainda não é suportado"
            row, sw = switch_row(title, sub)
            row.set_sensitive(not (part == "panel" and zorin))
            sw.connect("notify::active", lambda s, _p, part=part: self._fx_edit(part=(part, s.get_active())))
            self.fx_switch[part] = sw
            where.add(row)
        self.fx_windows = Adw.ComboRow(title="Vidro nas janelas",
                                       subtitle="Deixa ver o fundo através das janelas. "
                                                "“Todas as janelas” pesa na placa de vídeo",
                                       model=Gtk.StringList.new([label for _m, label in WINDOW_MODES]))
        self.fx_windows.connect("notify::selected", lambda r, _p: self._fx_edit(
            windows=WINDOW_MODES[r.get_selected()][0]))
        where.add(self.fx_windows)
        page.add(where)

        strength = Adw.PreferencesGroup(title="Intensidade")
        row, self.fx_radius = scale_row("Desfoque", 0, 100, "Quanto o fundo fica borrado")
        strength.add(row)
        row, self.fx_bright = scale_row("Brilho", 30, 100, "Quanto mais baixo, mais escuro o vidro")
        strength.add(row)
        row, self.fx_opacity = scale_row("Opacidade das janelas", 50, 100,
                                         "Só vale com o vidro nas janelas ligado")
        strength.add(row)
        page.add(strength)
        self.fx_radius.connect("value-changed", lambda s: self._fx_edit(radius=round(s.get_value() * .6)))
        self.fx_bright.connect("value-changed", lambda s: self._fx_edit(brightness=round(s.get_value() / 100, 2)))
        self.fx_opacity.connect("value-changed", lambda s: self._fx_edit(opacity=int(s.get_value() * 2.55)))
        self._fx_sync()

    def _fx_edit(self, part=None, **values):
        if self._fx_syncing:
            return
        d = self.fx_draft
        if part:
            name, on = part
            d["parts"] = [p for p in d["parts"] if p != name] + ([name] if on else [])
        d.update(values)
        self.fx_opacity.row.set_sensitive(d["windows"] != "nenhuma")
        self._fx_pending()

    def _fx_preset(self, name):
        p = BLUR_PRESETS[name]
        self.fx_draft.update(parts=list(p["parts"]), windows=p["windows"], radius=p["radius"],
                             brightness=p["brightness"])
        if self._is_zorin():
            self.fx_draft["parts"] = [x for x in self.fx_draft["parts"] if x != "panel"]
        self._fx_sync()
        self._fx_pending()

    def _fx_pending(self):
        cur, d = self.blur.read(), dict(self.fx_draft)
        if set(cur["parts"]) == set(d["parts"]) and same(cur, {k: v for k, v in d.items() if k != "parts"}):
            self.sel.remove("efeitos")
        else:
            label = next((n for n, p in BLUR_PRESETS.items() if self._same_fx(d, p)), "Ajustes próprios")
            self.sel.set(Change("efeitos", d, label, lambda: self.blur.write(d, self._is_zorin()),
                                source=("efeitos", None)))
        self.detail_for = ("efeitos", None)
        self._selection_changed()

    def _fx_sync(self):
        d = self.fx_draft
        self._fx_syncing = True
        for part, sw in self.fx_switch.items():
            sw.set_active(part in d["parts"])
        self.fx_windows.set_selected(next(i for i, (m, _l) in enumerate(WINDOW_MODES) if m == d["windows"]))
        self.fx_radius.set_value(d["radius"] / .6)
        self.fx_bright.set_value(d["brightness"] * 100)
        self.fx_opacity.set_value(d["opacity"] / 2.55)
        self.fx_opacity.row.set_sensitive(d["windows"] != "nenhuma")
        self._fx_syncing = False

    def _detail_efeitos(self, _key):
        if not getattr(self, "fx_draft", None):
            return None
        d = self.fx_draft
        names = [t for p, t, _s in BLUR_PARTS if p in d["parts"]]
        return dict(preview=drawing(340, 190, lambda cr, w, h: draw_glass(cr, w, h, self.fx_draft)),
                    caption="Ilustração: o vidro real depende do que está atrás de cada parte.",
                    title="Vidro " + ("ligado" if names or d["windows"] != "nenhuma" else "desligado"),
                    subtitle=(human_list(names) if names else "Nenhuma parte do sistema") + " · janelas: " +
                    dict(WINDOW_MODES)[d["windows"]].lower(),
                    notes=limitations("efeitos", self.env()), in_use=self._fx_pending_is_empty())

    def _fx_pending_is_empty(self):
        return "efeitos" not in self.sel.changes

    def _install_blur(self, btn):
        btn.set_sensitive(False)
        btn.set_label("Esperando você confirmar…")

        def done(result):
            if result == "ok":
                Blur().adapt(self._is_zorin())
                self.rebuild("efeitos")
                self.toast("Efeito de vidro instalado. Escolha um modelo e clique em Aplicar")
            else:
                btn.set_sensitive(True)
                btn.set_label("Instalar efeito de vidro")
                self.toast("Instalação cancelada" if result == "cancelled" else f"Não deu para instalar: {result}")
        Blur.install(done)

    # ------------------------------------------------------------ explorar (instalar ≠ aplicar)
    def _build_explorar(self, holder):
        if not self.store.items:
            self._page_header(holder, "explorar")
            if self.store_loading:
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, vexpand=True,
                              valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
                sp = Spinner(32)
                sp.start()
                box.append(sp)
                box.append(Gtk.Label(label="Carregando o catálogo…", css_classes=["dim-label"]))
                holder.append(box)
            else:
                holder.append(status_page("network-offline-symbolic", "Não deu para carregar o catálogo",
                                          "Confira sua conexão com a internet. Os temas que você já tem continuam "
                                          "nas outras seções.", "Tentar de novo", self._retry_catalog))
            return
        self.install_btn = Gtk.Button(label="Instalar marcados", css_classes=["suggested-action"], sensitive=False)
        self.install_btn.connect("clicked", lambda _b: self._install_marked())
        self._page_header(holder, "explorar", self.install_btn)
        if self.store.offline:
            holder.append(Gtk.Label(label="Sem conexão: mostrando a última cópia do catálogo. Downloads podem "
                                          "falhar.", wrap=True, xalign=0, css_classes=["card", "studio-notice"]))
        filters = [("Tudo", None), ("Não instalados", "novo"), ("Instalados", "instalado")]
        kinds = Gtk.Box(css_classes=["linked"])
        first = None
        for label, kind in (("Todos os tipos", None), ("Temas", "gtk"), ("Ícones", "icons"), ("Cursores", "cursors")):
            b = Gtk.ToggleButton(label=label, active=first is None)
            if first:
                b.set_group(first)
            first = first or b
            b.connect("toggled", lambda b, k=kind: b.get_active() and self.grids["explorar"].filter_kind(k))
            kinds.append(b)
        grid = self.grids["explorar"] = Grid(lambda k: self._show_item(k), extra=kinds, filters=filters,
                                             placeholder="Buscar no catálogo…")
        holder.append(grid)
        self.store_status, self.marked = {}, {}
        order = {"gtk": 0, "icons": 1, "cursors": 2}
        for item in sorted(self.store.items.values(), key=lambda i: (order[i["kind"]], i["title"].lower())):
            pic = picture(width_request=230, height_request=130, css_classes=["studio-thumb"],
                          overflow=Gtk.Overflow.HIDDEN)
            # marcador até a miniatura chegar (ou se ela não vier)
            thumb = Gtk.Overlay(child=Gtk.Image(icon_name=KIND_ICON[item["kind"]], pixel_size=32,
                                                width_request=230, height_request=130, css_classes=["dim-label"]))
            thumb.add_overlay(pic)
            pill = Gtk.Label(halign=Gtk.Align.START, margin_top=2, css_classes=["studio-pill"])
            mark = Gtk.CheckButton(label="Marcar para instalar", margin_start=6)
            mark.connect("toggled", lambda _b: self._marked_changed())
            grid.add(item["id"], thumb, item["title"],
                     f"{KIND_LABEL[item['kind']]} · {mb(item['bytes'])} · {item['author']}", extra=pill,
                     kind=item["kind"], below=mark)
            self.store_status[item["id"]] = pill
            self.marked[item["id"]] = mark
            self.store.preview(item, lambda f, pic=pic: pic.set_paintable(thumb_texture(f, 230, 130)))
        self._store_refresh()

    def _store_refresh(self):
        grid = self.grids.get("explorar")
        for item_id, pill in getattr(self, "store_status", {}).items():
            ok = self.store.is_installed(self.store.items[item_id])
            err = self.store.failed.get(item_id)
            pill.set_label("Instalado ✓" if ok else "Falhou ✗" if err else "Não instalado")
            for cls, on in (("ok", ok), ("erro", bool(err) and not ok)):
                (pill.add_css_class if on else pill.remove_css_class)(cls)
            mark = self.marked[item_id]
            mark.set_visible(not ok)
            if ok:
                mark.set_active(False)
            if grid and item_id in grid.cards:
                grid.cards[item_id].tags = {"instalado" if ok else "novo"}
        if grid:
            grid.flow.invalidate_filter()
        self._marked_changed()

    def _marked_changed(self):
        ids = [i for i, m in getattr(self, "marked", {}).items() if m.get_active()]
        if hasattr(self, "install_btn"):
            size = sum(self.store.items[i]["bytes"] for i in ids)
            self.install_btn.set_label(f"Instalar marcados ({len(ids)} · {mb(size)})" if ids else "Instalar marcados")
            self.install_btn.set_sensitive(bool(ids) and not self.busy)

    def _show_item(self, item_id):
        self.detail_for = ("explorar", item_id)
        self._render_detail()
        self._open_detail()

    def _install(self, ids):
        items = self.store.with_needs([self.store.items[i] for i in ids])
        grid = self.grids.get("explorar")
        if grid and len(ids) == 1:
            grid.start(ids[0])
        label = f"Baixando {items[0]['title']}…" if len(items) == 1 else f"Baixando {len(items)} pacotes…"

        def done(err):
            self._store_refresh()
            grid = self.grids.get("explorar")
            for i in ids:
                if grid:
                    grid.finish(i, ok=i not in self.store.failed)
            ok = [self.store.items[i]["title"] for i in ids if i not in self.store.failed]
            if err:
                inform(self, "Alguns pacotes não foram instalados" if ok else "Nada foi instalado",
                       (f"Instalado: {human_list(ok)}.\n" if ok else "") + f"Falhou — {err}.")
            else:
                self.toast(f"Instalado: {human_list(ok)}. Para usar, escolha na seção e clique em Aplicar")
            self._render_detail()
        if not self._download(items, label, done) and grid and len(ids) == 1:
            grid.finish(ids[0], ok=False)
        self._render_detail()

    def _install_marked(self):
        ids = [i for i, m in self.marked.items() if m.get_active()]
        if ids:
            self._install(ids)

    def _remove_item(self, item):
        def go():
            self.store.remove(item)
            self.rebuild()
            self._store_refresh()
            self.toast(f"{item['title']} removido")
        confirm(self, f"Remover {item['title']}?",
                f"Ele some da seção {self.PAGE[self.KIND_PAGE[item['kind']]][1]}. Se estiver em uso, o sistema "
                "volta para o padrão desse item. Dá para instalar de novo quando quiser.", "Remover", go,
                destructive=True)

    def _use_item(self, item):
        page = self.KIND_PAGE[item["kind"]]
        self.go(page)
        grid = self.grids.get(page)
        if grid:
            grid.set_query(item["title"].split()[0].lower())

    def _detail_explorar(self, key):
        item = self.store.items.get(key)
        if not item:
            return None
        pic = picture("contain", width_request=340, height_request=190, css_classes=["studio-thumb"],
                      overflow=Gtk.Overflow.HIDDEN)
        self.store.preview(item, lambda f: pic.set_paintable(thumb_texture(f, 340, 190)))
        installed = self.store.is_installed(item)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for label, value in (("Autor", item.get("author", "—")), ("Licença", item.get("license", "—")),
                             ("Tamanho", mb(item["bytes"])), ("Instala em", str(STORE_DIRS[item["kind"]]).replace(
                                 str(HOME), "~"))):
            line = Gtk.Box(spacing=8)
            line.append(Gtk.Label(label=label, xalign=0, hexpand=True, css_classes=["dim-label"]))
            line.append(Gtk.Label(label=value, xalign=1, wrap=True, selectable=True))
            box.append(line)
        if item.get("source"):
            box.append(Gtk.LinkButton(uri=item["source"], label="Ver a origem do pacote", halign=Gtk.Align.START))
        actions = Gtk.Box(spacing=8, margin_top=6)
        if installed:
            use = Gtk.Button(label=f"Escolher em {self.PAGE[self.KIND_PAGE[item['kind']]][1]}",
                             css_classes=["suggested-action"])
            use.connect("clicked", lambda _b: self._use_item(item))
            actions.append(use)
            rm = Gtk.Button(label="Remover", css_classes=["destructive-action"])
            rm.connect("clicked", lambda _b: self._remove_item(item))
            actions.append(rm)
        else:
            inst = Gtk.Button(label="Baixando…" if self.busy else f"Instalar ({mb(item['bytes'])})",
                              css_classes=["suggested-action"], sensitive=not self.busy)
            inst.connect("clicked", lambda _b: self._install([key]))
            actions.append(inst)
        box.append(actions)
        notes = ["Instalar só baixa para a sua pasta pessoal. Nada muda no visual até você escolher o item e "
                 "clicar em Aplicar."]
        err = self.store.failed.get(key)
        return dict(preview=pic, caption="Imagem gerada a partir do pacote.", title=item["title"],
                    subtitle=f"{KIND_LABEL[item['kind']]} · {'instalado' if installed else 'não instalado'}",
                    extra=box, notes=notes, error=f"A última tentativa falhou: {err}" if err and not installed
                    else None, state="Instalado" if installed else "Não instalado")

    # ------------------------------------------------------------ meus estilos
    def _build_meus(self, holder):
        save = Gtk.Button(label="Salvar visual atual…")
        save.connect("clicked", lambda _b: self._save_current())
        self._page_header(holder, "meus", save)
        grid = self.grids["meus"] = Grid(
            lambda k: self._pick("meus", k), placeholder="Buscar nos meus estilos…",
            empty=status_page("user-bookmarks-symbolic", "Nenhum estilo salvo",
                              "Aplique um visual de que você goste e salve aqui para voltar a ele depois.",
                              "Salvar visual atual…", self._save_current))
        holder.append(grid)
        for st in load_styles():
            grid.add(st["name"], self._style_preview(st, 250, 140), st["name"],
                     " · ".join(pretty(st[k]) for k in ("gtk", "icons") if st.get(k)))
        grid.set_loading(False)

    def _style_preview(self, st, w, h):
        theme = self.themes.get(st.get("gtk"))
        pal = theme_palette(theme) if theme else tuple(map(rgba, ("#2b2b2b", "#242424", "#eeeeee", "#3584e4")))
        return desktop_preview(st.get("wallpaper"), pal, st.get("icons") if st.get("icons") in self.icons else None,
                               w, h)

    def _save_current(self):
        cur = self.desk.current()

        def save(name):
            save_style(dict(name=name, gtk=cur["gtk"], icons=cur["icons"], cursor=cur["cursor"],
                            wallpaper=cur["wallpaper"]))
            self.rebuild("meus")
            self.toast(f"Estilo “{name}” salvo")
        body = (f"Guarda o tema {pretty(cur['gtk'])}, os ícones, o cursor e o papel de parede que estão em uso. "
                "A barra e o vidro não entram no estilo.")
        if cur["icons"].startswith(FOLDER_PREFIX):
            body += " As cores de uma paleta da seção Cores não são guardadas."
        ask_text(self, "Salvar visual atual", body, "Salvar", self._current_name(), save)

    def _detail_meus(self, key):
        st = next((s for s in load_styles() if s["name"] == key), None)
        if not st:
            return None
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        missing = []
        for label, field, have in (("Tema", "gtk", self.themes), ("Ícones", "icons", self.icons),
                                   ("Cursor", "cursor", self.cursors)):
            line = Gtk.Box(spacing=8)
            line.append(Gtk.Label(label=label, xalign=0, hexpand=True, css_classes=["dim-label"]))
            ok = st.get(field) in have
            line.append(Gtk.Label(label=pretty(st.get(field) or "—") + ("" if ok else " (não instalado)"),
                                  xalign=1, wrap=True))
            info.append(line)
            if not ok:
                missing.append(label)
        if not (st.get("wallpaper") and Path(st["wallpaper"]).exists()):
            missing.append("Papel de parede")
        rm = Gtk.Button(label="Excluir estilo", css_classes=["destructive-action"], halign=Gtk.Align.START,
                        margin_top=6)
        rm.connect("clicked", lambda _b: confirm(self, f"Excluir “{key}”?", "Só apaga o estilo salvo; não muda "
                                                 "nada no visual nem desinstala temas.", "Excluir",
                                                 lambda: self._delete_style(key), destructive=True))
        info.append(rm)
        notes = [f"Não está mais neste computador e fica de fora: {human_list(missing)}."] if missing else []
        return dict(preview=self._style_preview(st, 340, 190), caption=ILLUSTRATIVE, title=key,
                    subtitle="Estilo salvo", extra=info, notes=notes, in_use=self._my_style_in_use() == key)

    def _delete_style(self, name):
        delete_style(name)
        for k, c in list(self.sel.changes.items()):
            if c.source == ("meus", name):
                self.sel.remove(k)
        self.rebuild("meus")
        self.detail_for = ("meus", None)
        self._selection_changed()

    # ------------------------------------------------------------ painel de prévia e detalhes
    def env(self):
        return environment(self.desk, self.user_theme)

    def _render_detail(self):
        while child := self.detail.get_first_child():
            self.detail.remove(child)
        page, key = self.detail_for or (self.page, None)
        info = None
        if key is not None or page in ("barra", "efeitos"):
            builder = getattr(self, f"_detail_{page}", None)
            info = builder(key) if builder else None
        d = self.detail
        head = Gtk.Box(spacing=10)
        head.append(Gtk.Label(label="Prévia", xalign=0, css_classes=["title-2"]))
        if info:
            selected = (page, key) in [c.source for c in self.sel.changes.values()]
            if info.get("state"):
                state, cls = info["state"], "em-uso" if info["state"] == "Instalado" else None
            elif selected:
                state, cls = "Selecionado · não aplicado", "selecionado"
            elif info.get("in_use"):
                state, cls = "Em uso", "em-uso"
            else:
                state, cls = "Não selecionado", None
            head.append(Gtk.Label(label=state, valign=Gtk.Align.CENTER,
                                  css_classes=["studio-badge"] + ([cls] if cls else [])))
        d.append(head)
        if not info:
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=24, margin_bottom=24)
            empty.append(Gtk.Image(icon_name=self.PAGE[page][3], pixel_size=48, css_classes=["dim-label"]))
            empty.append(Gtk.Label(label="Escolha um item", css_classes=["title-3"]))
            empty.append(Gtk.Label(label="Clique num cartão para ver a prévia aqui. Nada muda no sistema até você "
                                         "clicar em Aplicar.", wrap=True, justify=Gtk.Justification.CENTER,
                                   css_classes=["dim-label"]))
            d.append(empty)
        else:
            preview = info["preview"]
            preview.set_halign(Gtk.Align.CENTER)
            d.append(preview)
            if info.get("caption"):
                d.append(Gtk.Label(label=info["caption"], xalign=0, wrap=True, css_classes=["caption", "dim-label"]))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text.append(Gtk.Label(label=info["title"], xalign=0, wrap=True, css_classes=["title-2"]))
            if info.get("subtitle"):
                text.append(Gtk.Label(label=info["subtitle"], xalign=0, wrap=True, css_classes=["dim-label"]))
            d.append(text)
            if info.get("error"):
                d.append(Gtk.Label(label=info["error"], xalign=0, wrap=True, css_classes=["studio-note", "erro"]))
            if info.get("extra"):
                d.append(info["extra"])
        d.append(self._pending_list())
        if info and info.get("notes"):
            d.append(Gtk.Label(label="Bom saber", xalign=0, css_classes=["heading"]))
            for note in info["notes"]:
                row = Gtk.Box(spacing=8, css_classes=["studio-note"])
                row.append(Gtk.Image(icon_name="dialog-information-symbolic", valign=Gtk.Align.START))
                row.append(Gtk.Label(label=note, xalign=0, wrap=True, hexpand=True))
                d.append(row)

    def _pending_list(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(Gtk.Label(label="O que será alterado", xalign=0, css_classes=["heading"]))
        changes = self.sel.for_reading()
        if not changes:
            box.append(Gtk.Label(label="Nada selecionado ainda.", xalign=0, css_classes=["dim-label"]))
            return box
        box.append(Gtk.Label(label="Desmarque o que você não quer mudar.", xalign=0, wrap=True,
                             css_classes=["caption", "dim-label"]))
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        icons = {k: i for k, _t, i in COMPONENTS}
        for c in changes:
            line = Gtk.Box(spacing=10, margin_start=8, margin_end=6, margin_top=6, margin_bottom=6)
            check = Gtk.CheckButton(active=c.enabled)
            set_label(check, f"Aplicar {c.title}: {c.label}")
            check.connect("toggled", lambda b, k=c.key: (self.sel.toggle(k, b.get_active()), self._refresh_chrome()))
            line.append(check)
            line.append(Gtk.Image(icon_name=icons[c.key]))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            text.append(Gtk.Label(label=c.title, xalign=0))
            text.append(Gtk.Label(label=c.label, xalign=0, wrap=True, css_classes=["caption", "dim-label"]))
            if c.error:
                text.append(Gtk.Label(label=f"Falhou: {c.error}", xalign=0, wrap=True, css_classes=["caption"]))
            line.append(text)
            rm = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                            tooltip_text=f"Tirar {c.title} da seleção")
            set_label(rm, f"Tirar {c.title} da seleção")
            rm.connect("clicked", lambda _b, k=c.key: self._unselect(k))
            line.append(rm)
            lst.append(Gtk.ListBoxRow(child=line, activatable=False))
        box.append(lst)
        return box

    def _unselect(self, key):
        self.sel.remove(key)
        if key in ("barra", "efeitos"):
            self._reset_drafts()
        self._selection_changed()

    # ------------------------------------------------------------ desfazer / restaurar / sobre
    def _ask_undo(self):
        if not self.desk.has_previous():
            return
        confirm(self, "Desfazer a última aplicação?",
                "Tema, ícones, cursor, papel de parede, barra e vidro voltam a ser como estavam antes do "
                "último “Aplicar”. Só existe um nível de desfazer.", "Desfazer", self._undo)

    def _undo(self):
        self.desk.undo()
        self._after_restore("Última aplicação desfeita")

    def _ask_restore(self):
        confirm(self, "Voltar ao visual original?",
                "Tema, ícones, cursor, papel de parede, barra e vidro voltam a ser como eram "
                f"antes de você abrir o {APP_NAME} pela primeira vez.", "Restaurar", self._restore)

    def _restore(self):
        self.desk.snapshot(PREVIOUS)  # dá para desfazer a restauração também
        self.desk.restore()
        self._after_restore("Visual original restaurado")

    def _after_restore(self, message):
        self._follow_theme()
        self._reset_drafts()
        if "efeitos" in self.grids or not self.builders["efeitos"][1]:
            self.rebuild("efeitos")
        self._selection_changed()
        self.toast(message)

    def _about(self):
        comments = ("Deixe seu Linux com a sua cara: escolha temas, ícones, cursores, papéis de parede e a "
                    "barra de tarefas, veja a prévia e aplique quando quiser.")
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
