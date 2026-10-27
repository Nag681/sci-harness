# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.1.1] - 2026-10-27

### Fixed
- STOP lifted after two hypotheses and one written verdict, even if no experiment was run. It now also needs at
  least one `.sci/sci run` since the STOP, and a run can lift it once the hypotheses are in place.
- The test-weakening guard missed `assert err < 1e-6` becoming `assert err <= 1e-1`, a number change after an
  inserted comment (lines were paired by position), and a loosened tolerance when another changed line in the
  file had a looser one. Lines are now paired by similarity and tolerances are also compared line by line. It
  also flags new `if False:` blocks and `try/except` that swallows assertion failures in Python tests.
- Golden outputs read `12.5s` as 12 and `1.2.3` as 1.2. A number glued to a unit or inside a version string is
  now skipped. `golden add` notes exact zeros when `atol` is 0, since any round-off there fails.
- `sci order` with resolutions given finest first judged the coarsest pair and skipped the monotonicity
  warning. Resolutions are now sorted coarse to fine.
- `purge` and `review --clear` refusals printed the `--yes` command on the `DO:` line, which agents are told
  to run. The `DO:` line now tells the agent to leave it to the human.
- Hooks: a note or commit message mentioning `git stash`, `git restore` or `git clean -fd` was blocked, while
  `bash -c "git stash"` and `git checkout HEAD <file>` were not. Quoted text is now checked only when it is a
  command of its own, `git checkout <ref> <path>` is blocked, and so are `sci purge` and `sci review --clear`.
- `--scope a.py,b.py` is two entries, like `--dep`. Changes to `pyproject.toml`, `setup.cfg`, `*.cmake`,
  `Cargo.toml` and `*.rs` trigger the configured rebuild. A `%` in the Python path no longer breaks the Windows
  launcher.

## [3.1.0] - 2026-10-26

### Changed
- The skill is now named `sci` (`/sci` in Claude Code) and the project Sci-Harness. The skill installs to
  `~/.local/share/agent-skills/sci` and each agent's `skills/sci` folder. Running the installer again moves an
  install made under the old name, `software-engineer`: it removes the links, copies, Antigravity plugin and
  installed copy it made there, points existing hooks at the new location and leaves everything else alone.
  `--status` lists what is left under the old name.
- README: written for newcomers, with how-to steps, a small bill-splitting example that uses real sci output, and
  the architecture.

### Fixed
- After `verify` failed only because a golden changed, `NEXT:` said "Fix the code" even once the change had
  been justified with `golden update`. It now says to fix the code or justify the change, and after
  `golden update` it says to run `verify` again.
- `golden update` no longer warns about uncommitted changes, which are expected when re-recording.

## [3.0.0] - 2026-10-26

Rewritten for one job: keeping small models productive on long-horizon scientific and numerical
coding in large codebases, across context compactions. Breaking: the 2.x scripts, `.agent/` state
folder and generic DevSecOps/CI-CD material are gone.

### Added
- `scripts/sci.py`: a single standard-library tool that keeps all state in `<repo>/.sci/`
  (git-excluded) and drives a plan → task → check → lock → verify → done loop, one commit per task.
- Compaction recovery: `resume`, a `[sci] ... NEXT:` footer after every command, `now`, and
  `.sci/HANDOFF.md` regenerated on every change.
- Regression guards: locked acceptance tests; tests locked or created by a finished task stay
  protected; edits to existing tests are scanned for loosened tolerances, removed assertions,
  skips, changed numbers, duplicate test names and early returns; golden numerical outputs;
  every earlier task's check re-runs on `verify`; `baseline` enrolls the test suite that `verify`
  re-runs and records pre-existing failures; hand edits to task specs are detected.
- Debugging discipline: strike counter, STOP after 3 failed fixes (hypotheses + logged experiments
  required), ESCALATE after 6; refuted hypotheses shown as RULED OUT after compactions.
- Safe undo: snapshots through a private git index (the user's staging area is untouched);
  `restore` saves the current attempt as a patch first and restores locked files exactly.
- Large-codebase helpers: `find`, `map`, typed notes, capped output with full logs in `.sci/runs/`,
  timeouts that kill the whole process group, automatic rebuilds for compiled code (`build_cmd`).
- Science helpers: `golden`, `order` (observed convergence order), `run` (experiments with the
  repo on PYTHONPATH, cited by run number).
- `lock --none` and automatic "nothing to lock" for tasks whose verify command names no test
  file; files in a task's scope are never locked.
- References: scientific verification templates, debugging protocol, large codebases, and a
  recorded example session.
- `scripts/sci_hook.py`: hooks for Claude Code, Codex, Gemini CLI and Antigravity that re-anchor
  the agent each turn and after compaction and block destructive git commands. They do nothing
  outside repositories with `.sci/`.
- `install.py`: installs the skill for every detected agent (Claude Code, Codex, Gemini CLI,
  Antigravity, OpenCode, pi, or any skills directory), with opt-in hooks, `--status` and
  `--uninstall`.
- Online installers: `install.sh` (`curl ... | sh`) for Linux, macOS, WSL and Git Bash, and
  `install.ps1` (`irm ... | iex`, or curl.exe and `powershell -File` in cmd.exe) for Windows. Both
  accept install.py's options, `SCI_REF` to pin a version and `SCI_SOURCE` to install offline.
- Windows support: sci runs commands with Git Bash (never WSL's `bash.exe`), else cmd.exe, and the
  new `shell` config key overrides it; `.sci\sci.cmd` launcher for cmd.exe and PowerShell; timeouts
  kill the whole process tree; per-repo locking; UTF-8 output; hooks and printed commands call
  `py -3`, `python` or `python3`, whichever really runs; the installed copy lives in
  `~/.local/share` on every OS.
- Test suites: `tests/test_sci.py`, `tests/test_install.py` and `tests/test_bootstrap.py`, run by
  CI on Linux, macOS and Windows.

### Changed
- `SKILL.md` rewritten as short imperative rules, a table mapping tool messages to actions, and a
  NEXT-driven loop.
- State moved from `.agent/` (an Antigravity customization folder name) to `.sci/`. `sci.py init`
  imports tasks from an old `.agent/feature_list.json`.

### Removed
- `scripts/event_logger.py`, `security_audit.py`, `security_audit.sh`, `test_runner.sh`,
  `release_manager.sh`, `init_harness.sh`; `examples/`, `resources/`, `SESSION_LOG.md`,
  `tests/test_all.py`.
- References `cicd-and-supply-chain.md`, `security-and-cwe.md`, `event-logging-and-git.md`,
  `testing-handbook.md`, `debugging-playbook.md`, `scientific-computing-and-vv.md` (the scientific
  and debugging material lives on in the new references).

## [2.x and earlier]

Unpublished predecessors named `software-engineer`: a general software-engineering guide (ADRs, event
logging, security audits, CI/CD). Nothing from them remains in 3.0.0 except the task import from
`.agent/feature_list.json`.
