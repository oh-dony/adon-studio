import base  # noqa: F401  (isola HOME/GSettings antes do import do app)

import hashlib
import io
import json
import shutil
import tarfile
import unittest
from pathlib import Path

import adon_studio as A


def mkdirs(*paths):
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)


class Cursor(unittest.TestCase):
    """
    Regressão: com qualquer cursor que não fosse o Adwaita, o mouse virava um quadrado cinza ao
    passar sobre a barra de tarefas e o painel. O GNOME Shell só procura cursores em ~/.icons e
    /usr/share/icons, e os cursores eram instalados em ~/.local/share/icons (log do Shell:
    "No cursor theme available").
    """

    def setUp(self):
        self.theme = A.USER_DATA / "icons" / "CursorDeTeste"
        mkdirs(self.theme / "cursors")
        (self.theme / "cursors" / "default").write_bytes(b"Xcur")

    def tearDown(self):
        shutil.rmtree(self.theme, ignore_errors=True)
        link = base.HOME / ".icons" / "CursorDeTeste"
        if link.is_symlink():
            link.unlink()

    def test_aplicar_cursor_deixa_ele_visivel_para_o_shell(self):
        A.Desktop().apply_cursor("CursorDeTeste")
        link = base.HOME / ".icons" / "CursorDeTeste"
        self.assertTrue(link.is_symlink(), "o cursor precisa estar em ~/.icons")
        self.assertEqual(link.resolve(), self.theme.resolve())

    def test_ao_abrir_o_app_todos_os_cursores_ganham_atalho(self):
        A.Desktop.link_cursor_themes()
        self.assertTrue((base.HOME / ".icons" / "CursorDeTeste").is_symlink())

    def test_nao_duplica_atalho_existente(self):
        A.Desktop.link_cursor_themes()
        A.Desktop.link_cursor_themes()  # não pode estourar FileExistsError


class BarraEmTodosOsMonitores(unittest.TestCase):
    """
    Regressão: com o estilo dock aplicado, a barra de um monitor ficava compacta e a do outro
    continuava em tela inteira. A barra guarda os ajustes por id de monitor (fabricante-serial)
    e o monitor externo conectado tinha outro serial, ausente das configurações.
    """

    def setUp(self):
        self.desk = A.Desktop()
        self.assertIsNotNone(self.desk.bar)
        self.desk.bar.set_string("panel-lengths", json.dumps({"AUO-0x0": 100, "AOC-ANTIGO": 100}))
        self._ids = A.monitor_ids
        A.monitor_ids = lambda: ["AUO-0x0", "AOC-NOVO"]

    def tearDown(self):
        A.monitor_ids = self._ids
        self.desk.bar.reset("panel-lengths")

    def test_aplica_no_monitor_conectado_que_nao_estava_salvo(self):
        self.desk.bar_set("panel-lengths", -1)
        saved = json.loads(self.desk.bar.get_string("panel-lengths"))
        self.assertEqual(saved, {"AUO-0x0": -1, "AOC-ANTIGO": -1, "AOC-NOVO": -1})

    def test_le_o_valor_de_um_monitor_conectado(self):
        self.desk.bar_set("panel-lengths", -1)
        self.assertEqual(self.desk.bar_get("panel-lengths", 100), -1)


class PontoDeRestauracao(unittest.TestCase):
    """
    Regressão (prevenção): o botão de restaurar precisa voltar ao visual de antes do primeiro uso,
    e não ao do momento em que o app foi aberto de novo.
    """

    def setUp(self):
        shutil.rmtree(A.BACKUP, ignore_errors=True)
        self.desk = A.Desktop()
        self.desk.iface.set_string("gtk-theme", "TemaOriginal")

    def test_restaura_o_original_mesmo_depois_de_reabrir(self):
        self.desk.ensure_backup()
        self.desk.iface.set_string("gtk-theme", "OutroTema")
        A.Desktop().ensure_backup()  # segunda abertura não pode sobrescrever o original
        self.desk.restore()
        self.assertEqual(self.desk.iface.get_string("gtk-theme"), "TemaOriginal")


