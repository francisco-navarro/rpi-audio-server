from __future__ import absolute_import, division, print_function

import array
from collections import deque
import os
import subprocess
import sys
import threading
import time
import uuid

try:
    import queue
except ImportError:  # Python 2.7
    import Queue as queue


RATE = 48000
CHANNEL_COUNT = 6
FRAMES_PER_BLOCK = 960  # 20 ms
SAMPLE_BYTES = 2
BLOCK_SECONDS = float(FRAMES_PER_BLOCK) / RATE

# This is the ALSA 5.1 order used by speaker-test with the tested HDMI device.
CHANNELS = {
    'front_left': 0,
    'front_right': 1,
    'rear_left': 2,
    'rear_right': 3,
    'center': 4,
    'lfe': 5,
}


def _samples(data):
    result = array.array('h')
    if hasattr(result, 'frombytes'):
        result.frombytes(data)
    else:
        result.fromstring(data)
    if sys.byteorder != 'little':
        result.byteswap()
    return result


def _as_bytes(samples):
    if sys.byteorder != 'little':
        samples.byteswap()
    if hasattr(samples, 'tobytes'):
        return samples.tobytes()
    return samples.tostring()


def mix_block(inputs):
    """Mix [(pcm_bytes, destination_indices), ...] into one ALSA 5.1 block.

    Mono inputs contain one sample per frame; stereo inputs contain left then
    right. When several sounds use one speaker, average them to avoid clipping.
    """
    totals = [0] * (FRAMES_PER_BLOCK * CHANNEL_COUNT)
    counts = [0] * CHANNEL_COUNT
    for pcm, destinations in inputs:
        source_channels = len(destinations)
        expected = FRAMES_PER_BLOCK * source_channels * SAMPLE_BYTES
        if len(pcm) != expected:
            raise ValueError('PCM block has the wrong length')
        source = _samples(pcm)
        for destination in destinations:
            counts[destination] += 1
        for frame in range(FRAMES_PER_BLOCK):
            source_offset = frame * source_channels
            output_offset = frame * CHANNEL_COUNT
            for source_index, destination in enumerate(destinations):
                totals[output_offset + destination] += source[source_offset + source_index]

    output = array.array('h')
    for frame in range(FRAMES_PER_BLOCK):
        offset = frame * CHANNEL_COUNT
        for destination in range(CHANNEL_COUNT):
            count = counts[destination]
            value = totals[offset + destination] // count if count else 0
            output.append(max(-32768, min(32767, value)))
    return _as_bytes(output)


def _read_exact(stream, size):
    chunks = []
    remaining = size
    while remaining:
        part = stream.read(remaining)
        if not part:
            break
        chunks.append(part)
        remaining -= len(part)
    return b''.join(chunks)


class Playback(object):
    def __init__(self, file_id, path, channels):
        self.id = uuid.uuid4().hex
        self.file_id = file_id
        self.path = path
        self.channels = tuple(channels)
        self.destination_indices = tuple(CHANNELS[name] for name in channels)
        self.blocks = queue.Queue(maxsize=12)
        self.stop_event = threading.Event()
        self.done_event = threading.Event()
        self.process = None
        self.state = 'loading'
        self.error = None
        self.lfe_level = 0.0

    def describe(self):
        return {
            'id': self.id,
            'file_id': self.file_id,
            'channels': list(self.channels),
            'state': self.state,
        }

    def stop(self):
        self.stop_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def filter_lfe(self, pcm):
        if 'lfe' not in self.channels:
            return pcm
        samples = _samples(pcm)
        width = len(self.channels)
        source_index = self.channels.index('lfe')
        level = self.lfe_level
        # One-pole low pass: 48 kHz / (2π × 64) ≈ 119 Hz.
        for frame in range(FRAMES_PER_BLOCK):
            index = frame * width + source_index
            level += (samples[index] - level) / 64.0
            samples[index] = int(level)
        self.lfe_level = level
        return _as_bytes(samples)


