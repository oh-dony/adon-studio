import base  # noqa: F401  (isola HOME/GSettings antes do import do app)

import hashlib
import shutil
import tarfile
import unittest

import adon_studio as A
from gi.repository import GLib


def make_tar(path, files):
    with tarfile.open(path, "w:xz") as tf:
        for name, content in files:
            info = tarfile.TarInfo(name)
            data = content.encode()
            info.size = len(data)
            import io
            tf.addfile(info, io.BytesIO(data))


def store_item(item_id, folder):
    tar = base.LOJA / f"{item_id}.tar.xz"
    make_tar(tar, [(f"{folder}/gtk-3.0/gtk.css", "/* ok */")])
    shutil.rmtree(A.STORE_DIRS["gtk"] / folder, ignore_errors=True)
    return {"id": item_id, "kind": "gtk", "title": folder, "dirs": [folder], "file": tar.name,
            "preview": "p.png", "bytes": tar.stat().st_size, "sha256": hashlib.sha256(tar.read_bytes()).hexdigest()}


def run_install(store, items):
    loop, result = GLib.MainLoop(), {}
    store.install(items, lambda f: None, lambda err: (result.update(err=err), loop.quit()))
    GLib.timeout_add_seconds(20, loop.quit)
    loop.run()
    return result.get("err", "tempo esgotado")


class InstalarVariosComFalha(unittest.TestCase):
    """
    Regressão: ao instalar vários pacotes de uma vez (um estilo que precisa de tema, ícones e
    cursor), se o primeiro falhasse os outros nem eram tentados e a mensagem só citava o primeiro;
    o usuário não sabia o que tinha sido instalado. Agora cada item é tentado e o erro diz qual
    falhou.
    """

    def test_continua_depois_de_um_item_com_erro(self):
        ruim = dict(store_item("ruim", "TemaRuim"), sha256="0" * 64)
        bom = store_item("bom", "TemaBom")
        store = A.Store()
        err = run_install(store, [ruim, bom])
        self.assertTrue(A.Store.is_installed(bom), "o segundo pacote precisa ser instalado")
        self.assertFalse(A.Store.is_installed(ruim))
        self.assertIn("TemaRuim", err)
        self.assertNotIn("TemaBom", err)
        self.assertEqual(set(store.failed), {"ruim"})


class DesfazerPaleta(unittest.TestCase):
    """
    Regressão: aplicar uma paleta e depois outra apagava a pasta de ícones coloridos da primeira.
    Desfazer a última aplicação voltava para um tema de ícones que não existia mais (e o sistema
    caía nos ícones padrão).
    """

    def test_mantem_as_pastas_da_paleta_anterior(self):
        a, b = A.make_palettes("#e01b24")[0], A.make_palettes("#3584e4")[0]
        primeiro = A.folder_icon_theme(a, "Adwaita")
        segundo = A.folder_icon_theme(b, primeiro)
        self.assertTrue((A.USER_DATA / "icons" / primeiro / "index.theme").exists())
        self.assertTrue((A.USER_DATA / "icons" / segundo / "index.theme").exists())
        terceiro = A.folder_icon_theme(a, segundo)
        self.assertFalse((A.USER_DATA / "icons" / "nao-existe").exists())
        restantes = {p.name for p in (A.USER_DATA / "icons").glob(f"{A.FOLDER_PREFIX}*")}
        self.assertEqual(restantes, {segundo, terceiro}, "só a atual e a anterior ficam guardadas")


def tema(nome):
    d = A.USER_DATA / "themes" / nome
    (d / "gtk-3.0").mkdir(parents=True, exist_ok=True)
    (d / "gtk-3.0" / "gtk.css").write_text("@define-color theme_bg_color #202020;\n")
    return A.Theme(nome, d)


