# sci-harness

The `software-engineer` [Agent Skill](https://agentskills.io): a long-horizon harness that helps small,
cheap models (Haiku, Gemini Flash, GPT-OSS and similar) do long scientific and numerical coding tasks
in large codebases without regressing. It is built for the ways small models fail on long tasks:

| Failure | What the skill does |
| --- | --- |
| Context compaction loses the plan | Plan, tasks, notes, ruled-out causes and "what I was doing" live in `<repo>/.sci/`. `.sci/sci resume` rebuilds the picture, and every command ends with a `NEXT:` line. Optional hooks re-inject it after compaction. |
| "Fixing" a test by weakening it | Acceptance tests are locked once they fail for the right reason. Tests from finished tasks stay protected, and edits to existing tests are scanned for loosened tolerances, removed assertions, skips and changed numbers. |
| Silent numerical regressions | Golden outputs (rtol 1e-12 by default) are checked on every `verify`, along with every earlier task's check and the baseline test suite. |
| Random edits in a loop | Three failed fixes trigger STOP: write hypotheses, test them with logged experiments, then fix. Six trigger ESCALATE. |
| Destroying work with git | `.sci/sci restore` undoes changes and saves the attempt as a patch first. Optional hooks block `git reset --hard`, `git checkout -- .`, `git clean` and similar. |
| Drowning in output | Test output is capped, full logs stay in `.sci/runs/`, and `find`/`map` replace reading whole files. |

Everything goes through one tool, `scripts/sci.py` (standard library only, Python 3.8+, git 2.23+).
`SKILL.md` tells the agent how to use it. `references/` holds templates for convergence, MMS,
conservation and golden tests, a debugging protocol, notes for large codebases, and a complete
example session.

## Install

You need Python 3.8+ and git.

**Linux, macOS, WSL, Git Bash**

```bash
curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh
```

**Windows PowerShell**

```powershell
irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
```

**Windows Command Prompt**

```bat
curl -fsSLo %TEMP%\sci-install.ps1 https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 && powershell -NoProfile -ExecutionPolicy Bypass -File %TEMP%\sci-install.ps1
```

The installer downloads this repository, copies the skill to
`~/.local/share/agent-skills/software-engineer` and links it where each agent on the machine looks
for skills. It does not touch any agent configuration unless you ask for hooks. Run the same command
again to update.

| Agent | Skill location | Hooks (`--hooks`) |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/` | `~/.claude/settings.json`: SessionStart, UserPromptSubmit, PreToolUse (Bash) |
| Codex CLI | `~/.agents/skills/` | `~/.codex/hooks.json` (needs `[features] hooks = true` in `~/.codex/config.toml`) |
| Gemini CLI | `~/.agents/skills/` | `~/.gemini/settings.json`: SessionStart, BeforeAgent, BeforeTool (run_shell_command) |
| Antigravity | plugin in `~/.gemini/config/plugins/software-engineer/` | plugin `hooks.json`: SessionStart, PreInvocation, PreToolUse, Stop |
| OpenCode, pi | `~/.agents/skills/` (OpenCode also reads `~/.claude/skills/`) | none |
| anything else | `--target <skills dir>` | none |

### Options

| Option | Effect |
| --- | --- |
| `--hooks` | also register the hooks (each config file is backed up first) |
| `--remove-hooks` | take the hooks out, keep the skill |
| `--status` | show what is installed where |
| `--uninstall` | remove everything the installer added |
| `--only claude,antigravity` | limit the agents |
| `--copy` | copy instead of symlinking |
| `--project <repo>` | copy the skill into a repository's `.claude/skills/` and `.agents/skills/` for the team |
| `--target <dir>` | also install into another skills directory |
| `--dry-run` | show the changes without making them |

Pass options like this:

```bash
curl -fsSL https://raw.githubusercontent.com/Nag681/sci-harness/main/install.sh | sh -s -- --hooks
```

```powershell
$env:SCI_ARGS = "--hooks"; irm https://raw.githubusercontent.com/Nag681/sci-harness/main/install.ps1 | iex
```

In the Command Prompt, put them after the `.ps1` path. To install a tagged version instead of `main`,
set `SCI_REF` first (`SCI_REF=v3.0.0` before `sh`, `$env:SCI_REF = "v3.0.0"` in PowerShell,
`set "SCI_REF=v3.0.0" &&` in cmd.exe).

From a clone, the same options work with `install.py` directly:

```bash
git clone https://github.com/Nag681/sci-harness
python3 sci-harness/install.py --status
```

An existing copy of an older version is moved to `~/.local/share/agent-skills/backups/`, never
deleted. Restart running agent sessions after installing.

### Windows

- Python: the installer from python.org or `winget install Python.Python.3.12`. The installer and
  the hooks use `py -3`, `python` or `python3`, whichever really runs.
- Without Developer Mode, Windows does not allow symlinks, so each agent gets a copy of the skill.
  Run the installer again after updating.
- The commands sci runs for you (`check`, `verify`, goldens, `run`) go through Git Bash, which comes
  with Git for Windows. Write them with forward slashes and bash quoting:
  `--verify "python -m pytest tests/test_heat.py -q"`. To use another shell:
  `.sci/sci config set shell cmd` (or `pwsh`).
- The tool is `.sci/sci` in PowerShell and Git Bash, and `.sci\sci` in the Command Prompt.

### What the hooks do

The hooks run `scripts/sci_hook.py`. Outside a repository with an initialized `.sci/` (or when
`.sci/sci pause` is on), every hook does nothing, so a global install is safe.

- **Session start / after compaction**: tells the agent the harness is active and shows the
  objective, active task and `NEXT:` step.
- **Each turn** (UserPromptSubmit, BeforeAgent, PreInvocation): a five-line anchor with the
  objective, active task, last check and `NEXT:`.
- **Before shell commands**: blocks commands that destroy uncommitted work or harness state and
  points the agent to `.sci/sci restore`. All other commands keep the agent's normal permission
  flow; the hook never auto-approves anything.
- **Antigravity Stop**: only with `.sci/sci config set autopilot true`. It asks the agent to
  continue while eligible tasks remain, at most twice without progress.

## Using it

Ask the agent to use the skill (in Claude Code: `/software-engineer`), state the goal, and let it
work. In a new repository the agent runs `python3 <skill>/scripts/sci.py init --objective "..."`,
explores, records notes, goldens and a baseline, and plans small tasks. Then it loops
check → lock → implement → verify → done, with one commit per task.

Commands for you, run from the repository root:

```bash
.sci/sci resume        # where things stand
.sci/sci report        # summary with everything that needs your review
.sci/sci review        # test unlocks, spec/scope changes, golden updates, skipped locks
.sci/sci review --clear --yes   # after you have checked them
.sci/sci task unblock T3 --answer "periodic boundaries"   # answer a question the agent parked
.sci/sci pause         # harness and hooks go quiet in this repo (unpause to resume)
.sci/sci purge --yes   # remove the harness from this repo
```

`.sci/` is excluded through `.git/info/exclude` and never committed. `.sci/HANDOFF.md` is a
readable snapshot of the state.

## Development

```bash
python3 -m unittest discover -s tests          # sci, hooks, install.py, install.sh, install.ps1
SCI_TEST_PYTEST_PY=/path/to/python-with-pytest python3 -m unittest discover -s tests   # + pytest cases
```

CI runs the suite on Linux, macOS and Windows (Python 3.8 and current), and runs the install
one-liners above against every pushed commit. `install.sh` and `install.ps1` accept `SCI_SOURCE`
(a local folder or archive) to install without downloading, and `GH_TOKEN` for private forks.

Upgrading from 2.x: `sci.py init` imports tasks from an old `.agent/feature_list.json`. Delete the
old `.agent/` folder afterwards, because Antigravity treats `.agent/` as a customization folder.
See `CHANGELOG.md` for what changed.

## License

MIT
