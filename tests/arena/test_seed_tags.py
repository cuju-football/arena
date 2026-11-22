# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""Pin the seed streams used by match requests and presentation."""

import random

from cuju.arena._playing import draw


def test_match_draw_uses_the_original_tag():
    assert draw.generator(17).getstate() == random.Random('cuju.render.draw 17').getstate()
