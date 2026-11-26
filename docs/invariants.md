# Learned invariants

Writing assertions is the part nobody finishes. You assert the two things you
thought of, and the twenty rules your agent has genuinely always followed stay
unwritten — so when one of them breaks, nothing catches it.

Invariant mining reads your approved recordings and tells you what those rules
are.

```bash
drift learn
```

```
Mined 15 rule(s) from 23 approved recording(s)

  lookup_order always runs before issue_refund        23/23   conf 1.00
  validate_customer is called in every run            23/23   conf 1.00
  issue_refund is always the last tool                18/18   conf 1.00
  lookup_order is never called twice in a row         23/23   conf 1.00
  a run never makes more than 6 tool calls            23/23   conf 1.00
  lookup_order.args.order_id always equals
    issue_refund.args.order_id                        18/18   conf 1.00
  escalate_to_human is offered but never called       23/23   conf 1.00
```

Nobody wrote those. They were inferred from what the agent has actually done.

The technique is [specification mining](https://en.wikipedia.org/wiki/Specification_mining),
which goes back to Daikon (2001) and is well established for ordinary programs.
Applying it to agent trajectories is what makes it useful here: an agent's
behaviour is exactly the kind of thing nobody writes down.

---

## The templates

| Kind | Reads as |
|---|---|
| `always_precedes` | `a` always runs before `b` |
| `always_called` | `a` is called in every run |
| `never_called` | `a` is offered but never called |
| `always_terminal` | `a` is always the last tool |
| `never_consecutive` | `a` is never called twice in a row |
| `max_tool_calls` | A run never makes more than *n* tool calls |
| `data_equality` | `a.args.x` always equals `b.args.y` |

`data_equality` is the one that catches real bugs. "The order id we look up is
the order id we refund" is a rule everybody assumes and nobody tests, and it is
exactly what breaks when a loop variable gets reused.

---

## Why the rules are not noise

Mining naively produces dozens of coincidences. Four filters run before anything
is proposed.

**Minimum support.** A rule needs at least 5 confirming runs. Below that there
is no corpus, only anecdote.

**A confidence gate.** Confidence is `1 - 0.5^support`, the Daikon-style odds
that a pattern this consistent is not a coincidence. The gate is 0.95, which
needs 5 runs to clear — the two thresholds are pinned together deliberately, so
neither is dead code.

**The value must vary.** If a field is the same constant in every run, "these
two fields are always equal" is not a relationship, it is two constants. Rules
over values that never varied are discarded.

**Redundancy removal.** If `a` precedes `b` and `b` precedes `c`, then
`a` precedes `c` is implied and adds nothing. Transitive ordering rules and
equality chains are collapsed. On a real corpus this took 19 mined rules down to
15 — and the 15 are the ones worth reading.

**Only approved recordings are read.** Mining from unreviewed runs would launder
a bug into a specification. This is the same boundary the
[approval gate](concepts.md#the-approval-gate) draws everywhere else, and it is
why "we have recordings" is not the same as "we can mine rules".

---

## Rules are proposed, not enforced

`drift learn` writes rules as **pending**. A pending rule is never enforced.

```bash
drift invariants
```

```
ENFORCED   RULE                                          EVIDENCE   CONF
yes        lookup_order always runs before issue_refund   23/23      1.00
no         escalate_to_human is offered but never called  23/23      1.00
```

Review them and enforce the ones you believe:

```bash
drift learn --approve
```

That second gate matters. A mined rule is a description of the past, and some
descriptions should not become laws — "escalate_to_human is never called" is
true today and is probably not something you want to freeze.

---

## Enforcing them

```python
with drift.take("refund_1182") as take:
    run_agent("refund order 1182")

take.assert_invariants().hold()
```

This is the payoff: a scenario with no hand-written assertion at all is still
guarded by every rule the corpus established. Add it to a
[suite's](suites.md) `check` and it covers every case at once.

```python
suite = drift.Suite("refund", cases={...}, check=lambda t: t.assert_invariants().hold())
```

Inspect without failing:

```python
for violation in take.invariant_violations():
    print(violation.message)
```

Approvals survive re-mining. Running `drift learn` again after adding
recordings keeps the rules you already enforced, matched by a stable key.

---

## Tuning

```bash
drift learn --min-support 10
```

Raising support gives fewer, stronger rules. There is no setting to lower the
confidence gate, because a rule below it is a coincidence and enforcing
coincidences is how a test suite becomes something people disable.

---

## JSON

```bash
drift learn --json
drift invariants --json
```

Both are available to coding agents through the [MCP server](MCP.md). `learn`
is the one exposed tool that writes, and it is safe to expose precisely because
it cannot enforce: an agent can propose rules and cannot turn its own guesses
into a gate.
