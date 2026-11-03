#!/bin/bash
# Roda dentro do contêiner (como root). Sobe a sessão GNOME da pessoa, e com ela já aberta instala
# o .deb (como a pessoa faria), aplica um combo e confere tudo. Saída em /out.
set -e
DISTRO=$1 COMBO=$2
case "$DISTRO" in
  ubuntu-*) export MODE=ubuntu XDG_CURRENT_DESKTOP=ubuntu:GNOME ;;
  pop-*)    export MODE=pop XDG_CURRENT_DESKTOP=pop:GNOME ;;
esac
[ -f /usr/share/gnome-shell/modes/$MODE.json ] || export MODE=user

# o que o systemd faria numa máquina de verdade
rm -rf /run/systemd/seats   # sem logind no contêiner: o Shell usa o modo sem login
mkdir -p /run/dbus && dbus-daemon --system --fork
mkdir -m1777 -p /tmp/.X11-unix /tmp/sinais

# ajudante só do teste: libera o Eval/Screenshot do Shell por D-Bus
EXT=/home/tester/.local/share/gnome-shell/extensions/adon-teste@adon.test
mkdir -p "$EXT"
cp /test/gnome/ajudante/metadata.json "$EXT/"
V=$(gnome-shell --version | grep -o '[0-9]*' | head -1)
cp /test/gnome/ajudante/extension-$([ "$V" -ge 45 ] && echo 45 || echo 42).js "$EXT/extension.js"
chown -R tester: /home/tester/.local
chmod 777 /out /tmp/sinais

su tester -c "dbus-run-session -- bash /test/gnome/usuario.sh '$COMBO'" > /out/sessao.log 2>&1 &
SESSION=$!
for i in $(seq 90); do [ -f /tmp/sinais/pronto ] && break; sleep 1; done
[ -f /tmp/sinais/pronto ] || { echo "a sessão GNOME não subiu"; tail -30 /out/sessao.log; tail -30 /out/shell.log; exit 1; }

echo "== GNOME Shell $(gnome-shell --version | grep -o '[0-9.]*$') no ar (modo $MODE); instalando o .deb com a sessão aberta"
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq /tmp/app.deb > /out/apt.log 2>&1 || { tail -20 /out/apt.log; exit 1; }
echo "   instalou também: $(grep -o 'Setting up [^ ]*' /out/apt.log | cut -d' ' -f3 | grep -v adon-studio | tr '\n' ' ')"
touch /tmp/sinais/deb-ok
wait $SESSION || true
grep -v -E "WARNING|CRITICAL|^\s*$|dbus-daemon|^\*\*|Gtk-Message" /out/sessao.log | tail -40
grep -q "^RESULTADO: 0 falha" /out/sessao.log
