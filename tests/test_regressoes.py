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


class FakeBus:
    """Imita o D-Bus da sessão com um Nautilus rodando (ou não) e N janelas abertas."""

    def __init__(self, running, windows=0):
        self.running, self.windows, self.calls = running, windows, []

    def call_sync(self, name, path, iface, method, args, *_rest):
        self.calls.append(method if method != "Activate" else f"{iface}.{args.unpack()[0] if args.n_children() > 1 else ''}")
        if method == "NameHasOwner":
            return A.GLib.Variant("(b)", (self.running,))
        if method == "Introspect":
            nodes = "".join(f'<node name="{i}"/>' for i in range(1, self.windows + 1))
            return A.GLib.Variant("(s)", (f"<node>{nodes}</node>",))
        if method == "Activate" and iface == "org.gtk.Actions":
            self.running = False
        return None

    def call(self, name, path, iface, method, *_rest):
        self.calls.append(f"{iface}.{method}")


class NautilusPegaOTema(unittest.TestCase):
    """
    Regressão: ao escolher um combo (ou tema), o Nautilus continuava cinza, da cor do Zorin. Ele
    fica rodando em segundo plano o tempo todo (--gapplication-service) e um app GTK4 só lê
    ~/.config/gtk-4.0/gtk.css quando abre; o arquivo era trocado, mas o Nautilus nunca relia.
    """

    def test_aplicar_tema_recarrega_o_nautilus(self):
        calls = []
        orig = A.reload_files_app
        A.reload_files_app = lambda *a, **k: calls.append(1)
        try:
            theme = A.USER_DATA / "themes" / "Recarrega"
            mkdirs(theme / "gtk-4.0", theme / "gtk-3.0")
            (theme / "gtk-4.0" / "gtk.css").write_text("")
            A.Desktop().apply_gtk(A.Theme("Recarrega", theme))
        finally:
            A.reload_files_app = orig
        self.assertEqual(calls, [1])

    def test_fecha_e_reabre_quando_tinha_janela(self):
        bus = FakeBus(running=True, windows=2)
        self.assertTrue(A.reload_files_app(bus, wait=0))
        self.assertIn("org.gtk.Actions.kill", bus.calls, "quit só fecha as janelas; o serviço continua")
        self.assertEqual(bus.calls[-1], "org.freedesktop.Application.Activate", "reabre uma janela")

    def test_so_fecha_o_servico_quando_nao_tinha_janela(self):
        bus = FakeBus(running=True, windows=0)
        A.reload_files_app(bus, wait=0)
        self.assertIn("org.gtk.Actions.kill", bus.calls, "quit só fecha as janelas; o serviço continua")
        self.assertNotIn("org.freedesktop.Application.Activate", bus.calls)

    def test_nautilus_fechado_nao_e_aberto(self):
        bus = FakeBus(running=False)
        self.assertFalse(A.reload_files_app(bus, wait=0))
        self.assertEqual(bus.calls, ["NameHasOwner"])

    def test_sem_dbus_nao_quebra(self):
        self.assertFalse(A.reload_files_app(None))



class ShellFalso:
    """D-Bus com um GNOME Shell que conhece (ou não) a extensão User Themes."""

    def __init__(self, knows):
        self.knows, self.calls = knows, []

    def call_sync(self, name, path, iface, method, args, *_rest):
        self.calls.append(method)
        if method == "GetExtensionInfo":
            info = {"state": A.GLib.Variant("d", 1.0)} if self.knows else {}
            return A.GLib.Variant("(a{sv})", (info,))
        if method == "EnableExtension":
            return A.GLib.Variant("(b)", (self.knows,))
        return None


