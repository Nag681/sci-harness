---
name: software-engineer
description: >-
  Durable-memory harness for long-horizon scientific and numerical coding in large codebases
  (simulations, solvers, numerical methods, physics/engineering/climate/bio models, scientific
  data pipelines). Use when starting, continuing or resuming multi-step work on such code, after
  a conversation was summarized or compacted, or when work has lost direction. Keeps the plan,
  task status, ruled-out causes and codebase notes on disk; enforces locked tests, golden
  numerical outputs, regression checks, a 3-strike debugging stop and safe undo. Built for small
  models (Haiku, Gemini Flash, GPT-OSS) and works with any model.
license: MIT
compatibility: >-
  Needs git and Python 3.8+ (standard library only); any test runner. Linux, macOS and Windows.
  Works in any agent that can run shell commands (Claude Code, Codex, Gemini CLI, Antigravity,
  OpenCode, pi, Cursor).
allowed-tools: Bash Read Write Edit Glob Grep
metadata:
  version: "3.0.0"
---

# Long-horizon scientific coding

Your memory of this conversation is unreliable: long sessions get summarized and details vanish.
The truth lives in `.sci/` inside the repository. The tool `.sci/sci` reads it, updates it and
checks your work. When your memory and `.sci/sci` disagree, believe `.sci/sci`.

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
  `~/.local/share/agent-skills/software-engineer`; otherwise
  `find ~ -path '*software-engineer/scripts/sci.py' 2>/dev/null | head -1`)
- Run `.sci/sci resume` again after any summary or compaction, after finishing a task, whenever
  you are unsure, and at least every ~20 tool calls.
- Every sci command ends with a `[sci] ... NEXT: ...` line. Read it every time.

## 2. Rules

1. One task at a time. Change only what the active task needs, inside its scope.
2. Never weaken tests. Do not edit locked tests, existing assertions, tolerances, seeds or golden
   values to get a pass. If a test is truly wrong, explain why to the human, or run
   `.sci/sci unlock <file> --reason "<why>"` (the human reviews it).
3. No mental math. Every formula, constant, unit conversion, expected value and tolerance comes
   from a script you ran (`.sci/scratch/` + `.sci/sci run`). Save results:
   `.sci/sci note "<result>" --kind derivation`.
4. Only `.sci/sci check` and `.sci/sci verify` decide pass or fail. Never claim success without
   their PASS.
5. Never run `git reset --hard`, `git checkout -- <path>`, `git checkout .`, `git restore`,
   `git clean`, `git stash` or a force push. Undo with `.sci/sci restore` (it saves your attempt).
6. After 3 failed fixes the tool prints STOP. Then stop editing code: write hypotheses, test them,
   fix only what the evidence supports (section 5).
7. Keep outputs small. Never print whole large files or full logs. Use `.sci/sci find`, read
   50-150 line slices, `.sci/sci out <run> --grep <pattern>`.
8. Ask instead of guessing physics, units, boundary conditions or requirements:
   `.sci/sci task block <ID> --reason "<question>"`, then continue with another task.

## 3. Plan (when there are no tasks)

1. Explore: `.sci/sci map`, then `.sci/sci find <name>` for the functions involved. Read their
   definitions and tests.
2. Save what you learn so you never search for it again:
   `.sci/sci note "<fact with file:line>" --kind code|units|env|gotcha`
3. If numerical results could change, capture current behavior first:
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
   whole suite), and invariants for numerical work (conservation, symmetry, order of accuracy,
   bounds). Characterize current behavior first, change it next, switch defaults/docs last.

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

- Experiments and probes: `.sci/sci run "python3 .sci/scratch/probe.py"` (repo on PYTHONPATH,
  output logged as evidence). Keep scratch files in `.sci/scratch/`; they are never committed.
- Undo: `.sci/sci restore` (back to the last PASS), `--to start`, or `--paths <file>`.
- sci runs `--verify`, `--cmd` and `run` commands with bash (Git Bash on Windows): use forward
  slashes and bash quoting in them, even when your own shell is PowerShell.

## 5. When the tool says...

| Output | Do this |
| --- | --- |
| `STOP` | Do not edit code. `.sci/sci hyp add "<specific cause>"` (at least 2). Test each with a small `.sci/sci run` experiment. `.sci/sci hyp refute H1 "<evidence>"` or `.sci/sci hyp confirm H2 "<evidence>"`. Then fix only the confirmed cause. |
| `ESCALATE` | `.sci/sci restore --to start` and try a different approach (write it down with `.sci/sci note --kind decision`), or block the task and ask the human. |
| `LOCKED FILE CHANGED`, `EXISTING TEST WEAKENED`, `PROTECTED TEST CHANGED` | `.sci/sci restore --paths <file>`. Only if the test itself is wrong: `.sci/sci unlock <file> --reason "..."`. |
| `REGRESSION: your change broke T2` | Fix your code. Never edit T2's tests. |
| `FAIL golden <name>` | Numbers changed. Unintended: fix the code. Intended (better scheme, real bug fix): `.sci/sci golden update <name> --reason "..."`. |
| `NEW FAILURES (not in baseline)` | Your change broke other tests. Fix them before `done`. |
| `OUT OF SCOPE` / `Changes outside ... scope` | `.sci/sci restore --paths <file>`; if the change is truly needed: `.sci/sci task scope <glob>`. |
| `Untracked files ... not in its scope` | Delete them, move scratch work to `.sci/scratch/`, or add them: `.sci/sci done -m "..." --add <file>`. |
| `not verified on the current files` | Run `.sci/sci verify` again. |
| `TIMED OUT` | Check a narrower command: `.sci/sci check --cmd "<single test>"`. |
| `REFUSED: ... DO: ...` | Run the command after `DO:`. |

## 6. Scientific practice

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
- Fix random seeds. Compiled code (C/C++/Fortran/Cython/CUDA) must be rebuilt before testing:
  `.sci/sci config set build_cmd "<rebuild command>"` and sci rebuilds when sources change.

Templates for convergence, MMS, conservation and golden tests: `references/scientific-verification.md`.

## 7. Large codebases

- `.sci/sci find <name>` lists likely definitions first, then usages by file. Narrow it with
  `--path <dir>`.
- Read the definition, its callers and its tests before editing. A signature change means
  updating every caller, so put the callers in the task scope.
- Follow existing patterns (registries, config systems, naming). No new abstractions and no
  drive-by refactors.
- Never open huge generated or data files; `.sci/sci map` lists the large ones.

More: `references/large-codebases.md` · Debugging: `references/debugging.md` ·
A complete example session: `references/worked-example.md`

## 8. Finishing

When `NEXT:` says all tasks are done: run `.sci/sci verify`, then `.sci/sci report`, and give the
human the report, including every NEEDS YOUR REVIEW item and open question.
