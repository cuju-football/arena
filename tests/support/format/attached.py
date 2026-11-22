# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""A recording of the folder as the current writer keeps it, the game alone and its attachments.

`convert` takes a recording that keeps its annotations and overlays in sections and its look in
its header, as the files of this folder do, and gives the same match as a recording that holds the
game alone, with the attachments `annotations`, `overlays` and `look` beside it. The bodies are the
sections and the header fields as the file held them, so both readers lay them over the recording
and read the match exactly as they read the file. `ATTACHED` holds the converted `sections-5v5`,
which the tests of both readers and the mock server read, and a test holds it equal to a new
conversion. The files compress with no time of their own, so a conversion gives the same bytes on
every machine.

Run it by hand from the root of the repository to write the folder again:

    python tests/support/format/attached.py
"""

import gzip
import hashlib
import json
import struct
import sys
from pathlib import Path

FOLDER = Path(__file__).parent
ATTACHED = FOLDER / 'attached'
SOURCE = 'sections-5v5.cjr'
# The time that every converted attachment names as its creation.
CREATED = '2026-11-19T12:00:00'
WRITER = 'cuju-arena 0.1.0'
_MAGIC = b'CUJREC1\n'
_SECTION = struct.Struct('<4sI')
# The kind of attachment that each old section becomes.
KINDS = {b'ANNO': 'annotations', b'GRID': 'overlays'}
LOOK_FIELDS = ('kits', 'sky', 'ground')
TEAM_LOOK_FIELDS = ('color', 'secondary', 'style')


def _attachment(kind, key, frames, body):
    header = {
        'attachment': 1,
        'kind': kind,
        'version': 1,
        'recording': key,
        'frames': frames,
        'writer': WRITER,
        'created': CREATED,
    }
    text = json.dumps(header, separators=(',', ':')).encode()
    return gzip.compress(b'CUJATT1\n' + struct.pack('<I', len(text)) + text + body, mtime=0)


def convert(data):
    """Returns the files of a converted recording, `.cjr` and each `<kind>.cja`, by suffix.

    Args:
        data: The bytes of a recording file of version 2 or 3, compressed with gzip.
    """
    plain = gzip.decompress(data)
    (length,) = struct.unpack_from('<I', plain, len(_MAGIC))
    start = len(_MAGIC) + 4
    document = json.loads(plain[start : start + length])
    at = start + length
    sections = []
    while at + _SECTION.size <= len(plain):
        tag, size = _SECTION.unpack_from(plain, at)
        payload = plain[at + _SECTION.size : at + _SECTION.size + size]
        sections.append((tag, payload))
        at += _SECTION.size + size + (-size % 4)
    look = {field: document.pop(field) for field in LOOK_FIELDS if field in document}
    worn = []
    for team in document.get('teams') or []:
        worn.append({field: team.pop(field) for field in TEAM_LOOK_FIELDS if field in team})
    if any(worn):
        look['teams'] = worn
    kept = [(tag, payload) for tag, payload in sections if tag not in KINDS]
    document['sections'] = [tag.decode() for tag, _ in kept[1:]]
    text = json.dumps(document, separators=(',', ':')).encode()
    text += b' ' * (-(len(_MAGIC) + 4 + len(text)) % 4)
    game = _MAGIC + struct.pack('<I', len(text)) + text
    for tag, payload in kept:
        game += _SECTION.pack(tag, len(payload)) + payload + bytes(-len(payload) % 4)
    key = 'sha256:' + hashlib.sha256(game).hexdigest()
    frames = document['frame_count']
    files = {'.cjr': gzip.compress(game, mtime=0)}
    for tag, payload in sections:
        if tag in KINDS:
            files[f'.{KINDS[tag]}.cja'] = _attachment(KINDS[tag], key, frames, payload)
    if look:
        body = json.dumps(look, separators=(',', ':')).encode()
        files['.look.cja'] = _attachment('look', key, frames, body)
    return files


def converted(name=SOURCE):
    """Returns the converted files of a recording of the folder, by suffix."""
    return convert((FOLDER / name).read_bytes())


def main():
    ATTACHED.mkdir(exist_ok=True)
    stem = SOURCE[: -len('.cjr')]
    for suffix, data in converted().items():
        (ATTACHED / f'{stem}{suffix}').write_bytes(data)
    return 0


if __name__ == '__main__':
    sys.exit(main())
