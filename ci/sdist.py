# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Keeps the source distribution within the package file selection."""

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class SourceArchive(BuildHookInterface):
    """Uses the package selection for files that govern a checkout."""

    def initialize(self, version, build_data):
        forced = build_data['force_include']
        for path, name in list(forced.items()):
            if name in ('.gitignore', '.hgignore'):
                del forced[path]
