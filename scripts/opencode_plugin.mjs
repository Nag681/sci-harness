// The sci hooks for OpenCode. OpenCode has no hook commands: plugins are modules whose functions it calls, so this
// one asks scripts/sci_hook.py the same questions the hooks of the other agents ask it.
//
//   before a tool runs      `opencode-pre-tool`: a destructive git command (or deleting .sci/) is refused, and
//                           the reason is what the model reads instead of the command's output
//   every model request     `opencode-context`: the objective and NEXT step join the system prompt, so they
//                           survive compaction (asked again only after a sci command changed .sci/state.json)
//
// install.py --hooks writes ~/.config/opencode/plugins/sci.js, which imports this file and passes the Python to
// run. The default export serves both OpenCode generations: 1.x calls `server`, 2.x calls `setup`.
// It fails open: when Python or the hook is missing, slow or broken, nothing is blocked and nothing is added.
// Outside repositories with an active .sci/ the hook answers nothing, so the plugin does nothing there.
import { spawn, spawnSync } from "node:child_process";
import { statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HOOK = join(dirname(fileURLToPath(import.meta.url)), "sci_hook.py");
const TIMEOUT_MS = 10000;
// OpenCode 2.x runs plugins in a background service, which on Windows has no console: without this, every hook
// would open a console window of its own.
const HIDDEN = { windowsHide: true };

const parse = (text) => {
  try {
    const v = JSON.parse(String(text || "").trim().split("\n").pop() || "null");
    return v && typeof v === "object" ? v : {};
  } catch {
    return {};
  }
};

/** .sci/state.json of the repository around `dir` and its change time, or null. */
function stateOf(dir) {
  for (let d = dir; d; ) {
    try {
      const file = join(d, ".sci", "state.json");
      return { file, mtime: statSync(file).mtimeMs };
    } catch {
      /* not here */
    }
    try {
      statSync(join(d, ".git"));
      return null; // the repository root holds no .sci/
    } catch {
      /* keep looking upwards */
    }
    const up = dirname(d);
    if (up === d) return null;
    d = up;
  }
  return null;
}

export default function sci(python) {
  const py = Array.isArray(python) && python.length ? python : [process.platform === "win32" ? "python" : "python3"];
  const briefings = new Map(); // state file -> { mtime, text }

  /** The hook's answer to `mode`, without waiting on the event loop (for hooks OpenCode may not await). */
  function askSync(mode, payload) {
    try {
      const r = spawnSync(py[0], [...py.slice(1), HOOK, mode], {
        ...HIDDEN,
        input: JSON.stringify(payload),
        encoding: "utf8",
        timeout: TIMEOUT_MS,
        stdio: ["pipe", "pipe", "ignore"],
        env: { ...process.env, PYTHONUTF8: "1" },
      });
      return r.status === 0 ? parse(r.stdout) : {};
    } catch {
      return {};
    }
  }

  function ask(mode, payload) {
    return new Promise((done) => {
      let out = "";
      let child;
      try {
        child = spawn(py[0], [...py.slice(1), HOOK, mode], {
          ...HIDDEN,
          stdio: ["pipe", "pipe", "ignore"],
          env: { ...process.env, PYTHONUTF8: "1" },
        });
      } catch {
        done({});
        return;
      }
      const timer = setTimeout(() => {
        try {
          child.kill();
        } catch {
          /* gone */
        }
        done({});
      }, TIMEOUT_MS);
      child.stdout.setEncoding("utf8");
      child.stdout.on("data", (d) => (out += d));
      child.on("error", () => {
        clearTimeout(timer);
        done({});
      });
      child.on("close", () => {
        clearTimeout(timer);
        done(parse(out));
      });
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify(payload));
    });
  }

  /** The sci briefing for requests made in `dir`, or "". */
  function briefing(dir) {
    const s = stateOf(dir);
    if (!s) return "";
    const seen = briefings.get(s.file);
    if (seen && seen.mtime === s.mtime) return seen.text;
    const res = askSync("opencode-context", { cwd: dir });
    const text = typeof res.system === "string" ? res.system : "";
    briefings.set(s.file, { mtime: s.mtime, text });
    return text;
  }

  /** Why the command a tool is about to run must not run, or "". */
  async function refusal(dir, args) {
    const command = args && typeof args === "object" ? args.command ?? args.cmd : undefined;
    if (typeof command !== "string" || !command.trim() || !stateOf(dir)) return "";
    const res = await ask("opencode-pre-tool", { cwd: dir, tool_input: { command } });
    return typeof res.deny === "string" ? res.deny : "";
  }

  return {
    id: "sci",

    // OpenCode 1.x
    async server(input) {
      const dir = (input && input.directory) || process.cwd();
      return {
        "tool.execute.before": async (_call, output) => {
          const why = await refusal(dir, output && output.args);
          if (why) throw new Error(why);
        },
        "experimental.chat.system.transform": async (_call, output) => {
          const text = briefing(dir);
          if (text && output && Array.isArray(output.system)) output.system.push(text);
        },
      };
    },

    // OpenCode 2.x. Each hook is registered on its own, so a release without one still gets the other.
    async setup(ctx) {
      const dir = (ctx && ctx.location && ctx.location.directory) || process.cwd();
      try {
        await ctx.tool.hook("execute.before", async (event) => {
          const why = await refusal(dir, event && (event.input ?? event.args));
          if (why) throw new Error(why);
        });
      } catch {
        /* no tool hooks in this release */
      }
      try {
        await ctx.session.hook("context", (event) => {
          const text = briefing(dir);
          if (text && event && Array.isArray(event.system)) event.system.push({ type: "text", text });
        });
      } catch {
        /* no context hook in this release */
      }
    },
  };
}
