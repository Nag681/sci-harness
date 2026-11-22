#!/usr/bin/env python3
"""Tests for scripts/sci.py. Stdlib only: python3 -m unittest discover -s tests -q

Every test builds a throwaway git repository with a tiny numerical package and drives sci
through its command line, exactly as an agent would.
"""
import ast
import base64
import contextlib
import json
import os
import shlex
import shutil
import signal
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
        # purge deletes the running launcher: from cmd.exe, a calling batch file and PowerShell
        sb.write("purge.cmd", "@echo off\ncall .sci\\sci purge --yes\necho after purge: %errorlevel%\n")
        purges = [(".sci\\sci purge --yes", True), ("purge.cmd", True)]
        purges += [([ps, "-NoProfile", "-NonInteractive", "-Command", ".sci/sci purge --yes; exit $LASTEXITCODE"], False)
                   for ps in (shutil.which("powershell"), shutil.which("pwsh")) if ps]
        for i, (argv, shell) in enumerate(purges):
            if i:
                sb.init()
            p = run(argv, shell=shell)
            self.assertEqual(p.returncode, 0, (argv, p.stdout))
            self.assertIn("Removed .sci/", p.stdout)
            self.assertNotIn("cannot", p.stdout)  # "The system cannot find the path specified."
            self.assertFalse((sb.root / ".sci").exists(), argv)
            if argv == "purge.cmd":
                self.assertIn("after purge: 0", p.stdout)  # the caller keeps running

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

    def test_scope_accepts_comma_separated_lists(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "t", "--verify", "true", "--scope", "a.py, b.py", "--scope", "c")
        self.assertEqual(sb.state()["tasks"][0]["scope"], ["a.py", "b.py", "c"])
        sb.ok("task", "start")
        sb.ok("task", "scope", "d.py,a.py")
        self.assertEqual(sb.state()["tasks"][0]["scope"], ["a.py", "b.py", "c", "d.py"])

    def test_human_only_refusals_do_not_hand_the_agent_the_command(self):
        sb = Sandbox(self)
        sb.init()
        for args in (("purge",), ("review", "--clear")):
            rc, out = sb.sci(*args)
            self.assertEqual(rc, 2, out)
            do = [ln for ln in out.splitlines() if ln.startswith("DO:")]
            self.assertTrue(do, out)
            self.assertNotIn("--yes", do[0])
        self.assertTrue((sb.root / ".sci").is_dir())

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
        self.assertIn("final verify passed. Run `.sci/sci report`", st["next"])  # T1's verify was the final one
        self.assertEqual(st["last_verify"]["by_done"], "T1")
        sb.write("pkg/core.py", sb.read("pkg/core.py") + "\nEDITED_AFTER_FINAL_VERIFY = 1\n")
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
        out = sb.ok("hyp", "confirm", "H2", "the code has a single slope")
        self.assertIn("STOP still active: need an experiment", out)  # a verdict alone does not lift it
        out = sb.ok("run", "%s -c \"print('order 1.0')\"" % PYQ)
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
        sb.write("pkg/core.py", sb.read("pkg/core.py") + "\nLATE_EDIT = 1\n")
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

    def test_golden_warns_about_exact_zeros_with_atol_zero(self):
        sb = Sandbox(self)
        sb.init()
        out = sb.ok("golden", "add", "z", "--cmd", "echo 'momentum 0.0 energy 1.5'")
        self.assertIn("1 of the numbers are exactly 0", out)
        out = sb.ok("golden", "add", "z2", "--cmd", "echo 'momentum 0.0 energy 1.5'", "--atol", "1e-14")
        self.assertNotIn("exactly 0", out)

    def test_intended_golden_change_leads_back_to_verify(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("golden", "add", "euler", "--cmd", "%s demo.py" % PYQ)
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        sb.write("pkg/core.py", sb.read("pkg/core.py").replace("y + dt * (-rate * y)", "y + dt * (-rate * y) * 1.0000001"))
        sb.ok("check")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("NEXT: Golden euler changed", out)
        rc, out = sb.sci("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL golden euler", out)
        out = sb.ok("golden", "update", "euler", "--reason", "euler now applies the corrected rate")
        self.assertIn("NEXT: Golden euler is up to date. Run `.sci/sci verify` again.", out)
        self.assertNotIn("uncommitted changes", out)
        sb.ok("verify")
        sb.ok("done", "-m", "feat: rk2 and corrected euler")

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


class TestReuse(unittest.TestCase):
    def _rk2_checked(self, sb):
        """T1 (rk2) started, locked and passing its check."""
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        sb.ok("check")

    def test_verify_reuses_passes_on_identical_code(self):
        sb = Sandbox(self)
        self._rk2_checked(sb)
        out = sb.ok("verify")
        self.assertIn("PASS task T1: reused (passed on identical code", out)
        out = sb.ok("verify", "--fresh")
        self.assertNotIn("reused", out)
        self.assertIn("PASS task T1: exit 0", out)
        sb.write("pkg/core.py", sb.read("pkg/core.py") + "\n# edited\n")
        self.assertNotIn("reused", sb.ok("verify"))

    def test_suite_runs_earlier_tasks_tests_once_and_blames_the_task(self):
        sb = Sandbox(self)
        sb.finish_rk2()
        self.assertIn("reruns this suite", sb.ok("baseline"))
        sb.ok("task", "add", "refactor", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        out = sb.ok("verify")
        self.assertIn("note: the regression suite runs the tests of T1, so their checks do not run separately.", out)
        self.assertNotIn("regression T1", out)
        sb.write("pkg/core.py", sb.read("pkg/core.py").replace("0.5 * dt * (k1 + k2)", "dt * k1"))
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("REGRESSION: your change broke T1's tests (test_rk2.TestRK2.test_order)", out)

    def _tune_verified(self, sb, verify=None):
        sb.init()
        sb.ok("task", "add", "tune", "--verify", verify or ("%s tests.test_core -q" % UT), "--scope", "pkg/core.py")
        for c in (("task", "start"), ("check",), ("lock",), ("verify",)):
            sb.ok(*c)

    def test_comments_and_layout_in_python_need_no_new_verify(self):
        sb = Sandbox(self)
        self._tune_verified(sb)
        relaid = "# Scaling and a simple integrator.\n\n\n" + CORE.replace(
            "    return x * k", "    return (x\n            * k)  # k scales")
        sb.write("pkg/core.py", relaid)
        out = sb.ok("resume")
        self.assertIn('NEXT: Verified. Run `.sci/sci done -m "<summary>"`', out)
        sb.write("pkg/core.py", relaid.replace("    return (x", '    """Return x times k."""\n    return (x'))
        rc, out = sb.sci("done", "-m", "tune")
        self.assertEqual(rc, 2, out)
        self.assertIn("not verified on the current files", out)
        sb.write("pkg/core.py", relaid)
        sb.write("tests/test_core.py", "# a comment in a locked test\n" + TEST_CORE)
        rc, out = sb.sci("done", "-m", "tune")
        self.assertEqual(rc, 2, out)
        sb.write("tests/test_core.py", TEST_CORE)
        if not WINDOWS:
            os.chmod(str(sb.root / "pkg" / "core.py"), 0o755)
            rc, out = sb.sci("done", "-m", "tune")
            self.assertEqual(rc, 2, out)
            os.chmod(str(sb.root / "pkg" / "core.py"), 0o644)
        out = sb.ok("done", "-m", "tune")
        self.assertIn("comments or layout", out)
        self.assertIn("pkg/core.py", sb.git("show", "--stat", "HEAD"))

    def test_a_check_that_reads_source_text_sees_comment_edits(self):
        for verify in ("%s tests.test_core -q && grep -q scale pkg/core.py" % UT,
                       "%s tests.test_core -q  # make lint" % UT):
            sb = Sandbox(self)
            self._tune_verified(sb, verify)
            sb.write("pkg/core.py", "# a comment\n" + CORE)
            rc, out = sb.sci("done", "-m", "tune")
            self.assertEqual(rc, 2, out)
            self.assertIn("not verified on the current files", out)

    def test_with_goldens_comments_must_keep_line_numbers(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("golden", "add", "euler", "--cmd", "%s demo.py" % PYQ)
        sb.ok("task", "add", "tune", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        for c in (("task", "start"), ("check",), ("lock",), ("verify",)):
            sb.ok(*c)
        sb.write("pkg/core.py", "# moves every line down\n" + CORE)
        rc, out = sb.sci("done", "-m", "tune")
        self.assertEqual(rc, 2, out)
        sb.write("pkg/core.py", CORE.replace("    return x * k", "    return x * k  # same line"))
        self.assertIn("comments or layout", sb.ok("done", "-m", "tune"))

    @unittest.skipIf(WINDOWS, "a POSIX shell script stands in for ctest")
    def test_a_ctest_suite_runs_earlier_checks_once(self):
        sb = Sandbox(self)
        sb.write("bin/ctest", "#!/bin/sh\necho '100% tests passed, 0 tests failed out of 2'\n")
        os.chmod(str(sb.root / "bin" / "ctest"), 0o755)
        env = {"PATH": str(sb.root / "bin") + os.pathsep + os.environ.get("PATH", "")}
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "ctest")
        sb.init()
        sb.ok("config", "set", "regress_cmd", "ctest --test-dir build")
        sb.ok("task", "add", "heat", "--verify", "ctest --test-dir build -R heat", "--scope", "pkg/core.py")
        sb.ok("task", "add", "wave", "--verify", "ctest --test-dir build -R wave", "--scope", "pkg/core.py")
        none = ("lock", "--none", "ctest selects the tests by name")
        for c in (("task", "start"), ("check",), none, ("verify",), ("done", "-m", "T1"), ("task", "start"), ("check",),
                  none):
            sb.ok(*c, env=env)
        out = sb.ok("verify", env=env)
        self.assertIn("note: the regression suite runs the tests of T1", out)
        self.assertNotIn("regression T1", out)

    def test_done_accepts_doc_edits_after_verify_unless_a_test_reads_the_doc(self):
        sb = Sandbox(self)
        sb.write("README.md", "# demo\n")
        sb.write("NOTES.md", "numbers\n")
        sb.write("tests/test_notes.py", '"""Checks the claims of README.md."""\nimport unittest\n\n\n'
                 'class TestNotes(unittest.TestCase):\n    def test_notes(self):\n'
                 '        with open("NOTES.md") as f:\n            self.assertIn("numbers", f.read())\n')
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "docs")
        self._rk2_checked(sb)
        sb.ok("verify")
        sb.write("NOTES.md", "numbers and more\n")
        rc, out = sb.sci("done", "-m", "feat: rk2")
        self.assertEqual(rc, 2, out)
        self.assertIn("not verified on the current files", out)
        sb.write("NOTES.md", "numbers\n")
        sb.write("README.md", "# demo\n\nNow with rk2.\n")
        out = sb.ok("done", "-m", "feat: rk2")
        self.assertIn("Only documentation, comments or layout changed since verify (README.md)", out)
        self.assertIn("README.md", sb.git("show", "--name-only", "--format=", "HEAD").split())

    def test_a_docs_task_reuses_the_suite(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("baseline")
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        sb.write_rk2()
        sb.ok("check")
        self.assertIn("PASS regression suite: exit 0", sb.ok("verify"))
        sb.ok("done", "-m", "feat: rk2")
        sb.ok("task", "add", "document rk2", "--verify", "grep -q rk2 NOTES.md", "--scope", "NOTES.md")
        sb.ok("task", "start")
        sb.write("NOTES.md", "rk2 is second order in dt\n")
        sb.ok("check")
        sb.ok("lock")
        out = sb.ok("verify")
        self.assertIn("PASS task T2: reused", out)
        self.assertIn("PASS regression suite: reused", out)  # the suite cannot read NOTES.md; its check can
        sb.ok("done", "-m", "docs: rk2")

    def test_ignored_tracked_files_do_not_change_snapshots_or_scope(self):
        sb = Sandbox(self)
        sb.write("graphify-out/GRAPH_REPORT.md", "v1\n")
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "graph")
        sb.init()
        sb.ok("task", "add", "tune", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        sb.ok("check")
        tree = sb.state()["tasks"][0]["last_check"]["tree"]
        sb.write("graphify-out/GRAPH_REPORT.md", "v2, regenerated\n")
        out = sb.ok("check")
        self.assertEqual(sb.state()["tasks"][0]["last_check"]["tree"], tree)
        self.assertNotIn("OUT OF SCOPE", out)
        sb.ok("lock")
        sb.ok("verify")
        self.assertIn("files: (no changes)", sb.ok("done", "-m", "chore: nothing to change"))
        self.assertIn("graphify-out/GRAPH_REPORT.md", sb.git("status", "--porcelain"))

    def test_hyp_verdicts_check_the_id(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "a", "--verify", "true", "--scope", "x")
        sb.ok("task", "add", "b", "--verify", "true", "--scope", "y")
        sb.ok("task", "start", "T1")
        sb.ok("hyp", "add", "cause A")
        sb.ok("hyp", "confirm", "H1", "probe shows A")
        rc, out = sb.sci("hyp", "refute", "H1", "evidence meant for H2")
        self.assertEqual(rc, 2, out)
        self.assertIn("H1 is already confirmed (evidence: probe shows A)", out)
        sb.ok("hyp", "refute", "H1", "a second probe contradicts A", "--revise")
        h = sb.state()["hyps"][0]
        self.assertEqual((h["status"], h["history"][0]["evidence"]), ("refuted", "probe shows A"))
        sb.ok("task", "block", "T1", "--reason", "need data")
        sb.ok("task", "start", "T2")
        rc, out = sb.sci("hyp", "confirm", "H1", "x")
        self.assertEqual(rc, 2, out)
        self.assertIn("H1 belongs to T1, not to the active task T2", out)

    def test_config_add_and_remove_list_items(self):
        sb = Sandbox(self)
        sb.init()
        before = sb.read(".sci/config.json")
        sb.ok("config", "add", "ignore_globs", "data/**")
        globs = json.loads(sb.read(".sci/config.json"))["ignore_globs"]
        self.assertEqual(globs[:-1], json.loads(before)["ignore_globs"])
        self.assertEqual(globs[-1], "data/**")
        sb.ok("config", "remove", "ignore_globs", "data/**")
        self.assertNotIn("data/**", json.loads(sb.read(".sci/config.json"))["ignore_globs"])
        rc, out = sb.sci("config", "add", "check_timeout", "5")
        self.assertEqual(rc, 2, out)
        self.assertIn("check_timeout is not a list setting", out)

    def test_run_numbers_never_repeat(self):
        sb = Sandbox(self)
        sb.init()
        logs = lambda: sorted(p.name for p in (sb.root / ".sci" / "runs").glob("*.log"))  # noqa: E731
        sb.ok("run", "echo one")
        first = logs()
        st = sb.state()
        st["runs"] = 0  # as saved by a command that loaded the state before the run
        (sb.root / ".sci" / "state.json").write_text(json.dumps(st))
        sb.ok("run", "echo two")
        self.assertEqual(len(logs()), len(first) + 1)


SLOW_CHECK = '''\
import os, sys, time
gate, t0 = sys.argv[1], time.time()
while not os.path.exists(gate) and time.time() - t0 < 90:
    time.sleep(0.1)
sys.exit(0 if os.path.exists(gate) else 1)
'''


def kill_job(job):
    for pid in (job.get("pid"), job.get("child")):
        if not pid:
            continue
        if WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        else:
            with contextlib.suppress(OSError):
                os.killpg(int(pid), signal.SIGKILL)


ADD_TEST = '''\
import unittest
from pkg.calc import add


class T(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)
'''
MUL_TEST = '''\
import unittest
from pkg.calc import mul


class T(unittest.TestCase):
    def test_mul(self):
        self.assertEqual(mul(2, 3), 6)
'''


class TestFailedVerify(unittest.TestCase):
    def calc(self, k=0):
        return "def add(a, b):\n    return a + b + %d\n\n\ndef mul(a, b):\n    return a * b\n" % k

    def _regressed(self, sb):
        """T1 (add) done; T2 (mul) locked, and its verify failed because the change broke T1's check."""
        sb.write("pkg/calc.py", "def add(a, b):\n    return a + b\n")
        sb.write("tests/test_add.py", ADD_TEST)
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "add")
        sb.init()
        sb.ok("task", "add", "add works", "--verify", "%s tests.test_add -q" % UT, "--scope", "pkg/calc.py")
        sb.ok("task", "add", "mul", "--verify", "%s tests.test_mul -q" % UT, "--scope", "pkg/calc.py")
        for c in (("task", "start"), ("check",), ("lock",), ("verify",), ("done", "-m", "T1")):
            sb.ok(*c)
        sb.ok("task", "start")
        sb.write("tests/test_mul.py", MUL_TEST)
        sb.sci("check")
        sb.ok("lock")
        sb.write("pkg/calc.py", self.calc(1))
        sb.ok("check")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        return out

    def test_check_reruns_what_verify_failed_and_counts_strikes(self):
        sb = Sandbox(self)
        out = self._regressed(sb)
        self.assertIn("REGRESSION: your change broke T1's check", out)
        self.assertIn("it also reruns what failed the last verify (regression T1)", out)
        for k in (2, 3, 4):
            sb.write("pkg/calc.py", self.calc(k))
            rc, out = sb.sci("check")
            self.assertEqual(rc, 1, out)
            self.assertIn("FAIL regression T1", out)
        self.assertIn("STOP", out)
        self.assertEqual(sb.state()["tasks"][1]["strikes"], 3)
        sb.write("pkg/calc.py", self.calc(0))
        out = sb.ok("check")
        self.assertIn("PASS regression T1", out)
        self.assertIn("STOP cleared", out)
        self.assertIn("PASS regression T1: reused", sb.ok("verify"))
        sb.ok("done", "-m", "T2")

    def test_restarting_a_task_forgets_its_old_verify_failure(self):
        sb = Sandbox(self)
        self._regressed(sb)
        sb.ok("task", "block", "T2", "--reason", "parked while the human decides")
        sb.ok("task", "unblock", "T2")
        sb.ok("task", "start", "T2")
        self.assertIsNone(sb.state()["tasks"][1].get("pending"))

    def test_a_guard_violation_still_strikes_when_the_rerun_fails(self):
        sb = Sandbox(self)
        self._regressed(sb)
        sb.write("pkg/calc.py", self.calc(2))
        sb.write("tests/test_add.py", ADD_TEST.replace("add(2, 3), 5)", "add(2, 3), 5)  # touched"))
        rc, out = sb.sci("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("PROTECTED TEST CHANGED", out)
        self.assertIn("FAIL regression T1", out)
        self.assertEqual(sb.state()["tasks"][1]["strikes"], 1)

    def test_a_test_that_must_follow_an_api_change_costs_no_strike(self):
        sb = Sandbox(self)
        sb.write("pkg/calc.py", "def add(a, b):\n    return a + b\n")
        sb.write("tests/test_add.py", ADD_TEST)
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "add")
        sb.init()
        sb.ok("task", "add", "add works", "--verify", "%s tests.test_add -q" % UT, "--scope", "pkg/calc.py")
        sb.ok("task", "add", "keyword api", "--verify", "%s tests.test_mul -q" % UT, "--scope", "pkg/calc.py")
        for c in (("task", "start"), ("check",), ("lock",), ("verify",), ("done", "-m", "T1")):
            sb.ok(*c)
        sb.ok("task", "start")
        sb.write("tests/test_mul.py", MUL_TEST.replace("mul(2, 3)", "mul(x=2, y=3)"))
        sb.sci("check")
        sb.ok("lock")
        sb.write("pkg/calc.py", "def add(x, y):\n    return x + y\n\n\ndef mul(x, y):\n    return x * y\n")
        sb.write("tests/test_add.py", ADD_TEST.replace("add(2, 3)", "add(x=2, y=3)"))
        rc, out = sb.sci("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("PROTECTED TEST CHANGED", out)
        self.assertIn("must follow an intended change of this task", out)
        self.assertEqual(sb.state()["tasks"][1]["strikes"], 0)
        sb.ok("unlock", "tests/test_add.py", "--reason", "calls add() with the new keyword names")
        sb.ok("check")
        sb.ok("verify")

    def test_suite_failure_is_rerun_narrowly_then_whole_when_tests_depend_on_each_other(self):
        sb = Sandbox(self)
        sb.write("pkg/state.py", "SEEN = []\n\n\ndef record(x):\n    return list(SEEN) + [x]\n")
        sb.write("tests/test_a.py", "import unittest\nfrom pkg import state\n\n\nclass T(unittest.TestCase):\n"
                                    "    def test_a(self):\n        self.assertEqual(state.record(1), [1])\n")
        sb.write("tests/test_b.py", "import unittest\nfrom pkg import state\n\n\nclass T(unittest.TestCase):\n"
                                    "    def test_b(self):\n        self.assertEqual(state.SEEN, [])\n")
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "state")
        sb.init()
        sb.ok("baseline", "--cmd", "%s discover -s . -p 'test_*.py'" % UT)
        sb.ok("task", "add", "record remembers", "--verify", "%s tests.test_a -q" % UT, "--scope", "pkg/state.py")
        sb.ok("task", "start")
        sb.ok("check")
        sb.ok("lock")
        sb.write("pkg/state.py", "SEEN = []\n\n\ndef record(x):\n    SEEN.append(x)\n    return list(SEEN)\n")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("NEW FAILURES (not in baseline): tests.test_b.T.test_b", out)
        out = sb.ok("check")
        self.assertIn("tests.test_b.T.test_b", out)
        self.assertIn("PASS regression suite", out)
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("pass when they run alone but fail in the full suite", out)
        rc, out = sb.sci("check")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL regression suite", out)
        sb.write("pkg/state.py", "SEEN = []\n\n\ndef record(x):\n    return list(SEEN) + [x]\n")
        sb.ok("check")
        sb.ok("verify")
        self.assertIsNone(sb.state()["tasks"][0].get("pending"))


class TestBackground(unittest.TestCase):
    def _slow_task(self, sb):
        """T1 whose check runs until the returned gate file exists."""
        gate = Path(tempfile.mkdtemp(prefix="sci-gate-"))
        self.addCleanup(shutil.rmtree, str(gate), True)
        sb.write("slow_check.py", SLOW_CHECK)
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "slow check")
        sb.init()
        sb.ok("task", "add", "slow", "--verify", "%s slow_check.py %s" % (PYQ, shq(gate / "go")),
              "--scope", "pkg/core.py")
        sb.ok("task", "start")
        return gate / "go"

    def _job(self, sb, want=("pid", "child")):
        """The newest job record, once its worker has recorded `want`."""
        jobs = sb.root / ".sci" / "jobs"
        n = max(int(p.stem) for p in jobs.glob("*.json") if p.stem.isdigit())
        deadline, j = time.time() + 60, {}
        while time.time() < deadline:
            with contextlib.suppress(OSError, ValueError):
                j = json.loads((jobs / ("%d.json" % n)).read_text())
                if all(j.get(k) for k in want):
                    return j
            time.sleep(0.2)
        raise AssertionError("job %d never recorded %s: %s" % (n, want, j))

    def test_long_check_runs_in_background_and_wait_collects_it(self):
        sb = Sandbox(self)
        go = self._slow_task(sb)
        rc, out = sb.sci("check", "--wait", "1")
        self.assertEqual(rc, sci.JOB_RUNNING, out)
        self.assertIn("STILL RUNNING in the background", out)
        self.assertIn("NEXT: `.sci/sci wait`", out)
        out = sb.ok("note", "slow_check.py polls a gate file", "--kind", "code")
        self.assertIn("NEXT: Wait for the running check T1", out)
        sb.ok("hyp", "add", "the gate never opens")
        for cmd in (("verify",), ("check",), ("lock",)):
            rc, out = sb.sci(*cmd)
            self.assertEqual(rc, 2, out)
            self.assertIn("check T1 is still running (job", out)
            self.assertIn("`.sci/sci wait`", out)
        self.assertIn("RUNNING: check T1", sb.ok("resume"))
        rc, out = sb.sci("wait", "--secs", "1")
        self.assertEqual(rc, sci.JOB_RUNNING, out)
        go.write_text("open")
        rc, out = sb.sci("wait", "--secs", "90")
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS check T1", out)
        st = sb.state()
        self.assertTrue(st["tasks"][0]["last_check"]["ok"])
        self.assertIn("polls a gate file", json.dumps(st["notes"]))
        self.assertEqual(st["hyps"][0]["text"], "the gate never opens")
        self.assertIn("already finished (exit 0)", sb.ok("wait"))

    def test_edits_while_a_check_runs_void_its_result(self):
        sb = Sandbox(self)
        go = self._slow_task(sb)
        rc, out = sb.sci("check", "--wait", "1")
        self.assertEqual(rc, sci.JOB_RUNNING, out)
        self._job(sb)  # the command runs, so its snapshot is taken
        sb.write("pkg/core.py", sb.read("pkg/core.py") + "\n# edited mid-run\n")
        go.write_text("open")
        rc, out = sb.sci("wait", "--secs", "90")
        self.assertEqual(rc, 1, out)
        self.assertIn("FILES EDITED WHILE THE CHECK RAN: pkg/core.py", out)
        self.assertIsNone(sb.state()["tasks"][0].get("last_check"))
        self.assertIn("PASS check T1", sb.ok("check"))

    def test_output_a_check_rewrites_is_not_an_edit(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "t", "--verify", "%s -c \"import time; open('out.csv', 'w').write(repr(time.time()))\""
              % PYQ, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        sb.ok("check")
        out = sb.ok("check")
        self.assertNotIn("FILES EDITED", out)
        self.assertTrue(sb.state()["tasks"][0]["last_check"]["ok"])

    def test_cancel_stops_a_running_job(self):
        sb = Sandbox(self)
        self._slow_task(sb)
        rc, out = sb.sci("check", "--wait", "1")
        self.assertEqual(rc, sci.JOB_RUNNING, out)
        self._job(sb)
        self.assertIn("Stopped job", sb.ok("cancel"))
        self.assertNotIn("RUNNING", sb.ok("resume"))
        self.assertIn("No background job is running", sb.ok("cancel"))
        self.assertEqual(sb.state()["tasks"][0].get("last_check"), None)

    def test_killed_worker_is_reported_as_interrupted(self):
        sb = Sandbox(self)
        go = self._slow_task(sb)
        rc, out = sb.sci("check", "--wait", "1")
        self.assertEqual(rc, sci.JOB_RUNNING, out)
        job = self._job(sb)
        kill_job(job)
        rc, out = sb.sci("wait", "--secs", "60")
        self.assertEqual(rc, 1, out)
        self.assertIn("INTERRUPTED: background job %d (check T1)" % job["n"], out)
        self.assertIn("INTERRUPTED: background job %d" % job["n"], sb.ok("resume"))
        go.write_text("open")
        self.assertIn("PASS check T1", sb.ok("check"))

    def test_foreground_mode(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "t", "--verify", "true", "--scope", "x")
        sb.ok("task", "start")
        jobs = lambda: len(list((sb.root / ".sci" / "jobs").glob("*.json")))  # noqa: E731
        sb.ok("check")
        n = jobs()
        self.assertGreater(n, 0)
        sb.ok("config", "set", "background", "false")
        self.assertIn("PASS check T1", sb.ok("check"))
        self.assertIn("PASS check T1", sb.ok("check", env={"SCI_BACKGROUND": "0"}))
        self.assertEqual(jobs(), n)


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

    def test_resume_notes_a_newer_installed_sci(self):
        sb = Sandbox(self)
        sb.init()
        data = Path(tempfile.mkdtemp(prefix="sci-data-"))
        self.addCleanup(shutil.rmtree, str(data), True)
        self.assertNotIn("is installed", sb.ok("resume", env={"XDG_DATA_HOME": str(data)}))
        inst = data / "agent-skills" / "sci" / "scripts" / "sci.py"
        inst.parent.mkdir(parents=True)
        inst.write_text('VERSION = "99.0.0"\n')
        out = sb.ok("resume", env={"XDG_DATA_HOME": str(data)})
        self.assertIn("note: sci 99.0.0 is installed; this project still runs %s" % sci.VERSION, out)

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
    def test_test_files_next_to_the_code_are_a_python_project(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "notes.txt").write_text("x\n", encoding="utf-8")
            self.assertEqual(sci.detect_toolchain(d)[0], "")
            Path(d, "test_bill.py").write_text("import unittest\n", encoding="utf-8")
            Path(d, "tests").mkdir()
            self.assertRegex(sci.detect_toolchain(d)[0], r" -m (pytest -q|unittest discover -s \. -q)$")

    def test_failure_extraction(self):
        lines = ["FAILED tests/test_a.py::test_x - assert 1 == 2", "ERROR tests/test_b.py - ImportError: no",
                 "tests/test_c.py::test_y FAILED  [ 50%]", "FAIL: test_z (tests.test_d.TestD.test_z)",
                 "ERROR: test_w (tests.test_e.TestE)", "--- FAIL: TestGo (0.00s)", "test mod::t ... FAILED",
                 "FAILED (errors=1)", "  3/10 Test  #3: heat_1d ......................***Failed    0.02 sec"]
        self.assertEqual(sci.extract_failures(lines), [
            "tests/test_a.py::test_x", "tests/test_b.py", "tests/test_c.py::test_y", "tests.test_d.TestD.test_z",
            "tests.test_e.TestE.test_w", "TestGo", "mod::t", "heat_1d"])

    def test_weakening_rules_beyond_python(self):
        def issues(old, new, path):
            diff = "\n".join(__import__("difflib").unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0))
            return sci.weakening_issues(path, sci.parse_hunks(diff), old, new)
        js = "test('adds', () => {\n  expect(add(1, 2)).toBe(3)\n})\n"
        self.assertTrue(any("skip/xfail" in i for i in issues(js, js.replace("test(", "test.only("), "src/a.test.js")))
        cs = "[Fact]\npublic void Adds() { Assert.Equal(3, Add(1, 2)); }\n"
        self.assertTrue(any("skip/xfail" in i for i in issues(cs, cs.replace("[Fact]", '[Fact(Skip = "later")]'),
                                                              "tests/CalcTests.cs")))
        self.assertTrue(any("removed" in i for i in issues(cs, cs.replace("Assert.Equal(3, Add(1, 2));", ""),
                                                           "tests/CalcTests.cs")))
        go = "func TestAdd(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Errorf(\"bad\")\n\t}\n}\n"
        self.assertTrue(any("removed" in i for i in issues(go, go.replace('\t\tt.Errorf("bad")\n', ""), "calc_test.go")))
        for p in ("src/__tests__/cart.js", "src/test/java/CartTest.java", "Shop.Tests/CartTests.cs"):
            self.assertTrue(sci.is_test(p, sci.DEFAULT_CONFIG), p)

    def test_failure_extraction_beyond_python(self):
        lines = [" FAIL  src/sum.test.js", " FAIL  src/math.test.ts > adds > negatives",
                 "[ERROR] com.example.FooTest.testBar -- Time elapsed: 0.012 s <<< FAILURE!",
                 "[ERROR] testBaz(com.example.FooTest)  Time elapsed: 0.01 s  <<< ERROR!",
                 "com.example.BarTest > testQux() FAILED", "  Failed Shop.Tests.CartTests.Empty [12 ms]",
                 "rspec ./spec/cart_spec.rb:12 # Cart is empty", "1) Tests\\CartTest::testEmpty",
                 "FAIL: not a jest line", "1) mocha suite", "Failed upload [1/3]", "1) Error::message is shown",
                 "Notes > see FAILED"]
        self.assertEqual(sci.extract_failures(lines), [
            "src/sum.test.js", "src/math.test.ts > adds > negatives", "com.example.FooTest.testBar", "com.example.FooTest.testBaz",
            "com.example.BarTest.testQux", "Shop.Tests.CartTests.Empty", "./spec/cart_spec.rb:12",
            "Tests\\CartTest::testEmpty"])
        self.assertEqual(sci.extract_summary(["Tests run: 10, Failures: 1, Errors: 0, Skipped: 0",
                                              "10 tests completed, 1 failed"]),
                         ["Tests run: 10, Failures: 1, Errors: 0, Skipped: 0", "10 tests completed, 1 failed"])
        self.assertEqual(sci.extract_summary(["Failed!  - Failed:     1, Passed:     9, Skipped:     0, Total:    10",
                                              "10 examples, 1 failure"]),
                         ["Failed!  - Failed:     1, Passed:     9, Skipped:     0, Total:    10",
                          "10 examples, 1 failure"])

    def test_failure_ids_and_counts_of_cargo_jest_node_and_tap(self):
        lines = ["running 2 tests", ". 1/2", "tests::broken --- FAILED", "---- tests::broken stdout ----",
                 "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s",
                 "---- integ_bad stdout ----", "test src/lib.rs - add (line 3) ... FAILED",
                 "test result: FAILED. 1 passed; 2 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s",
                 "        FAIL [   0.004s] (2/4) geo::integ edge_case",
                 " FAIL  src/cart.test.ts", "  ● cart › totals", "  ● Console", " PASS  src/ok.test.ts",
                 "  ● not a failure", "Tests:       1 failed, 1 passed, 2 total",
                 "test at test/cart.test.mjs:5:3", "✖ totals (0.57ms)", "ℹ fail 1",
                 "not ok 3 - flaky # TODO later", "not ok 4 - real bad"]
        self.assertEqual(sci.extract_failures(lines), [
            "tests::broken", "integ_bad", "src/lib.rs - add (line 3)", "edge_case", "src/cart.test.ts",
            "src/cart.test.ts › cart › totals", "test/cart.test.mjs > totals", "real bad"])
        self.assertEqual(sci.failure_count(lines), 5)
        self.assertEqual(sci.failure_count(["FAILED (failures=2, errors=1)"]), 3)
        self.assertEqual(sci.failure_count(["=== 2 failed, 9 passed, 1 error in 0.3s ==="]), 3)
        self.assertIsNone(sci.failure_count(["all good"]))

    def test_suites_that_stop_at_the_first_failure(self):
        for cmd, stops, variant in [
                ("python3 -m pytest -x -q tests", True, "python3 -m pytest -q tests"),
                ("python3 -m unittest discover -s tests -f", True, "python3 -m unittest discover -s tests"),
                ("pytest --maxfail=2 tests", True, "pytest tests"),
                ("cargo test -q", True, "cargo test -q --no-fail-fast"),
                ("cargo test -q -- --test-threads 1", True, "cargo test -q --no-fail-fast -- --test-threads 1"),
                ("cargo test --no-fail-fast", False, None), ("cargo test --lib", False, None),
                ("cargo nextest run", True, "cargo nextest run --no-fail-fast"),
                ("npx jest --bail", True, "npx jest"), ("npx vitest run", False, None),
                ("go test -failfast ./...", True, "go test ./..."), ("mvn test", True, "mvn test --fail-at-end"),
                ("./gradlew test --continue", False, None), ("npm test", False, None),
                ("make test && cargo test", False, None)]:
            self.assertEqual((sci.stops_early(cmd), sci.no_stop_variant(cmd)), (stops, variant), cmd)
        self.assertTrue(sci.crashed(139) and sci.crashed(-11) and not sci.crashed(1) and not sci.crashed(101))

    def test_rust_test_regions(self):
        src = ('use std::fmt; // a { brace in a comment\n/* nested /* comment { */ still } */\n'
               'pub fn add(a: i64, b: i64) -> i64 { let s = "{"; let c = \'{\'; let r = r#"raw { "#; a + b }\n'
               "pub fn lt<'a>(x: &'a str) -> &'a str { x }\n\n"
               '#[cfg(test)]\nmod tests {\n    use super::*;\n    #[test]\n    fn adds() { assert_eq!(add(2, 2), 4); }\n}\n\n'
               '#[cfg(not(test))]\nfn real() {}\n\n'
               '#[tokio::test(flavor = "multi_thread")]\nasync fn top() {\n    assert!(true);\n}\n\n'
               'proptest! {\n    #[test]\n    fn prop(x in 0..10i64) { prop_assert!(add(x, 0) == x); }\n}\npub fn after() {}\n')
        regions = sci.test_region_text("src/lib.rs", src).split("\n%s\n" % sci.REGION_SEP)
        self.assertEqual([r.splitlines()[0] for r in regions],
                         ["#[cfg(test)]", '#[tokio::test(flavor = "multi_thread")]', "proptest! {"])
        self.assertNotIn("real", "".join(regions))
        self.assertNotIn("after", "".join(regions))
        changed = src.replace("add(2, 2), 4", "add(2, 2), 5")
        self.assertEqual(sci.splice_regions("src/lib.rs", changed, sci.test_region_text("src/lib.rs", src)), src)
        self.assertEqual(sci.test_region_text("src/lib.py", src), "")

    def test_rust_comment_and_layout_edits_are_not_code_changes(self):
        a = 'pub fn area(w: f64, h: f64) -> f64 {\n    w * h // product\n}\n/// area(2.0, 3.0) == 6.0\nfn s() -> &str { "a  b" }\n'
        self.assertTrue(sci.same_syntax(a, a.replace("w * h // product", "w*h /* { */"), path="src/lib.rs"))
        self.assertTrue(sci.same_syntax(a, a.replace("(w: f64, h: f64)", "(\n    w: f64,\n    h: f64,\n)"), path="src/lib.rs"))
        self.assertFalse(sci.same_syntax(a, a.replace("== 6.0", "== 7.0"), path="src/lib.rs"))  # a doc test
        self.assertFalse(sci.same_syntax(a, a.replace('"a  b"', '"a b"'), path="src/lib.rs"))
        self.assertFalse(sci.same_syntax(a, a.replace("(w: f64, h: f64)", "(\n    w: f64,\n    h: f64,\n)"),
                                         lines=True, path="src/lib.rs"))

    def test_data_files_named_like_tests_are_not_tests(self):
        cfg = sci.DEFAULT_CONFIG
        for p, want in [("test_output.json", False), ("results/test_run.log", False), ("tests/data/x.json", True),
                        ("test_x.py", True), ("src/geo_test.go", True), ("src/__snapshots__/a.test.ts.snap", True),
                        ("src/tests.rs", True), ("src/lib.rs", False), ("web/cart.spec.ts", True)]:
            self.assertEqual(sci.is_test(p, cfg), want, p)

    def test_cargo_suites_rerun_only_failing_tests_and_cover_checks(self):
        self.assertEqual(sci.narrow_suite("cargo test --no-fail-fast -q", ["tests::broken", "integ_bad"], {}),
                         "cargo test --no-fail-fast -q -- --exact tests::broken integ_bad")
        self.assertIsNone(sci.narrow_suite("cargo test -q", ["src/lib.rs - add (line 3)"], {}))
        self.assertEqual(sci.runner_covers("cargo test --no-fail-fast -q", "cargo test -q parser", ["known"]), ["parser"])
        self.assertIsNone(sci.runner_covers("cargo test -q", "cargo test -q parser", ["known"]))

    def test_weakening_rules_for_rust_js_and_dropped_tolerances(self):
        def issues(path, rem, add):
            return sci.weakening_issues(path, [{"rem": rem, "add": add}])
        self.assertEqual(issues("tests/test_x.py", ["    assert_allclose(a, b, rtol=1e-12)"], ["    assert_allclose(a, b)"]),
                         ["tolerance removed: rtol (the default may be looser)"])
        self.assertEqual(issues("tests/test_x.py", ["    assertAlmostEqual(x, y, places=7)", "    assertAlmostEqual(u, v, places=2)"],
                                ["    assertAlmostEqual(x, y, places=7)", "    assertAlmostEqual(u, v, places=1)"]),
                         ["tolerance loosened: places 2 -> 1"])
        self.assertIn('skip/xfail added: #[ignore = "slow"]', issues("src/lib.rs", ["    #[test]"], ["    #[test]", '    #[ignore = "slow"]']))
        self.assertIn("tolerance loosened: max_relative 1e-12 -> 0.001",
                      issues("src/lib.rs", ["assert_relative_eq!(a, b, max_relative = 1e-12);"],
                             ["assert_relative_eq!(a, b, max_relative = 1e-3);"]))
        self.assertIn("tolerance loosened: tobecloseto 5 -> 2",
                      issues("a.test.ts", ["expect(x).toBeCloseTo(3.14159, 5);"], ["expect(x).toBeCloseTo(3.14159, 2);"]))
        self.assertTrue(issues("a.test.ts", ['  it("adds", () => {'], ['  it.skipIf(ci)("adds", () => {']))
        self.assertTrue(issues("src/lib.rs", ["    assert_eq!(x, 5);"], ["    if false {", "    assert_eq!(x, 5);", "    }"]))
        self.assertTrue(issues("src/lib.rs", ["    assert_eq!(x, 5);"], ["    assert!(true);"]))
        self.assertEqual(issues("a.test.ts", ["function close(a: number, tol: number) {"],
                                ["function close(a: number, eps = 1e-9) {"]), [])
        old = "import unittest\nclass TestRK2(unittest.TestCase):\n    def test_order(self):\n        assert 1 + 1 == 2\n"
        for extra, word in [("class TestRK2(unittest.TestCase):\n    pass\n", "defined twice"),
                            ("TestRK2.test_order = lambda self: None\n", "replaced by an assignment"),
                            ("del TestRK2\n", "deleted")]:
            self.assertIn(word, " ".join(sci.py_test_issues(old + extra) - sci.py_test_issues(old)))
        self.assertIn("early `return`", " ".join(sci.py_test_issues(
            old.replace("        assert", "        if True:\n            return\n        assert"))))
        self.assertIn("`if not True:` block", " ".join(sci.py_test_issues(
            old.replace("        assert", "        if not True:\n            assert"))))

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
        # an operator change at the same time, an inserted comment, or a looser tolerance elsewhere in the diff
        self.assertTrue(any("number changed" in i for i in issues("    assert err < 1e-6\n", "    assert err <= 1e-1\n")))
        self.assertTrue(any("number changed" in i for i in issues("    assert err < 1e-6\n",
                                                                  "    # tightened\n    assert err < 1e-2\n")))
        self.assertTrue(any("tolerance loosened: rtol 1e-12" in i for i in issues(
            "    assert isclose(a, b, rtol=1e-12)\n    assert isclose(c, d, rtol=1e-2)\n",
            "    assert isclose(a, b, rtol=1e-4)\n    assert isclose(c, d, rtol=1e-3)\n")))
        base = "def test_a():\n    assert f() == 1\n"
        swallowed = "def test_a():\n    try:\n        assert f() == 1\n    except AssertionError:\n        pass\n"
        self.assertTrue(any("catches assertion failures" in i for i in issues(base, swallowed)))
        dead = "def test_a():\n    if False:\n        assert f() == 1\n"
        self.assertTrue(any("never runs" in i for i in issues(base, dead)))
        must_fail = ("def test_a():\n    try:\n        assert f() == 1\n    except AssertionError:\n        pass\n"
                     "    else:\n        raise RuntimeError('should fail')\n")
        self.assertEqual(issues(base, must_fail), [])

    def test_parse_numbers(self):
        got = sci.parse_numbers(["version v1.2.3 info: energy -1.5e-3 nan inf 2.0D+01 x2 step10 0.5"])
        self.assertEqual(len(got), 5)
        self.assertEqual(got[0], -1.5e-3)
        self.assertTrue(got[1] != got[1])
        self.assertEqual(got[2], float("inf"))
        self.assertEqual(got[3], 20.0)
        self.assertEqual(got[4], 0.5)
        # a number glued to a unit or inside a version is skipped, not cut to its integer part
        self.assertEqual(sci.parse_numbers(["t=12.5s 3.14rad 1.2.3 E=1.5. x 2."]), [1.5, 2.0])

    def test_compare_numbers(self):
        self.assertTrue(sci.compare_numbers([1.0, float("nan")], [1.0 + 1e-13, float("nan")], 1e-12, 0)[0])
        self.assertFalse(sci.compare_numbers([1.0], [1.001], 1e-6, 0)[0])
        self.assertFalse(sci.compare_numbers([1.0], [1.0, 2.0], 1e-6, 0)[0])

    def test_order_command(self):
        rc = sci.main(["order", "--h", "0.1", "0.05", "0.025", "--err", "1e-2", "2.5e-3", "6.25e-4", "--expect", "2"])
        self.assertEqual(rc, 0)
        rc = sci.main(["order", "--h", "0.1", "0.05", "--err", "1e-2", "5e-3", "--expect", "2"])
        self.assertEqual(rc, 1)
        # finest first: the finest pair (order 0.32 here) decides, not the coarsest (order 2)
        rc = sci.main(["order", "--h", "0.025", "0.05", "0.1", "--err", "2e-3", "2.5e-3", "1e-2", "--expect", "2"])
        self.assertEqual(rc, 1)
        rc = sci.main(["order", "--h", "0.025", "0.05", "0.1", "--err", "6.25e-4", "2.5e-3", "1e-2", "--expect", "2"])
        self.assertEqual(rc, 0)

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


class TestReuseUnits(unittest.TestCase):
    def test_non_python_suites_cover_checks_that_only_narrow_them(self):
        cov = sci.runner_covers
        self.assertEqual(cov("cargo test", "cargo test energy", []), ["energy"])
        self.assertEqual(cov("cargo test --release", "cargo test --release energy -- --nocapture", []), ["energy"])
        self.assertIsNone(cov("cargo test", "cargo test -- --ignored", []))
        self.assertIsNone(cov("cargo test energy", "cargo test energy_x", []))
        self.assertIsNone(cov("cargo test", "cargo test --release energy", []))
        self.assertIsNone(cov("cargo test", "cargo test energy", ["mod::t"]))
        self.assertEqual(cov("go test ./...", "go test ./internal/heat -run TestHeat", []),
                         ["TestHeat", "./internal/heat"])
        self.assertIsNone(cov("go test -short ./...", "go test ./internal/heat", []))
        self.assertIsNone(cov("go test ./cmd/...", "go test ./internal/heat", []))
        self.assertEqual(cov("ctest --test-dir build", "ctest --test-dir build -R heat --output-on-failure", []),
                         ["heat"])
        self.assertIsNone(cov("ctest --test-dir build -E slow", "ctest --test-dir build -R heat", []))
        self.assertEqual(cov("npx jest --ci", "npx jest --ci src/cart.test.ts", []), ["src/cart.test.ts"])
        self.assertIsNone(cov("npx jest --onlyChanged", "npx jest src/cart.test.ts", []))
        self.assertEqual(cov("npx vitest run", "npx vitest run cart -t empty", []), ["cart", "empty"])
        self.assertEqual(cov("dotnet test", "dotnet test --filter FullyQualifiedName~Cart", []),
                         ["FullyQualifiedName~Cart"])
        self.assertEqual(cov("./gradlew test", "./gradlew test --tests '*CartTest*'", []), ["*CartTest*"])
        self.assertIsNone(cov("mvn test", "mvn test -Dtest=CartIT", []))
        self.assertIsNone(cov("cargo test", "cargo test --test energy", []))
        self.assertIsNone(cov("go test ./...", "go test ./vendor/x", []))
        self.assertIsNone(cov("go test ./...", "go test ./tools/gen", [], {"tools/go.mod": 1}))
        self.assertEqual(cov("go test ./...", "go test ./internal/...", []), ["./internal/..."])
        self.assertIsNone(cov("make test", "make test T=cart", []))
        self.assertIsNone(cov("cargo test && cargo clippy", "cargo test energy", []))
        self.assertEqual(sci.blame_tasks([("T1", [], ["energy"]), ("T2", [], ["*CartTest*"])],
                                         ["physics::energy_conserved", "com.x.CartTest.testEmpty"]),
                         [("T1", ["physics::energy_conserved"]), ("T2", ["com.x.CartTest.testEmpty"])])

    def test_source_text_tools_are_recognized(self):
        for c in ("ruff check .", "pytest --flake8", "pytest --cov=pkg", "make lint", "grep -q x a.py", "tox -e mypy"):
            self.assertTrue(sci.SOURCE_TEXT_RE.search(c), c)
        for c in ("python -m unittest discover -s tests", "pytest -q tests", "cargo test energy"):
            self.assertIsNone(sci.SOURCE_TEXT_RE.search(c), c)

    def test_narrow_suite_reruns_only_the_failing_tests(self):
        files = {"tests/__init__.py": 1, "tests/test_a.py": 1}
        self.assertEqual(sci.narrow_suite("python -m pytest -q tests", ["tests/test_a.py::test_x[1-2]"], files),
                         "python -m pytest -q 'tests/test_a.py::test_x[1-2]'")
        self.assertEqual(sci.narrow_suite("python -m unittest discover -s . -q", ["tests.test_a.T.test_x"], files),
                         "python -m unittest tests.test_a.T.test_x")
        self.assertIsNone(sci.narrow_suite("python -m unittest discover -s tests", ["test_a.T.test_x"], files))
        self.assertEqual(sci.narrow_suite("python -m unittest", ["tests.test_a.T.setUpClass", "tests.test_a.setUpModule",
                                                                 "tests.test_a.T.test_y"], files),
                         "python -m unittest tests.test_a.T tests.test_a tests.test_a.T.test_y")
        self.assertIsNone(sci.narrow_suite("make test", ["tests/test_a.py::test_x"], files))
        self.assertIsNone(sci.narrow_suite("python -m pytest -q", [], files))

    def test_parse_test_cmd(self):
        p = sci.parse_test_cmd
        s = p("python -m pytest -q tests/test_a.py::TestA::test_x")
        self.assertEqual((s["kind"], s["targets"], s["narrow"], s["stop"], s["unknown"]),
                         ("pytest", ["tests/test_a.py"], False, False, False))
        self.assertTrue(p("pytest -k slow")["narrow"])
        self.assertTrue(p("pytest -x tests")["stop"])
        self.assertTrue(p("pytest --weird-flag")["unknown"])
        self.assertEqual(p("python3 -m unittest discover -s tests -p 'test_*.py' -q")["discover"],
                         {"start": "tests", "pattern": "test_*.py"})
        self.assertEqual(p("python3 -m unittest -q tests.test_a")["targets"], ["tests.test_a"])
        self.assertEqual(p("python3 -m unittest")["discover"], {"start": ".", "pattern": "test*.py"})
        for cmd in ("cd sub && pytest", "FOO=1 pytest", "make test", "python run_tests.py", "pytest | tee log"):
            self.assertIsNone(p(cmd), cmd)

    def test_suite_covers_only_what_it_surely_runs(self):
        files = dict.fromkeys(["tests/__init__.py", "tests/test_a.py", "tests/test-b.py", "tests/sub/test_c.py",
                               "tests/pkg/__init__.py", "tests/pkg/test_d.py"], ("100644", "0", 0))

        def cov(suite, cmd, baseline=(), ini=None):
            return sci.suite_covers(sci.parse_test_cmd(suite), cmd, files, ini or {}, list(baseline))

        ut = "python -m unittest discover -s tests -q"
        self.assertEqual(cov(ut, "python -m unittest tests.test_a -q"), ["tests/test_a.py"])
        self.assertEqual(cov(ut, "python -m unittest tests.pkg.test_d.TestD.test_x"), ["tests/pkg/test_d.py"])
        self.assertIsNone(cov(ut, "python -m unittest tests/test-b.py"))  # discovery skips non-module names
        self.assertIsNone(cov(ut, "python -m unittest tests.sub.test_c"))  # no __init__.py: never discovered
        self.assertIsNone(cov(ut, "python3 -m unittest tests.test_a"))  # another interpreter
        self.assertIsNone(cov(ut + " -f", "python -m unittest tests.test_a"))  # stops at the first failure
        self.assertIsNone(cov(ut + " -k fast", "python -m unittest tests.test_a"))
        self.assertIsNone(cov(ut, "python -m unittest tests.test_a", ["test_a.TestA.test_old"]))  # known failure
        self.assertIsNone(cov(ut, "python -m pytest tests/test_a.py"))
        pt = "python -m pytest -q"
        self.assertEqual(cov(pt, "python -m pytest tests/test_a.py -q"), ["tests/test_a.py"])
        self.assertIsNone(cov(pt, "python -m pytest tests/test-b.py"))  # run by name, not collected by the suite
        self.assertIsNone(cov(pt, "python -m pytest tests/test_a.py", ini={"addopts": ["-m", "not", "slow"]}))
        self.assertIsNone(cov("python -m pytest tests/sub", "python -m pytest tests/test_a.py"))
        self.assertEqual(sci.blame_tasks([("T1", ["tests/test_a.py"], [])], ["test_a.TestA.test_x", "test_z.T.t"]),
                         [("T1", ["test_a.TestA.test_x"])])

    def test_py_strings_skip_docstrings_and_comments(self):
        s = sci.py_strings('"""Module doc: PLAN.md"""\n# comment: NOTES.md\nX = "data.md"\n\n\ndef f():\n'
                           '    """Function doc: SPEC.md"""\n    return f"{X}.rst"\n')
        self.assertIn("data.md", s)
        self.assertIn(".rst", s)
        for name in ("PLAN.md", "NOTES.md", "SPEC.md"):
            self.assertNotIn(name, s)

    def test_cacheable(self):
        for cmd in ("python -m pytest -q", "../venv/bin/python -m pytest tests", "pytest -p no:cacheprovider"):
            self.assertTrue(sci.cacheable(cmd), cmd)
        for cmd in ("test -s ../data/run.h5", "cat /etc/hostname", "pytest --basetemp=/tmp/x", "echo $HOME",
                    "python ~/probe.py"):
            self.assertFalse(sci.cacheable(cmd), cmd)

    def test_pass_records_need_the_same_code_and_packages(self):
        st = {}
        sci.note_result(st, "cmd:x", "fp1", True, 1.0, "envA")
        self.assertTrue(sci.cached_pass(st, "cmd:x", "fp1", "envA"))
        self.assertIsNone(sci.cached_pass(st, "cmd:x", "fp1", "envB"))
        self.assertIsNone(sci.cached_pass(st, "cmd:x", "fp2", "envA"))
        sci.note_result(st, "cmd:x", "fp1", False, 1.0, "envA")
        self.assertIsNone(sci.cached_pass(st, "cmd:x", "fp1", "envA"))
        self.assertEqual(st["durations"]["cmd:x"], 1.0)

    def test_env_stamp_follows_the_virtualenv_packages(self):
        tmp = Path(tempfile.mkdtemp(prefix="sci-env-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        (tmp / "venv" / "bin").mkdir(parents=True)
        (tmp / "venv" / "bin" / "python").write_text("")
        (tmp / "venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
        site = tmp / "venv" / "lib" / "python3.12" / "site-packages"
        site.mkdir(parents=True)
        os.utime(str(site), ns=(10**18, 10**18))

        def stamp(cmd):
            sci._ENV_MEMO.clear()
            return sci.env_stamp(tmp, cmd)

        a = stamp("venv/bin/python -m pytest -q")
        self.assertTrue(a)
        self.assertEqual(stamp("PYTHONPATH=. venv/bin/python -m pytest"), a)
        os.utime(str(site), ns=(2 * 10**18, 2 * 10**18))  # pip install, upgrade or uninstall
        self.assertNotEqual(stamp("venv/bin/python -m pytest -q"), a)
        self.assertEqual(stamp("make test"), "")

    def test_inert_docs(self):
        sb = Sandbox(self)
        for rel in ("README.md", "NOTES.md", "CHANGELOG.md", "docs/guide.md", "pkg/prompts/system.md"):
            sb.write(rel, "text\n")
        sb.write("tests/test_docs.py", '"""Mentions README.md and CHANGELOG.md in a docstring only."""\n'
                 '# guide.md in a comment\nNOTES = "NOTES.md"\n')
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "docs")
        sb.init()
        store = sci.Store(sci.Repo(sb.root))
        store.load()

        def inert():
            tree, _ = sci.snapshot(store.repo, store.cfg)
            return sci.inert_docs(store, tree, sci.tree_entries(store.repo, tree))

        self.assertEqual(inert(), {"README.md", "CHANGELOG.md", "docs/guide.md"})  # NOTES.md is read; prompts too
        store.cfg["build_cmd"] = "./tools/check_links docs"
        self.assertEqual(inert(), {"README.md", "CHANGELOG.md"})
        store.cfg["build_cmd"] = "markdownlint README.md"
        self.assertEqual(inert(), set())
        store.cfg["build_cmd"] = ""
        sb.write("Makefile", "lint:\n\tcheck-links *.md\n")
        self.assertEqual(inert(), set())


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
        sb.ok("config", "set", "build_cmd", "echo rebuilt-with-new-flags >> build.log")
        sb.ok("check")  # same sources, different build: it runs
        self.assertIn("rebuilt-with-new-flags", sb.read("build.log"))


FAKE_SUITE = '''\
import os, sys
mode = open("mode.txt").read().strip()
print(" FAIL  src/cart.test.ts")
if mode == "base":
    print("Tests:       1 failed, 50 passed, 51 total")
elif mode == "more":
    print("Tests:       4 failed, 47 passed, 51 total")
else:
    sys.stdout.flush()
    os._exit(139)  # what bash reports for a test process killed by SIGSEGV
sys.exit(1)
'''


class TestVerdicts(unittest.TestCase):
    """A suite run whose named failures are all known baseline failures can still hide new ones."""

    def _known_failure(self, sb):
        sb.write("suite.py", FAKE_SUITE)
        sb.write("mode.txt", "base\n")
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "suite")
        sb.init()
        self.assertIn("1 pre-existing failures", sb.ok("baseline", "--cmd", "%s suite.py" % PYQ))
        sb.ok("task", "add", "t", "--verify", "true", "--scope", "mode.txt")
        sb.ok("task", "start")
        sb.ok("check")
        sb.ok("lock", "--none", "the suite alone decides in this test")

    def test_more_failures_behind_a_file_level_id_fail_verify(self):
        sb = Sandbox(self)
        self._known_failure(sb)
        sb.write("mode.txt", "more\n")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("NOT ONLY KNOWN FAILURES", out)
        self.assertIn("4 tests fail now, 1 in the baseline", out)

    def test_a_crashed_suite_fails_verify(self):
        sb = Sandbox(self)
        self._known_failure(sb)
        sb.write("mode.txt", "crash\n")
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("was killed (exit 139)", out)

    def test_baseline_of_a_fail_fast_suite_runs_every_test(self):
        sb = Sandbox(self)
        sb.write("tests/test_a_legacy.py", "import unittest\n\nclass TestLegacy(unittest.TestCase):\n"
                                            "    def test_old(self):\n        self.assertEqual(1, 2)\n")
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "legacy failing test")
        sb.init()
        out = sb.ok("baseline", "--cmd", "%s discover -s tests -t . -f" % UT)
        self.assertIn("instead of", out)
        self.assertNotIn(" -f", sb.state()["baseline"]["cmd"])
        sb.ok("task", "add", "noop", "--verify", "true", "--scope", "pkg/core.py")
        sb.ok("task", "start")
        sb.write("pkg/core.py", sb.read("pkg/core.py").replace("return x * k", "return x * k + 1.0"))
        rc, out = sb.sci("verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("NEW FAILURES", out)


class TestEnablers(unittest.TestCase):
    """Things that used to stop an agent that did nothing wrong."""

    def test_unlock_before_editing_a_protected_test(self):
        sb = Sandbox(self)
        sb.finish_rk2()
        sb.ok("task", "add", "speed", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        out = sb.ok("unlock", "tests/test_rk2.py", "--reason", "order test must follow the new integrator API")
        self.assertIn("checks accept its changes until `done`", out)
        self.assertIn("unlocked for this task", sb.ok("resume"))
        sb.write("tests/test_rk2.py", sb.read("tests/test_rk2.py").replace("1.9", "1.8"))
        sb.ok("check")
        sb.ok("lock")
        sb.ok("verify")
        sb.ok("done", "-m", "perf: speed")
        self.assertEqual(sb.state()["protected"]["tests/test_rk2.py"]["task"], "T2")
        sb.ok("task", "add", "more", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        sb.write("tests/test_rk2.py", sb.read("tests/test_rk2.py").replace("1.8", "1.5"))
        rc, out = sb.sci("check")
        self.assertIn("PROTECTED TEST CHANGED (locked by T2)", out)

    def test_files_a_check_writes_are_ignored_and_never_block_done(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "t", "--verify", "%s -c \"import time; open('test_output.json', 'w').write(repr(time.time()))\""
              " && %s tests.test_core -q" % (PYQ, UT), "--scope", "pkg/core.py")
        sb.ok("task", "start")
        out = sb.ok("check")
        self.assertIn("sci ignores it from now on", out)
        self.assertIn("test_output.json", json.loads(sb.read(".sci/config.json"))["ignore_globs"])
        out = sb.ok("lock")
        self.assertNotIn("test_output.json", out)
        sb.write("pkg/core.py", sb.read("pkg/core.py") + "\n\ndef double(x):\n    return 2 * x\n")
        sb.ok("check")
        sb.ok("verify")
        sb.ok("done", "-m", "feat: double")
        self.assertNotIn("test_output.json", sb.git("ls-files"))

    def test_escalate_after_six_failed_fixes_and_a_fresh_start_resets_strikes(self):
        sb = Sandbox(self)
        sb.init()
        sb.add_rk2_task()
        sb.ok("task", "start")
        sb.write_rk2_test()
        sb.sci("check")
        sb.ok("lock")
        for i in range(6):
            sb.write_rk2(good=False)
            sb.write("pkg/core.py", sb.read("pkg/core.py") + "\n# attempt %d\n" % i)
            rc, out = sb.sci("check")
            self.assertEqual(rc, 1, out)
        self.assertIn("NEXT: ESCALATE", sb.ok("resume"))
        out = sb.ok("restore", "--to", "start")
        self.assertIn("Strikes reset", out)
        t = sb.state()["tasks"][0]
        self.assertEqual((t["strikes"], t["stop"], t["resets"]), (0, None, 1))

    def test_run_options_after_a_quoted_command_are_sci_options(self):
        sb = Sandbox(self)
        sb.init()
        out = sb.ok("run", "echo hello", "--tail", "5")
        self.assertIn("| hello", out)
        self.assertNotIn("--tail", out.split("Cite it")[0])

    def test_locking_files_after_lock_none_needs_them_locked(self):
        sb = Sandbox(self)
        sb.init()
        sb.ok("task", "add", "t", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg/core.py")
        sb.ok("task", "start")
        sb.ok("lock", "--none", "a refactor guarded by existing tests")
        sb.ok("lock", "tests/test_core.py")
        self.assertIsNone(sb.state()["tasks"][0]["lock_none"])

    def test_a_rejected_commit_leaves_the_index_as_it_was(self):
        sb = Sandbox(self)
        hook = sb.write(".git/hooks/pre-commit", "#!/bin/sh\necho 'lint says no'\nexit 1\n")
        os.chmod(str(hook), 0o755)
        sb.init()
        sb.ok("task", "add", "t", "--verify", "%s tests.test_core -q" % UT, "--scope", "pkg")
        sb.ok("task", "start")
        sb.ok("lock", "--none", "a new module guarded by existing tests")
        sb.write("pkg/extra.py", "X = 1\n")
        sb.ok("verify")
        rc, out = sb.sci("done", "-m", "feat: extra")
        self.assertEqual(rc, 2, out)
        self.assertIn("lint says no", out)
        self.assertEqual(sb.git("diff", "--cached", "--name-only").strip(), "")

    def test_golden_warns_about_nan(self):
        sb = Sandbox(self)
        sb.init()
        self.assertIn("NaN or infinite", sb.ok("golden", "add", "e", "--cmd", "echo 'energy nan residual 1e-9'"))

    def test_the_last_done_is_the_final_verify(self):
        sb = Sandbox(self)
        out = sb.finish_rk2()
        self.assertIn("All tasks done and the final verify passed", out)


RUST_LIB = '''\
pub fn area(w: f64, h: f64) -> f64 {
    0.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn area_of_rectangle() {
        assert!((area(2.0, 3.0) - 6.0).abs() < 1e-12);
    }
}
'''


@unittest.skipUnless(shutil.which("cargo"), "cargo not installed")
class TestRust(unittest.TestCase):
    def test_inline_tests_are_locked_protected_and_restored_without_the_code(self):
        sb = Sandbox(self)
        sb.git("rm", "-r", "-q", "pkg", "tests", "demo.py")
        sb.write("Cargo.toml", '[package]\nname = "geo"\nversion = "0.1.0"\nedition = "2021"\n')
        sb.write(".gitignore", "/target\n")
        sb.write("src/lib.rs", RUST_LIB.split("#[cfg(test)]")[0])
        sb.git("add", "-A")
        sb.git("commit", "-q", "-m", "crate")
        sb.ok("init", "--objective", "geometry")
        self.assertIn("--no-fail-fast", sb.ok("config"))
        sb.ok("task", "add", "area", "--verify", "cargo test -q --lib tests::area", "--scope", "src/lib.rs")
        sb.ok("task", "start")
        sb.write("src/lib.rs", RUST_LIB)
        rc, out = sb.sci("check", timeout=600)
        self.assertEqual(rc, 1, out)
        self.assertIn("tests::area_of_rectangle", out)
        out = sb.ok("lock")
        self.assertIn("src/lib.rs (its tests only)", out)
        sb.write("src/lib.rs", RUST_LIB.replace("    0.0\n", "    w * h\n"))
        sb.ok("check", timeout=600)
        sb.write("src/lib.rs", sb.read("src/lib.rs").replace("< 1e-12", "< 10.0"))
        rc, out = sb.sci("check", timeout=600)
        self.assertEqual(rc, 1, out)
        self.assertIn("LOCKED TESTS CHANGED: src/lib.rs", out)
        out = sb.ok("restore", "--paths", "src/lib.rs")
        self.assertIn("Restored the locked tests in src/lib.rs", out)
        self.assertIn("w * h", sb.read("src/lib.rs"))
        self.assertIn("< 1e-12", sb.read("src/lib.rs"))
        sb.ok("verify", timeout=600)
        sb.write("src/lib.rs", sb.read("src/lib.rs").replace("    w * h\n", "    // the product of the sides\n    w * h\n"))
        out = sb.ok("done", "-m", "feat: area")  # a comment after verify needs no new verify
        self.assertIn("src/lib.rs (tests)", out)
        self.assertIn("Only documentation, comments or layout changed", out)
        sb.ok("task", "add", "perimeter", "--verify", "cargo test -q --lib", "--scope", "src/lib.rs")
        sb.ok("task", "start")
        sb.write("src/lib.rs", sb.read("src/lib.rs").replace("< 1e-12", "< 1e-3"))
        rc, out = sb.sci("check", timeout=600)
        self.assertIn("PROTECTED TESTS CHANGED (locked by T1): src/lib.rs", out)
        sb.ok("unlock", "src/lib.rs", "--reason", "tolerance must follow the float32 port")
        sb.ok("check", timeout=600)


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
               "git restore --staged --worktree a.py", "git checkout HEAD file.py", "git checkout HEAD~1 src/",
               'bash -c "git stash"', "sh -c 'cd x; git clean -fdx'", ".sci/sci purge --yes",
               ".sci/sci review --clear --yes", 'rm -rf ".sci"', "/usr/bin/git reset --hard", "\\git reset --hard",
               "git restore -SW a.py", 'powershell -Command "git stash"', 'cmd /c "git clean -fd"',
               "rm -rf .sci/scratch/../state.json"]
        good = ["git status", "git checkout -b topic", "git checkout main", "git restore --staged a.py",
                "git stash list", "git diff", "rm -rf build/.scikit", "git log --oneline", "git push origin topic",
                "pytest -q", "git commit -m 'fix: x'", '.sci/sci note "never run git stash or git restore" --kind gotcha',
                'git commit -m "avoid git clean -fd; it deletes data"', "git checkout main 2>/dev/null",
                ".sci/sci review", '.sci/sci done -m "fix: git reset --hard handling"',
                "rm .sci/scratch/probe.py", "rm -rf .sci/scratch/old", "git restore -S a.py",
                '.sci/sci note "git reset --hard wiped our build" --kind gotcha', 'git commit -m "rm -rf .sci cleanup"']
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

        def stop():
            return json.loads(self.hook("agy-stop", dict(ws, terminationReason="NO_TOOL_CALL"))).get("decision")
        self.assertEqual([stop(), stop(), stop()], ["continue", "continue", None])
        sb.ok("resume")
        sb.ok("note", "reading and notes are not progress")
        self.assertIsNone(stop())
        sb.sci("check")
        self.assertEqual([stop(), stop(), stop()], ["continue", "continue", None])
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
