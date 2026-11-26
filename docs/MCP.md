# Using DriftCatch from a coding agent

DriftCatch can be driven two ways from Cursor, Claude Code, Codex and similar.
Most people want the first.

| | How it reaches the agent | Setup |
|---|---|---|
| **CLI + `AGENTS.md`** | the agent runs shell commands, as it already does | `drift init` — zero config |
| **MCP server** | the agent calls typed tools | one config file entry |

The CLI route needs no setup at all: `drift init` writes an `AGENTS.md` that
every major coding agent reads automatically, and every read command takes
`--json`. Use MCP when you want typed tools and a tighter integration.

---

## Install

```bash
pip install "driftcatch[mcp]"
```

## Claude Code

```bash
claude mcp add driftcatch -- drift mcp
```

Or in `.mcp.json` at your repo root (shareable with the team):

```json
{
  "mcpServers": {
    "driftcatch": {
      "command": "drift",
      "args": ["mcp", "--recordings-dir", "tests/recordings"]
    }
  }
}
```

## Cursor

`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "driftcatch": {
      "command": "drift",
      "args": ["mcp"]
    }
  }
}
```

> If `drift` is not on PATH (a virtualenv that the editor does not activate),
> use the interpreter directly:
> `"command": ".venv/bin/python", "args": ["-m", "driftcatch.cli", "mcp"]`
> On Windows: `".venv\\Scripts\\python.exe"`.

---

## What the agent gets

Ten tools. Nine read; one proposes.

| Tool | Does |
|---|---|
| `explain` | what DriftCatch is and is not, exit codes, workflows — call first |
| `status` | recordings and their approval state |
| `coverage` | which behaviours were never tested |
| `show` | one recording: trajectory and every event |
| `bisect` | compare two recordings and attribute the change to an input |
| `learn` | mine rules from approved recordings (**proposes only**) |
| `invariants` | learned rules and whether each is enforced |
| `scan` | credential audit |
| `audit` | how each request is keyed, for diagnosing a matcher |
| `pending_approvals` | what needs a human, and the command they should run |

---

## What the agent deliberately cannot do

**`approve`, `reject`, `record`, `restore` and `check --live` are not exposed.**

This is the most important design decision in the integration, so it is worth
stating plainly.

An agent's reflex when a test goes red is to make it green. In DriftCatch the
green button is `approve`, and approving a recording means *a human has
confirmed this behaviour is correct*. An agent that could approve would
eventually approve a regression to clear a failing test — and the approval
gate, the thing that stops a bug being laundered into a specification, would be
worth nothing.

So the boundary is drawn at judgement:

- the agent may **read, diagnose and propose**;
- the human **decides**.

`pending_approvals` exists for exactly this. The agent can see what is waiting
and tell you the command to run, without being able to run it:

```json
{
  "pending_recordings": ["refund_9000"],
  "actions_for_the_human": [
    {"what": "1 recording(s) awaiting approval", "human_runs": "drift review"}
  ],
  "note": "You cannot perform these. Report them and let the human decide."
}
```

`learn` is included because mining is read-only in effect: it writes rules as
*pending* and can never enforce one. Enforcement still needs
`drift learn --approve`, run by you.

Live runs are also absent: they cost real money, and
`DRIFTCATCH_ALLOW_LIVE=1` should be a decision you make, not one an agent makes
on your behalf.

---

## A typical exchange

> **You:** the refund test is failing, what happened?

The agent calls `explain`, then `bisect(golden="refund_1182", use_previous=true)`:

```json
{
  "matched": false,
  "golden_tools": ["lookup_order", "issue_refund"],
  "current_tools": ["web_search", "issue_refund"],
  "attribution": {
    "changed_inputs": ["prompt_hash"],
    "root_cause": "the system prompt",
    "confident": true,
    "prompt_diff": ["- Use available tools to find", "+ Search for"]
  }
}
```

> **Agent:** The system prompt changed — "Use available tools to find" became
> "Search for", and the word *search* pushed the model to `web_search` instead
> of the database. That's a regression, not an intended change. I'd revert the
> wording. If you did mean it, you'll need to run `drift record
> refund_1182` and `drift review` yourself — I can't approve recordings.

Diagnosis from the agent, decision from you.

---

## Note on SDK versions

This targets the **MCP Python SDK v2**, where `FastMCP` was renamed to
`MCPServer`. Most tutorials still show the v1 API. If you see
`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`, you are on v2 and
the code here is correct.
