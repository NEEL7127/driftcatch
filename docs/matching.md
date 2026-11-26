# Matching

During replay your agent makes a request, and DriftCatch has to decide which
recorded event answers it. That decision is the matcher.

Get it too strict and every harmless difference — a timestamp, a UUID, a
reordered key — looks like a regression. Get it too loose and a real behaviour
change gets served a plausible-looking recorded answer and your test passes
when it should not. The second failure is worse, and it is silent.

So matching is two layers:

```
request  ->  normalizers (remove noise)  ->  strategy (pick the event)
```

---

## Normalizers

Applied to both the incoming request and the recorded events before comparison.
They remove things that differ run to run but carry no behavioural meaning.

| Normalizer | Removes |
|---|---|
| `drop_timestamps` | Timestamp fields |
| `mask_uuids` | UUIDs, replaced with a stable placeholder |
| `normalize_tool_call_ids` | Provider-generated `call_abc123` ids |
| `json_canonical` | Key-order and whitespace differences in JSON strings |
| `redact_secrets` | Credential-shaped values |

The first three are on by default:

```toml
normalizers = ["drop_timestamps", "mask_uuids", "normalize_tool_call_ids"]
```

`json_canonical` is worth adding when your tool arguments are JSON strings whose
key order is not stable.

### Writing one

```python
import driftcatch as drift


@drift.normalizer("drop_request_id")
def drop_request_id(payload: dict) -> dict:
    payload.pop("x_request_id", None)
    return payload
```

```toml
normalizers = ["drop_timestamps", "mask_uuids", "drop_request_id"]
```

A normalizer must be **pure and total**: same input, same output, no network, no
clock. It runs on both sides of a comparison, so anything non-deterministic
inside one makes matching itself non-deterministic.

Normalizers are part of the [fingerprint](concepts.md#fingerprints). Changing
the list marks recordings stale, on purpose — you changed what "the same
request" means.

---

## Strategies

| Strategy | Matches on | Use when |
|---|---|---|
| `field` | Normalized payload: model, messages, tools (or tool name and args) | Default. Precise. |
| `positional` | The next unconsumed event of the same kind, ignoring content | Payloads are noisy and the step sequence is stable |
| `tool_call` | Tool name first, then normalized arguments | You want a clearer failure when an unexpected tool is called |

```toml
matcher = "field"
```

```python
with drift.take("refund_1182", matcher="positional") as take:
    ...
```

### `field` — the default

Compares the normalized payload. Precise, and the cost is that you must declare
which noise to drop, via normalizers.

### `positional`

Serves the next unconsumed event of the same kind and ignores content entirely.
Robust to payloads full of noise; breaks the moment the agent adds or removes a
step.

Use it as a deliberate trade, not as a way to silence failures. A `positional`
matcher will happily answer a completely different question with a recorded
reply.

### `tool_call`

Matches tool calls on name first, then arguments. The benefit over `field` is
the failure message: when the agent calls a tool that the recording does not
contain, `tool_call` says exactly that, while `field` reports a generic payload
mismatch.

---

## Chaining

Try strategies in order; the first match wins.

```python
# conftest.py
import driftcatch as drift

drift.chain("field", "positional")
```

`chain()` builds **and registers** the composite, under a name made from its
parts:

```python
>>> drift.chain("field", "positional").name
'chain(field,positional)'
```

Use that name wherever a matcher name goes:

```toml
matcher = "chain(field,positional)"
```

The registration has to happen before the matcher is looked up, so call
`chain()` at import time — `conftest.py` is the natural place. Unknown strategy
names fail immediately rather than at match time, so a typo surfaces at
registration.

Read a chain as a fallback: be exact when you can, fall back to position when
the payload has drifted in ways you do not care about. Order matters — put the
strict one first, or the loose one will always answer.

---

## Consumption

A matched event is **consumed** and will not match again. This is what makes
repeated identical calls work: three identical requests match the first, second
and third recorded events, not the first one three times.

It is also why a replay that skips a step fails on the one after it rather than
silently shifting — the events are no longer where the agent expects them.

---

## When no event matches

With `strict = true` (the default), an unmatched request raises `NoMatchError`,
which carries the first point of divergence.

`NoMatchError` inherits from `DriftSignal`, which inherits from
`BaseException` rather than `Exception`. That is deliberate: provider SDKs and
agent loops routinely wrap `except Exception:` around calls, and the Anthropic
SDK was observed turning a `NoMatchError` into an `APIConnectionError` before
this changed. A control signal that an agent can swallow is not a control
signal.

With `strict = false`, an unmatched request falls through to the real network.
Useful while building up a recording, risky in CI — a test can silently start
spending money.

---

## Seeing how requests key

```bash
drift audit refund_1182
```

```
refund_1182   matcher=field
  [0] llm   key=a3f91c…   model=gpt-4o-mini  messages=2  tools=3
  [1] tool  key=7b2e04…   lookup_order(order_id=1182)
  [2] llm   key=c81d55…   model=gpt-4o-mini  messages=4  tools=3
```

Two events sharing a key means the matcher cannot tell them apart. That is how a
loose matcher becomes visible before it produces a false pass.

---

## Choosing

Start with `field` and the default normalizers. Change only in response to a
specific failure:

| Symptom | Fix |
|---|---|
| A field differs every run and means nothing | Add a normalizer |
| Payloads are hopelessly noisy, steps are stable | `positional` |
| Unclear failures when the wrong tool is called | `tool_call` |
| Mostly stable, occasionally noisy | `chain(field,positional)` |

Reaching for a looser matcher to make a failing test pass is how you end up with
a suite that cannot fail. If a test is failing, first ask whether the behaviour
actually changed.
