from __future__ import absolute_import, division, print_function

import json
import os
import sys
import threading
import uuid
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

    def __init__(self, address, handler, files, mixer, ost_files, ost):
        HTTPServer.__init__(self, address, handler)
        self.files = files
        self.mixer = mixer
        self.ost_files = ost_files
        self.ost = ost


class Handler(BaseHTTPRequestHandler):
    server_version = 'RPiAudioServer/1.0'

    def log_message(self, format_string, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(),
                                         format_string % args))

    def _send(self, status, body, content_type='application/json; charset=utf-8'):
        self.send_response(status)
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

    def _same_origin(self):
        origin = self.headers.get('Origin')
        if not origin:
            return True  # REST clients need not send Origin.
        parsed = urlsplit(origin)
        return parsed.netloc == self.headers.get('Host') and parsed.scheme in ('http', 'https')

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
            return self._json(200, self.server.mixer.health())
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
                                                          '/api/ost/channels'):
            try:
                if parsed.path == '/api/ost/play':
                    payload = self._read_json()
                    file_id = payload.get('file_id')
                    if file_id is not None and not isinstance(file_id, string_types):
                        raise ValueError('file_id must be a string')
                    result = self.server.ost.play(file_id)
                elif parsed.path == '/api/ost/shuffle':
                    result = self.server.ost.set_shuffle(self._read_json().get('shuffle'))
                elif parsed.path == '/api/ost/channels':
                    result = self.server.ost.set_channels(self._read_json().get('channels'))
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
    parser.add_option('--debug-audio', action='store_true', default=False,
                      help='Show ALSA and FFmpeg output in the terminal')
    args, extras = parser.parse_args(argv)
    if extras:
        parser.error('Unexpected arguments: %s' % ' '.join(extras))

    files = FileStore(args.data_dir)
    ost_files = FileStore(args.ost_dir, ('.mp3',))
    mixer = Mixer(args.device, debug_audio=args.debug_audio)
    ost = OSTController(ost_files, mixer)
    server = ThreadedHTTPServer((args.host, args.port), Handler, files, mixer,
                                ost_files, ost)
    print('Open http://<raspberry-pi-ip>:%d/ on your local network' % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        mixer.close()


if __name__ == '__main__':
    main()
