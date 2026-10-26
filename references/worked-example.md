# Worked example: a Verlet integrator, start to finish

A real session, recorded with sci 3.0.0. Outputs are real but trimmed: most repeated `[sci] ... NEXT:`
footer lines are left out, and `...` marks cuts in long test output. The repository is a toy:
`orbit/integrators.py` has `euler()` and `run()` for the harmonic oscillator x'' = -x. Copy the
pattern, not the numbers.

## 1. Start

```
$ .sci/sci resume
bash: .sci/sci: No such file or directory

$ python3 ~/.claude/skills/software-engineer/scripts/sci.py init --objective "Add a velocity Verlet integrator that conserves energy; keep Euler results unchanged"
sci 3.0.0 initialized in /home/me/orbit
  state: .sci/ (git-ignored via .git/info/exclude; never commit it)
  tool : .sci/sci <command>   (use this path from now on)
  test_cmd: .venv/bin/python -m pytest -q
  note: Python project detected -> .venv/bin/python -m pytest -q
Repo map saved to .sci/map.txt (view: .sci/sci map).
NEXT: Plan: explore (.sci/sci map, .sci/sci find <symbol>), then add 3-12 small tasks: .sci/sci task add "<title>" --verify "<cmd>" --scope <path>. Once, before task 1: .sci/sci baseline
```

## 2. Plan: explore, write facts down, capture current numbers

```
$ .sci/sci find run
5 matches for 'run' in 3 files.
DEFINITIONS (likely):
  orbit/integrators.py:8: def run(step, x0, v0, dt, n):
USAGES by file (most first):
  energy_demo.py (2): lines 1, 3
  tests/test_euler.py (2) [test]: lines 2, 6
  orbit/integrators.py (1): lines 8
Read only the relevant ranges (about 80 lines around each line number), not whole files.

$ .sci/sci note "integrators: orbit/integrators.py; step(x, v, dt) -> (x, v); run() loops n steps" --kind code
Noted N1 [code].

$ .sci/sci note "nondimensional: omega = 1, energy = 0.5*(x^2 + v^2)" --kind units
Noted N2 [units].
```

The objective says Euler results must not change, so freeze them with a golden output. The golden
command calls only the Euler code path, which no task will edit:

```
$ .sci/sci golden add euler_energy --cmd ".venv/bin/python -c 'from orbit.integrators import euler, run; x, v = run(euler, 1.0, 0.0, 0.01, 1000); print(x, v, 0.5 * (x * x + v * v))'"
Golden euler_energy recorded: 3 numbers, rtol=1e-12 atol=0 (reproducible across 2 runs).

$ .sci/sci baseline
PASS baseline: exit 0 in 0.2s | log .sci/runs/0003-baseline.log
  $ .venv/bin/python -m pytest -q
Baseline is green: any failure of this suite later is a regression.
`.sci/sci verify` reruns this suite from now on (it took 0s). Too slow? Pick a narrower one: .sci/sci baseline --cmd "<subset>"
```

Two small tasks. Each has a targeted verify command (one test file), a scope, and invariants that
are numbers, not wishes:

```
$ .sci/sci task add "Add velocity Verlet step" --verify ".venv/bin/python -m pytest -q tests/test_verlet.py" --scope orbit/integrators.py --scope tests/test_verlet.py --inv "energy error < 1e-4 after 10000 steps at dt=0.01" --inv "2nd order in dt"
Added T1: Add velocity Verlet step

$ .sci/sci task add "Report Verlet energies in energy_demo.py" --verify ".venv/bin/python -m pytest -q tests/test_energy_demo.py" --scope energy_demo.py --scope tests/test_energy_demo.py --dep T1
Added T2: Report Verlet energies in energy_demo.py
[sci] no active task | NEXT: Start the next task: `.sci/sci task start` (T1: Add velocity Verlet step).

$ .sci/sci task start
T1 [active] Add velocity Verlet step
  verify: .venv/bin/python -m pytest -q tests/test_verlet.py
  scope : orbit/integrators.py, tests/test_verlet.py
  invariants: energy error < 1e-4 after 10000 steps at dt=0.01; 2nd order in dt
[sci] T1 "Add velocity Verlet step" | check not run | strikes 0/3 | NEXT: Read the code (.sci/sci find <symbol>), write or pick the acceptance test, then `.sci/sci check` (FAIL expected for new behavior, PASS for a refactor).
```

## 3. T1: the acceptance test first (RED), then lock it

`tests/test_verlet.py` turns both invariants into assertions:

