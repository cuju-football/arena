"""Writes three untrained recurrent policies that read the minimap.

Each takes one player's observation, 72 by 96 by 4, and its state, and returns 19 logits and the
next state, as a MAPPO actor exported with torch.jit.script does. Run with python make_policies.py
in this folder. It needs torch.
"""

import pathlib

import torch


class Actor(torch.nn.Module):
    def __init__(self, hidden: int = 128):
        super().__init__()
        self.conv = torch.nn.Sequential(
            torch.nn.Conv2d(4, 16, 8, 4),
            torch.nn.ReLU(),
            torch.nn.Conv2d(16, 32, 4, 2),
            torch.nn.ReLU(),
            torch.nn.Flatten(),
        )
        self.cell = torch.nn.GRUCell(32 * 7 * 10, hidden)
        self.head = torch.nn.Linear(hidden, 19)

    def forward(self, observation: torch.Tensor, state: torch.Tensor):
        features = self.conv(observation.permute(0, 3, 1, 2) / 255.0)
        state = self.cell(features, state)
        return self.head(state), state


torch.manual_seed(0)
folder = pathlib.Path('policies')
folder.mkdir(exist_ok=True)
for name in ('defender', 'midfielder', 'forward'):
    torch.jit.script(Actor()).save(str(folder / f'{name}.pt'))