class Mixer(object):
    def __init__(self, device, max_playbacks=4, ffmpeg='ffmpeg', aplay='aplay',
                 debug_audio=False):
        self.device = device
        self.max_playbacks = max_playbacks
        self.ffmpeg = ffmpeg
        self.aplay = aplay
        self.debug_audio = debug_audio
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.playbacks = {}
        self.sink = None
        self.sink_reader = None
        self.alsa_log = deque(maxlen=80)
        self.ffmpeg_log = deque(maxlen=80)
        self.last_error = None
        self.last_signal_peak = 0
        self.last_signal_at = None
        self.decode_starvations = 0
        self.late_blocks = 0
        self.alsa_underruns = 0
        self.last_mix_ms = 0.0
        self.max_mix_ms = 0.0
        self.last_write_ms = 0.0
        self.max_write_ms = 0.0
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()

    def play(self, file_id, path, channels):
        if len(channels) not in (1, 2):
            raise ValueError('Select one or two channels')
        if any(name not in CHANNELS for name in channels):
            raise ValueError('Unknown channel')
        if len(set(channels)) != len(channels):
            raise ValueError('Stereo destinations must be different')
        with self.lock:
            if len(self.playbacks) >= self.max_playbacks:
                raise OverflowError('Maximum simultaneous sounds reached')
            if self.closed.is_set():
                raise RuntimeError('Audio engine is closed')
            playback = Playback(file_id, path, channels)
            self.last_error = None
            self.playbacks[playback.id] = playback
            self.wake.set()
        worker = threading.Thread(target=self._decode, args=(playback,))
        worker.daemon = True
        worker.start()
        return playback.describe()

    def list_playbacks(self):
        with self.lock:
            return [item.describe() for item in self.playbacks.values()]

    def stop(self, playback_id):
        with self.lock:
            playback = self.playbacks.pop(playback_id, None)
        if playback is None:
            return False
        playback.stop()
        return True

    def stop_all(self):
        with self.lock:
            ids = list(self.playbacks)
        for playback_id in ids:
            self.stop(playback_id)
        return len(ids)

    def health(self):
        with self.lock:
            sink_running = self.sink is not None and self.sink.poll() is None
            return {
                'device': self.device,
                'sink_running': sink_running,
                'active_playbacks': len(self.playbacks),
                'last_error': self.last_error,
                'last_signal_peak': self.last_signal_peak,
                'last_signal_at': self.last_signal_at,
                'alsa_log': list(self.alsa_log),
                'ffmpeg_log': list(self.ffmpeg_log),
                'decode_starvations': self.decode_starvations,
                'late_blocks': self.late_blocks,
                'alsa_underruns': self.alsa_underruns,
                'last_mix_ms': self.last_mix_ms,
                'max_mix_ms': self.max_mix_ms,
                'last_write_ms': self.last_write_ms,
                'max_write_ms': self.max_write_ms,
            }

    def _log(self, kind, line):
        with self.lock:
            (self.alsa_log if kind == 'ALSA' else self.ffmpeg_log).append(line)
            if kind == 'ALSA' and ('underrun' in line.lower() or
                                   'xrun' in line.lower()):
                self.alsa_underruns += 1
        if self.debug_audio:
            try:
                os.write(2, ('[%s] %s\n' % (kind, line)).encode('utf-8', 'replace'))
            except OSError:
                pass

    def _collect_output(self, stream, kind, prefix='', local=None):
        try:
            for raw in iter(stream.readline, b''):
                line = raw.decode('utf-8', 'replace').rstrip('\r\n')[:500]
                if line:
                    if local is not None:
                        local.append(line)
                    self._log(kind, prefix + line)
        finally:
            stream.close()

    def close(self):
        self.closed.set()
        self.stop_all()
        self.wake.set()
        self.thread.join(2)
        self._close_sink()

    def _decode(self, playback):
        command = [self.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'info',
                   '-nostats', '-i',
                   playback.path, '-map', '0:a:0', '-vn']
        command += ['-ac', str(len(playback.channels)), '-ar', str(RATE),
                    '-f', 's16le', '-acodec', 'pcm_s16le', 'pipe:1']
        null = None
        reader = None
        lines = deque(maxlen=80)
        try:
            null = open(os.devnull, 'wb')
            process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, stdin=null)
            playback.process = process
            self._log('FFmpeg', '[%s] Iniciando decodificación: %s' %
                      (playback.id[:8], playback.path))
            reader = threading.Thread(target=self._collect_output,
                                      args=(process.stderr, 'FFmpeg',
                                            '[%s] ' % playback.id[:8], lines))
            reader.daemon = True
            reader.start()
            block_bytes = FRAMES_PER_BLOCK * len(playback.channels) * SAMPLE_BYTES
            while not playback.stop_event.is_set():
                chunk = _read_exact(process.stdout, block_bytes)
                if not chunk:
                    break
                if len(chunk) < block_bytes:
                    chunk += b'\x00' * (block_bytes - len(chunk))
                while not playback.stop_event.is_set():
                    try:
                        playback.blocks.put(chunk, timeout=0.1)
                        self.wake.set()
                        break
                    except queue.Full:
                        pass
            if playback.stop_event.is_set() and process.poll() is None:
                process.terminate()
            return_code = process.wait()
            reader.join(1)
            if return_code and not playback.stop_event.is_set():
                playback.error = 'FFmpeg no pudo leer el archivo: %s' % (
                    lines[-1] if lines else 'salida %s' % return_code)
                with self.lock:
                    self.last_error = playback.error
        except (OSError, IOError) as exc:
            playback.error = 'Cannot start FFmpeg: %s' % exc
            with self.lock:
                self.last_error = playback.error
        finally:
            playback.done_event.set()
            self.wake.set()
            if playback.process is not None and playback.process.stdout:
                playback.process.stdout.close()
            if null is not None:
                null.close()
            if reader is not None and reader.is_alive():
                reader.join(1)

    def _open_sink(self):
        if self.sink is not None:
            if self.sink.poll() is None:
                return True
            if self.sink_reader is not None:
                self.sink_reader.join(1)
            self.last_error = 'Salida HDMI detenida: %s' % (
                self.alsa_log[-1] if self.alsa_log else
                'aplay terminó con código %s' % self.sink.returncode)
            self._close_sink()
            return False
        self._close_sink()
        command = [self.aplay, '-v', '-D', self.device, '-t', 'raw',
                   '-f', 'S16_LE', '-r', str(RATE), '-c', str(CHANNEL_COUNT),
                   '-m', 'FL,FR,RL,RR,FC,LFE', '-']
        try:
            with self.lock:
                self.alsa_log.clear()
            self._log('ALSA', 'Iniciando aplay con dispositivo %s' % self.device)
            self.sink = subprocess.Popen(command, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT)
            self.sink_reader = threading.Thread(target=self._collect_output,
                                                args=(self.sink.stdout, 'ALSA'))
            self.sink_reader.daemon = True
            self.sink_reader.start()
            return True
        except OSError as exc:
            self.last_error = 'No se pudo iniciar la salida HDMI: %s' % exc
            self._log('ALSA', self.last_error)
            return False

    def _close_sink(self):
        sink = self.sink
        reader = self.sink_reader
        self.sink = None
        self.sink_reader = None
        if sink is None:
            return
        try:
            if sink.stdin:
                sink.stdin.close()
        except IOError:
            pass
        # Let aplay drain its ALSA buffer. Terminating it immediately can
        # discard an entire short effect before the receiver plays it.
        if sink.poll() is None:
            deadline = time.time() + 2.0
            while sink.poll() is None and time.time() < deadline:
                time.sleep(0.02)
            if sink.poll() is None:
                sink.terminate()
        return_code = None
        try:
            return_code = sink.wait()
        except OSError:
            pass
        if reader is not None:
            reader.join(1)
        if return_code and return_code > 0 and self.last_error is None:
            detail = self.alsa_log[-1] if self.alsa_log else ''
            self.last_error = 'aplay terminó con código %s%s' % (
                return_code, ': %s' % detail if detail else '')

    def _run(self):
        next_tick = time.time()
        while not self.closed.is_set():
            with self.lock:
                playbacks = list(self.playbacks.values())
            if not playbacks:
                self._close_sink()
                self.wake.wait(0.2)
                self.wake.clear()
                next_tick = time.time()
                continue

            if not self._open_sink():
                self.stop_all()
                continue

            inputs = []
            finished = []
            for playback in playbacks:
                if playback.stop_event.is_set():
                    finished.append(playback.id)
                    continue
                try:
                    block = playback.blocks.get_nowait()
                    playback.state = 'playing'
                    inputs.append((playback.filter_lfe(block), playback.destination_indices))
                except queue.Empty:
                    if playback.done_event.is_set():
                        finished.append(playback.id)
                    elif playback.state == 'playing':
                        with self.lock:
                            self.decode_starvations += 1

            for playback_id in finished:
                self.stop(playback_id)

            mix_started = time.time()
            mixed = mix_block(inputs)
            mix_ms = (time.time() - mix_started) * 1000
            with self.lock:
                self.last_mix_ms = mix_ms
                self.max_mix_ms = max(self.max_mix_ms, mix_ms)
            try:
                write_started = time.time()
                self.sink.stdin.write(mixed)
                self.sink.stdin.flush()
                write_ms = (time.time() - write_started) * 1000
                with self.lock:
                    self.last_write_ms = write_ms
                    self.max_write_ms = max(self.max_write_ms, write_ms)
                if inputs:
                    peak = max(abs(sample) for sample in _samples(mixed))
                    if peak:
                        with self.lock:
                            self.last_signal_peak = peak
                            self.last_signal_at = time.time()
            except (OSError, IOError) as exc:
                self._close_sink()
                with self.lock:
                    detail = self.alsa_log[-1] if self.alsa_log else ''
                with self.lock:
                    self.last_error = 'Fallo de salida HDMI: %s%s' % (
                        exc, ' · %s' % detail if detail else '')
                self.stop_all()

            next_tick += BLOCK_SECONDS
            now = time.time()
            if now - next_tick > 0.005:
                with self.lock:
                    self.late_blocks += 1
            if next_tick > now:
                time.sleep(next_tick - now)
            else:
                next_tick = now
