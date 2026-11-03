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


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class BotaoOutraCor(unittest.TestCase):
    """
    Regressão: na aba Cores, depois de clicar na bolinha vermelha, o botão "Outra cor…" continuava
    azul. Ele só recebia a cor de partida e não acompanhava a cor escolhida.
    """

    def test_acompanha_a_cor_escolhida(self):
        from gi.repository import Adw
        win = A.Window(Adw.Application(application_id="com.adonay.studio.teste"))
        win.stack.set_visible_child_name("cores")
        win._fill_palettes("#e01b24")
        self.assertTrue(win.color_btn.get_rgba().equal(A.rgba("#e01b24")))
        self.assertEqual(len(win.grids["cores"].cards), len(A.HARMONIES))


def janela():
    from gi.repository import Adw
    win = A.Window(Adw.Application(application_id="com.adonay.studio.teste"))
    win.curtain = CortinaFalsa()  # a de verdade cobriria a tela de quem roda os testes
    return win


def textos(widget):
    """Todos os textos visíveis (rótulos e títulos de StatusPage) dentro do widget."""
    from gi.repository import Adw
    out, stack = [], [widget]
    while stack:
        w = stack.pop()
        if isinstance(w, Gtk.Label):
            out.append(w.get_label())
        if isinstance(w, Adw.StatusPage):
            out.append(w.get_title())
        child = w.get_first_child()
        while child:
            stack.append(child)
            child = child.get_next_sibling()
    return " | ".join(out)


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class CatalogoSemInternet(unittest.TestCase):
    """
    Regressão: sem internet e sem cópia salva do catálogo, a Loja ficava para sempre em
    "Carregando a loja…". A falha do download era ignorada, sem aviso nem como tentar de novo.
    """

    def test_mostra_erro_e_tentar_de_novo(self):
        win = janela()
        win.store.items = {}
        nome = "explorar" if "explorar" in win.builders else "loja"
        win.stack.set_visible_child_name(nome)
        win._on_catalog(False)
        texto = textos(win.builders[nome][0])
        self.assertNotIn("Carregando", texto)
        self.assertIn("Tentar de novo", texto)


def espera(cond, segundos=5):
    from gi.repository import GLib
    import time
    fim = time.time() + segundos
    ctx = GLib.MainContext.default()
    while not cond() and time.time() < fim:
        ctx.iteration(False)
        time.sleep(.01)
    return cond()


