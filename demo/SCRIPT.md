# DriftCatch — launch demo script

**Target:** 60 seconds · 1920×1080 · silent-first (LinkedIn autoplays muted)
**Rule:** every frame must be readable with the sound off. Captions carry the
story; narration is a bonus, not a dependency.

---

## The strategy

Most dev-tool launch videos open with the tool. That is backwards — nobody cares
about a tool until they recognise the pain.

This one spends the first 14 seconds on a failure the viewer has personally had,
and does not name the product until second 20. By the time the logo appears,
the viewer already wants it.

Three things it must land, in order:

1. **Agents drift silently.** The viewer nods — they have shipped this bug.
2. **Record once, replay forever.** The mechanism, in one glance.
3. **It learns rules you never wrote.** The reason to pick this over vcrpy.

One thing it must *not* do: claim replay catches model updates. The product's
credibility rests on being honest about that, and the video should not spend it.

---

## Timing

| # | Time | Beat |
|---|---|---|
| 1 | 0:00–0:05 | The one-word change |
| 2 | 0:05–0:13 | What it silently did |
| 3 | 0:13–0:18 | "Agents don't fail loudly. They drift." |
| 4 | 0:18–0:24 | Product reveal |
| 5 | 0:24–0:33 | Record once |
| 6 | 0:33–0:43 | Replay forever, and catch it |
| 7 | 0:43–0:54 | Rules you never wrote |
| 8 | 0:54–1:00 | Call to action |

---

## Scene 1 — The one-word change · 0:00–0:05

**On screen.** A prompt diff, dark editor. One token changes, highlighted red →
green. Everything else stays still.

```diff
  You are a refund agent.
- Resolve the customer's issue.
+ Resolve the customer's issue carefully.
```

**Caption:** `You changed one word.`

**Direction.** Hold. Let the viewer read the whole diff. The stillness is the
point — this looks harmless, which is exactly the trap.

---

## Scene 2 — What it silently did · 0:05–0:13

**On screen.** Two trajectories, stacked. The top one is green and settled. The
bottom one types itself out and the final node lands in red.

```
before   lookup_order  →  check_eligibility  →  issue_refund
after    lookup_order  →  check_eligibility  →  escalate_to_human
```

**Caption:** `Your agent stopped refunding customers.`
**Then (0:10):** `Every test still passed.`

**Direction.** The second caption is the knife. Beat of silence before it. The
red node pulses once.

---

## Scene 3 — The thesis · 0:13–0:18

**On screen.** Everything clears. One line, centred, large.

> **Agents don't fail loudly. They drift.**

**Direction.** Full stop. No motion except a slow fade up. This is the line
people screenshot and quote, so give it room.

---

## Scene 4 — Product reveal · 0:18–0:24

**On screen.** Wordmark, then the positioning line under it.

> **DriftCatch**
> Catch the moment your agent's behaviour changes.

**Caption:** `A pre-merge CI gate for agent behaviour.`

**Direction.** First time the name appears — 18 seconds in, by design.

---

## Scene 5 — Record once · 0:24–0:33

**On screen.** Editor pane, code types in at a readable speed.

```python
import driftcatch as drift

def test_refund():
    with drift.take("refund_1182") as take:
        agent("refund order 1182")

    take.assert_tools().called_in_order("lookup_order", "issue_refund")
```

Then a terminal slides up:

```
$ pytest
recorded refund_1182 · PENDING

$ drift approve refund_1182 --by you
approved refund_1182
```

**Caption:** `Record one real run.`
**Then:** `A human approves it. Recording isn't approving.`

**Direction.** The approval beat is a differentiator, not filler — competitors
record, they don't gate. Give the `PENDING → approved` transition a colour
change the eye catches.

---

## Scene 6 — Replay, and catch it · 0:33–0:43

**On screen.** Terminal. A passing replay, a broken one, then the bisect.

```
$ pytest
1 passed   replayed from disk · no API calls · $0.00

# someone changes one word in the prompt
$ pytest
FAILED test_refund.py   behaviour changed

$ drift bisect refund_1182 --previous
  golden:     lookup_order → check_eligibility → issue_refund
  candidate:  lookup_order → check_eligibility → escalate_to_human
                                                 ─────────────────

  ROOT CAUSE: the system prompt
    - issue.
    + issue carefully.
```