class SelecaoPendente(unittest.TestCase):
    """
    Pedido do usuário: escolher não pode mudar o sistema na hora. As escolhas ficam pendentes,
    dá para desmarcar cada componente, e só o "Aplicar" muda alguma coisa.
    """

    def setUp(self):
        self.feito = []
        self.sel = A.Selection()

    def change(self, key, falha=None):
        def apply():
            if falha:
                raise A.NotInstalled(falha)
            self.feito.append(key)
        return A.Change(key, key, key, apply, source=("pagina", key))

    def test_selecionar_nao_aplica(self):
        self.sel.set(self.change("tema"))
        self.assertEqual(self.feito, [])
        self.assertEqual(self.sel.selected("pagina"), {"tema"})

    def test_aplica_so_o_que_esta_marcado_e_na_ordem(self):
        for k in ("wallpaper", "tema", "icones"):
            self.sel.set(self.change(k))
        self.sel.toggle("tema", False)
        ok, failed = self.sel.apply()
        self.assertEqual(self.feito, ["icones", "wallpaper"])
        self.assertEqual((ok, failed), (["Ícones", "Papel de parede"], []))
        self.assertEqual(list(self.sel.changes), ["tema"], "o desmarcado continua pendente")

    def test_falha_parcial_relata_e_continua(self):
        self.sel.set(self.change("tema", falha="o tema X não está instalado"))
        self.sel.set(self.change("cursor"))
        ok, failed = self.sel.apply()
        self.assertEqual(ok, ["Cursor"])
        self.assertEqual(failed, [("Tema", "o tema X não está instalado")])
        self.assertIn("tema", self.sel.changes, "o que falhou fica para tentar de novo")
        self.assertEqual(self.sel.changes["tema"].error, "o tema X não está instalado")

    def test_paleta_e_tema_nao_vao_juntos(self):
        self.sel.set(self.change("tema"))
        self.assertEqual(self.sel.set(self.change("cores")), ["Tema"])
        self.assertNotIn("tema", self.sel.changes)

    def test_descartar(self):
        self.sel.set(self.change("tema"))
        self.sel.clear()
        self.assertEqual(self.sel.apply(), ([], []))
        self.assertEqual(self.feito, [])


class DesfazerUltimaAplicacao(unittest.TestCase):
    """Pedido do usuário: desfazer a última aplicação (além do "voltar ao original")."""

    def test_volta_ao_que_estava_antes_de_aplicar(self):
        d = A.Desktop()
        d.apply_gtk(tema("Antes-Dark"))
        d.apply_icons("Icones-Antes")
        d.snapshot(A.PREVIOUS)
        d.apply_gtk(tema("Depois-Dark"))
        d.apply_icons("Icones-Depois")
        self.assertTrue(d.has_previous())
        d.undo()
        self.assertEqual(d.iface.get_string("gtk-theme"), "Antes-Dark")
        self.assertEqual(d.iface.get_string("icon-theme"), "Icones-Antes")
        self.assertFalse(d.has_previous(), "um nível só")


class RascunhoDaBarra(unittest.TestCase):
    """A barra só é gravada no Aplicar, e só os ajustes que mudaram (o resto fica como estava)."""

    def setUp(self):
        orig = A.monitor_ids
        A.monitor_ids = lambda: ["mon-1"]
        self.addCleanup(setattr, A, "monitor_ids", orig)
        self.d = A.Desktop()
        for k in A.TASKBAR_KEYS + ["panel-anchors"]:
            self.d.bar.reset(k)

    def test_grava_so_o_que_mudou(self):
        draft = self.d.bar_read()
        draft.update(size=60, margin=8)
        self.d.bar_write(draft)
        self.assertEqual(self.d.bar_read()["size"], 60)
        self.assertEqual(self.d.bar.get_int("panel-margin"), 8)
        self.assertEqual(self.d.bar.get_string("panel-anchors"), "{}", "comprimento não mudou: âncora intocada")
        self.assertEqual(self.d.bar.get_string("panel-positions"), "{}")


class MeusEstilos(unittest.TestCase):
    def test_salvar_substituir_e_excluir(self):
        A.MY_STYLES.unlink(missing_ok=True)
        A.save_style(dict(name="Noite", gtk="A", icons="B", cursor="C", wallpaper=None))
        A.save_style(dict(name="Noite", gtk="D", icons="B", cursor="C", wallpaper=None))
        self.assertEqual([(s["name"], s["gtk"]) for s in A.load_styles()], [("Noite", "D")])
        A.delete_style("Noite")
        self.assertEqual(A.load_styles(), [])

    def test_arquivo_estragado_nao_derruba(self):
        A.MY_STYLES.parent.mkdir(parents=True, exist_ok=True)
        A.MY_STYLES.write_text("{nada")
        self.assertEqual(A.load_styles(), [])


class LimitacoesJuntoDoRecurso(unittest.TestCase):
    """Sem a User Themes o painel não muda: o aviso aparece no tema, não numa faixa genérica."""

    def test_aviso_do_painel_no_tema(self):
        env = A.environment(A.Desktop(), "falta")
        self.assertTrue(any("User Themes" in n for n in A.limitations("tema", env)))
        self.assertFalse(any("User Themes" in n for n in A.limitations("cursor", env)))


if __name__ == "__main__":
    unittest.main()
