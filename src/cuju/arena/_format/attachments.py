# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Attachments, the files beside a recording that carry what is not the game itself.

A recording holds the game alone, its frames, the lanes of the players, the actions of the
agents, the state of the kick off and the names of the teams and players. What a model or a
viewer adds, the annotations of a policy, its overlays and the look of the match, travels in
attachments, one file `<stem>.<kind>.cja` beside `<stem>.cjr` for each kind, under the protocol
`attachment/1`, which the repository's `docs/recording-format.md` describes.

An attachment is keyed by the digest of its recording, the SHA-256 of the recording's bytes once
decompressed. The library never rewrites a recording, so the digest holds for as long as the
recording does, and a reader that finds an attachment whose key names another recording leaves it
out with a note, so an attachment never reaches a recording it was not made for. An attachment
never changes a frame, and the game file stays as it was written.

A recording of an earlier version keeps its annotations and overlays as sections of its own and
its look in its header. The readers here take those parts as attachments of origin `embedded`,
read only, so an old recording opens as it always did and no writer writes them inline again.

Producers make the attachments of a recording as it closes. They are plugins of the kind
`attachment/1`, each named by the kind of attachment it writes, as `plugins` describes. The
plugin `builtin` offers `annotations` and `overlays`, whose implementations stand in `STANDARD`,
and a plugin `acme` that offers `heat` writes the attachment `acme.heat`. The kind `look` belongs
to the renderer, which registers its producer with `register` when it loads.
"""

from __future__ import annotations

import dataclasses
import glob
import gzip
import hashlib
import io
import json
import logging
import re
import struct
import threading
import time
import zipfile
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cuju.errors import ArgumentError, StateError

from .. import _version
from .._text.messages import MessageError
from . import annotations as _annotations
from . import overlays as _overlays
from . import recording as _recording

_LOGGER = logging.getLogger('cuju')

MAGIC = b'CUJATT1\n'
# The protocol of the attachments, and the major that a file names in its header.
PROTOCOL = 'attachment/1'
MAJOR = 1
SUFFIX = '.cja'
# The limits that a reader holds an attachment to, before and after decompression.
MAX_HEADER_BYTES = 64 << 10
MAX_BYTES = 64 << 20
# A shipped kind is lowercase letters, digits and dashes, and the kind of a plugin is the plugin's
# name and its kind joined by a dot.
_KIND = re.compile(r'[a-z0-9-]+(\.[a-z0-9-]+)?')
# Where an attachment comes from, a file beside its recording or a part inside an older recording.
FILE, EMBEDDED = 'file', 'embedded'
# The kinds that the arena ships and the kind that the renderer ships.
ANNOTATIONS, OVERLAYS, LOOK = 'annotations', 'overlays', 'look'
# The section of an older recording that each embedded kind reads, and that the body of each
# kind keeps the layout of.
SECTIONS = {ANNOTATIONS: _annotations.TAG, OVERLAYS: _overlays.TAG}
_LENGTH = struct.Struct('<I')
# A bundle, a ZIP archive of a recording and its attachments, which export writes and import reads.
BUNDLE_SUFFIX = '.cjz'
BUNDLE_MAJOR = 1
BUNDLE_RECORDING = 'recording.cjr'
BUNDLE_INDEX = 'bundle.json'
BUNDLE_FOLDER = 'attachments/'
# The most attachments that a bundle holds.
MAX_BUNDLED = 64


@dataclasses.dataclass
class Attachment:
    """One attachment of a recording, as a file holds it or as an older recording embeds it.

    Attributes:
        kind: The kind, such as `annotations`, `overlays` or `look`.
        version: The version of the kind, whose layout the body follows.
        recording: The key, `sha256:` and the digest of the recording it belongs to.
        frames: The number of frames of that recording.
        writer: The program that wrote it, such as `cuju-arena 0.1.0`.
        created: When it was written, as local time `YYYY-MM-DDTHH:MM:SS`.
        origin: `file` for a file beside the recording, `embedded` for a part of an older one.
        path: The file, or `None` for an embedded attachment.
    """

    kind: str
    version: int
    recording: str
    frames: int
    writer: str
    created: str
    origin: str = FILE
    path: Path | None = None
    _body: bytes | None = dataclasses.field(default=None, repr=False)

    @property
    def body(self) -> bytes:
        """The body, which a file reads only when it is asked for."""
        if self._body is None:
            self._body = _read(self.path, body=True)[1] if self.path is not None else b''
        return self._body

    def header(self) -> dict[str, Any]:
        """Returns the header of the attachment, as its file keeps it."""
        found = {
            'attachment': MAJOR,
            'kind': self.kind,
            'version': self.version,
            'recording': self.recording,
            'frames': self.frames,
            'writer': self.writer,
            'created': self.created,
        }
        if self.origin != FILE:
            found['origin'] = self.origin
        return found

    def summary(self) -> dict[str, Any]:
        """Returns what a listing shows of the attachment, its header and the size of its body."""
        return {**self.header(), 'origin': self.origin, 'bytes': len(self.body)}


@dataclasses.dataclass
class Context:
    """What the producers of attachments read as a recording closes.

    Attributes:
        path: The recording file, written already.
        header: Its header, as the file holds it.
        digest: Its key, `sha256:` and the digest of its bytes once decompressed.
        presentation: What the header that the writer was given held beside the game, which the
            file leaves out, as `recording.split` returns it.
        annotations: The records of the annotations, as `annotations.decode` gives them.
        overlays: The records of the overlays of a policy, as `overlays.encode` takes them.
    """

    path: Path
    header: dict[str, Any]
    digest: str
    presentation: dict[str, Any] = dataclasses.field(default_factory=dict)
    annotations: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    overlays: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class Producer:
    """The producer of one kind of attachment.

    An implementation of the kind `attachment/1` that a plugin provides holds `produce` and may
    hold `version`, 1 by default, and `writer`, by default the plugin and its version.

    Attributes:
        kind: The kind it writes.
        version: The version of the kind it writes.
        produce: Returns the body of the attachment for a `Context`, or `None` for no attachment.
        writer: The program that the files name as their writer.
        plugin: The plugin that provides it, `None` for one that `register` took.
    """

    kind: str
    version: int
    produce: Callable[[Context], bytes | None]
    writer: str = f'cuju-arena {_version.__version__}'
    plugin: str | None = None


_PRODUCERS: dict[str, Producer] = {}
_LOCK = threading.RLock()
# The digest of each recording read, under its size and time.
_DIGESTS: dict[str, tuple[tuple[int, int], str]] = {}


def check_kind(kind: Any) -> str:
    """Returns a kind of attachment after checking its form.

    Raises:
        MessageError: If the kind is no text of lowercase letters, digits and dashes, with at most
            one dot between the name of a plugin and its kind.
    """
    if not isinstance(kind, str) or len(kind) > 80 or not _KIND.fullmatch(kind):
        raise MessageError('server.library.attachment_kind', kind=repr(kind))
    return kind


def register(
    kind: str,
    version: int,
    produce: Callable[[Context], bytes | None],
    writer: str | None = None,
) -> Producer:
    """Registers the producer of a kind of attachment, in place of any it had, and returns it.

    Args:
        kind: The kind, as `check_kind` takes it.
        version: The version of the kind that the producer writes, a whole number from 1.
        produce: Returns the body for a `Context`, or `None` when the recording has none.
        writer: The program that the files name, by default the arena and its version.
    """
    check_kind(kind)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ArgumentError(f'expected the version of a kind as a whole number from 1, {version!r}')
    found = Producer(kind, version, produce, writer or Producer.writer)
    with _LOCK:
        _PRODUCERS[kind] = found
    return found


def producers() -> dict[str, Producer]:
    """Returns the producers by kind, those of the plugins and those that `register` took.

    A plugin that is broken or that `[plugins] disabled` turns off offers none. A kind that both
    give is the plugin's.
    """
    from .. import plugins as _plugins

    with _LOCK:
        found = dict(_PRODUCERS)
    try:
        held = [plugin for plugin in _plugins.plugins() if plugin.broken is None]
        off = set(_plugins.disabled())
    except Exception as error:
        _LOGGER.warning('cuju: the plugins of attachments were not read: %s', error)
        return found
    for plugin in held:
        if plugin.id in off and not plugin.builtin:
            continue
        for (made, name), implementation in plugin.provided.items():
            if made != 'attachment':
                continue
            kind = plugin.qualify(name)
            if kind == 'render.look' and plugin.source == 'entry point':
                kind = LOOK
            version = getattr(implementation, 'version', 1)
            writer = getattr(implementation, 'writer', None) or ' '.join(
                part for part in (plugin.id, plugin.version) if part
            )
            if _KIND.fullmatch(kind) and isinstance(version, int) and version >= 1:
                found[kind] = Producer(kind, version, implementation.produce, writer, plugin.id)
    return found


def path_of(recording: Path, kind: str) -> Path:
    """Returns the file of a kind of attachment of a recording, `<stem>.<kind>.cja` beside it."""
    recording = Path(recording)
    return recording.with_name(f'{recording.stem}.{check_kind(kind)}{SUFFIX}')


def digest(recording: Path) -> str:
    """Returns the key of a recording file, read once while its size and time stay the same.

    Raises:
        StateError: If the file is larger than a recording may be, or its compression is broken.
    """
    recording = Path(recording)
    stat = recording.stat()
    stamp = (stat.st_size, stat.st_mtime_ns)
    with _LOCK:
        held = _DIGESTS.get(str(recording))
    if held is not None and held[0] == stamp:
        return held[1]
    found = hashlib.sha256()
    with _recording._open(recording) as raw:
        stream = _recording._Budget(raw)
        while block := stream.read(1 << 20):
            found.update(block)
    value = 'sha256:' + found.hexdigest()
    with _LOCK:
        _DIGESTS[str(recording)] = (stamp, value)
    return value


def remember(recording: Path, value: str) -> None:
    """Keeps the key of a recording file just written, so no reader decompresses it again."""
    stat = Path(recording).stat()
    with _LOCK:
        _DIGESTS[str(recording)] = ((stat.st_size, stat.st_mtime_ns), value)


def encode(attachment: Attachment, body: bytes) -> bytes:
    """Returns the bytes of an attachment file, gzip-compressed with no time of its own."""
    text = json.dumps(attachment.header(), separators=(',', ':')).encode()
    plain = MAGIC + _LENGTH.pack(len(text)) + text + body
    return gzip.compress(plain, compresslevel=6, mtime=0)


def _parse(plain: bytes, path: Path | None) -> tuple[dict[str, Any], int]:
    """Returns the header of the decompressed bytes of an attachment and where its body starts."""
    if not plain.startswith(MAGIC) or len(plain) < len(MAGIC) + _LENGTH.size:
        raise MessageError('server.library.attachment_file', path=str(path))
    (length,) = _LENGTH.unpack_from(plain, len(MAGIC))
    start = len(MAGIC) + _LENGTH.size
    if length > MAX_HEADER_BYTES or len(plain) < start + length:
        raise MessageError('server.library.attachment_file', path=str(path))
    try:
        header = json.loads(plain[start : start + length], parse_constant=_recording._constant)
    except (ValueError, RecursionError):
        raise MessageError('server.library.attachment_file', path=str(path)) from None
    whole = (int,)
    if not (
        isinstance(header, dict)
        and header.get('attachment') == MAJOR
        and isinstance(header.get('kind'), str)
        and _KIND.fullmatch(header['kind'])
        and all(
            isinstance(header.get(field), whole) and not isinstance(header[field], bool)
            for field in ('version', 'frames')
        )
        and isinstance(header.get('recording'), str)
        and header['recording'].startswith('sha256:')
        and all(isinstance(header.get(field, ''), str) for field in ('writer', 'created'))
    ):
        raise MessageError('server.library.attachment_file', path=str(path))
    return header, start + length


def decode(data: bytes, path: Path | None = None) -> Attachment:
    """Returns the attachment that the bytes of a file hold, compressed or not.

    Raises:
        MessageError: If the bytes are no attachment of cuju or reach past the limits of one.
    """
    if data[:2] == b'\x1f\x8b':
        try:
            stream = gzip.GzipFile(fileobj=io.BytesIO(data))
            plain = stream.read(MAX_BYTES + 1)
        except (OSError, EOFError, zlib.error):
            raise MessageError('server.library.attachment_file', path=str(path)) from None
    else:
        plain = data
    if len(plain) > MAX_BYTES:
        raise MessageError('server.library.attachment_file', path=str(path))
    header, start = _parse(plain, path)
    return _attachment(header, path, plain[start:])


def _attachment(header: dict[str, Any], path: Path | None, body: bytes | None) -> Attachment:
    return Attachment(
        kind=header['kind'],
        version=header['version'],
        recording=header['recording'],
        frames=header['frames'],
        writer=header.get('writer', ''),
        created=header.get('created', ''),
        origin=FILE,
        path=path,
        _body=body,
    )


def _read(path: Path, body: bool) -> tuple[dict[str, Any], bytes | None]:
    """Returns the header of an attachment file and, when asked, its body."""
    if path.stat().st_size > MAX_BYTES:
        raise MessageError('server.library.attachment_file', path=str(path))
    try:
        with gzip.open(path, 'rb') as stream:
            if not body:
                plain = stream.read(len(MAGIC) + _LENGTH.size)
                if len(plain) == len(MAGIC) + _LENGTH.size and plain.startswith(MAGIC):
                    (length,) = _LENGTH.unpack_from(plain, len(MAGIC))
                    plain += stream.read(min(length, MAX_HEADER_BYTES + 1))
            else:
                plain = stream.read(MAX_BYTES + 1)
    except (OSError, EOFError, zlib.error):
        raise MessageError('server.library.attachment_file', path=str(path)) from None
    if len(plain) > MAX_BYTES:
        raise MessageError('server.library.attachment_file', path=str(path))
    header, start = _parse(plain, path)
    return header, plain[start:] if body else None


def read(path: Path) -> Attachment:
    """Returns the attachment of a file, with its body read when it is first asked for.

    Raises:
        MessageError: If the file is no attachment of cuju.
    """
    path = Path(path)
    return _attachment(_read(path, body=False)[0], path, None)


def _frames_of(header: Mapping[str, Any]) -> int:
    count = header.get('frame_count')
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def write(
    recording: Path,
    kind: str,
    version: int,
    body: bytes,
    writer: str | None = None,
    digest_: str | None = None,
) -> Path:
    """Writes an attachment beside a recording, through a temporary file, and returns its path.

    Args:
        recording: The recording file.
        kind: The kind, as `check_kind` takes it.
        version: The version of the kind that the body follows.
        body: The body.
        writer: The program that writes it, by default the arena and its version.
        digest_: The key of the recording when the caller knows it, which must be the key of the
            file, else it is read from the file.

    Raises:
        MessageError: If the key given is not the key of the recording.
    """
    recording = Path(recording)
    found = digest(recording)
    if digest_ is not None and digest_ != found:
        raise MessageError(
            'server.library.attachment_key', path=str(recording), found=digest_, expected=found
        )
    attachment = Attachment(
        kind=check_kind(kind),
        version=version,
        recording=found,
        frames=_frames_of(_recording.read_header(recording)),
        writer=writer or Producer.writer,
        created=time.strftime('%Y-%m-%dT%H:%M:%S'),
    )
    target = path_of(recording, kind)
    temporary = target.with_name(target.name + '.part')
    temporary.write_bytes(encode(attachment, body))
    temporary.replace(target)
    return target


def files(recording: Path) -> dict[str, Path]:
    """Returns the attachment files beside a recording by kind, checked by their names alone."""
    recording = Path(recording)
    found = {}
    pattern = f'{glob.escape(recording.stem)}.*{SUFFIX}'
    for path in sorted(recording.parent.glob(pattern)):
        kind = path.name[len(recording.stem) + 1 : -len(SUFFIX)]
        if _KIND.fullmatch(kind):
            found[kind] = path
    return found


def section_signature(recording: Path) -> list[int]:
    """Returns the kind, size and modification time of each section attachment."""
    found = []
    for index, kind in enumerate(SECTIONS):
        try:
            stat = path_of(recording, kind).stat()
        except OSError:
            continue
        found.extend((index, stat.st_size, stat.st_mtime_ns))
    return found


def embedded(recording: Path, header: dict[str, Any] | None = None, inline: Any = None) -> dict:
    """Returns the parts of an older recording that read as attachments, by kind.

    The sections `ANNO` and `GRID` read as `annotations` and `overlays`, and the look that the
    header holds, its kits, sky, ground and what the teams wear, reads as `look`. A recording
    that the current writer wrote holds none of them.

    Args:
        recording: The recording file.
        header: Its header, read from the file when not given.
        inline: Its sections by tag, as `recording._read` returns them, read when not given.
    """
    recording = Path(recording)
    header = _recording.read_header(recording) if header is None else header
    found: dict[str, Attachment] = {}
    frames = _frames_of(header)

    def part(kind: str, body: bytes) -> Attachment:
        return Attachment(kind, 1, '', frames, 'embedded', '', EMBEDDED, None, body)

    if header.get('version', 1) != 1:
        tags = tuple(SECTIONS.values())
        if inline is None:
            inline = _recording._read(recording, *tags)[1]
        for kind, tag in SECTIONS.items():
            if inline.get(tag) is not None:
                found[kind] = part(kind, inline[tag])
    presentation = _recording.split(header)[1]
    if presentation:
        found[LOOK] = part(LOOK, json.dumps(presentation, separators=(',', ':')).encode())
    return found


@dataclasses.dataclass
class Found:
    """The attachments of a recording and the notes of those left out.

    Attributes:
        attachments: The attachments by kind, a file whose key fits before an embedded part.
        notes: For each file left out, its `kind`, `path` and the `reason`, `key` for a key of
            another recording, `frames` for another number of frames and `file` for a file that
            is no attachment.
    """

    attachments: dict[str, Attachment]
    notes: list[dict[str, Any]]


def find(recording: Path, header: dict[str, Any] | None = None, inline: Any = None) -> Found:
    """Returns the attachments of a recording, the files beside it and the parts it embeds.

    Each file is checked against the key of the recording and its number of frames, and a file
    that fails is left out with a note and a warning in the logger `cuju`, never applied.

    Args:
        recording: The recording file.
        header: Its header, read from the file when not given.
        inline: Its sections by tag, as `recording._read` returns them, read when not given.
    """
    recording = Path(recording)
    header = _recording.read_header(recording) if header is None else header
    beside = files(recording)
    attachments: dict[str, Attachment] = {}
    notes = []
    if beside:
        expected = digest(recording)
        for kind, path in beside.items():
            try:
                attachment = read(path)
            except MessageError:
                notes.append({'kind': kind, 'path': str(path), 'reason': 'file'})
                continue
            reason = None
            if attachment.kind != kind:
                reason = 'file'
            elif attachment.recording != expected:
                reason = 'key'
            elif attachment.frames != _frames_of(header):
                reason = 'frames'
            if reason is not None:
                notes.append(
                    {'kind': kind, 'path': str(path), 'reason': reason, 'key': attachment.recording}
                )
                _LOGGER.warning('cuju: left out the attachment %s, %s', path.name, reason)
                continue
            attachments[kind] = attachment
    for kind, attachment in embedded(recording, header, inline).items():
        attachments.setdefault(kind, attachment)
    return Found(attachments, notes)


def load(recording: Path, kind: str) -> Attachment | None:
    """Returns one attachment of a recording, a file whose key fits before an embedded part.

    Raises:
        MessageError: If the file of the kind belongs to another recording or is no attachment.
    """
    found = find(recording)
    for note in found.notes:
        if note['kind'] == kind and kind not in found.attachments:
            if note['reason'] == 'file':
                raise MessageError('server.library.attachment_file', path=note['path'])
            if note['reason'] == 'frames':
                raise MessageError('server.library.attachment_frames', path=note['path'])
            raise MessageError(
                'server.library.attachment_key',
                path=note['path'],
                found=note['key'],
                expected=digest(recording),
            )
    return found.attachments.get(kind)


def _records(recording: Path, kind: str, decode_: Callable[[bytes], Any], inline: Any) -> list:
    """Returns the records of a kind whose body keeps the layout of its old section.

    Raises:
        StateError: If the file is no recording or reaches past the limits of one.
    """
    attachment = find(recording, inline=inline).attachments.get(kind)
    if attachment is None or attachment.version > 1:
        return []
    return decode_(attachment.body)


def annotations(recording: Path, inline: Any = None) -> list[dict[str, Any]]:
    """Returns the records of the annotations of a recording, from its attachment or its section.

    Args:
        recording: The recording file.
        inline: Its sections by tag, when the caller read them already.
    """
    return _records(recording, ANNOTATIONS, _annotations.decode, inline)


def overlays(recording: Path, inline: Any = None) -> list[dict[str, Any]]:
    """Returns the overlays of a recording, from its attachment or its section.

    Args:
        recording: The recording file.
        inline: Its sections by tag, when the caller read them already.
    """
    return _records(recording, OVERLAYS, _overlays.decode, inline)


def produce(context: Context) -> list[str]:
    """Runs every producer for a recording that just closed and writes what they return.

    A producer that raises leaves its kind out, with a warning in the logger `cuju`, and the
    recording and the other attachments stay. A plugin other than `builtin` whose producer
    raises is marked failing, as `plugins.call` marks one. A kind without a producer in this
    process, such as the look of a recording made without the renderer, is drawn when the
    recording is first shown.

    Returns:
        The kinds written, sorted.
    """
    written = []
    found = producers()
    for kind, producer in sorted(found.items()):
        try:
            body = producer.produce(context)
            if body is not None:
                write(context.path, kind, producer.version, body, producer.writer, context.digest)
                written.append(kind)
        except Exception as error:
            _LOGGER.warning(
                'cuju: the attachment %s of %s was not written, %s: %s',
                kind,
                context.path.name,
                type(error).__name__,
                error,
            )
            if producer.plugin is not None and producer.plugin != 'builtin':
                from .. import plugins as _plugins

                _plugins.mark_failing(producer.plugin, f'attachment {kind}, {error}')
    return written


@dataclasses.dataclass
class Written:
    """What `write_recording` wrote.

    Attributes:
        key: The key of the recording, `sha256:` and its digest.
        header: The header of the recording, as the file holds it.
        kinds: The kinds of the attachments written beside it, sorted.
    """

    key: str
    header: dict[str, Any]
    kinds: list[str]


def write_recording(
    path: Path,
    head: bytes,
    frames: list[bytes],
    version: int = _recording.VERSION,
    annotations: list[dict[str, Any]] | None = None,
) -> Written:
    """Writes a recording, the game alone, and the attachments that the producers make for it.

    The recording takes what `recording.write` writes. The producers then read what the header
    held beside the game, the annotations and the overlays that `frames` carries as a
    `recording.Frames`, and their attachments go beside it.

    Args:
        path: The path of the recording file.
        head: The magic, length and header, as `recording.header` returns them.
        frames: The frames, plain or as a `recording.Frames`.
        version: The version of the recording file.
        annotations: The records of the annotations, for the attachment `annotations`.
    """
    path = Path(path)
    key = _recording.write(path, head, frames, version)
    remember(path, key)
    header = _recording.read_header(path)
    carried = frames if isinstance(frames, _recording.Frames) else None
    context = Context(
        path,
        header,
        key,
        _recording.split(_recording._document(head))[1],
        list(annotations or ()),
        list((carried.overlays if carried is not None else None) or ()),
    )
    return Written(key, header, produce(context))


def copy(source: Path, target: Path) -> list[str]:
    """Copies the attachment files of a recording beside a copy of it, and returns their kinds.

    The copy of the recording holds the same bytes, so its key is the same, and only the files
    whose key fits the source go along.
    """
    found = find(source).attachments
    copied = []
    for kind, attachment in found.items():
        if attachment.origin != FILE or attachment.path is None:
            continue
        destination = path_of(target, kind)
        temporary = destination.with_name(destination.name + '.part')
        temporary.write_bytes(attachment.path.read_bytes())
        temporary.replace(destination)
        copied.append(kind)
    return sorted(copied)


def remove(recording: Path) -> list[str]:
    """Deletes the attachment files of a recording, and returns their kinds."""
    removed = []
    for kind, path in files(recording).items():
        path.unlink(missing_ok=True)
        removed.append(kind)
    return removed


def body(kind: str, records: Any) -> bytes:
    """Returns the body of a kind whose body keeps the layout of a section, from its records.

    The bodies of `annotations` and `overlays` keep the layouts of the sections `ANNO` and `GRID`,
    which a live stream carries as well.

    Raises:
        ArgumentError: If the kind is neither of the two, or a record does not fit its layout.
    """
    if kind == ANNOTATIONS:
        return _annotations.encode(records)
    if kind == OVERLAYS:
        return _overlays.encode(records)
    raise ArgumentError(f'expected the kind annotations or overlays, found {kind!r}')


def bundle(recording: Path) -> bytes:
    """Returns a bundle of a recording and its attachment files, a ZIP archive of `.cjz`.

    The archive holds the recording file as it is, `recording.cjr`, each attachment file whose
    key fits as `attachments/<kind>.cja`, and `bundle.json`, which names the bundle's major, the
    key of the recording and each attachment. The parts that an older recording embeds stay
    inside its file.
    """
    recording = Path(recording)
    found = find(recording)
    kept = [
        attachment
        for attachment in found.attachments.values()
        if attachment.origin == FILE and attachment.path is not None
    ]
    index = {
        'bundle': BUNDLE_MAJOR,
        'recording': digest(recording),
        'attachments': [
            {key: attachment.header()[key] for key in ('kind', 'version', 'writer', 'created')}
            for attachment in sorted(kept, key=lambda attachment: attachment.kind)
        ],
        'writer': Producer.writer,
        'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr(BUNDLE_INDEX, json.dumps(index, indent=1))
        archive.write(recording, BUNDLE_RECORDING)
        for attachment in sorted(kept, key=lambda attachment: attachment.kind):
            archive.write(attachment.path, f'{BUNDLE_FOLDER}{attachment.kind}{SUFFIX}')
    return out.getvalue()


def is_bundle(path: Path) -> bool:
    """Returns whether a file is a ZIP archive that holds the index of a bundle."""
    try:
        with zipfile.ZipFile(path) as archive:
            return BUNDLE_INDEX in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def unbundle(path: Path, folder: Path, stem: str = 'bundle') -> Path:
    """Takes a bundle apart into a folder, `<stem>.cjr` and its attachments beside it.

    Every attachment is checked against the key of the recording before anything is written, so
    a bundle whose attachment belongs to another recording is refused whole.

    Returns:
        The recording file in the folder.

    Raises:
        MessageError: If the file is no bundle of cuju, holds parts beyond the limits, or holds
            an attachment of another recording.
    """
    path, folder = Path(path), Path(folder)
    try:
        with zipfile.ZipFile(path) as archive:
            members = {info.filename: info for info in archive.infolist()}
            kinds = {}
            for name, info in members.items():
                if name in (BUNDLE_INDEX, BUNDLE_RECORDING):
                    continue
                kind = name[len(BUNDLE_FOLDER) : -len(SUFFIX)]
                fits = name.startswith(BUNDLE_FOLDER) and name.endswith(SUFFIX)
                if not (fits and _KIND.fullmatch(kind)) or info.file_size > MAX_BYTES:
                    raise MessageError('server.library.bundle', path=str(path))
                kinds[kind] = name
            recording = members.get(BUNDLE_RECORDING)
            index_info = members.get(BUNDLE_INDEX)
            if (
                recording is None
                or index_info is None
                or recording.file_size > _recording.MAX_BYTES
                or index_info.file_size > MAX_HEADER_BYTES
                or len(kinds) > MAX_BUNDLED
            ):
                raise MessageError('server.library.bundle', path=str(path))
            index = json.loads(archive.read(BUNDLE_INDEX))
            if not isinstance(index, dict) or index.get('bundle') != BUNDLE_MAJOR:
                raise MessageError('server.library.bundle', path=str(path))
            target = folder / f'{stem}.cjr'
            target.write_bytes(archive.read(BUNDLE_RECORDING))
            parts = {kind: archive.read(name) for kind, name in kinds.items()}
    except (OSError, zipfile.BadZipFile, ValueError, RecursionError):
        raise MessageError('server.library.bundle', path=str(path)) from None
    header = _recording.read_header(target)
    expected = digest(target)
    for kind, data in parts.items():
        attachment = decode(data, Path(f'{path.name}/{kinds[kind]}'))
        if attachment.kind != kind:
            raise MessageError('server.library.attachment_file', path=f'{path.name}/{kinds[kind]}')
        if attachment.recording != expected:
            raise MessageError(
                'server.library.attachment_key',
                path=f'{path.name}/{kinds[kind]}',
                found=attachment.recording,
                expected=expected,
            )
        if attachment.frames != _frames_of(header):
            where = f'{path.name}/{kinds[kind]}'
            raise MessageError('server.library.attachment_frames', path=where)
    for kind, data in parts.items():
        path_of(target, kind).write_bytes(data)
    return target


def _annotations_of(context: Context) -> bytes | None:
    return body(ANNOTATIONS, context.annotations) if context.annotations else None


def _overlays_of(context: Context) -> bytes | None:
    if not context.overlays:
        return None
    try:
        return body(OVERLAYS, context.overlays)
    except ArgumentError as error:
        raise StateError(str(error)) from None


# The producers of the attachments that the arena ships, which the plugin `builtin` provides.
STANDARD = {
    ANNOTATIONS: Producer(ANNOTATIONS, 1, _annotations_of),
    OVERLAYS: Producer(OVERLAYS, 1, _overlays_of),
}
