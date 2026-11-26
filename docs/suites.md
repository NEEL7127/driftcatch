# Scenario suites

A behaviour suite grows the same way every time. You start with one scenario,
then you want the refund path and the escalation path and the "customer is
rude" path, and within a week you have forty test functions that differ only in
their input and their recording name.

Copy-paste is the usual answer, and it is the reason the fortieth scenario never
gets the assertion somebody added to the third.

A `Suite` is a table of cases plus one body.

```python
import driftcatch as drift
from myapp.agent import run_agent

refunds = drift.Suite(
    "refund",
    cases={
        "small":      "refund order 1182",
        "large":      "refund order 9999",
        "ineligible": "refund order 3001",
        "rude":       "REFUND ME NOW this is unacceptable",
    },
    check=lambda take: take.assert_invariants().hold(),
)


@refunds.parametrize
def test_refund_behaviour(take, case):
    answer = run_agent(case)
    take.assert_output().not_empty()
```

That collects four tests:

```
test_refund_behaviour[small]
test_refund_behaviour[large]
test_refund_behaviour[ineligible]
test_refund_behaviour[rude]
```

each with its own recording — `refund_small`, `refund_large`,
`refund_ineligible`, `refund_rude` — so the goldens stay separate and a diff
still points at exactly one scenario.

---

## The parts

### `cases`

Maps a case name to whatever the body needs. The payload is passed through
untouched, because the body is the only thing that knows what a case means.

```python
drift.Suite("checkout", cases={
    "guest":    {"user": None,          "items": 1},
    "member":   {"user": "u_1",         "items": 3},
    "flagged":  {"user": "u_2", "risk": "high", "items": 1},
})
```

```python
@checkout.parametrize
def test_checkout(take, case):
    run_agent(**case)
```

### `check`

Runs after **every** case, once the take has closed and the trajectory is
complete. This is where a policy that must hold everywhere belongs — adding it
once applies it to the fortieth scenario too.

```python
def house_rules(take):
    take.assert_invariants().hold()
    take.assert_output().excludes("Traceback")
    take.assert_cost().under(tokens=4_000)
    take.assert_tools().never("delete_customer")


suite = drift.Suite("refund", cases={...}, check=house_rules)
```

A failing `check` fails that case. It is a gate, not decoration.

### Take options

Anything else is forwarded to `drift.take`:

```python
drift.Suite(
    "refund",
    cases={...},
    mode="record",
    tolerance="exact",
    strict=True,
)
```

---

## Extra fixtures

The body is called as `fn(take, case, **fixtures)`. Parameters beyond the first
two stay in the generated signature, so ordinary pytest fixtures still resolve:

```python
@refunds.parametrize
def test_refund(take, case, tmp_path, caplog):
    ...
```

---

## Useful properties

```python
refunds.names                    # ["refund_small", "refund_large", ...]
refunds.recording_name("small")  # "refund_small"
refunds.cases                    # the table, as a dict
```

`names` is handy for scripting status checks over just this suite's recordings.

---

## Errors you get early

Both of these are raised when the `Suite` is **constructed**, at import time,
not on the twelfth case of a live recording run.

```python
drift.Suite("refund", cases={})
# ConfigError: suite 'refund' has no cases. A suite with an empty table
# would collect zero tests and report green, which looks exactly like a
# suite that passed.
```

```python
drift.Suite("refund", cases={"../escape": "x"})
# InvalidRecordingName: invalid recording name 'refund_../escape'...
```

---

## When not to use a suite

If the cases need genuinely different assertions, they are not one suite — they
are separate tests, and forcing them together produces a body full of
`if case == ...`. A suite is for scenarios that differ in **input**, not in what
correct looks like.

`check` is for the rules shared by all of them; per-case assertions go in the
body.

---

## What a suite is not

It is not a runner. The body still calls your agent, exactly as a plain test
would. DriftCatch does not own your agent and does not know how to invoke it —
`Suite` removes the repetition around the call, not the call.
