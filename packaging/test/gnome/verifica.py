#!/usr/bin/env python3
# Confere, na sessão GNOME de verdade do contêiner, se o combo pegou em tudo: janelas, painel,
# ícones, cursor, wallpaper, Nautilus. Fala com o Shell por D-Bus (o ajudante do teste libera o Eval).
# Uso: verifica.py foto <arquivo.png>
#      verifica.py pid-nautilus
#      verifica.py combo <pasta do adon_studio.py> <pasta de saída> <nome do combo> <pid antigo do Nautilus>
import json
import re
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS = Gio.bus_get_sync(Gio.BusType.SESSION)
USER_THEME = "user-theme@gnome-shell-extensions.gcampax.github.com"
# cores que mostram que o painel NÃO pegou a cor do tema: azul do Adwaita, laranja do Yaru, Pop
DEFAULT_ACCENTS = {"azul do Adwaita": (53, 132, 228), "laranja do Yaru": (233, 84, 32),
                   "Pop": (72, 185, 199)}


def call(name, path, iface, method, args=None, reply=None):
    return BUS.call_sync(name, path, iface, method, args, GLib.VariantType(reply) if reply else None,
                         Gio.DBusCallFlags.NONE, 20000, None)


def ev(code):
    ok, out = call("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell", "Eval",
                   GLib.Variant("(s)", (code,)), "(bs)").unpack()
    if not ok:
        raise RuntimeError(f"Eval falhou: {out}")
    val = json.loads(out) if out else None
    # o Shell já devolve o resultado em JSON; o código daqui também usa JSON.stringify
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except ValueError:
            pass
    return val


def foto(path):
    call("org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot", "org.gnome.Shell.Screenshot",
         "Screenshot", GLib.Variant("(bbs)", (False, False, str(path))), "(bs)")


def pid_nautilus():
    try:
        return call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                    "GetConnectionUnixProcessID", GLib.Variant("(s)", ("org.gnome.Nautilus",)), "(u)").unpack()[0]
    except GLib.Error:
        return 0


def nautilus_windows():
    try:
        xml = call("org.gnome.Nautilus", "/org/gnome/Nautilus/window", "org.freedesktop.DBus.Introspectable",
                   "Introspect", None, "(s)").unpack()[0]
        return len(Gio.DBusNodeInfo.new_for_xml(xml).nodes)
    except GLib.Error:
        return 0


def toggles_ligados():
    """Cor de fundo dos botões ligados do painel rápido (GNOME 43+). None se não existe."""
    has = ev("JSON.stringify(!!Main.panel.statusArea.quickSettings)")
    if not has:
        return None
    ev("Main.panel.statusArea.quickSettings.menu.open(0), JSON.stringify(1)")
    time.sleep(1.5)
    return ev("""(() => {
        const out = [];
        const walk = a => {
            if (a.has_style_class_name?.('quick-toggle') && a.checked) {
                const c = a.get_theme_node().get_background_color();
                out.push([a.title ?? a.label ?? '?', c.red, c.green, c.blue]);
            }
            a.get_children().forEach(walk);
        };
        walk(Main.panel.statusArea.quickSettings.menu.actor ?? Main.panel.statusArea.quickSettings.menu.box);
        return JSON.stringify(out);
    })()""")


def aceita_dialogo(out, segundos=120):
    """Faz o que a pessoa faria na confirmação do GNOME ("Instalar extensão?"): fotografa e clica."""
    find = """(() => {
        for (const d of Main.layoutManager.modalDialogGroup.get_children())
            if (d._onInstallButtonPressed) return true;
        return false; })()"""
    for _ in range(segundos):
        if ev(find):
            time.sleep(1)
            foto(Path(out) / "dialogo-extensao.png")
            ev("""(() => { for (const d of Main.layoutManager.modalDialogGroup.get_children())
                if (d._onInstallButtonPressed) { d._onInstallButtonPressed(); return true; } })()""")
            print("== a pessoa clicou em Instalar na confirmação do GNOME", flush=True)
            return
        time.sleep(1)


