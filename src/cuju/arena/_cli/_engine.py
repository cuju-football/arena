# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Check the engine files before a package command runs."""

from cuju import engine_status

from . import contract


def damaged_engine(prog: str, as_json: bool) -> int | None:
    """Returns an environment error if an upgrade removed files from the engine."""
    if engine_status.intact():
        return None
    return contract.fail(
        contract.Parser(prog=prog),
        RuntimeError('the cuju engine installation is damaged, run cuju-repair'),
        contract.ENVIRONMENT,
        as_json=as_json,
        verbose=False,
        hint=engine_status.repair_command(),
    )
