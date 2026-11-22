# Copyright 2026 The cuju Authors
# SPDX-License-Identifier: Apache-2.0
"""The recording format, its codec, the recorder's writer, filters and annotations.

`recording` reads states into frames and writes and reads the files. `codec` codes the frames
of version 2, `players` the lanes of the players, `actions` the actions of the agents, `tracks`
the tracks of version 3, `overlays` the overlays of a policy and `sky` the sky that a match
draws.
"""

KEYFRAME_INTERVAL = 100
