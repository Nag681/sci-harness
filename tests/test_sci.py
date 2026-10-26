#!/usr/bin/env python3
"""Tests for scripts/sci.py. Stdlib only: python3 -m unittest discover -s tests -q

Every test builds a throwaway git repository with a tiny numerical package and drives sci
through its command line, exactly as an agent would.
"""
import ast
import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCI = HERE.parent / "scripts" / "sci.py"
PY = sys.executable
WINDOWS = os.name == "nt"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SCI.parent))
import sci  # noqa: E402


def shq(path):
    """A path quoted for the shell sci runs commands with: bash everywhere (Git Bash on Windows)."""
    return shlex.quote(str(path).replace("\\", "/") if WINDOWS else str(path))


PYQ = shq(PY)
UT = "%s -m unittest" % PYQ

CORE = '''\
def scale(x, k=2.0):
    return x * k


def euler(y0, rate, dt, n):
    y = y0
    for _ in range(n):
        y = y + dt * (-rate * y)
    return y
'''

TEST_CORE = '''\
import unittest
from pkg.core import scale


class TestScale(unittest.TestCase):
    def test_scale(self):
        self.assertAlmostEqual(scale(1.5), 3.0, places=7)
'''


class Sandbox(object):
    def __init__(self, testcase):
        self.tmp = tempfile.mkdtemp(prefix="sci-test-")
        testcase.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "tester")
        self.git("config", "commit.gpgsign", "false")
        self.write("pkg/__init__.py", "")
        self.write("pkg/core.py", CORE)
        self.write("tests/__init__.py", "")
        self.write("tests/test_core.py", TEST_CORE)
        self.write("demo.py", "from pkg.core import euler\nfor n in (10, 100):\n    print('n', n, repr(euler(1.0, 1.0, 1.0/n, n)))\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "baseline")

    def git(self, *args):
        p = subprocess.run(["git"] + list(args), cwd=self.tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise AssertionError("git %s failed: %s" % (args, p.stdout))
        return p.stdout

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text) if text.startswith("\n") else text, encoding="utf-8")
        return p

    def read(self, rel):
        return (self.root / rel).read_text(encoding="utf-8")

    def sci(self, *args, **kw):
        exe = self.root / ".sci" / "sci.py"
        script = str(exe if exe.exists() and args[0] != "init" else SCI)
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env.update(kw.get("env") or {})
        p = subprocess.run([PY, script] + list(args), cwd=str(kw.get("cwd") or self.root), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                           timeout=kw.get("timeout", 120))
        return p.returncode, p.stdout

    def ok(self, *args, **kw):
        rc, out = self.sci(*args, **kw)
        if rc != 0:
            raise AssertionError("sci %s exited %d:\n%s" % (" ".join(args), rc, out))
        return out

    def state(self):
        return json.loads(self.read(".sci/state.json"))

    def init(self, objective="Add an RK2 integrator", test_cmd=None):
        args = ["init", "--objective", objective, "--test-cmd", test_cmd or ("%s discover -s tests -q" % UT)]
        return self.ok(*args)

    def add_rk2_task(self, extra=()):
        return self.ok("task", "add", "Add rk2 integrator", "--verify", "%s tests.test_rk2 -q" % UT,
                       "--scope", "pkg/core.py", "--scope", "tests/test_rk2.py", "--inv", "order 2", *extra)

    def write_rk2_test(self):
        self.write("tests/test_rk2.py", '''\
import math, unittest
from pkg.core import rk2


def err(n):
    return abs(rk2(1.0, 1.0, 1.0 / n, n) - math.exp(-1.0))


class TestRK2(unittest.TestCase):
    def test_order(self):
        p = math.log(err(40) / err(80)) / math.log(2.0)
        self.assertGreater(p, 1.9)


if __name__ == "__main__":
    unittest.main()
''')

    def write_rk2(self, good=True):
        body = ("    for _ in range(n):\n        k1 = -rate * y\n        k2 = -rate * (y + dt * k1)\n"
                "        y = y + 0.5 * dt * (k1 + k2)\n    return y\n") if good else (
                "    for _ in range(n):\n        y = y + dt * (-rate * y)\n    return y\n")
        src = self.read("pkg/core.py").split("\n\ndef rk2")[0].rstrip("\n")
        self.write("pkg/core.py", src + "\n\n\ndef rk2(y0, rate, dt, n):\n    y = y0\n" + body)

    def finish_rk2(self):
        """Run T1 (rk2) through the whole loop and commit it."""
        self.init()
        self.add_rk2_task()
        self.ok("task", "start")
        self.write_rk2_test()
        rc, _ = self.sci("check")
        assert rc == 1
        self.ok("lock")
        self.write_rk2()
        self.ok("check")
        self.ok("verify")
        return self.ok("done", "-m", "feat: add rk2")


