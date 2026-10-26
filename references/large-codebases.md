# Working in large codebases

The goal is to change the right lines without reading the whole repository and without
rediscovering the same facts after every compaction.

## 1. First contact (once per repo)

1. `.sci/sci map`: file counts per directory, languages, build/test config, test directories,
   and large files to avoid.
2. Read the README section on building and testing, if there is one (search it; do not read
   the whole file if it is long).
3. Find the entry point of the feature: `.sci/sci find <main function or class>`.
4. Save the essentials immediately:
   ```
   .sci/sci note "solver entry: src/heat/solve.py:run_case(); time loop in src/heat/stepper.py:advance()" --kind code
   .sci/sci note "nondimensional units; x in [0,1]; dt from cfl() in src/heat/stepper.py" --kind units
   .sci/sci note "build: pip install -e . --no-build-isolation; tests: pytest tests/heat -q" --kind env
   ```
   Notes appear in every `.sci/sci resume`, so you never search for these again.

## 2. Reading strategy

- `.sci/sci find <name>` shows likely definitions first, then usage counts per file. Narrow it with
  `--path src/heat`. Use `--regex` for patterns and `-i` to ignore case.
- Read 50-150 lines around the definition, never the whole file. Open callers only where
  `find` shows them.
- Read the tests next to the code: they show intended behavior, fixtures and tolerances.
- Trace data flow in one direction: config or input -> setup -> main loop -> output. Write the path
  down as a note.
- Generated files, vendored libraries, data files and notebooks: do not read them unless the task
  is about them.

## 3. Changing code safely

- Follow local conventions: naming, error handling, logging, array layout, how options are
  registered. Copy the pattern of the nearest similar feature.
- No new abstraction layers, no drive-by refactors, no reformatting of untouched code.
- Signature changes: `.sci/sci find <name>` lists every caller. All of them belong in the task
  scope (`.sci/sci task scope <glob>`), and every caller must be updated in the same task.
- Keep each step runnable: change, `.sci/sci check`, next change. A PASS saves a restore point.
- Keep the diff small. sci warns above 400 changed lines; split the task instead of pushing on.

## 4. Builds and environments

- Compiled extensions (C, C++, Fortran, Cython, CUDA): tests import the built binary, not your
  edited source. Set the rebuild command once:
  `.sci/sci config set build_cmd "pip install -e . --no-build-isolation"` (or `cmake --build build`,
  `make`, `meson compile -C build`). sci reruns it when matching sources change.
- If tests need an environment (conda, modules, venv), put the activation in the commands:
  `.sci/sci config set test_cmd "source env.sh && pytest -q"`.
- Never install packages or change environments without the human's approval.

## 5. Big test suites

- Task checks must be targeted: one file (`tests/heat/test_stepper.py`), one test id
  (`tests/heat/test_stepper.py::test_cfl`), a keyword (`-k cfl`) or a marker.
- Record known failures once with `.sci/sci baseline`. From then on `verify` reruns that suite and
  fails only on new failures.
- Slow suites: enroll the relevant subset instead, `.sci/sci baseline --cmd "pytest -q tests/heat"`,
  and leave the full suite to the human or CI.
- Use `-x` (stop at first failure) for quick loops, and `.sci/sci out <run> --grep <pattern>` to
  inspect long logs.

## 6. Output hygiene (protects your context window)

- Never `cat` large files, print whole arrays, or dump full test logs.
- Pipe exploratory commands through `head`: `grep -rn "pattern" src | head -30`.
- Prefer `.sci/sci find`, `.sci/sci map` and `.sci/sci out` because their output is capped and
  the full output is kept in `.sci/runs/`.
