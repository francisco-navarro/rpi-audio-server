from __future__ import absolute_import, division, print_function

import json
import os
import shlex
import signal
import sys
import threading
import uuid
from collections import deque
from optparse import OptionParser

try:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
    from urlparse import parse_qs, urlsplit
    text_type = unicode
    string_types = (str, unicode)
except ImportError:  # Python 3
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, urlsplit
    text_type = str
    string_types = (str,)

from mixer import Mixer
from ost import OSTController


ROOT = os.path.dirname(os.path.abspath(__file__))
EXTENSIONS = frozenset(('.mp3', '.ogg', '.wav'))
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 8192
REBOOT_EXIT_CODE = 75
DEFAULT_ALLOWED_ORIGINS = (
    'http://mansiones.local',
    'http://mansiones.local:5173',
    'http://localhost:5173',
)


def to_text(value):
    if isinstance(value, text_type):
        return value
    return value.decode('utf-8')


class FileStore(object):
    def __init__(self, directory, extensions=EXTENSIONS):
        self.directory = os.path.abspath(directory)
        self.extensions = frozenset(extensions)
        self.index_path = os.path.join(self.directory, 'index.json')
        self.lock = threading.RLock()
        if not os.path.isdir(self.directory):
            os.makedirs(self.directory)
        if os.path.exists(self.index_path):
            with open(self.index_path, 'rb') as index_file:
                self.index = json.loads(index_file.read().decode('utf-8'))
        else:
            self.index = {}

    def list_files(self):
        with self.lock:
            files = []
            for file_id, item in self.index.items():
                if os.path.isfile(os.path.join(self.directory, item['stored_name'])):
                    files.append({'id': file_id, 'name': item['name'],
                                  'size': item['size']})
            return sorted(files, key=lambda item: item['name'].lower())

    def resolve(self, file_id):
        with self.lock:
            item = self.index.get(file_id)
            if item is None:
                return None
            path = os.path.join(self.directory, item['stored_name'])
            return path if os.path.isfile(path) else None

    def save_upload(self, name, source, length):
        name = to_text(name).replace('\\', '/').split('/')[-1].strip()
        extension = os.path.splitext(name)[1].lower()
        if not name or len(name) > 255 or extension not in self.extensions:
            raise ValueError('Use a %s filename' % ', '.join(sorted(self.extensions)))
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            raise ValueError('File must be between 1 byte and 100 MB')

        file_id = uuid.uuid4().hex
        stored_name = file_id + extension
        temporary = os.path.join(self.directory, file_id + '.part')
        destination = os.path.join(self.directory, stored_name)
        remaining = length
        try:
            with open(temporary, 'wb') as output:
                while remaining:
                    block = source.read(min(65536, remaining))
                    if not block:
                        raise IOError('Upload ended before all bytes arrived')
                    output.write(block)
                    remaining -= len(block)
            os.rename(temporary, destination)
            with self.lock:
                self.index[file_id] = {'name': name, 'stored_name': stored_name,
                                       'size': length}
                self._write_index()
            return {'id': file_id, 'name': name, 'size': length}
        except Exception:
            for path in (temporary, destination):
                if os.path.exists(path):
                    os.remove(path)
            raise

    def _write_index(self):
        temporary = self.index_path + '.tmp'
        data = json.dumps(self.index, ensure_ascii=False, sort_keys=True)
        with open(temporary, 'wb') as output:
            output.write(data.encode('utf-8'))
        os.rename(temporary, self.index_path)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, files, mixer, ost_files, ost,
                 allowed_origins=DEFAULT_ALLOWED_ORIGINS):
        HTTPServer.__init__(self, address, handler)
        self.files = files
        self.mixer = mixer
        self.ost_files = ost_files
        self.ost = ost
        self.allowed_origins = frozenset(allowed_origins)
        self.instance_id = uuid.uuid4().hex
        self.can_reboot = os.environ.get('RPI_AUDIO_SUPERVISED') == '1'
        self.reboot_requested = threading.Event()
        self.playback_log = deque(maxlen=20)
        self.playback_log_lock = threading.Lock()

    def record_playback(self, entry):
        with self.playback_log_lock:
            self.playback_log.append(entry)

    def recent_playbacks(self):
        with self.playback_log_lock:
            return list(self.playback_log)

    def request_reboot(self):
        if self.reboot_requested.is_set():
            return
        self.reboot_requested.set()
        timer = threading.Timer(0.2, self.shutdown)
        timer.daemon = True
        timer.start()