class TestInit(unittest.TestCase):
    def test_init_creates_private_state(self):
        sb = Sandbox(self)
        out = sb.init()
        self.assertIn("initialized", out)
        self.assertTrue((sb.root / ".sci" / "state.json").exists())
        self.assertTrue(os.access(str(sb.root / ".sci" / "sci"), os.X_OK))
        self.assertNotIn(b"\r", (sb.root / ".sci" / "sci").read_bytes())  # a CRLF shebang breaks Git Bash
        launcher = (sb.root / ".sci" / "sci.cmd").read_bytes()
        self.assertIn(b"%~dp0sci.py", launcher)
        self.assertEqual(launcher.count(b"\n"), launcher.count(b"\r\n"))  # batch files need CRLF
        self.assertEqual(sb.git("status", "--porcelain").strip(), "")
        self.assertIn("/.sci/", sb.read(".git/info/exclude"))
        self.assertIn("NEXT: Plan", out)

    def test_wrapper_runs_from_subdirectory(self):
        sb = Sandbox(self)
        sb.init()
        if WINDOWS:
            launchers = [[str(sb.root / ".sci" / "sci.cmd")]]
            if sci.windows_bash():
                launchers.append([sci.windows_bash(), (sb.root / ".sci" / "sci").as_posix()])
        else:
            launchers = [[str(sb.root / ".sci" / "sci")]]
        for argv in launchers:
            p = subprocess.run(argv + ["resume"], cwd=str(sb.root / "pkg"), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=120)
            self.assertEqual(p.returncode, 0, (argv, p.stdout))
            self.assertIn("OBJECTIVE: Add an RK2 integrator", p.stdout)

    @unittest.skipUnless(WINDOWS, "Windows launchers")
    def test_powershell_and_cmd_find_the_launcher_and_get_exit_codes(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "t", "--verify", "exit 1", "--scope", "x")
        sb.ok("task", "start")

        def run(argv, shell=False):
            return subprocess.run(argv, shell=shell, cwd=str(sb.root), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=120)
        for ps in [x for x in (shutil.which("powershell"), shutil.which("pwsh")) if x]:
            p = run([ps, "-NoProfile", "-NonInteractive", "-Command", ".sci/sci resume; exit $LASTEXITCODE"])
            self.assertEqual(p.returncode, 0, (ps, p.stdout))
            self.assertIn("OBJECTIVE: Add an RK2 integrator", p.stdout)
            p = run([ps, "-NoProfile", "-NonInteractive", "-Command", ".sci/sci check; exit $LASTEXITCODE"])
            self.assertEqual(p.returncode, 1, (ps, p.stdout))
            self.assertIn("FAIL check T1", p.stdout)
        p = run(".sci\\sci resume", shell=True)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("OBJECTIVE: Add an RK2 integrator", p.stdout)
        p = run(".sci\\sci check", shell=True)
        self.assertEqual(p.returncode, 1, p.stdout)
        p = run(".sci\\sci task show T99", shell=True)
        self.assertEqual(p.returncode, 2, p.stdout)  # REFUSED
        p = run(".sci\\sci purge --yes", shell=True)  # deletes the running launcher
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertNotIn("cannot be found", p.stdout)
        self.assertFalse((sb.root / ".sci").exists())

    def test_reinit_keeps_state_and_restores_deleted_state_from_git_backup(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        shutil.rmtree(str(sb.root / ".sci"))
        out = sb.ok("init")
        self.assertIn("restored", out)
        self.assertEqual(sb.state()["tasks"][0]["title"], "Add rk2 integrator")

    def test_corrupt_state_recovers_from_bak(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("note", "units are SI")
        (sb.root / ".sci" / "state.json").write_text("{ not json")
        rc, out = sb.sci("resume")
        self.assertEqual(rc, 0, out)
        self.assertIn("WARNING: state.json was missing or corrupt", out)
        self.assertIn("Add rk2 integrator", out)

    def test_not_a_git_repo_refuses_with_instruction(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = subprocess.run([PY, str(SCI), "init"], cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           encoding="utf-8", errors="replace")
        self.assertEqual(p.returncode, 2)
        self.assertIn("REFUSED: Not inside a git repository", p.stdout)

    def test_imports_old_feature_list(self):
        sb = Sandbox(self)
        sb.write(".agent/feature_list.json", json.dumps({"features": [
            {"id": "PHYS-001", "title": "Verlet", "status": "passing", "verification_cmd": "true", "dependencies": []},
            {"id": "PHYS-002", "title": "MMS", "status": "not_started", "verification_cmd": "true",
             "dependencies": ["PHYS-001"]}]}))
        out = sb.init()
        self.assertIn("imported 2 tasks", out)
        st = sb.state()
        self.assertEqual([t["status"] for t in st["tasks"]], ["done", "todo"])
        self.assertEqual(st["tasks"][1]["deps"], ["T1"])


class TestPlanning(unittest.TestCase):
    def test_task_add_requires_verify(self):
        sb = Sandbox(self)
        sb.init()
        rc, out = sb.sci("task", "add", "do things")
        self.assertEqual(rc, 2)
        self.assertIn("needs --verify", out)

    def test_whole_suite_and_missing_scope_warnings(self):
        sb = Sandbox(self)
        sb.init()
        out = sb.ok("task", "add", "x", "--verify", "%s -m pytest -q" % PY)
        self.assertIn("runs a whole test suite", out)
        self.assertIn("no --scope", out)

    def test_one_active_task_and_dependencies(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "add", "docs", "--verify", "true", "--scope", "README.md", "--dep", "T1")
        rc, out = sb.sci("task", "start", "T2")
        self.assertEqual(rc, 2)
        self.assertIn("depends on unfinished T1", out)
        sb.ok("task", "start")
        rc, out = sb.sci("task", "start", "T2")
        self.assertEqual(rc, 2)
        self.assertIn("already active", out)

    def test_block_asks_human_and_unblock_records_answer(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        out = sb.ok("task", "block", "T1", "--reason", "Which boundary condition: periodic or Dirichlet?")
        self.assertIn("Ask the human", out)
        st = sb.state()
        self.assertIsNone(st["active"])
        self.assertIn("periodic or Dirichlet", st["next"])
        sb.ok("task", "unblock", "T1", "--answer", "periodic")
        st = sb.state()
        self.assertEqual(st["tasks"][0]["status"], "todo")
        self.assertIn("periodic", st["notes"][-1]["text"])


class TestLoop(unittest.TestCase):
    def test_full_loop_commits_only_task_files(self):
        sb = Sandbox(self)
        sb.write("notes_by_human.txt", "untracked file that existed before")
        out = sb.finish_rk2()
        self.assertIn("DONE T1", out)
        log = sb.git("log", "-1", "--format=%B")
        self.assertIn("feat: add rk2 [T1]", log)
        self.assertIn("Verified:", log)
        files = sb.git("show", "--name-only", "--format=", "HEAD").split()
        self.assertEqual(sorted(files), ["pkg/core.py", "tests/test_rk2.py"])
        st = sb.state()
        self.assertEqual(st["tasks"][0]["status"], "done")
        self.assertIn("tests/test_rk2.py", st["protected"])
        self.assertEqual(sb.git("for-each-ref", "refs/sci/T1").strip(), "")
        self.assertTrue((sb.root / "notes_by_human.txt").exists())
        self.assertIn("All tasks done. Run `.sci/sci verify`", st["next"])
        sb.ok("verify")
        self.assertIn("final verify passed. Run `.sci/sci report`", sb.state()["next"])
        sb.write("pkg/core.py", sb.read("pkg/core.py") + "\n# edit after the final verify\n")
        self.assertIn("All tasks done. Run `.sci/sci verify`", sb.ok("resume"))

    def test_check_exit_code_governs_not_output_text(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "t", "--verify", "echo '5 passed in 0.1s'; exit 1", "--scope", "x")
        sb.ok("task", "start")
        rc, out = sb.sci("check")
        self.assertEqual(rc, 1)
        self.assertIn("FAIL check T1", out)
        sb.ok("task", "amend", "T1", "--verify", "echo '3 failed, 1 passed'; exit 0", "--reason", "test exit codes")
        rc, out = sb.sci("check")
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS check T1", out)

    def test_strikes_stop_and_hypotheses(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        for i in range(3):
            sb.write_rk2(good=False)
            sb.write("pkg/core.py", sb.read("pkg/core.py") + "\n# attempt %d\n" % i)
            rc, out = sb.sci("check")
            self.assertEqual(rc, 1)
        self.assertIn("!!! STOP", out)
        st = sb.state()
        self.assertEqual(st["tasks"][0]["strikes"], 3)
        self.assertIn("STOP", st["next"])
        self.assertTrue(list((sb.root / ".sci" / "attempts").glob("T1-strike*.patch")))
        out = sb.ok("hyp", "add", "k2 uses the wrong slope")
        self.assertIn("STOP still active", out)
        sb.ok("hyp", "add", "update is first order (no averaging)")
        out = sb.ok("hyp", "confirm", "H2", "run 3 shows order 1.0 and the code has a single slope")
        self.assertIn("STOP cleared", out)
        sb.ok("hyp", "refute", "H1", "there is no k2 at all")
        out = sb.ok("resume")
        self.assertIn("RULED OUT", out)
        self.assertIn("k2 uses the wrong slope", out)

    def test_rerun_without_edit_is_not_a_strike(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2(good=False)
        for _ in range(4):
            sb.sci("check")
        self.assertEqual(sb.state()["tasks"][0]["strikes"], 1)

    def test_locked_file_edit_fails_check_and_restore_paths_repairs_it(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        original = sb.read("tests/test_rk2.py")
        sb.write_rk2(good=False)
        sb.write("tests/test_rk2.py", original.replace("1.9", "0.9"))
        rc, out = sb.sci("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("LOCKED FILE CHANGED: tests/test_rk2.py", out)
        self.assertIn("sci restore --paths tests/test_rk2.py", out)
        out = sb.ok("restore", "--paths", "tests/test_rk2.py")
        self.assertIn("Restored locked content", out)
        self.assertEqual(sb.read("tests/test_rk2.py"), original)
        self.assertNotIn("Guard", sb.state()["next"])

    def test_done_refuses_unverified_and_changed_after_verify(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        rc, out = sb.sci("done", "-m", "x")
        self.assertEqual(rc, 2)
        self.assertIn("not verified", out)
        sb.ok("verify")
        sb.write("pkg/core.py", sb.read("pkg/core.py") + "\n# late edit\n")
        rc, out = sb.sci("done", "-m", "x")
        self.assertEqual(rc, 2)
        self.assertIn("not verified on the current files", out)
        self.assertIn("Files changed since the last verify", sb.ok("resume"))

    def test_done_refuses_junk_and_out_of_scope(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        sb.write("scratch_plot.py", "print(1)\n")
        sb.write("demo.py", sb.read("demo.py") + "# drive-by edit\n")
        sb.ok("verify")
        rc, out = sb.sci("done", "-m", "x")
        self.assertEqual(rc, 2)
        self.assertIn("scratch_plot.py", out)
        os.remove(str(sb.root / "scratch_plot.py"))
        sb.ok("verify")
        rc, out = sb.sci("done", "-m", "x")
        self.assertEqual(rc, 2)
        self.assertIn("outside T1's scope: demo.py", out)
        out = sb.ok("done", "-m", "x", "--allow-outside", "demo prints the new integrator too")
        self.assertIn("DONE T1", out)
        self.assertEqual(sb.state()["review"][-1]["kind"], "outside-scope")

    def test_done_refuses_debugger_statements(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        sb.write("pkg/core.py", sb.read("pkg/core.py").replace("    y = y0\n    for", "    y = y0\n    breakpoint() if False else None\n    for", 1))
        sb.ok("verify")
        rc, out = sb.sci("done", "-m", "x")
        self.assertEqual(rc, 2)
        self.assertIn("Debugger statements", out)

    def test_done_without_lock_refused_unless_justified(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "readme", "--verify", "grep -q RK2 README.md", "--scope", "README.md")
        sb.ok("task", "start")
        sb.write("README.md", "RK2 integrator\n")
        sb.ok("verify")
        rc, out = sb.sci("done", "-m", "docs")
        self.assertEqual(rc, 2)
        self.assertIn("Nothing is locked", out)
        sb.ok("done", "-m", "docs", "--no-lock", "doc-only task, grep is the check")
        self.assertEqual(sb.state()["review"][-1]["kind"], "no-lock")

    def test_lock_never_locks_the_file_the_task_changes(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "demo uses rk2", "--verify", "grep -q rk2 demo.py", "--scope", "demo.py")
        sb.ok("task", "start")
        out = sb.ok("lock")  # locking before the edit must not freeze demo.py
        self.assertIn("Nothing to lock for T1", out)
        self.assertIn("never locked", out)
        sb.write("demo.py", sb.read("demo.py").replace("euler", "rk2"))
        rc, out = sb.sci("check")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("GUARD", out)
        self.assertIn("Checks pass", sb.state()["next"])
        sb.ok("verify")
        sb.ok("done", "-m", "docs: demo uses rk2")  # no --no-lock needed
        self.assertEqual(sb.state()["review"][-1]["kind"], "no-lock")

    def test_lock_refuses_until_the_named_test_exists(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        rc, out = sb.sci("lock")
        self.assertEqual(rc, 2, out)
        self.assertIn("do not exist yet: tests.test_rk2", out)
        sb.write_rk2_test()
        self.assertIn("Locked for T1: tests/test_rk2.py", sb.ok("lock"))

    def test_lock_with_test_runner_but_no_test_file(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "refactor", "--verify", "%s -m pytest -q -k core" % PYQ, "--scope", "pkg")
        sb.ok("task", "start")
        rc, out = sb.sci("lock")
        self.assertEqual(rc, 2, out)
        self.assertIn("lock --none", out)
        rc, out = sb.sci("lock", "--none", "short")
        self.assertEqual(rc, 2, out)
        out = sb.ok("lock", "--none", "pure refactor guarded by the existing tests")
        self.assertIn("Nothing to lock", out)
        t = sb.state()["tasks"][0]
        self.assertTrue(t["lock_none"])
        self.assertIn("nothing to lock", sb.ok("resume"))

    def test_done_protects_test_files_created_in_the_task(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        sb.write("tests/test_rk2_step.py", "import unittest\nfrom pkg.core import rk2\n\n\nclass T(unittest.TestCase):\n"
                 "    def test_one_step(self):\n        self.assertAlmostEqual(rk2(1.0, 1.0, 0.1, 1), 0.905, places=9)\n")
        sb.ok("check")
        sb.ok("verify")
        out = sb.ok("done", "-m", "feat: rk2")
        self.assertIn("protected tests", out)
        self.assertIn("tests/test_rk2_step.py", sb.state()["protected"])
        sb.ok("task", "add", "tweak", "--verify", "true", "--scope", "pkg/core.py")
        sb.ok("task", "start")
        sb.write("tests/test_rk2_step.py", sb.read("tests/test_rk2_step.py").replace("places=9", "places=2"))
        rc, out = sb.sci("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("PROTECTED TEST CHANGED (locked by T1): tests/test_rk2_step.py", out)

    def test_no_commit_mode_writes_patch(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("config", "set", "commit", "false")
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        sb.ok("verify")
        before = sb.git("rev-parse", "HEAD")
        out = sb.ok("done", "-m", "x")
        self.assertEqual(before, sb.git("rev-parse", "HEAD"))
        self.assertIn("Patch: .sci/patches/T1.patch", out)
        self.assertIn("def rk2", sb.read(".sci/patches/T1.patch"))

    def test_restore_to_green_saves_attempt_and_removes_new_files(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        sb.ok("check")
        green = sb.read("pkg/core.py")
        sb.write("pkg/core.py", green.replace("0.5 * dt", "0.25 * dt"))
        sb.write("pkg/helper.py", "X = 1\n")
        out = sb.ok("restore")
        self.assertIn("green state", out)
        self.assertEqual(sb.read("pkg/core.py"), green)
        self.assertFalse((sb.root / "pkg" / "helper.py").exists())
        patches = list((sb.root / ".sci" / "attempts").glob("T1-attempt1.patch"))
        self.assertTrue(patches)
        self.assertIn("0.25 * dt", patches[0].read_text())
        self.assertIn("helper.py", patches[0].read_text())
        self.assertIn("T1/attempt-1", sb.git("for-each-ref", "refs/sci/"))

    def test_timeout_kills_process_group(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("config", "set", "check_timeout", "2")
        marker = sb.root / "child_alive"
        sb.ok("task", "add", "slow", "--verify", "(sleep 8; touch %s) & sleep 30" % shq(marker),
              "--scope", "x")
        sb.ok("task", "start")
        t0 = time.time()
        rc, out = sb.sci("check")
        self.assertLess(time.time() - t0, 20)
        self.assertEqual(rc, 1)
        self.assertIn("TIMED OUT", out)
        time.sleep(7)
        self.assertFalse(marker.exists(), "background child survived the timeout")

    def test_run_puts_repo_on_pythonpath(self):
        sb = Sandbox(self)
        sb.init()
        sb.write(".sci/scratch/probe.py", "from pkg.core import scale\nprint('scaled', scale(2.0))\n")
        out = sb.ok("run", "%s .sci/scratch/probe.py" % PYQ)
        self.assertIn("scaled 4.0", out)
        self.assertIn("run ", out)


class TestGuard(unittest.TestCase):
    def _start(self, sb, verify=None):
        sb.init()
        sb.ok("task", "add", "tune", "--verify", verify or ("%s tests.test_core -q" % UT), "--scope", "pkg/core.py")
        sb.ok("task", "start")

    def test_existing_test_tolerance_loosened_fails_verify(self):
        sb = Sandbox(self)
        self._start(sb)
        sb.write("tests/test_core.py", TEST_CORE.replace("places=7", "places=2"))
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1)
        self.assertIn("EXISTING TEST WEAKENED: tests/test_core.py: tolerance loosened: places", out)

    def test_existing_test_skip_and_removed_assert_detected(self):
        sb = Sandbox(self)
        self._start(sb)
        sb.write("tests/test_core.py", TEST_CORE.replace("    def test_scale", "    @unittest.skip('later')\n    def test_scale"))
        rc, out = sb.sci("verify")
        self.assertIn("skip/xfail added", out)
        sb.write("tests/test_core.py", TEST_CORE.replace("        self.assertAlmostEqual(scale(1.5), 3.0, places=7)", "        pass"))
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1)
        self.assertIn("assertion(s) removed", out)

    def test_adding_a_new_test_to_existing_file_is_fine(self):
        sb = Sandbox(self)
        self._start(sb)
        sb.write("tests/test_core.py", TEST_CORE.replace(
            "\n\nif __name__", "\n    def test_scale_zero(self):\n        self.assertEqual(scale(0.0), 0.0)\n\n\nif __name__"))
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 0, out)

    def test_protected_tests_of_done_tasks(self):
        sb = Sandbox(self)
        sb.finish_rk2()
        sb.ok("task", "add", "speed", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        text = sb.read("tests/test_rk2.py")
        sb.write("tests/test_rk2.py", text.replace("1.9", "1.5"))
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1)
        self.assertIn("PROTECTED TEST CHANGED (locked by T1): tests/test_rk2.py", out)
        sb.write("tests/test_rk2.py", text + "\n\nclass TestMore(unittest.TestCase):\n    def test_more(self):\n        self.assertEqual(1, 1)\n")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 0, out)
        sb.write("tests/test_rk2.py", text.replace("1.9", "1.5"))
        out = sb.ok("unlock", "tests/test_rk2.py", "--reason", "order test was over-strict for coarse grids")
        self.assertIn("flagged for human review", out)
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sb.state()["review"][-1]["kind"], "test-change")

    def test_regression_of_done_task_blocks_verify(self):
        sb = Sandbox(self)
        sb.finish_rk2()
        sb.ok("task", "add", "refactor", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        sb.write("pkg/core.py", sb.read("pkg/core.py").replace("0.5 * dt * (k1 + k2)", "dt * k1"))
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1)
        self.assertIn("REGRESSION: your change broke T1", out)

    def test_hand_edited_spec_is_detected_and_amend_fixes_it(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        st = sb.state()
        st["tasks"][0]["verify"] = "true"
        (sb.root / ".sci" / "state.json").write_text(json.dumps(st))
        sb.ok("task", "start")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1)
        self.assertIn("SPEC CHANGED OUTSIDE sci", out)
        sb.ok("task", "amend", "T1", "--verify", "%s tests.test_core -q" % UT, "--reason", "restore real check")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 0, out)


class TestGoldenAndBaseline(unittest.TestCase):
    def test_golden_detects_changed_numbers(self):
        sb = Sandbox(self)
        sb.init()
        out = sb.ok("golden", "add", "euler", "--cmd", "%s demo.py" % PYQ, "--rtol", "1e-12")
        self.assertIn("4 numbers", out)
        sb.ok("golden", "check")
        sb.write("pkg/core.py", sb.read("pkg/core.py").replace("y + dt * (-rate * y)", "y + dt * (-rate * y) * 1.0000001"))
        rc, out = sb.sci("golden", "check")
        self.assertEqual(rc, 1)
        self.assertIn("outside tolerance", out)
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1)
        rc, out = sb.sci("golden", "update", "euler")
        self.assertEqual(rc, 2)
        out = sb.ok("golden", "retol", "euler", "--rtol", "1e-3", "--reason", "single precision port needs it")
        self.assertIn("LOOSENED", out)
        self.assertEqual(sb.state()["review"][-1]["kind"], "tolerance-loosened")
        sb.ok("golden", "check")

    def test_golden_refuses_nondeterministic_output(self):
        sb = Sandbox(self)
        sb.init()
        rc, out = sb.sci("golden", "add", "noisy", "--cmd", "%s -c 'import random; print(random.random())'" % PYQ)
        self.assertEqual(rc, 2)
        self.assertIn("not reproducible", out)

    def test_baseline_known_failures_do_not_block_but_new_ones_do(self):
        sb = Sandbox(self)
        sb.write("tests/test_legacy.py", "import unittest\n\nclass TestLegacy(unittest.TestCase):\n    def test_old(self):\n        self.assertEqual(1, 2)\n")
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "legacy failing test")
        sb.init()
        sb.ok("config", "set", "regress_cmd", "%s discover -s tests -t . -v" % UT)
        out = sb.ok("baseline")
        self.assertIn("1 pre-existing failures", out)
        sb.ok("task", "add", "noop", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        out = sb.ok("verify")
        self.assertIn("only the 1 known pre-existing failures", out)
        sb.write("tests/test_new.py", "import unittest\n\nclass TestNew(unittest.TestCase):\n    def test_new(self):\n        self.assertTrue(False)\n")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1)
        self.assertIn("NEW FAILURES", out)
        self.assertIn("test_new", out)

    def test_baseline_enrolls_the_suite_for_verify(self):
        sb = Sandbox(self)
        sb.init()  # test_cmd set, regress_cmd not set
        self.assertIn("reruns this suite", sb.ok("baseline"))
        sb.ok("task", "add", "noop", "--verify", "true", "--scope", "pkg/core.py")
        sb.ok("task", "start")
        self.assertIn("PASS regression suite", sb.ok("verify"))
        sb.write("pkg/core.py", sb.read("pkg/core.py").replace("return x * k", "return x * k + 1.0"))
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("NEW FAILURES", out)


class TestResumeAndHandoff(unittest.TestCase):
    def test_resume_restores_direction_after_compaction(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("note", "positions in metres, time in seconds", "--kind", "units")
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.ok("now", "writing tests/test_rk2.py convergence test; next: sci check")
        sb.ok("hyp", "add", "use Ralston weights")
        sb.ok("hyp", "refute", "H1", "Ralston gives the same order; not the issue")
        out = sb.ok("resume")
        for s in ("OBJECTIVE: Add an RK2 integrator", "ACTIVE T1", "verify:", "order 2", "positions in metres",
                  "use Ralston weights", "writing tests/test_rk2.py", "NEXT:"):
            self.assertIn(s, out)
        self.assertLess(len(out.splitlines()), 60)
        brief = sb.ok("resume", "--brief")
        self.assertIn("[sci] Active T1", brief)
        self.assertIn("[sci] NEXT:", brief)
        handoff = sb.read(".sci/HANDOFF.md")
        self.assertIn("use Ralston weights", handoff)
        self.assertIn("positions in metres", handoff)

    def test_handoff_keeps_failed_approaches_across_tasks(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.ok("hyp", "add", "approach A")
        sb.ok("hyp", "refute", "H1", "diverges")
        sb.ok("task", "block", "T1", "--reason", "need input")
        sb.ok("task", "add", "other", "--verify", "true", "--scope", "x")
        sb.ok("task", "start", "T2")
        self.assertIn("approach A", sb.read(".sci/HANDOFF.md"))

    def test_every_command_prints_anchor(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        out = sb.ok("note", "x")
        self.assertIn("[sci] no active task | NEXT: Start the next task", out)

    def test_report_lists_review_items(self):
        sb = Sandbox(self)
        sb.finish_rk2()
        sb.ok("golden", "add", "e", "--cmd", "%s demo.py" % PYQ)
        sb.ok("golden", "retol", "e", "--rtol", "1e-2", "--reason", "testing the report output")
        out = sb.ok("report")
        self.assertIn("NEEDS YOUR REVIEW", out)
        self.assertIn("T1 [done]", out)


class TestNavigation(unittest.TestCase):
    def test_find_ranks_definitions_and_caps_output(self):
        sb = Sandbox(self)
        for i in range(40):
            sb.write("pkg/use_%02d.py" % i, "from pkg.core import scale\nv = scale(%d)\n" % i)
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "users")
        sb.init()
        out = sb.ok("find", "scale")
        self.assertIn("DEFINITIONS (likely):", out)
        self.assertIn("pkg/core.py:1: def scale(x, k=2.0):", out)
        self.assertIn("more files", out)
        self.assertLess(len(out.splitlines()), 30)

    def test_map_summarizes(self):
        sb = Sandbox(self)
        sb.init()
        out = sb.ok("map")
        self.assertIn("tracked files", out)
        self.assertIn("pkg/", out)


class TestUnits(unittest.TestCase):
    def test_failure_extraction(self):
        lines = ["FAILED tests/test_a.py::test_x - assert 1 == 2", "ERROR tests/test_b.py - ImportError: no",
                 "tests/test_c.py::test_y FAILED  [ 50%]", "FAIL: test_z (tests.test_d.TestD.test_z)",
                 "ERROR: test_w (tests.test_e.TestE)", "--- FAIL: TestGo (0.00s)", "test mod::t ... FAILED",
                 "FAILED (errors=1)", "  3/10 Test  #3: heat_1d ......................***Failed    0.02 sec"]
        self.assertEqual(sci.extract_failures(lines), [
            "tests/test_a.py::test_x", "tests/test_b.py", "tests/test_c.py::test_y", "tests.test_d.TestD.test_z",
            "tests.test_e.TestE.test_w", "TestGo", "mod::t", "heat_1d"])

    def test_weakening_rules(self):
        def issues(old, new, path="tests/test_x.py"):
            diff = "\n".join(__import__("difflib").unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0))
            return sci.weakening_issues(path, sci.parse_hunks(diff), old, new)
        self.assertEqual(issues("assert_allclose(a, b, rtol=1e-8)", "assert_allclose(a, b, rtol=1e-10)"), [])
        self.assertTrue(any("tolerance loosened" in i for i in issues("np.testing.assert_allclose(a, b, rtol=1e-8)",
                                                                      "np.testing.assert_allclose(a, b, rtol=1e-3)")))
        self.assertTrue(any("number changed" in i for i in issues("    assert err < 1e-8\n", "    assert err < 1e-4\n")))
        dup = "def test_a():\n    assert f() == 1\n\ndef test_a():\n    assert True\n"
        self.assertTrue(any("defined twice" in i for i in issues("def test_a():\n    assert f() == 1\n", dup)))
        early = "def test_a():\n    return\n    assert f() == 1\n"
        self.assertTrue(any("early `return`" in i for i in issues("def test_a():\n    assert f() == 1\n", early)))
        self.assertTrue(any("trivial assertion" in i for i in issues("def test_a():\n    assert f() == 1\n",
                                                                     "def test_a():\n    assert True\n")))
        self.assertEqual(issues("x = 1\n", "x = 1\ny = 2\n"), [])

    def test_parse_numbers(self):
        got = sci.parse_numbers(["version v1.2.3 info: energy -1.5e-3 nan inf 2.0D+01 x2 step10 0.5"])
        self.assertEqual(len(got), 5)
        self.assertEqual(got[0], -1.5e-3)
        self.assertTrue(got[1] != got[1])
        self.assertEqual(got[2], float("inf"))
        self.assertEqual(got[3], 20.0)
        self.assertEqual(got[4], 0.5)

    def test_compare_numbers(self):
        self.assertTrue(sci.compare_numbers([1.0, float("nan")], [1.0 + 1e-13, float("nan")], 1e-12, 0)[0])
        self.assertFalse(sci.compare_numbers([1.0], [1.001], 1e-6, 0)[0])
        self.assertFalse(sci.compare_numbers([1.0], [1.0, 2.0], 1e-6, 0)[0])

    def test_order_command(self):
        rc = sci.main(["order", "--h", "0.1", "0.05", "0.025", "--err", "1e-2", "2.5e-3", "6.25e-4", "--expect", "2"])
        self.assertEqual(rc, 0)
        rc = sci.main(["order", "--h", "0.1", "0.05", "--err", "1e-2", "5e-3", "--expect", "2"])
        self.assertEqual(rc, 1)

    def test_glob_matching(self):
        self.assertTrue(sci.match_any("src/heat/solver.py", ["src/heat"]))
        self.assertTrue(sci.match_any("a/b/test_x.py", ["**/test_*.py"]))
        self.assertTrue(sci.match_any("test_x.py", ["**/test_*.py"]))
        self.assertFalse(sci.match_any("src/heatwave.py", ["src/heat"]))
        self.assertTrue(sci.match_any("tests/unit/a.py", ["tests/**"]))

    def test_source_is_python38_compatible(self):
        for path in (SCI, SCI.parent / "sci_hook.py", HERE.parent / "install.py"):
            ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 8))

    def test_shell_selection_and_override(self):
        self.assertEqual(sci.shell_argv({"shell": "cmd"}, "echo hi"), ("echo hi", True))
        self.assertEqual(sci.shell_argv({"shell": "/opt/zsh"}, "x"), (["/opt/zsh", "-c", "x"], False))
        args, use_shell = sci.shell_argv({"shell": "pwsh"}, "echo 'é'")
        self.assertFalse(use_shell)
        self.assertEqual(args[:4], ["pwsh", "-NoProfile", "-NonInteractive", "-EncodedCommand"])
        self.assertEqual(base64.b64decode(args[4]).decode("utf-16-le"), "echo 'é'")
        args, use_shell = sci.shell_argv({}, "x")
        if WINDOWS and not sci.windows_bash():
            self.assertTrue(use_shell)
        else:
            self.assertIn(os.path.basename(args[0]).lower(), ("bash", "bash.exe", "sh"))
            self.assertNotIn("system32", args[0].lower())  # never WSL's bash.exe

    def test_windows_backslash_paths_in_verify_commands(self):
        old = sci.WINDOWS
        sci.WINDOWS = True
        try:
            self.assertEqual(sci.verify_tokens("python -m pytest tests\\test_a.py -q"),
                             ["python", "-m", "pytest", "tests/test_a.py", "-q"])
        finally:
            sci.WINDOWS = old


class TestSnapshot(unittest.TestCase):
    def test_same_second_same_size_rewrite_is_detected(self):
        # Regression: copying the index with a fresh mtime defeated git's racy-entry check.
        sb = Sandbox(self)
        sb.init()
        f = sb.root / "pkg" / "core.py"
        sec = int(time.time()) - 100
        os.utime(str(f), ns=(sec * 10**9 + 100000000, sec * 10**9 + 100000000))
        sb.git("add", "pkg/core.py")
        index = sb.root / ".git" / "index"
        os.utime(str(index), ns=(sec * 10**9 + 200000000, sec * 10**9 + 200000000))
        repo = sci.Repo(sb.root)
        cfg = dict(sci.DEFAULT_CONFIG)
        before, _ = sci.snapshot(repo, cfg)
        text = f.read_text()
        f.write_text(text.replace("x * k", "k * x"))
        os.utime(str(f), ns=(sec * 10**9 + 900000000, sec * 10**9 + 900000000))
        after, _ = sci.snapshot(repo, cfg)
        self.assertNotEqual(before, after)


PYTEST_PY = os.environ.get("SCI_TEST_PYTEST_PY")


@unittest.skipUnless(PYTEST_PY, "set SCI_TEST_PYTEST_PY to a python with pytest to run these")
class TestRealPytest(unittest.TestCase):
    def test_baseline_and_new_failures_with_pytest(self):
        sb = Sandbox(self)
        sb.write("tests/test_a.py", "def test_good():\n    assert 1 == 1\n\ndef test_bad():\n    assert 1 == 2\n")
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "a")
        sb.init(test_cmd="%s -m pytest -q" % shq(PYTEST_PY))
        sb.ok("config", "set", "regress_cmd", "%s -m pytest -q tests" % shq(PYTEST_PY))
        out = sb.ok("baseline")
        self.assertIn("1 pre-existing failures", out)
        self.assertEqual(sb.state()["baseline"]["failures"], ["tests/test_a.py::test_bad"])
        sb.ok("task", "add", "t", "--verify", "%s -m pytest -q tests/test_core.py" % shq(PYTEST_PY),
              "--scope", "pkg/core.py")
        sb.ok("task", "start")
        out = sb.ok("verify")
        self.assertIn("only the 1 known pre-existing failures", out)
        sb.write("tests/test_broken.py", "def test_x(:\n    pass\n")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1)
        self.assertIn("NEW FAILURES (not in baseline): tests/test_broken.py", out)

    def test_pytest_failure_report_is_compact(self):
        sb = Sandbox(self)
        sb.init(test_cmd="%s -m pytest -q" % shq(PYTEST_PY))
        sb.write("tests/test_f.py", "import math\n\ndef test_f():\n    assert math.isclose(0.1 + 0.2, 0.31)\n")
        sb.ok("task", "add", "t", "--verify", "%s -m pytest -q tests/test_f.py" % shq(PYTEST_PY),
              "--scope", "pkg/core.py")
        sb.ok("task", "start")
        rc, out = sb.sci("check")
        self.assertEqual(rc, 1)
        self.assertIn("failing (1): tests/test_f.py::test_f", out)
        self.assertIn("1 failed", out)
        self.assertLess(len(out.splitlines()), 60)

    @unittest.skipIf(WINDOWS, "symlinked POSIX venv layout")
    def test_lock_never_locks_the_interpreter(self):
        sb = Sandbox(self)
        os.symlink(os.path.dirname(os.path.dirname(os.path.abspath(PYTEST_PY))), str(sb.root / ".venv"))
        sb.init(test_cmd="true")
        sb.ok("task", "add", "t", "--verify", ".venv/bin/python -m pytest -q tests/test_core.py", "--scope", "pkg")
        sb.ok("task", "start")
        out = sb.ok("lock")
        self.assertIn("Locked for T1: tests/test_core.py", out)
        self.assertNotIn("python", out.splitlines()[0])

    @unittest.skipIf(WINDOWS, "symlinked POSIX venv layout")
    def test_detects_project_venv(self):
        sb = Sandbox(self)
        os.symlink(os.path.dirname(os.path.dirname(os.path.abspath(PYTEST_PY))), str(sb.root / ".venv"))
        sb.write(".gitignore", ".venv\n")
        sb.git("add", ".gitignore")
        sb.git("commit", "-q", "-m", "ignore venv")
        (sb.root / "pyproject.toml").write_text("[project]\nname='x'\n")
        out = sb.ok("init", "--objective", "x")
        self.assertIn("test_cmd: .venv/bin/python -m pytest -q", out)


class TestBuild(unittest.TestCase):
    def test_build_runs_only_when_compiled_sources_change(self):
        sb = Sandbox(self)
        sb.write("ext/fast.c", "int f(void){return 1;}\n")
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "c source")
        sb.init()
        sb.ok("config", "set", "build_cmd", "echo built >> build.log")
        sb.ok("task", "add", "t", "--verify", "true", "--scope", "ext", "--scope", "build.log")
        sb.ok("task", "start")
        sb.ok("check")
        sb.ok("check")
        self.assertEqual(sb.read("build.log").count("built"), 1)
        sb.write("ext/fast.c", "int f(void){return 2;}\n")
        sb.ok("check")
        self.assertEqual(sb.read("build.log").count("built"), 2)


HOOK = SCI.parent / "sci_hook.py"
sys.path.insert(0, str(HOOK.parent))
import sci_hook  # noqa: E402


class TestHooks(unittest.TestCase):
    def hook(self, mode, payload, cwd=None):
        p = subprocess.run([PY, str(HOOK), mode], input=json.dumps(payload), cwd=cwd or tempfile.gettempdir(),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout)
        return p.stdout

    def test_destructive_detection(self):
        bad = ["git reset --hard HEAD~1", "git checkout -- src/a.py", "git checkout .", "git restore src/a.py",
               "git clean -fdx", "git stash", "git stash push -m x", "git push --force", "git branch -D topic",
               "cd repo && git checkout HEAD -- .", "rm -rf .sci", "git -C x reset --hard", "git switch -f main",
               "git restore --staged --worktree a.py"]
        good = ["git status", "git checkout -b topic", "git checkout main", "git restore --staged a.py",
                "git stash list", "git diff", "rm -rf build/.scikit", "git log --oneline", "git push origin topic",
                "pytest -q", "git commit -m 'fix: x'"]
        for c in bad:
            self.assertIsNotNone(sci_hook.destructive_reason(c), c)
        for c in good:
            self.assertIsNone(sci_hook.destructive_reason(c), c)

    def test_windows_shell_deletes_and_file_uris(self):
        for c in ("Remove-Item -Recurse -Force .sci", "rd /s /q .sci", "del .sci\\state.json",
                  "remove-item .\\.sci\\state.json", "rmdir /s .sci"):
            self.assertIsNotNone(sci_hook.destructive_reason(c), c)
        for c in ("Remove-Item build", "rd /s /q build", "del .scikit", "Remove-Item src\\.scipy"):
            self.assertIsNone(sci_hook.destructive_reason(c), c)
        self.assertEqual(sci_hook.uri_path("/plain/path"), "/plain/path")
        if WINDOWS:
            self.assertEqual(sci_hook.uri_path("file:///C:/work/my%20repo"), "C:\\work\\my repo")
        else:
            self.assertEqual(sci_hook.uri_path("file:///home/me/my%20repo"), "/home/me/my repo")

    def test_agy_hooks_are_neutral_outside_sci_repos(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for mode in ("agy-session-start", "agy-pre-invocation", "agy-stop"):
            self.assertEqual(json.loads(self.hook(mode, {"workspacePaths": [d]})), {})
        out = self.hook("agy-pre-tool", {"workspacePaths": [d], "toolCall": {"name": "run_command",
                                                                             "args": {"CommandLine": "git reset --hard"}}})
        self.assertEqual(json.loads(out), {})
        self.assertEqual(self.hook("claude-prompt", {"cwd": d}), "")

    def test_agy_hooks_in_sci_repo(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        ws = {"workspacePaths": [str(sb.root / "pkg")]}
        out = json.loads(self.hook("agy-pre-invocation", dict(ws, invocationNum=3)))
        msg = out["injectSteps"][0]["ephemeralMessage"]
        self.assertIn("[sci] Active T1", msg)
        self.assertIn("[sci] NEXT:", msg)
        out = json.loads(self.hook("agy-session-start", ws))
        self.assertIn(".sci/sci resume", out["injectSteps"][0]["systemMessage"]["systemMessage"])
        out = json.loads(self.hook("agy-pre-tool", dict(ws, toolCall={"name": "run_command",
                                                                      "args": {"CommandLine": "git stash"}})))
        self.assertEqual(out["decision"], "deny")
        out = json.loads(self.hook("agy-pre-tool", dict(ws, toolCall={"name": "run_command",
                                                                      "args": {"CommandLine": "git status"}})))
        self.assertEqual(out, {})
        self.assertEqual(json.loads(self.hook("agy-stop", dict(ws, terminationReason="NO_TOOL_CALL"))), {})
        sb.ok("config", "set", "autopilot", "true")
        stops = [json.loads(self.hook("agy-stop", dict(ws, terminationReason="NO_TOOL_CALL"))) for _ in range(3)]
        self.assertEqual([s.get("decision") for s in stops], ["continue", "continue", None])
        sb.ok("pause")
        self.assertEqual(json.loads(self.hook("agy-pre-invocation", ws)), {})

    def test_claude_hooks(self):
        sb = Sandbox(self)
        sb.init()
        out = json.loads(self.hook("claude-session-start", {"cwd": str(sb.root)}))["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart")
        self.assertIn("[sci] Objective: Add an RK2 integrator", out["additionalContext"])
        out = json.loads(self.hook("claude-prompt", {"cwd": str(sb.root / "pkg")}))["hookSpecificOutput"]
        self.assertIn("[sci] NEXT:", out["additionalContext"])
        out = json.loads(self.hook("claude-pre-tool", {"cwd": str(sb.root), "tool_name": "Bash",
                                                        "tool_input": {"command": "git checkout -- ."}}))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.hook("claude-pre-tool", {"cwd": str(sb.root), "tool_name": "Bash",
                                                        "tool_input": {"command": "ls"}}), "")
        out = json.loads(self.hook("claude-pre-tool", {"cwd": str(sb.root), "tool_name": "shell",
                                                        "tool_input": {"command": ["bash", "-lc", "git stash"]}}))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")  # Codex list form

    def test_gemini_hooks(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for mode in ("gemini-session-start", "gemini-before-agent", "gemini-before-tool"):
            self.assertEqual(json.loads(self.hook(mode, {"cwd": d})), {})
        sb = Sandbox(self)
        sb.init()
        out = json.loads(self.hook("gemini-before-agent", {"cwd": str(sb.root), "prompt": "go on"}))
        self.assertIn("[sci] NEXT:", out["hookSpecificOutput"]["additionalContext"])
        out = json.loads(self.hook("gemini-before-tool", {"cwd": str(sb.root), "tool_name": "run_shell_command",
                                                           "tool_input": {"command": "git clean -fd"}}))
        self.assertEqual(out["decision"], "deny")
        out = json.loads(self.hook("gemini-before-tool", {"cwd": str(sb.root), "tool_name": "run_shell_command",
                                                           "tool_input": {"command": "pytest -q"}}))
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
