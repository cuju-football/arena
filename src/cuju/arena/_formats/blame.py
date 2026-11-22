# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0

"""Blame for an exception, by where it arose, the submission's copy or the format's folder.

The traceback is read from its innermost frame outwards. The first frame whose file lies in the
submission's copy decides for the team, the first in the format's folder for the format, and
frames of the standard library, the site packages, cuju and the renderer are passed over. With no
owned frame at all, as for an error inside a built-in format's own code, the failure is the
format's. A syntax error names its file, which decides the same way.

This module uses the standard library alone, since the child of a format imports it.
"""

from __future__ import annotations

import traceback
from pathlib import Path

# Who an exception belongs to.
TEAM = 'team'
FORMAT = 'format'


def _under(path: str | None, root: Path | None) -> bool:
    if not path or root is None:
        return False
    try:
        return Path(path).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def blame(error: BaseException, submission: Path | None, format_folder: Path | None) -> str:
    """Returns `TEAM` or `FORMAT`, whose code raised an exception.

    Args:
        error: The exception, with its traceback.
        submission: The submission's folder, its read-only copy where the format runs in a
            process, or `None`.
        format_folder: The format's folder, or `None` for a built-in format, whose code is the
            renderer's own.
    """
    team = None if submission is None else Path(submission).resolve()
    own = None if format_folder is None else Path(format_folder).resolve()
    if isinstance(error, SyntaxError):
        if _under(error.filename, team):
            return TEAM
        if _under(error.filename, own):
            return FORMAT
    for frame in reversed(traceback.extract_tb(error.__traceback__)):
        if _under(frame.filename, team):
            return TEAM
        if _under(frame.filename, own):
            return FORMAT
    return FORMAT


def last_line(error: BaseException) -> str:
    """Returns the exception as one line, its type and its text."""
    text = ' '.join(str(error).split())
    return f'{type(error).__name__}: {text}' if text else type(error).__name__
