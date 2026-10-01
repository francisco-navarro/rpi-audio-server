from __future__ import absolute_import, division, print_function

import array
from collections import deque
import os
import signal
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mixer import CHANNELS, FRAMES_PER_BLOCK, Mixer, Playback, mix_block


def pcm(values):
    samples = array.array('h', values)
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples.tobytes() if hasattr(samples, 'tobytes') else samples.tostring()


def decode(data):
    samples = array.array('h')
    if hasattr(samples, 'frombytes'):
        samples.frombytes(data)
    else:
        samples.fromstring(data)
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples


class MixerTests(unittest.TestCase):
    def test_stereo_left_and_right_go_to_ordered_destinations(self):
        source = pcm([1000, -2000] * FRAMES_PER_BLOCK)
        result = decode(mix_block([(source, (CHANNELS['front_left'],
                                              CHANNELS['rear_left']))]))
        self.assertEqual(list(result[:6]), [1000, 0, -2000, 0, 0, 0])
        self.assertEqual(list(result[-6:]), [1000, 0, -2000, 0, 0, 0])

    def test_four_sounds_can_overlap_without_overflow(self):
        source = pcm([30000] * FRAMES_PER_BLOCK)
        inputs = [(source, (CHANNELS['center'],)) for _ in range(4)]
        result = decode(mix_block(inputs))
        self.assertEqual(list(result[:6]), [0, 0, 0, 0, 30000, 0])

    def test_overlapping_stereo_and_mono_average_only_shared_speaker(self):
        stereo = pcm([-3001, 7000] * FRAMES_PER_BLOCK)
        mono = pcm([1000] * FRAMES_PER_BLOCK)
        result = decode(mix_block([
            (stereo, (CHANNELS['front_left'], CHANNELS['rear_left'])),
            (mono, (CHANNELS['front_left'],)),
        ]))
        self.assertEqual(list(result[:6]), [-1001, 0, 7000, 0, 0, 0])

    def test_all_six_destinations_are_separate(self):
        for name, index in CHANNELS.items():
            source = pcm([100] * FRAMES_PER_BLOCK)
            result = decode(mix_block([(source, (index,))]))
            frame = list(result[:6])
            self.assertEqual(frame[index], 100, name)
            self.assertEqual(sum(frame), 100, name)

    def test_lfe_filter_affects_only_lfe_side_of_stereo(self):
        playback = Playback('file', 'file.wav', ('front_left', 'lfe'))
        source = pcm([12000, 12000] * FRAMES_PER_BLOCK)
        result = decode(playback.filter_lfe(source))
        self.assertEqual(result[0], 12000)
        self.assertLess(result[1], 12000)
        self.assertGreater(result[-1], result[1])

    def test_output_is_allowed_to_drain_before_exit(self):
        class Sink(object):
            def __init__(self):
                self.drained = False
                self.terminated = False
                self.stdin = self

            def close(self):
                timer = threading.Timer(0.05, self.finish)
                timer.start()

            def finish(self):
                self.drained = True

            def poll(self):
                return 0 if self.drained else None

            def terminate(self):
                self.terminated = True
                self.drained = True

            def wait(self):
                return 0

        mixer = Mixer.__new__(Mixer)
        mixer.sink_lock = threading.RLock()
        mixer.sink = Sink()
        mixer.sink_reader = None
        sink = mixer.sink
        mixer._close_sink()
        self.assertTrue(sink.drained)
        self.assertFalse(sink.terminated)

    def test_forced_close_kills_an_aplay_process_that_ignores_sigterm(self):
        script = ('import signal,sys,time; '
                  'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                  'sys.stdout.write("ready\\n"); sys.stdout.flush(); '
                  'time.sleep(30)')
        sink = subprocess.Popen([sys.executable, '-c', script],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(sink.stdout.readline(), b'ready\n')
            mixer = Mixer.__new__(Mixer)
            mixer.sink_lock = threading.RLock()
            mixer.sink = sink
            mixer.sink_reader = None
            mixer.alsa_log = deque()
            mixer.last_error = None
            started = time.time()
            mixer._close_sink(force=True)
            self.assertLess(time.time() - started, 2)
            self.assertIsNotNone(sink.poll())
            self.assertEqual(sink.returncode, -signal.SIGKILL)
        finally:
            if sink.poll() is None:
                sink.kill()
                sink.wait()
            sink.stdout.close()


if __name__ == '__main__':
    unittest.main()
