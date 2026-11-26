# Critique of the v1 Design

> A hard, honest review of `REHEARSAL_DESIGN.md`. Issues are ordered by severity.
> **One blocking issue is architectural**: a central claim in the v1 design is not physically possible as written.

---

## Summary verdict

The core idea is sound and worth building. But v1 has **three blocking defects**, **ten major gaps**, and **under-states the competition badly enough that the differentiation story has to be re-based**.

The good news: fixing the blocking architectural flaw doesn't kill the project — it makes it a *better, more honest* product, and it surfaces the one feature that genuinely differentiates it from the incumbent (`vcrpy`). That feature is **staleness detection**, which v1 listed as a *risk* rather than the headline capability it should be.

---

## BLOCKING — must be fixed before any code is written

### B1. Replay cannot detect model-behaviour change. Stage 5 is impossible as written.

This is the big one.

The v1 doc's flagship demo (§6 Stage 5) claims: a teammate rewords the prompt, the model now calls `web_search` instead of `lookup_order`, and replay catches it.

**It cannot.** Under replay, the LLM's response is served *from the tape*. The model never runs. Therefore the agent's decisions are frozen to whatever was recorded — it is structurally incapable of "switching to web_search." Replay would either (a) serve the old response and the agent would dutifully call `lookup_order` again (false green), or (b) fail to match because the request changed, which tells you *something* changed but not *what the model would now do*.

This isn't a nitpick — it's the headline demo of the whole framework, and it would collapse the first time anyone tried it.

It's also the single most-cited criticism of VCR-style replay for LLMs:

> "If you change a prompt, the LLM might respond differently in real life, but VCR will keep aggressively replaying the old response. Your tests will pass, but they're testing outdated reality."

**Fix — split into two tiers with different guarantees:**

| Tier | What runs | Cost | Catches | Cadence |
|---|---|---|---|---|
| **Tier 1 — Replay** | your code, with model+tool responses pinned from tape | free, ms | regressions in *your* glue: parsing, control flow, state, retries, error handling | every commit |
| **Tier 2 — Live behaviour check** | the real model, N times | real API spend | *model-behaviour* change ("now picks a different tool") | on prompt/model change, nightly, pre-release |

Plus a third, cheap capability that v1 missed entirely:

- **Request diff (free, under replay).** When your prompt/context/tool-schema changes, the *request* you send diverges from the recorded request. You can diff requests without running the model. This catches "someone changed the prompt upstream" for zero cost — it just can't tell you what the model would now decide.

Being explicit about which tier buys which guarantee is strictly better product design than v1's single blurred flow.

---

### B2. The assertion API in the worked example does not work.

```python
def refund_agent(message):
    ...
    return f"Refunded ${order.total} to order {order.id}"   # <- a plain string

def test_refund():
    with rh.tape("refund_1182"):
        result = refund_agent("refund order 1182")
    rh.assert_tools(result).called("lookup_order")           # <- result is a string
```

`result` is the agent's own return value — a string. It carries no trajectory, no events, no cost. `assert_tools(result)` has nothing to read. The example as printed would raise immediately.

**Fix — assert on the recording, not on the agent's return value:**

```python
with us.take("refund_1182") as take:
    result = refund_agent("refund order 1182")

take.assert_tools().called("lookup_order").never("web_search")
take.assert_cost().under(tokens=8_000)
assert "Refunded $40" in result      # ordinary assertion on the agent's output
```

The context manager yields the recording object. This is also how `vcrpy` and most recorders work, so it matches user expectation.

---

### B3. "Adapters per provider & framework" is the wrong interception layer.

v1 specifies an adapter per provider (OpenAI, Anthropic, LiteLLM…) *and* per framework (LangGraph, CrewAI, Pydantic AI…). That is an unbounded maintenance surface — every SDK version bump can break you, forever, and you can never claim broad coverage.

**Fix — intercept at the HTTP transport layer.** Nearly every Python LLM SDK (OpenAI, Anthropic, LiteLLM, Mistral, Cohere, Google GenAI) issues requests through **`httpx`**. One transport-level interceptor covers all of them at once, survives SDK refactors, and is how the mature prior art (cagent, llama-stack, vcrpy) does it.

Framework "adapters" then shrink to a much smaller job: **extracting the trajectory** and **intercepting tool calls** (see M5), not proxying model calls. This cuts v0 scope dramatically while *increasing* coverage.

---

## MAJOR — fix before launch

### M1. The name is taken.

