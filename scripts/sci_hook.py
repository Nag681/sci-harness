#!/usr/bin/env python3
"""Agent-harness hooks for sci: re-anchor the agent after compaction and block destructive git commands.

Does nothing unless the workspace repo has an initialized .sci/ (and is not paused), so it is safe to
install globally. Never fails: on any error it prints the harness's neutral answer and exits 0.

  sci_hook.py agy-session-start | agy-pre-invocation | agy-pre-tool | agy-stop      (Antigravity CLI/IDE)
  sci_hook.py claude-session-start | claude-prompt | claude-pre-tool                (Claude Code, Codex CLI)
  sci_hook.py gemini-session-start | gemini-before-agent | gemini-before-tool       (Gemini CLI)
  sci_hook.py plain [DIR]                                                           (anything else)

The hook payload (JSON) is read from stdin when present.
"""
import json
import os
import re
import sys
from pathlib import Path


def read_payload():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = sys.stdin.buffer.read().decode("utf-8", "replace")  # agents send UTF-8 whatever the code page
        return json.loads(data) if data.strip() else {}
    except Exception:
        return {}


def uri_path(c):
    """Filesystem path from a path or a file:// URI (file:///C:/x on Windows, file:///home/x elsewhere)."""
    if not c.startswith("file://"):
        return c
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    u = urlparse(c)
    return url2pathname(("//" + u.netloc if u.netloc else "") + u.path)


def find_root(payload, extra=None):
    cands = []
    if extra:
        cands.append(extra)
    for key in ("workspacePaths", "workspace_paths"):
        v = payload.get(key)
        if isinstance(v, list):
            cands += [x for x in v if isinstance(x, str)]
    for key in ("cwd", "workspacePath", "projectDir"):
        v = payload.get(key)
        if isinstance(v, str):
            cands.append(v)
    for key in ("CLAUDE_PROJECT_DIR", "GEMINI_PROJECT_DIR"):
        if os.environ.get(key):
            cands.append(os.environ[key])
    cands.append(os.getcwd())
    for c in cands:
        p = Path(uri_path(c)).expanduser()
        for d in [p] + list(p.parents):
            if (d / ".sci" / "state.json").exists():
                return d
            if (d / ".git").exists():
                break
    return None


