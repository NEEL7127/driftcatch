# Troubleshooting

Specific errors, what they mean, and what to do.

---

## `NoMatchError`

```
NoMatchError: no recorded event matches this request
  recording: refund_1182
  step 2:    llm call with 4 messages
```

Your agent made a call the recording does not contain. That is usually the
point — behaviour changed.

**If the change was intended:** re-record.

```bash
drift record tests/test_refund.py -k refund_1182
drift diff refund_1182 --previous
drift approve refund_1182 --by you@example.com
```

**If it was not intended:** you have found the regression. `drift bisect` names
which input caused it.

**If the behaviour is identical and matching is just too strict:** something in
the payload differs run to run. Find it with `drift audit refund_1182`, then add
a [normalizer](matching.md#normalizers). Do not reach for a looser matcher
first — that silences the next real regression too.

---

## `DriftWarning: recording is STALE`

```
DriftWarning: recording 'refund_1182' is STALE: prompt_hash changed since it
was approved. Re-verify with: drift check --live refund_1182
```

One of the four [fingerprint](concepts.md#fingerprints) components changed. The
recording still replays — the warning says it may no longer describe reality.

```bash
drift bisect refund_1182     # which component, and what it did to behaviour
```

If you changed the prompt deliberately and the behaviour is still right,
re-record and approve. To make staleness a hard failure:

```toml
drift_policy = "fail"
```

---

## `UnapprovedReplayWarning`

You are replaying a recording nobody approved. It works, but that recording is
not a golden — it is just a file.

```bash
drift show refund_1182
drift approve refund_1182 --by you@example.com
```

In CI:

```bash
drift status --require-approved      # exits 7 if anything is pending
```

---

## `PrunedRecording: cannot be replayed`

The recording went through `drift prune`, which drops raw response bodies. It
refuses to replay rather than serving an empty body, because an empty body would
give your SDK a completion with no choices and no error — a test that passes
having verified nothing.

Trajectory, tool and invariant assertions still work on a pruned recording. For
byte-level replay, re-record it.

---

## `LatencyNotMeasured`

```
LatencyNotMeasured: latency assertions skipped for take 'refund_1182':
replay serves recorded bytes, so there is no real call to time.
```

Not a problem — the framework is telling you it did **not** verify a performance
guarantee. Replay reads from disk, so there is no duration to measure.

Check latency where there are real calls:

```bash
drift check --live
```

---

## `InvalidRecordingName`

```
InvalidRecordingName: invalid recording name 'refund/1182'. Use letters,
digits, dot, dash or underscore...
```

Names become filenames. Use `refund_1182`. Names are refused rather than
auto-corrected, because rewriting one would let two names collide on one file.

---

## `RecordingVersionError`

```
refund_1182.json was written in recording format v2, but this build
understands v1.
```

The file was written by a different version of DriftCatch. Either upgrade, or
re-record. The format is refused rather than guessed at, so an old build never
silently misreads a newer file.

---

## Nothing is being recorded

Checklist:

1. **Is the call inside the take?** Only calls made within the `with` block are
   captured.
2. **Does the SDK use `httpx` or `httpx2`?** Those are hooked. An SDK built on
   `requests` or `aiohttp` is not.
3. **Is the URL recognised as an LLM path?** Non-LLM traffic passes through
   untouched by design.
4. **Does `record` cover it?** The default is `["llm.*", "tools.*"]`.
5. **Is it in `passthrough`?** Passthrough boundaries are deliberately excluded.

```python
with drift.take("debug", mode="record") as take:
    run_agent("hello")
print(take.events)          # empty means nothing was intercepted
print(take.describe())
```

---

## Tests pass alone but fail under `pytest -n`

Almost always shared state between tests — a module-level list, a registered
matcher, a recording name used by two tests.

This matters more here than elsewhere: a tool promising determinism must not
leak state between concurrent tests, so CI runs `-n 4` on purpose. If a test
only passes serially, that is a finding rather than a flake.

Give each test its own `tmp_path` recordings directory:

```python
@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    drift.reset_config()
    drift.config(recordings_dir=tmp_path / "recordings")
    yield
    drift.reset_config()
```

---

## `drift check` exits 0 but nothing ran

It does not. Collecting no tests exits **1** with:

```
No tests were collected, so no behaviour was verified.
```

Exiting 0 there would be indistinguishable from a passing run, which is the
failure mode this whole project exists to prevent. Check your path or `-k`
filter.

---

## Live runs are skipped

```
SKIPPED: live behaviour check for 'refund_1182' skipped;
set DRIFTCATCH_ALLOW_LIVE=1 to run it
```

Working as intended. Live runs spend money and never happen by accident in CI.

```bash
DRIFTCATCH_ALLOW_LIVE=1 drift check --live
```

---

## Interception seems to leak between tests

It should not — the patch is installed on the outermost take and removed when it
closes, and sessions live in a `ContextVar` so threads and asyncio tasks are
isolated.

If you see otherwise, check that every take is used as a context manager. A take
opened without `with` never closes, so the patch is never removed.

---

## A PermissionError on Windows

Windows refuses to replace a file another handle has open. DriftCatch's own
reads and writes retry against a 5-second deadline, so this should surface only
under genuine contention, and then as a clear `RecordingWriteError` rather than
a raw OS error.

The usual cause is recording the **same name** from several threads at once.
Avoid that pattern. A third-party raw read during a swap — an editor, a
`git add` — is outside the library's control on Windows.

---

## Still stuck

```bash
drift status            # what exists, and its state
drift show <name>       # the full event list
drift audit <name>      # how requests are being keyed
drift explain           # the tool described for a coding agent
```

`take.describe()` prints the same context that is appended to every assertion
failure.
