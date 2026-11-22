"""The Policy format uses one trained network for the active player.

The method is the network, called once a step on the observation that gfootball would have given
it. The bookkeeping is here, reading policy.toml, loading the file on its device once for the run,
the frame stack fed every step, the choice of an action, the seed for sampling, and the time that
a step may take.
"""

import numpy

from cuju.arena.formats import v1 as formats

REPRESENTATIONS = {'simple115', 'simple115v2', 'extracted'}


def read(folder):
    """The settings of policy.toml, or a ValueError that says what does not fit."""
    spec = formats.read_manifest(folder, 'policy.toml')
    model = folder / str(spec.get('model', ''))
    if model.suffix not in ('.pt', '.onnx') or not model.is_file():
        raise ValueError(
            f'policy.toml must name a .pt or .onnx file in the folder, found {spec.get("model")!r}'
        )
    if spec.get('representation') not in REPRESENTATIONS:
        raise ValueError(f'representation must be one of {", ".join(sorted(REPRESENTATIONS))}')
    if spec.get('device', 'cpu') not in ('cpu', 'cuda'):
        raise ValueError("device must be 'cpu' or 'cuda'")
    return {
        'model': model,
        'representation': spec['representation'],
        'stacked': bool(spec.get('stacked', False)),
        'sample': bool(spec.get('sample', False)),
        'device': spec.get('device', 'cpu'),
    }


def load(spec, threads):
    """The network as a function from a batch of observations to logits, on its device."""
    if spec['model'].suffix == '.onnx':
        onnxruntime = formats.require(
            'onnxruntime', hint='install onnxruntime, or onnxruntime-gpu for cuda'
        )
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        providers = (
            ['CUDAExecutionProvider'] if spec['device'] == 'cuda' else ['CPUExecutionProvider']
        )
        session = onnxruntime.InferenceSession(str(spec['model']), options, providers=providers)
        name = session.get_inputs()[0].name
        return lambda batch: session.run(None, {name: batch.astype(numpy.float32)})[0]
    torch = formats.require('torch', hint='install torch')
    network = torch.jit.load(str(spec['model']), map_location=spec['device']).eval()

    def run(batch):
        with torch.no_grad():
            tensor = torch.as_tensor(batch, dtype=torch.float32, device=spec['device'])
            return network(tensor).cpu().numpy()

    return run


def gpu_visible(spec):
    """Whether the framework of the model sees a GPU in this process."""
    if spec['model'].suffix == '.onnx':
        onnxruntime = formats.require('onnxruntime')
        return 'CUDAExecutionProvider' in onnxruntime.get_available_providers()
    return formats.require('torch').cuda.is_available()


def accept(folder, team):
    """Checks policy.toml and the device, then plays 50 steps against the built-in AI."""
    try:
        spec = read(folder)
    except (ValueError, formats.ManifestError) as error:
        return formats.refuse('load', str(error), detail='policy.toml')
    if team.control.players != 1:
        return formats.refuse(
            'rule',
            'a single policy plays the active player alone, set Control to 1 player',
            detail='players',
        )
    if spec['device'] == 'cuda' and not gpu_visible(spec):
        return formats.refuse(
            'load',
            'policy.toml asks for cuda, and no GPU is visible here',
            detail='gpu',
            environment=True,
        )
    smoke = formats.test_match('builtin', steps=50)
    if smoke.forfeit:
        return formats.refuse(
            smoke.forfeit.kind, smoke.forfeit.message, detail=smoke.forfeit.detail
        )
    slowest = max(smoke.seconds, default=0.0) * 1000
    found = [
        formats.problem('note', f'50 steps, the slowest {slowest:.0f} ms', 'note', detail='smoke')
    ]
    return formats.accept(
        players=1,
        deterministic=not spec['sample'] and spec['device'] == 'cpu',
        problems=found,
    )


def start(folder, match):
    return Player(read(folder), match)


class Player:
    """One side of one match, the network and the observer of its representation."""

    def __init__(self, spec, match):
        self.run = load(spec, match.threads)
        self.observer = formats.Observer(spec['representation'], stacked=spec['stacked'])
        self.sample = spec['sample']
        self.rng = numpy.random.default_rng(match.policy_seed)
        self.limit = match.options['step_seconds']
        stacked = ', stacked' if spec['stacked'] else ''
        print(f'{spec["model"].name} on {spec["device"]}, {spec["representation"]}{stacked}')

    def reset(self):
        self.observer.reset()

    def act(self, step):
        # The observer stacks every step, so it reads each one, whether or not its answer is used.
        batch = numpy.stack(self.observer.per_player(step))
        if step.renderer_seconds is not None and step.renderer_seconds > self.limit:
            raise formats.Forfeit(
                'timeout',
                f'step {step.number - 1} took {step.renderer_seconds:.2f} s, '
                f'the limit is {self.limit:g} s',
            )
        if not step.used:
            return None
        logits = self.run(batch)
        if self.sample:
            weights = numpy.exp(logits - logits.max(axis=1, keepdims=True))
            weights /= weights.sum(axis=1, keepdims=True)
            choice = [self.rng.choice(len(w), p=w) for w in weights]
        else:
            choice = numpy.argmax(logits, axis=1)
        return formats.as_actions(choice, len(logits), step.action_set)
