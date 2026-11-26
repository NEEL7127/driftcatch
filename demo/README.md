# Demo assets

Everything used to show DriftCatch: the launch video, the README GIFs, and the
scripts that produce them.

All of it is **generated from source**, not hand-edited. A demo you cannot
re-render is a demo that goes stale the first time the CLI changes.

```
demo/
  SCRIPT.md          the 60-second script, scene by scene, with direction
  video/             Remotion project - the animated video
  tapes/             VHS tapes - real terminal recordings
  out/               rendered output (gitignored)
```

---

## The animated video

Built with [Remotion](https://remotion.dev): React components rendered to MP4.
Chosen over a video editor because the whole thing is diffable, and because
every command shown can be checked against the real CLI.

```bash
cd demo/video
npm install
npm run studio          # live preview at localhost:3000
```

### Rendering

```bash
npm run build           # 60s, 1920x1080  -> out/driftcatch-launch.mp4
npm run build:short     # 30s, 1920x1080  -> out/driftcatch-short.mp4
npm run build:vertical  # 30s, 1080x1920  -> out/driftcatch-vertical.mp4
npm run build:gif       # 10s loop        -> out/driftcatch-drift.gif
```

| Composition | Use |
|---|---|
| `Launch` | LinkedIn, X, the repository's social preview |
| `Short` | Feeds where attention is shorter |
| `Vertical` | Shorts, Reels, TikTok |
| `DriftCatchGif` | The README hero |

### Fonts

Inter and JetBrains Mono are loaded through `@remotion/google-fonts` rather
than assumed to be installed. The first render of this video came out in a
fallback face, and the tell was a malformed `^` glyph. Typography that depends
on the rendering machine would be a poor look for a project about determinism.

### A note on the browser

Remotion renders through headless Chromium. If its bundled download fails to
extract — Windows Defender is a common cause, and the symptom is a re-download
on every run that never produces a file — point it at any installed Chromium:

```bash
npx remotion render Launch out/launch.mp4 \
  --browser-executable="C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
```

Or set `REMOTION_BROWSER_EXECUTABLE` once in your environment.

---

## The terminal GIFs

Built with [VHS](https://github.com/charmbracelet/vhs). The tapes record a
**real** terminal running **real** commands — unlike the video, where the
terminal is drawn. For the README that authenticity is worth the setup cost: a
GIF of a tool that was animated rather than run is a GIF you cannot trust.

```bash
winget install charmbracelet.vhs    # also needs ttyd and ffmpeg

vhs demo/tapes/drift-catch.tape     # the regression being caught
vhs demo/tapes/drift-learn.tape     # rules mined from the corpus
```

Run from the repository root, with the sandbox seeded first:

```bash
python demo/seed_demo.py
```

| Tape | Shows |
|---|---|
| `drift-catch.tape` | A passing replay, then a caught regression, then `drift bisect` naming the cause |
| `drift-learn.tape` | `drift learn` mining rules, then `drift contracts` |

---

## Keeping it honest

Before publishing anything from here:

- [ ] Every command shown exists — check against `drift --help`
- [ ] No claim that replay catches model updates. It cannot, and the project's
      credibility rests on saying so
- [ ] Readable with the sound off, and at phone width
- [ ] The first frame works as a static thumbnail

The commands in the video are covered by the repository's documentation tests,
which fail if a documented command does not exist. The video is not yet under
that check — verify it by hand until it is.
