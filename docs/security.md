# Security

Recordings are files containing real requests and real responses, and they are
meant to be committed. That combination deserves care, and this page is about
where the sharp edges are.

---

## Secrets

### Redaction on the write path

Credential-shaped values are redacted **before** a recording is written, not
after. These patterns are recognised by default:

| Pattern | Covers |
|---|---|
| `sk-…` | OpenAI-style keys |
| `sk-ant-…` | Anthropic keys |
| `AKIA…` | AWS access key ids |
| `ghp_…`, `github_pat_…` | GitHub tokens |
| `xoxb-`, `xoxp-`, `xoxa-`, `xoxr-`, `xoxs-` | Slack tokens |
| `AIza…` | Google API keys |
| `Bearer …` | Authorization headers |

Redacted field paths are listed in the recording's `meta.redacted_fields`, so
you can see that redaction happened rather than assuming it.

### Scanning

```bash
drift scan
```

Exits **5** when anything credential-shaped is found. Run it in CI and in a
pre-commit hook:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: drift-scan
        name: scan recordings for credentials
        entry: drift scan
        language: system
        pass_filenames: false
```

### What redaction does not cover

Pattern matching catches credentials that *look* like credentials. It will not
catch:

- A bespoke internal token with no recognisable shape
- PII in message bodies — a customer's name, address or account number is not
  credential-shaped and will be recorded verbatim
- A secret passed as an ordinary-looking tool argument

**Recordings should be treated as sensitive by default.** If your agent handles
regulated data, assume recordings contain it, and decide deliberately whether
they belong in a public repository. `drift prune` drops response bodies while
keeping behaviour, which is one way to commit trajectories without committing
content.

---

## Recording names reach the filesystem

A recording name becomes a filename, and names do not only come from test
authors. They arrive from CLI arguments, UI routes and **MCP tool calls made by
a coding agent** — which may be acting on content it read somewhere.

Names are validated at the path chokepoint:

```
^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$
```

No separators, no `..`, no leading dot, no null bytes.

Names are **refused, never sanitised**. Silently rewriting `a/b` to `a_b` would
let two different names collide on one file, which turns a path bug into a
data-corruption bug.

```python
drift.take("../../../../etc/passwd")
# InvalidRecordingName: invalid recording name '../../../../etc/passwd'.
```

Before this was fixed, that wrote outside the recordings directory, and an
absolute name escaped entirely — giving arbitrary `.json` read through
`show`/`audit`/`bisect` and arbitrary write through `prune`/`restore`.

---

## The MCP boundary

The [MCP server](MCP.md) exposes ten tools to coding agents. Every one is
read-only except `learn`, and `learn` cannot enforce what it mines.

**Deliberately not exposed:** `approve`, `reject`, `record`, `restore`, and
`check --live`.

The reasoning is specific rather than general caution. An agent looking at a red
behaviour test will reach for the fastest way to make it green, and approving
the new recording is that way. It would be doing exactly what it was asked —
and it would have deleted the signal. Approval stays with a human.

`check --live` is withheld for a second reason: it spends money, and an agent
should not be able to decide to.

The toolset is pinned by a test, so adding to it is always a deliberate edit.

---

## Live runs cost money

Live runs refuse in CI unless explicitly permitted:

```bash
DRIFTCATCH_ALLOW_LIVE=1 drift check --live
```

Outside CI they are allowed, since you are there watching. `drift.live_check`
**skips** rather than fails when live runs are not permitted, so a normal suite
stays free and offline while the gate still exists for runs that opt in.

---

## Replay does not reach the network

In replay mode no HTTP request leaves the process — responses are served from
disk. That is worth knowing for two reasons: a pull request from a fork can run
the full behaviour suite without credentials, and a replayed test cannot
accidentally spend money.

The exception is deliberate: `passthrough` boundaries execute for real during
replay. Anything listed there does reach the network.

With `strict = false`, an unmatched request falls through to the real network.
That is useful while building up a recording and risky in CI, because a test can
silently start making real calls. The default is `strict = true`.

---

## Reporting a vulnerability

Please do not open a public issue. Email the maintainer and allow time for a fix
before disclosure.

Areas worth scrutiny: recording names reaching the filesystem, secret redaction
on the write path, and anything reachable from the MCP server.
