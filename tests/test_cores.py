import base  # noqa: F401  (isola HOME/GSettings antes do import do app)

import unittest

import adon_studio as A


def so_temas_do_teste():
    """Os testes não podem ver os temas do sistema (num Zorin ou Ubuntu de verdade há ZorinRed, Yaru...)."""
    orig = A.list_gtk_themes
    A.list_gtk_themes = lambda: [t for t in orig() if t.path.is_relative_to(base.HOME)]
    return lambda: setattr(A, "list_gtk_themes", orig)


class PaletasDaCorFavorita(unittest.TestCase):
    """
    Aba Cores: o usuário escolhe a cor favorita e o app gera paletas que combinam, com texto
    legível e um wallpaper nas mesmas cores.
    """

    def test_gera_todas_as_harmonias_com_texto_legivel(self):
        for _nome, cor in A.FAVORITE_COLORS + [("Branco", "#ffffff"), ("Preto", "#000000")]:
            pals = A.make_palettes(cor)
            self.assertEqual([p["key"] for p in pals], [k for k, *_ in A.HARMONIES])
            for p in pals:
                self.assertGreaterEqual(A.contrast(p["fg"], p["bg"]), 7, (cor, p["key"]))
                self.assertGreaterEqual(A.contrast(p["accent_fg"], p["accent"]), 3, (cor, p["key"]))
                self.assertEqual(len(p["wall"]), 4)

    def test_destaque_mantem_o_tom_da_cor_escolhida(self):
        azul = A.make_palettes("#3584e4")[0]
        self.assertAlmostEqual(A.hex_hls(azul["accent"])[0], A.hex_hls("#3584e4")[0], delta=.02)
        self.assertEqual(A.gnome_accent(azul["accent"]), "blue")
        self.assertEqual(A.gnome_accent("#8a8f98"), "slate")
        self.assertEqual(A.gnome_accent("#e01b24"), "red")


class AplicarPaleta(unittest.TestCase):
    """
    Regressão: com o Catppuccin aplicado, escolher uma paleta não mudava o Nautilus, e a paleta
    Clara deixava o calendário do painel ilegível (fundo claro, letra clara). A paleta era
    colocada por cima do tema, mas o CSS do Catppuccin tem as cores escritas direto (nunca usa
    @accent_bg_color), então nada mudava; e o modo claro era ligado com o tema GTK e o do Shell
    ainda escuros. Agora a paleta troca a base: estilo próprio da libadwaita, Adwaita no GTK3 e
    o Shell do sistema, que seguem as cores e o modo claro/escuro.
    """

    def setUp(self):
        self.addCleanup(so_temas_do_teste())
        A.Desktop._clear_gtk4()
        self.d = A.Desktop()
        tema = base.HOME / ".themes" / "Fixo-Dark"
        (tema / "gtk-4.0").mkdir(parents=True, exist_ok=True)
        (tema / "gnome-shell").mkdir(exist_ok=True)
        (tema / "gtk-4.0" / "gtk.css").write_text("@define-color accent_bg_color #f5c2e7;\n"
                                                  "button { background-color: #f5c2e7; }\n")
        self.d.apply_gtk(A.Theme("Fixo-Dark", tema))

    def test_paleta_substitui_o_tema_que_tem_cores_fixas(self):
        pal = A.make_palettes("#e01b24")[0]
        self.d.apply_palette(pal)
        css = (A.GTK4_CFG / "gtk.css").read_text()
        self.assertNotIn("@import", css)
        self.assertIn(f"@define-color accent_bg_color {pal['accent']};", css)
        self.assertFalse((A.GTK4_CFG / "assets").exists())
        self.assertEqual(self.d.iface.get_string("gtk-theme"), "Adwaita-dark")

    def test_paleta_clara_nao_mistura_com_tema_escuro(self):
        self.d.apply_palette(A.make_palettes("#e01b24")[-1])
        self.assertEqual(self.d.iface.get_string("color-scheme"), "prefer-light")
        self.assertEqual(self.d.iface.get_string("gtk-theme"), "Adwaita")
        self.assertEqual(self.d.shell.get_string("name"), "")

    def test_nao_acumula_e_aplica_o_wallpaper(self):
        pals = A.make_palettes("#9141ac")
        self.d.apply_palette(pals[0])
        wall = self.d.apply_palette(pals[2])
        css = (A.GTK4_CFG / "gtk.css").read_text()
        self.assertEqual(css.count(A.PALETTE_START), 1)
        self.assertIn(f"@define-color accent_bg_color {pals[2]['accent']};", css)
        self.assertEqual(self.d.bg.get_string("picture-uri"), wall.as_uri())
        self.assertEqual(self.d.iface.get_string("color-scheme"), "prefer-dark")


