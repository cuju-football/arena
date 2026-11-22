"""The Safety format of the tests, a Kaggle main.py that breaks a ceiling on request.

Its option `mode` breaks one of the renderer's ceilings once `after` steps have passed, so the
tests reach each ceiling forfeit. `report` writes what the process sees into its cache folder.
"""

import json
import os
import subprocess
import sys
import threading
import time

from cuju.arena.formats import v1 as formats

# What the process holds on purpose, so nothing frees it early.
HELD = []


def accept(folder, team):
    formats.kaggle_agent(folder)
    return formats.accept(deterministic=True)


def _report(match):
    names = (
        'OMP_NUM_THREADS',
        'TF_NUM_INTEROP_THREADS',
        'XLA_FLAGS',
        'CUDA_VISIBLE_DEVICES',
        'JAX_PLATFORMS',
        'CUJU_ARENA_CPUS',
        'SAFETY_SECRET',
        'SAFETY_OTHER',
        'PYTHONPATH',
    )
    found = {
        'environment': {name: os.environ.get(name) for name in names},
        'cpus': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
        'pid': os.getpid(),
    }
    if match.options['mode'] == 'report':
        with formats.Child(match.folder, 'inner.py', 'Echo') as child:
            found['child'], _ = child.call('environment', list(names))
        if os.environ.get('SAFETY_TORCH'):
            import torch

            found['torch'] = [torch.get_num_threads(), torch.get_num_interop_threads()]
            layer = torch.nn.Linear(115, 19)
            for _ in range(50):
                layer(torch.zeros(1, 115))
    (match.cache / f'report-{match.side}.json').write_text(json.dumps(found), encoding='utf-8')


def start(folder, match):
    if match.cache is not None:
        _report(match)
    player = Player(folder, match)
    if match.options['after'] < 0:
        # A mode with `after = -1` breaks its ceiling as the side loads.
        player.fail()
    return player


class Player:
    def __init__(self, folder, match):
        self.folder = folder
        self.agent = formats.kaggle_agent(folder)
        self.mode = match.options['mode']
        self.after = match.options['after']
        self.calls = 0
        self.children = []

    def act(self, step):
        if step.number >= self.after:
            self.fail()
        if not step.used:
            return None
        obs = formats.kaggle_observation(step.raw, step=self.calls)
        self.calls += 1
        return formats.as_actions(self.agent(obs), len(step.raw), step.action_set, negative=True)

    def fail(self):
        if self.mode == 'spawn':
            subprocess.run([sys.executable, '-c', 'pass'], check=False)
        if self.mode == 'helper':
            import ctypes.util

            ctypes.util.find_library('c')
            self.mode = 'play'
        if self.mode == 'children':
            while True:
                self.children.append(formats.Child(self.folder, 'inner.py', 'Echo'))
        if self.mode == 'memory':
            size = 64 * 1024 * 1024
            for _ in range(256):
                block = bytearray(size)
                # Each page is written, so the memory is resident and not only reserved.
                block[::4096] = b'\x01' * (size // 4096)
                HELD.append(block)
                time.sleep(0.05)
        if self.mode == 'threads':
            for _ in range(40):
                thread = threading.Thread(target=time.sleep, args=(60,), daemon=True)
                thread.start()
                HELD.append(thread)
            time.sleep(30)

    def close(self):
        for child in self.children:
            child.close()
