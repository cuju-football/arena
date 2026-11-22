"""Writes model.pt, an untrained network of 460 inputs and 19 outputs.

A trained policy replaces it, exported the way most training libraries export, for example
torch.jit.script of the actor network over simple115v2 with the frame stack, 4 times 115 values.
Run with python make_model.py in this folder. It needs torch.
"""

import torch


class Actor(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.body = torch.nn.Sequential(
            torch.nn.Linear(460, 64), torch.nn.ReLU(), torch.nn.Linear(64, 19)
        )

    def forward(self, observation: torch.Tensor) -> torch.Tensor:
        return self.body(observation)


torch.manual_seed(0)
torch.jit.script(Actor()).save('model.pt')
