# Getting started

Five minutes from install to a caught regression.

## Install

```bash
pip install driftcatch
```

Optional extras:

```bash
pip install "driftcatch[ui]"    # the local review UI
pip install "driftcatch[mcp]"   # the MCP server for coding agents
```

Requires Python 3.11 or newer. DriftCatch intercepts at the HTTP client layer
and supports both `httpx` and `httpx2`, so the OpenAI SDK, the Anthropic SDK,
LangChain, LlamaIndex, CrewAI and anything else speaking HTTP all work without
an adapter.

## Set up the project

```bash
drift init
```

This creates a `recordings/` directory, a `driftcatch.toml` with the defaults
written out, and an `AGENTS.md` section so coding agents working in your repo
discover the conventions.

## Write a test

Nothing about your agent changes. Wrap the call you already make.

```python
# tests/test_agent.py
import driftcatch as drift
from myapp.agent import run_agent


def test_refund_flow():
    with drift.take("refund_1182") as take:
        answer = run_agent("refund order 1182")

    assert "refunded" in answer
    take.assert_tools().called_in_order("lookup_order", "issue_refund")
```

If your tools are plain Python functions, decorate them so their results are
recorded too:

```python
import driftcatch as drift


@drift.tool
def lookup_order(order_id: int) -> dict:
    return db.fetch(order_id)
```

## Record

```bash
drift record
```

That runs your test suite with recording turned on. Real calls go out, real
tokens are spent, and the result lands in `recordings/refund_1182.json`.

## Approve

**Recording is not approving.** A recorded run can be wrong, and an unreviewed
recording would turn a bug into an enforced contract. Look at what happened
first:

```bash
drift show refund_1182
```

```
refund_1182   PENDING
  model      gpt-4o-mini
  trajectory lookup_order -> issue_refund
  tokens     1,240 in / 95 out
  duration   2,310ms
```

If that is the behaviour you want:

```bash
drift approve refund_1182 --by you@example.com
```

Or review everything pending at once, in the browser:

```bash
drift ui
```

## Replay

```bash
pytest
```

The same test now replays from disk. No network, no tokens, no spend, and the
result is byte-identical every time. A suite that took 32 seconds and cost
money runs in under a second for free.

## See a regression caught

Change your agent's system prompt, or reorder a tool, or swap the model id.
Then:

```bash
pytest
```

```
DriftDetected: behaviour differs from golden (tools+order)

  recording:  refund_1182
  changed:    prompt_hash

  golden:   lookup_order -> issue_refund
  current:  lookup_order -> escalate_to_human
                            ^^^^^^^^^^^^^^^^^ first divergence, step 2
```

It names the input that changed (`prompt_hash`), and points at the first step
that diverged rather than dumping the whole trajectory.

## What next

- Re-record deliberately after an intended change: `drift record`, then
  `drift show` and `drift approve`. The previous version is archived, so
  `drift restore` undoes a mistake.
- Add [assertions](assertions.md) beyond tool order — output shape, cost
  ceilings, latency budgets.
- Once you have a handful of approved recordings, run
  [`drift learn`](invariants.md) and see what rules your agent has been
  following all along.
- Wire it into [CI](ci.md).

## A note on what replay can and cannot catch

Replay catches changes on *your* side: code, prompts, model id, tool schemas,
matcher config. It cannot catch the provider changing the model underneath you,
because a recording holds what the model said then, not what it would say now.

For that, [Tier 2](concepts.md#the-two-tier-model) makes real calls:

```bash
drift check --live
```

That costs money, so it belongs on a schedule or before a release, not on every
pull request.
