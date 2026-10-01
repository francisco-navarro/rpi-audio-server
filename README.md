# Servidor de audio HDMI 5.1 para Raspberry Pi 4

Aplicación web y API REST para reproducir MP3, OGG y WAV en los altavoces de un
receptor 5.1 conectado por HDMI. Se arranca con Python 3; no necesita
paquetes de Python externos. Permite cuatro efectos simultáneos, incluso en el
mismo altavoz, más una pista de música OST.

## Preparación en la Raspberry Pi

Instala los programas de audio del sistema:

```bash
sudo apt-get update
sudo apt-get install ffmpeg alsa-utils psmisc
```

El dispositivo configurado por defecto es el que se verificó con:

```bash
speaker-test -D plughw:CARD=b2,DEV=0 -c 6 -r 48000 -t wav -l 1
```

Arranca la aplicación manualmente desde este directorio:

```bash
./start.sh
```

`start.sh` pide la contraseña de administrador con `sudo` sin almacenarla.
Arranca `python3 audio_server.py --device 'plughw:CARD=b2,DEV=0' --port 8080`.
Al pulsar **Reiniciar servidor**, Python cierra el audio y el script ejecuta
`sudo killall aplay` seguido de `sudo reboot`: se reinicia toda la Raspberry.
Si la autorización ha caducado, `sudo` vuelve a pedir la contraseña en esa
terminal.

En otro equipo o si cambia el nombre ALSA, indica el dispositivo y el puerto:

```bash
python3 audio_server.py --device 'plughw:CARD=b2,DEV=0' --port 8080
```

Detén el servidor con Ctrl+C o `kill -TERM <PID>`. El servidor cierra el audio
y deja que `aplay` vacíe su búfer antes de salir. `aplay` y FFmpeg están en
sesiones separadas para que Ctrl+C no interrumpa directamente la salida ALSA.
Evita `kill -9` para detener el servidor, porque impide ejecutar esa limpieza.
El botón **Reiniciar servidor** de la parte superior reinicia toda la Raspberry
y la página se recarga si el servicio vuelve a estar disponible. Si arrancas
directamente con `python3 audio_server.py`, el botón permanece desactivado:
inicia mediante `./start.sh` o el servicio systemd para habilitarlo.

### Arranque automático con systemd

Para que el servidor se inicie al encender la Raspberry, detén primero el
`./start.sh` que hayas abierto a mano (Ctrl+C) y ejecuta **sin `sudo`**:

```bash
./install-systemd.sh
```

El instalador pide la contraseña de administrador una sola vez. Crea una unidad
con la ruta de este directorio y tu usuario, activa su arranque al encender y
la inicia ahora. El servidor de audio corre con tu usuario; solo el paso que
ejecuta `killall aplay` y `systemctl reboot` tras pulsar el botón corre como
administrador. Ese paso se instala en una ruta propiedad de root y únicamente
se ejecuta cuando el servidor sale con el código 75. Detener el servicio de
forma normal no reinicia la Raspberry. El servicio vuelve a arrancar si el
proceso falla, pero no cuando sale para reiniciar la máquina.

La pestaña **Arranque** de la propia web muestra si el servicio está activo y
permite habilitar o deshabilitar su arranque al encender. Se queda en la misma
página; el ajuste no detiene la reproducción actual. Para usarla hay que
ejecutar el instalador una vez. Este instala también un permiso limitado a
esas dos acciones, sin dar permisos de administrador al proceso de audio.

Puedes revisar la unidad sin instalarla con `./install-systemd.sh --dry-run`.
Para consultar el estado y las últimas líneas del registro:

```bash
sudo systemctl status rpi-audio-server
sudo journalctl -u rpi-audio-server -n 50 --no-pager
```

Para detenerlo, iniciarlo o quitar el arranque automático:

```bash
sudo systemctl stop rpi-audio-server
sudo systemctl start rpi-audio-server
sudo systemctl disable --now rpi-audio-server
```

Una vez instalado, no arranques `./start.sh` a la vez: ambos intentarían usar el
puerto 8080 y el mismo dispositivo HDMI. Si mueves este directorio o cambias de
usuario, vuelve a ejecutar el instalador desde su nueva ubicación.