```python
import math
from orbit.integrators import run, verlet


def energy(x, v):
    return 0.5 * (x * x + v * v)


def test_energy_bounded():
    x, v = run(verlet, 1.0, 0.0, 0.01, 10000)
    assert abs(energy(x, v) - 0.5) < 1e-4


def test_second_order():
    errs = [abs(run(verlet, 1.0, 0.0, 1.0 / n, n)[0] - math.cos(1.0)) for n in (50, 100)]
    p = math.log(errs[0] / errs[1]) / math.log(2.0)
    assert p > 1.8
```

```
$ .sci/sci now "wrote tests/test_verlet.py (energy bound + order); next: check for RED, then lock"

$ .sci/sci check
FAIL check T1: exit 2 in 0.2s | log .sci/runs/0004-check-T1.log
  $ .venv/bin/python -m pytest -q tests/test_verlet.py
  failing (1): tests/test_verlet.py
  --- output (15 lines) ---
  ...
  | E ImportError: cannot import name 'verlet' from 'orbit.integrators' (orbit/integrators.py)
  ...
Not locked yet: when the acceptance test is right, run `.sci/sci lock`.
[sci] T1 "Add velocity Verlet step" | check FAIL 0s ago | strikes 0/3 | NEXT: If the check fails for the expected reason (feature missing, not a broken test), lock the acceptance tests: `.sci/sci lock`, then implement in small steps.
```

It fails because `verlet` does not exist yet. That is the expected reason, so lock:

```
$ .sci/sci lock
Locked for T1: tests/test_verlet.py
Do not edit locked files. Implement in small steps and run `.sci/sci check` after each.
[sci] T1 "Add velocity Verlet step" | check FAIL 0s ago | strikes 0/3 | NEXT: Fix the code (not the tests), then `.sci/sci check`. Strike 0/3.
```

## 4. Three failed fixes, then STOP

