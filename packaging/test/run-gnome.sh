#!/bin/bash
# Simula um computador recém-instalado com GNOME de verdade (sem tela) e testa o fluxo completo:
# instalar o .deb com a sessão aberta, abrir o app, clicar num combo e conferir se tudo pegou.
# Uso: packaging/test/run-gnome.sh <ubuntu-22.04|ubuntu-24.04|pop-22.04> <arquivo .deb> <pasta com catalog.json> [combo]
set -e
DISTRO=$1 DEB=$(realpath "$2") BUNDLES=$(realpath "$3") COMBO=${4:-Catppuccin Rosa}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/out/gnome-$DISTRO"
rm -rf "$OUT" && mkdir -p "$OUT"
case "$DISTRO" in
  ubuntu-22.04|pop-22.04) BASE=ubuntu:22.04 ;;
  ubuntu-24.04) BASE=ubuntu:24.04 ;;
  *) echo "distro desconhecida: $DISTRO"; exit 2 ;;
esac
# a imagem (o sistema "recém-instalado") só é montada da primeira vez; depois vem do cache
docker build -q -t "adon-gnome:$DISTRO" --build-arg BASE=$BASE --build-arg DISTRO=$DISTRO "$HERE/gnome" >/dev/null

PORT=8766
python3 -m http.server $PORT --bind 127.0.0.1 --directory "$BUNDLES" >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER' EXIT
docker run --rm --network host -e CATALOG="${CATALOG:-http://127.0.0.1:$PORT/catalog.json}" \
  -v "$DEB:/tmp/app.deb:ro" -v "$HERE:/test:ro" -v "$OUT:/out" "adon-gnome:$DISTRO" \
  bash /test/gnome/sessao.sh "$DISTRO" "$COMBO"
echo "prints e logs em $OUT"