**Caption:** `Replay forever. Free, offline, deterministic.`
**Then:** `Caught before merge — and it names the cause.`

**Direction.** This is the climax, and every line of it is **real output** from
`drift bisect`, not a composite. The first cut invented a `DriftDetected`
header over a trajectory diff; no single command prints that, and a video that
fabricates output for a tool about honest failure reporting is the wrong kind of
irony.

The real thing is also better. `ROOT CAUSE` names the input, and the
`- issue. / + issue carefully.` diff closes the loop with Scene 1 literally —
the viewer watches the one word they saw change turn out to be the cause.

Draw the underline left to right under `escalate_to_human`, then hold a beat
before `ROOT CAUSE` lands.

**Verify before publishing.** Reproduce it:

```bash
python demo/seed_demo.py
cd demo/sandbox
pytest -q                       # passes
python change_prompt.py
pytest -q --drift-update        # re-records, trajectory changes
drift bisect refund_1182 --previous
```

## Scene 7 — Rules you never wrote · 0:43–0:54

**On screen.** Terminal.

```
$ drift learn
Mined 15 rules from 23 approved recordings
```

Rules fade in one per beat:

```
✓ lookup_order always runs before issue_refund          23/23
✓ validate_customer is called in every run              23/23
✓ issue_refund is always the last tool                  18/18
✓ lookup_order.order_id always equals
  issue_refund.order_id                                 18/18
```

**Caption:** `Nobody wrote these. They were learned from runs you approved.`

**Direction.** Hold on the fourth rule — the order-id equality is the one that
makes engineers sit up, because it is a real bug class nobody tests for. Give it
an extra beat and a subtle highlight.

---

## Scene 8 — Call to action · 0:54–1:00

**On screen.**

> **DriftCatch**
> `pip install driftcatch`
> github.com/NEEL7127/driftcatch

**Caption:** `Record once. Replay forever. Catch the drift.`

---

## Narration (optional voiceover)

Keep it under 110 words so it fits in 60 seconds without rushing.

> You changed one word in your prompt.
> Your agent stopped refunding customers and started escalating them.
> Every test still passed.
> Agents don't fail loudly. They drift.
> DriftCatch records one real run of your agent. You approve it — because a
> recording isn't automatically correct. After that it replays from disk:
> free, offline, and identical every time.
> Change the prompt, and the build fails, naming the input that caused it.
> Then it reads your approved runs and finds the rules you never wrote — like
> the fact that the order you look up is always the order you refund.
> DriftCatch. Catch the moment your agent's behaviour changes.

---

## Cut-downs

**30 seconds** — Scenes 1, 2, 3, 6, 8. Drop the setup; keep pain → catch → CTA.

**15 seconds (vertical, Shorts/Reels)** — Scene 2 trajectory swap, the thesis
line, the `DriftDetected` block, the logo. Crop to 1080×1920, bump font sizes by
roughly 40%.

**GIF for the README** — Scene 6 only, looped. That is the money shot: a passing
replay followed by a caught regression.

---

## Design

| | |
|---|---|
| Background | `#0B0E14` near-black |
| Surface | `#141921` |
| Text | `#E6EDF3` |
| Dim | `#7D8590` |
| Accent (brand) | `#4D9FFF` |
| Pass | `#3FB950` |
| Drift | `#F85149` |
| Warn | `#D29922` |
| Mono | JetBrains Mono, fallback Cascadia Code, Consolas |
| Sans | Inter, fallback system-ui |

Code is never below 28px at 1080p. If a block does not fit, cut the block — do
not shrink the type. Someone is watching this on a phone in a feed.

---

## Checks before publishing

- [ ] Readable muted, start to finish
- [ ] Readable at 360px wide (phone in feed)
- [ ] Every command shown actually exists — verify against `drift --help`
- [ ] No claim that replay catches model updates
- [ ] First frame works as the static thumbnail
- [ ] Under 60s for LinkedIn native autoplay
