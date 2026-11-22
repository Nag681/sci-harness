---
name: sci
description: >-
  Keeps coding agents on track in long, multi-step coding work. Use when starting, continuing or resuming
  multi-step work in a real codebase (features, bug fixes, refactors, migrations, performance work, scientific
  or numerical code; Python, Rust, JavaScript/TypeScript or any other language), after a conversation was
  summarized or compacted, or when work has lost direction. Not for one-line edits, questions or read-only
  exploration. Keeps the plan, task status, test results, ruled-out causes and codebase notes on disk; runs
  each task's acceptance test, earlier tasks' checks, saved reference outputs and the regression suite before
  every commit; stops repeated failed fixes; undoes safely. Works with any model.
license: MIT
compatibility: >-
  Needs git and Python 3.8+ (standard library only); any language and test runner. Linux, macOS and Windows.
  Works in any agent that can run shell commands (Claude Code, Codex, Gemini CLI, Antigravity,
  OpenCode, pi, Cursor).
allowed-tools: Bash(.sci/sci:*) Read Grep Glob
metadata:
  version: "3.4.2"
---

# Long coding tasks that stay on track

Long sessions get summarized and details vanish. The plan, task status, test results and notes live in
`.sci/` inside the repository. The tool `.sci/sci` reads and updates them and checks your work. When your
memory and `.sci/sci` disagree about what passed or what comes next, believe `.sci/sci`.

Run every command from the repository root.

## 1. Start or recover (always first)

```
.sci/sci resume
```

- It prints the objective, tasks, active task, ruled-out causes, notes and a `NEXT:` line.
  **Do what `NEXT:` says.**
- If it fails with `No such file or directory`, this repo is new to the harness:
  `python3 <this-skill-folder>/scripts/sci.py init --objective "<the user's goal in one line>"`
  (Windows: `py -3` or `python` instead of `python3`. Folder unknown? It is usually
  `~/.local/share/agent-skills/sci`; otherwise `ls ~/.claude/skills/sci ~/.agents/skills/sci 2>/dev/null`,
  and only then `find ~ -maxdepth 6 -path '*/sci/scripts/sci.py' 2>/dev/null | head -1`)
- Run `.sci/sci resume` again after any summary or compaction, after finishing a task, and whenever you
  are unsure.
- Every sci command ends with a `[sci] ... NEXT: ...` line. Read it every time.

## 2. Rules

1. One task at a time. Change only what the active task needs, inside its scope.
2. Never weaken tests to get a pass: no looser assertions, tolerances or timeouts, no deleted or skipped
   tests, no edited seeds, golden values or snapshots. A test that is wrong, or that must follow an intended
   change of the task (a new signature, a renamed function), may change: run
   `.sci/sci unlock <file> --reason "<why>"` before or after editing it (the human reviews it).
3. No guessed numbers. Expected values, constants, tolerances, timeouts, limits and unit conversions that
   you write into code or tests come from something you ran or a source you cite. A one-liner is enough:
   `.sci/sci run "python3 -c 'print(0.5 * 0.1 / 340)'"`. Save results that matter:
   `.sci/sci note "<result>" --kind derivation`.
4. Only `.sci/sci check` and `.sci/sci verify` decide pass or fail. Never claim success without
   their PASS.
5. Never run `git reset --hard`, `git checkout -- <path>`, `git checkout .`, `git restore`,
   `git clean`, `git stash` or a force push. Undo with `.sci/sci restore` (it saves your attempt).
6. After 3 failed fixes the tool prints STOP. Then stop editing code: write hypotheses, test them,
   fix only what the evidence supports (section 5).
7. Keep outputs small. Never print whole large files or full logs. Use `.sci/sci find`, read
   50-150 line slices, `.sci/sci out <run> --grep <pattern>`.
8. Ask instead of guessing requirements, API contracts, units or physics:
   `.sci/sci task block <ID> --reason "<question>"`, then continue with another task.

## 3. Plan (when there are no tasks)

1. Explore: `.sci/sci map`, then `.sci/sci find <name>` for the code involved. Read its definitions
   and tests.
2. Save what you learn so you never search for it again:
   `.sci/sci note "<fact with file:line>" --kind code|units|env|gotcha`
3. If numbers the code prints could change (results, metrics, counts), capture them first:
   `.sci/sci golden add <name> --cmd "<command that prints the key numbers>"` (call the code path
   directly; not a script that a task will edit)
