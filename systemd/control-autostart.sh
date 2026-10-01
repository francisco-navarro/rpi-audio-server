#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
    echo 'Uso: rpi-audio-server-systemd enable|disable' >&2
    exit 2
fi

case "${1:-}" in
    enable) exec /usr/bin/systemctl enable rpi-audio-server.service ;;
    disable) exec /usr/bin/systemctl disable rpi-audio-server.service ;;
    *) echo 'Uso: rpi-audio-server-systemd enable|disable' >&2; exit 2 ;;
esac
