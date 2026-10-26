# sci-harness

[![CI](https://github.com/Nag681/sci-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/Nag681/sci-harness/actions/workflows/ci.yml)

**Keeps small, inexpensive coding models on track in long scientific coding tasks.**
An [Agent Skill](https://agentskills.io) (`software-engineer`) for Claude Code, Codex, Gemini CLI,
Antigravity, OpenCode and pi.

## Install

**Linux · macOS · WSL**

```bash
curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh
```

**Windows · PowerShell**

```powershell
irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
```

**Windows · Command Prompt**

```bat
curl -fsSLo %TEMP%\sci.ps1 https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 && powershell -NoProfile -ExecutionPolicy Bypass -File %TEMP%\sci.ps1
```

Needs Python 3.8+ and git. Run it again to update. Options such as `--hooks` and `--uninstall` are
under [Install options](#install-options).

## Use

1. Open your coding agent in the repository.
2. Ask it: *"Use the software-engineer skill to add a Verlet integrator."* (Claude Code: `/software-engineer`)
3. The agent plans small tasks and finishes them one commit at a time. Check in whenever you like:

| Command | Shows or does |
| --- | --- |
| `.sci/sci resume` | where things stand and the next step |
| `.sci/sci report` | summary, plus everything that needs your review |
| `.sci/sci review` | test unlocks, spec changes, golden updates |
| `.sci/sci task unblock T3 --answer "..."` | answers a question the agent parked |
| `.sci/sci pause` / `unpause` | keeps the harness quiet in this repo |

## What it fixes

| Small models tend to... | sci-harness... |
| --- | --- |
| lose the plan when the context is compacted | keeps plan, notes and progress in `.sci/`; every command ends with `NEXT:` |
| "fix" tests by weakening them | locks acceptance tests and flags loosened tolerances, removed asserts, skips |
| change numbers silently | checks golden outputs and every finished task on each `verify` |
| loop on random edits | stops them after 3 failed fixes until hypotheses are tested |
| wipe work with git | saves each attempt before undoing; optional hooks block `git reset --hard` |
| drown in output | caps output; full logs stay in `.sci/runs/` |

## How it works

```mermaid
flowchart LR
    R([resume]) --> S[start task] --> F[check fails] --> L[lock tests] --> E[edit + check] --> V[verify] --> D["done: commit"]
    D --> S
    E -- 3 failed fixes --> H["STOP: test hypotheses"] --> E
```

One standard-library Python tool, `scripts/sci.py`, keeps the state in `<repo>/.sci/` (never
committed). `SKILL.md` gives the agent the rules; [`references/`](references/) has test templates
and a [worked example](references/worked-example.md).

<a id="install-options"></a>
<details>
<summary><b>Install options</b></summary>

| Option | Effect |
| --- | --- |
| `--hooks` | add the optional hooks (each config file is backed up first) |
| `--remove-hooks` | remove the hooks, keep the skill |
| `--status` | show what is installed where |
| `--uninstall` | remove everything the installer added |
| `--only claude,codex` | only these agents |
| `--copy` | copies instead of symlinks |
| `--project <repo>` | put the skill into a repository for the whole team |
| `--target <dir>` | also install into another skills directory |
| `--dry-run` | show the changes, make none |

```bash
curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh -s -- --hooks
```

```powershell
$env:SCI_ARGS = "--hooks"; irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
```

- Command Prompt: add the options after `%TEMP%\sci.ps1`.
- A tagged version instead of `main`: set `SCI_REF=v3.0.0` first.
- From a clone: `python3 install.py <options>`.
- Prefer to read the script first? Download it, read it, then run `sh install.sh`
  (Windows: `powershell -ExecutionPolicy Bypass -File install.ps1`).

</details>

<details>
<summary><b>Where it installs</b></summary>

The skill lives in `~/.local/share/agent-skills/software-engineer`. Each agent gets a link to it:

| Agent | Skill folder | Hooks go into |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/` | `~/.claude/settings.json` |
| Codex CLI | `~/.agents/skills/` | `~/.codex/hooks.json` (needs `[features] hooks = true`) |
| Gemini CLI | `~/.agents/skills/` | `~/.gemini/settings.json` |
| Antigravity | `~/.gemini/config/plugins/software-engineer/` | the plugin's `hooks.json` |
| OpenCode, pi | `~/.agents/skills/` | (no hooks) |
| anything else | `--target <dir>` | (no hooks) |

Updates replace what the installer put there itself. Anything else in the way is moved to
`~/.local/share/agent-skills/backups/`, never deleted. Restart running agents after installing.

</details>

<details>
<summary><b>Optional hooks</b></summary>

Off unless you install with `--hooks`. They do nothing outside repositories that use the harness.

| When | What happens |
| --- | --- |
| session start, after compaction | the agent is told where the plan is and what to do next |
| every turn | a short reminder: objective, progress, active task, last check, next step |
| before a shell command | commands that destroy work (`git reset --hard`, `git clean`, ...) are blocked |
| Antigravity stop | with `autopilot` on, the agent is asked to continue while tasks remain |

The hooks never approve anything on their own; your agent's permission settings still apply.

</details>

<details>
<summary><b>Windows notes</b></summary>

- Python from python.org or `winget install Python.Python.3.12`.
- Without Developer Mode, agents get copies instead of links. Run the installer again after updating.
- sci runs check/verify commands with Git Bash: use forward slashes (`tests/test_x.py`).
  Change it with `.sci/sci config set shell cmd` (or `pwsh`).
- The tool is `.sci/sci` in PowerShell and Git Bash, `.sci\sci` in the Command Prompt.

</details>

<details>
<summary><b>Development</b></summary>

```bash
python3 -m unittest discover -s tests
SCI_TEST_PYTEST_PY=/path/to/python-with-pytest python3 -m unittest discover -s tests   # adds pytest cases
```

CI runs the tests on Linux, macOS and Windows and runs the install commands above on every push.
`SCI_SOURCE=<folder or archive>` makes the installers work offline; `GH_TOKEN` lets them fetch a
private fork. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

</details>

## License

MIT
