# LinkedIn launch post

Attach `assets/driftcatch-launch.mp4` as a **native** upload. Put the repo link
in the first comment — LinkedIn suppresses reach on posts with external links in
the body.

---

## The post

> You change one word in a prompt.
>
> Your agent stops refunding customers and starts escalating them.
>
> Every test still passes.
>
> I kept hitting this, and I couldn't find anything that caught it. Evals score
> the *answer*, and the answer still looks fine. Observability catches it in
> production — a week too late. Nothing was watching the thing that actually
> changed: what the agent **decided to do**.
>
> So I built DriftCatch. It's open source, MIT, and it does three things.
>
> **1. Record once, replay forever.**
> One real run gets recorded. After that your suite replays from disk — free,
> offline, byte-identical. A 32-second suite became 0.1 seconds and $0.00.
> It hooks the HTTP layer, so LangChain, LlamaIndex, CrewAI, a raw SDK or your
> own loop all work with no adapter.
>
> **2. Recording isn't approving.**
> A recorded run can be wrong. If "recorded" meant "correct", the first time
> your agent did something stupid that stupidity would become the enforced
> contract — and the fix would fail as a regression. So goldens need a human.
>
> **3. It finds the rules you never wrote.**
> This is the part I haven't seen anywhere else.
>
> Point it at runs you've approved and it mines the invariants your agent has
> always obeyed:
>
> → lookup_order always runs before issue_refund — 23/23 runs
> → issue_refund is always the last tool — 18/18 runs
> → **the order id you look up is always the order id you refund** — 18/18
>
> Nobody wrote those. That last one is a real bug class that nobody tests for.
> It breaks when someone reuses a loop variable, and it costs a customer money.
>
> The technique is specification mining — Daikon, 2001, well established for
> ordinary programs. Pointing it at agent trajectories is the bit that seems to
> be new, and it only works because of #2: you can only mine rules from runs
> somebody confirmed were correct.
>
> It also compares the tool schemas you *declare* against how the model
> *actually* uses them. On my own demo corpus it found a parameter I'd been
> paying prompt tokens for on every single request and never once used.
>
> ---
>
> One thing I want to be straight about, because I think the whole category is
> fuzzy on it:
>
> **Replay cannot catch the model changing underneath you.** A recording holds
> what the model said then, not what it would say now. That's physics, not a
> missing feature. There's a separate live mode that makes real calls for
> exactly that. Any tool claiming to catch model drift from a cached response is
> claiming something impossible.
>
> ---
>
> It's 0.1. 407 tests, mypy --strict clean, fully documented — and used by
> nobody but me so far.
>
> That last part is why I'm posting. I'd much rather hear "this is wrong
> because X" than collect stars.
>
> Link in the comments.
>
> #AIAgents #Python #OpenSource

---

**First comment:**

> github.com/NEEL7127/driftcatch
>
> The five-minute path is in docs/getting-started.md. If you try it and get
> stuck, tell me where — that's the most useful thing anyone can do right now.

---

## What this post is careful about

Worth knowing before you edit it:

**It does not claim record/replay is novel.** It isn't — vcrpy,
pytest-agentreplay, agentreplay, graphtrace, trajence, agent-trajectory-diff and
LangChain's agentevals all record runs and diff tool sequences. Claiming
otherwise is the fastest way to get corrected in the comments by someone who
maintains one of them, and you lose the room.

**The uniqueness claim is narrow and defensible:** invariant mining over
approved agent trajectories, the approval gate that makes it sound, and the
declared-vs-actual tool contract report. That is the honest version, and it is
still a strong one.

**The limitation is stated on purpose.** Admitting what it cannot do is the
cheapest credibility available, and it pre-empts the sharpest comment anyone
could leave.

**The ask is feedback, not stars.** Asking for stars reads as marketing. Asking
to be told you're wrong reads as engineering, and engineers respond to it.

## Posting notes

- Tuesday–Thursday, 9–11am in your audience's timezone
- Reply to every comment in the first two hours; that window decides reach
- Don't edit the post in the first hour — it resets distribution
