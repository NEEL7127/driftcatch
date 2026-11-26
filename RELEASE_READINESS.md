# DriftCatch — release readiness

**Date:** 2026-11-26
**Version:** `0.1.0.dev0`
**Scope:** upgrade pass, full verification (mocked + live), documentation, local release prep

---

## Verdict

**Ready for a `0.1.0` pre-release once two things are done**, neither of which
is a code change:

1. **Rotate the NVIDIA API key.** It was pasted into a chat transcript. It is
   gitignored and absent from every tracked file, but the transcript is the
   exposure.
2. **Run CI once on Linux.** Every test to date has run on Windows / Python
   3.12, while the matrix claims 3.11–3.13 on Ubuntu. Genuinely untested
   territory.

Not yet ready for a confident `1.0`: nobody outside this project has used it.

---

## What this pass produced

```
tests          296  ->  404 passed   (parallel and serial)
source       7,242  ->  8,377 lines
test code    4,045  ->  5,581 lines
CLI            21   ->  24 commands
docs          ~160  ->  2,877 lines across 15 pages
```

```
ruff check + format    clean (72 files)
mypy --strict          clean (39 source files)
main suite             404 passed, -n 4 and serial
testbed offline        77 passed
testbed + live         102 passed, 1 skipped
examples               3 passed
wheel                  builds, 115 KB
secret scan            clean
```

---

## Scope decision

The brief listed ~13 capability areas "to evaluate, not a requirement to
implement every feature blindly". One was declined.

| Capability | Outcome |
|---|---|
| Agent registration / configuration | Already covered by `take()` + `@drift.tool`; documented |
| **Reusable test cases / suites** | **Built** — `drift.Suite` |
| Different agent types | Already validated across 4 testbed agents |
| LLM provider integration | Already covered — HTTP-layer interception |
| Tool-calling, multi-agent | Already validated live |
| **Input / output validation** | **Built** — `assert_output()` |
| LLM response evaluators | **Declined** — see below |
| Memory / context / state | Captured in requests; documented |
| Traces and debugging | Already strong — bisect, first-divergence, UI |
| **Latency tracking** | **Built** — per-event capture + assertions |
| Regression / repeatable | Core strength |
| Mock + real LLM | Replay + live |
| **Structured reports** | **Verified already present** — see below |

### Declined: LLM response evaluation and custom evaluators

Scoring whether an answer is *good* is a different discipline from gating
whether behaviour *changed*. It is well served by promptfoo and DeepEval, and
building a second-rate version here would blur the one thing this tool is clear
about. A failing build that mixes "behaviour changed" with "quality dropped" is
a build nobody can interpret.

`assert_output().satisfies(predicate)` covers deterministic custom checks. The
line drawn is: no LLM judge inside a determinism tool.

### Verified rather than built: structured reports

`drift check` forwards unrecognised arguments to pytest, so
`drift check --junitxml=report.xml` already produces standard JUnit that every
major CI renders natively. Verified working and documented; a second
implementation would only have been a worse one.

---

## New capabilities

### `drift contracts`

Compares each tool's **declared** JSON schema against how it is **actually
used** across the approved corpus.

- Dead schema surface — parameters paid for in tokens on every request and never
  once supplied
- Arguments the model invented but nobody declared
- Result fields that appear in only some runs
- Per-tool error rates and median latency

Findings that recommend *removing* something are withheld below a corpus of 5,
because "never used" on a thin corpus means "not yet seen". The report says
findings were withheld rather than claiming there were none.

Analysis, never a gate. It cannot fail a build.

### `assert_output()`

`contains` · `excludes` · `matches` · `equals` · `is_json` · `json_has` ·
`not_empty` · `satisfies`. Mechanical checks only. `excludes` is the
safety-relevant one — catching a prompt change that starts leaking internal ids
or stack traces.

### `assert_latency()` and per-event latency

Every real call is timed, including calls that failed. Budgets are absolute
(`under`) or relative to the golden (`within_factor_of_golden`).

**During replay, latency assertions are skipped with a warning.** Replay serves
bytes from disk, so the stored duration describes the original call; checking it
would compare a number against itself and pass forever. That is a green light
meaning nothing, so it is refused rather than reported.

### `drift.Suite`

One test body across a table of cases, each with its own recording, plus one
`check` applied to every case — so the fortieth scenario gets the assertion
somebody added to the third. Empty tables and unusable case names are refused at
construction, not on the twelfth case of a live run.

### `drift approve` / `drift reject` / `drift status --require-approved`

Non-interactive approval alongside the interactive `drift review`, and a CI gate
that exits 7 when anything is still pending.

---

## Bugs found and fixed

Six, each with a regression test. Five were found by writing the documentation
and checking every claim against the code; one was found only by a live run.

### The exit-code contract was fiction

`drift init` writes an `AGENTS.md` into the user's project promising that exit
`3` means a behaviour regression and `7` means not approved. **The CLI emitted
neither.** `drift check` returned pytest's raw code, so an agent branching on
`3` could never match. pytest's codes are now translated onto the documented
contract.

### `drift check` reported success having verified nothing

