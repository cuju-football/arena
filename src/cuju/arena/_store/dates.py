# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Resolve dates used to find recordings."""

from datetime import date, timedelta


def day(value: str) -> str:
    """Return a calendar day in the form used by recording dates."""
    if value == 'today':
        return date.today().isoformat()
    if value == 'yesterday':
        return (date.today() - timedelta(days=1)).isoformat()
    day = date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError(value)
    return value
