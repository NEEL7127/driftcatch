# CLI reference

```bash
drift --help
drift <command> --help
```

Every command accepts `--recordings-dir` / `-d` to point at a directory other
than the configured one.

---

## Commands

### Recording and verifying

| Command | What it does |
|---|---|
| `drift record` | Record by running your tests. Extra arguments go to pytest. |
| `drift check` | Re-verify behaviour by replaying (Tier 1). Free and offline. |
| `drift check --live` | Re-verify against the real model (Tier 2). **Costs real money.** |
| `drift restore <name>` | Undo a re-record, putting the previous version back. |
| `drift prune` | Drop raw response bodies, keeping the trajectory for behaviour tests. |

### Reviewing

| Command | What it does |
|---|---|
| `drift status` | List every recording with its approval status and drift state. |
| `drift status --require-approved` | Same, but exit 7 if anything is still pending. For CI. |
| `drift show <name>` | Print a recording: its trajectory, then every event in order. |
| `drift review` | Walk the pending queue interactively, approving or rejecting. |
| `drift approve <name> --by you` | Approve one recording non-interactively. |
| `drift reject <name>` | Mark a recording wrong so it never becomes golden. |
| `drift diff <name> --against <other>` | Show the behaviour difference between two recordings. |
| `drift ui` | Launch the local web UI. |

### Understanding

| Command | What it does |
|---|---|
| `drift coverage` | Behaviour coverage: which of the agent's paths were ever tested. |
| `drift suggest` | Propose scenarios that would close the biggest coverage gaps. |
| `drift bisect <name>` | Attribute a behaviour change to the input that caused it. |
| `drift what-if <name>` | Explain how to run a counterfactual against a recording. |
| `drift audit <name>` | Show how each recorded request keys, so a loose matcher is visible. |
| `drift learn` | Learn the rules your agent always follows, from approved recordings. |
| `drift invariants` | Show the learned rules and whether they are enforced. |
| `drift contracts` | Compare each tool's declared schema with how it is really used. |

### Operating

| Command | What it does |
|---|---|
| `drift init` | Set up DriftCatch in this project and print a starter test. |
| `drift scan` | Audit recordings for credential-shaped values. Pre-commit friendly. |
| `drift mcp` | Run as an MCP server, so a coding agent can call DriftCatch as tools. |
| `drift explain` | Describe this tool to a coding agent: commands, exit codes, workflows. |
| `drift version` | Print the installed version. |

---

## Exit codes

Branch on these without parsing output.

| Code | Name | Meaning |
|---|---|---|
| 0 | `OK` | Success |
| 1 | `ERROR` | Something went wrong that is not one of the cases below |
| 2 | `USAGE` | Bad command-line usage |
| 3 | `REGRESSION` | Behaviour changed: a trajectory diff or a learned invariant broke |
| 4 | `GATE_FAILED` | A quality gate failed, e.g. coverage below `--fail-under` |
| 5 | `SECRETS_FOUND` | Credential-shaped values were found in recordings |
| 6 | `NOT_FOUND` | The named recording does not exist |
| 7 | `NOT_APPROVED` | The recording exists but no human has approved it |

```bash
drift scan
case $? in
  0) echo "clean" ;;
  5) echo "secrets found - do not commit" ; exit 1 ;;
esac
```

---

## Machine-readable output

These commands take `--json` and write a single JSON object to stdout, with
nothing else mixed in:

`status` · `coverage` · `learn` · `invariants` · `contracts` · `scan` ·
`explain`

```bash
drift status --json | jq '.recordings[] | select(.status != "approved") | .name'
```

Every payload has the same envelope:

```json
{ "ok": true, "command": "status", "...": "..." }
```

Errors use the same envelope, with a `hint` saying what to do:

```json
{
  "ok": false,
  "command": "show",
  "error": "no recording at recordings/refund.json",
  "hint": "Record one first: drift record refund"
}
```

---

## Passing arguments to pytest

`drift record` and `drift check` run your test suite, and forward anything they
do not recognise:

```bash
drift record tests/test_refund.py -k small -x
drift check --junitxml=report.xml -n 4
drift check --live tests/test_critical.py
```

This is why there is no `--junit` flag of its own — pytest's `--junitxml`
already works, and a second implementation would only be a worse one.

---

## Worked flows

**First recording**

```bash
drift init
drift record tests/test_agent.py
drift show my_scenario
drift approve my_scenario --by you@example.com
```

**After an intended behaviour change**

```bash
drift record tests/test_agent.py     # previous version is archived
drift diff my_scenario --previous    # new vs the archived old one
drift approve my_scenario --by you@example.com
# changed your mind:
drift restore my_scenario
```

**Investigating a failure**

```bash
drift status                 # what is stale
drift bisect my_scenario     # which input caused it
drift show my_scenario       # the full event list
drift audit my_scenario      # how requests are being keyed
```

**Getting more from the corpus**

```bash
drift coverage               # untested paths
drift suggest                # scenarios that would close the gaps
drift learn                  # mine rules
drift invariants             # review them
drift contracts              # schema versus reality
```
