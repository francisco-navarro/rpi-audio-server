from __future__ import absolute_import, division, print_function

import json
import os
import signal
import shutil
import sys
import tempfile
import threading
import unittest
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import audio_server
from audio_server import FileStore, Handler
from ost import OSTController


class FakeMixer(object):
    def __init__(self):
        self.playbacks = {}
        self.callbacks = {}
        self.next_id = 0

    def play(self, file_id, path, channels, kind='effect', on_complete=None,
             paused=False, volume=1.0):
        if len(channels) not in (1, 2):
            raise ValueError('Select one or two channels')
        if len(set(channels)) != len(channels):
            raise ValueError('Stereo destinations must be different')
        if any(name not in ('front_left', 'front_right', 'rear_left',
                            'rear_right', 'center', 'lfe') for name in channels):
            raise ValueError('Unknown channel')
        self.next_id += 1
        result = {'id': 'playback-%s' % self.next_id, 'file_id': file_id,
                  'channels': list(channels),
                  'state': 'paused' if paused else 'playing', 'kind': kind,
                  'volume': volume}
        self.playbacks[result['id']] = result
        self.callbacks[result['id']] = on_complete
        return result

    def set_volume(self, playback_id, volume):
        playback = self.playbacks.get(playback_id)
        if playback is None:
            return False
        playback['volume'] = volume
        return True

    def list_playbacks(self, kind=None):
        return [item for item in self.playbacks.values()
                if kind is None or item['kind'] == kind]

    def has_playback(self, playback_id):
        return playback_id in self.playbacks

    def position_seconds(self, playback_id):
        playback = self.playbacks.get(playback_id)
        return playback.get('position', 0.0) if playback else None

    def stop(self, playback_id, kind=None):
        item = self.playbacks.get(playback_id)
        if item is None or kind is not None and item['kind'] != kind:
            return False
        return self.playbacks.pop(playback_id, None) is not None

    def stop_all(self, kind=None):
        ids = [item['id'] for item in self.list_playbacks(kind)]
        for playback_id in ids:
            self.stop(playback_id)
        return len(ids)

    def pause(self, playback_id):
        if playback_id not in self.playbacks:
            return False
        self.playbacks[playback_id]['state'] = 'paused'
        return True

    def resume(self, playback_id):
        if playback_id not in self.playbacks:
            return False
        self.playbacks[playback_id]['state'] = 'playing'
        return True

    def complete(self, playback_id, error=None):
        self.playbacks.pop(playback_id)
        playback = type('Completed', (object,),
                        {'id': playback_id, 'error': error})()
        callback = self.callbacks.pop(playback_id)
        if callback:
            callback(playback)

    def health(self):
        return {'sink_running': True}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.files = FileStore(os.path.join(self.temp, 'effects'))
        self.ost_files = FileStore(os.path.join(self.temp, 'ost'), ('.mp3',))
        self.mixer = FakeMixer()
        self.ost = OSTController(self.ost_files, self.mixer, ffprobe=None)
        self.playback_log = []
        self.terminal_log = []

    def tearDown(self):
        shutil.rmtree(self.temp)

    def test_sigterm_closes_server_and_mixer(self):
        closed = []

        class FakeMixer(object):
            def __init__(self, *args, **kwargs):
                pass

            def close(self):
                closed.append('mixer')

        class FakeServer(object):
            def __init__(self, *args, **kwargs):
                self.reboot_requested = threading.Event()

            def serve_forever(self):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

            def server_close(self):
                closed.append('server')

        old_mixer = audio_server.Mixer
        old_server = audio_server.ThreadedHTTPServer
        try:
            audio_server.Mixer = FakeMixer
            audio_server.ThreadedHTTPServer = FakeServer
            audio_server.main(['--data-dir', os.path.join(self.temp, 'main-effects'),
                               '--ost-dir', os.path.join(self.temp, 'main-ost')])
        finally:
            audio_server.Mixer = old_mixer
            audio_server.ThreadedHTTPServer = old_server
        self.assertEqual(closed, ['server', 'mixer'])

    def test_reboot_closes_audio_and_socket_before_returning_to_start_script(self):
        events = []

        class FakeMixer(object):
            def __init__(self, *args, **kwargs):
                pass

            def close(self):
                events.append('mixer')

        class FakeServer(object):
            def __init__(self, *args, **kwargs):
                self.reboot_requested = threading.Event()

            def serve_forever(self):
                self.reboot_requested.set()

            def server_close(self):
                events.append('server')

        old_mixer = audio_server.Mixer
        old_server = audio_server.ThreadedHTTPServer
        old_supervised = os.environ.get('RPI_AUDIO_SUPERVISED')
        try:
            audio_server.Mixer = FakeMixer
            audio_server.ThreadedHTTPServer = FakeServer
            os.environ['RPI_AUDIO_SUPERVISED'] = '1'
            result = audio_server.main(['--data-dir', os.path.join(self.temp, 'supervised-effects'),
                                        '--ost-dir', os.path.join(self.temp, 'supervised-ost')])
        finally:
            audio_server.Mixer = old_mixer
            audio_server.ThreadedHTTPServer = old_server
            if old_supervised is None:
                os.environ.pop('RPI_AUDIO_SUPERVISED', None)
            else:
                os.environ['RPI_AUDIO_SUPERVISED'] = old_supervised
        self.assertEqual(result, audio_server.REBOOT_EXIT_CODE)
        self.assertEqual(events, ['server', 'mixer'])

    def request(self, path, method='GET', body=None, origin=None):
        handler = Handler.__new__(Handler)
        handler.path = path
        handler.rfile = BytesIO(body if body is not None else b'')
        handler.wfile = BytesIO()
        handler.headers = {'Host': '127.0.0.1:8080'}
        if origin is not None:
            handler.headers['Origin'] = origin
        if body is not None:
            handler.headers['Content-Length'] = str(len(body))
        handler.server = type('Server', (object,), {})()
        handler.server.files = self.files
        handler.server.ost_files = self.ost_files
        handler.server.mixer = self.mixer
        handler.server.ost = self.ost
        handler.server.allowed_origins = frozenset(('http://mansiones.local',))
        handler.server.instance_id = 'instance-1'
        handler.server.can_reboot = getattr(self, 'can_reboot', True)
        handler.server.request_reboot = lambda: setattr(self, 'reboot_called', True)
        handler.server.record_playback = self.playback_log.append
        handler.server.recent_playbacks = lambda: list(self.playback_log)
        handler.log_message = lambda format_string, *args: self.terminal_log.append(format_string % args)
        status = []
        self.last_headers = {}
        handler.send_response = lambda code: status.append(code)
        handler.send_header = lambda name, value: self.last_headers.__setitem__(name, value)
        handler.end_headers = lambda: None
        {'GET': handler.do_GET, 'POST': handler.do_POST,
         'DELETE': handler.do_DELETE, 'OPTIONS': handler.do_OPTIONS}[method]()
        raw = handler.wfile.getvalue()
        return status[0], json.loads(raw.decode('utf-8')) if raw else {}

    def test_mansiones_origin_can_upload_and_play_with_cors(self):
        origin = 'http://mansiones.local'
        self.assertEqual(self.request('/api/play', 'OPTIONS', origin=origin)[0], 204)
        self.assertEqual(self.last_headers['Access-Control-Allow-Origin'], origin)
        self.assertIn('Content-Type', self.last_headers['Access-Control-Allow-Headers'])
        code, uploaded = self.request('/upload?filename=mythos.ogg', 'POST', b'ogg', origin)
        self.assertEqual(code, 201)
        self.assertEqual(self.last_headers['Access-Control-Allow-Origin'], origin)
        body = json.dumps({'file_id': uploaded['id'], 'channel': 'lfe'}).encode('utf-8')
        code, playback = self.request('/api/play', 'POST', body, origin)
        self.assertEqual(code, 201)
        self.assertEqual(playback['channels'], ['lfe'])
        self.assertEqual(self.mixer.playbacks[playback['id']]['volume'], 1.0)
        logged = self.request('/api/health')[1]['playback_log'][-1]
        self.assertIn('volumen 100 %', logged)
        self.assertIn('http://127.0.0.1:8080/api/play', logged)
        self.assertIn('"file_id":"%s"' % uploaded['id'], logged)
        self.assertIn('"channel":"lfe"', logged)
        self.assertIn('curl -X POST', self.terminal_log[-1])
        self.assertEqual(self.request('/api/files', origin=origin)[0], 200)
        self.assertEqual(self.last_headers['Access-Control-Allow-Origin'], origin)

    def test_other_web_origin_is_rejected(self):
        origin = 'http://untrusted.example'
        self.assertEqual(self.request('/api/play', 'OPTIONS', origin=origin)[0], 403)
        self.assertNotIn('Access-Control-Allow-Origin', self.last_headers)
        self.assertEqual(self.request('/upload?filename=no.ogg', 'POST', b'ogg', origin)[0], 403)

    def test_reboot_endpoint_returns_instance_and_requests_reboot(self):
        self.reboot_called = False
        self.assertEqual(self.request('/api/health')[1]['instance_id'], 'instance-1')
        code, response = self.request('/api/reboot', 'POST')
        self.assertEqual(code, 202)
        self.assertEqual(response, {'rebooting': True, 'instance_id': 'instance-1'})
        self.assertTrue(self.reboot_called)
        self.reboot_called = False
        self.assertEqual(self.request('/api/reboot', 'POST', origin='http://mansiones.local')[0], 403)
        self.assertFalse(self.reboot_called)
        self.can_reboot = False
        self.assertEqual(self.request('/api/reboot', 'POST')[0], 503)
        self.assertFalse(self.reboot_called)

    def test_upload_then_play_stereo_and_stop(self):
        code, uploaded = self.request('/upload?filename=effect.ogg', 'POST', b'ogg')
        self.assertEqual(code, 201)
        file_id = uploaded['id']
        code, listing = self.request('/api/files')
        self.assertEqual(code, 200)
        self.assertEqual(listing['files'][0]['name'], 'effect.ogg')

        body = json.dumps({'file_id': file_id,
                           'channels': ['front_left', 'rear_left']}).encode('utf-8')
        code, playback = self.request('/api/play', 'POST', body)
        self.assertEqual(code, 201)
        self.assertEqual(playback['channels'], ['front_left', 'rear_left'])
        code, stopped = self.request('/api/playbacks/playback-1', 'DELETE')
        self.assertEqual(code, 200)
        self.assertEqual(stopped['stopped'], 'playback-1')

    def test_invalid_stereo_destinations_are_rejected(self):
        code, uploaded = self.request('/upload?filename=effect.mp3', 'POST', b'mp3')
        body = json.dumps({'file_id': uploaded['id'],
                           'channels': ['center', 'center']}).encode('utf-8')
        code, _ = self.request('/api/play', 'POST', body)
        self.assertEqual(code, 400)

    def test_ost_upload_playlist_controls_and_separate_effects(self):
        code, _ = self.request('/api/ost/upload?filename=bad.wav', 'POST', b'wav')
        self.assertEqual(code, 400)
        first = self.request('/api/ost/upload?filename=01.mp3', 'POST', b'mp3')[1]
        second = self.request('/api/ost/upload?filename=02.mp3', 'POST', b'mp3')[1]
        self.assertEqual(self.request('/api/files')[1]['files'], [])

        channels = json.dumps({'channels': ['front_left', 'rear_left']}).encode('utf-8')
        code, state = self.request('/api/ost/channels', 'POST', channels)
        self.assertEqual(code, 200)
        self.assertEqual(state['channels'], ['front_left', 'rear_left'])
        code, state = self.request('/api/ost/play', 'POST', b'{}')
        self.assertEqual(code, 200)
        self.assertIn('volumen 60 %', self.playback_log[-1])
        self.assertIn('http://127.0.0.1:8080/api/ost/play', self.playback_log[-1])
        self.assertEqual(state['current']['id'], first['id'])
        self.assertEqual(state['position_seconds'], 0.0)
        self.assertIsNone(state['duration_seconds'])
        self.assertEqual(self.mixer.list_playbacks('ost')[0]['channels'],
                         ['front_left', 'rear_left'])
        self.assertEqual(self.request('/api/playbacks')[1]['playbacks'], [])
        effect = self.request('/upload?filename=effect.wav', 'POST', b'wav')[1]
        effect_body = json.dumps({'file_id': effect['id'],
                                  'channel': 'center'}).encode('utf-8')
        self.assertEqual(self.request('/api/play', 'POST', effect_body)[0], 201)
        self.assertEqual(len(self.request('/api/playbacks')[1]['playbacks']), 1)
        self.assertEqual(self.request('/api/playbacks', 'DELETE')[1]['stopped'], 1)
        self.assertEqual(len(self.mixer.list_playbacks('ost')), 1)

        self.mixer.playbacks[state['playback_id']]['position'] = 2.5
        code, paused = self.request('/api/ost/pause', 'POST')
        self.assertEqual(paused['state'], 'paused')
        self.assertEqual(paused['position_seconds'], 2.5)
        reversed_channels = json.dumps({'channels': ['rear_left', 'front_left']}).encode('utf-8')
        rerouted = self.request('/api/ost/channels', 'POST', reversed_channels)[1]
        self.assertEqual(rerouted['state'], 'paused')
        self.assertNotEqual(rerouted['playback_id'], paused['playback_id'])
        old_id = rerouted['playback_id']
        resumed = self.request('/api/ost/play', 'POST', b'{}')[1]
        self.assertEqual(resumed['playback_id'], old_id)
        self.assertEqual(resumed['state'], 'playing')

        self.mixer.complete(old_id)
        self.assertEqual(self.request('/api/ost')[1]['current']['id'], second['id'])
        self.assertEqual(self.request('/api/ost/previous', 'POST')[1]['current']['id'],
                         first['id'])
        self.assertEqual(len(self.mixer.list_playbacks('ost')), 1)

        shuffle = self.request('/api/ost/shuffle', 'POST', b'{"shuffle":true}')[1]
        self.assertTrue(shuffle['shuffle'])
        self.assertEqual(self.request('/api/ost/next', 'POST')[1]['current']['id'],
                         second['id'])

    def test_ost_rejects_duplicate_speakers(self):
        body = b'{"channels":["center","center"]}'
        self.assertEqual(self.request('/api/ost/channels', 'POST', body)[0], 400)

    def test_ost_volume_starts_at_sixty_percent_and_changes_without_restart(self):
        self.request('/api/ost/upload?filename=music.mp3', 'POST', b'mp3')
        self.assertEqual(self.request('/api/ost')[1]['volume'], 60)
        started = self.request('/api/ost/play', 'POST', b'{}')[1]
        playback_id = started['playback_id']
        self.assertEqual(self.mixer.playbacks[playback_id]['volume'], 0.6)
        self.mixer.playbacks[playback_id]['position'] = 2.5

        code, changed = self.request('/api/ost/volume', 'POST', b'{"volume":25}')
        self.assertEqual(code, 200)
        self.assertEqual(changed['volume'], 25)
        self.assertEqual(changed['playback_id'], playback_id)
        self.assertEqual(changed['position_seconds'], 2.5)
        self.assertEqual(self.mixer.playbacks[playback_id]['volume'], 0.25)

        self.request('/api/ost/next', 'POST')
        current_id = self.request('/api/ost')[1]['playback_id']
        self.assertEqual(self.mixer.playbacks[current_id]['volume'], 0.25)

        for value in (b'{"volume":-1}', b'{"volume":101}',
                      b'{"volume":true}', b'{"volume":50.5}', b'{}'):
            self.assertEqual(self.request('/api/ost/volume', 'POST', value)[0], 400)
        self.assertEqual(self.request('/api/ost')[1]['volume'], 25)

    def test_ost_can_restart_after_output_stops(self):
        self.request('/api/ost/upload?filename=music.mp3', 'POST', b'mp3')
        started = self.request('/api/ost/play', 'POST', b'{}')[1]
        self.mixer.stop(started['playback_id'])
        self.assertEqual(self.request('/api/ost')[1]['state'], 'stopped')
        restarted = self.request('/api/ost/play', 'POST', b'{}')[1]
        self.assertEqual(restarted['state'], 'playing')
        self.assertNotEqual(restarted['playback_id'], started['playback_id'])


if __name__ == '__main__':
    unittest.main()
