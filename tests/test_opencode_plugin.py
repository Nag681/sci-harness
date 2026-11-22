#!/usr/bin/env python3
"""scripts/opencode_plugin.mjs under Node, with OpenCode's plugin context stood in: both entry points (1.x
`server`, 2.x `setup`) block a destructive command, let others run and add the sci briefing to the system prompt;
outside a sci repository, or without a working Python, they do nothing."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent / "scripts" / "opencode_plugin.mjs"
SCI = HERE.parent / "scripts" / "sci.py"
PY = sys.executable
NODE = shutil.which("node")

HARNESS = r'''
import sci from %(plugin)s;
const dir = process.argv[2];
const plugin = sci(JSON.parse(process.argv[3]));
const v2 = {};
await plugin.setup({
  location: { directory: dir },
  tool: { hook: async (name, fn) => { v2["tool." + name] = fn; } },
  session: { hook: async (name, fn) => { v2["session." + name] = fn; } },
});
const v1 = await plugin.server({ directory: dir });
const blocked = async (fn) => { try { await fn(); return ""; } catch (e) { return String(e.message); } };
const out = { id: plugin.id };
out.v2_reset = await blocked(() => v2["tool.execute.before"]({ tool: "bash", input: { command: "git reset --hard" } }));
out.v2_status = await blocked(() => v2["tool.execute.before"]({ tool: "bash", input: { command: "git status" } }));
out.v2_read = await blocked(() => v2["tool.execute.before"]({ tool: "read", input: { filePath: "a.txt" } }));
const ctx = { system: [] };
v2["session.context"](ctx);
out.v2_system = ctx.system;
out.v1_stash = await blocked(() => v1["tool.execute.before"]({ tool: "bash" }, { args: { command: "git stash" } }));
out.v1_ls = await blocked(() => v1["tool.execute.before"]({ tool: "bash" }, { args: { command: "ls" } }));
const sys1 = { system: [] };
await v1["experimental.chat.system.transform"]({ sessionID: "s" }, sys1);
out.v1_system = sys1.system;
console.log(JSON.stringify(out));
'''


@unittest.skipUnless(NODE, "node not installed")
class TestOpenCodePlugin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sci-opencode-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.harness = self.tmp / "harness.mjs"
        self.harness.write_text(HARNESS % {"plugin": json.dumps(PLUGIN.as_uri())}, encoding="utf-8")

    def repo(self, sci=True):
        d = self.tmp / ("repo-sci" if sci else "repo-plain")
        d.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.com")
        for args in (["init", "-q"], ["config", "commit.gpgsign", "false"]):
            subprocess.run(["git"] + args, cwd=str(d), check=True, env=env)
        (d / "a.txt").write_text("a\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(d), check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "a"], cwd=str(d), check=True, env=env)
        if sci:
            p = subprocess.run([PY, str(SCI), "init", "--objective", "Ship the parser"], cwd=str(d),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", env=env)
            self.assertEqual(p.returncode, 0, p.stdout)
        return d

    def run_harness(self, repo, python):
        p = subprocess.run([NODE, str(self.harness), str(repo), json.dumps(python)], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_both_generations_block_destructive_commands_and_brief_the_model(self):
        out = self.run_harness(self.repo(), [PY])
        self.assertEqual(out["id"], "sci")
        for k in ("v2_reset", "v1_stash"):
            self.assertIn("Blocked by the sci harness", out[k])
        self.assertIn("git reset --hard", out["v2_reset"])
        self.assertEqual((out["v2_status"], out["v2_read"], out["v1_ls"]), ("", "", ""))
        self.assertEqual(len(out["v2_system"]), 1)
        self.assertEqual(out["v2_system"][0]["type"], "text")
        for text in (out["v2_system"][0]["text"], out["v1_system"][0]):
            self.assertIn("[sci] Long-horizon harness active", text)
            self.assertIn("Ship the parser", text)

    def test_outside_a_sci_repository_and_without_python_it_does_nothing(self):
        for repo, python in ((self.repo(sci=False), [PY]), (self.repo(), [str(self.tmp / "no-such-python")])):
            out = self.run_harness(repo, python)
            self.assertEqual((out["v2_reset"], out["v1_stash"], out["v2_system"], out["v1_system"]), ("", "", [], []))


if __name__ == "__main__":
    unittest.main()
