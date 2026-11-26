# The differentiator: rules nobody wrote.
#
# Record/replay is commoditised - vcrpy, pytest-agentreplay and half a dozen
# others do it. This is the tape that shows the thing none of them do, so it is
# the one worth putting in a launch post rather than just the README.
#
#   vhs demo/tapes/drift-learn.tape

Output demo/out/drift-learn.gif
Output demo/out/drift-learn.mp4

Set Shell powershell
Set FontFamily "JetBrains Mono"
Set FontSize 22
Set Width 1400
Set Height 820
Set Padding 28
Set Theme { "name": "DriftCatch", "background": "#0B0E14", "foreground": "#E6EDF3", "cursor": "#4D9FFF", "black": "#0B0E14", "red": "#F85149", "green": "#3FB950", "yellow": "#D29922", "blue": "#4D9FFF", "magenta": "#BC8CFF", "cyan": "#39C5CF", "white": "#E6EDF3" }
Set TypingSpeed 55ms

Hide
Type "cd demo/sandbox" Enter
Type "clear" Enter
Show

Type "# 23 approved recordings. nobody wrote a single assertion about them."
Sleep 800ms
Enter
Sleep 1.2s

Type "drift learn"
Sleep 400ms
Enter
Sleep 5s

Type "# the last one is a real bug class: the id you look up"
Enter
Type "# is the id you refund. nobody tests for that."
Sleep 600ms
Enter
Sleep 2.5s

Type "drift contracts"
Sleep 400ms
Enter
Sleep 4.5s