def load_state(root):
    for name in ("state.json", "state.json.bak"):
        try:
            with open(str(root / ".sci" / name), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def load_config(root):
    try:
        with open(str(root / ".sci" / "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def anchor(st):
    a = st.get("anchor")
    if a:
        return a
    return "[sci] Objective: %s\n[sci] NEXT: %s" % (st.get("objective", ""), st.get("next", ""))


def briefing(root, st):
    return ("[sci] Long-horizon harness active in %s. The plan and progress live in .sci/ (your memory may be "
            "summarized; trust .sci). Run `.sci/sci resume` before acting.\n%s" % (root, anchor(st)))


QUOTED_RE = re.compile(r'"((?:\\.|[^"\\])*)"|\'([^\']*)\'')
SEP_RE = re.compile(r"&&|\|\||;|\||\n")
CHECKOUT_BRANCH_OPTS = ("-b", "-B", "--orphan", "-t", "--track", "--detach", "-")


def _unquote(seg):
    """Quoted words stay (`rm -rf ".sci"`); quoted text with spaces (messages, notes) is blanked."""
    return QUOTED_RE.sub(lambda m: '""' if re.search(r"\s", m.group(1) or m.group(2) or "")
                         else (m.group(1) or m.group(2) or '""'), seg)


def _checkout_files(s):
    """git checkout <ref> <path>... overwrites files even without `--`; `git checkout <branch>` does not."""
    words = s.split()
    args = words[words.index("checkout") + 1:] if "checkout" in words else []
    if any(a in CHECKOUT_BRANCH_OPTS for a in args):
        return False
    return len([a for a in args if not a.startswith("-") and a != '""' and not re.match(r"\d*[<>&]", a)]) >= 2


def destructive_reason(cmd):
    if not isinstance(cmd, str):
        return None
    # Quoted text is checked only as a command of its own (bash -c "git stash"); a message or note that
    # mentions a git command is not one.
    for m in QUOTED_RE.finditer(cmd):
        q = (m.group(1) if m.group(1) is not None else m.group(2)).replace('\\"', '"')
        for part in SEP_RE.split(q):
            if re.match(r"\s*(git|rm|rmdir|del|remove-item|\S*sci(\.py|\.cmd)?)\s", part, re.I):
                r = destructive_reason(part)
                if r:
                    return r
    for seg in SEP_RE.split(_unquote(cmd)):
        s = " " + " ".join(seg.split()) + " "
        if re.search(r"(^|[\s/\\])sci(\.py|\.cmd)?\s+(purge\b|review\s.*--clear\b)", s):
            return "sci purge / review --clear (human only)"
        has_git = re.search(r"(^|\s)git\s", s) is not None
        if has_git:
            if re.search(r"\sreset\s.*--hard\b", s):
                return "git reset --hard"
            if re.search(r"\scheckout\s", s) and (re.search(r"\s--\s|\s\.\s|\s-f\s|--force|--ours|--theirs", s)
                                                  or _checkout_files(s)):
                return "git checkout of files"
            if re.search(r"\srestore\s", s) and ("--staged" not in s or "--worktree" in s or " -W " in s):
                return "git restore"
            if re.search(r"\sclean\s", s) and re.search(r"\s-[a-zA-Z]*[fdxX]", s):
                return "git clean"
            if re.search(r"\sstash(\s|$)", s) and not re.search(r"\sstash\s+(list|show)\s", s):
                return "git stash"
            if re.search(r"\spush\s", s) and re.search(r"--force|\s-f\s", s):
                return "git push --force"
            if re.search(r"\sbranch\s", s) and re.search(r"\s-D\s|\s--delete\s.*--force|\s--force\s.*--delete", s):
                return "git branch -D"
            if re.search(r"\sswitch\s", s) and re.search(r"--discard-changes|\s-f\s|--force", s):
                return "git switch --discard-changes"
            if re.search(r"\supdate-ref\s.*refs/sci", s):
                return "deleting sci refs"
        if (re.search(r"(^|\s)(rm|rmdir|rd|del|erase|ri|remove-item)\s", s, re.I)
                and re.search(r"(^|[\s/\\'\"])\.sci([/\\\s'\"]|$)", s)):
            return "deleting .sci"
    return None


DENY_TEXT = ("Blocked by the sci harness: `%s` can destroy uncommitted work or harness state. Use "
             "`.sci/sci restore` (it saves the current attempt first) or `.sci/sci restore --paths <file>`. "
             "If the human explicitly asked for this command, ask them to run it themselves.")


def active_state(payload, extra=None):
    root = find_root(payload, extra)
    if root is None:
        return None, None
    st = load_state(root)
    if not st or st.get("paused"):
        return None, None
    return root, st


def tool_command(payload):
    tc = payload.get("toolCall") or {}
    args = tc.get("args") or {}
    for k in ("CommandLine", "commandLine", "command", "cmd"):
        if isinstance(args.get(k), str):
            return args[k]
    ti = payload.get("tool_input") or {}
    for k in ("command", "cmd"):
        v = ti.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, list) and all(isinstance(x, str) for x in v):  # Codex: ["bash", "-lc", "..."]
            return " ".join(v)
    return None


def context(event, text):
    """Context injection in the Claude Code / Codex / Gemini CLI hook protocol."""
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


def stop_decision(root, st, payload):
    cfg = load_config(root)
    if not cfg.get("autopilot"):
        return {}
    if payload.get("terminationReason") not in (None, "", "NO_TOOL_CALL"):
        return {}
    tasks = st.get("tasks") or []
    active = [t for t in tasks if t.get("status") == "active"]
    done = set(t["id"] for t in tasks if t.get("status") == "done")
    eligible = [t for t in tasks if t.get("status") == "todo" and all(d in done for d in t.get("deps") or [])]
    if not active and not eligible:
        return {}
    hs_path = root / ".sci" / "hook_state.json"
    try:
        hs = json.loads(hs_path.read_text(encoding="utf-8"))
    except Exception:
        hs = {}
    if hs.get("updated") != st.get("updated"):
        hs = {"updated": st.get("updated"), "streak": 0}
    if hs.get("streak", 0) >= 2:
        return {}
    hs["streak"] = hs.get("streak", 0) + 1
    try:
        hs_path.write_text(json.dumps(hs), encoding="utf-8")
    except Exception:
        pass
    return {"decision": "continue",
            "reason": "[sci autopilot] The objective is not finished. NEXT: %s  If you need the human, run "
                      "`.sci/sci task block <ID> --reason \"<question>\"` and then stop." % st.get("next", "")}


def main(argv):
    mode = argv[1] if len(argv) > 1 else "plain"
    extra = argv[2] if len(argv) > 2 else None
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # agents read hook output as UTF-8
        except Exception:
            pass
    payload = read_payload()
    json_neutral = mode.startswith(("agy-", "gemini-"))  # these harnesses expect a JSON object on stdout
    try:
        root, st = active_state(payload, extra)
        if mode == "plain":
            if st:
                print(briefing(root, st))
            return 0
        if mode == "agy-session-start":
            res = {"injectSteps": [{"systemMessage": {"systemMessage": briefing(root, st)}}]} if st else {}
        elif mode == "agy-pre-invocation":
            res = {}
            if st:
                every = int(load_config(root).get("anchor_every") or 1)
                n = int(payload.get("invocationNum") or 0)
                if every <= 1 or n % every == 0:
                    res = {"injectSteps": [{"ephemeralMessage": anchor(st)}]}
        elif mode == "agy-pre-tool":
            reason = destructive_reason(tool_command(payload)) if st else None
            res = {"decision": "deny", "reason": DENY_TEXT % reason} if reason else {}
        elif mode == "agy-stop":
            res = stop_decision(root, st, payload) if st else {}
        elif mode == "claude-session-start":
            res = context("SessionStart", briefing(root, st)) if st else None
        elif mode == "claude-prompt":
            res = context("UserPromptSubmit", anchor(st)) if st else None
        elif mode == "claude-pre-tool":
            reason = destructive_reason(tool_command(payload)) if st else None
            res = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": DENY_TEXT % reason}} if reason else None
        elif mode == "gemini-session-start":
            res = context("SessionStart", briefing(root, st)) if st else {}
        elif mode == "gemini-before-agent":
            res = context("BeforeAgent", anchor(st)) if st else {}
        elif mode == "gemini-before-tool":
            reason = destructive_reason(tool_command(payload)) if st else None
            res = {"decision": "deny", "reason": DENY_TEXT % reason} if reason else {}
        else:
            res = {} if json_neutral else None
        if res is not None:
            print(json.dumps(res))
    except Exception:
        if json_neutral:
            print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
