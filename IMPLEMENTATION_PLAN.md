# DriftCatch — Implementation Plan

> Mechanical, phase-ordered build plan. Every task states **what to create**, **the exact interface**, and **how to verify it's done**.
> **Do not start a phase until the previous phase's acceptance criteria all pass.**

Read [`DESIGN.md`](DESIGN.md) first. This document specifies *what* and *how*, not *why*.

## Progress

**222 tests passing** (serial and under `pytest -n 4`) · `mypy --strict` clean · `ruff`
clean · wheel builds · **verified against the real OpenAI and Anthropic SDKs** (sync,
async, streaming, HTTP errors, tool-call ids) with no API keys and no network.

37 source modules · 16 test files · 21 CLI commands · 10 MCP tools · 7 invariant templates.

| Phase | Name | Status |
|---|---|---|
| 0 | Scaffold | ✅ **DONE** |
| 1 | Recording schema, IO, fingerprint | ✅ **DONE** |
| 2 | Session, config, redaction | ✅ **DONE** |
| 3 | **Matcher** ⭐ | ✅ **DONE** (26 adversarial tests) |
| 4 | HTTP interception + Recorder | ✅ **DONE** |
| 5 | Replayer | ✅ **DONE** |
| 6 | Tools + Trajectory | ✅ **DONE** |
| 7 | Assertions + pytest plugin | ✅ **DONE** |
| 8 | Approval gate + CLI | ✅ **DONE** (16 commands) |
| 9 | Drift detection | ✅ **DONE** |
| 10 | **Behaviour coverage** ⭐ | ✅ **DONE** (+ badge, `--fail-under`) |
| 11 | What-if mutation | ✅ **DONE** |
| 12 | Determinism + Tier 2 live | ✅ **DONE** (`run_live`, scoring, guards) |
| 13 | Bisect + attribution | ✅ **DONE** (model compare: deferred) |
| 14 | Local UI | ✅ **DONE** (FastAPI, loopback-only, no Node) |
| 15 | Docs, demo, release | 🟨 docs + demos done; **release pending** |
| 16 | **Invariant mining** ⭐⭐ | ✅ **DONE** (7 templates, 4 anti-spurious filters) |
| 17 | **Agent surface** | ✅ **DONE** (`--json`, exit codes, `explain`, `AGENTS.md`) |
| 18 | **MCP server** | ✅ **DONE** (10 tools, mutating tools withheld by design) |

### Design changes made during implementation

Each of these came out of hitting a real problem while building, and each is a deliberate
deviation from the plan as written:

| Change | Why |
|---|---|
| Events store `raw_response` (verbatim provider body) **as well as** the normalised `response` | Rebuilding a provider-shaped body from a normalised view breaks on every SDK quirk. Replay returns the recorded bytes, so any SDK parses exactly what it parsed when recording. |
| Interception hooks **`httpx.Client.send`**, not `HTTPTransport.handle_request` | Transport-level patching misses `MockTransport` and any custom transport. Hooking `send` is transport-agnostic — and it means the test suite exercises the same code path users do. |
| **Consumption replaces the call-counter** as the repeated-request mechanism | A served event is consumed, so the second identical request naturally receives the second recorded reply. One rule covers retries and `pending → done` polling with no special cases. |
| Fingerprint inputs are **derived automatically** from the first model call | Asking users to declare their system prompt and tool schemas is an adoption tax, and a declaration that drifts from reality is worse than none. |
| Drift check runs **before** matching, not after the run | Lets a failed match say "the system prompt changed" instead of "nothing matched". Turns a confusing error into an actionable one. |
| `what_if_unmatched` policy added (`execute` / `stub` / `error`) | A counterfactual sends the agent down a branch that was never recorded, so strict matching past the mutation point would make what-if useless. Found by a failing test. |
| UI is **FastAPI + one HTML template**, not React/Vite | No Node toolchain at install time, no compiled bundle in the wheel, and nothing to go stale. Upgradeable later without changing the routes. |
| Coverage denominator derived from **declared tool schemas** | The recorded request already lists every tool the agent was offered, so coverage knows what *could* have happened, not merely what did. No configuration needed. |
| Bisect **attributes by comparison** rather than reverting inputs live | Comparison needs no API spend and is certain when exactly one input moved. Proving causation by one-at-a-time reverting is a larger feature and is not claimed. |

