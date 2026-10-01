"""Read and change this server's systemd auto-start state."""

import subprocess


UNIT = 'rpi-audio-server.service'
CONTROL_HELPER = '/usr/local/libexec/rpi-audio-server-systemd'


def status():
    try:
        result = subprocess.run(
            ['/usr/bin/systemctl', 'show', UNIT,
             '--property=LoadState', '--property=UnitFileState',
             '--property=ActiveState', '--no-pager'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('No se pudo consultar systemd: %s' % exc)
    if result.returncode:
        raise RuntimeError('No se pudo consultar systemd: %s' %
                           (result.stderr.strip() or result.returncode))
    values = dict(line.split('=', 1) for line in result.stdout.splitlines()
                  if '=' in line)
    return {
        'installed': values.get('LoadState') == 'loaded',
        'enabled': values.get('UnitFileState') == 'enabled',
        'active': values.get('ActiveState') == 'active',
    }


def set_enabled(enabled):
    action = 'enable' if enabled else 'disable'
    try:
        result = subprocess.run(
            ['/usr/bin/sudo', '-n', CONTROL_HELPER, action],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('No se pudo cambiar el arranque automático: %s' % exc)
    if result.returncode:
        raise RuntimeError('No se pudo cambiar el arranque automático: %s' %
                           (result.stderr.strip() or result.stdout.strip() or result.returncode))
