#!/usr/bin/env python3
"""Tests for install.py. Each test uses a throwaway HOME and a PATH with stub agent executables."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALL = HERE.parent / "install.py"
PY = sys.executable
NAME = "software-engineer"


class Home(object):
    def __init__(self, testcase, agents=("claude", "codex", "gemini", "agy")):
        self.root = Path(tempfile.mkdtemp(prefix="sci-install-"))
        testcase.addCleanup(shutil.rmtree, str(self.root), True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for a in agents:
            stub = self.bin / a
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        self.home = self.root / "home"
        self.home.mkdir()

    def run(self, *args):
        env = {"HOME": str(self.home), "PATH": "%s:/usr/bin:/bin" % self.bin, "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run([PY, str(INSTALL)] + list(args), env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        if p.returncode != 0:
            raise AssertionError("install.py %s exited %d:\n%s" % (" ".join(args), p.returncode, p.stdout))
        return p.stdout

    def p(self, rel):
        return self.home / rel

    def json(self, rel):
        return json.loads(self.p(rel).read_text())

    def write_json(self, rel, data):
        self.p(rel).parent.mkdir(parents=True, exist_ok=True)
        self.p(rel).write_text(json.dumps(data, indent=2))


def our_commands(events):
    cmds = []
    for groups in events.values():
        for g in groups:
            for h in g.get("hooks", [g]):
                if "sci_hook.py" in h.get("command", ""):
                    cmds.append(h["command"])
    return cmds


class TestInstall(unittest.TestCase):
    def test_install_links_every_detected_agent_without_config_edits(self):
        h = Home(self)
        out = h.run()
        installed = h.p(".local/share/agent-skills/" + NAME)
        self.assertTrue((installed / "SKILL.md").is_file())
        self.assertTrue((installed / "scripts" / "sci.py").is_file())
        self.assertTrue((installed / ".sci-install.json").is_file())
        for rel in (".claude/skills/" + NAME, ".agents/skills/" + NAME):
            self.assertTrue(os.path.islink(str(h.p(rel))), rel)
            self.assertEqual(os.path.realpath(str(h.p(rel))), os.path.realpath(str(installed)))
        plugin = h.p(".gemini/config/plugins/" + NAME)
        self.assertEqual(json.loads((plugin / "plugin.json").read_text())["version"], "3.0.0")
        self.assertTrue((plugin / "skills" / NAME / "SKILL.md").is_file())
        self.assertFalse((plugin / "hooks.json").exists())
        self.assertFalse(h.p(".claude/settings.json").exists())
        self.assertFalse(h.p(".gemini/settings.json").exists())
        self.assertIn("--hooks", out)
        self.assertIn("already linked", h.run())  # idempotent

    def test_only_and_dry_run(self):
        h = Home(self)
        out = h.run("--dry-run")
        self.assertIn("[dry-run]", out)
        self.assertEqual(list(h.home.iterdir()), [])
        h.run("--only", "claude")
        self.assertTrue(h.p(".claude/skills/" + NAME).exists())
        self.assertFalse(h.p(".agents/skills/" + NAME).exists())
        self.assertFalse(h.p(".gemini/config/plugins/" + NAME).exists())

    def test_hooks_merge_with_user_hooks_and_uninstall_removes_only_ours(self):
        h = Home(self)
        user_hook = {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo mine"}]}
        h.write_json(".claude/settings.json", {"model": "haiku", "hooks": {"PreToolUse": [user_hook]}})
        theirs = {"hooks": [{"command": "bash notify.sh session", "timeout": 10, "type": "command"}]}
        h.write_json(".codex/hooks.json", {"hooks": {"SessionStart": [theirs]}})
        h.p(".codex/config.toml").write_text("[features]\nhooks = true\n")
        h.run("--hooks")
        claude = h.json(".claude/settings.json")
        self.assertEqual(claude["model"], "haiku")
        self.assertIn(user_hook, claude["hooks"]["PreToolUse"])
        self.assertEqual(len(our_commands(claude["hooks"])), 3)
        self.assertEqual([g.get("matcher") for g in claude["hooks"]["PreToolUse"]], ["Edit", "Bash"])
        self.assertTrue(list(h.p(".claude").glob("settings.json.bak-sci-*")))
        codex = h.json(".codex/hooks.json")
        self.assertIn(theirs, codex["hooks"]["SessionStart"])
        self.assertEqual(len(our_commands(codex["hooks"])), 3)
        gemini = h.json(".gemini/settings.json")
        self.assertEqual(sorted(gemini["hooks"]), ["BeforeAgent", "BeforeTool", "SessionStart"])
        agy = h.json(".gemini/config/plugins/%s/hooks.json" % NAME)
        self.assertEqual(sorted(agy["sci-harness"]), ["PreInvocation", "PreToolUse", "SessionStart", "Stop"])
        h.run("--hooks")  # idempotent: no duplicates
        self.assertEqual(len(our_commands(h.json(".claude/settings.json")["hooks"])), 3)
        h.run()  # plain re-install keeps the hooks
        self.assertTrue(h.p(".gemini/config/plugins/%s/hooks.json" % NAME).exists())
        self.assertEqual(len(our_commands(h.json(".gemini/settings.json")["hooks"])), 3)
        self.assertIn("on (3 handlers)", h.run("--status"))
        h.run("--uninstall")
        claude = h.json(".claude/settings.json")
        self.assertEqual(claude, {"model": "haiku", "hooks": {"PreToolUse": [user_hook]}})
        self.assertEqual(h.json(".codex/hooks.json"), {"hooks": {"SessionStart": [theirs]}})
        self.assertNotIn("hooks", h.json(".gemini/settings.json"))
        for rel in (".claude/skills/" + NAME, ".agents/skills/" + NAME, ".gemini/config/plugins/" + NAME,
                    ".local/share/agent-skills/" + NAME):
            self.assertFalse(os.path.lexists(str(h.p(rel))), rel)

    def test_remove_hooks_keeps_skill(self):
        h = Home(self)
        h.run("--hooks")
        h.run("--remove-hooks")
        self.assertNotIn("hooks", h.json(".claude/settings.json"))
        self.assertFalse(h.p(".gemini/config/plugins/%s/hooks.json" % NAME).exists())
        self.assertTrue(h.p(".claude/skills/%s/SKILL.md" % NAME).exists())

    def test_installed_hook_commands_run(self):
        h = Home(self)
        h.run("--hooks")
        neutral = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, neutral, True)
        cmds = our_commands(h.json(".claude/settings.json")["hooks"]) + \
            our_commands(h.json(".gemini/settings.json")["hooks"])
        for cmd in cmds:
            p = subprocess.run(["sh", "-c", cmd], input=json.dumps({"cwd": neutral}), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
            self.assertEqual(p.returncode, 0, (cmd, p.stdout))
            self.assertIn(p.stdout.strip(), ("", "{}"), cmd)

    def test_replaces_old_versions_but_not_other_skills(self):
        h = Home(self)
        old_plugin = h.p(".gemini/config/plugins/" + NAME)
        (old_plugin / "skills" / NAME).mkdir(parents=True)
        (old_plugin / "plugin.json").write_text(json.dumps({"name": NAME, "version": "2.0.0"}))
        old_copy = h.p(".claude/skills/" + NAME)
        old_copy.mkdir(parents=True)
        (old_copy / "SKILL.md").write_text("---\nname: %s\nmetadata:\n  version: \"2.4.0\"\n---\nold\n" % NAME)
        other = h.p(".agents/skills/" + NAME)
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("---\nname: someone-elses-skill\n---\n")
        out = h.run()
        self.assertIn("SKIPPED", out)
        self.assertEqual((other / "SKILL.md").read_text(), "---\nname: someone-elses-skill\n---\n")
        self.assertTrue(os.path.islink(str(old_copy)))
        backups = sorted(p.name for p in h.p(".local/share/agent-skills/backups").iterdir())
        self.assertEqual(len(backups), 2, backups)
        self.assertTrue(any(b.startswith("antigravity-plugin-") for b in backups))
        self.assertTrue((old_plugin / ".sci-install.json").is_file())

    def test_copy_mode_and_project(self):
        h = Home(self, agents=("claude",))
        proj = h.root / "proj"
        proj.mkdir()
        h.run("--copy", "--project", str(proj))
        dst = h.p(".claude/skills/" + NAME)
        self.assertFalse(os.path.islink(str(dst)))
        self.assertTrue((dst / ".sci-install.json").is_file())
        for sub in (".claude/skills", ".agents/skills"):
            self.assertTrue((proj / sub / NAME / "SKILL.md").is_file())
        h.run("--uninstall", "--project", str(proj))
        self.assertFalse(dst.exists())
        self.assertFalse((proj / ".claude/skills" / NAME).exists())


if __name__ == "__main__":
    unittest.main()