### Bugs found by testing against real SDKs

These shipped in an earlier version of this framework. All three would have produced
**passing tests that verified nothing**, and none was caught by review — only by driving the
genuine SDKs.

| # | Bug | Impact | Fix |
|---|---|---|---|
| 1 | **Anthropic SDK uses `httpx2`, not `httpx`** | Interception missed every Anthropic call entirely. Recordings came out empty and replay tests passed while testing nothing. | Patch `Client.send` on every installed client library; build each replayed response with that library's `Response`. |
| 2 | **SDKs swallow our control signals** | Anthropic wrapped `NoMatchError` in `APIConnectionError`, destroying the divergence message. Worse, any agent with `except Exception:` would absorb a replay mismatch and the regression would ship. | `NoMatchError` and `DriftDetected` now derive from `DriftSignal(BaseException)`. |
| 3 | **Re-recording destroyed the golden** | `drift record --update` overwrote the approved recording, so a mistaken re-record was unrecoverable and there was nothing left to bisect against. | Archive to `.previous/`; add `drift bisect --previous` and `drift restore`. |

Additional hardening in the same pass: a warning when replaying an unapproved recording
(silence there recreates the problem the approval gate solves); session-level caching of
normalized payloads (matching was O(n²) normalizations per run); async tool capture tested;
nested-session patch reference-counting tested; `drift init` added for first-run DX; the
vestigial `request_key` field removed.

### Later additions and what they cost

| Added | Why it earned its place |
|---|---|
| **Invariant mining** (`drift learn`) | Research confirmed record/replay + trajectory diff is done by 6+ tools and is no longer a differentiator. Mining a specification from an *approved* corpus was unclaimed on three independent searches. Only possible because the approval gate already existed. |
| **Agent surface** (`--json`, exit codes, `explain`, `AGENTS.md`) | Coding agents are increasingly the caller. Converged conventions: one uniform `--json`, clean stdout, JSON errors with a `hint`, documented exit codes, a self-describing manifest. |
| **MCP server** (`drift mcp`) | Typed tools for Cursor / Claude Code. Deliberately omits `approve`, `reject`, `record`, `restore` and live runs — an agent's reflex on a red test is to make it green, and the green button here is the human approval gate. A test asserts their absence. |

### Bugs and inconsistencies caught after the first build

| Issue | Impact | Fix |
|---|---|---|
| `DEFAULT_MIN_SUPPORT = 3` with a 0.95 confidence gate | Dead constant: `1 - 0.5**4 = 0.9375` can never clear 0.95, so mining with 4 recordings silently returned nothing while claiming a minimum of 3. | Pinned both to 5, with a comment that the thresholds must agree. |
| 19 mined rules, several implied by others | A list a human will not read is a list nobody enforces. | Daikon-style redundancy filtering: transitive ordering and transitive equality. 19 → 15 distinct facts. |
| `ruff format` emitted Python 3.12-only f-string syntax | Would have broken the CI matrix on 3.11. | Extracted to a variable with a comment. |
| README documented `drift determinism` and `drift compare --models` | Commands that do not exist. | Corrected the README; added an automated README-vs-CLI consistency check. |
| `regex_replace` documented as a normalizer | Never registered; 5 exist, not 6. | Corrected the docs. |
| MCP test harness assumed a tuple return | `call_tool` returns `CallToolResult.structured_content`. | Fixed the harness; the safety-boundary tests passed throughout. |

### Deferred (explicitly out of v0)

`drift compare --models` (cross-model behaviour table) · `drift strength` (assertion-strength scoring by mutating recordings) · cut-point/partial replay · semantic matcher · MCP *transport* hook for capturing MCP tool calls (distinct from the MCP *server*, which is built) · seeded fault injection · OpenTelemetry export · TypeScript port · dedicated LangGraph/CrewAI adapters (the `@drift.tool` decorator covers these today).

---

## Ground rules (every phase)

