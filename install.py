#!/usr/bin/env python3
"""Install the software-engineer skill (the sci harness) for the coding agents on this machine.

  python3 install.py                 install for every detected agent (skill files only, no config edits)
  python3 install.py --hooks         also register the sci hooks in the agents' configs (backups first)
  python3 install.py --remove-hooks  take the hooks out again, keep the skill
  python3 install.py --status        show what is installed where
  python3 install.py --uninstall     remove everything this installer added, hooks included

Options: --only AGENTS (claude,codex,gemini,opencode,pi,antigravity), --copy (copies instead of symlinks),
--prefix DIR (where the installed copy lives), --target DIR (also link into another agent's skills dir),
--project DIR (copy into a repository's .claude/skills and .agents/skills), --dry-run, --force.

Standard library only; Python 3.8+. Linux, macOS and Windows (falls back to copies where symlinks fail).
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
WINDOWS = os.name == "nt"
MARK = ".sci-install.json"  # written into every copy and plugin this installer creates
HOOK_TAG = "sci_hook.py"    # identifies our entries in agent hook configs
STAMP = time.strftime("%Y%m%d-%H%M%S")
# Repository files that agents do not need: kept out of every installed copy.
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".github", ".gitignore", ".gitattributes",
                                ".pytest_cache", ".sci", "tests", "install.sh", "install.ps1", MARK)


def skill_meta(d):
    """(name, version) from d/SKILL.md, or (None, None)."""
    try:
        text = (Path(d) / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return None, None
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    fm = m.group(1) if m else ""
    name = re.search(r"^name:\s*(\S+)", fm, re.M)
    ver = re.search(r"^\s+version:\s*[\"']?([\w.+-]+)", fm, re.M)
    return (name.group(1) if name else None), (ver.group(1) if ver else None)


NAME, VERSION = skill_meta(HERE)


def data_home():
    # Also on Windows: the Microsoft Store Python redirects writes under AppData into its private folder,
    # where the agents would never see the skill.
    return Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")


def tilde(p):
    s = str(p)
    h = str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + os.sep) else s


def shell_path(p):
    """A path to paste into a command: ~/... on POSIX; quoted and absolute on Windows (cmd.exe and PowerShell
    do not expand ~ for programs)."""
    return '"%s"' % p if WINDOWS else tilde(p)


def runs_python(argv):
    try:
        return subprocess.run(argv + ["-c", "import sys; sys.exit(sys.version_info < (3, 8))"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@functools.lru_cache(maxsize=None)
def python_cmd():
    """How hooks and printed commands start Python 3.8+ here: python3 on POSIX; on Windows py -3, python or
    python3, whichever really runs (the Microsoft Store stubs named python/python3 only print an install hint).
    Falls back to the interpreter running this installer."""
    cands = [["py", "-3"], ["python"], ["python3"]] if WINDOWS else [["python3"]]
    for c in cands:
        if shutil.which(c[0]) and runs_python(c):
            return " ".join(c)
    exe = Path(sys.executable).as_posix()
    return '"%s"' % exe if " " in exe else exe


CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
AGENTS_SKILLS = HOME / ".agents" / "skills"
AGY_PLUGINS = HOME / ".gemini" / "config" / "plugins"
GEMINI_SETTINGS = HOME / ".gemini" / "settings.json"

# agent -> (skills directory it reads, how to detect it, hook config kind)
AGENTS = {
    "claude": (CLAUDE_HOME / "skills", ("claude",), CLAUDE_HOME, "claude"),
    "codex": (AGENTS_SKILLS, ("codex",), CODEX_HOME, "codex"),
    "gemini": (AGENTS_SKILLS, ("gemini",), None, "gemini"),
    "opencode": (AGENTS_SKILLS, ("opencode",), HOME / ".config" / "opencode", None),
    "pi": (AGENTS_SKILLS, ("pi",), HOME / ".pi", None),
    "antigravity": (None, ("agy", "antigravity"), AGY_PLUGINS, "antigravity"),
}
READERS = {CLAUDE_HOME / "skills": "Claude Code, OpenCode", AGENTS_SKILLS: "Gemini CLI, Codex, OpenCode, pi"}
# Where older or manual installs may live: shown by --status, removed by --uninstall when they are ours.
OTHER_SKILL_DIRS = [CODEX_HOME / "skills", HOME / ".gemini" / "skills", HOME / ".gemini" / "config" / "skills",
                    HOME / ".config" / "opencode" / "skill", HOME / ".config" / "opencode" / "skills",
                    HOME / ".cursor" / "skills"]


def detected(agent):
    _, exes, home, _ = AGENTS[agent]
    return any(shutil.which(e) for e in exes) or bool(home and home.is_dir())


class Plan(object):
    """Collects actions; performs them unless dry-run."""

    def __init__(self, dry):
        self.dry = dry
        self.lines = []

    def say(self, msg):
        self.lines.append(msg)
        print(("[dry-run] " if self.dry else "") + msg)


# --------------------------------------------------------------------------- files

def is_ours(path, home_dir):
    """True if path is a link to our installed copy, or a copy/plugin made by this installer."""
    if os.path.islink(str(path)):
        try:
            return Path(os.path.realpath(str(path))) == Path(os.path.realpath(str(home_dir)))
        except OSError:
            return False
    return (Path(path) / MARK).is_file()


def write_mark(d, kind):
    (Path(d) / MARK).write_text(json.dumps({"name": NAME, "version": VERSION, "kind": kind, "source": str(HERE),
                                            "installed": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2) + "\n",
                                encoding="utf-8")


def _clear_readonly(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)  # Windows refuses to delete read-only files
    func(path)


def remove_path(p):
    if os.path.islink(str(p)):
        try:
            os.unlink(str(p))
        except OSError:
            os.rmdir(str(p))  # a directory symlink on Windows
    elif Path(p).is_file():
        os.unlink(str(p))
    elif Path(p).is_dir():
        if sys.version_info >= (3, 12):
            shutil.rmtree(str(p), onexc=_clear_readonly)
        else:
            shutil.rmtree(str(p), onerror=_clear_readonly)


def replace_dir(src, dst):
    """os.replace with retries: on Windows a file in use (a running hook, a virus scanner) blocks the rename."""
    for attempt in range(10):
        try:
            os.replace(str(src), str(dst))
            return
        except PermissionError:
            if attempt == 9:
                sys.exit("Cannot replace %s: a file in it is in use. Close running agents and try again." % dst)
            time.sleep(0.5)


def backup_dir(prefix):
    return prefix / "backups"


def move_aside(plan, p, prefix, label):
    dst = backup_dir(prefix) / ("%s-%s" % (label, STAMP))
    plan.say("  moved the previous %s to %s" % (tilde(p), tilde(dst)))
    if not plan.dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dst))


def install_home(plan, home_dir, prefix):
    """Copy this skill folder to the installed location (skipped when running from it)."""
    if home_dir.exists() and HERE == home_dir.resolve():
        plan.say("Installed copy: %s (running from it)" % tilde(home_dir))
        return
    plan.say("Installed copy: %s  <- %s" % (tilde(home_dir), tilde(HERE)))
    if os.path.lexists(str(home_dir)) and not os.path.islink(str(home_dir)) and not (home_dir / MARK).is_file():
        move_aside(plan, home_dir, prefix, "installed-copy")
    if plan.dry:
        return
    home_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = home_dir.with_name(home_dir.name + ".new-sci")
    old = home_dir.with_name(home_dir.name + ".old-sci")
    for p in (tmp, old):
        if os.path.lexists(str(p)):
            remove_path(p)
    shutil.copytree(str(HERE), str(tmp), ignore=IGNORE)
    write_mark(tmp, "installed-copy")
    if os.path.lexists(str(home_dir)):
        replace_dir(home_dir, old)
    replace_dir(tmp, home_dir)
    if os.path.lexists(str(old)):
        remove_path(old)


def place(plan, home_dir, dst, copy, prefix, force, label):
    """Make dst provide the skill: a symlink to home_dir (default) or a copy."""
    if os.path.lexists(str(dst)):
        if is_ours(dst, home_dir) and not copy and os.path.islink(str(dst)):
            plan.say("  %s: already linked" % tilde(dst))
            return
        name, _ = skill_meta(dst)
        if is_ours(dst, home_dir) or os.path.islink(str(dst)):
            if not is_ours(dst, home_dir):
                plan.say("  replacing link %s -> %s" % (tilde(dst), os.readlink(str(dst))))
            if not plan.dry:
                remove_path(dst)
        elif name == NAME or force:
            move_aside(plan, dst, prefix, label)
        else:
            plan.say("  SKIPPED %s: it holds a different skill (%s); use --force to replace it" % (tilde(dst), name))
            return
    if plan.dry:
        plan.say("  %s: %s" % (tilde(dst), "copy" if copy else "link -> " + tilde(home_dir)))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    note = ""
    if not copy:
        try:
            os.symlink(str(home_dir), str(dst), target_is_directory=True)
            plan.say("  %s: link -> %s" % (tilde(dst), tilde(home_dir)))
            return
        except (OSError, NotImplementedError):
            # Windows without Developer Mode or admin rights
            note = " (symlinks are not allowed here; run the installer again after updating)"
    shutil.copytree(str(home_dir), str(dst), ignore=IGNORE)
    write_mark(dst, "copy")
    plan.say("  %s: copy%s" % (tilde(dst), note))


def unplace(plan, home_dir, dst):
    if os.path.lexists(str(dst)) and is_ours(dst, home_dir):
        plan.say("  removed %s" % tilde(dst))
        if not plan.dry:
            remove_path(dst)


# --------------------------------------------------------------------------- hooks

def hook_cmd(home_dir, mode):
    # Forward slashes: the same string works in sh, bash, cmd.exe and PowerShell, whichever runs the hook.
    return '%s "%s" %s' % (python_cmd(), (home_dir / "scripts" / "sci_hook.py").as_posix(), mode)


def claude_style_hooks(home_dir, tool_matcher):
    """Claude Code and Codex share this format; Codex gets no matcher (its shell tool names vary)."""
    def entry(mode):
        return {"type": "command", "command": hook_cmd(home_dir, mode), "timeout": 10}
    pre = {"hooks": [entry("claude-pre-tool")]}
    if tool_matcher:
        pre = {"matcher": tool_matcher, "hooks": pre["hooks"]}
    return {"SessionStart": [{"hooks": [entry("claude-session-start")]}],
            "UserPromptSubmit": [{"hooks": [entry("claude-prompt")]}],
            "PreToolUse": [pre]}


def gemini_hooks(home_dir):
    def entry(name, mode):
        return {"name": name, "type": "command", "command": hook_cmd(home_dir, mode), "timeout": 10000}
    return {"SessionStart": [{"hooks": [entry("sci-session-start", "gemini-session-start")]}],
            "BeforeAgent": [{"hooks": [entry("sci-before-agent", "gemini-before-agent")]}],
            "BeforeTool": [{"matcher": "run_shell_command",
                            "hooks": [entry("sci-before-tool", "gemini-before-tool")]}]}


def agy_hooks(home_dir):
    def entry(mode):
        return {"type": "command", "command": hook_cmd(home_dir, mode), "timeout": 10}
    return {"sci-harness": {"SessionStart": [entry("agy-session-start")],
                            "PreInvocation": [entry("agy-pre-invocation")],
                            "PreToolUse": [{"matcher": "run_command", "hooks": [entry("agy-pre-tool")]}],
                            "Stop": [entry("agy-stop")]}}


def _ours(h):
    return isinstance(h, dict) and HOOK_TAG in str(h.get("command", ""))


def strip_hooks(events):
    """Remove our handlers from an {event: [group or handler, ...]} mapping. Returns how many were removed."""
    removed = 0
    for ev in list(events):
        groups = events[ev]
        if not isinstance(groups, list):
            continue
        keep = []
        for g in groups:
            if _ours(g):
                removed += 1
                continue
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                inner = [h for h in g["hooks"] if not _ours(h)]
                removed += len(g["hooks"]) - len(inner)
                if not inner and g["hooks"]:
                    continue
                g = dict(g, hooks=inner)
            keep.append(g)
        if keep:
            events[ev] = keep
        else:
            del events[ev]
    return removed


def count_hooks(events):
    n = 0
    for groups in (events or {}).values():
        for g in groups if isinstance(groups, list) else []:
            if _ours(g):
                n += 1
            elif isinstance(g, dict) and isinstance(g.get("hooks"), list):
                n += sum(1 for h in g["hooks"] if _ours(h))
    return n


def hooks_present(agent):
    kind = AGENTS[agent][3]
    if kind == "antigravity":
        hj = agy_plugin_dir() / "hooks.json"
        return hj.is_file() and HOOK_TAG in hj.read_text(encoding="utf-8")
    path = {"claude": CLAUDE_HOME / "settings.json", "codex": CODEX_HOME / "hooks.json",
            "gemini": GEMINI_SETTINGS}.get(kind)
    if path is None:
        return False
    data, _ = load_json(path)
    events = (data or {}).get("hooks")
    return count_hooks(events if isinstance(events, dict) else {}) > 0


def load_json(path):
    """(data, raw) for a JSON object file; (None, error) if it cannot be parsed. Missing file -> ({}, None)."""
    if not path.exists():
        return {}, None
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError as e:
        return None, "cannot parse %s (%s); edit it by hand" % (tilde(path), e)
    if not isinstance(data, dict):
        return None, "%s is not a JSON object; edit it by hand" % tilde(path)
    return data, raw


def edit_json(plan, path, fn):
    """Apply fn(data) to a JSON settings file, keeping a timestamped backup of the old file.
    Returns "changed", "unchanged" or None (file could not be parsed)."""
    data, raw = load_json(path)
    if data is None:
        plan.say("  SKIPPED: " + raw)
        return None
    before = json.dumps(data, sort_keys=True)
    fn(data)
    if json.dumps(data, sort_keys=True) == before:
        return "unchanged"
    if not plan.dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            bak = path.with_name(path.name + ".bak-sci-" + STAMP)
            bak.write_text(raw, encoding="utf-8")
            plan.say("  backup: %s" % tilde(bak))
        tmp = path.with_name(path.name + ".tmp-sci")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    return "changed"


def set_hooks(plan, path, new_events, label, on):
    def fn(data):
        events = data.get("hooks")
        if not isinstance(events, dict):
            events = {}
        strip_hooks(events)
        if on:
            for ev, groups in new_events.items():
                events.setdefault(ev, [])
                events[ev] = list(events[ev]) + groups
        if events:
            data["hooks"] = events
        elif "hooks" in data:
            del data["hooks"]
    res = edit_json(plan, path, fn)
    if res == "changed" or (res == "unchanged" and on):
        plan.say("  %s hooks %s: %s" % (label, "on" if on else "off", tilde(path)))


def codex_hooks_enabled():
    try:
        text = (CODEX_HOME / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    sec = re.search(r"^\[features\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    return bool(sec and re.search(r"^\s*hooks\s*=\s*true\b", sec.group(1), re.M))


# --------------------------------------------------------------------------- antigravity plugin

def agy_plugin_dir():
    return AGY_PLUGINS / NAME


def install_agy(plan, home_dir, copy, prefix, force, hooks):
    pdir = agy_plugin_dir()
    if os.path.lexists(str(pdir)) and not is_ours(pdir, home_dir):
        try:
            pname = json.loads((pdir / "plugin.json").read_text(encoding="utf-8")).get("name")
        except (OSError, ValueError):
            pname = None
        if pname == NAME or force:
            move_aside(plan, pdir, prefix, "antigravity-plugin")
        else:
            plan.say("  SKIPPED %s: it holds another plugin (%s); use --force" % (tilde(pdir), pname))
            return
    plan.say("  %s: plugin%s" % (tilde(pdir), " with hooks" if hooks else ""))
    if plan.dry:
        return
    pdir.mkdir(parents=True, exist_ok=True)
    desc = ("Durable-memory harness for long-horizon scientific and numerical coding in large codebases; built "
            "for small models.")
    (pdir / "plugin.json").write_text(json.dumps({"name": NAME, "displayName": "Software Engineer (sci)",
                                                  "description": desc, "version": VERSION}, indent=2) + "\n",
                                      encoding="utf-8")
    write_mark(pdir, "antigravity-plugin")
    place(Plan(plan.dry), home_dir, pdir / "skills" / NAME, copy, prefix, True, "antigravity-skill")
    set_agy_hooks(plan, home_dir, hooks, quiet=True)


def set_agy_hooks(plan, home_dir, on, quiet=False):
    pdir = agy_plugin_dir()
    if not (pdir / MARK).is_file() and not plan.dry:
        if on:
            plan.say("  Antigravity: plugin not installed by this installer; run without --only or install first")
        return
    hj = pdir / "hooks.json"
    if on:
        if not plan.dry:
            hj.write_text(json.dumps(agy_hooks(home_dir), indent=2) + "\n", encoding="utf-8")
    elif hj.exists() and HOOK_TAG in hj.read_text(encoding="utf-8"):
        if not plan.dry:
            hj.unlink()
    if not quiet or on:
        plan.say("  Antigravity hooks %s: %s" % ("on" if on else "off", tilde(hj)))


# --------------------------------------------------------------------------- commands

def chosen_agents(args):
    if args.only:
        names = [x.strip() for x in args.only.split(",") if x.strip()]
        bad = [n for n in names if n not in AGENTS]
        if bad:
            sys.exit("Unknown agent(s): %s. Choose from: %s" % (", ".join(bad), ", ".join(AGENTS)))
        return names
    return [a for a in AGENTS if detected(a)]


def apply_hooks(plan, home_dir, agents, on):
    for a in agents:
        kind = AGENTS[a][3]
        if kind == "claude":
            set_hooks(plan, CLAUDE_HOME / "settings.json", claude_style_hooks(home_dir, "Bash"), "Claude Code", on)
        elif kind == "codex":
            set_hooks(plan, CODEX_HOME / "hooks.json", claude_style_hooks(home_dir, None), "Codex", on)
            if on and not codex_hooks_enabled():
                plan.say("  note: Codex runs hooks only with this in %s:  [features]  hooks = true"
                         % tilde(CODEX_HOME / "config.toml"))
        elif kind == "gemini":
            set_hooks(plan, GEMINI_SETTINGS, gemini_hooks(home_dir), "Gemini CLI", on)
        elif kind == "antigravity":
            set_agy_hooks(plan, home_dir, on)


def cmd_install(args, prefix, home_dir):
    plan = Plan(args.dry_run)
    agents = chosen_agents(args)
    if not agents and not args.target and not args.project:
        print("No supported agent found (claude, codex, gemini, opencode, pi, antigravity). Use --only or --target.")
        return 1
    print("Installing %s %s for: %s" % (NAME, VERSION, ", ".join(agents) or "(explicit targets only)"))
    install_home(plan, home_dir, prefix)
    done = set()
    for a in agents:
        sdir = AGENTS[a][0]
        if a == "antigravity":
            install_agy(plan, home_dir, args.copy, prefix, args.force, args.hooks or hooks_present(a))
        elif sdir not in done:
            done.add(sdir)
            place(plan, home_dir, sdir / NAME, args.copy, prefix, args.force, sdir.parent.name.strip(".") + "-skill")
    for t in args.target or []:
        place(plan, home_dir, Path(t).expanduser().resolve() / NAME, args.copy, prefix, args.force, "target-skill")
    for proj in args.project or []:
        root = Path(proj).expanduser().resolve()
        for sub in (".claude/skills", ".agents/skills"):
            place(plan, home_dir, root / sub / NAME, True, prefix, args.force, "project-skill")
    refresh = [a for a in agents if a != "antigravity" and (args.hooks or hooks_present(a))]
    if refresh:
        apply_hooks(plan, home_dir, refresh, True)
    for a in agents:
        if a == "codex" and (CODEX_HOME / "skills" / NAME).exists() and not is_ours(CODEX_HOME / "skills" / NAME,
                                                                                         home_dir):
            print("note: %s also exists; Codex may list the skill twice." % tilde(CODEX_HOME / "skills" / NAME))
    print("")
    print("Done. Restart running agent sessions so they see the skill.")
    if not args.hooks and not any(hooks_present(a) for a in agents):
        print("Optional: `%s %s --hooks` adds hooks that re-orient the agent after context compaction and block "
              "destructive git commands (only inside repos where `.sci/` exists)."
              % (python_cmd(), shell_path(home_dir / "install.py")))
    print("In a repository, the agent starts with: %s %s init --objective \"<goal>\""
          % (python_cmd(), shell_path(home_dir / "scripts" / "sci.py")))
    return 0


def cmd_remove_hooks(args, home_dir):
    plan = Plan(args.dry_run)
    agents = chosen_agents(args) if args.only else list(AGENTS)
    apply_hooks(plan, home_dir, [a for a in agents if AGENTS[a][3]], False)
    return 0


def cmd_uninstall(args, prefix, home_dir):
    plan = Plan(args.dry_run)
    print("Removing %s installed by this installer" % NAME)
    agents = chosen_agents(args) if args.only else list(AGENTS)
    apply_hooks(plan, home_dir, [a for a in agents if AGENTS[a][3] and a != "antigravity"], False)
    if "antigravity" in agents:
        pdir = agy_plugin_dir()
        if os.path.lexists(str(pdir)) and is_ours(pdir, home_dir):
            plan.say("  removed %s" % tilde(pdir))
            if not plan.dry:
                remove_path(pdir)
    dirs = sorted(set([CLAUDE_HOME / "skills", AGENTS_SKILLS] + OTHER_SKILL_DIRS), key=str)
    for d in dirs + [Path(t).expanduser().resolve() for t in args.target or []]:
        unplace(plan, home_dir, d / NAME)
    for proj in args.project or []:
        for sub in (".claude/skills", ".agents/skills"):
            unplace(plan, home_dir, Path(proj).expanduser().resolve() / sub / NAME)
    if not args.only and os.path.lexists(str(home_dir)) and (home_dir / MARK).is_file():
        plan.say("  removed %s" % tilde(home_dir))
        if not plan.dry:
            remove_path(home_dir)
    if backup_dir(prefix).is_dir():
        print("Earlier versions this installer replaced are kept in %s" % tilde(backup_dir(prefix)))
    print("Repositories keep their .sci/ folders; inside one, `.sci/sci purge --yes` removes the harness state.")
    return 0


def describe(p, home_dir):
    if os.path.islink(str(p)):
        tgt = os.readlink(str(p))
        if not os.path.exists(str(p)):
            return "BROKEN link -> " + tgt
        return "link -> " + tilde(tgt) + ("" if is_ours(p, home_dir) else "  (not ours)")
    if p.is_dir():
        name, ver = skill_meta(p)
        return "%s %s%s" % ("copy" if (p / MARK).is_file() else "folder", name or "?", " " + ver if ver else "")
    return None


def cmd_status(args, prefix, home_dir):
    print("%s %s (this folder: %s)" % (NAME, VERSION, tilde(HERE)))
    name, ver = skill_meta(home_dir)
    print("installed copy: %s %s" % (tilde(home_dir), ("(%s)" % ver) if name else "(missing)"))
    print("skills directories:")
    for d in [CLAUDE_HOME / "skills", AGENTS_SKILLS] + OTHER_SKILL_DIRS:
        state = describe(d / NAME, home_dir)
        if state or d in READERS:
            print("  %-48s %s%s" % (tilde(d / NAME), state or "-", ("   [%s]" % READERS[d]) if d in READERS else ""))
    pdir = agy_plugin_dir()
    if os.path.lexists(str(pdir)):
        try:
            pv = json.loads((pdir / "plugin.json").read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            pv = "?"
        hooks = (pdir / "hooks.json").exists() and HOOK_TAG in (pdir / "hooks.json").read_text(encoding="utf-8")
        print("antigravity plugin: %s (version %s, %s, hooks %s)" % (tilde(pdir), pv,
                                                                     "ours" if is_ours(pdir, home_dir) else "not ours",
                                                                     "on" if hooks else "off"))
    print("hooks:")
    for label, path in (("Claude Code", CLAUDE_HOME / "settings.json"), ("Codex", CODEX_HOME / "hooks.json"),
                        ("Gemini CLI", GEMINI_SETTINGS)):
        data, _ = load_json(path)
        n = count_hooks((data or {}).get("hooks") if isinstance((data or {}).get("hooks"), dict) else {})
        extra = ""
        if label == "Codex" and n and not codex_hooks_enabled():
            extra = "  (disabled: config.toml lacks [features] hooks = true)"
        print("  %-12s %-34s %s%s" % (label, tilde(path), ("on (%d handlers)" % n) if n else "off", extra))
    print("detected agents: " + (", ".join(a for a in AGENTS if detected(a)) or "none"))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Install the %s skill for coding agents." % NAME)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="show what is installed where")
    g.add_argument("--uninstall", action="store_true", help="remove everything this installer added")
    g.add_argument("--remove-hooks", action="store_true", help="remove the hooks, keep the skill")
    p.add_argument("--hooks", action="store_true", help="also register hooks in agent configs (backups first)")
    p.add_argument("--only", help="comma-separated agents: " + ",".join(AGENTS))
    p.add_argument("--copy", action="store_true", help="copy into each skills dir instead of symlinking")
    p.add_argument("--prefix", help="parent dir of the installed copy (default: %s)"
                   % tilde(data_home() / "agent-skills"))
    p.add_argument("--target", action="append", help="another skills directory to install into (repeatable)")
    p.add_argument("--project", action="append", help="repository to copy the skill into (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="show what would change")
    p.add_argument("--force", action="store_true", help="replace a different skill/plugin with the same name")
    args = p.parse_args(argv)
    if not NAME:
        sys.exit("SKILL.md with a name: field must sit next to install.py")
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8+ required")
    prefix = Path(args.prefix).expanduser().resolve() if args.prefix else data_home() / "agent-skills"
    home_dir = prefix / NAME
    if args.status:
        return cmd_status(args, prefix, home_dir)
    if args.uninstall:
        return cmd_uninstall(args, prefix, home_dir)
    if args.remove_hooks:
        return cmd_remove_hooks(args, home_dir)
    if not shutil.which("git"):
        print("warning: git not found; the skill needs git in every repository it manages.")
    return cmd_install(args, prefix, home_dir)


if __name__ == "__main__":
    sys.exit(main())
