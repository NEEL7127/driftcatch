# DriftCatch documentation

**Catch the moment your AI agent's behaviour changes.**

DriftCatch records what your agent did, replays it deterministically, and fails
your build when the behaviour changes. It is a pre-merge CI gate, not a
dashboard and not a scoring system.

```python
import driftcatch as drift

def test_refund_flow():
    with drift.take("refund_1182") as take:
        answer = my_agent("refund order 1182")

    take.assert_tools().called_in_order("lookup_order", "issue_refund")
    take.assert_output().contains("refunded")
```

First run records. Every run after that replays from disk — no network, no
tokens, no spend — and fails the moment your agent decides something different.

---

## Start here

| | |
|---|---|
| [Getting started](getting-started.md) | Install, record your first run, see a regression caught. Five minutes. |
| [Core concepts](concepts.md) | The two-tier model, the approval gate, fingerprints. Read this before trusting the tool. |
| [FAQ](faq.md) | Short answers, including what DriftCatch deliberately does not do. |

## Using it

| | |
|---|---|
| [Recording and replay](recording-and-replay.md) | Modes, what gets captured, tool recording, streaming. |
| [Assertions](assertions.md) | Tools, trajectory, output, cost, latency, invariants. |
| [Scenario suites](suites.md) | One test body across many cases, with a shared policy. |
| [Matching](matching.md) | How a request finds its recorded reply, and how to change that. |
| [Configuration](configuration.md) | Every setting, its default, and where it can be set. |

## Getting more out of your recordings

| | |
|---|---|
| [Learned invariants](invariants.md) | Rules mined from the corpus that nobody had to write. |
| [Tool contracts](contracts.md) | What your tools really do, versus what you declared. |

## Operating it

| | |
|---|---|
| [CLI reference](cli.md) | Every command, flag and exit code. |
| [CI integration](ci.md) | Pipelines, JUnit reports, what to gate on. |
| [Coding agents and MCP](MCP.md) | Using DriftCatch from Cursor, Claude Code and other agents. |
| [Security](security.md) | Secrets, redaction, recording-name safety, the MCP boundary. |
| [Troubleshooting](troubleshooting.md) | Specific errors and what to do about them. |

---

## What DriftCatch is not

Being clear about this is the point, not a disclaimer.

**Not an evaluation framework.** It does not score whether an answer is *good*.
That is a different discipline and [promptfoo](https://github.com/promptfoo/promptfoo)
and [DeepEval](https://github.com/confident-ai/deepeval) do it well. DriftCatch
answers one question: *did the behaviour change?*

**Not an observability platform.** It does not watch production. It runs in CI,
before a merge. [LangSmith](https://www.langchain.com/langsmith) and
[Langfuse](https://langfuse.com) cover production tracing.

**Not an agent framework.** There is no base class, no registry and no runtime
to adopt. It wraps calls your code already makes, at the HTTP layer, so it works
with LangChain, LlamaIndex, CrewAI, a raw SDK, or your own loop — it never had
to know which.

## The honest limitation

Replay cannot tell you how the model would behave *today*. A recording holds
what the model said when it was recorded, not what it would say now. That is
physics, not a missing feature. Catching model-side change requires real calls,
which is exactly what [Tier 2](concepts.md#the-two-tier-model) is for. Any tool
claiming to catch model drift from a cached response is claiming something
impossible.