| Rule | Detail |
|---|---|
| Python | 3.11+ |
| Packaging | `hatchling`; dev install `pip install -e ".[dev]"` |
| Lint/format | `ruff check` + `ruff format --check` must pass |
| Typing | `mypy --strict driftcatch` must pass |
| Module header | every module starts `from __future__ import annotations` |
| Boundaries | pydantic v2 models for anything serialized or user-facing |
| Unit tests | **never touch the network**; HTTP uses a stub transport |
| State | **never module-level globals** for session state — always `contextvars` |
| Errors | raise `DriftCatchError` subclasses with actionable messages |
| Naming | the package/CLI name lives **only** in `driftcatch/_name.py` |

### Verification commands

```powershell
.\.venv\Scripts\python.exe -m pytest -n 4
.\.venv\Scripts\python.exe -m ruff check driftcatch tests
.\.venv\Scripts\python.exe -m ruff format --check driftcatch tests
.\.venv\Scripts\python.exe -m mypy driftcatch
.\.venv\Scripts\python.exe -m driftcatch.cli version
```

> On Windows the generated `drift.exe` shim may be blocked; `python -m driftcatch.cli` is the
> reliable equivalent during development.

### Target layout

```
driftcatch/
├── __init__.py          ✅ public API re-exports
├── _name.py             ✅ NAME, CLI_NAME, RECORDING_VERSION
├── errors.py            ✅ exception hierarchy
├── config.py            ⬜ Config model + resolution
├── session.py           ⬜ per-test Session, contextvars
├── redact.py            ⬜ redaction (fail-closed)
├── recording/
│   ├── schema.py        ✅ Recording, Event, Trajectory, Fingerprint
│   ├── io.py            ✅ load / save (atomic, gzipped bodies)
│   └── fingerprint.py   ✅ compute + compare
├── match/
│   ├── base.py          ⬜ Matcher protocol, Match/NoMatch, MatchState
│   ├── registry.py      ⬜ name -> matcher / normalizer
│   ├── normalizers.py   ⬜ built-in normalizers
│   ├── strategies.py    ⬜ field, positional, tool_call, chain
│   └── resolve.py       ⬜ resolution + first-divergence
├── record.py            ⬜ Recorder
├── replay.py            ⬜ Replayer
├── intercept/
│   ├── http.py          ⬜ httpx transport hook
│   ├── tools.py         ⬜ @tool decorator
│   └── mcp.py           ⬜ MCP transport hook
├── trajectory.py        ⬜ build + diff
├── take.py              ⬜ Take object + assertions
├── review.py            ⬜ approval state machine
├── coverage.py          ⬜ behaviour coverage
├── whatif.py            ⬜ mutations
├── determinism.py       ⬜ variance measurement
├── bisect.py            ⬜ root-cause attribution
├── cli.py               ✅ CLI (version, status so far)
├── pytest_plugin.py     ✅ options registered
└── ui/                  ⬜ FastAPI app + built static assets
```

---

## ✅ PHASE 0 — Scaffold (DONE)

Delivered: `pyproject.toml` (hatchling, deps, `drift` script, `pytest11` entry point, ruff/mypy/pytest config) · `_name.py` · `errors.py` · `__init__.py` · minimal `cli.py` (`version`, `status`) · `pytest_plugin.py` with `--drift-mode` / `--drift-recordings-dir` · `.gitignore` · `README.md`.

**Verified:** ruff clean · `mypy --strict` clean (9 files) · `drift version` prints · pytest loads the plugin.

---

## ✅ PHASE 1 — Recording schema, IO, fingerprint (DONE)

Delivered:
- `recording/schema.py` — `Outcome`, `Status`, `ErrorInfo`, `Usage`, `LLMRequest/Response`, `LLMEvent`, `ToolEvent`, discriminated `Event` union, `Step`, `Trajectory`, `Fingerprint`, `RecordingMeta`, `Recording`. Validator rejects an event carrying both a success payload and an error.
- `recording/io.py` — `recording_path`, `save_recording` (atomic via temp + fsync + `os.replace`), `load_recording` (version-gated), `list_recordings` (skips unparseable files).
- `recording/fingerprint.py` — `canonical`, `hash_str`, `compute_fingerprint`, `compare`.

