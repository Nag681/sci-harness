# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- The README now shows what it left out. Another agent opened in the same folder picks up the same plan, locked
  checks and ruled-out causes, even a test run that was still going. The report lists every rule the agent bent.
  Questions do not stop the job, test runs outlive the agent, numerical results are saved before a change, and
  each step is one commit. Three lines say more exactly what happens: after STOP the agent must test the cause,
  and the report says when it did not; sci skips checks that passed on the same code; sci itself never sends
  code anywhere.

## [3.4.2] - 2026-11-22

Fixes from a third review by Gemini, this time of 3.4.1, and hooks for OpenCode. Each fix was reproduced
before it was made and has a test that fails without it.

### Added
- OpenCode hooks. `install.py --hooks` writes `~/.config/opencode/plugins/sci.js`, a plugin that imports
  `scripts/opencode_plugin.mjs` from the installed skill. On OpenCode 1.x (1.18.29 and newer) and 2.x it blocks
  the same destructive commands as the other agents' hooks, and the model reads why. It also puts the objective
  and the next step into every request's system prompt, so compaction cannot drop them. `--remove-hooks` and
  `--uninstall` delete the file, and a `sci.js` that the installer did not write is left alone. Checked live on
  OpenCode 2.0.31 with a stand-in model; OpenCode 1.x is checked only against a stand-in of its plugin API.
- When `.sci/state.json` or `.sci/config.json` was edited by hand rather than by a sci command, sci now warns and
  lists the changed fields for the human (for example `T2.strikes` or `ignore_globs`). Nothing is blocked.
- Code changed while STOP is on is counted. The check says so, and `report` lists the count for the human.
  STOP still does not block a check.

### Fixed
- Rust: an attribute such as `#[cfg(any())]` above a `proptest!` block outside a test module lay outside the
  locked region, so it could switch those tests off unnoticed. 3.4.1 had fixed this for `#[test]` and
  `#[cfg(test)]` only. Paths such as `proptest::proptest!` count too. Regions recorded by 3.4.1 are upgraded
  when sci loads the state, and the upgrade flags nothing.
- `restore --to start` when the files already matched the start (reverted by hand) reset the strikes. The check
  that confirms the state then counted as a fix attempt, and Strike 1 was used up at once.
- A check that rewrote an untracked file, which sci then ignored as output, saved the pass of a rerun with the
  files as they were before. `verify` could not reuse that pass later.
- Rerunning only the failing jest, vitest, `node --test` or `bun test` files kept a folder the suite command
  named, as in `npx jest tests/ tests/cart.test.ts`, and those runners then ran the whole folder again.
  Folders and files that hold the failing tests are now left out. A flag's value, as in `--rootDir src`, stays.

### Changed
- The skill description says when to use sci in its first sentence and that it is not for one-line edits,
  questions or read-only exploration.
- The README is written for anyone who uses a coding agent, not just developers, in plain Markdown: what goes
  wrong on long jobs, how Sci-Harness catches it, and a one-line install. The technical material (the rules,
  commands, settings, install options, hooks and Windows notes) moved to `docs/how-it-works.md`.
- The installer no longer copies `docs/` into installed skills.

## [3.4.1] - 2026-11-22

Fixes for a second review by Gemini, this time of 3.4.0. The most serious finding was a regression in 3.4.0
itself: ignoring the files a check writes could stop sci from guarding the tests. Every finding below was
reproduced before it was fixed and has a test.

### Fixed
- 3.4.0 ignored the files a check writes, and ignored a whole folder when the check wrote five or more files in
  it, even a test folder such as `tests/`. Locked and protected tests in that folder were then no longer
  guarded, so a weakened test could pass `verify` and `done`. Outputs are now ignored one file at a time. A folder
  is ignored as a whole only when it is outside the task scope and holds no test, tracked or guarded file. A
  guarded file that the check itself rewrites is output, not a test: it stops being guarded and is listed for
  human review.
- After a verify that ignored new outputs, `done` saw different files from the ones verified and asked for
  another verify. `check` and `verify` now record the files after ignoring outputs.
- A file that the task scope names (a report the task must produce) was ignored if an earlier check had written
  it, so `done` never committed it. Naming it in the scope (`task start`, `task scope`, `task amend --scope`)
  takes it back. `done` also commits untracked files the scope names that existed before the task started.
- Rust: an attribute placed above a test attribute, such as `#[cfg(feature = "x")]` above `#[cfg(test)]` or
  `#[ignore]` above `#[test]`, lay outside the locked region, so it could turn locked tests off without being
  noticed. A region now starts at the item's first attribute. Regions recorded by 3.4.0 are upgraded when sci
  loads the state, and the upgrade flags nothing.
