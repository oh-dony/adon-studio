#!/bin/bash
# Instala o .deb num Ubuntu limpo (Docker) e roda o teste de fumaça sem tela (GTK Broadway).
# Uso: packaging/test/run-docker.sh <imagem, ex. ubuntu:22.04> <arquivo .deb> <pasta com catalog.json>
# CATALOG=<url> testa contra outro catálogo (ex. o publicado no GitHub) em vez do servidor local.
set -e
IMG=$1 DEB=$(realpath "$2") BUNDLES=$(realpath "$3")
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/out/${IMG//[:\/]/-}"
rm -rf "$OUT" && mkdir -p "$OUT"

# servidor local fazendo papel das Releases do GitHub
python3 -m http.server 8765 --bind 127.0.0.1 --directory "$BUNDLES" >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER' EXIT

docker run --rm --network host -e CATALOG="${CATALOG:-http://127.0.0.1:8765/catalog.json}" -v "$DEB:/tmp/app.deb:ro" -v "$HERE:/test:ro" -v "$OUT:/out" "$IMG" bash -c '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  apt-get install -y -qq /tmp/app.deb >/tmp/apt.log 2>&1 || { tail -20 /tmp/apt.log; exit 1; }
  echo "== instalado: $(dpkg -s adon-studio | grep ^Version)"
  echo "== libadwaita: $(dpkg -s gir1.2-adw-1 | grep ^Version)  gtk4: $(dpkg -s gir1.2-gtk-4.0 | grep ^Version)"
  test -x /usr/bin/adon-studio && test -f /usr/share/applications/com.adonay.studio.desktop && echo "== atalho no menu OK"
  # tela virtual só para o teste (não faz parte do que o usuário instala)
  apt-get install -y -qq xvfb xauth imagemagick >/dev/null 2>&1
  useradd -m tester
  su tester -c "mkdir -p /tmp/shots && cd /tmp && GDK_BACKEND=x11 GSK_RENDERER=cairo GSETTINGS_BACKEND=memory \
    ADON_STUDIO_CATALOG=$CATALOG \
    timeout 240 xvfb-run -a -s \"-screen 0 1280x860x24\" python3 /test/teste_app.py /usr/share/adon-studio /tmp/shots" \
    > /tmp/app.log 2>&1 || true
  grep -vE "Gtk-WARNING|^\s*$" /tmp/app.log | tail -40
  cp -r /tmp/shots/. /out/ 2>/dev/null || true
  grep -q "^ERROS: 0" /tmp/app.log && grep -q "^LOJA: instalado" /tmp/app.log
'
echo "prints em $OUT"
