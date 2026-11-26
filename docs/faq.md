# FAQ

### Does this replace my evaluation suite?

No, and it is not trying to. Evaluation asks *is this answer good?* DriftCatch
asks *did the behaviour change?* Those are different questions with different
tooling. Run promptfoo or DeepEval for quality; run DriftCatch as the pre-merge
gate. They sit next to each other.

### Can replay catch the model changing underneath me?

No. A recording holds what the model said when it was recorded. It cannot hold
what the model would say now — that is physics, not a missing feature.

Catching model-side change requires real calls, which is what
[Tier 2](concepts.md#the-two-tier-model) is for. Any tool claiming to catch
model drift from a cached response is claiming something impossible, and that
claim is worth checking before you trust its green builds.

### Which agent frameworks are supported?

The question does not really apply. Interception is at the HTTP client layer
(`httpx` and `httpx2`), so LangChain, LlamaIndex, CrewAI, AutoGen, a raw SDK or
your own loop all work without an adapter — including frameworks that did not
exist when this was written.

If your stack speaks HTTP through one of those libraries, it works.

### Do I have to restructure my agent?

No. There is no base class, no registry and no runtime to adopt. You wrap the
call you already make:

```python
with drift.take("scenario"):
    my_existing_function()
```

### What if the first recording is wrong?

That is exactly why [approval](concepts.md#the-approval-gate) is a separate
step. A recording is `PENDING` until a human approves it. If it is wrong,
`drift reject` it, fix the agent, and record again.

Without that gate the first wrong run would become the enforced contract, and
the fix would then fail as a regression.

### Why does my latency assertion do nothing?

It is being skipped, with a `LatencyNotMeasured` warning. Replay serves bytes
from disk, so there is no real call to time, and checking the recorded duration
would compare a number against itself and pass forever. Check latency with
`drift check --live`. See [assertions](assertions.md#latency--assert_latency).

### Should recordings go in version control?

Yes. They are the baseline, and a behaviour change should be visible in the
pull-request diff. Status, fingerprint and trajectory are stored as plaintext
precisely so a reviewer can read the change.

Run `drift scan` in CI and a pre-commit hook first — and read
[security](security.md) on what redaction does not cover.

### How big do recordings get?

Event bodies are gzipped, so a repetitive payload compresses hard. If size is
still a problem, `drift prune` drops raw bodies and keeps trajectories;
behaviour assertions keep working, and byte-level replay stops.

### Can a coding agent use this?

Yes — that is a design goal. There is an [MCP server](MCP.md) with ten tools,
`--json` on the machine-readable commands, documented exit codes, and
`drift init` writes an `AGENTS.md` section so agents discover the conventions.

The exposed tools are read-only. An agent can diagnose a failure and cannot
approve its way out of one.

### Why can't the agent approve recordings?

Because it would. An agent looking at a red behaviour test will reach for the
fastest way to make it green, and approving the new recording is that way — it
would be doing exactly what it was asked, and the signal would be gone.

### Does it work with streaming?

Yes, including streamed tool calls and streamed reasoning deltas from both
OpenAI-shaped (`reasoning_content`) and Anthropic-shaped (`thinking`) models.

### Does it work with reasoning models?

Yes. o1-style, DeepSeek-R1, QwQ and Nemotron return `content: null` with the
answer in `reasoning_content`. Both fields are captured, `take.output` falls
back to reasoning, and reasoning tokens are counted separately.

This was a real bug once: the parser read only `content`, so assertions compared
against `None` while the run reported green.

### Async?

Yes. Async HTTP clients and `async def` tools are both supported, and sessions
live in a `ContextVar` so asyncio tasks are isolated from each other.

### What Python versions?

3.11 and newer.

### What does it cost to run?

Replay is free and offline. Recording costs one real run per scenario. Live
checks cost real money and never happen by accident — in CI they refuse unless
`DRIFTCATCH_ALLOW_LIVE=1` is set.

On one measured corpus, a suite that took 32 seconds live replayed in under 0.1
seconds for $0.00.

### How much overhead does it add?

About 0.6ms per event, flat from 20 to 200 events. Matching is linear, not
quadratic, and that is pinned by a test.

### What is `drift contracts` for, if it never fails?

It is a report, not a gate. It tells you where your declared tool schemas and
their real use disagree — dead parameters costing tokens on every request,
arguments the model invented, unstable result shapes. See
[contracts](contracts.md).

### Is it stable?

It is `0.1.0`. The recording format carries an integer version and refuses to
load a format it does not understand, so an upgrade never silently misreads an
old golden. The Python API may change between `0.x` minors; breaking changes are
listed in the changelog with a migration note.

Honest caveat: it has been tested extensively but has not yet been used by
anyone other than its author, and CI has not been exercised on Linux. Treat
`0.1.0` accordingly.