def theme_colors(gtk):
    """Cores que aparecem no CSS do painel do tema aplicado."""
    for d in (Path(GLib.get_user_data_dir()) / "themes", Path.home() / ".themes"):
        css = d / gtk / "gnome-shell" / "gnome-shell.css"
        if css.exists():
            text = css.read_text(errors="ignore")
            out = {tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) for h in re.findall(r"#([0-9a-fA-F]{6})\b", text)}
            out |= {tuple(int(x) for x in m) for m in re.findall(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", text)}
            return out
    return set()


def combo(app_dir, out, title, old_pid):
    sys.path.insert(0, app_dir)
    import adon_studio as A
    gtks, icons, cursors, wall = next(p[1:] for p in A.PACKS if p[0] == title)
    iface = Gio.Settings.new("org.gnome.desktop.interface")
    results = []

    def check(ok, what, detail=""):
        results.append(ok)
        print(("✔ " if ok else "✘ ") + what + (f"  ({detail})" if detail else ""), flush=True)

    gtk = iface.get_string("gtk-theme")
    check(gtk in gtks, "tema das janelas", gtk)
    check(iface.get_string("icon-theme") in icons, "ícones", iface.get_string("icon-theme"))
    check(iface.get_string("cursor-theme") in cursors, "cursor", iface.get_string("cursor-theme"))
    uri = Gio.Settings.new("org.gnome.desktop.background").get_string("picture-uri")
    check(uri.endswith(f"/{wall}.svg"), "wallpaper", uri)
    log = Path(out) / "shell.log"
    failed = [l for l in (log.read_text(errors="ignore").splitlines() if log.exists() else [])
              if "Failed to load background" in l]
    check(not failed, "o Shell conseguiu desenhar o wallpaper", failed[-1].split("**: ")[-1] if failed else "")
    css = Path(GLib.get_user_config_dir()) / "gtk-4.0" / "gtk.css"
    check(css.exists() and gtk in css.read_text(), "apps GTK4 (~/.config/gtk-4.0)")

    state = ev(f"JSON.stringify(Main.extensionManager.lookup('{USER_THEME}')?.state ?? -1)")
    check(state == 1, "extensão User Themes ligada", f"estado {state}; 1 = ligada, -1 = o Shell não a conhece")
    sheet = ev("JSON.stringify(Main.getThemeStylesheet()?.get_path() ?? '')")
    check(gtk in sheet, "tema do painel (GNOME Shell)", sheet or "tema padrão")

    new_pid = pid_nautilus()
    check(new_pid and new_pid != old_pid, "Nautilus reiniciou com o tema novo", f"pid {old_pid} → {new_pid}")
    check(nautilus_windows() >= 1, "janela do Nautilus reaberta", f"{nautilus_windows()} janela(s)")

    colors = toggles_ligados()
    if colors is None:
        print("– painel rápido não existe nesta versão do GNOME (42): não se aplica")
    elif not colors:
        print("– nenhum botão ligado no painel rápido para conferir a cor")
    else:
        palette = theme_colors(gtk)
        for name, *rgb in colors:
            ok = any(all(abs(a - b) <= 3 for a, b in zip(rgb, c)) for c in palette)
            bad = next((k for k, v in DEFAULT_ACCENTS.items() if all(abs(a - b) < 40 for a, b in zip(rgb, v))), "")
            check(ok, f"botão '{name}' do painel com a cor do tema",
                  f"rgb{tuple(rgb)}" + ("" if ok else f", não está no CSS do tema {bad}"))
    foto(Path(out) / "depois-painel.png")
    ev("Main.panel.statusArea.quickSettings?.menu.close(0), JSON.stringify(1)")
    time.sleep(1)
    foto(Path(out) / "depois.png")
    print(f"RESULTADO: {results.count(False)} falha(s) em {len(results)} verificações")
    return 0 if all(results) else 1


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "foto":
        foto(sys.argv[2])
    elif cmd == "aceita-dialogo":
        aceita_dialogo(sys.argv[2])
    elif cmd == "pid-nautilus":
        print(pid_nautilus())
    elif cmd == "combo":
        sys.exit(combo(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])))
