# The money shot: a passing replay, then a caught regression.
#
# This is the README hero. Unlike the rendered video, every character here is
# real output from a real run - which is the point. A demo GIF that was drawn
# rather than recorded is a demo you cannot trust, and this project's entire
# argument is about not trusting things you have not verified.
#
#   vhs demo/tapes/drift-catch.tape
#
# Requires: vhs, ttyd, ffmpeg
#   winget install charmbracelet.vhs
#
# Run from the repository root, with the demo sandbox already seeded:
#   python demo/seed_demo.py

Output demo/out/drift-catch.gif
Output demo/out/drift-catch.mp4

Set Shell powershell
Set FontFamily "JetBrains Mono"
Set FontSize 22
Set Width 1400
Set Height 760
Set Padding 28
Set Theme { "name": "DriftCatch", "background": "#0B0E14", "foreground": "#E6EDF3", "cursor": "#4D9FFF", "black": "#0B0E14", "red": "#F85149", "green": "#3FB950", "yellow": "#D29922", "blue": "#4D9FFF", "magenta": "#BC8CFF", "cyan": "#39C5CF", "white": "#E6EDF3" }
Set TypingSpeed 55ms

Hide
Type "cd demo/sandbox" Enter
Type "clear" Enter
Show

# --- 1. the golden replays, free and instant ---------------------------

Type "pytest -q"
Sleep 400ms
Enter
Sleep 2.5s

Type "# replayed from disk. no API calls. $0.00"
Sleep 600ms
Enter
Sleep 1.2s

# --- 2. one word changes in the prompt ---------------------------------

Type "python change_prompt.py"
Sleep 400ms
Enter
Sleep 1.8s

# --- 3. the regression is caught, and named ----------------------------

Type "pytest -q"
Sleep 400ms
Enter
Sleep 4.5s

Type "drift bisect refund_1182 --previous"
Sleep 400ms
Enter
Sleep 4s
