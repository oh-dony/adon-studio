#!/bin/bash
# A sessão da pessoa (roda como o usuário, dentro do dbus-run-session do sessao.sh).
COMBO=$1
export XDG_RUNTIME_DIR=/tmp/rt XDG_SESSION_TYPE=wayland WAYLAND_DISPLAY=wayland-0 GDK_BACKEND=wayland
mkdir -p -m700 $XDG_RUNTIME_DIR
# numa sessão de verdade o gnome-session faz isso: apps abertos pelo D-Bus sabem onde está a tela
dbus-update-activation-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR XDG_SESSION_TYPE XDG_CURRENT_DESKTOP GDK_BACKEND
cd ~
V=/test/gnome/verifica.py

cur=$(gsettings get org.gnome.shell enabled-extensions | sed "s/@as //")
gsettings set org.gnome.shell enabled-extensions "$(python3 -c "import ast,sys; l=ast.literal_eval(sys.argv[1]); print(l+['adon-teste@adon.test'])" "$cur")"
echo "== antes: tema $(gsettings get org.gnome.desktop.interface gtk-theme), ícones $(gsettings get org.gnome.desktop.interface icon-theme)"

gnome-shell --headless --wayland --mode=$MODE --virtual-monitor 1280x800 > /out/shell.log 2>&1 &
for i in $(seq 60); do
  gdbus call --session -d org.gnome.Shell -o /org/gnome/Shell -m org.gnome.Shell.Eval "1" 2>/dev/null | grep -q true && break
  sleep 1
done
sleep 3
# a pessoa está com o gerenciador de arquivos aberto
nautilus --new-window >/dev/null 2>&1 &
sleep 4
python3 $V foto /out/antes.png
touch /tmp/sinais/pronto
for i in $(seq 300); do [ -f /tmp/sinais/deb-ok ] && break; sleep 1; done

OLD=$(python3 $V pid-nautilus)
python3 $V aceita-dialogo /out &
echo "== abrindo o Adon Studio e clicando no combo $COMBO"
ADON_STUDIO_CATALOG=$CATALOG timeout 360 python3 /test/gnome/aplica_combo.py /usr/share/adon-studio /out "$COMBO" 2>&1 \
  | grep -v -E "WARNING|CRITICAL|^\s*$"
sleep 3
echo "== conferindo"
python3 $V combo /usr/share/adon-studio /out "$COMBO" "$OLD"
