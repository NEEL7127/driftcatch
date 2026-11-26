# DriftCatch — Design

> **Catch the moment your AI agent's behaviour changes.**
> Record a run once, approve it as golden, then measure your behavioural coverage, detect drift automatically, attribute *why* behaviour changed, and test counterfactuals — without re-recording.

**Version:** design v3 · supersedes v2 (`Understudy`) and v1 (`Rehearsal`)
**Package:** `driftcatch` · **CLI:** `drift` · **Verified available on PyPI** (2026-11-24)
**⚠ Still verify:** GitHub org/repo and npm before launch. Name is isolated in `driftcatch/_name.py`.

---

## 1. The thesis (what makes this different)

Record/replay for LLM calls is **table stakes**. `vcrpy` + `pytest-recording` does it, it's free, mature, and **officially recommended in LangChain's own documentation**. `coding-agent-vcr` and `cendor-cassette` already exist. We are not competing there.

The insight that makes DriftCatch a different category:

> **Existing tools treat a recording as a *mock* — something to stub out a network call.
> DriftCatch treats a recording as a *behavioural dataset* — something you can measure, attribute, mutate, and interrogate.**

Once you have a corpus of human-approved behaviour recordings, five capabilities become possible that no current tool offers. These are the product. Record/replay is just the substrate underneath them.

| # | Capability | The question it answers | Who else does it |
|---|---|---|---|
| 1 | **Invariant mining** ⭐⭐ | *"What rules does my agent always follow?"* | **nobody** |
| 2 | **Behaviour Coverage** ⭐ | *"Which decision paths were ever tested?"* | partially (AgentCheck) |
| 3 | **Behaviour Bisect** | *"WHY did it change — prompt, model, or tool?"* | **nobody** |
| 4 | **What-If Mutation** | *"What if that tool had failed?"* | **nobody** |
| 5 | **Determinism Score** | *"How flaky is this really?"* | **nobody** |
| 6 | **Drift Detection** | *"Does this recording still reflect reality?"* | rare |
| — | Record / replay / trajectory diff | *"Did the tool sequence change?"* | **6+ tools — commoditised** |

