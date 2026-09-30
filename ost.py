from __future__ import absolute_import, division, print_function

import random
import threading

from mixer import CHANNELS


class OSTController(object):
    """Server-side playlist so REST clients and the web share one player."""

    def __init__(self, files, mixer):
        self.files = files
        self.mixer = mixer
        self.lock = threading.RLock()
        self.channels = ('front_left', 'front_right')
        self.current_file_id = None
        self.playback_id = None
        self.status = 'stopped'
        self.shuffle = False
        self.remaining = []
        self.history = []

    def describe(self):
        with self.lock:
            playlist = self.files.list_files()
            current = next((item for item in playlist
                            if item['id'] == self.current_file_id), None)
            active = (self.playback_id is not None and
                      self.mixer.has_playback(self.playback_id))
            return {
                'playlist': playlist,
                'current': current,
                'state': self.status if active else 'stopped',
                'shuffle': self.shuffle,
                'channels': list(self.channels),
                'playback_id': self.playback_id if active else None,
            }

    def _start(self, file_id, paused=False):
        path = self.files.resolve(file_id)
        if path is None:
            raise ValueError('OST track not found')
        if self.playback_id is not None:
            self.mixer.stop(self.playback_id)
        self.playback_id = None
        self.status = 'stopped'
        playback = self.mixer.play(file_id, path, self.channels, kind='ost',
                                   on_complete=self._on_complete, paused=paused)
        self.current_file_id = file_id
        self.playback_id = playback['id']
        self.status = 'paused' if paused else 'playing'

    def play(self, file_id=None):
        with self.lock:
            playlist = self.files.list_files()
            ids = [item['id'] for item in playlist]
            if not ids:
                raise ValueError('Upload an MP3 to the OST playlist first')
            if file_id is not None and file_id not in ids:
                raise ValueError('OST track not found')
            selected = file_id or self.current_file_id or ids[0]
            if selected not in ids:
                selected = ids[0]
            if (selected == self.current_file_id and self.status == 'paused'
                    and self.playback_id is not None
                    and self.mixer.resume(self.playback_id)):
                self.status = 'playing'
            elif not (selected == self.current_file_id and
                      self.status == 'playing' and self.playback_id is not None
                      and self.mixer.has_playback(self.playback_id)):
                self.history = []
                self.remaining = []
                self._start(selected)
            return self.describe()

    def pause(self):
        with self.lock:
            if self.status == 'playing' and self.playback_id is not None:
                if self.mixer.pause(self.playback_id):
                    self.status = 'paused'
                else:
                    self.playback_id = None
                    self.status = 'stopped'
            return self.describe()

    def stop(self):
        with self.lock:
            if self.playback_id is not None:
                self.mixer.stop(self.playback_id)
            self.playback_id = None
            self.status = 'stopped'
            return self.describe()

    def _next_id(self, ids):
        if self.current_file_id not in ids:
            return ids[0]
        if not self.shuffle or len(ids) == 1:
            return ids[(ids.index(self.current_file_id) + 1) % len(ids)]
        self.remaining = [item for item in self.remaining
                          if item in ids and item != self.current_file_id]
        if not self.remaining:
            self.remaining = [item for item in ids if item != self.current_file_id]
        selected = random.choice(self.remaining)
        self.remaining.remove(selected)
        return selected

    def _advance(self, backwards=False):
        ids = [item['id'] for item in self.files.list_files()]
        if not ids:
            self.stop()
            raise ValueError('OST playlist is empty')
        if backwards and self.history:
            selected = self.history.pop()
        elif backwards and self.current_file_id in ids:
            selected = ids[(ids.index(self.current_file_id) - 1) % len(ids)]
        else:
            selected = self._next_id(ids)
            if self.current_file_id in ids and selected != self.current_file_id:
                self.history.append(self.current_file_id)
        self._start(selected)
        return self.describe()

    def next(self):
        with self.lock:
            return self._advance()

    def previous(self):
        with self.lock:
            return self._advance(backwards=True)

    def set_shuffle(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('shuffle must be true or false')
        with self.lock:
            self.shuffle = enabled
            self.remaining = []
            return self.describe()

    def set_channels(self, channels):
        if (not isinstance(channels, list) or len(channels) != 2 or
                any(channel not in CHANNELS for channel in channels) or
                channels[0] == channels[1]):
            raise ValueError('Choose two different stereo speakers')
        with self.lock:
            self.channels = tuple(channels)
            if (self.playback_id is not None and
                    self.mixer.has_playback(self.playback_id)):
                was_paused = self.status == 'paused'
                self._start(self.current_file_id, paused=was_paused)
            else:
                self.playback_id = None
                self.status = 'stopped'
            return self.describe()

    def _on_complete(self, playback):
        with self.lock:
            if playback.id != self.playback_id:
                return
            self.playback_id = None
            if playback.error:
                self.status = 'stopped'
                return
            if self.status == 'playing':
                self._advance()