- **`rehearsal`** is an existing PyPI package (a measurement/research library).
- **`rehearser`** is also taken — and is, ironically, almost the same concept ("replay interactions for python method, HTTP or etc.", keywords include *contract testing, testing tools, rehearsal, proxy*).

Shipping under either name means a confusing install story and no clean namespace.

**Fix:** pick a new name and verify it on **PyPI, TestPyPI, npm, and GitHub** before writing a line. Candidates (all still need verification): **Understudy**, **Tapedeck**, **Takes**, **Soundcheck**. This document and the new design use **Understudy** as the working name, isolated to a single constant so renaming is a one-line change.

### M2. The competition is substantially stronger than v1 admits.

v1's comparison table lists "VCR-style cassettes" as a vague also-ran. Reality:

- **`vcrpy` + `pytest-recording` is the incumbent**, and **LangChain's own documentation officially recommends it** for exactly this use case. That is a very strong default to displace.
- **`coding-agent-vcr`** already exists on PyPI — "VCR / time-travel replay for Claude Code and Codex — deterministic offline agent testing."
- **`cendor-cassette`** exists.
- There are already published write-ups titled "VCR-style record/replay for LLM tests — make your agent tests deterministic and free."

**Consequence:** "record and replay LLM calls" is **not** a differentiator. It is table stakes, available today in two lines of config. The differentiation must be re-based onto what `vcrpy` structurally cannot do:

1. **Staleness detection** (see M9) — vcrpy will serve stale responses forever and never tell you.
2. **Trajectory/behaviour as a first-class object** — vcrpy knows HTTP, not tool calls, ordering, or agent decisions.
3. **The approval gate** — vcrpy has no notion of a human-blessed golden.
4. **The two-tier model** (B1) — vcrpy has no concept of a live behaviour check.

The README must answer *"why not just use vcrpy?"* above the fold. If it can't, the project is dead on arrival on Hacker News.

### M3. Cascading mismatch makes output useless.

If the prompt changes, event #1 stops matching — and so do events #2…#N, because everything downstream shifts. v1 has no story for this. A user changing one word would see fifty failures.

**Fix:** matching is **sequential and fail-fast**. Report the **first divergence point only** — "diverged at event #3 (llm): request differs from tape" with a focused diff — then stop. One change → one clear error.

### M4. Secrets and PII in committed tapes — an adoption blocker, barely mentioned.

Tapes contain full prompts and responses and are *committed to git*. That means: API keys in `Authorization` headers, customer data in prompts, PII in responses. Confirmed as a real failure mode in practice — cassettes capture `Authorization` headers unless explicitly filtered.

v1 mentions "secret redaction" once, as a parenthetical in a diagram. For any company, this is a go/no-go question.

**Fix — make it a first-class, default-on subsystem:**
- Redaction happens **at record time, before anything touches disk** (never a post-process).
- Default deny-list: `authorization`, `api-key`, `x-api-key`, `cookie`, `set-cookie`, plus common key patterns (`sk-`, `AKIA`, bearer tokens).
- `understudy scan` command to audit existing tapes.
- Documented PII policy + custom redactor hooks.
- Refuse to write a tape if a known key pattern survives redaction.

### M5. Tool interception is undesigned — and it's the hard part.

Model calls are HTTP and therefore interceptable. **Tools are arbitrary Python functions.** There is no universal hook. v1 simply assumes tool calls get captured.

**Fix — be explicit and honest about the three mechanisms:**
1. **Explicit decorator** (`@us.tool`) — always works, requires a one-line change per tool. The documented default.
2. **MCP transport interception** — MCP tool calls go over a transport, so they're interceptable like HTTP. Free coverage for MCP users.
3. **Framework adapters** — for LangGraph/CrewAI, hook the framework's own tool-dispatch point. Best-effort, per-framework.

Do not claim automatic universal tool capture. It isn't achievable.

### M6. "Trajectory" is never defined — yet the whole diff depends on it.

v1 shows `"trajectory": ["lookup_order", "issue_refund"]` — just tool names. That is too thin to be useful and too thin to diff meaningfully. Unanswered: are arguments included? Nesting/sub-agents? Parallel branches? Retries? LLM steps, or only tools?

**Fix:** define a precise, versioned trajectory schema (see new design §5). Diff tolerance becomes a real, explicit knob: `tools` / `tools+order` / `tools+order+args`.

### M7. Errors and exceptions are never captured.

If a tool raises, or the API returns 429/500, **that is behaviour** — often the exact behaviour you most want to pin (retry logic!). v1 records only successful results.

**Fix:** events carry `result` **or** `error` (type, message, status). Replay must be able to *re-raise* a recorded exception so retry/error paths are testable. This is a significant capability vcrpy handles poorly.

