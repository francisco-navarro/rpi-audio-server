from __future__ import absolute_import, division, print_function

import json
import os
import shutil
import sys
import tempfile
import unittest
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audio_server import FileStore, Handler
from ost import OSTController


class FakeMixer(object):
    def __init__(self):
        self.playbacks = {}
        self.callbacks = {}
        self.next_id = 0

    def play(self, file_id, path, channels, kind='effect', on_complete=None,
             paused=False):
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
                  'state': 'paused' if paused else 'playing', 'kind': kind}
        self.playbacks[result['id']] = result
        self.callbacks[result['id']] = on_complete
        return result

    def list_playbacks(self, kind=None):
        return [item for item in self.playbacks.values()
                if kind is None or item['kind'] == kind]

    def has_playback(self, playback_id):
        return playback_id in self.playbacks

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
        self.ost = OSTController(self.ost_files, self.mixer)

    def tearDown(self):
        shutil.rmtree(self.temp)

    def request(self, path, method='GET', body=None):
        handler = Handler.__new__(Handler)
        handler.path = path
        handler.rfile = BytesIO(body if body is not None else b'')
        handler.wfile = BytesIO()
        handler.headers = {'Host': '127.0.0.1:8080'}
        if body is not None:
            handler.headers['Content-Length'] = str(len(body))
        handler.server = type('Server', (object,), {})()
        handler.server.files = self.files
        handler.server.ost_files = self.ost_files
        handler.server.mixer = self.mixer
        handler.server.ost = self.ost
        status = []
        handler.send_response = lambda code: status.append(code)
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        {'GET': handler.do_GET, 'POST': handler.do_POST,
         'DELETE': handler.do_DELETE}[method]()
        return status[0], json.loads(handler.wfile.getvalue().decode('utf-8'))

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
        self.assertEqual(state['current']['id'], first['id'])
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

        code, paused = self.request('/api/ost/pause', 'POST')
        self.assertEqual(paused['state'], 'paused')
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