class Handler(BaseHTTPRequestHandler):
    server_version = 'RPiAudioServer/1.0'

    def log_message(self, format_string, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(),
                                         format_string % args))

    def _send(self, status, body, content_type='application/json; charset=utf-8'):
        self.send_response(status)
        self._cors_headers()
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self._send(status, body)

    def _error(self, status, message):
        self._json(status, {'error': message})

    def _log_playback_request(self, path, payload, playback_id, volume):
        url = 'http://%s%s' % (self.headers.get('Host'), path)
        body = json.dumps(payload, ensure_ascii=True, separators=(',', ':'))
        command = "curl -X POST %s -H 'Content-Type: application/json' --data %s" % (
            shlex.quote(url), shlex.quote(body))
        entry = 'Reproducción %s · volumen %s %%\n%s' % (
            playback_id, volume, command)
        self.server.record_playback(entry)
        self.log_message('%s', entry.replace('\n', ' | '))

    def _same_origin(self):
        origin = self.headers.get('Origin')
        if not origin:
            return True  # REST clients need not send Origin.
        parsed = urlsplit(origin)
        return ((parsed.netloc == self.headers.get('Host') and parsed.scheme in ('http', 'https'))
                or origin in self.server.allowed_origins)

    def _own_origin(self):
        origin = self.headers.get('Origin')
        if not origin:
            return True
        parsed = urlsplit(origin)
        return parsed.netloc == self.headers.get('Host') and parsed.scheme in ('http', 'https')

    def _cors_headers(self):
        origin = self.headers.get('Origin')
        if origin and self._same_origin():
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')

    def do_OPTIONS(self):
        if not self._same_origin():
            return self._error(403, 'Origin is not allowed')
        if urlsplit(self.path).path not in ('/upload', '/api/files', '/api/play', '/api/health'):
            return self._error(404, 'Not found')
        self.send_response(204)
        self._cors_headers()
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        if self.headers.get('Access-Control-Request-Private-Network') == 'true':
            self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _content_length(self, maximum):
        value = self.headers.get('Content-Length')
        if value is None:
            raise ValueError('Content-Length is required')
        try:
            length = int(value)
        except ValueError:
            raise ValueError('Invalid Content-Length')
        if length < 0 or length > maximum:
            raise OverflowError('Request body is too large')
        return length

    def _read_json(self):
        length = self._content_length(MAX_JSON_BYTES)
        if not length:
            raise ValueError('JSON body is required')
        try:
            value = json.loads(self.rfile.read(length).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Invalid JSON body')
        if not isinstance(value, dict):
            raise ValueError('JSON body must be an object')
        return value

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/files':
            return self._json(200, {'files': self.server.files.list_files()})
        if path == '/api/playbacks':
            return self._json(200, {'playbacks': self.server.mixer.list_playbacks('effect')})
        if path == '/api/ost':
            return self._json(200, self.server.ost.describe())
        if path == '/api/health':
            health = self.server.mixer.health()
            health['instance_id'] = self.server.instance_id
            health['can_reboot'] = self.server.can_reboot
            health['playback_log'] = self.server.recent_playbacks()
            return self._json(200, health)
        static = {
            '/': ('index.html', 'text/html; charset=utf-8'),
            '/app.js': ('app.js', 'application/javascript; charset=utf-8'),
            '/style.css': ('style.css', 'text/css; charset=utf-8'),
        }
        if path not in static:
            return self._error(404, 'Not found')
        filename, content_type = static[path]
        with open(os.path.join(ROOT, 'static', filename), 'rb') as source:
            self._send(200, source.read(), content_type)

    def do_POST(self):
        if not self._same_origin():
            return self._error(403, 'Origin is not allowed')
        parsed = urlsplit(self.path)
        if parsed.path == '/api/reboot':
            if not self._own_origin():
                return self._error(403, 'Origin is not allowed to reboot the server')
            if not self.server.can_reboot:
                return self._error(503, 'Inicia el servidor con ./start.sh para reiniciar la Raspberry')
            self._json(202, {'rebooting': True, 'instance_id': self.server.instance_id})
            self.server.request_reboot()
            return
        if parsed.path in ('/upload', '/api/ost/upload'):
            try:
                length = self._content_length(MAX_UPLOAD_BYTES)
                values = parse_qs(parsed.query)
                name = values.get('filename', [''])[0]
                if not name:
                    raise ValueError('filename query parameter is required')
                store = (self.server.ost_files if parsed.path == '/api/ost/upload'
                         else self.server.files)
                uploaded = store.save_upload(name, self.rfile, length)
                return self._json(201, uploaded)
            except OverflowError as exc:
                return self._error(413, str(exc))
            except (ValueError, UnicodeDecodeError) as exc:
                return self._error(400, str(exc))
            except IOError as exc:
                return self._error(500, str(exc))

        if parsed.path == '/api/play':
            try:
                payload = self._read_json()
                file_id = payload.get('file_id')
                if not isinstance(file_id, string_types):
                    raise ValueError('file_id must be a string')
                if 'channel' in payload and 'channels' in payload:
                    raise ValueError('Use channel or channels, not both')
                channels = payload.get('channels')
                if channels is None and 'channel' in payload:
                    channels = [payload['channel']]
                if not isinstance(channels, list) or len(channels) not in (1, 2):
                    raise ValueError('channels must contain one or two destinations')
                path = self.server.files.resolve(file_id)
                if path is None:
                    return self._error(404, 'Audio file not found')
                playback = self.server.mixer.play(file_id, path, channels)
                replay_payload = {'file_id': file_id}
                if 'channel' in payload:
                    replay_payload['channel'] = payload['channel']
                else:
                    replay_payload['channels'] = channels
                self._log_playback_request('/api/play', replay_payload,
                                           playback['id'], 100)
                return self._json(201, playback)
            except OverflowError as exc:
                return self._error(409, str(exc))
            except (ValueError, UnicodeDecodeError, TypeError) as exc:
                return self._error(400, str(exc))
            except RuntimeError as exc:
                return self._error(503, str(exc))

        ost_actions = {
            '/api/ost/play': 'play',
            '/api/ost/pause': 'pause',
            '/api/ost/stop': 'stop',
            '/api/ost/next': 'next',
            '/api/ost/previous': 'previous',
        }
        if parsed.path in ost_actions or parsed.path in ('/api/ost/shuffle',
                                                          '/api/ost/channels',
                                                          '/api/ost/volume'):
            try:
                if parsed.path == '/api/ost/play':
                    payload = self._read_json()
                    file_id = payload.get('file_id')
                    if file_id is not None and not isinstance(file_id, string_types):
                        raise ValueError('file_id must be a string')
                    result = self.server.ost.play(file_id)
                    self._log_playback_request('/api/ost/play',
                                               {'file_id': result['current']['id']},
                                               result['playback_id'], result['volume'])
                elif parsed.path == '/api/ost/shuffle':
                    result = self.server.ost.set_shuffle(self._read_json().get('shuffle'))
                elif parsed.path == '/api/ost/channels':
                    result = self.server.ost.set_channels(self._read_json().get('channels'))
                elif parsed.path == '/api/ost/volume':
                    result = self.server.ost.set_volume(self._read_json().get('volume'))
                else:
                    result = getattr(self.server.ost, ost_actions[parsed.path])()
                return self._json(200, result)
            except OverflowError as exc:
                return self._error(409, str(exc))
            except (ValueError, UnicodeDecodeError, TypeError) as exc:
                return self._error(400, str(exc))
            except RuntimeError as exc:
                return self._error(503, str(exc))

        return self._error(404, 'Not found')

    def do_DELETE(self):
        if not self._same_origin():
            return self._error(403, 'Origin is not allowed')
        path = urlsplit(self.path).path
        if path == '/api/playbacks':
            count = self.server.mixer.stop_all('effect')
            return self._json(200, {'stopped': count})
        prefix = '/api/playbacks/'
        if path.startswith(prefix):
            playback_id = path[len(prefix):]
            if self.server.mixer.stop(playback_id, 'effect'):
                return self._json(200, {'stopped': playback_id})
            return self._error(404, 'Playback not found')
        return self._error(404, 'Not found')


def main(argv=None):
    parser = OptionParser(description='Raspberry Pi 5.1 HDMI sound server')
    parser.add_option('--host', default='0.0.0.0')
    parser.add_option('--port', type='int', default=8080)
    parser.add_option('--device', default='plughw:CARD=b2,DEV=0')
    parser.add_option('--data-dir', default=os.path.join(ROOT, 'audio'))
    parser.add_option('--ost-dir', default=os.path.join(ROOT, 'ost_audio'))
    parser.add_option('--allow-origin', action='append', dest='allowed_origins',
                      default=[], help='Additional web origin allowed to use the API')
    parser.add_option('--debug-audio', action='store_true', default=False,
                      help='Show ALSA and FFmpeg output in the terminal')
    args, extras = parser.parse_args(argv)
    if extras:
        parser.error('Unexpected arguments: %s' % ' '.join(extras))
    for origin in args.allowed_origins:
        parsed = urlsplit(origin)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            parser.error('--allow-origin must be an exact http(s) origin')

    files = FileStore(args.data_dir)
    ost_files = FileStore(args.ost_dir, ('.mp3',))
    mixer = Mixer(args.device, debug_audio=args.debug_audio)
    ost = OSTController(ost_files, mixer)
    server = ThreadedHTTPServer((args.host, args.port), Handler, files, mixer,
                                ost_files, ost,
                                DEFAULT_ALLOWED_ORIGINS + tuple(args.allowed_origins))
    def stop_server(_signum, _frame):
        raise KeyboardInterrupt()

    old_sigterm = signal.signal(signal.SIGTERM, stop_server)
    print('Open http://<raspberry-pi-ip>:%d/ on your local network' % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, old_sigterm)
        try:
            server.server_close()
        finally:
            mixer.close()
    if server.reboot_requested.is_set():
        return REBOOT_EXIT_CODE
    return 0


if __name__ == '__main__':
    sys.exit(main())