### M8. Parallel test execution would corrupt tapes.

`pytest-xdist` is near-universal in real repos. v1 implies global interception (monkeypatching, a global config). Under parallel workers that's a race: interleaved writes, cross-test event bleed, nondeterministic failures — in a tool whose entire value proposition is determinism.

**Fix:** all interception state lives in a **per-test context** bound to a `contextvars.ContextVar` (async-safe and thread-safe), never module globals. Tape writes are **atomic** (write temp + `os.replace`). Add an xdist test to CI from day one.

### M9. Staleness is treated as a risk. It should be the headline feature.

v1 buries "tape rot" in the risk table. But staleness is *the* criticism of replay testing, and handling it well is the clearest thing that separates this from `vcrpy`.

**Fix — promote it to a core subsystem.** Every tape stores a **fingerprint** of the things that determine whether it still reflects reality:

```
fingerprint = hash(system_prompt + tool_schemas + model_id + matcher_config)
```

On every run, recompute and compare. If it differs, the tape is marked **STALE** and the framework says so:

> `⚠ tape "refund_1182" is STALE — the system prompt changed since it was approved.
>  Replay still passes, but it no longer reflects current model behaviour.
>  Run: understudy check --live refund_1182`

That single feature answers "your tests pass while testing outdated reality" — and nothing in the incumbent toolchain does it. **This should be on the first screen of the README.**

### M10. Tape size and repo bloat.

Full prompts + responses, especially with long contexts or streaming chunks, produce large files. A few hundred tests could add tens of MB to a repo, slowing clones and making diffs unreadable.

**Fix:** gzip event bodies by default; keep metadata and trajectory in readable plaintext (so PR diffs stay reviewable); warn above a configurable per-tape size budget; offer `--prune` to drop raw bodies while keeping the trajectory for behaviour-only tests.

---

## MINOR / SCOPE DISCIPLINE

| # | Issue | Fix |
|---|---|---|
| S1 | **v0 scope creep** — OTel export, local web UI, TypeScript port, cut-point replay, seeded fault injection are all in v1's plan. | Cut all of them from v0. Ship Python + CLI + pytest only. Each is a post-traction feature. |
| S2 | **"Local web UI (premium version)"** signals paid tiers before the project has a single user, and conflicts with the "neutral open tool" positioning. | Remove all commercial signalling from the OSS docs. |
| S3 | **Cost assertions** assume comparable token accounting across providers. | Normalize usage into one schema per provider; document which providers report what. |
| S4 | **Tape format versioning** is listed as an "open question." | It is not optional — tapes are committed artifacts. Ship `version: 1` plus a loader that refuses unknown versions with a clear message, from the first commit. |
| S5 | Says "five components," lists seven. | Editorial. |
| S6 | No stated Python version floor, packaging tool, or typing policy. | Python 3.11+, `uv`, `ruff`, strict `mypy`, `pydantic` at boundaries. |

---

## What survives, and what the project actually is

After the above, the honest positioning is **narrower but much more defensible** than v1's:

> **Understudy is not "record/replay for LLMs" — that already exists and is free.**
> Understudy is **an agent-behaviour regression system**: it pins your agent's behaviour as a human-approved golden, gives you free deterministic tests of your own code, tells you the moment a tape stops reflecting reality, and gives you a deliberate live check to re-verify behaviour when it matters.

The four load-bearing differentiators:

1. **Staleness detection via fingerprinting** — the headline.
2. **Trajectory as a first-class, diffable object** — behaviour, not bytes.
3. **Human approval gate** — golden state, reviewed in the PR.
4. **Two-tier model** — a free gate on every commit, a deliberate live check when it counts.

Everything else (record, replay, matching, normalizers) is **necessary plumbing, not a selling point.** Build it well, but don't market it.

---

## Honest risk assessment

- **Displacing a documented default is hard.** `vcrpy` + `pytest-recording` is free, mature, and recommended in LangChain's docs. You must be clearly better *on the axes above*, not merely different.
- **Tool interception will never be fully automatic.** Accept the one-line decorator and say so plainly.
- **Tier 2 costs real money**, so adoption of the behaviour-check half depends on teams accepting a paid nightly run. Keep Tier 1 valuable enough to stand alone.
- **The hard engineering is concentrated in one module** (request normalization + matching). If that isn't excellent, nothing else matters. Build it first, and build it with a brutal test suite.

---

*Next: `DESIGN.md` (revised architecture) and `IMPLEMENTATION_PLAN.md` (mechanical build order).*
