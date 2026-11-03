#!/bin/bash
# Instala o que vem num desktop recém-instalado (sem o Adon Studio e sem o gnome-shell-extensions).
set -e
DISTRO=$1
apt_q() { apt-get install -y -qq --no-install-recommends "$@" >/tmp/apt.log 2>&1 || { tail -30 /tmp/apt.log; exit 1; }; }
apt-get update -qq
# só para o teste (não fazem parte do sistema da pessoa): barramento, servidor gráfico, python
TESTE="dbus-user-session dbus-x11 dconf-cli dconf-service xwayland libgl1-mesa-dri python3-gi ca-certificates"
# vêm em qualquer desktop recém-instalado (o Shell só carrega o leitor de SVG ao iniciar)
DESKTOP="librsvg2-common unzip"
case "$DISTRO" in
  ubuntu-*)
    apt_q $TESTE $DESKTOP gnome-shell ubuntu-settings yaru-theme-gnome-shell yaru-theme-gtk yaru-theme-icon \
      gnome-shell-extension-ubuntu-dock gnome-shell-extension-appindicator nautilus gnome-settings-daemon
    ;;
  pop-22.04)
    apt_q $TESTE gnupg curl
    gpg --batch --keyserver hkps://keyserver.ubuntu.com --recv-keys 63C46DF0140D738961429F4E204DD8AEC33A7AFF
    gpg --batch --export 63C46DF0140D738961429F4E204DD8AEC33A7AFF > /usr/share/keyrings/pop.gpg
    echo "deb [signed-by=/usr/share/keyrings/pop.gpg] http://apt.pop-os.org/release jammy main" > /etc/apt/sources.list.d/pop.list
    apt-get update -qq
    apt_q $TESTE $DESKTOP gnome-shell pop-session pop-default-settings pop-gtk-theme pop-icon-theme pop-shell pop-cosmic \
      gnome-shell-extension-cosmic-dock gnome-shell-extension-appindicator nautilus gnome-settings-daemon
    # o pop-default-settings liga os repositórios e a preferência do Pop: um Pop de verdade tem as versões dele
    apt-get update -qq
    apt-get install -y -qq --allow-downgrades -o Dpkg::Options::=--force-confold \
      $(apt list --upgradable 2>/dev/null | cut -d/ -f1 | grep -v Listing) >/tmp/apt.log 2>&1 || true
    apt-get -y -qq --allow-downgrades -o Dpkg::Options::=--force-confold dist-upgrade >>/tmp/apt.log 2>&1 || { tail -30 /tmp/apt.log; exit 1; }
    dpkg -s gnome-shell | grep -q pop || { echo "gnome-shell não é o do Pop"; exit 1; }
    ;;
esac
useradd -m tester
apt-get clean
