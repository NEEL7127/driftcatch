# Configuration

Settings resolve in this order, each layer overriding the one before it:

```
defaults  <  driftcatch.toml / pyproject.toml  <  environment  <  drift.config()  <  drift.take(...)
```

The most specific wins, so a single test can override everything without
disturbing the rest of the suite.

---

## The file

`drift init` writes a `driftcatch.toml` with the defaults spelled out. A
standalone file is preferred over `pyproject.toml`, so changing a setting from
the UI never has to rewrite your build file.

```toml
# driftcatch.toml
recordings_dir = "tests/recordings"
matcher = "field"
normalizers = ["drop_timestamps", "mask_uuids", "normalize_tool_call_ids"]
strict = true
drift_policy = "warn"
require_approved = false
record = ["llm.*", "tools.*"]
passthrough = []
redactors = ["default"]
stream_timing = "instant"
max_recording_bytes = 2_000_000
default_tolerance = "tools+order"
what_if_unmatched = "execute"
```

The same keys work under `[tool.driftcatch]` in `pyproject.toml`.

---

## Every setting

| Setting | Default | What it does |
|---|---|---|
| `recordings_dir` | `tests/recordings` | Where recordings are written and read |
| `matcher` | `field` | How a request finds its recorded event — see [Matching](matching.md) |
| `normalizers` | `drop_timestamps`, `mask_uuids`, `normalize_tool_call_ids` | Noise removed before matching |
| `strict` | `true` | An unmatched request raises rather than falling through to the network |
| `drift_policy` | `warn` | What a stale fingerprint does: `warn`, `fail` or `ignore` |
| `require_approved` | `false` | Refuse to replay a recording nobody approved |
| `record` | `["llm.*", "tools.*"]` | Which boundaries are captured |
| `passthrough` | `[]` | Boundaries that stay live even during replay |
| `redactors` | `["default"]` | Secret redaction applied on the write path |
| `stream_timing` | `instant` | Whether replayed streams reproduce original chunk timing |
| `max_recording_bytes` | `2_000_000` | Size ceiling for one recording |
| `default_tolerance` | `tools+order` | Default strictness for trajectory diffs |
| `what_if_unmatched` | `execute` | What an unmatched tool does in a counterfactual run |

---

## From Python

Process-wide:

```python
import driftcatch as drift

drift.config(
    recordings_dir="tests/recordings",
    matcher="tool_call",
    drift_policy="fail",
)
```

Read it back, or reset between tests:

```python
drift.get_config()
drift.reset_config()
```

Per take, overriding everything above:

```python
with drift.take("refund_1182", mode="record", tolerance="exact", strict=False) as take:
    ...
```

---

## Environment variables

Useful in CI, where you want one command to behave differently without editing
a file.

| Variable | Effect |
|---|---|
| `DRIFTCATCH_MODE` | Force `record`, `replay` or `live` |
| `DRIFTCATCH_RECORDINGS_DIR` | Override the recordings directory |
| `DRIFTCATCH_MATCHER` | Override the matcher |
| `DRIFTCATCH_DRIFT_POLICY` | Override the stale-recording policy |
| `DRIFTCATCH_STRICT` | `1`/`0` for strict matching |
| `DRIFTCATCH_ALLOW_LIVE` | `1` permits live runs in CI |

```bash
DRIFTCATCH_MODE=replay pytest
```

---

## Modes

A take resolves its mode from, in order: the `mode=` argument,
`DRIFTCATCH_MODE`, the pytest `--drift-mode` flag, and finally whether a
recording already exists.

| Mode | Calls go out? | Writes a recording? | Serves from disk? |
|---|---|---|---|
| `record` | Yes | Yes | No |
| `replay` | No | No | Yes |
| `live` | Yes | **No** | No |

`live` observes without persisting. It makes real calls and builds a
trajectory, so a Tier 2 check can compare current behaviour against the golden,
but it never overwrites the golden it is checking.

---

## Choosing what to capture

`record` and `passthrough` take glob patterns over dotted boundary names such
as `llm.chat` and `tools.get_time`.

```toml
record = ["llm.*", "tools.*"]
passthrough = ["tools.get_current_time"]
```

`passthrough` wins over `record`, so one unrepeatable tool can stay live while
everything else is pinned. A passthrough tool executes for real during replay
and does **not** appear in the trajectory — it is deliberately outside the
behaviour being compared.

Capture nothing but the model:

```toml
record = ["llm.*"]
```

---

## Stale-recording policy

A recording whose [fingerprint](concepts.md#fingerprints) no longer matches is
stale.

| `drift_policy` | Behaviour |
|---|---|
| `warn` | `DriftWarning`, the run continues (default) |
| `fail` | Raises, failing the test |
| `ignore` | Silent |

`warn` is the default because the first thing that goes stale is usually a
prompt you edited on purpose. Switch to `fail` once your recordings are
trustworthy enough that staleness is always a signal.

---

## Requiring approval

```toml
require_approved = true
```

Replaying a `PENDING` recording then raises instead of warning. Worth turning on
once the team is in the habit, so an unreviewed recording cannot quietly act as
a golden.

The CI equivalent, which does not need the setting:

```bash
drift status --require-approved
```
