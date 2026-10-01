#!/bin/sh

set -eu

# systemd provides these values to ExecStopPost. A normal stop or a crash
# must never reboot the Raspberry.
if [ "${EXIT_CODE:-}" != 'exited' ] || [ "${EXIT_STATUS:-}" != '75' ]; then
    exit 0
fi

if /usr/bin/killall aplay; then
    :
else
    status=$?
    if [ "$status" -ne 1 ]; then
        echo 'No se pudo cerrar aplay; la Raspberry no se reinició.' >&2
        exit "$status"
    fi
fi

/usr/bin/systemctl reboot