Abre `http://<ip-de-la-raspberry>:8080/` desde un dispositivo de la misma red.
La web tiene dos pestañas. **Efectos de sonido** permite subir archivos de hasta
100 MB y enviarlos a los altavoces. **OST** tiene una playlist de MP3 con
anterior, reproducir, pausar, siguiente, modo aleatorio y una barra de progreso
con el tiempo transcurrido y la duración de cada canción. La música avanza
automáticamente al terminar cada pista y vuelve al principio de la lista. El
modo aleatorio recorre las pistas antes de repetirlas. Los efectos se guardan
en `audio/` y la música en `ost_audio/`, carpetas creadas automáticamente.
Puedes cambiar las carpetas con `--data-dir` y `--ost-dir`. El servidor
escucha en la red local sin clave; no lo expongas a Internet.

Para usar la API desde el navegador de Mansiones, se permiten por defecto
`http://mansiones.local`, `http://mansiones.local:5173` y
`http://localhost:5173`. Si abres el juego con otra dirección, añádela al
arrancar el servidor, por ejemplo:

```bash
python3 audio_server.py --allow-origin http://192.168.1.20
```

Solo se aceptan los orígenes indicados; los clientes REST
sin cabecera `Origin` siguen funcionando igual.

## API REST

`GET /api/files` devuelve los archivos cargados con su `id`. Los archivos se
suben desde la web. `GET /api/playbacks` muestra las reproducciones activas y
`GET /api/health` el estado del motor de audio.

Una salida reproduce el archivo mezclado a mono en el altavoz indicado:

```bash
curl -X POST http://<ip-de-la-raspberry>:8080/api/play \
  -H 'Content-Type: application/json' \
  -d '{"file_id":"ID_DEL_ARCHIVO","channel":"center"}'
```

Para un archivo estéreo, `channels` contiene **dos destinos ordenados**: el
canal izquierdo del archivo va al primero y el derecho al segundo. Por ejemplo,
izquierdo al frontal izquierdo y derecho al trasero izquierdo:

```bash
curl -X POST http://<ip-de-la-raspberry>:8080/api/play \
  -H 'Content-Type: application/json' \
  -d '{"file_id":"ID_DEL_ARCHIVO","channels":["front_left","rear_left"]}'
```

Destinos válidos: `front_left`, `front_right`, `center`, `rear_left`,
`rear_right`, `lfe`. Se admiten también listas de un elemento. Los dos destinos
estéreo deben ser distintos. FFmpeg convierte archivos mono a estéreo cuando se
solicitan dos destinos. El canal LFE lleva un filtro de graves de unos 120 Hz.

`POST /api/play` responde con un `id` de reproducción. Para detenerlo:

```bash
curl -X DELETE http://<ip-de-la-raspberry>:8080/api/playbacks/ID_REPRODUCCION
```

`DELETE /api/playbacks` detiene todas las reproducciones. La API devuelve JSON
y usa `400` para solicitudes inválidas, `404` para archivos o reproducciones
inexistentes y `409` cuando ya suenan cuatro archivos.

### API OST

`GET /api/ost` devuelve la playlist, la pista actual, el estado, los dos
altavoces elegidos, el modo aleatorio, `position_seconds` y
`duration_seconds` y `volume` (porcentaje de 0 a 100). El volumen de la OST
empieza al 60 %; el control de la web lo cambia durante la reproducción sin
reiniciar la pista ni alterar los efectos. El tiempo reproducido se detiene al
pausar y vuelve a cero al pasar a la siguiente pista. La duración se obtiene
con `ffprobe`, incluido en el paquete `ffmpeg`. La playlist OST acepta sólo
MP3 y se sube a una carpeta independiente:

```bash
curl -X POST --data-binary @musica.mp3 \
  'http://<ip-de-la-raspberry>:8080/api/ost/upload?filename=musica.mp3'
```

Para reproducir la primera pista o reanudar la actual:

```bash
curl -X POST -H 'Content-Type: application/json' -d '{}' \
  http://<ip-de-la-raspberry>:8080/api/ost/play
```

