"""The child of the Probe format, which runs in the submission's folder and answers calls."""

from pathlib import Path


class Echo:
    def __init__(self, folder):
        self.folder = Path(folder)

    def echo(self, value):
        return value

    def where(self):
        return str(Path.cwd())

    def slow(self, seconds):
        import time

        time.sleep(seconds)
        return seconds

    def fail(self):
        raise ValueError('the format child failed')

    def team(self):
        import mine

        return mine.boom()
