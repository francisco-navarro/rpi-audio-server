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


class FakeMixer(object):
    def __init__(self):
        self.playbacks = {}

    def play(self, file_id, path, channels):
        if len(channels) not in (1, 2):
            raise ValueError('Select one or two channels')
        if len(set(channels)) != len(channels):
            raise ValueError('Stereo destinations must be different')
        if any(name not in ('front_left', 'front_right', 'rear_left',
                            'rear_right', 'center', 'lfe') for name in channels):
            raise ValueError('Unknown channel')
        result = {'id': 'playback-1', 'file_id': file_id,
                  'channels': channels, 'state': 'loading'}
        self.playbacks[result['id']] = result
        return result

    def list_playbacks(self):
        return list(self.playbacks.values())

    def stop(self, playback_id):
        return self.playbacks.pop(playback_id, None) is not None

    def stop_all(self):
        count = len(self.playbacks)
        self.playbacks.clear()
        return count

    def health(self):
        return {'sink_running': True}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.files = FileStore(self.temp)
        self.mixer = FakeMixer()

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
        handler.server.mixer = self.mixer
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


if __name__ == '__main__':
    unittest.main()