- Rust: the lexer misread an escaped quote character (`'\''`) and C strings (`c"..."`, `cr#"..."#`), so a brace
  after one could end a test region in the wrong place.
- `restore --paths` on a Rust file whose tests no longer match the locked ones (a test added or removed) put
  back the whole file, code included, and reported only "Restored 1 file". It now warns that the code was
  restored too and names the saved patch that holds the previous version.
- The check that confirms a `restore --to start` counted as a failed fix attempt and cost a strike. The count
  of fresh starts also carried over when a blocked task was started again, so NEXT asked for the human too early.
- jest adds a time to slow files (`FAIL src/cart.test.ts (5.1 s)`), and sci did not read those failures.
- `cargo test --lib` in a workspace (`--workspace`, `--all`, or a virtual manifest without `-p`) runs one target
  per crate and stops at the first failing crate. sci treated it as a single target that never stops early.
- `unlock` without paths skipped tests locked inside Rust source files, and `unlock` with no active task did not
  update protected tests in source files. A test that a task locked, then unlocked to change, was no longer
  guarded after `done`; it now is.
- `done` counted as the final verify even when a commit hook changed files after the commit.
- `lock` found no file for a `cargo test tests::name` filter (the crate root's own tests). It now locks
  `src/lib.rs` or `src/main.rs`.
- Narrowing `test_globs` stopped treating some files as tests without saying so. Those files are now listed for
  human review.
- The optional hooks let `/bin/rm -rf .sci`, `\rm -rf .sci`, `bash -c -- "git stash"` and `sh -c $'git stash'`
  through.
- `install.py` moved two old copies backed up in the same second (`--project` with both `.claude/skills` and
  `.agents/skills`) to one backup folder, so the second ended up inside the first. Each now gets its own folder.
- `install.py` reported Codex hooks as disabled unless `config.toml` had `[features] hooks = true`. Current Codex
  runs hooks by default, so they now count as off only when `config.toml` sets `hooks = false`.
- `install.ps1` split `SCI_ARGS` at every space, which broke quoted values such as `--project "C:\My Repo"`.
- `install.sh` failed with a bare `cp` error when `SCI_SOURCE` named a file that does not exist; it now says
  what is wrong.

### Added
- After a failed verify, `check` reruns only the failing test files of a jest, vitest, `node --test` or
  `bun test` suite, and of `npm test` when package.json's test script runs jest or vitest. It already did this for
  pytest, unittest and cargo.
- A check that rewrites a file in the task's scope prints a note: a file that changes on every run never matches
  the verified version.

## [3.4.0] - 2026-11-22

Rust and JavaScript/TypeScript are first-class next to Python, `verify` no longer passes a suite that hides new
failures behind known ones, and several loops that stopped an agent that did nothing wrong are gone. Prompted by a
review from Gemini; every finding below was reproduced before it was fixed.

### Fixed
- `verify` passed a regression suite whose named failures were all in the baseline even when it hid new ones: a
  suite that stops at its first failure (`-x`, `-f`, `--bail`, plain `cargo test`), a runner that names files
  rather than tests (jest), or a suite that crashed. It now fails with `NOT ONLY KNOWN FAILURES` when the run was
  killed, ended without its summary, or failed more tests than the baseline. `baseline` records a stop-early suite
  with the option that runs every test (`--no-fail-fast` for cargo, without `-x` for pytest).
- A check that writes a file with changing content (a log, a timestamped report) made `done` refuse forever: every
  verify rewrote the file, so the files never matched the verified ones. Untracked files a check writes are now
  ignored from then on, unless the task scope names them, so they are never compared, locked or committed.
- `lock` locked such files when their names looked like tests (`test_output.json`), and every later check failed
  with `LOCKED FILE CHANGED`. Names like `test_*` make a file a test only if it is source code or lies in a test
  directory.
- `unlock` worked only after the test was edited; unlocking first (as SKILL.md says) still failed the next check.
  A file unlocked during a task is now open until `done`, which guards its final version.
- An agent that ignored STOP kept seeing STOP; ESCALATE now comes after 6 failed fixes as well. `restore --to
  start` is a fresh start that resets the strikes; after two fresh starts NEXT says to ask the human.
- Changing `build_cmd` neither rebuilt nor invalidated passes made with the old build.
- `run "cmd" --tail 20` ran `cmd --tail 20`; options after a quoted command are sci's now.
- `lock FILE` after `lock --none` left the task treated as having nothing to lock.
- A pre-commit hook that rejected `done`'s commit left new files staged in the user's index.
- `wait` crashed when a background job's output file was missing.
- The optional hooks blocked deleting `.sci/scratch/` files, notes and commit messages that start with "git reset
  --hard" or "rm -rf .sci", and `git restore -S`; they let `/usr/bin/git reset --hard` through. A hook payload
  that names a directory no longer falls back to another repository.
- "tolerance loosened: places 7 -> 7" now names the loosened value.

### Added
- Rust: unit tests inside source files (`#[cfg(test)] mod tests`, `#[test]`, `#[tokio::test]`, `#[rstest]`,
  `proptest!`) are found with a small lexer. `lock` locks only them, so the code around them stays editable;
  `done` protects them; edits to them are checked for weakening; `restore --paths` puts back only the tests.
  `cargo test` filters and `--test NAME` map to the files to lock. Failing tests are read from `cargo test -q`,
  doc tests and nextest; after a failed verify `check` reruns only them (`-- --exact`). The suite is
  `cargo test --no-fail-fast -q`, and a check like `cargo test -q parser` is left to it. `find` treats `impl`
  blocks and `macro_rules!` as definitions. PyO3/maturin projects get a `build_cmd` hint. Comment and layout edits
  in `.rs` files after a verify (`cargo fmt` included) need no new verify, as in Python; doc comments still count,
  since cargo runs them as doc tests.
- JavaScript/TypeScript: failing tests are named per test for jest (`file › test`), vitest, `node --test` and TAP;
  snapshot files are tests; `test.skipIf`, `test.fails` and `toBeCloseTo` digits count as weakening; `init` picks
  pnpm, yarn or bun from the lockfile.
- Weakening checks: a dropped tolerance (`rtol=1e-12` removed), a duplicate test class, a test replaced by an
  assignment or deleted, `if True: return`, `if not True:`, `#[ignore = "..."]`, `assert!(true)`, `if false {`,
  and approx's `max_relative`/`max_ulps`.
- `golden add` warns when the reference contains NaN or infinity.

### Changed
- The last `done` counts as the final verify when that task's full verify covered the same suite, goldens and
  baseline, so an agent no longer runs a verify that only reuses every step.
- On a large repository a check after an edit is about 5x faster (60,000 files: 2.8 s to 0.6 s): which code
  mentions which documents is kept per file in `.sci/mentions.json`, and glob lists match as one regex.
- SKILL.md: `allowed-tools` pre-approves only sci's own commands and read-only tools (`Bash(.sci/sci:*) Read Grep
  Glob`) instead of every shell command and file edit. Language examples for Python, Rust and JavaScript;
  `references/large-codebases.md` has a Rust and JavaScript section. When the skill folder is unknown, SKILL.md
  checks the known install paths before a depth-limited `find ~`.
- README: the optional hooks remind the agent with each message you send (each model step only in Antigravity),
  not "every turn".

## [3.3.1] - 2026-11-21

### Fixed
- `install.ps1` failed in Windows PowerShell 5.1 started from PowerShell 7, for example
  `powershell -File install.ps1` typed in a pwsh window. It loaded PowerShell 7's `Expand-Archive`, which calls a
  .NET method that 5.1 lacks. The installer now unpacks the download with .NET directly.
- `init` found no test command in a Python project whose `test_*.py` files sit next to the code, so `baseline` was
  refused until the agent set one. Such files now mark a Python project, and the command is pytest or
  `unittest discover -s .`.

### Changed
- After a failed `verify`, `NEXT:` says "it also reruns what failed the last verify (regression suite)" instead of
  "it also reruns regression suite, which failed the last verify".
- `.sci/sci help` says `unlock` is for a test that is wrong or must follow an intended change.
- README: rewritten, with a banner, a terminal picture, the bill-splitter example as real sci 3.3.1 output, and
  reference tables for the commands and settings. `.github/readme/demo.sh` replays the example, and
  `.github/readme/build.py` draws the images and checks that every quoted line is real output.

## [3.3.0] - 2026-11-21

sci is now a general software-engineering skill: features, bug fixes, refactors, migrations and performance work
in any language, with its numerical checks as one part. The changes below remove the places where it slowed
capable agents down without catching anything.

### Changed
- After a failed `verify`, `check` also reruns the step that failed (an earlier task's check, a golden or the
  regression suite) until it passes. A failing suite is rerun as just its failing tests when sci can select them
  (pytest node ids, unittest ids); if those pass alone but still fail in the full suite, sci says the tests
  depend on each other and reruns the whole suite from then on. A fix that misses the failure now counts as a
  strike, so STOP works for regressions too. Before, `check` ran only the task's own test, passed, reset the
  strikes and sent the agent back to a full `verify`.
- A check whose tests pass but which changes a locked or protected test is still a FAIL, but no longer a strike.
  The GUARD FIX and REGRESSION lines say that `unlock --reason` is the right step when a test must follow an
  intended change of the task, such as a new signature.
- `done`, `resume` and NEXT accept edits made after `verify` to comments and layout in Python files: the syntax
  tree is unchanged, docstrings included. This does not apply when a check reads source as text (a linter,
  formatter, type checker, doctest or `grep`).
- More test runners. sci reads the failing tests of jest, vitest, Maven, Gradle, dotnet, RSpec and PHPUnit, so
  baselines work for them. Earlier tasks' checks are left to the regression suite for cargo, go, ctest, jest,
  vitest, dotnet and Gradle when the check is the suite's command plus a test selection that cannot reach tests
  the suite leaves out, and a suite failure names the task. Not when the baseline has failures.
- Defaults for more languages: test files (`__tests__/`, `*Test.*`, `*Tests.*`), rebuild triggers for
  TypeScript, the JVM, .NET, Swift, Go and Zig, test-weakening checks for focused tests (`it.only`, `fit`),
  xUnit, NUnit and PHPUnit skips, and C# and Go assertions.
- Autopilot (Antigravity hooks, off by default) lets the agent stop after two stops in a row without a test run
  or a task status change. Reading the state or writing notes in between no longer counts as progress.
- SKILL.md: rule 3 covers the numbers an agent writes into code and tests, and a one-line `sci run` satisfies
  it. A test that must follow an intended change may be unlocked with a reason. The plan step says which check
  fits a feature, bug fix, refactor, performance or numerical task. `resume` is no longer asked for every 20
  tool calls. Widening the scope is the first answer to OUT OF SCOPE when the change is needed.

## [3.2.0] - 2026-10-29

Long sessions spent most of their time waiting on `verify`: every test ran twice, nothing was reused, and long
runs died with the agent's shell-tool timeout.

### Changed
- `verify` runs each test once. When the regression suite runs an earlier task's test files anyway, that task's
  check is left to the suite, and a suite failure in those files names the task (`REGRESSION: your change broke
  T1's tests (...)`). This applies to plain pytest and `python -m unittest` commands with the same interpreter,
  when the suite doesn't narrow the selection or stop at the first failure and the files have no known baseline
  failure.
- `verify` reuses a step that passed on the same code with the same installed packages (`PASS ...: reused`).
  `verify --fresh` reruns everything; `check` always runs. A document counts as code only for steps that can
  read it: every step, when a test, source file or build file names it or its extension, or when it lies under
  a source directory; otherwise only the steps whose command names it, its directory or its extension, or runs
  a documentation tool. A task that only edits documentation no longer reruns the whole suite.
- `done` accepts documentation edits made after `verify` when no step of that verify can read them. A modified
  document no longer counts as being outside the task's scope.
- check, verify, run, baseline and golden add/update/check run as detached background jobs. The command relays
  the output for up to `wait_secs` (100) seconds, then prints `STILL RUNNING` and exits with code 3.
  `.sci/sci wait` collects the result and `.sci/sci cancel` stops the job. A shell-tool timeout or a restarted
  session no longer kills a long verify, and a job whose process died is reported as `INTERRUPTED`. With
  `config set background false` (or `SCI_BACKGROUND=0`), everything runs in the foreground as before.
- note, hyp, now and resume work while a job runs. Commands that run tests or change files are refused. The
  refusal says how long the job has run and how long its current step took last time.
- A check or verify whose code files were edited while it ran doesn't count (`FILES EDITED WHILE ... RAN`).
- Tracked files that match `ignore_globs` keep their committed version in snapshots, so regenerating them no
  longer changes the snapshot or trips scope checks. `graphify-out/**` is ignored by default.
- `config add` and `config remove` change one item of a list setting (`config set` replaces the whole list).
- `resume` says when a newer sci is installed than the project's copy in `.sci/`. Running `init` again updates
  the copy and keeps the plan.

### Fixed
- `hyp confirm|refute` with a mistyped ID could overwrite the verdict of another task's hypothesis or of one
  already resolved. Both are now refused; `--revise` changes a verdict on new evidence and keeps the old one in
  the hypothesis's history.

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
