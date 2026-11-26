# Tool contracts

A tool has two descriptions.

The one you **declare** to the model lives in the JSON schema you send with
every request. The one that is **real** is whatever the model and your code
actually did, run after run.

They drift apart silently. Neither your type checker nor your tests can see the
gap, because the model is the thing choosing which half of the schema to use.

```bash
drift contracts
```

```
Tool contracts from 23 approved recording(s)

lookup_order  -  31 call(s) in 23 run(s), median 42ms
  arguments:
    order_id: integer
    include_history: boolean  (optional, 2/31)
  returns: object
    id: integer
    total: number
    status: 'pending' | 'shipped' | 'refunded'

issue_refund  -  18 call(s) in 18 run(s), median 310ms
  arguments:
    order_id: integer
    amount: number
    reason: string  (optional, 4/18)
  returns: object
    ok: boolean
    refund_id: string
  ! parameter 'currency' is declared but was never supplied
  ! failed 2/18 calls (11%): TimeoutError

escalate_to_human  -  0 call(s) in 0 run(s)
  ! offered in 23 run(s) but never called - its schema costs tokens on every request

3 finding(s). These are observations, not failures.
```

---

## What it tells you

### Dead schema surface

A parameter you declare and the model has never once used is paid for on
**every single request**, in prompt tokens, forever.

```
! parameter 'currency' is declared but was never supplied
! offered in 23 run(s) but never called - its schema costs tokens on every request
```

This is the finding with a direct cost attached. A tool definition that is 200
tokens and never chosen across an entire approved corpus is 200 tokens on every
request for nothing.

### Invented arguments

```
! parameter 'customer_email' was supplied but is not in the declared schema
```

The model invented an argument name, or the schema sent at request time
disagrees with the function your code actually calls. Either way something is
wrong, and your tool is probably ignoring the input silently.

### Shape instability

```
! result fields appear only sometimes: refund_id - downstream code reading them
  is relying on luck
```

A result whose keys differ between runs is a contract your downstream code is
depending on by accident.

### Reliability

```
! failed 2/18 calls (11%): TimeoutError
```

Per-tool error rates and median latency, drawn from runs you approved. This is
where a retry belongs.

---

## Evidence before advice

Findings that recommend **removing** something are withheld until the corpus is
large enough for the absence to mean anything.

```
2 approved recording(s) is too few to call anything unused. Findings that
recommend removing a tool or a parameter need at least 5 and are withheld
until then - with this few runs, 'never used' means 'not yet seen'.
```

Across one run, "this parameter was never used" is not evidence — it is a
sample size of one, and acting on it deletes a working parameter. The threshold
is 5, the same one [invariant mining](invariants.md) uses, for the same reason.

Findings that reason from something that **happened** — an invented argument, a
failure, an unstable result — need no such threshold. One run proves those.

When findings are withheld, the report says `No findings from the evidence
available`, not `No findings`. Those mean different things and conflating them
would be a lie by omission.

---

## It is a report, not a gate

`drift contracts` never fails a build. It is an analysis of the approved corpus.
[`drift invariants`](invariants.md) is the gate.

Keeping the two apart means a contract report can be exploratory and a little
noisy without ever breaking somebody's pipeline.

---

## Machine-readable form

```bash
drift contracts --json
```

```json
{
  "ok": true,
  "command": "contracts",
  "corpus": 23,
  "removal_findings_enabled": true,
  "min_corpus_for_removal": 5,
  "tools": [
    {
      "name": "issue_refund",
      "calls": 18,
      "runs": 18,
      "offered_in": 23,
      "never_called": false,
      "error_rate": 0.1111,
      "error_types": ["TimeoutError"],
      "median_ms": 310,
      "returns": ["object"],
      "arguments": [
        {
          "name": "order_id",
          "types": ["integer"],
          "required": true,
          "present_in": 18,
          "total": 18,
          "values": []
        }
      ],
      "result_fields": [{"name": "ok", "types": ["boolean"], "required": true}],
      "declared_parameters": ["order_id", "amount", "reason", "currency"],
      "unused_parameters": ["currency"],
      "undeclared_parameters": [],
      "unstable_result_fields": ["refund_id"],
      "findings": ["parameter 'currency' is declared but was never supplied"]
    }
  ]
}
```

Also available to coding agents through the [MCP server](MCP.md) as the
`contracts` tool.

---

## From Python

```python
import driftcatch as drift

recordings = drift.list_recordings("recordings")
contracts = drift.extract_contracts(recordings)

for contract in contracts:
    if contract.error_rate > 0.05:
        print(f"{contract.name} fails {contract.error_rate:.0%} of the time")
    for param in contract.unused_parameters:
        print(f"{contract.name}.{param} is dead schema surface")
```

| Property | Meaning |
|---|---|
| `calls`, `runs`, `offered_in` | Frequency |
| `arguments`, `result_fields` | `FieldShape` with types, optionality, enum values |
| `declared_parameters` | From the JSON schema actually sent |
| `unused_parameters` | Declared, never supplied |
| `undeclared_parameters` | Supplied, never declared |
| `unstable_result` | Result fields present in only some calls |
| `never_called` | Offered to the model, never chosen |
| `error_rate`, `error_types` | Reliability |
| `median_ms` | Median measured duration, or `None` |

---

## Provider shapes

Both common tool-schema envelopes are read: the flat
`{"name", "parameters"}` form and the OpenAI `{"function": {...}}` wrapper, plus
Anthropic's `input_schema`. A report that handled only one would claim every
parameter was undeclared.

---

## Enum detection

A field with few distinct values is reported as an enumeration:

```
status: 'pending' | 'shipped' | 'refunded'
```

Above 8 distinct values it falls back to a type, so free-text fields do not turn
into an unreadable wall of examples. Containers are never rendered as enums.

Booleans are reported as `boolean`, never `integer` — `bool` subclasses `int` in
Python, and a contract report saying `integer` would actively mislead anyone
regenerating a schema from it.
