import base  # noqa: F401  (isola HOME/GSettings antes do import do app)

import shutil
import unittest

import adon_studio as A


def reset(blur):
    for s in [blur.root, *blur.parts.values()]:
        for k in s.props.settings_schema.list_keys():
            s.reset(k)


class VidroNoZorin(unittest.TestCase):
    """
    Regressão: ao instalar a Blur my Shell no Zorin, aparecia um "quadrado estranho" no topo da
    tela. A extensão já vem com o vidro da barra superior ligado, mas no Zorin essa barra é
    escondida (a barra de tarefas do Zorin é outra), e o vidro fica desenhado solto.
    """

    def setUp(self):
        self.blur = A.Blur()
        reset(self.blur)

    def test_desliga_o_vidro_da_barra_superior_no_zorin(self):
        self.assertTrue(self.blur.get("panel"), "pré-condição: a extensão vem com a barra ligada")
        self.blur.adapt(zorin=True)
        self.assertFalse(self.blur.get("panel"))
        self.assertTrue(self.blur.get("popup"), "o resto do vidro continua")

    def test_fora_do_zorin_mantem_a_barra(self):
        self.blur.adapt(zorin=False)
        self.assertTrue(self.blur.get("panel"))

    def test_preset_no_zorin_nao_liga_a_barra(self):
        self.blur.apply_preset(A.BLUR_PRESETS["Vidro forte"], zorin=True)
        self.assertFalse(self.blur.get("panel"))


class IntensidadeDoVidro(unittest.TestCase):
    """
    A intensidade fica em lugares diferentes conforme a versão da Blur my Shell: na nova
    (GNOME 45+) dentro de um 'pipeline'; na antiga (GNOME 42, Zorin 17) em sigma/brightness.
    """

    def test_versao_nova_cria_pipeline_proprio_e_preserva_o_padrao(self):
        blur = A.Blur()
        reset(blur)
        blur.set_strength(45, 0.7)
        pipes = blur.root.get_value("pipelines").unpack()
        self.assertIn("pipeline_default", pipes, "não pode apagar os pipelines da pessoa")
        self.assertEqual(blur.strength(), (45, 0.7))
        self.assertEqual(blur.parts["popup"].get_string("pipeline"), A.ADON_PIPELINE)

    def test_versao_antiga_usa_sigma(self):
        blur = A.Blur("org.adon.teste.blur-antigo")
        reset(blur)
        blur.set_strength(12, 0.9)
        self.assertEqual(blur.root.get_int("sigma"), 12)
        self.assertEqual(blur.strength(), (12, 0.9))
        self.assertFalse(blur.parts["overview"].get_boolean("customize"))


class JanelasDeVidro(unittest.TestCase):
    """
    Pedido do usuário: vidro só no gerenciador de arquivos (Nautilus), além do modo "todas as
    janelas". O modo só Arquivos usa a lista de apps da extensão em vez de ligar tudo.
    """

    def setUp(self):
        self.blur = A.Blur()
        reset(self.blur)
        self.apps = self.blur.parts["applications"]

    def test_todas_as_janelas(self):
        self.blur.set_windows("todas")
        self.assertTrue(self.apps.get_boolean("blur"))
        self.assertTrue(self.apps.get_boolean("enable-all"))
        self.assertEqual(self.blur.windows(), "todas")

    def test_so_o_nautilus(self):
        self.blur.set_windows("arquivos")
        self.assertTrue(self.apps.get_boolean("blur"))
        self.assertFalse(self.apps.get_boolean("enable-all"))
        self.assertIn("org.gnome.Nautilus", self.apps.get_strv("whitelist"))
        self.assertEqual(self.blur.windows(), "arquivos")

    def test_janela_em_foco_tambem_fica_de_vidro(self):
        """
        Regressão: com o modo "só Arquivos" ligado, o Nautilus continuava opaco. A extensão vem
        com 'dynamic-opacity' ligado, que deixa opaca a janela em foco — e a janela que você
        acabou de abrir é sempre a que está em foco.
        """
        self.blur.set_windows("arquivos")
        self.assertFalse(self.apps.get_boolean("dynamic-opacity"))

    def test_vidro_nas_janelas_comeca_visivel(self):
        """Regressão: o vidro no Nautilus ficava "bem fraco" — a extensão usa 84% de opacidade."""
        self.blur.set_windows("arquivos")
        self.assertLessEqual(self.apps.get_int("opacity"), A.GLASS_OPACITY)

    def test_nao_mexe_na_opacidade_que_a_pessoa_escolheu(self):
        self.blur.set_windows("arquivos")
        self.apps.set_int("opacity", 240)
        self.blur.set_windows("todas")
        self.assertEqual(self.apps.get_int("opacity"), 240)

    def test_desligar_preserva_outros_apps_da_lista(self):
        self.apps.set_strv("whitelist", ["firefox"])
        self.blur.set_windows("arquivos")
        self.blur.set_windows("nenhuma")
        self.assertFalse(self.apps.get_boolean("blur"))
        self.assertEqual(self.apps.get_strv("whitelist"), ["firefox"])


class RestaurarDesligaVidro(unittest.TestCase):
    """
    Prevenção: o botão de restaurar volta ao visual de antes do primeiro uso. Se não havia vidro
    naquela hora, o vidro instalado depois precisa ser desligado.
    """

    def test_restaurar_desliga_vidro_que_nao_existia(self):
        shutil.rmtree(A.BACKUP, ignore_errors=True)
        desk = A.Desktop()
        self._orig = A.Blur.installed
        A.Blur.installed = staticmethod(lambda: False)
        try:
            desk.ensure_backup()  # backup feito sem a extensão
        finally:
            A.Blur.installed = self._orig
        blur = A.Blur()
        reset(blur)
        desk.restore()
        self.assertFalse(any(blur.get(p) for p in blur.parts))


if __name__ == "__main__":
    unittest.main()