class PainelNumSistemaNovo(unittest.TestCase):
    """
    Regressão: num Ubuntu 24.04 recém-instalado (teste ./studio gnome), o combo mudava janelas,
    ícones, cursor e wallpaper, mas o painel continuava com o tema padrão, e o app nem avisava.
    O .deb traz a extensão User Themes, mas o GNOME Shell já aberto não enxerga extensão
    instalada depois do login; o app pedia para ligá-la, o Shell respondia "não" e o app
    ignorava a resposta. Agora, se o Shell não a conhece, o app pede para ele instalá-la (o
    Shell carrega na hora, sem sair da sessão).
    """

    def setUp(self):
        self.ext = A.USER_DATA / "gnome-shell" / "extensions" / A.USER_THEME_UUID
        self.ext.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.ext, ignore_errors=True)

    def test_shell_que_responde_nao_nao_conta_como_ligada(self):
        self.assertFalse(A.enable_extension(A.USER_THEME_UUID, bus=ShellFalso(knows=False)))

    def test_extensao_que_o_shell_nao_conhece_precisa_ser_carregada(self):
        self.assertEqual(A.Desktop().ensure_user_theme(bus=ShellFalso(knows=False)), "carregar")

    def test_extensao_carregada_fica_pronta(self):
        self.assertEqual(A.Desktop().ensure_user_theme(bus=ShellFalso(knows=True)), "ok")

    def test_sem_a_extensao_no_disco(self):
        orig = A.extension_installed
        A.extension_installed = lambda uuid: False  # o computador de teste pode tê-la em /usr/share
        try:
            self.assertEqual(A.Desktop().ensure_user_theme(bus=ShellFalso(knows=True)), "falta")
        finally:
            A.extension_installed = orig

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


class TemaClaroSemLightNoNome(unittest.TestCase):
    """
    Regressão: aplicar o Adwaita (ou o Yaru) deixava os apps GTK 4 no modo escuro e os GTK 3
    claros, e a prévia o mostrava como tema escuro. O claro/escuro era deduzido pela palavra
    "light" no nome; o CSS do Adwaita não tem cores para ler ("Adwaita is now part of GTK+ 3").
    """

    def test_adwaita_e_claro(self):
        pasta = A.USER_DATA / "themes" / "Adwaita"
        (pasta / "gtk-3.0").mkdir(parents=True, exist_ok=True)
        (pasta / "gtk-3.0" / "gtk.css").write_text("/* Adwaita is now part of GTK+ 3 */\n")
        tema = A.Theme("Adwaita", pasta)
        self.assertFalse(A.is_dark(A.theme_palette(tema)))
        d = A.Desktop()
        d.apply_gtk(tema)
        self.assertEqual(d.iface.get_string("color-scheme"), "prefer-light")

    def test_escuro_pelo_nome_continua_escuro(self):
        pasta = A.USER_DATA / "themes" / "Qualquer-Dark"
        (pasta / "gtk-3.0").mkdir(parents=True, exist_ok=True)
        (pasta / "gtk-3.0" / "gtk.css").write_text("/* sem cores */\n")
        self.assertTrue(A.is_dark(A.theme_palette(A.Theme("Qualquer-Dark", pasta))))


if __name__ == "__main__":
    unittest.main()


class TemasOcultosForaDaLista(unittest.TestCase):
    """
    Regressão: os temas “Adon Pastas …” (gerados pelo app para pintar as pastas com a paleta)
    apareciam na seção Ícones como se fossem temas para escolher. Eles são marcados Hidden=true.
    """

    def test_tema_de_icones_oculto_nao_aparece(self):
        pasta = A.USER_DATA / "icons" / "Oculto-Teste"
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "index.theme").write_text("[Icon Theme]\nName=Oculto\nHidden=true\nDirectories=a\n")
        visivel = A.USER_DATA / "icons" / "Visivel-Teste"
        visivel.mkdir(parents=True, exist_ok=True)
        (visivel / "index.theme").write_text("[Icon Theme]\nName=Visivel\nDirectories=a\n")
        temas = A.list_icon_themes()
        self.assertNotIn("Oculto-Teste", temas)
        self.assertIn("Visivel-Teste", temas)


def tema_icones(nome, dirs, arquivos, inherits=None):
    """dirs: {pasta: entradas do index.theme}; arquivos: caminhos relativos a criar."""
    d = A.USER_DATA / "icons" / nome
    d.mkdir(parents=True, exist_ok=True)
    idx = f"[Icon Theme]\nName={nome}\nDirectories={','.join(dirs)}\n"
    if inherits:
        idx += f"Inherits={inherits}\n"
    for pasta, chaves in dirs.items():
        idx += f"\n[{pasta}]\n" + "".join(f"{k}={v}\n" for k, v in chaves.items())
    (d / "index.theme").write_text(idx)
    for f in arquivos:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text("<svg/>")
    return d