**Verified:** 30 tests pass (also under `pytest -n 4`) · `mypy --strict` clean · `scripts/smoke_roundtrip.py` writes, reloads, and detects drift attributed to `prompt_hash` with no false positives · metadata plaintext / prompt bodies compressed, both asserted.

---

## PHASE 2 — Session, config, redaction

**Goal:** parallel-safe per-test state, and fail-closed redaction. Pure logic.

**2.1 `config.py`**
```python
class Config(BaseModel):
    recordings_dir: Path = Path("tests/recordings")
    matcher: str = "field"
    normalizers: list[str] = ["drop_timestamps", "mask_uuids", "normalize_tool_call_ids"]
    strict: bool = True
    drift_policy: Literal["warn","fail","ignore"] = "warn"
    record: list[str] = ["llm.*", "tools.*"]
    passthrough: list[str] = []
    redactors: list[str] = ["default"]
    stream_timing: Literal["instant","recorded"] = "instant"
    max_recording_bytes: int = 2_000_000

def config(**kwargs: Any) -> None
def get_config() -> Config
```
Resolution order: `take()` kwargs > `drift.config()` > `pyproject.toml [tool.driftcatch]` > defaults.

**2.2 `session.py`** — the parallel-safety core
```python
@dataclass
class Session:
    name: str
    mode: Literal["record","replay","live"]
    config: Config
    recording: Recording
    events: list[Event]
    match_state: MatchState
    next_index: int = 0

_current: ContextVar[Session | None] = ContextVar("driftcatch_session", default=None)
def current_session() -> Session | None
@contextmanager
def use_session(s: Session) -> Iterator[Session]
```
**Rule:** no other module holds session state. Interceptors call `current_session()`; `None` ⇒ pass through untouched (zero production overhead).

**2.3 `redact.py`**
```python
DENY_HEADERS = {"authorization","api-key","x-api-key","openai-organization","cookie","set-cookie"}
KEY_PATTERNS = [r"sk-[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{16}", r"ghp_[A-Za-z0-9]{20,}", ...]

def redact(obj: Any, redactors: list[str]) -> tuple[Any, list[str]]
def assert_clean(obj: Any) -> None      # raises RedactionError
```
Redaction runs inside the recorder **before** serialization; `assert_clean` runs immediately before `save_recording`. On failure **no file is written** and the error names the field.

### Tests
- two threads and two asyncio tasks get isolated sessions (no bleed)
- redaction strips deny-listed headers and returns their field paths
- a surviving `sk-…` raises `RedactionError` **and no file exists afterwards**
- config resolution order honoured

### ✅ Acceptance
`pytest -n 4` green · a secret can never reach disk.

---

## PHASE 3 — The matcher ⭐ (the critical path)

**Goal:** given a request and candidate events, pick the right event or fail loudly.
**This module is the project.** Over-test it. It needs no network, so build it fully offline.

**3.1 `match/base.py`**
```python
class Confidence(StrEnum): EXACT="exact"; NORMALIZED="normalized"; FUZZY="fuzzy"

@dataclass
class Match:   event: Event; confidence: Confidence; score: float = 1.0
@dataclass
class NoMatch: reason: str; diverged_at: int | None = None; diff: str | None = None
MatchResult = Match | NoMatch

@dataclass
class MatchState:
    consumed: set[int] = field(default_factory=set)
    counters: dict[str, int] = field(default_factory=dict)

class Matcher(Protocol):
    name: str
    def match(self, request, candidates: Sequence[Event], state: MatchState) -> MatchResult: ...
```

**3.2 `match/registry.py`** — `matcher(name)` / `normalizer(name)` decorators, `get_matcher` / `get_normalizer`.

**3.3 `match/normalizers.py`** — each `(payload: dict) -> dict`, pure, no input mutation:

