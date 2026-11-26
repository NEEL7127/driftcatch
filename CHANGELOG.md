# Changelog

All notable changes to DriftCatch are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the version is `0.x`, the recording format and the Python API may change
between minor versions. Breaking changes are listed under **Changed** with a
migration note. The on-disk recording format carries its own integer version and
refuses to load a format it does not understand, so an upgrade never silently
misreads an old golden.

## [Unreleased]

## [0.1.0] - unreleased

First public release.

### Added

**Recording and replay**
- `drift.take(name)` context manager: records LLM calls and tool calls on first
  run, replays them deterministically afterwards.
- Interception at the HTTP client layer (`httpx` and `httpx2`), so any provider
  SDK or gateway that speaks HTTP is supported without a per-provider adapter.
- Streaming support, including assembly of streamed tool calls and of reasoning
  deltas from o1-style, DeepSeek-R1, QwQ, Nemotron and Anthropic thinking models.
- `@drift.tool` decorator for recording and replaying tool side effects, with a
  `passthrough` list for tools that must stay live during replay.
- Atomic, gzip-compressed recordings with plaintext status, fingerprint and
  trajectory so a behaviour change is legible in a pull request diff.

**The approval gate**
- Recording a run does not approve it. `drift approve` / `drift reject` make the
  distinction explicit, because a recorded-but-wrong golden would otherwise
  become a permanently enforced bug.
- `drift review` for walking pending recordings.
- Replaying an unapproved recording warns.

**Drift detection**
- Two-tier model. Tier 1 (`drift check`) replays offline and for free, catching
  changes in your own code, prompts, model id and tool schemas. Tier 2
  (`drift check --live`) spends real money against the provider and catches
  model-behaviour change, thresholded with `pass_k`.
- Four behaviour-determining inputs fingerprinted separately (system prompt,
  model id, tool schemas, matcher config) so a drift report names its cause.
- `drift bisect` for attributing a drift to one of those inputs.
- `drift diff`, `drift explain`, `drift show` for inspecting a divergence,
  reported at the first point of divergence rather than at the end.

**Invariant mining**
- `drift learn` mines behavioural rules from the approved corpus using seven
  templates: `always_precedes`, `always_called`, `never_called`,
  `always_terminal`, `never_consecutive`, `max_tool_calls`, `data_equality`.
- Four anti-spurious filters: minimum support, a confidence gate, a requirement
  that the value actually varies across runs, and Daikon-style redundancy
  removal.
- `drift invariants` enforces mined rules as a test gate.

**Assertions**
- `assert_output()` for what the model said: `contains`, `excludes`, `matches`,
  `equals`, `is_json`, `json_has`, `not_empty` and `satisfies` for an arbitrary
  deterministic predicate. Mechanical checks only - there is no scoring and no
  LLM judge, because that is evaluation and a different discipline.
- `assert_latency()` with `under(total_ms=, per_call_ms=)` and
  `within_factor_of_golden()`.
- Per-event latency is captured on every real call, including calls that
  failed, and survives the round trip to disk.

**Scenario suites**
- `drift.Suite(name, cases, check=...)` runs one test body across a table of
  cases, each with its own recording, and applies one shared policy to every
  case. Empty tables and unusable case names are refused at construction rather
  than on the twelfth case of a live run.

**Tool contracts**
- `drift contracts` compares each tool's declared JSON schema with how it is
  actually used across the approved corpus: dead schema surface costing tokens
  on every request, arguments the model invented, unstable result shapes, and
  per-tool error rates and median latency. An analysis command that never fails
  a build.
- Findings that recommend *removing* something are withheld below a corpus of
  5, since "never used" on a thin corpus means "not yet seen". The report says
  findings were withheld rather than claiming there were none.

**Tooling**
- 24 CLI commands, with `--json` on the machine-readable ones, documented exit
  codes and errors that carry a `hint` field.
- `drift approve` and `drift reject` for non-interactive approval, alongside the
  interactive `drift review`.
- `drift status --require-approved` exits 7 when any recording is still
  pending, as a CI gate.
- A pytest plugin.
- A local web UI (`drift ui`) for reviewing recordings and diffs.
- An MCP server (`drift mcp`) exposing ten read-only tools to coding agents.
  Mutating commands are deliberately not exposed: an agent's reflex on a red
  test is to make it green.
- `drift scan` for secrets in recordings, and redaction on the write path.
- `drift init` writes an `AGENTS.md` section so coding agents discover the
  framework's conventions.

### Fixed

Found while bringing the documentation in line with the code, and each one now
has a regression test.

- **Advertised exit codes were never emitted.** `drift init` writes an
  `AGENTS.md` section promising that `3` means a behaviour regression and `7`
  means not approved. The CLI emitted neither: `drift check` returned pytest's
  raw exit code, so an agent branching on `3` could never match. pytest's codes
  are now translated onto the documented contract.
- **`drift check` reported success when it collected no tests.** pytest exits
  `5` for "no tests collected"; passing that through both collided with
  `SECRETS_FOUND` and, worse, made "verified nothing" look like "verified
  everything". It is now an error that says so.
- **`drift show <missing>` exited `1` instead of the documented `6`.** Library
  errors are now mapped to exit codes by type at one chokepoint, so every
  command that reports a missing recording agrees.
- **The `AGENTS.md` template told agents to run `drift record --update`**,
  which pytest rejects; the flag is `--drift-update`. A template that instructs
  an agent to run a failing command is worse than none.
- **Tool contracts read no declared parameters from real recordings.**
  ``parse_request`` normalises every tool to ``{"name", "schema"}`` so the
  matcher sees one shape, and the extractor never unwrapped that layer. Tools
  were listed, every parameter was reported as undeclared, and the unit tests
  missed it because they built the request object directly instead of going
  through the normalisation. Found by a live run against a real SDK; the unit
  tests now go through ``parse_request`` so the fixture cannot drift from
  reality again.
- **Replayed events reported the recorded duration as this run's latency.** The
  replayer hands back the golden's own event object, so a latency assertion
  during replay compared a number against itself and passed unconditionally.
  Replayed steps are now marked at the session boundary and excluded.

### Documentation

- Fifteen pages under `docs/`, indexed from `docs/index.md`.
- Automated consistency checks: every command invoked in any documentation file
  must exist, every real command must appear in the CLI reference, every exit
  code must be documented, and every page must be reachable from the index.
  This project had already shipped a README naming two commands that did not
  exist, so the check is automated rather than remembered.
- `LICENSE` (MIT), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` and this changelog.

### Security
- Recording names are validated at the path chokepoint and refused — never
  silently sanitised — so a name arriving from a CLI argument, a UI route or an
  MCP tool call cannot escape the recordings directory.
- Pruned recordings refuse to replay rather than serving an empty body.
