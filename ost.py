from __future__ import absolute_import, division, print_function

import math
import os
import random
import subprocess
import threading

from mixer import CHANNELS


class OSTController(object):
    """Server-side playlist so REST clients and the web share one player."""

    def __init__(self, files, mixer, ffprobe='ffprobe'):
        self.files = files
        self.mixer = mixer
        self.lock = threading.RLock()
        self.channels = ('front_left', 'front_right')
        self.volume = 60
        self.current_file_id = None
        self.playback_id = None
        self.status = 'stopped'
        self.shuffle = False
        self.remaining = []
        self.history = []
        self.ffprobe = ffprobe
        self.durations = {}

    def _duration_for(self, file_id, path):
        if file_id in self.durations:
            return self.durations[file_id]
        duration = None
        if self.ffprobe:
            command = [self.ffprobe, '-v', 'error', '-show_entries',
                       'format=duration', '-of',
                       'default=noprint_wrappers=1:nokey=1', path]
            try:
                with open(os.devnull, 'wb') as null:
                    output = subprocess.check_output(command, stderr=null)
                value = float(output.strip())
                if value > 0 and not math.isnan(value) and not math.isinf(value):
                    duration = value
            except (OSError, subprocess.CalledProcessError, ValueError):
                pass
        self.durations[file_id] = duration
        return duration

    def describe(self):
        with self.lock:
            playlist = self.files.list_files()
            current = next((item for item in playlist
                            if item['id'] == self.current_file_id), None)
            active = (self.playback_id is not None and
                      self.mixer.has_playback(self.playback_id))
            position = (self.mixer.position_seconds(self.playback_id)
                        if active else None)
            return {
                'playlist': playlist,
                'current': current,
                'state': self.status if active else 'stopped',
                'shuffle': self.shuffle,
                'channels': list(self.channels),
                'volume': self.volume,
                'playback_id': self.playback_id if active else None,
                'position_seconds': position if position is not None else 0.0,
                'duration_seconds': (self.durations.get(self.current_file_id)
                                     if current is not None else None),
            }

    def _start(self, file_id, paused=False):
        path = self.files.resolve(file_id)
        if path is None:
            raise ValueError('OST track not found')
        self._duration_for(file_id, path)
        if self.playback_id is not None:
            self.mixer.stop(self.playback_id)
        self.playback_id = None
        self.status = 'stopped'
        playback = self.mixer.play(file_id, path, self.channels, kind='ost',
                                   on_complete=self._on_complete, paused=paused,
                                   volume=self.volume / 100.0)
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

    def set_volume(self, volume):
        if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
            raise ValueError('volume must be an integer between 0 and 100')
        with self.lock:
            self.volume = volume
            if self.playback_id is not None:
                self.mixer.set_volume(self.playback_id, volume / 100.0)
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
