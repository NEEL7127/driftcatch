# Core concepts

Four ideas carry the whole design. Understanding them is what tells you when to
trust a green build.

---

## The two-tier model

A recording holds what the model said **when it was recorded**. It does not,
and cannot, hold what the model would say now. Everything else follows from
that single fact.

So DriftCatch has two tiers that catch different things:

### Tier 1 — replay

```bash
drift check          # or just: pytest
```

Free, offline, deterministic, runs on every pull request.

**Catches:** your code changing, your prompt changing, the model *id* changing,
tool schemas changing, matcher config changing.

**Cannot catch:** the provider silently updating the model behind a stable name.
Not a limitation to be engineered around — a recorded response is a recorded
response.

### Tier 2 — live check

```bash
drift check --live
```

Real calls, real money. Runs nightly, or before a release.

**Catches:** model-behaviour change. The thing Tier 1 physically cannot see.

Because live model output varies run to run, a live check repeats the scenario
and counts how many runs matched, rather than demanding one exact match:

```python
import driftcatch as drift


@drift.live_check("refund_1182", runs=5)
def test_refund_still_behaves():
    result = drift.run_live("refund_1182", lambda: run_agent("refund order 1182"), runs=5)
    assert result.matches() >= 4, result.report
```

`live_check` **skips** rather than fails when live runs are not permitted, so
your ordinary suite stays free and offline while the gate still exists for the
runs that opt in.

Live runs never happen by accident: in CI they refuse unless
`DRIFTCATCH_ALLOW_LIVE=1` is set.

This is the honest version. A tool that claims to catch model drift from a
cached response is claiming something impossible.

---

## The approval gate

**Recording a run does not approve it.**

This is the feature people skip past, and it is the one that makes the rest
safe. A recorded run can be wrong. If "recorded" meant "correct", then the first
time your agent did something stupid, that stupidity would become the enforced
contract — and every future run that fixed it would fail as a regression.

So recordings have a status:

```
PENDING  ->  APPROVED     (drift approve)
         ->  REJECTED     (drift reject)
```

Only an approved recording is a golden. Replaying a `PENDING` recording works
but warns. Invariant mining and contract extraction read **only** approved
recordings, because inferring rules from unreviewed runs would launder a bug
into a specification.

The lineage here is [ApprovalTests](https://approvaltests.com) and Jest snapshot
review: the machine proposes, a human disposes.

Review them in the terminal or the browser:

```bash
drift review          # walk the pending queue
drift ui              # the same thing, with diffs rendered
```

---

## Fingerprints

Four inputs determine whether a recording still describes reality:

| Component | What it covers |
|---|---|
| `model_id` | The model you asked for |
| `prompt_hash` | System prompt and instructions |
| `tool_schema_hash` | The tools you offered and their schemas |
| `matcher_hash` | How requests are matched to recorded events |

They are hashed **separately**, not rolled into one digest. That is what lets a
drift report say *which* input changed instead of "something changed":

```
recording 'refund_1182' is STALE: prompt_hash changed since it was approved.
```

And it is what makes `drift bisect` possible — attributing a behaviour change to
one of the four by holding the others fixed.

A recording whose fingerprint no longer matches is **stale**. What happens then
is configurable:

```toml
drift_policy = "warn"    # "warn" (default), "fail", or "ignore"
```

---

## Trajectories

A trajectory is what your agent *decided and did*, separate from what it *said*.

```
lookup_order -> check_eligibility -> issue_refund -> [stop]
```

This is the object that gets diffed, because it is the part of agent behaviour
that matters and that is stable enough to compare. Comparing raw text would fail
on every reworded sentence; comparing trajectories fails when the agent actually
behaves differently.

Diffs report the **first** point of divergence rather than everything that
differs after it. Once step 2 changed, every later step is downstream noise.

Tolerance is configurable:

| Level | Fails when |
|---|---|
| `tools` | The set of tools called differs |
| `tools+order` | The sequence differs (default) |
| `exact` | Any step, argument digest or outcome differs |

---

## How these fit together

```
      your agent runs
             |
             v
   +---------------------+
   |  HTTP interception  |   httpx / httpx2, so any SDK works
   |  @drift.tool        |
   +---------------------+
             |
             v
       recording (PENDING)
             |
        human review  <----  the approval gate
             |
             v
       recording (APPROVED) = golden
             |
     +-------+--------+----------------+
     |                |                |
     v                v                v
  replay          invariant        contract
  (Tier 1)         mining          extraction
     |                |                |
     v                v                v
  behaviour      learned rules    schema vs reality
   diff           as a gate         as a report
```

Tier 2 (`--live`) bypasses the recording for the call itself but still builds a
trajectory, so it compares real current behaviour against the golden.

---

## Why these boundaries

Each of these is a line DriftCatch deliberately does not cross.

**It does not score answers.** Evaluation asks "is this good?". DriftCatch asks
"did this change?". Keeping those apart is why the tool is small enough to
trust: a behaviour gate that is also a quality judge gives you a failing build
you cannot interpret.

**It does not own your agent.** No base class, no registry, no runtime. It hooks
the HTTP client, so the question "does it support framework X" never comes up.

**It does not let an agent approve.** The [MCP server](MCP.md) exposes read-only
tools. An agent looking at a red test reaches for the fastest way to make it
green, and `approve` is that way.
