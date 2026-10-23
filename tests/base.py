# Isola os testes do computador de verdade: HOME e pastas XDG temporárias, e GSettings em memória.
# Precisa ser importado antes do adon_studio (os caminhos são calculados no import).
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_tmp = Path(tempfile.mkdtemp(prefix="adon-studio-teste-"))
for var, sub in (("HOME", "home"), ("XDG_DATA_HOME", "home/.local/share"),
                 ("XDG_CONFIG_HOME", "home/.config"), ("XDG_CACHE_HOME", "home/.cache")):
    os.environ[var] = str(_tmp / sub)
    (_tmp / sub).mkdir(parents=True, exist_ok=True)
os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["ADON_STUDIO_CATALOG"] = (_tmp / "loja" / "catalog.json").as_uri()
(_tmp / "loja").mkdir()

BLUR_PARTS = ("popup", "overview", "appfolder", "lockscreen", "panel", "applications")


def _blur_schemas():
    """Esquemas falsos da Blur my Shell: a versão nova (GNOME 45+, com 'pipelines') e a antiga
    (GNOME 42, com sigma/brightness), com o padrão da extensão de vir com a barra ligada."""
    pipe = ("{'pipeline_default': {'name': <'Default'>, 'effects': <[<{'type': <'dual_kawase_blur'>, "
            "'id': <'effect_000000000000'>, 'params': <{'unscaled_radius': <30>, 'brightness': <0.6>}>}>]>}}")
    out = []
    for base_id, modern in (("org.gnome.shell.extensions.blur-my-shell", True), ("org.adon.teste.blur-antigo", False)):
        path = "/" + base_id.replace(".", "/") + "/"
        root = (f'<key name="pipelines" type="a{{sa{{sv}}}}"><default><![CDATA[{pipe}]]></default></key>' if modern else
                '<key name="sigma" type="i"><default>30</default></key>'
                '<key name="brightness" type="d"><default>0.6</default></key>')
        out.append(f'<schema id="{base_id}" path="{path}">{root}</schema>')
        for part in BLUR_PARTS:
            keys = f'<key name="blur" type="b"><default>{"true" if part != "applications" else "false"}</default></key>'
            keys += ('<key name="pipeline" type="s"><default>"pipeline_default"</default></key>' if modern else
                     '<key name="customize" type="b"><default>true</default></key>')
            if part == "applications":
                keys += ('<key name="enable-all" type="b"><default>false</default></key>'
                         '<key name="opacity" type="i"><default>215</default></key>'
                         '<key name="whitelist" type="as"><default>[]</default></key>'
                         '<key name="dynamic-opacity" type="b"><default>true</default></key>')
            out.append(f'<schema id="{base_id}.{part}" path="{path}{part}/">{keys}</schema>')
    return "\n".join(out) + "\n"


# esquema falso da barra (o mesmo formato do Dash to Panel / barra do Zorin) para testar sem a extensão
_schemas = _tmp / "schemas"
_schemas.mkdir()
(_schemas / "teste.gschema.xml").write_text("""<schemalist>
  <schema id="org.gnome.shell.extensions.dash-to-panel" path="/org/gnome/shell/extensions/dash-to-panel/">
    <key name="panel-lengths" type="s"><default>'{}'</default></key>
    <key name="panel-anchors" type="s"><default>'{}'</default></key>
    <key name="panel-sizes" type="s"><default>'{}'</default></key>
    <key name="panel-positions" type="s"><default>'{}'</default></key>
    <key name="panel-margin" type="i"><default>0</default></key>
    <key name="global-border-radius" type="i"><default>0</default></key>
    <key name="trans-use-custom-opacity" type="b"><default>false</default></key>
    <key name="trans-panel-opacity" type="d"><default>0.4</default></key>
    <key name="intellihide" type="b"><default>false</default></key>
  </schema>
""" + _blur_schemas() + "</schemalist>\n")
os.system(f"glib-compile-schemas {_schemas}")
os.environ["GSETTINGS_SCHEMA_DIR"] = str(_schemas)

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

TMP = _tmp
HOME = _tmp / "home"
LOJA = _tmp / "loja"