| Name | Behaviour |
|---|---|
| `drop_timestamps` | remove keys matching `created\|timestamp\|time\|date` |
| `mask_uuids` | UUID-shaped strings → `"<uuid>"` |
| `normalize_tool_call_ids` | tool-call `id` → positional `"<tc:0>"` — **mandatory** |
| `json_canonical` | recursively sort keys |
| `regex_replace` | parameterized `(pattern, replacement)` |
| `redact_secrets` | reuse `redact.redact` |

**3.4 `match/strategies.py`** — `field` (default), `positional`, `tool_call`, `chain(*names)`.
Required: **call-counter** (identical requests → Nth occurrence → Nth event) · **pool** (served index enters `state.consumed`) · **ambiguity** → `AmbiguousMatchError`.

**3.5 `match/resolve.py`**
```python
def resolve(request, candidates, state, config) -> Match   # raises NoMatchError
```
Apply normalizers in order → delegate to the strategy → on `NoMatch` build a **first-divergence report** (event index, which part differed, short unified diff) and raise. `strict` + `FUZZY` ⇒ raise.

### Tests — the priority suite
1. identical request → `EXACT`
2. differs only by timestamp → `NORMALIZED`
3. differs only by `tool_call_id` → matches (proves the mandatory normalizer)
4. **same request twice, different responses → 1st→A, 2nd→B**
5. **parallel: two tool calls in reversed order → both match**
6. loop: 5 requests vs 3 recorded → **exactly one** error at the first divergence
7. changed system prompt → `NoMatch` naming `system prompt`
8. ambiguous candidates → `AmbiguousMatchError`
9. `strict=True` + fuzzy raises; `strict=False` matches
10. `chain("positional","field")` falls through correctly
11. a custom registered matcher is selectable by name

### ✅ Acceptance
All 11 green · **one input change produces exactly one error, never a cascade.**

---

## PHASE 4 — HTTP interception + Recorder

**4.1 `intercept/http.py`** — `UnderstudyTransport`-style `httpx.BaseTransport` wrapper plus an async variant; `@contextmanager intercept_http()` patches the default transport for the session's duration.
Flow: `current_session()` is `None` → delegate untouched · parse the body into `LLMRequest` via a per-provider body parser (the *only* provider-specific code; one dict-shaped function each, default passthrough) · **record** → call inner, capture, redact, append `LLMEvent` · **replay** → Phase 5.
**Streaming:** `text/event-stream` ⇒ buffer chunks in order into `stream_chunks`, assemble the final response, return an equivalent stream to the caller.

**4.2 `record.py`**
```python
class Recorder:
    def add_llm(self, req, resp, err, usage, chunks) -> None
    def add_tool(self, name, args, result, err) -> None
    def finalize(self) -> Recording
```
`finalize`: assign sequential indices · compute `request_key` through the normalizer pipeline · build the `Trajectory` · compute the `Fingerprint` · `assert_clean` · warn above `max_recording_bytes` · set `status=PENDING`.

**4.3 `take.py` (first cut)** — `@contextmanager take(name, *, mode=None, **overrides) -> Iterator[Take]`.
Mode: explicit > `$DRIFTCATCH_MODE` > (recording missing ⇒ `record`, else `replay`).

### Tests (`tests/integration/`)
stub transport returns a canned OpenAI-shaped reply → 1-event PENDING recording · streaming stub preserves chunk order both ways · HTTP 500 recorded as `error` · a header secret is absent from the written file.

### ✅ Acceptance
Recording against a stub transport writes a valid, loadable, secret-free PENDING recording.

---

## PHASE 5 — Replayer

**5.1 `replay.py`**
```python
class Replayer:
    def llm(self, req: LLMRequest) -> LLMResponse    # raises NoMatchError
    def tool(self, name: str, args: dict) -> Any     # re-raises recorded errors
```
Resolve against `session.recording.events` · **re-raise recorded errors** so retry/except paths execute · never perform network I/O · never fall through on `NoMatch`.

**5.2** Wire the replay branch of `intercept/http.py` — rebuild an `httpx.Response` from the matched event, including a synthetic SSE stream honouring `stream_timing`.

### Tests
replay returns the recorded response with **zero** transport calls (assert the stub was never invoked) · a recorded `TimeoutError` is re-raised and the agent's retry loop runs · changed prompt → exactly one `NoMatchError`.