def tema(nome):
    d = A.USER_DATA / "themes" / nome
    (d / "gtk-3.0").mkdir(parents=True, exist_ok=True)
    (d / "gtk-3.0" / "gtk.css").write_text("@define-color theme_bg_color #202020;\n")
    return d


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class SelecionarVerAplicar(unittest.TestCase):
    """
    Pedido do usuário: clicar num cartão só seleciona e mostra a prévia; o sistema só muda no
    "Aplicar"; dá para desmarcar componentes e descartar; o que falhar é relatado sem esconder o
    que deu certo.
    """

    def setUp(self):
        tema("Fluxo-Um-Dark")
        tema("Fluxo-Dois-Dark")
        self.win = janela()
        self.iface = self.win.desk.iface
        self.iface.set_string("gtk-theme", "Fluxo-Um-Dark")
        self.win.go("temas")
        espera(lambda: not self.win.grids["temas"].loading)

    def test_clicar_seleciona_sem_aplicar(self):
        card = self.win.grids["temas"].cards["Fluxo-Dois-Dark"]
        card.emit("clicked")
        self.assertEqual(self.iface.get_string("gtk-theme"), "Fluxo-Um-Dark", "clicar não pode aplicar")
        self.assertTrue(card.check.get_visible(), "o cartão mostra que está selecionado")
        self.assertIn("1 alteração pendente", self.win.pending_label.get_label())
        self.assertIn("Fluxo Dois Dark", textos(self.win.detail))
        self.assertIn("Selecionado", textos(self.win.detail))
        self.assertTrue(self.win.grids["temas"].cards["Fluxo-Um-Dark"].badge.get_visible(), "o atual é 'Em uso'")

    def test_aplicar_muda_e_desfazer_volta(self):
        self.win.grids["temas"].cards["Fluxo-Dois-Dark"].emit("clicked")
        self.win.apply()
        self.assertTrue(espera(lambda: not self.win.applying))
        self.assertEqual(self.iface.get_string("gtk-theme"), "Fluxo-Dois-Dark")
        self.assertEqual(self.win.sel.changes, {})
        self.assertTrue(self.win.undo_btn.get_sensitive())
        self.win._undo()
        self.assertEqual(self.iface.get_string("gtk-theme"), "Fluxo-Um-Dark")

    def test_descartar(self):
        self.win.grids["temas"].cards["Fluxo-Dois-Dark"].emit("clicked")
        self.win.discard()
        self.assertEqual(self.win.sel.changes, {})
        self.assertFalse(self.win.apply_btn.get_sensitive())
        self.assertFalse(self.win.grids["temas"].cards["Fluxo-Dois-Dark"].check.get_visible())

    def test_desmarcado_nao_e_aplicado(self):
        self.win.grids["temas"].cards["Fluxo-Dois-Dark"].emit("clicked")
        self.win.sel.toggle("tema", False)
        self.win._refresh_chrome()
        self.assertFalse(self.win.apply_btn.get_sensitive())
        self.win.apply()
        self.assertEqual(self.iface.get_string("gtk-theme"), "Fluxo-Um-Dark")

    def test_falha_parcial_mostra_o_que_foi_e_o_que_nao_foi(self):
        self.win.grids["temas"].cards["Fluxo-Dois-Dark"].emit("clicked")
        self.win.sel.set(A.Change("cursor", "X", "X", lambda: None))
        del self.win.themes["Fluxo-Dois-Dark"]  # desinstalado entre escolher e aplicar
        relatos = []
        orig = A.inform
        A.inform = lambda _p, titulo, corpo: relatos.append((titulo, corpo))
        self.addCleanup(setattr, A, "inform", orig)
        self.win.apply()
        self.assertTrue(espera(lambda: not self.win.applying))
        self.assertEqual(relatos[0][0], "Parte das alterações não foi aplicada")
        self.assertIn("Aplicado: Cursor", relatos[0][1])
        self.assertIn("Tema: o tema Fluxo Dois Dark não está instalado", relatos[0][1])
        self.assertEqual(list(self.win.sel.changes), ["tema"])


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class NavegacaoELayout(unittest.TestCase):
    """
    Pedido do usuário: navegação lateral agrupada (Visual, Desktop, Biblioteca) no lugar das abas
    no topo; em janela estreita os detalhes abrem numa página própria em vez de espremer três
    colunas; aplicar e descartar ficam sempre acessíveis.
    """

    def test_lateral_com_todas_as_secoes(self):
        win = janela()
        grupos = {}
        for name, _t, grupo, *_ in win.PAGES:
            grupos.setdefault(grupo, []).append(name)
        self.assertEqual(grupos, {"Visual": ["estilos", "cores", "temas", "icones", "cursores", "wallpaper"],
                                  "Desktop": ["barra", "efeitos"], "Biblioteca": ["explorar", "meus"]})
        win.nav.select_row(win.rows["cursores"])
        self.assertEqual(win.page, "cursores")
        win.go("meus")
        self.assertIs(win.nav.get_selected_row(), win.rows["meus"], "a lateral acompanha a página")

    def test_detalhes_viram_pagina_em_janela_estreita(self):
        win = janela()
        self.assertEqual(win.mode_for(1300), "largo")
        self.assertEqual(win.mode_for(900), "medio")
        self.assertEqual(win.mode_for(500), "estreito")
        self.assertIs(win.detail_scroll.get_parent(), win.side_slot)
        win._set_mode("medio")
        self.assertIs(win.detail_scroll.get_parent(), win.page_slot)
        self.assertFalse(win.side_panel.get_visible())
        tema("Fluxo-Estreito-Dark")
        win.rebuild()
        win.go("temas")
        espera(lambda: not win.grids["temas"].loading)
        win.grids["temas"].cards["Fluxo-Estreito-Dark"].emit("clicked")
        self.assertEqual(win.view.get_visible_child_name(), "detalhe")
        win._close_detail()
        self.assertTrue(win.review_btn.get_visible(), "com o detalhe fechado, 'Revisar' fica na barra de baixo")
        win._set_mode("estreito")
        self.assertTrue(win.menu_btn.get_visible())
        self.assertFalse(win.sidebar.get_visible())
        win.menu_btn.set_active(True)
        self.assertFalse(win.main.get_visible(), "a lista de seções vira uma tela própria")
        win.nav.select_row(win.rows["cores"])
        self.assertTrue(win.main.get_visible())
        win._set_mode("largo")
        self.assertIs(win.detail_scroll.get_parent(), win.side_slot)


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class Acessibilidade(unittest.TestCase):
    """Estado não pode depender só de cor; movimento reduzido para o carregando."""

    def test_estado_escrito_no_cartao(self):
        grid = A.Grid(lambda k: None)
        card = grid.add("a", Gtk.Box(), "Tema A")
        grid.mark("a")
        grid.select({"a"})
        self.assertEqual(card.badge.get_label(), "Em uso")
        self.assertIn("selecionado", card.get_tooltip_text())
        self.assertIn("em uso", card.get_tooltip_text())

    def test_sem_animacao_quando_o_sistema_pede(self):
        s = Gtk.Settings.get_default()
        antes = s.props.gtk_enable_animations
        s.props.gtk_enable_animations = False
        self.addCleanup(setattr, s.props, "gtk_enable_animations", antes)
        sp = A.Spinner(16)
        sp.start()
        self.assertIsNone(sp._tick)

    def test_busca_sem_resultado(self):
        grid = A.Grid(lambda k: None)
        grid.add("a", Gtk.Box(), "Tema A")
        grid.set_query("zzz")
        self.assertEqual(grid.status.get_visible_child_name(), "sem-resultados")
        grid.set_query("")
        self.assertEqual(grid.status.get_visible_child_name(), "lista")


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class ExplorarSeparaInstalarDeAplicar(unittest.TestCase):
    """
    Pedido do usuário: no Explorar, instalar não aplica nada e o "Instalar tudo" deu lugar a
    marcar itens um por um.
    """

    def test_sem_instalar_tudo_e_com_marcar(self):
        win = janela()
        espera(lambda: not win.store_loading)  # o fetch de verdade termina antes (e não apaga os itens falsos)
        win.store.items = {"x": {"id": "x", "kind": "gtk", "title": "Tema X", "author": "Fulano", "license": "GPL-3.0",
                                 "source": "https://example.org", "dirs": ["TemaX"], "file": "x.tar.xz",
                                 "preview": "x.png", "bytes": 2_000_000, "sha256": "0"}}
        win.store_loading = False
        win.go("explorar")
        texto = textos(win.builders["explorar"][0])
        self.assertNotIn("Instalar tudo", texto)
        self.assertFalse(win.install_btn.get_sensitive())
        win.marked["x"].set_active(True)
        self.assertTrue(win.install_btn.get_sensitive())
        self.assertIn("1 · 2 MB", win.install_btn.get_label())
        win.grids["explorar"].cards["x"].emit("clicked")
        detalhe = textos(win.detail)
        for esperado in ("Fulano", "GPL-3.0", "Não instalado", "Nada muda no visual"):
            self.assertIn(esperado, detalhe)
        self.assertEqual(win.sel.changes, {}, "abrir um item do catálogo não seleciona nada para aplicar")


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class AppSegueOTemaAplicado(unittest.TestCase):
    """
    Regressão: depois de aplicar o Gruvbox, o próprio Adon Studio continuava com as cores do tema
    anterior. Apps GTK 4 só leem ~/.config/gtk-4.0/gtk.css quando abrem; o app gravava o arquivo
    para os outros apps e não o relia.
    """

    def test_rele_o_css_do_tema_depois_de_aplicar(self):
        pasta = tema("Segue-Dark")
        (pasta / "gtk-4.0").mkdir(exist_ok=True)
        (pasta / "gtk-4.0" / "gtk.css").write_text("@define-color window_bg_color #282828;\n"
                                                    "window { background-color: #282828; }\n")
        win = janela()
        win.go("temas")
        espera(lambda: not win.grids["temas"].loading)
        win.grids["temas"].cards["Segue-Dark"].emit("clicked")
        self.assertIsNone(getattr(win, "theme_css", None), "só selecionar não muda o app")
        win.apply()
        self.assertTrue(espera(lambda: not win.applying))
        self.assertIn("rgb(40,40,40)", win.theme_css.to_string())  # o GTK reescreve #282828


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class PainelDePreviaNaoEstica(unittest.TestCase):
    """
    Regressão: em tela cheia o painel de prévia ficava com metade da janela e o conteúdo dele
    boiando no meio. A rolagem do painel pedia para expandir e o GTK dividia a sobra com o catálogo.
    """

    def test_so_o_catalogo_expande(self):
        win = janela()
        self.assertFalse(win.side_panel.compute_expand(Gtk.Orientation.HORIZONTAL))
        self.assertTrue(win.view.compute_expand(Gtk.Orientation.HORIZONTAL))


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class RodapesAlinhados(unittest.TestCase):
    """
    Regressão: a linha acima do “Visual atual” (lateral) ficava mais alta que a linha acima da
    barra de Aplicar. As duas faixas de baixo tinham alturas diferentes, e quanto cada uma cresce
    depende do tema (o Gruvbox aumenta os títulos, por exemplo).
    """

    def test_mesma_altura(self):
        from gi.repository import Gdk
        css = Gtk.CssProvider()
        tema = A.CSS + ".heading { font-size: 22px; }\n"  # um tema com títulos maiores
        css.load_from_data(tema.encode()) if not hasattr(css, "load_from_string") else css.load_from_string(tema)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, 600)
        self.addCleanup(Gtk.StyleContext.remove_provider_for_display, Gdk.Display.get_default(), css)
        win = janela()
        alt = lambda w: w.measure(Gtk.Orientation.VERTICAL, -1)[:2]  # noqa: E731
        self.assertEqual(alt(win.footer), alt(win.pending_box))