class TemaGtk4(unittest.TestCase):
    """
    Regressão: o primeiro script de troca de tema ligava gtk-dark.css no lugar do gtk.css, deixando
    escuros os apps GTK4 com temas claros.
    """

    def test_liga_cada_arquivo_ao_seu_par(self):
        theme = A.USER_DATA / "themes" / "Claro-Light"
        mkdirs(theme / "gtk-4.0", theme / "gtk-3.0")
        (theme / "gtk-3.0" / "gtk.css").write_text("")
        (theme / "gtk-4.0" / "gtk.css").write_text("/* claro */")
        (theme / "gtk-4.0" / "gtk-dark.css").write_text("/* escuro */")
        desk = A.Desktop()
        desk.apply_gtk(A.Theme("Claro-Light", theme))
        self.assertIn((theme / "gtk-4.0" / "gtk.css").as_uri(), (A.GTK4_CFG / "gtk.css").read_text())
        self.assertIn((theme / "gtk-4.0" / "gtk-dark.css").as_uri(), (A.GTK4_CFG / "gtk-dark.css").read_text())
        self.assertEqual(desk.iface.get_string("color-scheme"), "prefer-light")


class ImagensDoTemaGtk4(unittest.TestCase):
    """
    Regressão: com o tema Dracula, os interruptores (liga/desliga) ficavam invisíveis em todos os
    apps GTK4. O tema aponta as imagens como "../assets/switch-off.svg", relativo ao CSS; o app
    ligava o CSS em ~/.config/gtk-4.0 por atalho, e o GTK resolvia o caminho a partir de
    ~/.config, onde as imagens não existem. Com @import, o caminho vale a partir da pasta do tema.
    """

    def test_css_do_tema_e_importado_e_nao_ligado_por_atalho(self):
        theme = A.USER_DATA / "themes" / "ComAssets"
        mkdirs(theme / "gtk-3.0", theme / "gtk-4.0", theme / "assets")
        (theme / "gtk-3.0" / "gtk.css").write_text("")
        (theme / "assets" / "switch-off.svg").write_text("<svg/>")
        (theme / "gtk-4.0" / "gtk.css").write_text('switch { background-image: url("../assets/switch-off.svg"); }')
        A.Desktop().apply_gtk(A.Theme("ComAssets", theme))
        cfg = A.GTK4_CFG / "gtk.css"
        self.assertFalse(cfg.is_symlink())
        self.assertIn(f'@import url("{(theme / "gtk-4.0" / "gtk.css").as_uri()}")', cfg.read_text())

    def test_trocar_de_tema_substitui_o_import(self):
        a, b = A.USER_DATA / "themes" / "TemaA", A.USER_DATA / "themes" / "TemaB"
        for t in (a, b):
            mkdirs(t / "gtk-3.0", t / "gtk-4.0")
            (t / "gtk-3.0" / "gtk.css").write_text("")
            (t / "gtk-4.0" / "gtk.css").write_text("")
        desk = A.Desktop()
        desk.apply_gtk(A.Theme("TemaA", a))
        desk.apply_gtk(A.Theme("TemaB", b))
        text = (A.GTK4_CFG / "gtk.css").read_text()
        self.assertIn("TemaB", text)
        self.assertNotIn("TemaA", text)


class PastasOcultas(unittest.TestCase):
    """
    Regressão: pastas de apoio ocultas (como .Fluent-base, de onde o Fluent puxa os ícones) não
    podem aparecer como pacote de ícones na lista.
    """

    def test_pasta_oculta_nao_vira_tema(self):
        d = A.USER_DATA / "icons" / ".Base-oculta"
        mkdirs(d)
        (d / "index.theme").write_text("[Icon Theme]\nName=x\nDirectories=16\n")
        self.assertNotIn(".Base-oculta", A.list_icon_themes())


def make_tar(path, members):
    with tarfile.open(path, "w:xz") as tf:
        for name, kind, extra in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                tf.addfile(info)
            elif kind == "sym":
                info.type, info.linkname = tarfile.SYMTYPE, extra
                tf.addfile(info)
            else:
                data = extra.encode()
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))


