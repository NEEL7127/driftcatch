# Launch kit

Copy for the launch: the LinkedIn post, the creator outreach email, and how to
target it.

---

## LinkedIn post

Lead with the pain, not the tool. Put the link in the first comment — LinkedIn
suppresses posts with external links in the body.

> You change one word in a prompt.
>
> Your agent stops refunding customers and starts escalating them.
>
> Every test still passes.
>
> I kept hitting this. Evals score the answer, so they pass — the answer still
> looks fine. Observability catches it in production, which is a week too late.
> Nothing was watching the thing that actually changed: what the agent *decided
> to do*.
>
> So I built DriftCatch.
>
> It records one real run of your agent. You approve it — because a recording
> isn't automatically correct, and an unreviewed one would turn a bug into an
> enforced contract. After that it replays from disk: free, offline, identical
> every time. Change the prompt and the build fails, naming the input that
> caused it.
>
> Then it does the thing I actually care about. It reads your approved runs and
> finds the rules nobody wrote down:
>
> "lookup_order always runs before issue_refund"
> "the order id you look up is always the order id you refund"
>
> That second one is a real bug class. Nobody writes a test for it. It breaks
> when someone reuses a loop variable, and it costs a customer money.
>
> One thing I want to be straight about: replay cannot catch the model changing
> underneath you. A recording holds what the model said then, not what it would
> say now. That's physics, not a missing feature — so there's a separate live
> mode that makes real calls. Any tool claiming to catch model drift from a
> cached response is claiming something impossible.
>
> It's 0.1. 404 tests, documented, and nobody but me has used it yet.
>
> That last part is why I'm posting. I'd rather hear "this is wrong because X"
> than collect stars.
>
> Link in the comments.

**First comment:**
> github.com/NEEL7127/driftcatch — the five-minute path is in docs/getting-started.md.
> If you try it and it's confusing, tell me where. That's the most useful thing
> right now.

### Why it's shaped this way

- **The hook is a story, not a feature.** People scroll past "I built a tool".
- **The honest limitation is deliberate.** Admitting what it cannot do is the
  cheapest credibility available, and it pre-empts the top comment.
- **The ask is feedback, not stars.** Asking for stars reads as marketing;
  asking to be told you're wrong reads as engineering.
- **No hashtag soup.** Two or three at most: `#AIAgents` `#Python` `#OpenSource`

### Posting notes

- Attach `driftcatch-launch.mp4` as a **native video**. LinkedIn heavily favours
  native uploads over YouTube links.
- Tuesday–Thursday, 9–11am in your audience's timezone.
- Reply to every comment in the first two hours; that window decides reach.

---

## Creator outreach email

Creators get hundreds of these. The ones that get read are short, lead with the
audience's interest rather than yours, and do the thinking for them.

**Subject:** `A testing tool that learns rules nobody wrote`

> Hi {name},
>
> I watch your {specific thing — name an actual recent video}, so you'll know
> the gap I'm describing better than most.
>
> Everyone testing AI agents is either scoring answer quality or watching
> production dashboards. Almost nobody is gating the thing that actually breaks:
> what the agent *decided to do*, before it merges.
>
> I built DriftCatch to do that. Record one run, approve it, replay it forever
> — free and offline. Change a prompt and the build fails, naming which input
> caused it.
>
> The part I think your audience would find interesting isn't the record/replay
> — that's commoditised. It's that it reads your approved runs and mines the
> rules you never wrote:
>
>     lookup_order always runs before issue_refund        23/23 runs
>     lookup_order.order_id always equals
>       issue_refund.order_id                             18/18 runs
>
> That second rule is a real bug class nobody writes a test for, and it was
> inferred, not authored. It's specification mining — a technique from 2001 —
> applied to agent trajectories.
>
> 60-second demo: {link}
> Repo: github.com/NEEL7127/driftcatch
>
> It's 0.1 and I'm its only user so far, so if you look at it and think it's
> wrong, I'd genuinely rather hear that than nothing.
>
> No obligation either way — thanks for the videos.
>
> Neel

### Rules for sending it

- **Never send it as a blast.** One line referencing a specific video of theirs
  is the difference between read and deleted. If you can't write that line, they
  are the wrong target.
- **Attach nothing.** Link only.
- **Send to the business email** on their YouTube *About* tab, not a personal
  address found elsewhere. The business email exists for exactly this.
- **One follow-up, two weeks later, maximum.** Then stop.
- **Expect ~5% to reply.** That is a normal, healthy rate, not a failure.

---

## Finding the right creators

**I am not giving you a list of email addresses.** Any I produced from memory
would be plausible-looking and wrong, and you would either email nobody or email
the wrong person. Here is the method instead — it takes about an hour and gives
you verified contacts.

### How to get a creator's real business email

1. Open the channel → **About** tab
2. Click **View email address** (there is a captcha; this is the address they
   published for business enquiries)
3. If there is none, check their GitHub profile, personal site, or a Substack —
   many list one

That address is explicitly for this. Anything you have to dig for is not.

### Who to target

Rank by *fit*, not subscriber count. A 15k-subscriber channel whose audience
ships Python agents converts far better than a 2M-subscriber general coding
channel.

**Search YouTube for these, sorted by upload date (last 3 months):**

```
"AI agent testing"          "LLM evaluation tools"
"agent observability"       "open source dev tools weekly"
"pytest plugins"            "LangChain testing"
"AI engineering tools"      "vcrpy"
```

**The three tiers worth your time:**

| Tier | Who | Why |
|---|---|---|
| Best | Channels doing weekly open-source tool roundups | Actively hunting for exactly this; lowest bar |
| Good | Python/pytest-focused educators | Your install path is `pip install` and a pytest plugin |
| Worth a try | AI-engineering channels covering LangChain, agents, RAG | Big audience, but you compete with every launch |

**Channels that came up in research** — treat as leads to verify, not
recommendations. I have not watched these and cannot vouch for fit:

- *AI Agents Studio* — does recurring open-source dev tool roundups, which is
  the best-fit format
- Python educator channels with large Indian audiences: Telusko (Navin Reddy),
  Learn Coding, Indian Programmer

Check each one's last five uploads before writing. If they have not covered a
developer tool recently, skip them.

### Order of operations

Do **not** email creators first.

1. Rotate the API key, run CI on Linux, make the repo public
2. Get 3–5 external people through the five-minute path and fix what confuses
   them
3. Post on LinkedIn, Hacker News (*Show HN*), r/Python, r/LocalLLaMA
4. **Then** email creators — with real usage and a few stars behind you

A creator looking at a repo with 2 stars and no external users will pass. The
same repo three weeks later, with traction and the rough edges sanded off, is a
different pitch. The video is what makes it an easy yes once you are there.
