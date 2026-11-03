#!/usr/bin/env python3
# Faz o que a pessoa faria: abre o Adon Studio, espera o catálogo da Loja e clica num combo.
# Uso: python3 aplica_combo.py <pasta do adon_studio.py> <pasta de saída> <nome do combo>
import sys
import traceback
from pathlib import Path

sys.path.insert(0, sys.argv[1])
OUT, COMBO = Path(sys.argv[2]), sys.argv[3]
import adon_studio as A  # noqa: E402
from gi.repository import GLib  # noqa: E402

errors = []
sys.excepthook = lambda *e: errors.append("".join(traceback.format_exception(*e)))


class T(A.App):
    def do_activate(self):
        A.App.do_activate(self)
        win = self.props.active_window
        print("USER_THEMES:", win.user_theme, flush=True)
        state = {"t": 0, "clicked": False}

        def finish(msg):
            print("COMBO:", msg)
            print("ERROS:", len(errors))
            for e in errors:
                print(e)
            self.quit()
            return False

        def tick():
            state["t"] += 1
            if state["t"] > 600:  # 5 min
                return finish("tempo esgotado")
            if not state["clicked"]:
                if COMBO in getattr(win, "packs", {}) and win.store.items:
                    win.stack.set_visible_child_name("combos")
                    print("baixa:", [i["id"] for i in win.packs[COMBO]["need"]], flush=True)
                    state["antes"] = win.desk.iface.get_string("gtk-theme")
                    win._pick_combo(COMBO)
                    state["clicked"] = True
                return True
            if win.busy or win.desk.iface.get_string("gtk-theme") == state["antes"]:
                return True
            # espera o ✓ do cartão (o combo é aplicado ~1s depois do download)
            state.setdefault("wait", state["t"] + 8)
            if state["t"] < state["wait"]:
                return True
            return finish("aplicado")
        GLib.timeout_add(500, tick)


T().run([])
