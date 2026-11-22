"""The child of the Safety format, which answers calls in the submission's folder."""

import os


class Echo:
    def __init__(self, folder):
        self.folder = folder

    def echo(self, value):
        return value

    def environment(self, names):
        found = {name: os.environ.get(name) for name in names}
        if hasattr(os, 'sched_getaffinity'):
            found['cpus'] = sorted(os.sched_getaffinity(0))
        return found
