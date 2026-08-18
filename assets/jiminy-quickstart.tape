# jiminy-quickstart.tape
# Install vhs: https://github.com/charmbracelet/vhs
# Run (from the repo root):
#   vhs assets/jiminy-quickstart.tape
# Outputs:     assets/jiminy-quickstart.gif

Output assets/jiminy-quickstart.gif

Set Shell "bash"
Set FontSize 16
Set Width 1000
Set Height 560
Set Theme "Dracula"
Set Padding 20
Set TypingSpeed 60ms

Type "pip install jiminy-sdk"
Enter
Sleep 1.5s

Type "python quickstart.py"
Enter
Sleep 800ms

# quickstart.py should print progress lines ending in the verdict, e.g.:
#   Building trace...
#   Submitting to Jiminy API...
#   Verdict: approved
Sleep 3s

Sleep 2s
