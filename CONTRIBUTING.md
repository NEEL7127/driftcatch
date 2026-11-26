# Contributing to DriftCatch

Thanks for considering a contribution. This document covers how to get set up,
what the quality bar is, and the few design positions that are deliberate rather
than accidental.

## Getting set up

```bash
git clone https://github.com/neeldeshmane/driftcatch
cd driftcatch
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Verify the checkout:

```bash
python -m pytest -n 4 -q          # the suite runs in parallel on purpose
python -m mypy driftcatch
python -m ruff check driftcatch tests examples
python -m ruff format --check driftcatch tests examples
```

The suite is offline. No test in `tests/` makes a network call, and CI asserts
that. If you need a real provider for exploratory work, keep it out of `tests/`.

## The quality bar

- **Every behaviour change needs a test.** Not a test that the code runs — a
  test that the behaviour is right.
- `mypy --strict` is clean and stays clean.
- `ruff` with the project's rule set, line length 100.
- `from __future__ import annotations` at the top of every module.
- Public functions get docstrings that say *why*, not *what*. The what is in the
  signature.

### Tests run in parallel

CI runs `pytest -n 4`. A tool that promises determinism must not leak state
between concurrent tests, so this is a correctness check, not a speed
optimisation. If your test only passes serially, it is telling you something.

## Design positions that are deliberate

Before proposing one of these, please open an issue — they are choices, not
oversights.

**Replay cannot detect model-behaviour change.** It is physically impossible: a
recording contains what the model said then, not what it would say now. That is
what Tier 2 (`--live`) is for. Please do not file "replay should catch model
updates".

**Recording is not approving.** The approval gate exists because a recorded run
can be wrong, and an unreviewed golden turns a bug into an enforced contract.
Anything that makes approval implicit defeats the feature.

**DriftCatch does not evaluate answer quality.** Scoring whether an answer is
*good* is a different discipline, well served by promptfoo and DeepEval.
DriftCatch gates whether behaviour *changed*. Keeping that line sharp is why the
tool is small enough to trust.

**DriftCatch does not own your agent.** There is no agent registry, no base
class and no runtime to adopt. It wraps calls you already make. Anything that
requires restructuring the user's agent is out of scope.

**The MCP server is read-only.** An agent facing a red test will try to make it
green, and `approve` is the fastest way to do that. Mutating commands stay in
human hands.

## Pull requests

- One concern per pull request.
- Say what behaviour changed and how you verified it. Paste the commands.
- Update `CHANGELOG.md` under `## [Unreleased]`.
- If you changed the recording format, bump `RECORDING_VERSION` and say so
  loudly — old goldens must fail to load rather than be misread.

## Reporting a security issue

Please do not open a public issue for a vulnerability. Email the maintainer
instead, and allow time for a fix before disclosure.

Areas worth scrutiny: recording names reaching the filesystem, secret redaction
on the write path, and anything reachable from the MCP server.
