#!/bin/bash
# Gera dist/adon-studio_<versão>_all.deb. Uso: packaging/build-deb.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ID=com.adonay.studio
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/adon_studio.py")
PKG="$ROOT/dist/deb/adon-studio_${VERSION}_all"

rm -rf "$PKG"
install -Dm644 "$ROOT/adon_studio.py" "$PKG/usr/share/adon-studio/adon_studio.py"
install -Dm644 "$ROOT/icons/hicolor/scalable/apps/$ID.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/$ID.svg"
install -Dm644 "$ROOT/packaging/$ID.metainfo.xml" "$PKG/usr/share/metainfo/$ID.metainfo.xml"
install -Dm644 "$ROOT/LICENSE" "$PKG/usr/share/doc/adon-studio/copyright"
install -d "$PKG/usr/bin" "$PKG/usr/share/applications" "$PKG/DEBIAN"
printf '#!/bin/sh\nexec python3 /usr/share/adon-studio/adon_studio.py "$@"\n' > "$PKG/usr/bin/adon-studio"
chmod 755 "$PKG/usr/bin/adon-studio"

cat > "$PKG/usr/share/applications/$ID.desktop" <<EOF
[Desktop Entry]
Name=Adon Studio
Comment=Temas, ícones, cursores, wallpapers e barra com um clique
Exec=adon-studio
Icon=$ID
Terminal=false
Type=Application
Categories=Settings;DesktopSettings;GTK;
Keywords=tema;theme;ícones;icons;cursor;wallpaper;aparência;appearance;
StartupWMClass=$ID
EOF

cat > "$PKG/DEBIAN/control" <<EOF
Package: adon-studio
Version: $VERSION
Section: gnome
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi (>= 3.42), python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, gnome-shell-extensions, librsvg2-common
Recommends: gnome-shell
Maintainer: Adonay <oh-dony@users.noreply.github.com>
Homepage: https://github.com/oh-dony/adon-studio
Installed-Size: $(du -sk "$PKG/usr" | cut -f1)
Description: Deixe seu Linux com a sua cara em poucos cliques
 Troque o tema, os ícones, o cursor, o wallpaper e a barra de tarefas do
 GNOME vendo uma prévia de cada opção, sem abrir o terminal. Inclui uma loja
 com temas, ícones e cursores instalados com um clique e um botão para voltar
 ao visual original.
EOF

dpkg-deb --root-owner-group --build "$PKG" "$ROOT/dist/adon-studio_${VERSION}_all.deb" >/dev/null
echo "$ROOT/dist/adon-studio_${VERSION}_all.deb"
