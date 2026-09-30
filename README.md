# Servidor de audio HDMI 5.1 para Raspberry Pi 4

Aplicación web y API REST para reproducir MP3, OGG y WAV en los altavoces de un
receptor 5.1 conectado por HDMI. Funciona con Python 2.7 y Python 3; no necesita
paquetes de Python externos. Permite cuatro sonidos simultáneos, incluso en el
mismo altavoz.

## Preparación en la Raspberry Pi

Instala los programas de audio del sistema:

```bash
sudo apt-get update
sudo apt-get install ffmpeg alsa-utils
```

El dispositivo configurado por defecto es el que se verificó con:

```bash
speaker-test -D plughw:CARD=b2,DEV=0 -c 6 -r 48000 -t wav -l 1
```

Arranca la aplicación manualmente desde este directorio:

```bash
python2.7 audio_server.py
```

También puedes usar `python3 audio_server.py`. En otro equipo o si cambia el
nombre ALSA, indica el dispositivo y el puerto:

```bash
python2.7 audio_server.py --device 'plughw:CARD=b2,DEV=0' --port 8080
```

Abre `http://<ip-de-la-raspberry>:8080/` desde un dispositivo de la misma red.
La web permite subir archivos de hasta 100 MB, seleccionarlos en una lista con
desplazamiento, reproducirlos en un altavoz y detener sonidos activos. Los
archivos se guardan en `audio/`, que se crea automáticamente. El servidor
escucha en la red local sin clave; no lo expongas a Internet.

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

## Comprobaciones

```bash
python3 -m unittest discover -s tests -v
```

En la Raspberry Pi, ejecuta el mismo comando con `python2.7` y después prueba
los seis botones en el receptor. La correspondencia de canales del motor sigue
el orden ALSA que verificaste con `speaker-test`.
