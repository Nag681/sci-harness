# Scientific verification: patterns and templates

Copy a template, replace the placeholders (`solve`, `exact`, `step`, ...) with the project's real
functions, and match the project's test style (pytest or unittest). Put derivation scripts in
`.sci/scratch/` and run them with `.sci/sci run`.

## 1. Pick the right check

| The task... | Verify with |
| --- | --- |
| refactors code without changing results | golden outputs (`.sci/sci golden add`, rtol 1e-12) plus existing tests |
| adds or changes a discretization | observed order of accuracy on 3+ resolutions |
| has no closed-form solution | method of manufactured solutions (MMS) |
| simulates a conservative system | conservation of energy, mass, momentum or charge |
| must respect symmetry | invariance or equivariance tests |
| fixes a bug | a test that reproduces the bug first (RED), then passes |
| changes performance only | golden outputs plus a timing comparison on the same input |

## 2. Observed order of accuracy

```python
import math

def observed_orders(hs, errs):
    return [math.log(errs[i] / errs[i + 1]) / math.log(hs[i] / hs[i + 1]) for i in range(len(hs) - 1)]

def test_scheme_is_second_order():
    ns = [32, 64, 128]                        # resolutions in the asymptotic range
    hs = [1.0 / n for n in ns]
    errs = [error_norm(solve(n), exact) for n in ns]   # replace with real solver + exact solution
    assert errs[0] > errs[1] > errs[2], errs  # error must shrink with refinement
    p = observed_orders(hs, errs)
    assert p[-1] > 2.0 - 0.2, p               # formal order 2, tolerance 0.2
```

- Use the finest pair for the verdict; coarse grids are often pre-asymptotic.
- Testing spatial order? Make time error negligible (small dt or dt proportional to h^p).
- Error stuck near 1e-13? You hit round-off; use coarser grids.
- Quick check without writing a test: `.sci/sci order --h 0.1 0.05 0.025 --err <e1> <e2> <e3> --expect 2`.

## 3. Method of manufactured solutions (MMS)

Derive the source term symbolically once, then paste the printed expression into the test.

```python
# .sci/scratch/mms_heat.py   run: .sci/sci run "python3 .sci/scratch/mms_heat.py"
import sympy as sp
x, t, D = sp.symbols("x t D", real=True)
u = sp.sin(sp.pi * x) * sp.exp(-t)          # chosen smooth solution
s = sp.diff(u, t) - D * sp.diff(u, x, 2)    # source so that u_t = D*u_xx + s holds exactly
print(sp.simplify(s))
```

Then run the solver with source `s`, initial/boundary data from `u`, and check the observed order
(section 2) against `u`. If sympy is unavailable, ask the human before installing anything.

## 4. Conservation and invariants

```python
def test_energy_conserved():
    state = initial_state(seed=0)
    e0 = total_energy(state)
    for _ in range(1000):
        state = step(state, dt)
    assert abs(total_energy(state) - e0) <= 1e-6 * abs(e0)

def test_mass_nonnegative_and_conserved():
    rho = run_model(steps=200)
    assert (rho >= 0).all()
    assert abs(rho.sum() - rho0_total) <= 1e-12 * rho0_total
```

The tolerance must come from theory: symplectic integrators have bounded energy error of
O(dt^p); conservative finite-volume schemes conserve mass to round-off (about n_steps * 1e-16
relative); non-conservative schemes drift. Write the estimate in a scratch script and
`.sci/sci note --kind derivation` the result.

## 5. Symmetry and properties

```python
import numpy as np

def test_rotation_equivariance():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(50, 3))
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))   # random orthogonal matrix
    np.testing.assert_allclose(forces(x @ q.T), forces(x) @ q.T, rtol=1e-12, atol=1e-12)
```

Other useful properties: translation invariance, time reversibility, permutation of identical
particles, linearity, idempotence, round trips (`inverse(forward(x)) == x`). With `hypothesis`
installed, generate inputs with it; otherwise loop over a seeded RNG.

## 6. Golden outputs (regression on numbers)

- The golden command must print only deterministic key numbers: energies, norms, residuals,
  a few sampled values. No timings, dates or addresses (or exclude them with `--filter REGEX`).
- Small and fast: a short run of the real code path (seconds, not minutes).
- Call the protected code path directly (`python -c '...'` or a script in `.sci/scratch/`). Never use
  a script that a task will edit: its output changes on purpose and the golden stops meaning anything.
- `.sci/sci golden add` runs the command twice and refuses non-reproducible output.
- Default rtol 1e-12 suits refactors. Reordered floating-point sums can change the last bits;
  if the code changes reduction order (threads, vectorization), justify a looser tolerance with a
  script, then `.sci/sci golden retol <name> --rtol <x> --reason "..."` (flagged for review).
- Intended result changes (bug fix, better scheme): `.sci/sci golden update <name> --reason "..."`.

## 7. Choosing tolerances (script, not guesswork)

```python
# .sci/scratch/tolerance.py: predict the discretization error at the test resolution
import math
h1, e1 = 0.02, 3.1e-4          # errors measured from two real runs (replace)
h2, e2 = 0.01, 7.8e-5
p = math.log(e1 / e2) / math.log(h1 / h2)   # observed order
C = e2 / h2**p                              # error constant: e ~ C * h**p
h_test = 0.025                              # resolution the test will use
pred = C * h_test**p
print("observed p=%.2f  predicted error at h_test=%.2e  tolerance with 10x margin=%.2e" % (p, pred, 10 * pred))
```

- float64 machine epsilon is 2.2e-16; float32 is 1.2e-7.
- A matrix with condition number 1e8 loses about 8 of 16 digits.
- Never loosen a tolerance until a test passes. A tolerance that needs loosening is a finding:
  record it with `.sci/sci hyp add` and investigate.

## 8. Floating-point pitfalls

- `a == b` on floats: use `math.isclose(a, b, rel_tol=..., abs_tol=...)` or `np.allclose`.
- Explicit inverses: `np.linalg.solve(A, b)` (or Cholesky/QR), never `inv(A) @ b`.
- Cancellation: `math.expm1(x)` for e^x - 1, `math.log1p(x)` for log(1 + x), `logsumexp` for
  log(sum(exp(a))); rearrange formulas that subtract nearly equal numbers.
- Overflow and underflow: work in log space; rescale.
- Long sums: `math.fsum` or pairwise/Kahan summation when accuracy matters.
- NaN/Inf: `assert np.all(np.isfinite(x))` at module boundaries and inside solver loops.
- Integer division and dtype: `1/2` vs `1//2`; mixing float32 and float64; integer overflow in
  index arithmetic for large grids.
- NumPy aliasing: `a += a.T` and in-place updates on views give wrong results; copy first.

## 9. Stability

```python
def test_cfl_respected():
    dt, dx, u_max = choose_dt(grid), grid.dx, abs(velocity).max()
    assert u_max * dt / dx <= 1.0   # explicit upwind; use the bound for your scheme
```

For ODE integrators, check that dt times the largest Jacobian eigenvalue magnitude lies inside
the method's stability region; compute the eigenvalues in a script, do not estimate by eye.

## 10. Units and determinism

- Keep the codebase's unit system. If you add variables, follow its naming (for example
  `dt_s`, `mass_kg`) and record conventions with `.sci/sci note --kind units`.
- `pint` (if installed) can check dimensions in tests; otherwise check by hand in a scratch
  script, never in prose.
- Seed every RNG (`np.random.default_rng(seed)`). For goldens, pin threads if results depend on
  them (`OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`).
