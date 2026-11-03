#!/usr/bin/env python3
# Teste de fumaça: abre o app, passa por todas as seções, instala um pacote pelo Explorar e salva prints.
# Uso: python3 teste_app.py <pasta do adon_studio.py> <pasta de saída> [id do pacote]
import sys
import traceback
from pathlib import Path

sys.path.insert(0, sys.argv[1])
OUT = Path(sys.argv[2])
PACK = sys.argv[3] if len(sys.argv) > 3 else "dracula-gtk"
import adon_studio as A  # noqa: E402
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

errors = []
sys.excepthook = lambda *e: errors.append("".join(traceback.format_exception(*e)))


def shot(win, name):
    if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 12):
        # PyGObject antigo (Ubuntu 22.04) não lida com GskRenderNode: fotografa a tela virtual inteira
        import subprocess
        subprocess.run(["import", "-window", "root", str(OUT / f"{name}.png")], check=False)
        return
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    node = snap.to_node()
    if node:
        tex = win.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
        tex.save_to_png(str(OUT / f"{name}.png"))


class T(A.App):
    def do_activate(self):
        A.App.do_activate(self)
        win = self.props.active_window
        pages = [p[0] for p in win.PAGES]
        steps = iter(pages)

        def finish(ok):
            print("LOJA:", ok)
            print("ERROS:", len(errors))
            for e in errors:
                print(e)
            self.quit()

        def install():
            # Regressão: Adw.ResponseAppearance não existe na libadwaita 1.1 (Ubuntu 22.04) e derrubava
            # as janelas de confirmar e de salvar estilo. Abrir as duas aqui pega isso.
            for dlg in (A.confirm(win, "Teste", "Confirmar", "Ok", lambda: None),
                        A.ask_text(win, "Teste", "Nome", "Salvar", "x", lambda t: None)):
                dlg.close() if hasattr(dlg, "close") else dlg.destroy()
            print("DIALOGOS: ok", flush=True)
            # a cortina de verdade (tela cheia em cada monitor) abre e fecha sem erro
            cortina = A.Curtain(win)
            cortina.cover("#282828", lambda: (shot(win, "cortina"), cortina.uncover()))
            print("CORTINA: ok", flush=True)
            item = win.store.items.get(PACK)
            if not item:
                return finish(f"pacote {PACK} não está no catálogo ({len(win.store.items)} itens)")
            win._install([PACK])  # instalar não aplica nada

            def check():
                if win.busy:
                    return True
                shot(win, "loja-depois")
                finish("instalado" if win.store.is_installed(item) else "NÃO instalou")
                return False
            GLib.timeout_add(500, check)

        def nxt():
            try:
                name = next(steps)
            except StopIteration:
                install()
                return False
            win.stack.set_visible_child_name(name)
            GLib.timeout_add(4000, lambda: (shot(win, name), print("ok", name, flush=True), nxt())[0] and False)
            return False
        GLib.timeout_add(3000, nxt)


T().run([])
