#!/usr/bin/env python3
"""Tests for the online installers install.sh and install.ps1. They install from a local copy of this
repository (SCI_SOURCE) into a throwaway home, so no network is needed. The PowerShell tests run wherever
powershell or pwsh is installed (Windows, and GitHub's Linux and macOS runners)."""
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NAME = "software-engineer"
WINDOWS = os.name == "nt"
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".sci"}
POWERSHELLS = [p for p in dict.fromkeys(shutil.which(x) for x in ("powershell", "pwsh")) if p]


def posix_shell():
    """sh on POSIX; Git Bash on Windows."""
    if not WINDOWS:
        return shutil.which("sh")
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import sci
    return sci.windows_bash()


def repo_files():
    for dirpath, dirnames, filenames in os.walk(str(ROOT)):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if not f.endswith(".pyc"):
                full = Path(dirpath) / f
                yield full, full.relative_to(ROOT).as_posix()


class Bootstrap(object):
    """A throwaway home plus archives of the repository laid out like GitHub's (sci-harness-main/...)."""

    def __init__(self, testcase):
        self.root = Path(tempfile.mkdtemp(prefix="sci-bootstrap-"))
        testcase.addCleanup(shutil.rmtree, str(self.root), True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.tc = testcase

    def tarball(self):
        path = self.root / "source.tar.gz"
        with tarfile.open(str(path), "w:gz") as tf:
            for full, rel in repo_files():
                tf.add(str(full), arcname="sci-harness-main/" + rel)
        return path

    def zipball(self):
        path = self.root / "source.zip"
        with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
            for full, rel in repo_files():
                zf.write(str(full), "sci-harness-main/" + rel)
        return path

    def env(self, **extra):
        env = dict(os.environ)
        for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME", "SCI_ARGS", "SCI_REF", "SCI_REPO",
                  "SCI_SOURCE", "GH_TOKEN", "GITHUB_TOKEN"):
            env.pop(k, None)
        env.update({"HOME": str(self.home), "USERPROFILE": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"})
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def run(self, argv, **env):
        p = subprocess.run(argv, cwd=str(self.root), env=self.env(**env), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                           timeout=300)
        return p.returncode, p.stdout

    def assert_installed(self, out):
        installed = self.home / ".local" / "share" / "agent-skills" / NAME
        self.tc.assertTrue((installed / "SKILL.md").is_file(), out)
        self.tc.assertTrue((installed / "scripts" / "sci.py").is_file(), out)
        self.tc.assertFalse((installed / "tests").exists(), out)
        self.tc.assertTrue((self.home / ".claude" / "skills" / NAME / "SKILL.md").is_file(), out)
        self.tc.assertIn("Installing %s" % NAME, out)


SH = posix_shell()


@unittest.skipUnless(SH, "needs sh (Git Bash on Windows)")
class TestInstallSh(unittest.TestCase):
    def sh(self, b, *args, **env):
        return b.run([SH, (ROOT / "install.sh").as_posix()] + list(args), **env)

    def test_installs_from_an_archive(self):
        b = Bootstrap(self)
        rc, out = self.sh(b, "--only", "claude", SCI_SOURCE=b.tarball().as_posix())
        self.assertEqual(rc, 0, out)
        b.assert_installed(out)

    def test_installs_from_a_folder_and_passes_arguments(self):
        b = Bootstrap(self)
        rc, out = self.sh(b, "--only", "claude", "--dry-run", SCI_SOURCE=ROOT.as_posix())
        self.assertEqual(rc, 0, out)
        self.assertIn("[dry-run]", out)
        self.assertEqual(list(b.home.iterdir()), [])
        rc, out = self.sh(b, "--only", "claude", SCI_SOURCE=ROOT.as_posix())
        self.assertEqual(rc, 0, out)
        b.assert_installed(out)

    def test_failing_install_py_fails_the_script(self):
        b = Bootstrap(self)
        rc, out = self.sh(b, "--only", "no-such-agent", SCI_SOURCE=ROOT.as_posix())
        self.assertNotEqual(rc, 0, out)
        self.assertIn("Unknown agent", out)

    @unittest.skipIf(WINDOWS, "POSIX PATH handling")
    def test_explains_missing_python(self):
        b = Bootstrap(self)
        rc, out = b.run([SH, str(ROOT / "install.sh")], PATH=str(b.root / "empty"))
        self.assertEqual(rc, 1, out)
        self.assertIn("Python 3.8+ is required", out)

    def test_whole_script_is_wrapped_in_main(self):
        # A truncated download must not run anything: the only top-level command is the final `main "$@"`.
        lines = (ROOT / "install.sh").read_text(encoding="utf-8").rstrip("\n").splitlines()
        self.assertEqual(lines[-1], 'main "$@"')
        self.assertNotIn(b"\r", (ROOT / "install.sh").read_bytes())


@unittest.skipUnless(POWERSHELLS, "needs powershell or pwsh")
class TestInstallPs1(unittest.TestCase):
    def ps(self, exe, b, script, **env):
        return b.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                     **env)

    def test_file_mode_with_arguments(self):
        for exe in POWERSHELLS:
            b = Bootstrap(self)
            rc, out = b.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                             str(ROOT / "install.ps1"), "--only", "claude"], SCI_SOURCE=b.zipball())
            self.assertEqual(rc, 0, (exe, out))
            b.assert_installed(out)

    def test_piped_to_iex_with_sci_args(self):
        for exe in POWERSHELLS:
            b = Bootstrap(self)
            script = "Get-Content -Raw -LiteralPath '%s' | Invoke-Expression" % (ROOT / "install.ps1")
            rc, out = self.ps(exe, b, script, SCI_SOURCE=ROOT, SCI_ARGS="--only claude")
            self.assertEqual(rc, 0, (exe, out))
            b.assert_installed(out)

    def test_script_block_with_arguments_and_failure_exit_code(self):
        for exe in POWERSHELLS:
            b = Bootstrap(self)
            block = "& ([scriptblock]::Create((Get-Content -Raw -LiteralPath '%s')))" % (ROOT / "install.ps1")
            rc, out = self.ps(exe, b, block + " --only claude --dry-run", SCI_SOURCE=ROOT)
            self.assertEqual(rc, 0, (exe, out))
            self.assertIn("[dry-run]", out)
            rc, out = self.ps(exe, b, block + " --only no-such-agent", SCI_SOURCE=ROOT)
            self.assertNotEqual(rc, 0, (exe, out))
            self.assertIn("install.py failed", out)

    def test_leaves_nothing_in_the_session(self):
        for exe in POWERSHELLS:
            b = Bootstrap(self)
            script = ("$before = @(Get-Variable | ForEach-Object Name); "
                      "Get-Content -Raw -LiteralPath '%s' | Invoke-Expression; "
                      "$new = @(Get-Variable | ForEach-Object Name | Where-Object { $before -notcontains $_ "
                      "-and $_ -ne 'before' }); "
                      "if (Get-Command Find-Python -ErrorAction SilentlyContinue) { 'LEAK Find-Python' }; "
                      "'NEW:' + ($new -join ',')") % (ROOT / "install.ps1")
            rc, out = self.ps(exe, b, script, SCI_SOURCE=ROOT, SCI_ARGS="--only claude --dry-run")
            self.assertEqual(rc, 0, (exe, out))
            self.assertNotIn("LEAK", out)
            leaked = out.strip().splitlines()[-1]
            for name in ("python", "repo", "ref", "token", "tmp", "dir", "InstallArgs"):
                self.assertNotIn(name, leaked.split(":", 1)[1].split(","), (exe, leaked))


if __name__ == "__main__":
    unittest.main()
