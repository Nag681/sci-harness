# software-engineer: a long-horizon harness for small coding models

An [Agent Skill](https://agentskills.io) that helps small, cheap models (Haiku, Gemini Flash,
GPT-OSS and similar) do long scientific and numerical coding tasks in large codebases without
regressing. It is built for the ways small models fail on long tasks:

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

```bash
python3 install.py            # every agent found on this machine; no config files are edited
python3 install.py --hooks    # also add the hooks (writes a backup of each config first)
python3 install.py --status   # what is installed where
python3 install.py --uninstall
```

The installer copies this folder to `~/.local/share/agent-skills/software-engineer` and links it
where each agent looks for skills:

| Agent | Skill location | Hooks (`--hooks`) |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/` | `~/.claude/settings.json`: SessionStart, UserPromptSubmit, PreToolUse (Bash) |
| Codex CLI | `~/.agents/skills/` | `~/.codex/hooks.json` (needs `[features] hooks = true` in `~/.codex/config.toml`) |
| Gemini CLI | `~/.agents/skills/` | `~/.gemini/settings.json`: SessionStart, BeforeAgent, BeforeTool (run_shell_command) |
| Antigravity | plugin in `~/.gemini/config/plugins/software-engineer/` | plugin `hooks.json`: SessionStart, PreInvocation, PreToolUse, Stop |
| OpenCode, pi | `~/.agents/skills/` (OpenCode also reads `~/.claude/skills/`) | none |
| anything else | `--target <skills dir>` | none |

Other options:

- `--only claude,antigravity` limits the agents.
- `--copy` copies instead of symlinking (used automatically where symlinks fail, such as on Windows
  without developer mode).
- `--project <repo>` copies the skill into a repository's `.claude/skills/` and `.agents/skills/` so
  the team gets it.
- `--remove-hooks` takes the hooks out and keeps the skill.
- `--dry-run` shows the changes without making them.

An existing copy of an older version is moved to `~/.local/share/agent-skills/backups/`, never
deleted. Restart running agent sessions after installing.

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
python3 -m unittest discover -s tests          # sci, hooks and installer
SCI_TEST_PYTEST_PY=/path/to/python-with-pytest python3 -m unittest discover -s tests   # + pytest cases
```

Upgrading from 2.x: `sci.py init` imports tasks from an old `.agent/feature_list.json`. Delete the
old `.agent/` folder afterwards, because Antigravity treats `.agent/` as a customization folder.
See `CHANGELOG.md` for what changed.
