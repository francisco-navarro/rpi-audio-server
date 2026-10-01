#!/usr/bin/env bash

set -u
cd -- "$(dirname -- "$0")" || exit 1

echo 'Se necesita la contraseña de administrador para cerrar aplay al reiniciar.'
sudo -v || exit 1

RPI_AUDIO_SUPERVISED=1 python3 audio_server.py --device 'plughw:CARD=b2,DEV=0' --port 8080
status=$?
if [ "$status" -ne 75 ]; then
  exit "$status"
fi

sudo -v || exit 1
sudo killall aplay
kill_status=$?
if [ "$kill_status" -ne 0 ] && [ "$kill_status" -ne 1 ]; then
  echo 'No se pudo cerrar aplay; la Raspberry no se reinició.' >&2
  exit "$kill_status"
fi
sudo reboot
