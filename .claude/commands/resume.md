# Resume

The previous session may have been interrupted mid-task. Reconstruct context and get oriented for the current session.

1. Run `git log --oneline -10` to see recent commits and identify any WIP messages.
2. Run `git status` to check for uncommitted changes.
3. Run `git diff HEAD` to read the full diff of any uncommitted work.
4. If there are uncommitted changes or a WIP commit at the tip, read the affected files to understand the incomplete state.
5. Check `git log origin/master..HEAD --oneline` to see any commits not yet pushed to GitHub.

Then give the user a concise summary:
- What was being worked on (based on commit messages and diff)
- What appears complete vs. incomplete
- What the logical next step is

Ask the user to confirm the next step before proceeding.
