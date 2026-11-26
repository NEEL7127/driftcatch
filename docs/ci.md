# CI integration

The shape of a good DriftCatch pipeline follows from the
[two-tier model](concepts.md#the-two-tier-model): replay runs on every pull
request because it is free, and live runs on a schedule because it is not.

---

## Every pull request — Tier 1

```yaml
name: behaviour

on: [pull_request]

jobs:
  replay:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -e ".[dev]"

      # Secrets must never reach a recording. Exit code 5 means found.
      - name: Scan recordings for credentials
        run: drift scan

      # Tier 1: replay. No network, no tokens, deterministic.
      - name: Replay behaviour
        run: drift check --junitxml=report.xml

      # Nothing should become a golden without a human looking at it.
      - name: Refuse unapproved recordings
        run: drift status --require-approved

      - name: Publish test report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: behaviour-report
          path: report.xml
```

Nothing here needs an API key, which is the point: a fork's pull request can run
the whole behaviour gate.

---

## Nightly — Tier 2

```yaml
name: live behaviour

on:
  schedule:
    - cron: "0 3 * * *"
  workflow_dispatch:

jobs:
  live:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"

      - name: Live behaviour check
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          DRIFTCATCH_ALLOW_LIVE: "1"
        run: drift check --live --junitxml=live-report.xml
```

`DRIFTCATCH_ALLOW_LIVE=1` is a deliberate speed bump. Without it, a live run
refuses — so a stray `--live` in a pull-request workflow cannot quietly start
spending money.

Thresholds live in the test, because live output varies run to run:

```python
import driftcatch as drift


@drift.live_check("refund_1182", runs=5)
def test_refund_still_behaves():
    result = drift.run_live(
        "refund_1182", lambda: run_agent("refund order 1182"), runs=5
    )
    assert result.matches() >= 4, result.report
```

`result.matches()` counts how many of the runs matched the golden's behaviour
at the configured tolerance. Picking 4-of-5 rather than 5-of-5 is what keeps a
genuinely non-deterministic model from producing a flaky gate.

---

## JUnit XML

`drift check` and `drift record` forward unrecognised arguments to pytest, so
pytest's own reporting works unchanged:

```bash
drift check --junitxml=report.xml
```

That produces a standard JUnit file that GitHub Actions, GitLab, Jenkins,
CircleCI and Buildkite all render natively — one row per scenario, with the
divergence in the failure message.

There is deliberately no `drift --junit` flag. pytest already does this well,
and a second implementation would only be a worse one.

---

## What to gate on

| Check | Command | Fails with | Gate it? |
|---|---|---|---|
| Behaviour changed | `drift check` | 3 | **Yes** — this is the point |
| Secrets in recordings | `drift scan` | 5 | **Yes** — never commit a key |
| Learned invariants broke | `drift check` | 3 | Yes — enforced by `assert_invariants()` in your tests |
| Behaviour coverage | `drift coverage --fail-under 70` | 4 | Optional |
| Unapproved recordings | `drift status --require-approved` | 7 | Yes |
| Tool contracts | `drift contracts` | 0 | **No** — it is a report |

`drift contracts` never fails a build by design. It tells you about dead schema
surface and unstable results; it does not decide those are errors.

---

## Blocking unapproved recordings

A recording nobody reviewed should not silently become a golden. Replay warns,
but if you want it to be an error:

```bash
drift status --json \
  | jq -e '[.recordings[] | select(.status == "pending")] | length == 0' \
  || { echo "::error::unapproved recordings on this branch"; exit 1; }
```

---

## Committing recordings

Recordings belong in version control. They are the baseline, and a behaviour
change should be visible in the pull request diff.

They are written with plaintext status, fingerprint and trajectory, and only the
bulky event bodies are gzipped — so a reviewer sees the behaviour change in the
diff rather than a wall of base64.

```gitattributes
recordings/*.json text eol=lf
```

Run `drift scan` in a pre-commit hook so a key never reaches history in the
first place:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: drift-scan
        name: scan recordings for credentials
        entry: drift scan
        language: system
        pass_filenames: false
```

---

## Shrinking the repository

Recordings carrying full response bodies get large. If you only need behaviour
assertions and not byte-exact replay:

```bash
drift prune
```

This drops the raw bodies and keeps the trajectory. Trajectory, tool and
invariant assertions keep working.

**Replay of a pruned recording refuses**, with `PrunedRecording`, rather than
serving an empty body. Serving `{}` would hand the SDK a completion with no
choices and no error — a silent false green, the exact failure this project
exists to prevent.

---

## Keeping CI honest

Run the suite in parallel:

```bash
drift check -n 4
```

A tool that promises determinism must not leak state between concurrent tests.
Running `-n 4` in CI is a correctness check, not a speed optimisation — if the
suite only passes serially, that is a finding.
