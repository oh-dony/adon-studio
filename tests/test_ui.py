import base  # noqa: F401  (isola HOME/GSettings antes do import do app)

import unittest

import adon_studio as A
from gi.repository import Gdk, Gtk

TEM_TELA = Gdk.Display.get_default() is not None


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class CartoesDaGrade(unittest.TestCase):
    """
    Regressão: na aba Wallpaper só cabia um cartão por linha. Um arquivo com nome comprido
    ("2026-02-09-14-05-39-wallpapersden.com_abstract-wave-grey-duotone_2560x1435") esticava a
    largura natural do cartão, e a grade (homogênea) esticava todos os outros junto.
    """

    def test_nome_comprido_nao_estica_o_cartao(self):
        grid = A.Grid(lambda key: None)
        curto = grid.add("a", Gtk.Box(width_request=240), "Curto")
        longo = grid.add("b", Gtk.Box(width_request=240), "x" * 120, "y" * 120)
        _min, nat_curto = curto.measure(Gtk.Orientation.HORIZONTAL, -1)[:2]
        _min, nat_longo = longo.measure(Gtk.Orientation.HORIZONTAL, -1)[:2]
        self.assertLessEqual(nat_longo, max(nat_curto, 320))


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class CarregandoAoAplicar(unittest.TestCase):
    """
    Pedido do usuário: ao clicar num tema, ícone, cursor etc., o cartão mostra que está
    carregando e depois confirma. Enquanto aplica, outro clique não pode disparar de novo.
    """

    def test_mostra_carregando_e_bloqueia_clique_duplo(self):
        picks = []
        grid = A.Grid(picks.append)
        card = grid.add("a", Gtk.Box(), "Tema A")
        grid.add("b", Gtk.Box(), "Tema B")
        grid.start("a")
        self.assertTrue(card.veil.get_visible())
        self.assertEqual(card.veil_stack.get_visible_child_name(), "load")
        grid.cards["b"].emit("clicked")
        self.assertEqual(picks, [], "clique durante o carregando não pode aplicar outra coisa")
        grid.finish("a")
        self.assertEqual(card.veil_stack.get_visible_child_name(), "ok")
        grid.cards["b"].emit("clicked")
        self.assertEqual(picks, ["b"])


class MiniaturasDaLoja(unittest.TestCase):
    """
    Regressão: na Loja (Ubuntu 24.04) as miniaturas apareciam cortadas e só cabiam 2 cartões
    por linha. A imagem baixada tem 480 px de largura, e a Gtk.Picture usa o tamanho da imagem
    como largura natural, esticando o cartão.
    """

    def test_miniatura_vem_no_tamanho_do_cartao(self):
        from gi.repository import GdkPixbuf
        f = base.TMP / "previa.png"
        GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 480, 270).savev(str(f), "png", [], [])
        tex = A.thumb_texture(f, 240, 135)
        self.assertLessEqual(tex.get_width(), 240)
        self.assertLessEqual(tex.get_height(), 135)


if __name__ == "__main__":
    unittest.main()
