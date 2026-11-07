# Local autonomous CI watchdog

`tools/ci/watch.py` monitors the latest commit on `main` every 120 seconds. It uses
GitHub CLI (`gh`) to inspect the latest `main` run of each **active** workflow.
A workflow may have its latest run on an earlier commit because of path filters.
That failure remains actionable until the workflow has a newer successful run.
Pull-request runs, older superseded failures, and deleted workflow files are ignored. Queued/running jobs
are allowed to finish. A missing run is reported explicitly (path filters may
prevent workflows from starting); it is never treated as a successful build.

When it sees a new failure, the watchdog starts the local Codex CLI in an isolated
Git worktree. Codex is instructed to inspect failed logs, fix the cause, and run
relevant tests without disabling or weakening checks. The controller then checks
whitespace in the diff, requires a structured agent report listing successful
test commands, commits, and pushes to `main` only if the remote branch has not changed.
It never forces a push or reruns successful jobs. Each run/attempt is handled once;
automatic repairs are capped at three until all active workflows have successful
latest main runs. Missing/skipped workflows do not reset that counter. The state
records each observed run SHA alongside the current main SHA.

## Start

Requirements: Linux/Termux, Python 3.10+, Git, `gh auth status`, and an authenticated `codex`
CLI (`codex login status`). Existing ChatGPT CLI authentication is used; no new
OpenAI API key or GitHub secret is needed. Codex usage limits still apply. The
GitHub account must be able to read Actions logs and push to `main`; branch
protection may reject publication. This service is local and requires this
machine and its network connection to remain available.

Run these commands from the repository root:

```sh
# Observe once, without agent execution or repository changes.
python tools/ci/watch.py --once
# Inspect repair eligibility without starting the service.
python tools/ci/watch.py --dry-run

# Start the autonomous repair loop. This authorizes automatic commits/pushes.
nohup python -u tools/ci/watch.py > "$HOME/ose-ci-watch.out" 2>&1 &

python tools/ci/watch.py --status
python tools/ci/watch.py --stop
```

The lock prevents two watcher processes sharing the same state directory.
`--status` reports its location: normally `~/.local/state/ose-ci-watch/<repo-id>/`.
It contains `state.json`, `watch.log`, one agent log and one retained worktree per
repair. The original checkout and uncommitted changes are untouched. Inspect
agent logs and retained worktrees if a repair fails or publication is rejected.
The `watch.py` process runs the version loaded when it started; restart it after
updating this script. Keep only one instance/state directory per repository.

To choose a different interval or repair limit, add `--interval 120
--max-attempts 3`. A custom `--state-dir` must be outside repository worktrees;
use the same option for `--status` and `--stop`. Stop before manually changing
state. After resolving an exhausted incident, archive `state.json` and restart
only when further automatic repair attempts are intended. Worktrees and branches
are deliberately retained for review; remove them manually when no longer needed.

The script cannot guarantee that an agent's fix is correct; review its commits
and tests. The controller checks the agent report; it does not independently
repeat those tests. GitHub CI validates the published commit. Codex runs with the workspace-write sandbox, network access, and no
interactive approval prompts, and is instructed never to access credential
stores or publish directly. Agent logs may include repository code and CI output;
the state directory is private to the current user. GitHub/network/authentication
failures are reported and retried without consuming repair attempts. An agent
failure consumes one attempt and is not retried against the same run.

On Android/Termux, `nohup` does not prevent sleep or OS process termination. Use
`termux-wake-lock` if installed and configure battery settings for Termux, or run
on an always-on machine. Nothing can monitor or repair while the machine is off;
there is no GitHub-hosted scheduler and no background process is installed by
adding these files.

The agent uses the official [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).

Policy tests (no network, agent invocation, or publication):

```sh
python -m unittest discover -s tools/ci/tests -v
```