### ✅ Acceptance
Replay runs fully offline, deterministically, <50 ms for a 5-event recording.

---

## PHASE 6 — Tools + Trajectory

**6.1 `intercept/tools.py`** — `@tool` decorator, `functools.wraps`-preserving, sync **and** async, pass-through when no session.

**6.2 `trajectory.py`**
```python
def build(events: Sequence[Event]) -> Trajectory
def diff(golden: Trajectory, current: Trajectory, tolerance: str) -> TrajectoryDiff
def render(d: TrajectoryDiff) -> str
```
Tolerances: `tools` · `tools+order` (default) · `tools+order+args` · `exact`.
`build` fills `decided` from `response.tool_calls`, links `parent`, populates `parallel_groups` (tool events between the same pair of llm events) and `retries`.

`render` must produce exactly this shape — it is the product's signature moment:
```
  golden:   lookup_order → issue_refund
  current:  web_search   → issue_refund
            ^^^^^^^^^^^
  ✗ expected "lookup_order", never called
  ✗ unexpected "web_search"
```

### Tests
`@tool` records args/result; replay serves them **without executing the body** (side-effect counter) · async tool works · no session ⇒ normal execution · each tolerance level passes/fails on a purpose-built pair · `render` snapshot matches exactly.

---

## PHASE 7 — Assertions + pytest plugin

**7.1 `take.py` (complete)**
```python
class Take:
    name: str; mode: str; recording: Recording
    events: list[Event]; trajectory: Trajectory; usage: Usage
    stale: bool; changed_inputs: list[str]
    def assert_tools(self) -> ToolAssertion
    def assert_trajectory(self) -> TrajectoryAssertion
    def assert_cost(self) -> CostAssertion
```
`ToolAssertion`: `called`, `never`, `called_in_order`, `call_count`.
`TrajectoryAssertion`: `matches_golden(tolerance=..., pass_k=None)`.
`CostAssertion`: `under(tokens=..., usd=...)`.
Failures raise `AssertionError` **with the rendered diff**, never a bare `False`.

**7.2 `pytest_plugin.py` (complete)** — honour `--drift-mode` / `--drift-recordings-dir`, add a `drift` fixture, a `@pytest.mark.drift(recording=...)` marker, a terminal summary (replayed / recorded / stale counts), and stay xdist-safe.

### ✅ Acceptance
The `DESIGN.md` §11 Tier-1 example runs verbatim and passes.

---

## PHASE 8 — Approval gate + CLI

**8.1 `review.py`** — `approve`, `reject`, `mark_stale`, `require_approved`.
Legal transitions: `PENDING→APPROVED|REJECTED`, `STALE→APPROVED`, `APPROVED→STALE`; anything else raises `InvalidTransition`. Replaying a non-approved recording warns by default, can be configured to fail.

**8.2 `cli.py`** — add `record`, `review` (interactive + `--yes`), `replay`, `diff`, `audit`, `scan`. `scan` exits non-zero on findings (pre-commit friendly).

### ✅ Acceptance
Full lifecycle via CLI only: `record` → `status` (pending) → `review` → `status` (approved) → `replay` green.

---

## PHASE 9 — Drift detection

Recompute the fingerprint at session start; on mismatch apply `drift_policy` (`warn` prints a rich panel naming the changed component, `fail` raises, `ignore` silent) and set `Take.stale` / `Take.changed_inputs`. `drift status` shows a fingerprint column.

**Tests:** changed prompt ⇒ stale + `["prompt_hash"]` · `fail` raises · `warn` passes and prints · unchanged ⇒ no false positive.

---

## PHASE 10 — Behaviour coverage ⭐

**`coverage.py`**
```python
class CoverageReport(BaseModel):
    percent: float
    tools: dict[str, ToolCoverage]        # covered, calls, error_path_covered
    paths: list[PathCoverage]             # decision paths seen vs possible
    uncovered: list[str]
def analyse(recordings: list[Recording], known_tools: list[str]) -> CoverageReport
def render(report: CoverageReport) -> str
```
Measures: tools invoked · tool error paths · decision branches · multi-tool sequences · retry paths · terminal outcomes.
CLI: `drift coverage [--fail-under N]` (CI gate) and `drift suggest` (propose scenarios for uncovered paths).

