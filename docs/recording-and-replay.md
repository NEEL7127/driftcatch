# Recording and replay

## The one entry point

```python
import driftcatch as drift

with drift.take("refund_1182") as take:
    answer = run_agent("refund order 1182")
```

Opening a take decides from one call whether this run records or replays, binds
a session for its duration, and yields the object every assertion reads.

### How the mode is decided

In order:

1. `mode=` passed to `take()`
2. `DRIFTCATCH_MODE` in the environment
3. `--drift-mode` from the pytest plugin (what `drift record` and
   `drift check` set)
4. Whether a recording already exists — no file means record, a file means
   replay

So the first run records and every run after replays, with no flag to remember.

---

## What gets captured

### Model calls

Interception sits on `Client.send` in **both** `httpx` and `httpx2`. The OpenAI
SDK uses `httpx`; the Anthropic SDK moved to `httpx2`. Patching one would have
silently missed every call made through the other.

Because the hook is at the HTTP client rather than per provider, any SDK,
gateway or proxy that speaks HTTP works without an adapter — including ones that
did not exist when this was written.

Two copies of each reply are kept, on purpose:

- **`response`** — the normalised, provider-agnostic view. Assertions,
  trajectories and coverage read this.
- **`raw_response`** — the verbatim provider body. Replay returns *this*, so
  your SDK parses exactly what it parsed when recording. Rebuilding a
  provider-shaped body from the normalised view would break on every SDK
  version bump.

Non-LLM traffic passes straight through. A Stripe call inside a take is neither
recorded nor intercepted.

### Tools

```python
@drift.tool
def lookup_order(order_id: int) -> dict:
    return db.fetch(order_id)
```

Arguments, results and raised exceptions are recorded, and replayed — including
the failure. A tool that raised `TimeoutError` when recorded raises it again on
replay, so your retry path is exercised deterministically.

Async tools work the same way.

### Streaming

Streamed responses are captured chunk by chunk and reassembled, including:

- streamed **tool calls**, accumulated across deltas
- **reasoning** deltas — `delta.reasoning_content` (OpenAI-shaped) and
  `delta.thinking` (Anthropic-shaped)

On replay the stream is reproduced. `stream_timing = "instant"` (the default)
delivers chunks immediately rather than reproducing the original gaps.

### Reasoning models

o1-style models, DeepSeek-R1, QwQ and Nemotron return `content: null` with the
answer in `reasoning_content`. Both are captured:

```python
take.events[0].response.content     # None
take.events[0].response.reasoning   # "the actual answer"
take.events[0].response.text        # falls back to reasoning
take.output                         # same fallback
```

Reasoning tokens are counted separately in `usage.reasoning_tokens`.

---

## Re-recording safely

Re-recording **archives** the previous version to `.previous/` first:

```bash
drift record                  # overwrites, keeping a copy
drift diff refund_1182 --previous   # new vs archived old
drift restore refund_1182     # undo the re-record
```

Destroying a golden on re-record would be unacceptable twice over: the old
behaviour is exactly what you want to diff against, and a re-record done by
mistake should be recoverable.

---

## What replay guarantees

Byte-identical. The recorded provider body is handed back to your SDK, so the
objects your code receives are the objects it received when recording.

What it does **not** guarantee is that the model would still say that. See
[the two-tier model](concepts.md#the-two-tier-model).

---

## Nested and concurrent takes

Takes nest. The HTTP patch is installed on the outermost and removed only when
it closes, so an inner take never un-patches a client the outer one is using.

Sessions are held in a `ContextVar`, so threads and asyncio tasks each see their
own. Concurrent takes with different names are safe and are tested that way.

Concurrent takes with the **same** name are safe in the sense that the file
never corrupts — writes are atomic and last-writer-wins — but it is not a
pattern to rely on. Windows refuses to replace a file another handle has open,
so writes retry against a deadline rather than failing outright.

---

## Pruning

```bash
drift prune
```

Drops raw response bodies and keeps trajectories, which shrinks a repository
substantially. Trajectory, tool and invariant assertions keep working.

**Replay of a pruned recording refuses** with `PrunedRecording`. It does not
serve an empty body — that would hand the SDK a completion with no choices and
no error, which is a silent false green.

---

## On disk

```json
{
  "version": 1,
  "name": "refund_1182",
  "status": "approved",
  "approved_by": "you@example.com",
  "fingerprint": {"model_id": "...", "prompt_hash": "...", "...": "..."},
  "trajectory": {"tools_called": ["lookup_order", "issue_refund"], "...": "..."},
  "meta": {"usage": {"input_tokens": 1240}, "duration_ms": 2310},
  "events_gz": "H4sIAAAAAAAA…"
}
```

Status, fingerprint and trajectory stay **plaintext** so a behaviour change is
legible in a pull-request diff. Only the bulky event bodies are gzipped.

Writes are atomic: the payload goes to a unique temporary file, is flushed and
fsynced, then moved into place with `os.replace`. An interrupted write leaves
the previous file untouched rather than truncated.

A recording written in a format this build does not understand is **refused**,
not guessed at. Recording names are validated at the path chokepoint and
rejected rather than sanitised — silently rewriting a name would make two
different names collide on one file.