class CortinaFalsa:
    def __init__(self):
        self.eventos = []

    def cover(self, bg, then, accent=None):
        self.eventos.append(("cobre", bg))
        then()

    def uncover(self):
        self.eventos.append(("descobre",))


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class CortinaAoAplicar(unittest.TestCase):
    """
    Pedido do usuário: ao aplicar um tema o sistema dá uma travadinha. A tela inteira fica coberta
    (cor sólida do fundo do tema novo, logo e carregando) enquanto o sistema troca, e só depois
    aparece o visual novo.
    """

    def setUp(self):
        tema("Cortina-Dark")
        self.win = janela()
        self.win.curtain = self.cortina = CortinaFalsa()
        self.iface = self.win.desk.iface

    def test_tema_aplica_por_tras_da_cortina(self):
        self.win.go("temas")
        espera(lambda: not self.win.grids["temas"].loading)
        self.win.grids["temas"].cards["Cortina-Dark"].emit("clicked")
        tema_durante = []
        orig = self.cortina.cover
        self.cortina.cover = lambda bg, then, accent=None: (orig(bg, then), tema_durante.append(self.iface.get_string("gtk-theme")))
        self.win.apply()
        self.assertTrue(espera(lambda: not self.win.applying))
        self.assertEqual(self.cortina.eventos[0][0], "cobre")
        self.assertEqual(self.cortina.eventos[-1], ("descobre",))
        self.assertEqual(tema_durante, ["Cortina-Dark"], "o tema muda com a cortina na frente")
        self.assertTrue(A.rgba(self.cortina.eventos[0][1]), "cor sólida do fundo do tema novo")

    def test_so_papel_de_parede_nao_cobre_a_tela(self):
        self.win.sel.set(A.Change("wallpaper", "x", "x", lambda: None))
        self.win._refresh_chrome()
        self.win.apply()
        self.assertTrue(espera(lambda: not self.win.applying))
        self.assertEqual(self.cortina.eventos, [])


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class CortinaSegueOTema(unittest.TestCase):
    """Pedido do usuário: num tema branco a cortina fica clara, com texto escuro (e o contrário)."""

    def test_texto_contrasta_com_o_fundo(self):
        for fundo, tinta in (("#fafafa", "#000000"), ("#1e1e2e", "#ffffff")):
            overlay, _spin = A.Curtain.content(fundo)
            self.assertIn(f'foreground="{tinta}"', self.markup(overlay), fundo)

    @staticmethod
    def markup(widget):
        stack = [widget]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Label) and "Aplicando" in w.get_label():
                return w.get_label()
            child = w.get_first_child()
            while child:
                stack.append(child)
                child = child.get_next_sibling()
        return ""


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class OutraCorDesmarcaAFavorita(unittest.TestCase):
    """
    Regressão: em Cores, com o Laranja marcado, escolher um rosa em “Outra…” gerava as paletas do
    rosa mas o botão do Laranja continuava destacado, como se ainda fosse a cor escolhida.
    """

    def test_nenhuma_favorita_marcada_com_cor_propria(self):
        win = janela()
        win.go("cores")
        win.color_buttons["#ff7800"].set_active(True)
        self.assertTrue(win.color_buttons["#ff7800"].get_active())
        win.color_btn.set_rgba(A.rgba("#dc8add"))
        self.assertEqual([h for h, b in win.color_buttons.items() if b.get_active()], [])
        win.color_buttons["#3584e4"].set_active(True)  # e voltar para uma favorita continua funcionando
        self.assertEqual([h for h, b in win.color_buttons.items() if b.get_active()], ["#3584e4"])


@unittest.skipUnless(TEM_TELA, "precisa de tela (rode numa sessão gráfica ou com GDK_BACKEND=broadway)")
class RespiroNasBordas(unittest.TestCase):
    """
    Regressão: com alguns temas GTK (que sobrescrevem o estilo .navigation-sidebar), os ícones da
    lateral e do “Visual atual” encostavam na borda da janela. O espaço vinha só do CSS do tema.
    """

    def test_margens_proprias_na_lateral_e_nos_rodapes(self):
        win = janela()
        self.assertGreaterEqual(win.nav.get_margin_start(), 6)
        conteudo = win.rows["estilos"].get_child()
        self.assertGreaterEqual(conteudo.get_margin_start(), 6)
        self.assertGreaterEqual(win.footer.get_margin_start(), 12)
        self.assertGreaterEqual(win.pending_box.get_margin_start(), 12)