**Tests:** a corpus with 2 of 4 tools covered ⇒ expected percent · a tool with only successes ⇒ `error_path_covered=False` · `--fail-under` exits non-zero appropriately · `render` snapshot.

**Ship with:** a README badge generator (`drift coverage --badge`). This is the adoption lever.

---

## PHASE 11 — What-if mutation

**`whatif.py`** — `Take.what_if(tool=..., returns=...)` plus CLI mutations `--fail-tool`, `--empty-result`, `--null-field`, `--slow-tool`, `--truncate-response`. A mutation rewrites one event in memory, then replays; the recording on disk is never modified.

**Tests:** `--fail-tool` drives the agent down its error branch · the on-disk recording is byte-identical afterwards · a mutation on an unknown tool raises a clear error.

---

## PHASE 12 — Determinism + Tier 2 live

`@drift.live_check(name, runs=N)` and `mode="live"`: real calls, N runs, N trajectories; `matches_golden(pass_k=(k,n))`; print a distribution summary.
`determinism.py`: group trajectories, compute the score, recommend a `pass_k`.
**Guardrails:** live mode requires an explicit flag/env, prints estimated spend, and refuses to run in CI without `DRIFTCATCH_ALLOW_LIVE=1`.

**Tests:** scripted varying responses ⇒ `pass_k` arithmetic exact at k-1 / k / k+1 · live refused in CI without the env var · **a prompt change is caught by Tier 2 and not falsely reported by Tier 1.**

---

## PHASE 13 — Bisect + model compare

`bisect.py`: given a behaviour change, revert one fingerprint component at a time, re-run live, and report which revert restores the golden behaviour; render a prompt word-diff when the prompt is the cause.
`drift compare <name> --models a,b,c`: run the same scenario per model, tabulate trajectory match / cost / determinism.

**Tests:** a scripted provider where only the prompt matters ⇒ bisect names `prompt_hash` · compare tabulates correctly.

---

## PHASE 14 — Local UI

Backend: FastAPI mounted by `drift ui`, **binding `127.0.0.1` only** (refuse `0.0.0.0` without `--i-know-what-im-doing`). Routes are thin wrappers over the same engine the CLI uses — **no logic lives only in the UI.**
Frontend: React + Vite + Tailwind, built to `driftcatch/ui/static/` and committed, so **`node` is not required to install or use DriftCatch**.

Screens: Dashboard · Recordings list · Recording detail (trajectory graph, event viewer, approve/reject) · Diff · **Coverage** · Config editor (schema-validated, with a live preview of how a matcher change affects existing recordings) · Match audit · What-if runner.

**Tests:** API route tests with `TestClient` · a UI build artifact check in CI · a test asserting the server refuses a non-loopback bind.

---

## PHASE 15 — Docs, demo, release

1. **README** — pitch · **30-second demo GIF** (a prompt change caught in CI) · 10-line quickstart · **"why not vcrpy?" above the fold** · the two-tier honesty section · coverage badge.
2. **`examples/refund_agent/`** — the `DESIGN.md` §6 agent with `make demo` reproducing the regression catch.
3. **Docs site** (mkdocs-material): quickstart, concepts, matcher guide, config reference, custom matchers, security/redaction, FAQ.
4. **Release:** TestPyPI → smoke install → PyPI `0.1.0` → tag, changelog, GitHub release.

---

## Definition of done for v0.1.0

- [x] Name verified free on PyPI (`driftcatch`)
- [ ] GitHub org/repo + npm name claimed
- [ ] Phases 0–10 acceptance criteria all pass
- [x] `mypy --strict` clean
- [x] `ruff` clean
- [x] `pytest -n 4` green (parallel-safe)
- [x] Zero network calls in the unit suite
- [ ] A planted secret cannot reach disk (test-enforced)
- [ ] One input change yields exactly one error (test-enforced)
- [ ] README answers "why not vcrpy?" above the fold
- [ ] `examples/refund_agent` reproduces the regression catch