También puedes pasar `{"file_id":"ID_DEL_MP3"}` para elegir una pista. Los
controles `POST /api/ost/pause`, `POST /api/ost/previous`,
`POST /api/ost/next` y `POST /api/ost/stop` no necesitan cuerpo. El modo
aleatorio se activa con `POST /api/ost/shuffle` y cuerpo
`{"shuffle":true}`; usa `false` para desactivarlo.

Para dirigir los canales izquierdo y derecho a dos altavoces distintos:

```bash
curl -X POST http://<ip-de-la-raspberry>:8080/api/ost/channels \
  -H 'Content-Type: application/json' \
  -d '{"channels":["front_left","rear_left"]}'
```

El cambio de altavoces durante una canción la reinicia desde el principio.
Para ajustar el volumen de la OST mediante la API:

```bash
curl -X POST http://<ip-de-la-raspberry>:8080/api/ost/volume \
  -H 'Content-Type: application/json' \
  -d '{"volume":60}'
```

Los efectos y la OST pueden sonar a la vez. `DELETE /api/playbacks` detiene
sólo los efectos; `POST /api/ost/stop` detiene sólo la música.

## Comprobaciones

```bash
python3 -m unittest discover -s tests -v
```

En la Raspberry Pi, prueba después los seis botones en el receptor. La
correspondencia de canales del motor sigue el orden ALSA que verificaste con
`speaker-test`.

## Si no se oye nada

Tras pulsar un altavoz, abre **Ver trazas de audio** bajo **Sonando ahora** o
consulta `http://<ip-de-la-raspberry>:8080/api/health`. El apartado **POST para
repetir la reproducción** muestra un `curl` copiable con la URL y el JSON
exactos; también aparece en la terminal. Los efectos se envían al 100 % y la
OST usa su volumen independiente. Los campos `alsa_log` y
`ffmpeg_log` contienen las últimas 80 líneas de cada programa, también cuando
no se produce un error. La web consulta este endpoint al abrirse, tras iniciar
una reproducción y mientras esté desplegado el panel de trazas; no lo sondea
continuamente en segundo plano. Para verlas en directo en la terminal, arranca así:

```bash
python3 audio_server.py --debug-audio
```

`last_error` recoge el último fallo detectado. Si `last_signal_peak` es mayor que cero y
`last_signal_at` tiene una hora reciente, el servidor ha enviado muestras de
audio a ALSA; comprueba entonces el dispositivo elegido y la entrada del
receptor. Si ambos siguen vacíos, prueba otro archivo y revisa el error de
decodificación.

### Si el audio se corta

Abre **Ver trazas de audio** mientras suena un archivo. Los contadores son
acumulados desde el arranque, así que observa si aumentan durante los cortes:

- `late_blocks`: la salida no completó a tiempo alguno de los bloques de 20 ms.
- `decode_starvations`: el mezclador esperaba un bloque que FFmpeg aún no había entregado.
- `alsa_underruns`: `aplay` informó de falta de muestras (`underrun`/`xrun`).
- `max_mix_ms`: tiempo máximo empleado en mezclar un bloque; cerca de 20 ms apunta a saturación del hilo de mezcla.
- `max_write_ms`: tiempo máximo de escritura a ALSA; si crece, la salida está bloqueando al mezclador.

En otra terminal de la Raspberry Pi, ejecuta `top -H`, pulsa `P` para ordenar
por CPU y reproduce el archivo. Mira los hilos de `python3` y los procesos
`ffmpeg` y `aplay`. En la Raspberry Pi 4, un proceso cerca del 100 % puede
estar ocupando **un núcleo completo** aunque los otros estén libres. Si el
uso de CPU sube a la vez que aumentan `late_blocks` o
`decode_starvations`, la CPU es una causa probable. Si sólo aparecen
`alsa_underruns` o crece `max_write_ms`, revisa la salida ALSA y el receptor.

Para repetir la comprobación física del dispositivo configurado:

```bash
speaker-test -D plughw:CARD=b2,DEV=0 -c 6 -r 48000 -t wav -l 1
```

Si después de detener el servidor con Ctrl+C `speaker-test` recorre los
canales sin error pero no se oye, anota si apagar y encender **solo el receptor
HDMI** recupera el sonido. Esa prueba distingue un bloqueo del receptor o de
la negociación HDMI de un problema que permanezca en la Raspberry.