class BuscaDeIconesSemGtk(unittest.TestCase):
    """
    Regressão (teste de carga): a aba Ícones deixava o app com +260 MB e travava a janela. Cada
    prévia carregava o tema com um Gtk.IconTheme, e o GTK não devolve a memória ao soltar o tema
    (~3 MB por tema, 7 MB no Papirus, a cada vez). A prévia agora acha os arquivos seguindo a
    especificação de temas de ícones (freedesktop), só conferindo se o arquivo existe.
    """

    @classmethod
    def setUpClass(cls):
        cls.pai = tema_icones("Busca-Pai", {"32x32/places": {"Size": 32, "Type": "Fixed"},
                                            "scalable/apps": {"Size": 64, "Type": "Scalable", "MinSize": 8,
                                                              "MaxSize": 512}},
                              ["32x32/places/folder.png", "scalable/apps/utilities-terminal.svg"])
        cls.filho = tema_icones("Busca-Filho", {"16x16/places": {"Size": 16, "Type": "Fixed"},
                                                "48x48/places": {"Size": 48, "Type": "Fixed"},
                                                "24/places": {"Size": 24}},
                                ["16x16/places/folder.png", "48x48/places/folder.png", "24/places/user-home.svg"],
                                inherits="Busca-Pai")

    def test_tamanho_exato_e_depois_o_mais_proximo_no_proprio_tema(self):
        self.assertEqual(A.find_icon("Busca-Pai", ["folder"], 32), str(self.pai / "32x32/places/folder.png"))
        # o tema escolhido vem antes do pai, mesmo sem o tamanho exato
        self.assertEqual(A.find_icon("Busca-Filho", ["folder"], 40), str(self.filho / "48x48/places/folder.png"))
        self.assertEqual(A.find_icon("Busca-Filho", ["folder"], 18), str(self.filho / "16x16/places/folder.png"))

    def test_limiar_padrao_e_escalavel(self):
        # Type padrão é Threshold (limiar 2): 24 serve para 22..26
        self.assertEqual(A.find_icon("Busca-Filho", ["user-home"], 26), str(self.filho / "24/places/user-home.svg"))
        self.assertEqual(A.find_icon("Busca-Pai", ["utilities-terminal"], 300),
                         str(self.pai / "scalable/apps/utilities-terminal.svg"))

    def test_nomes_alternativos_e_tema_pai(self):
        self.assertEqual(A.find_icon("Busca-Filho", ["nao-existe", "utilities-terminal"], 32),
                         str(self.pai / "scalable/apps/utilities-terminal.svg"))

    def test_sem_icone_e_sem_tema(self):
        self.assertIsNone(A.find_icon("Busca-Filho", ["nada-disso"], 32))
        self.assertIsNone(A.find_icon("Tema-Que-Nao-Existe", ["folder"], 32))

    def test_heranca_em_ciclo_nao_trava(self):
        tema_icones("Ciclo-A", {"a": {"Size": 32}}, [], inherits="Ciclo-B")
        tema_icones("Ciclo-B", {"a": {"Size": 32}}, [], inherits="Ciclo-A")
        self.assertIsNone(A.find_icon("Ciclo-A", ["folder"], 32))


class MemoriaDasMiniaturas(unittest.TestCase):
    """
    Regressão (teste de carga): abrir a aba Papel de parede com 80 imagens deixava o app com
    +150 MB, para 5 MB de miniaturas. Cada imagem grande é decodificada inteira antes de reduzir;
    o glibc sobe sozinho o limite de mmap depois do primeiro buffer grande e passa a guardar os
    seguintes no heap das threads, sem devolver ao sistema.
    """

    def test_decodificar_imagens_grandes_nao_retem_memoria(self):
        import subprocess
        import sys
        codigo = f"""
import sys; sys.path[:0] = {[str(base.ROOT / 'tests'), str(base.ROOT)]!r}
import base, adon_studio as A
from gi.repository import GdkPixbuf
f = base.TMP / 'grande.png'
GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 3000, 2000).savev(str(f), 'png', [], [])
def anon():
    return int([l for l in open('/proc/self/status') if l.startswith('RssAnon')][0].split()[1]) // 1024
antes = anon()
list(A.POOL.map(lambda _i: A.thumb_texture(f, 230, 130), range(40)))
print(anon() - antes)
"""
        out = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertLess(int(out.stdout.split()[-1]), 60, "MB presos depois de reduzir 40 imagens 3000×2000")