class LojaSegura(unittest.TestCase):
    """
    Regressão (prevenção): um pacote baixado não pode escrever fora da pasta de temas — nada de
    '../', caminho absoluto ou atalho apontando para fora.
    """

    def test_descarta_o_que_sai_da_pasta(self):
        f = base.TMP / "malicioso.tar.xz"
        make_tar(f, [("Tema", "dir", None), ("Tema/ok.css", "file", "a"),
                     ("../fora.txt", "file", "x"), ("/etc/abs.txt", "file", "x"),
                     ("Tema/link-fora", "sym", "../../../../etc/passwd"),
                     ("Tema/link-abs", "sym", "/etc/passwd"), ("Tema/link-ok", "sym", "ok.css")])
        dest = base.TMP / "destino"
        mkdirs(dest)
        with tarfile.open(f) as tf:
            names = {m.name for m in A._safe_members(tf, dest)}
        self.assertEqual(names, {"Tema", "Tema/ok.css", "Tema/link-ok"})


class LojaInstalar(unittest.TestCase):
    """
    Regressão (prevenção): instalar pela Loja precisa descompactar no lugar certo e recusar arquivo
    corrompido sem deixar nada pela metade.
    """

    def setUp(self):
        self.tar = base.LOJA / "pacote.tar.xz"
        make_tar(self.tar, [("TemaLoja", "dir", None), ("TemaLoja/gtk-3.0", "dir", None),
                            ("TemaLoja/gtk-3.0/gtk.css", "file", "/* ok */")])
        digest = hashlib.sha256(self.tar.read_bytes()).hexdigest()
        self.item = {"id": "tema-loja", "kind": "gtk", "title": "Tema Loja", "dirs": ["TemaLoja"],
                     "file": "pacote.tar.xz", "preview": "p.png", "bytes": self.tar.stat().st_size, "sha256": digest}
        shutil.rmtree(A.STORE_DIRS["gtk"] / "TemaLoja", ignore_errors=True)

    def install(self, item):
        from gi.repository import GLib
        loop, result = GLib.MainLoop(), {}
        A.Store().install([item], lambda f: None, lambda err: (result.update(err=err), loop.quit()))
        GLib.timeout_add_seconds(20, loop.quit)
        loop.run()
        return result.get("err", "tempo esgotado")

    def test_instala(self):
        self.assertIsNone(self.install(self.item))
        self.assertTrue(A.Store.is_installed(self.item))

    def test_recusa_arquivo_corrompido(self):
        err = self.install(dict(self.item, sha256="0" * 64))
        self.assertIn("corrompido", err)
        self.assertFalse((A.STORE_DIRS["gtk"] / "TemaLoja").exists())


class PacotesCompletos(unittest.TestCase):
    """
    Regressão: o pacote de ícones Fluent ia sem as pastas ocultas .Fluent-*-base, das quais todas as
    pastas dele são atalhos — no computador de outra pessoa o tema viria vazio. Defeitos que já
    vêm do tema original (atalhos quebrados na origem, como no Nordic) são tolerados.
    """

    def setUp(self):
        import build_bundles
        self.B = build_bundles
        self.src = base.TMP / "origem"
        shutil.rmtree(self.src, ignore_errors=True)
        mkdirs(self.src / ".Base" / "16", self.src / "Icones")
        (self.src / ".Base" / "16" / "a.svg").write_text("<svg/>")
        (self.src / "Icones" / "16").symlink_to("../.Base/16")
        (self.src / "Icones" / "quebrado-na-origem").symlink_to("nao-existe")

    def pack(self, dirs):
        arc = base.TMP / "pacote-check.tar.xz"
        with tarfile.open(arc, "w:xz") as tf:
            for d in dirs:
                tf.add(self.src / d, arcname=d)
        return arc

    def test_reclama_da_pasta_que_faltou(self):
        item = {"id": "x", "dirs": ["Icones"]}
        with self.assertRaises(SystemExit):
            self.B.check_links(self.pack(["Icones"]), item, self.src)

    def test_aceita_pacote_completo(self):
        item = {"id": "x", "dirs": ["Icones", ".Base"]}
        self.B.check_links(self.pack(["Icones", ".Base"]), item, self.src)


class CatalogoDoRepositorio(unittest.TestCase):
    """Regressão: o Fluent precisa levar as pastas ocultas de que depende (ver PacotesCompletos)."""

    def test_fluent_leva_as_pastas_base(self):
        items = {i["id"]: i for i in json.loads((base.ROOT / "packaging" / "sources.json").read_text())["items"]}
        for hidden in (".Fluent-base", ".Fluent-dark-base", ".Fluent-light-base"):
            self.assertIn(hidden, items["fluent-icons"]["dirs"])


if __name__ == "__main__":
    unittest.main()
