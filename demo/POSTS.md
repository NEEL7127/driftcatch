# Ready-to-post copy

Four venues, four posts. Same project, genuinely different framing — because
these audiences punish copy-paste and reward people who write for the room.

The rule behind all of them: **lead with the idea, not the tool.** "I built X"
gets ignored or removed. "Here is a technique, and here is what happened when I
applied it" gets read.

---

## 1. Hacker News — Show HN

**Post Tuesday–Thursday, 9am–12pm ET.** Block out four hours afterwards to sit
in the thread. The thread is the launch; the post is just the door.

### Title

```
Show HN: DriftCatch – mine behavioural rules from your AI agent's approved runs
```

No superlatives. No "revolutionary", "best", "first". HN removes posts for it
and commenters will say so.

### URL

```
https://github.com/NEEL7127/driftcatch
```

### First comment (post this yourself, immediately)

> I kept shipping prompt changes that silently altered what my agent *did*.
> One word changed, the agent stopped refunding customers and started escalating
> them, and every test still passed — because the tests checked the answer, and
> the answer still looked fine.
>
> Record/replay for agents already exists (vcrpy, pytest-agentreplay, agentevals
> and others). That part isn't new and I'm not claiming it is. What I hadn't
> seen is applying **specification mining** to agent trajectories.
>
> Daikon did this for ordinary programs in 2001: run the program, observe
> invariants that always hold, propose them as a spec. Agent runs turn out to be
> a good fit, because the thing you want to pin — the order of tool calls, which
> tools always appear, which arguments track each other — is exactly the shape
> Daikon-style templates capture.
>
> Point it at runs you've approved and it mines:
>
>     lookup_order always runs before issue_refund            23/23 runs
>     issue_refund is always the last tool                    18/18 runs
>     issue_refund.order_id always equals
>       lookup_order.order_id                                 18/18 runs
>
> That last one is a real bug class nobody writes a test for. It breaks when
> someone reuses a loop variable and it costs a customer money.
>
> Mining naively produces mostly coincidences, so there are four filters: a
> minimum support of 5 runs, a confidence gate of 1 - 0.5^support, a requirement
> that the value actually *varies* across runs (two constants aren't a
> relationship), and Daikon-style redundancy removal — if a precedes b and b
> precedes c, the a-precedes-c rule is dropped. On a real corpus that took 19
> mined rules down to 15, and the 15 are the ones worth reading.
>
> The precondition that makes any of it sound: it only mines from recordings a
> **human approved**. Mining from unreviewed runs would launder a bug into a
> specification. That's also why recording and approving are separate steps —
> if "recorded" meant "correct", the first time your agent did something stupid
> that stupidity would become the enforced contract, and the fix would fail as a
> regression.
>
> One thing I want to be upfront about, because I think the category is fuzzy on
> it: **replay cannot catch the model changing underneath you.** A recording
> holds what the model said then, not what it would say now. That's physics, not
> a missing feature. There's a separate live mode that makes real calls for
> exactly that, and it scores determinism by grouping repeated runs by
> trajectory rather than counting pass/fail. Any tool claiming to catch model
> drift from a cached response is claiming something impossible.
>
> It's 0.1 — 407 tests, mypy --strict, CI green on Linux across 3.11/3.12/3.13,
> MIT. I'm its only user so far, so I'd genuinely rather hear "this is wrong
> because X" than collect stars.
>
> pip install driftcatch

### In the thread

- Answer every question plainly. "I don't know" is a fine answer and reads well.
- Someone **will** say "this is just vcrpy". Agree about the replay half, then
  point at the mining. Do not get defensive — that exchange is how the rest of
  the thread learns what's different.
- Don't ask anyone to upvote. HN detects it and it kills the post.

---

## 2. r/LocalLLaMA

Your best-fit subreddit. People there build agents and have had this exact bug.

**Title:**

```
I applied specification mining (Daikon, 2001) to agent trajectories — it finds rules nobody wrote
```

**Body:**

> Context: I kept changing a prompt and silently changing what my agent *did*.
> The answer still looked right, so evals passed. I'd find out later.
>
> Replay testing catches that — record a run, replay it, diff the behaviour. A
> few tools already do this, so that part isn't interesting.
>
> What I hadn't seen anyone do: once you have a corpus of runs a human has
> approved, you can **mine** the rules out of it. This is specification mining,
> a technique from 2001 (Daikon) for ordinary programs. Nobody seems to have
> pointed it at agent trajectories.
>
> From 23 approved runs of a refund agent it mined 13 rules, including:
>
>     lookup_order always runs before issue_refund           23/23
>     escalate_to_human is offered but never called          23/23
>     issue_refund.order_id always equals
>       lookup_order.order_id                                18/18
>
> Nobody wrote those. The third one is the interesting case — "the order you
> look up is the order you refund" is an assumption everybody makes and nobody
> tests.
>
> The hard part is not mining, it's **not** mining garbage. Four filters:
> minimum support 5, confidence 1-0.5^support, the value has to actually vary
> across runs, and transitive rules get dropped as redundant.
>
> It also compares the tool schemas you declare against how the model really
> uses them. On my own demo corpus it found a parameter I'd been paying prompt
> tokens for on every single request and never once used.
>
> Honest limitation: replay can't catch the model itself changing. A recording
> is what the model said then. There's a live mode for that which runs the
> scenario N times and groups by trajectory — so you get "80% path A, 20% path
> B" rather than a meaningless 4/5.
>
> MIT, `pip install driftcatch`, repo: github.com/NEEL7127/driftcatch
>
> It's 0.1 and I'm the only user. If you build agents I'd really like to know
> where it breaks for you.

