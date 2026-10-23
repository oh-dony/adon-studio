#!/usr/bin/env python3
# Gera os pacotes da Loja (tar.xz + miniatura) e o catalog.json a partir de sources.json.
# Uso: python3 packaging/build_bundles.py [id ...]
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import cairo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import adon_studio as A  # noqa: E402
from gi.repository import GdkPixbuf, GLib  # noqa: E402

OUT = ROOT / "dist" / "bundles"
W, H = 480, 270
BG = "#1e1e2e"


def surface():
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    cr = cairo.Context(s)
    A._src(cr, A.rgba(BG))
    cr.paint()
    return s, cr


def preview_gtk(path):
    s, cr = surface()
    A.draw_window(cr, 24, 22, W - 48, H - 48, A.theme_palette(A.Theme(path.name, path)))
    return s


def icon_index(theme_dir):
    """nome -> arquivo, preferindo SVG e tamanhos maiores."""
    best = {}
    for f in theme_dir.rglob("*"):
        if f.suffix not in (".svg", ".png") or "symbolic" in f.name or not f.exists():
            continue
        rank = (f.suffix == ".svg", "scalable" in str(f) or "64" in str(f) or "48" in str(f))
        if rank > best.get(f.stem, ((False, False), None))[0]:
            best[f.stem] = (rank, f)
    return {k: v[1] for k, v in best.items()}


def preview_icons(path, fallbacks):
    s, cr = surface()
    indexes = [icon_index(path)] + [icon_index(f) for f in fallbacks if f.is_dir()]
    size, gap = 72, 16
    picks = []
    for names in A.ICON_SAMPLES[:6]:
        f = next((ix[n] for ix in indexes for n in names if n in ix), None)
        if f:
            picks.append(f)
    x = (W - len(picks) * size - (len(picks) - 1) * gap) / 2
    for i, f in enumerate(picks):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(f), size, size, True)
        except GLib.Error:
            continue
        paint_pixbuf(cr, pb, x + i * (size + gap), (H - size) / 2)
    return s


def paint_pixbuf(cr, pb, x, y):
    tmp = Path(OUT / ".tmp.png")
    pb.savev(str(tmp), "png", [], [])
    img = cairo.ImageSurface.create_from_png(str(tmp))
    cr.set_source_surface(img, x, y)
    cr.paint()
    tmp.unlink()


def preview_cursors(path):
    s, cr = surface()
    imgs = []
    for names in A.CURSOR_SAMPLES:
        f = next((path / "cursors" / n for n in names if (path / "cursors" / n).exists()), None)
        if f:
            img = xcursor_surface(f.resolve(), 48)
            if img:
                imgs.append(img)
    gap = 30
    total = sum(i.get_width() for i in imgs) + gap * (len(imgs) - 1)
    x = (W - total) / 2
    for img in imgs:
        cr.set_source_surface(img, x, (H - img.get_height()) / 2)
        cr.paint()
        x += img.get_width() + gap
    return s


def xcursor_surface(path, want):
    import struct
    data = path.read_bytes()
    hsize, _v, ntoc = struct.unpack_from("<III", data, 4)
    imgs = [struct.unpack_from("<III", data, hsize + i * 12) for i in range(ntoc)]
    imgs = [t for t in imgs if t[0] == 0xFFFD0002]
    if not imgs:
        return None
    pos = min(imgs, key=lambda t: abs(t[1] - want))[2]
    w, h = struct.unpack_from("<II", data, pos + 16)
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    stride = s.get_stride()
    buf = s.get_data()
    px = data[pos + 36:pos + 36 + w * h * 4]
    for row in range(h):
        buf[row * stride:row * stride + w * 4] = px[row * w * 4:(row + 1) * w * 4]
    s.mark_dirty()
    return s


def sha256(f):
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_links(archive, item, base):
    """Descompacta numa pasta vazia e compara os atalhos quebrados com os da origem: se houver mais,
    faltou alguma pasta no pacote (defeitos que já vêm do tema original são tolerados)."""
    import tempfile

    def broken(root, dirs):
        out = subprocess.run(["find", *[str(Path(root) / d) for d in dirs], "-xtype", "l"],
                             capture_output=True, text=True).stdout.split()
        return {o.split(str(root), 1)[1] for o in out}
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["tar", "-xJf", str(archive), "-C", tmp], check=True)
        new = broken(tmp, item["dirs"]) - broken(base, item["dirs"])
    if new:
        archive.unlink()
        raise SystemExit(f"{item['id']}: {len(new)} atalhos quebrados que não existem na origem, ex. {sorted(new)[0]}")


def build(item):
    base = Path(os.path.expanduser(item["from"]))
    missing = [d for d in item["dirs"] if not (base / d).is_dir()]
    if missing:
        raise SystemExit(f"{item['id']}: pastas não encontradas: {missing}")
    archive = OUT / f"{item['id']}.tar.xz"
    if not archive.exists():
        excl = [f"--exclude={e}" for e in item.get("exclude", [])]
        tar = subprocess.Popen(["tar", "-C", str(base), *excl, "-cf", "-", *item["dirs"]], stdout=subprocess.PIPE)
        with open(archive, "wb") as out:
            subprocess.run(["xz", "-T0", "-9", "-c"], stdin=tar.stdout, stdout=out, check=True)
        if tar.wait():
            raise SystemExit(f"{item['id']}: tar falhou")
    check_links(archive, item, base)
    first = base / item["dirs"][0]
    if item["kind"] == "gtk":
        img = preview_gtk(first)
    elif item["kind"] == "icons":
        img = preview_icons(first, [base / "Papirus-Dark", Path("/usr/share/icons/Adwaita")])
    else:
        img = preview_cursors(first)
    img.write_to_png(str(OUT / f"{item['id']}.png"))
    entry = {k: v for k, v in item.items() if k not in ("from", "exclude")}
    entry.update(file=archive.name, preview=f"{item['id']}.png", bytes=archive.stat().st_size, sha256=sha256(archive))
    print(f"{item['id']:22} {archive.stat().st_size / 1e6:6.1f} MB")
    return entry


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    items = json.loads((Path(__file__).parent / "sources.json").read_text())["items"]
    only = set(sys.argv[1:])
    catalog = OUT / "catalog.json"
    old = {e["id"]: e for e in json.loads(catalog.read_text())["items"]} if catalog.exists() else {}
    entries = [build(i) if (not only or i["id"] in only) else old.get(i["id"]) or build(i) for i in items]
    catalog.write_text(json.dumps({"version": 1, "items": entries}, indent=1, ensure_ascii=False))
    print(f"\n{len(entries)} pacotes, {sum(e['bytes'] for e in entries) / 1e6:.0f} MB no total")


if __name__ == "__main__":
    main()