Plus **Model-Swap Diff** (an application of #3): *"Can I move to a cheaper model without changing behaviour?"*

### The one-line pitch

> **`vcrpy` stops your tests from calling the API. DriftCatch tells you when your agent stopped behaving.**

### Non-goals

- Not an orchestration framework. It tests whatever you already built.
- Not an answer-quality eval platform. Quality scoring is a thin optional layer.
- Not a hosted service. Local-first, file-based, git-native. The UI runs on your machine.
- Not a promise of model determinism. It *measures and bounds* non-determinism; it cannot abolish it.

---

## 2. Design principles

1. **Recording ≠ approving.** Capture states what happened; a human states what is *correct*.
2. **Never silently serve a wrong response.** A **false green is worse than a false red.**
3. **Honest guarantees.** Every tier states exactly what it can and cannot catch. No feature claims more than physics allows.
4. **A recording is a dataset, not a mock.** Every feature should exploit the corpus.
5. **Zero-config golden path.** Install → record → approve → replay, untouched. Config is progressive disclosure.
6. **Config selects between real mechanisms** — never a stand-in for one.
7. **Fail closed on secrets.** If redaction is uncertain, refuse to write.
8. **Local-first.** No account, no server, no telemetry. The UI binds `127.0.0.1` only.

---

## 3. The two-tier model (the physics)

The most important architectural constraint, and the one v1 got wrong: **replay and live behaviour checking are different tests with different guarantees.**

Under replay, the model's response comes *from the recording* — the model never runs. So replay **structurally cannot** detect "the model now picks a different tool." Pretending otherwise produces a tool that lies.

```
┌─ TIER 1 · REPLAY ──────────────────────────────────────── every commit ──┐
│  Model + tool responses served from the recording. Model never runs.      │
│  Cost $0 · milliseconds · fully deterministic                             │
│  ✅ regressions in YOUR code: parsing, control flow, state, retries,      │
│     error handling, tool wiring, output shape                             │
│  ✅ request divergence (prompt / context / tool-schema changed)            │
│  ❌ cannot detect what the model would now decide                          │
└───────────────────────────────────────────────────────────────────────────┘

┌─ TIER 2 · LIVE CHECK ──────────── on prompt/model change · nightly ──────┐
│  The real model runs, N times. Trajectories compared to golden.           │
│  Real API spend · seconds–minutes · non-deterministic                     │
│  ✅ model-behaviour change, model-version drift, prompt side effects      │
│  ⚠️ thresholded with pass@k — never exact match                            │
└───────────────────────────────────────────────────────────────────────────┘
```

**Tier 1b — request diff (free).** When your prompt or tool schema changes, the *request* diverges from the recording. Detectable without running the model — the cheap early warning that bridges the tiers.

---

## 4. The differentiators

### 4.1 Behaviour Coverage ⭐ (the headline)

Code coverage tells you which *lines* ran. Nothing tells you which **agent behaviours** have ever been tested. DriftCatch computes this from the approved recording corpus.

```
$ drift coverage

  BEHAVIOUR COVERAGE                                      62%  ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░

  TOOLS                      covered   calls   error path
  ─────────────────────────────────────────────────────────
  lookup_order                  ✅       14        ✅
  issue_refund                  ✅        9        ❌  never tested
  web_search                    ❌        0        ❌  NEVER TESTED
  escalate_to_human             ❌        0        ❌  NEVER TESTED

  DECISION PATHS
  ✅ order found → refund issued                    (8 recordings)
  ✅ order found → refund failed → retry            (2 recordings)
  ❌ order NOT found                                 never recorded
  ❌ refund amount exceeds limit                     never recorded

  4 of 11 behaviours have no coverage.
  → drift suggest     # proposes scenarios to record next
```

**What's measured:** tools invoked · tool error paths · decision branches (distinct LLM tool-choices from equivalent states) · multi-tool sequences · retry paths · terminal outcomes.

**Why it drives adoption:** it's a *number*, developers are addicted to coverage numbers, and it produces a **README badge** (`behaviour coverage 62%`) — the same viral mechanic that made Codecov ubiquitous. `drift suggest` then turns a gap into an action.

### 4.2 Drift Detection

Every recording stores a fingerprint of the inputs that determine whether it still reflects reality:

```python
fingerprint = sha256(canonical(system_prompt) + canonical(tool_schemas)
                     + model_id + canonical(matcher_config))
```

Recomputed every run. On mismatch the recording is **STALE** and we name *which input changed*:

```
⚠ recording "refund_1182" is STALE
    approved: 2026-03-02 · model gpt-5.2 · prompt a1f3…
    current:  model gpt-5.2 · prompt 9c22…   ← system prompt changed
  Replay still passes, but no longer reflects current model behaviour.
  → drift check --live refund_1182
```

This directly answers replay testing's central criticism — *"your tests pass while testing outdated reality."* `drift_policy`: `warn` (default) · `fail` · `ignore`.

### 4.3 Behaviour Bisect

When behaviour changes, every other tool says *"it differs."* None says **why**. Because we fingerprint each input *separately*, we can attribute the cause by re-running with one variable reverted at a time — `git bisect` for agent behaviour.

```
$ drift bisect refund_1182

  Behaviour changed:  lookup_order → web_search
  Testing 3 changed inputs (3 live runs)…

    ✓ revert system_prompt  → behaviour RESTORED
    ✗ revert tool_schemas   → behaviour still changed
    ✗ revert model_id       → behaviour still changed

  ROOT CAUSE: the system prompt change.
    - "Use available tools to find the order."
    + "Search for the order information."
                ^^^^^^ "search" biases tool choice toward web_search
```

High-value, genuinely novel, and only possible because inputs are fingerprinted independently.

### 4.4 What-If Mutation

A recording is a real, complete execution. Mutate one event and replay to test counterfactuals — **no re-recording, no hand-written mocks.**

```python
with drift.take("refund_1182").what_if(
        tool="lookup_order", returns={"error": "not_found"}) as take:
    result = refund_agent("refund order 1182")

take.assert_tools().called("escalate_to_human")
```

```
$ drift what-if refund_1182 --fail-tool issue_refund
  Replaying with issue_refund → TimeoutError
  trajectory: lookup_order → issue_refund(error) → issue_refund(retry) → ok
  ✅ retry path exercised
```

Built-in mutations: `--fail-tool`, `--empty-result`, `--null-field`, `--slow-tool`, `--truncate-response`. **This is how you close the coverage gaps #4.1 reports** — the two features compose into a loop.

### 4.5 Determinism Score

Nobody tells you *how* non-deterministic your agent actually is — so nobody knows which tests are safe to gate on.

```
$ drift determinism refund_1182 -n 20

  20 live runs · 3 distinct trajectories

  85%  lookup_order → issue_refund                      ← golden
  10%  lookup_order → lookup_order → issue_refund         (redundant call)
   5%  web_search   → issue_refund                        ⚠ divergent

  DETERMINISM SCORE  0.85     recommended gate: pass_k=(17, 20)
  ⚠ 5% of runs use web_search — consider tightening the prompt.
```

Turns "agents are flaky" from a complaint into a **measurement**, and auto-recommends a safe `pass_k` threshold.

### 4.6 Model-Swap Diff (application of bisect)

```
$ drift compare refund_1182 --models gpt-5.2,claude-opus-5,llama-4-70b

  MODEL              TRAJECTORY MATCH   COST/RUN   DETERMINISM
  gpt-5.2 (golden)         100%          $0.0041      0.95
  claude-opus-5             95%          $0.0038      0.90
  llama-4-70b               40%  ⚠        $0.0002      0.55   ← diverges
```

Directly serves the "can we cut model cost without changing behaviour?" decision. Strongly commercially relevant.

---

### 4.7 Invariant Mining ⭐⭐ (the headline)

Recording and replaying agent runs is now done by at least six tools. Mining a
**specification** out of a corpus of approved runs is, as far as three independent
searches could establish, done by none.

The gap is specific and odd: specification mining has been mature in conventional
software for 25 years (Daikon 2001, Perracotta, Javert, DICE), and runtime
verification for agents is an active 2026 research area — but **every agent paper
writes its rules by hand.** Nobody infers them from logged trajectories.

```
$ drift learn

  Mined 15 invariant(s) from 6 approved recording(s):

  ALWAYS lookup_order always runs before issue_refund                6/6  conf 0.98
  ALWAYS issue_refund.args.amount always equals lookup_order.result.total  6/6
  BOUND  a run never makes more than 3 tool calls                    6/6  conf 0.98
  NEVER  web_search is offered but never called                      6/6  conf 0.98
```

**Seven templates** (Perracotta's closed-template approach, which keeps results
explainable and the search bounded): `always_precedes` · `always_called` ·
`never_called` · `always_terminal` · `never_consecutive` · `max_tool_calls` ·
`data_equality`.

**Why only DriftCatch can do this.** Specification mining needs a corpus of runs
known to be correct. Competitors have *recordings*; DriftCatch has **approved**
recordings. The approval gate was built for governance and turns out to be the
precondition for mining. Learning rules from unverified runs would launder a bug
into a specification, so `mine()` ignores anything not golden.

**Four filters against confident nonsense:**

| Filter | Rationale |
|---|---|
| `support >= 5` | two observations is a coincidence |
| `confidence >= 0.95`, as `1 - 0.5**n` | Daikon's statistical justification gate |
| **value must vary across runs** | two fields both `40` in every recording is a *constant*; reporting it as a relationship is the easiest way to generate authoritative nonsense |
| redundancy removal | transitive ordering (`a<b`, `b<c` implies `a<c`) and transitive equality collapsed to a spanning chain; 19 raw candidates become 15 distinct facts |

> The two thresholds must agree or one is dead: `1 - 0.5**4 = 0.9375` fails a 0.95
> gate, so a `min_support` of 4 or less could never produce output. They are pinned
> together at 5.

**Human approval is mandatory, and the evidence says why:** Daikon's own evaluation
found roughly a third of mined invariants were incorrect or irrelevant. Mined rules
are written as `pending` and enforce nothing until `drift learn --approve`.

Enforcement is free: `take.assert_invariants().hold()` checks the current run during
an ordinary replay. The payoff is that a rule guards **every** run, including
scenarios with no hand-written assertion at all.

---

## 5. Data model

### 5.1 Recording (`version: 1`)

Stored at `tests/recordings/<name>.json`. Bodies gzipped; **metadata and trajectory plaintext** so PR diffs stay readable.

```jsonc
{
  "version": 1,
  "name": "refund_1182",
  "status": "approved",              // pending | approved | rejected | stale
  "created_at": "2026-03-02T10:14:00Z",
  "approved_at": "2026-03-02T10:21:00Z",
  "approved_by": "neel",
  "fingerprint": {
    "value": "sha256:a1f3…",
    "model_id": "gpt-5.2-2026-03",
    "prompt_hash": "sha256:9c22…",
    "tool_schema_hash": "sha256:77ab…",
    "matcher_hash": "sha256:0d41…"
  },
  "events": [ /* 5.2 */ ],
  "trajectory": { /* 5.3 */ },
  "meta": {
    "usage": { "input_tokens": 1840, "output_tokens": 220, "cost_usd": 0.0041 },
    "duration_ms": 2310,
    "sdk": { "openai": "2.4.1", "httpx": "0.28.1" },
    "redacted_fields": ["headers.authorization"]
  }
}
```

### 5.2 Event (discriminated union on `type`)

Carries **either** a success payload **or** `error` — never both.

```jsonc
{ "type": "llm", "index": 0,
  "request":  { "model": "gpt-5.2", "messages": [...], "tools": [...], "params": {"temperature": 0} },
  "request_key": "sha256:…",
  "response": { "content": null,
                "tool_calls": [{"name": "lookup_order", "args": {"id": 1182}}],
                "finish_reason": "tool_calls" },
  "stream_chunks": null,
  "usage": { "input_tokens": 910, "output_tokens": 40 } }

{ "type": "tool", "index": 1, "name": "lookup_order",
  "args": {"id": 1182}, "result": {"id": 1182, "total": 40} }

{ "type": "tool", "index": 2, "name": "issue_refund", "args": {"id": 1182},
  "error": { "type": "TimeoutError", "message": "upstream timed out", "status": null } }
```

### 5.3 Trajectory — the behaviour that gets diffed

A typed tree, not a list of names. Also the input to coverage.

```jsonc
{
  "steps": [
    { "kind": "llm",  "index": 0, "decided": ["lookup_order"], "parent": null },
    { "kind": "tool", "index": 1, "name": "lookup_order",
      "args_digest": "sha256:…", "outcome": "ok", "parent": 0 },
    { "kind": "llm",  "index": 2, "decided": ["issue_refund"], "parent": null },
    { "kind": "tool", "index": 3, "name": "issue_refund",
      "args_digest": "sha256:…", "outcome": "error", "parent": 2 }
  ],
  "tools_called": ["lookup_order", "issue_refund"],
  "parallel_groups": [[1]],
  "retries": [3],
  "terminal": "error"
}
```

**Diff tolerance:** `tools` · `tools+order` *(default)* · `tools+order+args` · `exact`.

---

## 6. Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  YOUR AGENT   (LangGraph · CrewAI · OpenAI SDK · Pydantic AI · raw)      │
└──────┬─────────────────────────────────────────────┬───────────────────┘
       │ model calls (HTTP)                          │ tool calls
┌──────▼──────────────────┐                ┌─────────▼─────────────────┐
│ HTTP TRANSPORT HOOK      │                │ TOOL INTERCEPTION         │
│ one httpx hook covers    │                │ 1. @drift.tool decorator  │
│ OpenAI/Anthropic/LiteLLM │                │ 2. MCP transport hook     │
│ /Mistral/Cohere/Google   │                │ 3. framework adapter      │
└──────┬──────────────────┘                └─────────┬─────────────────┘
       └────────────────┬─────────────────────────────┘
            ┌───────────▼───────────┐
            │  SESSION (contextvars) │  per-test · xdist & async safe
            └───────────┬───────────┘
      record │           │ replay │ live
 ┌───────────▼──┐ ┌──────▼──────┐ ┌▼──────────┐
 │ RECORDER      │ │ REPLAYER    │ │ LIVE      │
 │ + REDACTOR    │ │ + MATCHER   │ │ RUNNER    │
 │ (fail closed) │ │ + mutations │ │ (N runs)  │
 └───────────┬──┘ └──────┬──────┘ └┬──────────┘
             └─────► RECORDING FILES ◄────┘
                           │
   ┌────────┬────────┬─────┴────┬─────────┬─────────┬──────────┐
   ▼        ▼        ▼          ▼         ▼         ▼          ▼
COVERAGE  DRIFT   BISECT    WHAT-IF  DETERMIN.  COMPARE   ASSERTIONS
   └────────┴────────┴──────────┴─────────┴─────────┘          │
                           │                                    │
                  ┌────────▼────────┐                  ┌────────▼────────┐
                  │  CLI  (`drift`)  │                  │ pytest plugin   │
                  └────────┬────────┘                  └─────────────────┘
                  ┌────────▼────────────────────┐
                  │  LOCAL UI  (FastAPI + React) │  127.0.0.1 only
                  └─────────────────────────────┘
```

### Interception — honest coverage

| Layer | Mechanism | Coverage | Caveat |
|---|---|---|---|
| Model calls | `Client.send` hook on **every** installed client library | verified against the real OpenAI and Anthropic SDKs, sync + async + streaming | a non-httpx-style SDK needs a shim |
| Python tools | `@drift.tool` | anything, explicitly | one line per tool |
| Framework tools | the decorator covers these today | LangGraph, CrewAI, Pydantic AI | dedicated adapters deferred |
| MCP tools | deferred | — | not in v0 |

> **There is no universal automatic tool capture.** The decorator is the documented default. Claiming otherwise would be dishonest.

#### There is more than one httpx

**The OpenAI SDK uses `httpx`. The Anthropic SDK has moved to `httpx2`** — a separate
distribution with the same API, which rejects an `httpx` client outright. Patching only one
silently misses every SDK built on the other: the calls are never recorded, and a replay test
passes while testing nothing at all.

So `intercept_http()` patches `Client.send` and `AsyncClient.send` on **every** library in
`CLIENT_LIBRARIES` that is installed, and builds each replayed response with *that* library's
`Response` class. Supporting a new client library means adding one name to that tuple.

Hooking `send` rather than a concrete transport is also deliberate: it catches `MockTransport`
and any custom transport, so interception cannot quietly stop working depending on how the
client was constructed.

### Control signals must not be swallowed

`NoMatchError` and `DriftDetected` derive from **`DriftSignal(BaseException)`**, not
`Exception`. Two layers of well-meaning code would otherwise eat them:

- **Provider SDKs.** The Anthropic SDK catches `Exception` around its request path and
  re-raises as `APIConnectionError`, replacing a precise *"the system prompt changed at
  messages[0].content"* with a useless *"Connection error"*.
- **The agent itself.** An agent with `try: ... except Exception: fallback()` would silently
  absorb a replay mismatch. The test would pass and a real regression would ship — a **false
  green**, which is the worst failure a testing tool can produce.

Both were found by testing against the real SDKs, not by review.

### Re-recording never destroys a golden

`drift record --update` used to overwrite the approved recording outright. That is
unacceptable twice over: the replaced behaviour is exactly what you want to diff the new one
against, and a re-record done by mistake has to be recoverable. The previous version is now
archived to `<recordings_dir>/.previous/`, which powers `drift bisect <name> --previous` and
`drift restore <name>`. Archived copies are excluded from listings, coverage and scans.

---

## 7. The matcher

> *"For this request, which recorded event do I serve?"* The matcher is the hard core. If it isn't excellent, nothing else matters.

```
request ──► [ normalizers ] ──► [ strategy ] ──► Match | NoMatch | Ambiguous
             strip noise          pick event
```

**Normalizers (5 registered):** `drop_timestamps` · `mask_uuids` · `normalize_tool_call_ids` *(mandatory — providers emit fresh random ids every request)* · `json_canonical` · `redact_secrets`.

**Strategies:**

| Strategy | Matches on | Weakness |
|---|---|---|
| `field` *(default)* | hash of `model`+`messages`+`tools` post-normalization | must declare the noise |
| `positional` | Nth call → Nth event | breaks when steps are added/removed |
| `tool_call` | tool name + args digest | — |
| `semantic` | embedding nearest ≥ threshold | can mis-match |
| `chain(...)` | compose: narrow then verify | — |
| custom | `match_fn(request, candidates, state)` | user-owned risk |

**Resolution:**
```
normalize → look up
  ├─ one candidate            → Match(exact | normalized | fuzzy@x)
  ├─ identical repeats        → call-counter: Nth occurrence → Nth event
  ├─ parallel/unordered       → pool: consume by content key
  ├─ none                     → FIRST DIVERGENCE: report & stop
  └─ ambiguous                → fail loudly, tighten the matcher
```

**Invariants:** matching is sequential and **fail-fast** — one change yields **one** error, never a cascade. A `NoMatch` never falls through to a live call. Low confidence fails under `strict` (default).

**Safety rails:** confidence on every match · strict default · `drift audit` shows how every request matched, so a too-loose matcher is visible *before* it's trusted.

---

## 8. Approval gate & lifecycle

```
record ──► PENDING ──► human reviews ──► APPROVED (golden)
              │             └── wrong ──► REJECTED → fix the agent
              │
   fingerprint changes ──► STALE ──► live check ──► re-approve
```

| Status | Meaning |
|---|---|
| `pending` | recorded, **never auto-trusted** |
| `approved` | human-blessed golden — the verified answer |
| `rejected` | the agent was wrong; fix the agent, not the recording |
| `stale` | inputs changed; replay runs, behaviour unverified |

Recordings are **committed files** → every behaviour change is a **readable PR diff**. Approval and code review become one step.

---

## 9. Security: redaction (default-on, fails closed)

Recordings contain full prompts and responses and live in git. A go/no-go concern for any company.

- Redaction runs **at record time, before anything touches disk**.
- **Deny-list:** `authorization`, `api-key`, `x-api-key`, `openai-organization`, `cookie`, `set-cookie`.
- **Pattern scan:** `sk-…`, `AKIA…`, `ghp_…`, bearer tokens, long base64 blobs.
- **Fails closed:** if a key pattern survives, **refuse to write** and name the field.
- `drift scan` audits existing recordings (pre-commit friendly, non-zero exit on findings).
- PII: documented policy + `custom_redactors`. Recordings are presumed to contain user data.
- **UI binds `127.0.0.1` only**, never `0.0.0.0`.

---

## 10. The local UI

`drift ui` launches a local web app (FastAPI + React, assets shipped in the wheel). This is what makes the dataset *legible* — the analytical features above are much weaker as CLI text alone.

### Screens

| Screen | Contents |
|---|---|
| **Dashboard** | behaviour coverage %, recording statuses, drift warnings, determinism scores, recent changes |
| **Recordings** | searchable list; filter by status; bulk approve |
| **Recording detail** | trajectory graph (steps as a flow), per-event prompt/response viewer, usage & cost, fingerprint panel, **Approve / Reject** |
| **Diff** | golden vs current side-by-side — trajectory diff on top, request/response diff below |
| **Coverage** | tools & decision paths covered vs never-tested, drill-down to the recording, **"record this scenario"** hints from `drift suggest` |
| **Config editor** | edit matcher/normalizers/policies with schema validation and a **live preview** of how a change affects matching across existing recordings |
| **Match audit** | every request and its confidence — visually tune a matcher instead of guessing |
| **What-if** | pick an event, inject a mutation, run, see the resulting trajectory |

### Rules

- **Read + write local files only.** No account, no cloud, no telemetry.
- Bind `127.0.0.1`; refuse `--host 0.0.0.0` without an explicit `--i-know-what-im-doing`.
- The UI is a **view over the same engine** the CLI uses — no logic lives only in the frontend.
- Ships pre-built; `node` is **not** required to install or use DriftCatch.
- Degrades gracefully: every UI action has a CLI equivalent.

### Stack

FastAPI (served by `drift ui`) · React + Vite + Tailwind · built to `driftcatch/ui/static/`, committed and packaged. *Fallback if Node maintenance proves too costly: Jinja + HTMX, same routes.*

---

## 10b. The agent surface

Coding agents are a first-class caller, not an afterthought: increasingly it is
Cursor or Claude Code running the command, not a person reading a table. Four
things follow from that.

**Machine-readable output.** Every read command takes `--json` (one uniform flag,
never a mix of spellings) and emits a stable envelope `{ok, command, ...}` on
stdout with no ANSI. Diagnostics go to stderr, so stdout is a clean contract.
Errors are JSON too, with a `hint` naming the next command — an agent should never
parse a traceback to learn that a recording is missing.

**A documented exit-code taxonomy**, so an agent can branch before reading anything:
`0` ok · `1` error · `2` usage · `3` regression · `4` gate failed · `5` secrets
found · `6` not found · `7` not approved.

**`drift explain`** is the single discovery entrypoint: purpose, what the tool is
*not* for, key facts, per-command metadata (including which commands mutate), the
exit codes, and the correct workflows. One call replaces reading documentation.

**`drift init` writes `AGENTS.md`**, which Cursor, Claude Code, Codex, Aider and
others read automatically. It appends rather than overwrites and is idempotent.

### The safety boundary

The manifest and the MCP server both carry the same warnings, because an agent gets
three things wrong by default:

1. it reaches for this as a production cache — it is a test tool and errors on any
   request it has not recorded;
2. it assumes replay can tell it what the model would do today — the model is not
   running;
3. **on a red test, it tries to make the test green.**

That third one is the dangerous one. In DriftCatch the green button is `approve`,
and approving means *a human confirmed this behaviour is correct*. So the MCP server
exposes reading and analysis only: `approve`, `reject`, `record`, `restore` and live
runs are **absent by construction**, and a test asserts their absence so a future
convenience commit cannot reintroduce them. `pending_approvals` lets the agent
surface what needs a decision, with the command the human should run, without being
able to run it.

`learn` is the one non-read tool, and it is safe because mining can only ever
propose: rules are written `pending` and enforce nothing.

### MCP

`drift mcp` runs an MCP server over stdio with ten tools, each annotated
`read_only_hint` where applicable so a client knows what is safe to call
unattended. Targets the MCP Python SDK **v2**, where `FastMCP` was renamed
`MCPServer`. See `docs/MCP.md`.

---

## 11. Public API

```python
import driftcatch as drift

# ── Tier 1: replay test (free, deterministic, every commit) ──────────────
def test_refund_uses_database():
    with drift.take("refund_1182") as take:        # record if missing, else replay
        result = refund_agent("refund order 1182")

    take.assert_tools().called("lookup_order").never("web_search")
    take.assert_trajectory().matches_golden(tolerance="tools+order")
    take.assert_cost().under(tokens=8_000)
    assert "Refunded $40" in result


# ── Tool interception (documented default) ───────────────────────────────
@drift.tool
def lookup_order(order_id: int) -> dict: ...


# ── What-if: close a coverage gap without re-recording ───────────────────
def test_order_not_found_escalates():
    with drift.take("refund_1182").what_if(
            tool="lookup_order", returns={"error": "not_found"}) as take:
        refund_agent("refund order 1182")
    take.assert_tools().called("escalate_to_human")


# ── Tier 2: live behaviour check (real spend, deliberate) ────────────────
@drift.live_check("refund_1182", runs=10)
def test_refund_behaviour_live():
    with drift.take("refund_1182", mode="live") as take:
        refund_agent("refund order 1182")
    take.assert_trajectory().matches_golden(pass_k=(8, 10))


# ── Config: never required, always available ─────────────────────────────
drift.config(
    recordings_dir = "tests/recordings",
    matcher        = "field",
    normalizers    = ["drop_timestamps", "mask_uuids", "normalize_tool_call_ids"],
    strict         = True,
    drift_policy   = "warn",
    record         = ["llm.*", "tools.*"],
    passthrough    = ["tools.get_time"],
    redactors      = ["default"],
)
```

---

## 12. CLI

```
drift record [<name>] [--update]        # (re)record → PENDING
drift review [<name>] [--yes]           # inspect, approve / reject
drift replay                            # deterministic replay
drift diff   [<name>]                   # behaviour diff vs golden
drift status                            # all recordings: status + drift
drift coverage [--fail-under N]         # ⭐ behaviour coverage (+ CI gate)
drift suggest                           # propose scenarios for uncovered paths
drift bisect <name>                     # attribute a behaviour change
drift what-if <name> --fail-tool X      # counterfactual replay
drift determinism <name> -n 20          # flakiness measurement
drift compare <name> --models a,b,c     # model-swap behaviour diff
drift check --live <name> [-n 10]       # Tier 2 live check
drift audit                             # how every request matched
drift scan                              # secret / PII audit
drift ui [--port 7777]                  # local web UI
```

---

## 13. Hard cases → mechanisms

| # | Case | Mechanism |
|---|---|---|
| 1 | Same request, different answers (retry, `pending`→`done`) | **Call-counter** — Nth occurrence → Nth response |
| 2 | Variable call count (loops) | Content matching + counter; **first-divergence** keeps errors readable |
| 3 | Parametrized inputs | `recording_per_param` (default) or placeholder recording |
| 4 | Parallel / concurrent calls | **Pool match** (order-insensitive) + `parallel_groups` |
| 5 | Streaming (SSE) | **Buffer-capture, replay-in-order**; `stream_timing: instant \| recorded` |
| 6 | Noise vs meaningful | **Typed placeholders** + **value propagation**; framework detects variance and *suggests*, user approves |
| 7 | Errors / exceptions | Events carry `error`; replay **re-raises** so retry paths execute |
| 8 | Parallel test workers | Per-test `contextvars` session + atomic writes |

---

## 14. Scope

**In for v0.1–v0.5:** Python 3.11+ · httpx interception · recording v1 + fingerprint · matcher (normalizers + `field`/`positional`/`tool_call`/`chain`) · recorder with fail-closed redaction · replayer (counter, pool, first-divergence, error re-raise) · `@drift.tool` · trajectory + diff · assertions · pytest plugin · approval gate · CLI · **coverage** · **drift detection** · **what-if** · **determinism** · **bisect** · local UI.

**Out of v0 (post-traction):** OpenTelemetry export · TypeScript/JS port · cut-point replay · semantic matcher · hosted dashboard/team features · any commercial tier.

---

## 15. Quality bar

| Area | Standard |
|---|---|
| Python | 3.11+ |
| Packaging | `hatchling` + `pyproject.toml` (uv-compatible) |
| Lint/format | `ruff`, line length 100 |
| Typing | `mypy --strict` on `driftcatch/`; pydantic v2 at boundaries |
| Tests | `pytest`; **no network in unit tests**; xdist-parallel in CI from day one |
| Priority | the matcher gets adversarial tests before anything else ships |
| README | answers *"why not vcrpy?"* above the fold; leads with the coverage badge |

---

## 16. Why users will actually choose this

1. **A number they don't have.** Behaviour coverage is new, actionable, and badge-able.
2. **It answers "why," not just "what."** Bisect attributes root cause; nothing else does.
3. **It closes its own gaps.** Coverage finds the hole; what-if fills it without re-recording.
4. **It tells the truth about flakiness.** A determinism score instead of a shrug.
5. **It never lies about staleness.** The central criticism of replay testing, handled head-on.
6. **It's legible.** The UI makes a corpus of recordings something you can actually see.
7. **It's honest.** Two tiers with stated limits earns the trust a testing tool lives on.

---

*DriftCatch — the framework detects change and explains it; the human decides.*