First implementation (wrong: the velocity update is Euler's):

```python
def verlet(x, v, dt):
    a = -x
    x_new = x + dt * v + 0.5 * dt * dt * a
    v_new = v + dt * a
    return x_new, v_new
```

```
$ .sci/sci check
FAIL check T1: exit 1 in 0.2s | log .sci/runs/0005-check-T1.log
  $ .venv/bin/python -m pytest -q tests/test_verlet.py
  failing (2): tests/test_verlet.py::test_energy_bounded, tests/test_verlet.py::test_second_order
  ...
  | E assert 0.32615504272312845 < 0.0001
  ...
  | E assert 0.9882624852411197 > 1.8
  ...
Attempt saved: .sci/attempts/T1-strike1-1793023174.patch
[sci] T1 "Add velocity Verlet step" | check FAIL 0s ago | strikes 1/3 | NEXT: Fix the code (not the tests), then `.sci/sci check`. Strike 1/3.
```

The model then guessed twice more (`v + dt * (a + 0.0)`, then `v + 0.5 * dt * a`). Guessing is
exactly what the strike counter is for:

```
$ .sci/sci check
!!! STOP is active for T1 (details at the end of this output).
FAIL check T1: exit 1 in 0.2s | log .sci/runs/0007-check-T1.log
  ...
  | E assert -0.015325831227916953 > 1.8
  ...
Attempt saved: .sci/attempts/T1-strike3-1793023174.patch
!!! STOP: 3 failed fix attempts in a row. Do NOT edit code now.
!!! 1) .sci/sci hyp add "<specific, testable cause>"  (at least 2 hypotheses)
!!! 2) test each with a tiny experiment (print/probe/script in .sci/scratch), not a code fix
!!! 3) .sci/sci hyp refute H<n> "<evidence>"  or  .sci/sci hyp confirm H<n> "<evidence>"
!!! Then fix only the confirmed cause. Undo bad edits with: .sci/sci restore
[sci] T1 "Add velocity Verlet step" | check FAIL 0s ago | strikes 3/3 | NEXT: STOP (3 failed fixes on T1): do not edit code. `.sci/sci hyp add` >=2 causes, test each with a small experiment, then `.sci/sci hyp refute|confirm H<n> "<evidence>"`.
```

## 5. Hypotheses and one experiment

Each hypothesis names one line of code and predicts what a measurement will show:

```
$ .sci/sci hyp add "velocity update ignores a(t+dt): should be v + 0.5*dt*(a(t) + a(t+dt))"
Added H1: velocity update ignores a(t+dt): should be v + 0.5*dt*(a(t) + a(t+dt))
Test it with a small experiment (probe/print/script in .sci/scratch), then: .sci/sci hyp refute|confirm H1 "<evidence>"
STOP still active: need 1 more hypothesis(es) and refute or confirm at least one.

$ .sci/sci hyp add "position update has the wrong dt^2 coefficient"
Added H2: position update has the wrong dt^2 coefficient
STOP still active: need refute or confirm at least one.
```

One step with dt = 0.1 from x = 1, v = 0 tests both. `.sci/scratch/probe.py` prints each value next
to what velocity Verlet requires (the script computes the expected values; no mental math):

```python
from orbit.integrators import verlet

x, v, dt = 1.0, 0.0, 0.1
x1, v1 = verlet(x, v, dt)
a0, a1 = -x, -x1
print("x1", x1, "expected", x + dt * v + 0.5 * dt * dt * a0)
print("v1", v1, "expected", v + 0.5 * dt * (a0 + a1), "| using a(t) only:", v + 0.5 * dt * a0)
```

```
$ .sci/sci run ".venv/bin/python .sci/scratch/probe.py"
run 8: exit 0 in 0.0s | log .sci/runs/0008-run-T1.log
  | x1 0.995 expected 0.995
  | v1 -0.05 expected -0.09975 | using a(t) only: -0.05
Cite it as evidence: .sci/sci hyp confirm|refute H<n> "run 8: <what it showed>"

$ .sci/sci hyp confirm H1 "run 8: v1 = -0.05 equals the a(t)-only value; Verlet needs -0.09975"
H1 confirmed.
STOP cleared: strikes reset. Fix only what the evidence supports (confirmed: H1).

$ .sci/sci hyp refute H2 "run 8: x1 = 0.995 matches x + dt*v + 0.5*dt^2*a"
H2 refuted.
[sci] T1 "Add velocity Verlet step" | check FAIL 0s ago | strikes 0/3 | NEXT: Fix the code (not the tests), then `.sci/sci check`. Strike 0/3.
```

## 6. Fix only the confirmed cause; check, verify, done

```python
def verlet(x, v, dt):
    a = -x
    x_new = x + dt * v + 0.5 * dt * dt * a
    a_new = -x_new
    v_new = v + 0.5 * dt * (a + a_new)
    return x_new, v_new
```

```
$ .sci/sci check
PASS check T1: exit 0 in 0.2s | log .sci/runs/0009-check-T1.log
  $ .venv/bin/python -m pytest -q tests/test_verlet.py
  | .. [100%]
  | 2 passed in 0.00s
[sci] T1 "Add velocity Verlet step" | check PASS 0s ago | strikes 0/3 | NEXT: Checks pass. Run `.sci/sci verify` (full gate).

$ .sci/sci verify
PASS task T1: exit 0 in 0.2s | log .sci/runs/0010-verify-T1.log
PASS golden euler_energy: 3 numbers within rtol=1e-12 atol=0
PASS regression suite: exit 0 in 0.2s | log .sci/runs/0012-regress-T1.log
VERIFY PASS
[sci] T1 "Add velocity Verlet step" | check PASS 1s ago | strikes 0/3 | NEXT: Verified. Run `.sci/sci done -m "<summary>"`.

$ .sci/sci done -m "feat(orbit): add velocity Verlet integrator"
DONE T1: Add velocity Verlet step | commit ccf7652edc
  files: orbit/integrators.py, tests/test_verlet.py
  protected tests (later tasks must not change them): tests/test_verlet.py
[sci] no active task | NEXT: Start the next task: `.sci/sci task start` (T2: Report Verlet energies in energy_demo.py).
```

## 7. T2, interrupted by a compaction

```
$ .sci/sci task start
T2 [active] Report Verlet energies in energy_demo.py
  verify: .venv/bin/python -m pytest -q tests/test_energy_demo.py
  scope : energy_demo.py, tests/test_energy_demo.py
  deps: T1
  Ruled out earlier (tested and false; do not re-test): position update has the wrong dt^2 coefficient
```

The model wrote `tests/test_energy_demo.py` (runs the demo, expects two `verlet ...` lines with
energy within 1e-2 of 0.5), saw it fail for the expected reason (`assert 0 == 2`: no verlet lines
yet), locked it and recorded its plan:

```
$ .sci/sci lock
Locked for T2: tests/test_energy_demo.py

$ .sci/sci now "T2: test written and locked; next: append verlet lines to energy_demo.py, then check"
```

Here the conversation was summarized. The model's first action afterwards is always the same:

```
$ .sci/sci resume
=== .sci/sci resume | session 2 | sci/verlet @ ccf7652e | 1 changed/untracked files | sci 3.0.0 ===
OBJECTIVE: Add a velocity Verlet integrator that conserves energy; keep Euler results unchanged
PROGRESS: 1/2 done, 0 blocked, 0 todo
  [x] T1 Add velocity Verlet step
  [>] T2 Report Verlet energies in energy_demo.py  <- ACTIVE
ACTIVE T2: Report Verlet energies in energy_demo.py
  verify: .venv/bin/python -m pytest -q tests/test_energy_demo.py
  scope : energy_demo.py, tests/test_energy_demo.py
  locked: tests/test_energy_demo.py
  last check: FAIL 0s ago | strikes 0/3 | verify: never
  failing: tests/test_energy_demo.py::test_demo_reports_verlet_energy
  doing (0s ago): T2: test written and locked; next: append verlet lines to energy_demo.py, then check
NOTES (2; all in .sci/HANDOFF.md):
  N2 [units] nondimensional: omega = 1, energy = 0.5*(x^2 + v^2)
  N1 [code] integrators: orbit/integrators.py; step(x, v, dt) -> (x, v); run() loops n steps
GOLDENS: euler_energy OK
BASELINE: 0 known pre-existing failures (not your job unless a task says so)
NEXT: Fix the code (not the tests), then `.sci/sci check`. Strike 0/3.
```

Everything needed to continue is on screen: the goal, the task, the locked test, the failing test
id and the plan written before the compaction. The model appended the Verlet loop to
`energy_demo.py` and went on:

```
$ .sci/sci check
PASS check T2: exit 0 in 0.2s | log .sci/runs/0014-check-T2.log

$ .sci/sci verify
PASS task T2: exit 0 in 0.2s | log .sci/runs/0015-verify-T2.log
PASS regression T1: exit 0 in 0.2s | log .sci/runs/0016-verify-T2.log
PASS golden euler_energy: 3 numbers within rtol=1e-12 atol=0
PASS regression suite: exit 0 in 0.2s | log .sci/runs/0018-regress-T2.log
VERIFY PASS

$ .sci/sci done -m "feat(demo): report Verlet energies"
DONE T2: Report Verlet energies in energy_demo.py | commit ae77d7c2b8
  files: energy_demo.py, tests/test_energy_demo.py
  protected tests (later tasks must not change them): tests/test_energy_demo.py
  note: added print/console output in energy_demo.py; keep only if intended.
[sci] no active task | NEXT: All tasks done. Run `.sci/sci verify` (final regression), then report to the human with `.sci/sci report`.
```

`verify` re-ran T1's test as a regression check and confirmed the Euler numbers are unchanged. The
print note is expected here: printing is the demo's job.

## 8. Finish

```
$ .sci/sci verify
PASS regression T1: exit 0 in 0.1s | log .sci/runs/0019-verify.log
PASS regression T2: exit 0 in 0.2s | log .sci/runs/0020-verify.log
PASS golden euler_energy: 3 numbers within rtol=1e-12 atol=0
PASS regression suite: exit 0 in 0.2s | log .sci/runs/0022-regress.log
VERIFY PASS
[sci] no active task | NEXT: All tasks done and the final verify passed. Run `.sci/sci report` and give the human the report, including every NEEDS YOUR REVIEW item.

$ .sci/sci report
# .sci/sci report

Objective: Add a velocity Verlet integrator that conserves energy; keep Euler results unchanged
Tasks: 2/2 done, 0 blocked, 0 todo

- T1 [done] Add velocity Verlet step (commit ccf7652edc)
- T2 [done] Report Verlet energies in energy_demo.py (commit ae77d7c2b8)

Last verify: PASS 0s ago
Goldens: euler_energy OK

Root causes found:
- H1: velocity update ignores a(t+dt): should be v + 0.5*dt*(a(t) + a(t+dt)) (run 8: v1 = -0.05 equals the a(t)-only value; Verlet needs -0.09975)
```

The final answer to the human is this report, plus anything it lists under NEEDS YOUR REVIEW.

## What to copy

- Run `.sci/sci resume` first, and again after any summary. Then do what `NEXT:` says.
- Write facts down the moment you learn them (`note`), and your plan before long steps (`now`).
- Capture numbers that must not change (`golden add`) and the suite's current state (`baseline`)
  before task 1.
- Write the test first and watch it fail for the right reason. Then `lock`.
- Three failed guesses means STOP. Ask what the evidence says: write hypotheses, run one small
  experiment, cite its run number, and only then fix.
- Only `check` and `verify` decide PASS. `done` commits only the task's files.