class PastasNaCorDaPaleta(unittest.TestCase):
    """
    Regressão: depois de aplicar uma paleta vermelha, as pastas do Nautilus continuavam roxas. Quem
    desenha as pastas é o tema de ícones (Catppuccin-Mocha), e a paleta não mexia nos ícones. Agora
    ela gera um tema de ícones só com as pastas na cor da paleta, herdando o resto do tema atual.
    """

    def setUp(self):
        self.d = A.Desktop()
        self.d.apply_icons("Catppuccin-Mocha")

    def test_pastas_ganham_a_cor_e_o_resto_vem_do_tema_atual(self):
        pal = A.make_palettes("#e01b24")[0]
        self.d.apply_palette(pal)
        nome = self.d.iface.get_string("icon-theme")
        tema = A.USER_DATA / "icons" / nome
        self.assertIn(pal["accent"], (tema / "scalable" / "places" / "folder.svg").read_text())
        self.assertTrue((tema / "scalable" / "places" / "folder-download.svg").exists())
        self.assertIn("Inherits=Catppuccin-Mocha,", (tema / "index.theme").read_text())

    def test_trocar_de_paleta_nao_empilha_temas(self):
        self.d.apply_palette(A.make_palettes("#e01b24")[0])
        primeiro = self.d.iface.get_string("icon-theme")
        self.d.apply_palette(A.make_palettes("#2ec27e")[0])
        segundo = self.d.iface.get_string("icon-theme")
        self.assertNotEqual(primeiro, segundo)  # nome novo: os apps recarregam os ícones
        self.assertFalse((A.USER_DATA / "icons" / primeiro).exists())
        self.assertIn("Inherits=Catppuccin-Mocha,",
                      (A.USER_DATA / "icons" / segundo / "index.theme").read_text())


class PainelDoSistemaNaCor(unittest.TestCase):
    """
    Regressão: no Zorin, com a paleta vermelha, o menu rápido do painel (Wi-Fi, Bluetooth...)
    ficava cinza. A paleta ligava o Shell padrão, que no GNOME 46 não tem cor de destaque. O Zorin
    (e o Ubuntu, com o Yaru) traz temas por cor com painel incluso: a paleta agora usa a variante
    mais parecida com a cor escolhida, no modo certo (claro/escuro).
    """
    CORES = {"Red": "#fa4444", "Blue": "#15a6f0", "Green": "#2ac88d", "Grey": "#606264", "Orange": "#f88052",
             "Brown": "#a26b4e"}

    def setUp(self):
        import shutil
        self.shutil = shutil
        self.addCleanup(so_temas_do_teste())
        self.criados = []
        for cor, hexa in self.CORES.items():
            for modo in ("Light", "Dark"):
                tema = A.USER_DATA / "themes" / f"Zorin{cor}-{modo}"  # ~/.themes não existia no import
                (tema / "gnome-shell").mkdir(parents=True, exist_ok=True)
                (tema / "gtk-3.0").mkdir(exist_ok=True)
                (tema / "gtk-3.0" / "gtk.css").write_text(f"@define-color theme_selected_bg_color {hexa};\n")
                self.criados.append(tema)
        self.d = A.Desktop()

    def tearDown(self):
        for tema in self.criados:
            self.shutil.rmtree(tema)

    def test_vermelho_claro_usa_o_zorin_vermelho_claro(self):
        self.d.apply_palette(A.make_palettes("#e01b24")[-1])
        self.assertEqual(self.d.iface.get_string("gtk-theme"), "ZorinRed-Light")
        self.assertEqual(self.d.shell.get_string("name"), "ZorinRed-Light")

    def test_escura_usa_a_variante_escura(self):
        self.d.apply_palette(A.make_palettes("#3584e4")[0])
        self.assertEqual(self.d.shell.get_string("name"), "ZorinBlue-Dark")

    def test_laranja_nao_vira_marrom(self):
        self.d.apply_palette(A.make_palettes("#ff7800")[0])
        self.assertEqual(self.d.shell.get_string("name"), "ZorinOrange-Dark")

    def test_cinza_usa_o_cinza(self):
        self.d.apply_palette(A.make_palettes("#8a8f98")[0])
        self.assertEqual(self.d.shell.get_string("name"), "ZorinGrey-Dark")


if __name__ == "__main__":
    unittest.main()
