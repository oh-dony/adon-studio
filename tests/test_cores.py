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
        # a anterior fica guardada para o "Desfazer" (ver test_fluxo.DesfazerPaleta); a de antes dela, não
        self.assertTrue((A.USER_DATA / "icons" / primeiro).exists())
        self.d.apply_palette(A.make_palettes("#3584e4")[0])
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


class PapelDeParedeNasCoresDaPaleta(unittest.TestCase):
    """
    Pedido do usuário: gerar papéis de parede (montanhas, ondas, gradiente, formas) com as cores
    escolhidas, e "Variar desenho" com uma semente para dar para repetir o resultado.
    """

    def test_cada_desenho_gera_svg_valido_com_as_cores(self):
        import xml.etree.ElementTree as ET
        pal = A.make_palettes("#e01b24")[0]
        for design, _nome in A.WALL_DESIGNS:
            svg = A.design_svg(pal["wall"], design, 3)
            ET.fromstring(svg)  # XML válido
            self.assertTrue(pal["wall"][2] in svg or pal["wall"][3] in svg, f"{design} usa o destaque da paleta")
            self.assertEqual(svg, A.design_svg(pal["wall"], design, 3), "mesma semente, mesmo desenho")
            self.assertNotEqual(svg, A.design_svg(pal["wall"], design, 4), "outra semente, outro desenho")

    def test_paleta_grava_o_desenho_escolhido(self):
        A.Desktop._clear_gtk4()
        pal = dict(A.make_palettes("#2ec27e")[1], design="montanhas", seed=2)
        wall = A.Desktop().apply_palette(pal)
        self.assertIn("montanhas 2", wall.read_text())
        self.assertTrue(wall.name.endswith("-montanhas-2.svg"))


class MontanhasComProfundidade(unittest.TestCase):
    """
    Regressão: no modelo Montanhas com a paleta clara, a camada da frente saía quase branca, mais
    clara que as do fundo (a profundidade ficava invertida) e o desenho parecia infantil.
    Agora a camada mais perto é sempre a mais escura, no claro e no escuro.
    """

    def test_frente_mais_escura_que_o_fundo(self):
        import re
        for pal in A.make_palettes("#2190a4"):  # escuras e a clara
            svg = A.design_svg(pal["wall"], "montanhas", 0)
            serras = re.findall(r'class="serra" fill="url\(#s(\d)\)"', svg)
            cores = dict(re.findall(r'<linearGradient id="s(\d)"[^>]*><stop offset="0" stop-color="(#[0-9a-f]{6})"', svg))
            self.assertGreaterEqual(len(serras), 4, pal["key"])
            lum = [A.luminance(cores[i]) for i in serras]
            self.assertEqual(lum, sorted(lum, reverse=True), f"{pal['key']}: do fundo para a frente escurece")

    def test_cristas_suaves(self):
        """Pedido do usuário: as pontas finas ficaram “agressivas”. As serras são curvas com poucos morros."""
        import re
        svg = A.design_svg(A.make_palettes("#9141ac")[1]["wall"], "montanhas", 5)
        for d in re.findall(r'class="serra"[^>]* d="([^"]+)"', svg):
            self.assertNotIn(",", d.split("L")[0], "sem polígono de pontos retos")
            self.assertLessEqual(d.count(" C"), 16, "poucos morros por camada")


class CartoesLegiveisNaPaleta(unittest.TestCase):
    """
    Regressão: com uma paleta escura aplicada, cartões e listas dos apps GTK 4 (inclusive o
    “O que será alterado” do próprio app) ficavam brancos com texto claro, ilegíveis. O CSS da
    paleta não definia card_bg_color e o fundo dos cartões caía no padrão branco.
    """

    def test_paleta_define_os_cartoes_com_contraste(self):
        for cor in ("#2190a4", "#e01b24"):
            for pal in A.make_palettes(cor):
                cores = dict(A.DEFINE.findall(A.palette_css(pal)))
                for nome in ("card_bg_color", "card_fg_color", "popover_bg_color", "dialog_bg_color"):
                    self.assertIn(nome, cores, pal["key"])
                self.assertGreaterEqual(A.contrast(cores["card_fg_color"].strip(), cores["card_bg_color"].strip()), 7,
                                        f"{cor} {pal['key']}")


