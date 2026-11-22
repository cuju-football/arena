# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Records the digests of the standard analysis outputs."""

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import cuju.arena
from cuju.arena._format import frames
from cuju.arena._moments import listing, search

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / 'plugins' / 'analysis.json'
SAMPLES = Path(cuju.arena.__file__).resolve().parent / 'samples'
RECORDINGS = (
    *sorted(SAMPLES.rglob('*.cjr')),
    Path(__file__).parent / 'format' / 'sections-5v5.cjr',
)
SIGNALS = 'value-drop surprise entropy:>0'


def _json(value):
    return json.dumps(value, separators=(',', ':')).encode()


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _parquet_input():
    arrow = ModuleType('pyarrow')
    arrow.table = lambda columns: columns
    arrow.parquet = ModuleType('pyarrow.parquet')
    arrow.parquet.write_table = lambda table, sink: sink.write(_json(table))
    return patch.dict(sys.modules, {'pyarrow': arrow, 'pyarrow.parquet': arrow.parquet})


def cases():
    from cuju.arena._export import analytics

    found = {}
    for path in RECORDINGS:
        name = path.stem
        every = 10 if name == 'sections-5v5' else 100
        frames_ = frames.read(path)
        entry, stats = search._read_recording(path)
        rate = entry['header']['steps_per_second']
        signals = search.signal_moments(
            search.parse(SIGNALS), entry['signals'], rate, search.thresholds()
        )
        outputs = {
            'catalog': _digest(_json(entry['moments'])),
            'listing': _digest(_json(listing.of_frames(frames_, name))),
            'signal_moments': _digest(_json(signals)),
            'signal_summary': _digest(_json(entry['signals'])),
            'statistics': _digest(_json(stats)),
            'tables': {},
        }
        for file in analytics.FILES:
            if file in analytics.PAIRS or file == 'metadata':
                data, _ = analytics.file_of(frames_, name, file, 'csv', 'meters', every)
                outputs['tables'][f'{file}.{analytics.extension(file, "csv")}'] = _digest(data)
                continue
            for writer in ('csv', 'jsonl'):
                data, _ = analytics.file_of(frames_, name, file, writer, 'meters', every)
                outputs['tables'][f'{file}.{writer}'] = _digest(data)
            with _parquet_input():
                data, _ = analytics.file_of(frames_, name, file, 'parquet', 'meters', every)
            outputs['tables'][f'{file}.parquet.input'] = _digest(data)
        found[name] = outputs
    return found


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(dir=ROOT) as home:
        with patch.dict('os.environ', {'CUJU_ARENA_HOME': home, 'CUJU_RENDER_HOME': home}):
            GOLDEN.write_text(json.dumps(cases(), indent=2) + '\n', encoding='utf-8')
