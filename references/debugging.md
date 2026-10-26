# Debugging without death spirals

Random edits until a test turns green waste the context window and break other things. sci counts
failed fix attempts. At 3 it prints STOP; after a second STOP it prints ESCALATE.

## 1. The STOP protocol

1. Stop editing code. Look at the evidence you already have:
   `.sci/sci out <run> --grep "Error|assert|nan"`, and the `failing:` line of the last check.
2. Write at least 2 specific, testable hypotheses:
   ```
   .sci/sci hyp add "velocity update uses a(t) instead of a(t+dt) in step()"
   .sci/sci hyp add "ghost cells are not refreshed before the flux computation"
   ```
   Bad: "something is wrong with the solver". Good: names a line, a variable or a step, and
   predicts what an experiment will show.
3. Test each hypothesis with an experiment, not a fix: a probe script in `.sci/scratch/` run with
   `.sci/sci run "python3 .sci/scratch/probe.py"`. Change one thing at a time.
4. Record the result with the run number as evidence:
   ```
   .sci/sci hyp refute H1 "run 41: a(t+dt) is used; energy drift unchanged"
   .sci/sci hyp confirm H2 "run 42: ghost values stale after step 1; refreshing them fixes drift"
   ```
5. Fix only the confirmed cause, then `.sci/sci check`. If your edits made things worse first,
   go back with `.sci/sci restore` (the attempt is kept as a patch).

Refuted hypotheses appear in `.sci/sci resume` and `.sci/HANDOFF.md` under RULED OUT, so nobody
tests them again after a compaction.

## 2. Experiments that work

- Shrink the problem: smallest grid, fewest particles, one time step, fixed seed.
- Compare with a reference: a slow, obvious pure-Python version of the same formula on a tiny
  input, or an analytic solution.
- Probe summary statistics at stage boundaries instead of printing arrays:
  ```python
  import numpy as np
  def probe(name, a):
      a = np.asarray(a)
      print(name, a.shape, a.dtype, "min %.3e max %.3e mean %.3e norm %.3e nan %d inf %d" % (
          np.nanmin(a), np.nanmax(a), np.nanmean(a), np.linalg.norm(np.nan_to_num(a)),
          np.isnan(a).sum(), np.isinf(a).sum()))
  ```
- Bisect the pipeline (wolf fence): probe the middle stage; if it is already wrong, the bug is
  upstream, otherwise downstream. Repeat until one function remains.
- Vary one parameter and predict the effect before running (halve dt: does the error drop 2x or
  4x? double the grid: does the drift change?). A wrong prediction is information.

## 3. Finding the commit that broke something

Only between tasks (no active changes), and only if the human agrees:

```
git bisect start <bad-commit> <good-commit>
git bisect run <command that exits 0 when good>      # e.g. python3 -m pytest -q tests/test_x.py
git bisect reset                                     # always finish with this
```

`.sci/` is git-ignored, so bisecting does not disturb the harness state. Record the culprit with
`.sci/sci hyp confirm ... "bisect: first bad commit <sha>"`.

## 4. Common numerical bugs (check these first)

- Sign error in a derivative, flux or force term (check with a symbolic derivation script).
- Off-by-one in stencils, ghost/halo cells, periodic wrap-around, or cell vs node indexing.
- Time step not passed through, or updated only on the first iteration.
- State updated in place while still being read (NumPy aliasing: `a += a.T`, views, `x[1:] = x[:-1]`).
- Integer division or float32 where float64 was intended; integer overflow in index arithmetic.
- Degrees vs radians, Hz vs rad/s, wrong unit prefix (cm vs m, ms vs s).
- C vs Fortran array order, transposed matrices, row vs column vectors.
- Uninitialized memory (`np.empty`) read before being written.
- Boundary conditions applied before instead of after the update (or not at all).
- Explicit scheme past its stability limit (CFL): errors grow geometrically toward NaN.
- Mutable default arguments or shared global state between test runs.
- Stale compiled extension: edited C/Fortran/Cython source not rebuilt (set `build_cmd`).

## 5. When to escalate

- ESCALATE printed (6+ failed attempts): `.sci/sci restore --to start`, write down a different
  approach (`.sci/sci note --kind decision "..."`), and start over smaller.
- The fix needs a decision about physics, units, requirements or acceptable accuracy:
  `.sci/sci task block <ID> --reason "<precise question with the evidence>"` and ask the human.