class PaisagemEntardecer(unittest.TestCase):
    """
    Pedido do usuário: paisagens ilustradas (começando por montanhas ao entardecer) em SVG com
    camadas por função, recoloríveis pela paleta sem perder a relação de claro/escuro entre as
    camadas, e com o centro tranquilo para não brigar com janelas e ícones.
    """

    PAPEIS = ["ceu", "luz", "distantes", "intermediarias", "primeiro-plano", "destaques"]

    def cores_do_papel(self, svg, papel):
        import re
        grupo = re.search(rf'<g id="{papel}"[^>]*>(.*?)</g><!-- /{papel} -->', svg, re.S)
        self.assertIsNotNone(grupo, papel)
        return re.findall(r'fill="(#[0-9a-f]{6})"', grupo.group(1))

    def test_camadas_por_funcao_e_profundidade_em_qualquer_paleta(self):
        paletas = [A.LANDSCAPE_DEFAULT] + [p["wall"] for cor in ("#2190a4", "#e01b24", "#2ec27e")
                                           for p in A.make_palettes(cor)]
        for wall in paletas:
            svg = A.design_svg(wall, "entardecer", 0)
            import xml.etree.ElementTree as ET
            ET.fromstring(svg)
            lum = {p: max(A.luminance(c) for c in self.cores_do_papel(svg, p))
                   for p in ("distantes", "intermediarias", "primeiro-plano")}
            self.assertGreater(lum["distantes"], lum["intermediarias"], wall)
            self.assertGreater(lum["intermediarias"], lum["primeiro-plano"], wall)
            for papel in self.PAPEIS:
                self.assertIn(f'<g id="{papel}"', svg)

    def test_centro_tranquilo(self):
        import re
        svg = A.design_svg(A.LANDSCAPE_DEFAULT, "entardecer", 3)
        frente = re.search(r'<g id="primeiro-plano"[^>]*>(.*?)</g><!-- /primeiro-plano -->', svg, re.S).group(1)
        for x, y in re.findall(r'data-pinheiro="(\d+),(\d+)"', frente):
            meio = .3 * 3840 < int(x) < .7 * 3840
            self.assertFalse(meio and int(y) < .82 * 2160, f"pinheiro grande no meio da tela em {x},{y}")


class PretoEBrancoNaCorFavorita(unittest.TestCase):
    """
    Regressão: em Cores → “Outra…”, escolher preto gerava as mesmas paletas cinza-médio do Cinza
    (destaque #808080), parecendo que a escolha não funcionava; e as quatro paletas escuras saíam
    idênticas. Cor sem saturação caía num cinza fixo, sem olhar se era clara ou escura.
    """

    def test_preto_fica_preto(self):
        preto = A.make_palettes("#000000")
        cinza = A.make_palettes("#8a8f98")
        escuras = [p for p in preto if p["dark"]]
        self.assertLess(A.luminance(escuras[0]["bg"]), A.luminance(cinza[0]["bg"]), "fundo mais escuro que o do Cinza")
        self.assertNotEqual(escuras[0]["accent"], cinza[0]["accent"])
        self.assertEqual(len({p["accent2"] for p in escuras}), len(escuras), "as paletas escuras não são iguais")

    def test_branco_tem_destaque_claro_e_paletas_diferentes(self):
        branco = [p for p in A.make_palettes("#ffffff") if p["dark"]]
        self.assertGreater(A.luminance(branco[0]["accent"]), A.luminance(A.make_palettes("#8a8f98")[0]["accent"]))
        self.assertEqual(len({p["accent2"] for p in branco}), len(branco))
