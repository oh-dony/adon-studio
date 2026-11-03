# Sistema "recém-instalado" com GNOME de verdade (sem tela), para o teste ./studio gnome.
# DISTRO: ubuntu-22.04, ubuntu-24.04 ou pop-22.04
ARG BASE=ubuntu:24.04
FROM ${BASE}
ARG DISTRO=ubuntu-24.04
ENV DEBIAN_FRONTEND=noninteractive
COPY prepara.sh /prepara.sh
RUN bash /prepara.sh "$DISTRO"
