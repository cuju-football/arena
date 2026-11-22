# Contributing to Cuju Arena

Discuss changes and report public bugs in the [arena issue tracker][issues].

Run the Python suite with `python -m pytest -n 4`. The suite covers the arena API, the
family command line, recorded matches, submission formats and their workers. Build the wheel
and source distribution with `python -m build`. Check them with
`python ci/check_wheel.py dist/*.whl dist/*.tar.gz`.
The source distribution includes the package, guides and the files needed to build it.
Use the repository checkout to run the suite.

Run the full suite on native Windows for each arena gate. Copy the gated checkout to a Windows
folder and build the wheel there with the local build tools. Use PowerShell to install the local
wheel with `--no-index --no-deps` into the Windows Python 3.12 environment with CPU PyTorch,
cuju-engine 0.3.0, pytest and pytest-xdist already installed. Run
`python -m pytest -n 4 -o faulthandler_timeout=900` from the copy with that environment.
The job limit test observes live descendants and uses Windows process handles to verify that
the job ends them, independently of the console language.

Keep the arena usable beside cuju-engine alone. Public engine names form its engine interface.
The renderer uses a declared arena host interface, listed and checked in the renderer repository.
The [plugin guide](docs/plugins.md) describes the interfaces that third-party extensions use.

Record user-facing changes under `changes/`, update their guides and run the reference,
links, prose and fragments checks.

[issues]: https://github.com/cuju-football/arena/issues
