#!/usr/bin/env bash

set -euo pipefail

if [[ $# -gt 1 || ( $# -eq 1 && $1 != '--dry-run' ) ]]; then
  echo 'Uso: ./install-systemd.sh [--dry-run]' >&2
  exit 2
fi
if [[ $EUID -eq 0 ]]; then
  echo 'Ejecuta este instalador con el usuario que debe reproducir el audio, sin sudo.' >&2
  exit 1
fi

project_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
service_user=$(id -un)
python_bin=/usr/bin/python3
if [[ ! $project_dir =~ ^[A-Za-z0-9_./-]+$ ]]; then
  echo 'La ruta del proyecto debe contener solo letras ASCII, números, /, ., _ o - para este servicio.' >&2
  exit 1
fi

unit_file=$(mktemp)
sudoers_file=$(mktemp)
trap 'rm -f "$unit_file" "$sudoers_file"' EXIT
cat > "$unit_file" <<UNIT
[Unit]
Description=Servidor de audio 5.1 de Raspberry Pi
After=network.target

[Service]
Type=simple
User=$service_user
WorkingDirectory=$project_dir
Environment=RPI_AUDIO_SUPERVISED=1
Environment=RPI_AUDIO_SYSTEMD_MANAGED=1
Environment=PYTHONUNBUFFERED=1
ExecStart=$python_bin $project_dir/audio_server.py --device=plughw:CARD=b2,DEV=0 --port=8080
ExecStopPost=+/usr/local/libexec/rpi-audio-server-reboot
SuccessExitStatus=75
Restart=on-failure
RestartSec=3
KillMode=mixed
TimeoutStopSec=20s

[Install]
WantedBy=multi-user.target
UNIT
cat > "$sudoers_file" <<SUDOERS
$service_user ALL=(root) NOPASSWD: /usr/local/libexec/rpi-audio-server-systemd enable, /usr/local/libexec/rpi-audio-server-systemd disable
SUDOERS

if [[ ${1:-} == '--dry-run' ]]; then
  cat "$unit_file"
  exit 0
fi

for dependency in /usr/bin/systemctl /usr/bin/killall /usr/bin/systemd-analyze /usr/sbin/visudo /usr/bin/sudo "$python_bin"; do
  if [[ ! -x $dependency ]]; then
    echo "Falta $dependency; instala los requisitos indicados en README.md." >&2
    exit 1
  fi
done
/usr/sbin/visudo -cf "$sudoers_file"

sudo -v
sudo install -D -o root -g root -m 0755 "$project_dir/systemd/reboot-after-exit.sh" /usr/local/libexec/rpi-audio-server-reboot
sudo install -o root -g root -m 0755 "$project_dir/systemd/control-autostart.sh" /usr/local/libexec/rpi-audio-server-systemd
sudo install -o root -g root -m 0644 "$unit_file" /etc/systemd/system/rpi-audio-server.service
systemd-analyze verify /etc/systemd/system/rpi-audio-server.service
sudo install -o root -g root -m 0440 "$sudoers_file" /etc/sudoers.d/rpi-audio-server
sudo systemctl daemon-reload
sudo systemctl enable rpi-audio-server.service
if ! sudo systemctl restart rpi-audio-server.service; then
  echo 'No se pudo iniciar el servicio. Comprueba si sigue abierto un servidor manual en el puerto 8080.' >&2
  echo 'Consulta el error con: sudo journalctl -u rpi-audio-server -n 50 --no-pager' >&2
  exit 1
fi
echo 'Servicio instalado y activado. Arrancará automáticamente al encender la Raspberry.'
echo 'Estado: sudo systemctl status rpi-audio-server'
