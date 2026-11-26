# Assertions

Every assertion hangs off the `Take` yielded by `drift.take(...)`. They chain,
and every failure prints the run's context — mode, trajectory, event count,
tokens, and whether the recording is stale.

```python
with drift.take("refund_1182") as take:
    answer = run_agent("refund order 1182")

take.assert_tools().called_in_order("lookup_order", "issue_refund")
take.assert_output().contains("refunded").excludes("error")
take.assert_cost().under(tokens=2_000)
```

---

## Tools — `assert_tools()`

What the agent decided to do.

| Method | Fails when |
|---|---|
| `.called(name)` | The tool was never called |
| `.never(name)` | The tool was called |
| `.called_in_order(*names)` | Those tools did not appear in that relative order |
| `.call_count(name, n)` | It was called a different number of times |

```python
take.assert_tools() \
    .called("lookup_order") \
    .called_in_order("lookup_order", "issue_refund") \
    .never("escalate_to_human") \
    .call_count("lookup_order", 1)
```

`called_in_order` checks *relative* order, so other tools may appear between
them. Use `assert_trajectory().matches_golden(tolerance="exact")` when you need
the exact sequence.

---

## Trajectory — `assert_trajectory()`

Compare the whole behaviour against the approved golden.

```python
take.assert_trajectory().matches_golden()
take.assert_trajectory().matches_golden(tolerance="exact")
```

| Tolerance | Fails when |
|---|---|
| `tools` | The *set* of tools differs |
| `tools+order` | The *sequence* differs (default) |
| `exact` | Any step, argument digest or outcome differs |

Failures report the first divergence:

```
behaviour differs from golden (tools+order):
  step 2:  golden=issue_refund  current=escalate_to_human
```

---

## Output — `assert_output()`

What the model actually said. The final reply, or `outputs` for every turn.

```python
take.assert_output() \
    .not_empty() \
    .contains("refunded") \
    .excludes("Traceback") \
    .matches(r"order \d+")
```

| Method | Fails when |
|---|---|
| `.not_empty()` | The reply is blank or whitespace |
| `.contains(s, case_sensitive=True)` | `s` is absent |
| `.excludes(s, case_sensitive=True)` | `s` is present |
| `.matches(pattern)` | The regex does not match (`re.DOTALL`) |
| `.equals(text, strip=True)` | The text differs |
| `.is_json()` | The reply is not valid JSON |
| `.json_has(*keys)` | The parsed object lacks a top-level key |
| `.satisfies(fn, description=...)` | `fn(text)` is falsey |

`.excludes()` is usually the safety-relevant one. Asserting that a reply does
*not* contain an internal id, a stack trace or a refusal phrase is how a prompt
change that starts leaking them gets caught.

```python
take.assert_output().excludes("internal_customer_id")
```

For structured output:

```python
take.assert_output().is_json().json_has("decision", "amount")
```

`.satisfies()` is the escape hatch for a rule with no verb here:

```python
take.assert_output().satisfies(
    lambda text: len(text.split()) < 200,
    description="stays under 200 words",
)
```

**The predicate must be deterministic.** Calling a model from inside it makes
the test non-reproducible, which is the one property the rest of the framework
exists to provide. If you want an LLM judge, that is evaluation — use
promptfoo or DeepEval alongside DriftCatch, not inside it.

### Reasoning models

`take.output` reads `content`, and falls back to the reasoning field when
`content` is null. o1-style models, DeepSeek-R1, QwQ, Nemotron and Anthropic
thinking blocks therefore report an answer rather than an empty string.

---

## Cost — `assert_cost()`

A cost regression is a regression.

```python
take.assert_cost().under(tokens=2_000)
take.assert_cost().under(usd=0.02)
```

Token counts come from the provider's `usage` block. `cost_usd` is only
populated when the provider reports it.

---

## Latency — `assert_latency()`

```python
take.assert_latency().under(total_ms=5_000, per_call_ms=2_000)
take.assert_latency().within_factor_of_golden(2.0)
```

| Method | Fails when |
|---|---|
| `.under(total_ms=, per_call_ms=)` | The run, or any single call, exceeded the budget |
| `.within_factor_of_golden(factor)` | The run took more than `factor` × the golden's time |

### Latency is skipped during replay, on purpose

This is the one assertion with a trap in it, so it is worth being explicit.

During replay, bytes come off disk. The durations stored on a replayed event
describe the call **as it was originally recorded** — comparing them would be
comparing a fixed number against itself and passing forever. That is a green
light that means nothing.

So during replay, every latency check is **skipped** and a `LatencyNotMeasured`
warning is raised:

```
LatencyNotMeasured: latency assertions skipped for take 'refund_1182':
replay serves recorded bytes, so there is no real call to time.
Record the take or use drift check --live to check latency.
```

Your test still runs offline. It just does not pretend to have verified a
performance guarantee. Latency is a Tier-2 property, like model behaviour:

```bash
drift check --live        # here, latency assertions do run
```

`take.latencies` returns `[(label, ms), ...]` for steps that were genuinely
timed, and is empty on a replayed run rather than reporting a convincing-looking
set of instant calls.

---

## Learned invariants — `assert_invariants()`

Rules mined from your approved corpus that nobody wrote by hand.

```python
take.assert_invariants().hold()
```

By default only **approved** invariants are enforced. See
[Learned invariants](invariants.md).

```python
violations = take.invariant_violations()   # inspect without failing
```

---

## Reading the take directly

When no assertion fits, read the run:

```python
take.output              # str  - the final reply
take.outputs             # list[str] - every reply, in order
take.tools_called        # list[str]
take.events              # list[Event] - the raw captured events
take.usage               # Usage - tokens and cost
take.latencies           # list[tuple[str, int]] - measured steps only
take.latency_ms          # int - total measured time, 0 on replay
take.mode                # "record" | "replay" | "live"
take.stale               # bool - fingerprint no longer matches
take.changed_inputs      # list[str] - which components changed
take.golden              # Recording | None
take.describe()          # the context string printed on failure
```

---

## Applying one policy to many tests

Repeating the same three assertions in forty tests is how the fortieth ends up
missing the one somebody added later. Use a [scenario suite](suites.md):

```python
suite = drift.Suite(
    "refund",
    cases={"small": "refund order 1182", "large": "refund order 9999"},
    check=lambda take: (
        take.assert_invariants().hold(),
        take.assert_output().excludes("Traceback"),
    ),
)
```