---

## 3. r/Python

Different room. They care about the packaging and the pytest integration, not
the AI angle. **Read their self-promotion rules before posting** — they're
enforced, and they change.

**Title:**

```
DriftCatch: a pytest plugin that records your AI agent's behaviour and fails the build when it changes
```

**Body:**

> **What my project does**
>
> It's a pytest plugin for behaviour regression testing of LLM agents. You wrap
> a call you already make:
>
> ```python
> import driftcatch as drift
>
> def test_refund():
>     with drift.take("refund_1182") as take:
>         answer = my_agent("refund order 1182")
>     take.assert_tools().called_in_order("lookup_order", "issue_refund")
> ```
>
> First run records the HTTP traffic and tool calls. Every run after that
> replays from disk — no network, no tokens, deterministic. Change a prompt and
> the test fails, naming which input caused it.
>
> Interception is at the `httpx`/`httpx2` client layer rather than per-provider,
> so OpenAI, Anthropic, LangChain, LlamaIndex or a hand-rolled loop all work
> without an adapter.
>
> **Target audience**
>
> Anyone running an LLM agent in production who wants a pre-merge gate. It's
> 0.1 — tested but not yet battle-tested, so I'd treat it as early.
>
> **Comparison**
>
> vcrpy is the closest analogue and does HTTP record/replay well, but it's not
> agent-aware: it doesn't model trajectories or tool calls. agentevals and
> similar score answer *quality*, which is a different question — this one only
> asks whether behaviour *changed*.
>
> The part I haven't found elsewhere: it mines behavioural invariants from the
> runs you've approved, Daikon-style, so you get rules you never wrote —
> including data relationships like "the order id you look up is the order id
> you refund".
>
> MIT. 407 tests, mypy --strict clean, CI on 3.11/3.12/3.13.
>
> `pip install driftcatch` · github.com/NEEL7127/driftcatch

---

## 4. dev.to — the technical article

Write this **first**. It's the asset everything else links to, and it's the
piece that still has value in six months.

**Title:** `Mining behavioural rules from AI agent runs`

**Tags:** `ai`, `python`, `testing`, `opensource`

**Structure:**

1. **The bug** (300 words) — one word changed, refund became escalation, tests
   passed. Make the reader recognise it.
2. **Why existing tools miss it** (200) — evals score the answer; observability
   is too late; replay catches it but only for *your* changes.
3. **Specification mining** (600) — Daikon, 2001. What an invariant template is.
   Why agent trajectories fit. The seven templates.
4. **Why it's mostly noise without filters** (500) — this is the real content.
   Minimum support. The confidence gate and why it's pinned to support=5. Why a
   value that never varies isn't a relationship. Transitive redundancy, 19 rules
   down to 15.
5. **The approval gate** (300) — the precondition. Mining from unreviewed runs
   launders a bug into a spec.
6. **What it can't do** (200) — replay vs model drift. Be blunt.
7. **Try it** (100) — `pip install driftcatch`, link.

Write §4 properly. Anyone can say "I mined some rules"; showing the ones you
*threw away* and why is what makes it credible.

---

## Order and timing

| When | Do |
|---|---|
| Day 0 | Publish the dev.to article |
| Day 1, Tue–Thu 9am ET | Show HN + first comment, then stay in the thread |
| Day 1, +2h | r/LocalLLaMA |
| Day 3 | r/Python |
| Day 3–5 | Console.dev, PyCoder's Weekly, Python Weekly |
| Ongoing | Be useful in one Discord. One adopter beats 500 visitors. |

## Before any of it

- [ ] Repo description and topics — still blank, it's the first thing a visitor sees
- [ ] Revoke the PyPI token that was pasted in chat
- [ ] `pip install driftcatch` from a clean machine, once, yourself

## What to expect

A good Show HN for a niche dev tool is 50–200 stars, not thousands. Thousands
in a day means a known author, a company behind it, or a category moment. Chase
that number and you'll make bad decisions — like launching before the repo is
ready.

The number that actually matters in week one is **how many people installed it
and came back**. Everything else is vanity.