4. Record the test suite's current state: `.sci/sci baseline` (`verify` reruns that suite and fails
   only on new failures)
5. Add 3-12 small tasks in order:
   ```
   .sci/sci task add "<what changes>" --verify "<command that passes only when done>" \
       --scope <file-or-dir> [--scope ...] [--inv "<invariant>"] [--dep T1] [--why "<context>"]
   ```
   A good task: one behavior, 1-3 files, a targeted `--verify` (one test file or test id, not the
   whole suite): `python3 -m pytest tests/test_x.py -q`, `cargo test -q --lib parser::tests`,
   `npx vitest run src/x.test.ts`. Several `task add` commands may go in one shell call, joined with
   `&&`. Pick the check by the kind of work:
   - Feature: a test of the new behavior (FAIL first).
   - Bug fix: a test that reproduces the bug (FAIL first), then the fix.
   - Refactor or migration: the existing tests that cover the code (PASS before and after).
   - Performance: a benchmark command that fails above a time or memory limit you measured.
   - Numerical change: invariants (`--inv`) and tests for them: conservation, symmetry, order of
     accuracy, bounds.

   Characterize current behavior first, change it next, switch defaults and docs last.

## 4. Task loop

```
.sci/sci task start                  # read the task card it prints
.sci/sci find <symbol>               # read the code and its tests; `.sci/sci note` what you learn
.sci/sci now "<plan + next command>" # survives compaction
  ... write or pick the acceptance test ...
.sci/sci check                       # new behavior: FAIL expected (RED). Refactor: PASS expected
.sci/sci lock                        # freezes the acceptance tests
  ... edit code in small steps ...
.sci/sci check                       # after every step; each PASS saves a restore point
.sci/sci verify                      # task + every earlier task + goldens + regression suite
.sci/sci done -m "<type>: <summary>" # commits only this task's files and shows the next task
```

- Long commands: check, verify, run, baseline and golden run in the background. If one prints
  `STILL RUNNING`, run `.sci/sci wait` (repeat until it finishes); the result is saved even if your
  session ends. Meanwhile read code or use note/hyp/now, but do not edit files. Never wrap sci in
  `nohup`, `&`, `timeout` or sleep loops.
- `verify` reuses steps that passed on identical code and leaves earlier tasks' tests to the
  regression suite when the suite runs them. After a failed verify, `check` also reruns what failed
  until it passes.
- After a verify PASS, edits to documentation, or to comments and layout in Python and Rust files (`cargo fmt`
  included; Rust doc comments count, they are doc tests), need no new verify unless a check reads them.
- Experiments and probes: `.sci/sci run "python3 .sci/scratch/probe.py"` (repo on PYTHONPATH,
  output logged as evidence). Keep scratch files in `.sci/scratch/`; they are never committed.
  Edit code with your file-editing tool, not with scripts.
- Rust unit tests inside source files (`#[cfg(test)] mod tests`, `#[test]` functions): `lock` locks only
  those tests, so the code around them stays editable. New tests may be added next to locked ones.
- `cargo test`, `go test`, jest and vitest build what they test. Tests that import built output (Python
  extensions in C/C++, Fortran, Cython, CUDA, or Rust through PyO3/maturin; compiled TypeScript in `dist/`)
  need `.sci/sci config set build_cmd "<rebuild command>"`; sci then rebuilds whenever sources change.
- Files your checks write (logs, reports, coverage) are ignored from then on, unless the task scope names
  them: they never block `done` and are never committed.
- Undo: `.sci/sci restore` (back to the last PASS), `--to start`, or `--paths <file>`.
- sci runs `--verify`, `--cmd` and `run` commands with bash (Git Bash on Windows): use forward
  slashes and bash quoting in them, even when your own shell is PowerShell.

## 5. When the tool says...

