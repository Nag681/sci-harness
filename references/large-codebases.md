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

- `cargo test`, `go test`, jest and vitest compile what they test themselves: no build command needed.
- Tests that import a built binary (Python extensions in C, C++, Fortran, Cython, CUDA or Rust through
  PyO3/maturin; tests that run `dist/` output) do not see your edited source until it is rebuilt. Set
  the rebuild command once: `.sci/sci config set build_cmd "maturin develop"` (or
  `pip install -e . --no-build-isolation`, `cmake --build build`, `npm run build`). sci reruns it when
  matching sources change, and when the build command itself changes.
- If tests need an environment (conda, modules, venv), put the activation in the commands:
  `.sci/sci config set test_cmd "source env.sh && pytest -q"`.
- Never install packages or change environments without the human's approval.

## 5. Big test suites

- Task checks must be targeted: one file (`tests/heat/test_stepper.py`), one test id
  (`tests/heat/test_stepper.py::test_cfl`), a keyword (`-k cfl`) or a marker.
- Record known failures once with `.sci/sci baseline`. From then on `verify` reruns that suite and
  fails only on new failures. A suite that stops at its first failure (`-x`, `--bail`, plain
  `cargo test`) would hide new failures behind known ones, so `baseline` records it with the option that
  runs every test (`--no-fail-fast` for cargo).
- Slow suites: enroll the relevant subset instead, `.sci/sci baseline --cmd "pytest -q tests/heat"`,
  and leave the full suite to the human or CI.
- After a failed verify, `check` reruns only the tests that failed (pytest, unittest, cargo test) or the
  test files that failed (jest, vitest, `node --test`, `bun test`).
- `.sci/sci out <run> --grep <pattern>` reads long logs.
- Files a check writes (logs, reports, coverage) are ignored from then on, so they never block `done`
  or get committed. Files in the task scope are never ignored, and a folder is ignored as a whole only
  when it holds no tests.

## 6. Output hygiene (protects your context window)

- Never `cat` large files, print whole arrays, or dump full test logs.
- Pipe exploratory commands through `head`: `grep -rn "pattern" src | head -30`.
- Prefer `.sci/sci find`, `.sci/sci map` and `.sci/sci out` because their output is capped and
  the full output is kept in `.sci/runs/`.

## 7. Rust and JavaScript/TypeScript

Rust:
- Targeted checks: `cargo test -q --lib parser::tests` (a module's unit tests),
  `cargo test -q --test cli` (the integration test `tests/cli.rs`), `cargo test -q -p engine` (one crate of a
  workspace). Find code with `.sci/sci find <fn|struct|trait>`; `impl` blocks count as definitions.
- Unit tests in `#[cfg(test)] mod tests` live inside the source file. `lock` locks only those tests
  (and `#[test]` functions, `proptest!` blocks); the code around them stays editable, and new tests may be
  added. After `done` they are protected like test files. `restore --paths src/x.rs` puts back just the
  locked tests; if a test was added or removed since, it puts back the whole file, code included, and says
  where your version is saved.
- Attributes above a test belong to it: adding `#[cfg(feature = "x")]`, `#[cfg_attr(...)]`,
  `#[ignore]` or `#[should_panic]` to an existing test counts as weakening it.
- The suite is `cargo test --no-fail-fast -q` (or `cargo nextest run --no-fail-fast`), so a failing
  crate does not hide the others.
- Add `cargo clippy -q -- -D warnings` to a task's `--verify` (with `&&`) when lint cleanliness is part of
  the task.

JavaScript/TypeScript:
- Targeted checks: `npx vitest run src/cart.test.ts`, `npx jest src/cart.test.ts -t "totals"`,
  `node --test test/cart.test.mjs`. sci reads each failing test's name from jest, vitest, node and TAP
  output.
- Snapshot files (`__snapshots__/`, `*.snap`) are tests: updating them to make a test pass needs
  `unlock`, like editing an assertion.
- Type checking is a check too: `npx tsc --noEmit && npx vitest run src/cart.test.ts`.