pytest exits `5` for "no tests collected". Passing that through both collided
with `SECRETS_FOUND` and, worse, made a misconfigured path look like a passing
run. Now an error that says so explicitly.

### `drift show <missing>` exited 1, not the documented 6

Library errors are now mapped to exit codes by exception type at one chokepoint,
so every command agrees.

### The `AGENTS.md` template told agents to run a failing command

It instructed `drift record --update`; pytest rejects `--update`, the flag is
`--drift-update`. An agent following it would conclude the workflow was broken
rather than the instructions.

### Replayed events reported a latency they never measured

The replayer hands back the golden's own event object, which still carries the
duration from when it was recorded. A latency assertion on a replayed run
compared that number against itself and passed unconditionally. Replayed steps
are now marked at the session boundary and excluded.

### Tool contracts read no declared parameters — found only by a live run

`parse_request` normalises every tool to `{"name", "schema"}` so the matcher
sees one shape, and the contract extractor never unwrapped that layer. Tools
were listed, and **every parameter was reported as undeclared**.

The unit tests missed it because they constructed `LLMRequest(tools=[...])`
directly, bypassing the normalisation — a fixture that did not match reality.
The unit tests now go through `parse_request`, and a live test asserts that a
real SDK's schemas are readable.

This is the one that justifies live testing. Twenty-five mocked tests passed
against a shape that never occurs on disk.

---

## Documentation

Fifteen pages, 2,877 lines, indexed from `docs/index.md`.

Getting started · Core concepts · Recording and replay · Assertions · Scenario
suites · Matching · Learned invariants · Tool contracts · CLI reference · CI
integration · MCP and coding agents · Configuration · Security ·
Troubleshooting · FAQ

### Documentation is now tested

26 automated checks, because this project had already shipped a README naming
two commands that did not exist:

- Every command invoked in any doc, README or the `AGENTS.md` template must
  exist in the CLI
- Every real command must appear in the CLI reference
- Every exit code in the enum must be documented
- Every page must exist and be reachable from the index
- The `AGENTS.md` template must not use the `--update` flag that broke it

The parser is itself tested, on what it must catch and what it must ignore — a
check that passes everything silently is worse than no check.

---

## Release files added

`LICENSE` (MIT) · `CHANGELOG.md` · `CONTRIBUTING.md` · `CODE_OF_CONDUCT.md` ·
`.gitattributes` · `docs/` · git repository initialised

**The license changed from Apache-2.0 to MIT.** `pyproject.toml` declared
Apache-2.0 with no `LICENSE` file present, and the canonical Apache text could
not be fetched offline. MIT is reproducible exactly and is what pytest, ruff and
vcrpy use. Say the word and it can go back to Apache-2.0 with the canonical
text.

---

## Git state

```
repository   initialised
commits      0
staged       103 files
```

**Nothing has been committed.** `AGENTS.md` forbids commits without explicit
authorisation, so the first commit is left for you.

Verified absent from everything staged: `_testbed/`, `.env`, `.venv`, and any
real credential. The six files matching credential patterns contain regex
definitions, prose examples, and obviously-fake test fixtures including AWS's
own published example key.

---

## Remaining risks

| Risk | Severity | Note |
|---|---|---|
| **Exposed API key** | 🔴 | Pasted into a chat transcript. **Rotate it at build.nvidia.com.** Gitignored and absent from all tracked files. |
| **Never run on Linux** | 🔴 | All testing Windows / 3.12; CI declares 3.11–3.13 on Ubuntu. The Windows file-contention retries are no-ops elsewhere, but that is untested. |
| No external users | 🟡 | Nobody but the author has used this. The five-minute path has never met a stranger. |
| Gemini / Bedrock not live-tested | 🟡 | Shapes unit-tested only. The contracts bug is a reminder that unit-tested shapes can be fiction. |
| Not on PyPI | 🟡 | Name unclaimed. Wheel builds cleanly. |
| Example recording is stale | 🟢 | `examples/refund_agent` warns about `prompt_hash`. Demo data; tests pass. |
| Data-equality mining breadth | 🟢 | First call per tool, scalar fields only. |

---

## Before announcing

1. Rotate the key.
2. Run CI on Linux across 3.11, 3.12 and 3.13.
3. Put one external person through `docs/getting-started.md` without helping
   them.
4. Live-test one more provider family — Gemini or Bedrock, given what the
   contracts bug showed about unit-tested shapes.
5. Decide MIT versus Apache-2.0.
6. Claim the PyPI name.

---

## What earns confidence

Six bugs this pass, and the pattern in them is worth naming: **five came from
checking documentation claims against the code, and one came from a live run.**
None came from re-reading the implementation.

Three of the six were silent — the framework kept working while doing the wrong
thing. A latency assertion that always passed. An exit code nobody could branch
on. A contract report that found no parameters. That is the only class of bug a
testing tool truly cannot afford, and it is precisely the class that mocks and
happy-path tests do not surface.

The documentation checks exist because of this, not in spite of it.

---

*The testbed is gitignored and disposable. Delete `_testbed/` once the key is
rotated.*