| Output | Do this |
| --- | --- |
| `STOP` | Do not edit code. `.sci/sci hyp add "<specific cause>"` (at least 2). Test each with a small `.sci/sci run` experiment. `.sci/sci hyp refute H1 "<evidence>"` or `.sci/sci hyp confirm H2 "<evidence>"`. Then fix only the confirmed cause. |
| `ESCALATE` | `.sci/sci restore --to start` (a fresh start: strikes reset) and try a different approach (write it down with `.sci/sci note --kind decision`), or block the task and ask the human. After two fresh starts it says to ask the human: do that. |
| `LOCKED FILE CHANGED`, `LOCKED TESTS CHANGED`, `EXISTING TEST WEAKENED`, `PROTECTED TEST(S) CHANGED` | `.sci/sci restore --paths <file>` (in a Rust source file it puts back only the tests, or the whole file if a test was added or removed; it says so). If the test is wrong or must follow an intended change of this task: `.sci/sci unlock <file> --reason "..."`. |
| `REGRESSION: your change broke T2` | Fix your code, then `.sci/sci check` (it reruns T2's check). Change T2's tests only if they must follow an intended change of this task (`unlock`, as above). |
| `FAIL golden <name>` | Output changed. Unintended: fix the code. Intended (a bug fix, a better method): `.sci/sci golden update <name> --reason "..."`. |
| `NEW FAILURES (not in baseline)` | Your change broke other tests. Fix the code, then `.sci/sci check` (it reruns them). |
| `NOT ONLY KNOWN FAILURES` | The suite named only known failures, but it crashed, stopped early or failed more tests than the baseline. Read the log (`.sci/sci out <run> --grep "FAIL\|panicked\|Error"`) and fix the code. |
| `pass when they run alone but fail in the full suite` | The failing tests depend on what other tests leave behind (shared state, files, processes). Find it; `check` now runs the whole suite. |
| `OUT OF SCOPE` / `Changes outside ... scope` | Needed for this task: `.sci/sci task scope <glob>`. A drive-by edit: `.sci/sci restore --paths <file>`. |
| `sci ignores it from now on` | Nothing to do: a check wrote that file. If the task must commit it: `.sci/sci config remove ignore_globs <file>` and `.sci/sci task scope <file>`. |
| `Untracked files ... not in its scope` | Delete them, move scratch work to `.sci/scratch/`, or add them: `.sci/sci done -m "..." --add <file>`. |
| `not verified on the current files` | Run `.sci/sci verify` again. |
| `TIMED OUT` | Check a narrower command: `.sci/sci check --cmd "<single test>"`. |
| `STILL RUNNING` | `.sci/sci wait` (repeat until it finishes). Do not edit files meanwhile. |
| `FILES EDITED WHILE ... RAN` | Run the command again and leave files alone until it finishes. |
| `INTERRUPTED` | Run the command again; steps that passed are reused. If it says your environment ends background processes: `.sci/sci config set background false`. |
| `REFUSED: ... DO: ...` | Run the command after `DO:`. |

## 6. Numerical and scientific code

- Units first: find the unit system and conventions (SI or nondimensional, degrees or radians,
  array order, grid staggering, sign conventions) and save them with `--kind units`.
- Before changing numerical code, capture behavior with a golden output and/or a
  characterization test. Refactors must keep goldens within rtol 1e-12.
- Numerical tasks list invariants (`--inv`) and test them: conservation, symmetry, positivity,
  bounds, analytic or manufactured solutions.
- Discretization changes: measure the observed order on 3+ resolutions and compute it with
  `.sci/sci order --h <h1 h2 h3> --err <e1 e2 e3> --expect <p>`.
- Floats: compare with tolerances (isclose/allclose), never `==`. Use solve, not inv. Watch for
  cancellation (expm1, log1p, logsumexp). Assert finite values at module boundaries.
- Tolerances come from analysis or a script, never from loosening until green.
- Fix random seeds.

Templates for convergence, MMS, conservation and golden tests: `references/scientific-verification.md`.

## 7. Large codebases

- `.sci/sci find <name>` lists likely definitions first, then usages by file. Narrow it with
  `--path <dir>`.
- Read the definition, its callers and its tests before editing. A signature change means
  updating every caller, so put the callers in the task scope.
- Follow existing patterns (registries, config systems, naming). No new abstractions and no
  drive-by refactors.
- Never open huge generated or data files; `.sci/sci map` lists the large ones.
- Rust and JavaScript/TypeScript specifics (targeted checks, workspaces, snapshots, clippy, tsc):
  `references/large-codebases.md` section 7.

More: `references/large-codebases.md` · Debugging: `references/debugging.md` ·
A complete example session: `references/worked-example.md`

## 8. Finishing

When `NEXT:` says all tasks are done, do what it says: `.sci/sci report` (the last task's verify already
covered every task), or `.sci/sci verify` first if files changed since. Give the human the report, including
every NEEDS YOUR REVIEW item and open question.