class TelaNaoEsperaAsThreads(unittest.TestCase):
    """
    Regressão (teste de carga): com uma thread de fundo ocupada (achar ícones, ler o catálogo),
    300 chamadas ao GTK levavam 143 ms em vez de 0,1 ms e cliques travavam a janela. Cada
    chamada ao GTK solta o GIL e precisa pegá-lo de volta; com o intervalo padrão do Python (5 ms)
    a tela principal esperava a thread de fundo a cada chamada.
    """

    def test_intervalo_do_gil_curto(self):
        import sys
        self.assertLessEqual(sys.getswitchinterval(), 0.0005)


class MiniaturasDoSistema(unittest.TestCase):
    """
    Regressão (teste de carga): a aba Papel de parede decodificava cada foto inteira toda vez que
    abria (o JPEG não sai reduzido: uma foto 6144×4292 ocupa 100 MB por um instante, quatro ao
    mesmo tempo davam picos de ~350 MB). Agora usa a miniatura do padrão freedesktop em
    ~/.cache/thumbnails (a mesma que o app Arquivos já criou) e cria a que faltar.
    """

    def foto(self, nome, w=2000, h=1000):
        from gi.repository import GdkPixbuf
        f = base.TMP / nome
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
        pb.fill(0x2040a0ff)
        pb.savev(str(f), "jpeg", [], [])
        return f

    def miniatura(self, f):
        import hashlib
        from gi.repository import Gio
        uri = Gio.File.new_for_path(str(f)).get_uri()
        return A.THUMBS / "large" / (hashlib.md5(uri.encode()).hexdigest() + ".png"), uri

    def test_cria_a_miniatura_no_padrao(self):
        from gi.repository import GdkPixbuf
        f = self.foto("nova.jpg")
        tex = A.thumb_texture(f, 230, 130)
        self.assertLessEqual(tex.get_width(), 230)
        thumb, uri = self.miniatura(f)
        self.assertTrue(thumb.exists(), "miniatura gravada em ~/.cache/thumbnails/large")
        pb = GdkPixbuf.Pixbuf.new_from_file(str(thumb))
        self.assertEqual(max(pb.get_width(), pb.get_height()), 256)
        self.assertEqual(pb.get_option("tEXt::Thumb::URI"), uri)
        self.assertEqual(pb.get_option("tEXt::Thumb::MTime"), str(int(f.stat().st_mtime)))

    def test_usa_a_miniatura_que_ja_existe_sem_abrir_a_foto(self):
        import os
        from gi.repository import GdkPixbuf
        f = self.foto("ja-tem.jpg")
        thumb, uri = self.miniatura(f)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        vermelha = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 256, 128)
        vermelha.fill(0xff0000ff)
        vermelha.savev(str(thumb), "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime"],
                       [uri, str(int(f.stat().st_mtime))])
        original = GdkPixbuf.Pixbuf.new_from_file_at_scale

        def proibido(*_a):
            raise AssertionError("abriu a foto original")
        GdkPixbuf.Pixbuf.new_from_file_at_scale = proibido
        try:
            tex = A.thumb_texture(f, 230, 130)
        finally:
            GdkPixbuf.Pixbuf.new_from_file_at_scale = original
        self.assertEqual(tex.get_width(), 230)
        # miniatura velha (a foto mudou depois): faz de novo a partir da foto
        os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 10))
        A.thumb_texture(f, 230, 130)
        pb = GdkPixbuf.Pixbuf.new_from_file(str(thumb))
        self.assertEqual(pb.get_option("tEXt::Thumb::MTime"), str(int(f.stat().st_mtime)))
        self.assertNotEqual(pb.get_pixels()[:3], b"\xff\x00\x00", "trocou a miniatura velha")

    def test_imagem_pequena_nao_ganha_miniatura(self):
        f = self.foto("pequena.jpg", 400, 200)
        A.thumb_texture(f, 230, 130)
        self.assertFalse(self.miniatura(f)[0].exists())
