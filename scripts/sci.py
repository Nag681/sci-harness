#!/usr/bin/env python3
"""sci: durable state, verification and recovery for long, multi-step software work.

Everything lives in <repo>/.sci/ (git-ignored through .git/info/exclude):
  state.json    plan, tasks, hypotheses, notes, goldens, locks (change it only through sci)
  HANDOFF.md    human-readable view of the state, regenerated on every change
  events.jsonl  append-only history
  runs/         full log of every command sci ran
  attempts/     patches of failed or reverted attempts (nothing is ever lost)
  golden/       reference numerical outputs
  scratch/      derivation scripts and experiments (never committed)

Standard library only; Python >= 3.8; git >= 2.23. Run `.sci/sci help` for the commands.
"""
from __future__ import annotations

import argparse
import ast
import base64
import contextlib
import datetime as _dt
import difflib
import fnmatch
import functools
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

VERSION = "3.4.1"
SD = ".sci"
SCHEMA = 1
WINDOWS = os.name == "nt"

DEFAULT_CONFIG = {
    "test_cmd": "",
    "regress_cmd": "",
    "build_cmd": "",
    "shell": "",
    "build_globs": [
        "**/*.c", "**/*.h", "**/*.cc", "**/*.cpp", "**/*.cxx", "**/*.hpp", "**/*.cu",
        "**/*.f", "**/*.for", "**/*.f90", "**/*.F90", "**/*.f95", "**/*.F",
        "**/*.pyx", "**/*.pxd", "**/*.rs", "**/CMakeLists.txt", "**/*.cmake", "**/meson.build", "setup.py",
        "setup.cfg", "pyproject.toml", "**/Cargo.toml",
        "**/*.ts", "**/*.tsx", "**/*.mts", "**/*.cts", "**/tsconfig*.json", "**/package.json",
        "**/*.java", "**/*.kt", "**/*.kts", "**/*.scala", "**/pom.xml", "**/*.gradle",
        "**/*.cs", "**/*.csproj", "**/*.swift", "**/*.go", "**/go.mod", "**/*.zig",
    ],
    "test_globs": [
        "tests/**", "test/**", "testing/**", "**/tests/**", "**/test/**", "**/testing/**",
        "**/test_*", "**/*_test.*", "**/*_tests.*", "**/*.test.*", "**/*.spec.*",
        "**/*_spec.*", "**/spec/**", "**/conftest.py", "**/runtests.jl", "**/__tests__/**", "**/*Test.*",
        "**/*Tests.*",
    ],
    "ignore_globs": [
        ".gemini/**", ".claude/**", ".codex/**", ".cursor/**", ".agents/**", ".agent/**",
        ".vscode/**", ".idea/**", "**/__pycache__/**", "**/*.pyc", "**/.pytest_cache/**",
        "**/.mypy_cache/**", "**/.ruff_cache/**", "**/.hypothesis/**", "**/.ipynb_checkpoints/**",
        "**/.DS_Store", "**/*.egg-info/**", ".coverage", "**/.coverage.*", "**/*.swp", "**/*~",
        "graphify-out/**",
    ],
    # Documentation: edits to these never make a verify stale, unless a test or a command reads the file.
    "doc_globs": [
        "**/*.md", "**/*.markdown", "**/*.rst", "**/*.adoc", "**/LICENSE", "**/LICENCE", "**/COPYING",
        "**/AUTHORS", "**/CHANGELOG", "**/CHANGES", "**/NEWS", "**/HISTORY", "**/CITATION.cff",
    ],
    "background": True,
    "wait_secs": 100,
    "check_timeout": 900,
    "verify_timeout": 3600,
    "tail_lines": 25,
    "max_diff_lines": 400,
    "snapshot_max_kb": 512,
    "keep_runs": 80,
    "commit": True,
    "autopilot": False,
    "anchor_every": 1,
}

NOTE_KINDS = ("code", "units", "derivation", "decision", "env", "gotcha", "perf", "other")


# --------------------------------------------------------------------------- basics

class Refuse(Exception):
    """A refusal or usage problem shown to the agent as `REFUSED: ...` (exit 2)."""


def refuse(what, do=None):
    raise Refuse(what + ("\nDO: " + do if do else ""))


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(iso):
    if not iso:
        return "never"
    try:
        t = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return iso
    s = int((_dt.datetime.now(_dt.timezone.utc) - t).total_seconds())
    if s < 60:
        return "%ds ago" % max(s, 0)
    if s < 3600:
        return "%dm ago" % (s // 60)
    if s < 86400:
        return "%dh ago" % (s // 3600)
    return "%dd ago" % (s // 86400)


def trunc(s, n):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: max(n - 3, 1)] + "..."


def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def file_sha(path):
    try:
        h = hashlib.sha256()
        with open(str(path), "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def read_text(path):
    try:
        with open(str(path), "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return None


def atomic_write(path, text, sync=True):
    """Write text exactly as given (no newline translation, so shell scripts keep LF on Windows)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp%d" % os.getpid())
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        if sync:
            os.fsync(f.fileno())
    for attempt in range(20):
        try:
            os.replace(str(tmp), str(path))
            return
        except PermissionError:  # Windows: a reader (hook, editor, virus scanner) has the file open
            if attempt == 19:
                raise
            time.sleep(0.1)


def chunked(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def fmt_secs(s):
    s = int(max(s or 0, 0))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


_GLOB_CACHE = {}


def glob_re(pat):
    rx = _GLOB_CACHE.get(pat)
    if rx is None:
        out, i = "", 0
        while i < len(pat):
            if pat.startswith("**/", i):
                out += "(?:.*/)?"
                i += 3
            elif pat.startswith("**", i):
                out += ".*"
                i += 2
            elif pat[i] == "*":
                out += "[^/]*"
                i += 1
            elif pat[i] == "?":
                out += "[^/]"
                i += 1
            else:
                out += re.escape(pat[i])
                i += 1
        rx = _GLOB_CACHE[pat] = re.compile(out + r"\Z")
    return rx


_ANY_CACHE = {}


def any_re(pats):
    """One compiled regex for a list of globs, exact paths and directory prefixes (see match_any)."""
    key = tuple(pats or ())
    rx = _ANY_CACHE.get(key)
    if rx is None:
        parts = []
        for p in key:
            p = p.strip()
            if p.startswith("./"):
                p = p[2:]
            if not p:
                continue
            if "*" in p or "?" in p:
                parts.append(glob_re(p).pattern[:-2])  # without its \Z
            else:
                parts.append(re.escape(p.rstrip("/")) + "(?:/.*)?")
        rx = _ANY_CACHE[key] = re.compile("(?:%s)\\Z" % "|".join(parts)) if parts else None
    return rx


def match_any(path, pats):
    """True if repo-relative posix `path` matches any glob, exact path, or directory prefix."""
    rx = any_re(pats)
    return bool(rx is not None and rx.match(path))


def norm_rel(root, p):
    """Repo-relative posix path for user input `p` (relative to cwd or repo root)."""
    p = p.strip()
    cand = Path(p)
    if not cand.is_absolute():
        c1 = (Path.cwd() / cand).resolve()
        c2 = (root / cand).resolve()
        cand = c1 if c1.exists() else c2
    try:
        return cand.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        refuse("Path is outside the repository: %s" % p)


# --------------------------------------------------------------------------- git

def git(root, args, env=None, check=True, input=None, timeout=None):
    try:
        # No stdin unless given: git must never wait for the agent's terminal (a credential or pinentry prompt).
        kw = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
        p = subprocess.run(["git"] + list(args), cwd=str(root), env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, encoding="utf-8", errors="replace", timeout=timeout, **kw)
    except FileNotFoundError:
        raise Refuse("git is not installed or not on PATH.")
    if check and p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        raise Refuse("git %s failed: %s" % (" ".join(list(args)[:3]), msg[-800:]))
    return p


SCI_GIT_ENV = {"GIT_AUTHOR_NAME": "sci", "GIT_AUTHOR_EMAIL": "sci@localhost",
               "GIT_COMMITTER_NAME": "sci", "GIT_COMMITTER_EMAIL": "sci@localhost"}


class Repo:
    def __init__(self, root):
        self.root = Path(root)
        self.dir = self.root / SD
        self._gitdir = None

    @property
    def gitdir(self):
        if self._gitdir is None:
            g = Path(git(self.root, ["rev-parse", "--git-dir"]).stdout.strip())
            self._gitdir = g if g.is_absolute() else (self.root / g).resolve()
        return self._gitdir

    def git_path(self, name):
        g = Path(git(self.root, ["rev-parse", "--git-path", name]).stdout.strip())
        return g if g.is_absolute() else (self.root / g)

    @property
    def backup_dir(self):
        return self.gitdir / "sci-backup"

    def rel(self, path):
        try:
            return Path(path).resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(path)


def head_sha(repo):
    return git(repo.root, ["rev-parse", "--verify", "-q", "HEAD"], check=False).stdout.strip() or None


def snapshot(repo, cfg):
    """Tree id of the current worktree: tracked files plus small untracked files, never .sci/.

    Files matching ignore_globs are left out, and tracked ones keep their committed content, so files that other
    tools regenerate (caches, graphs) never count as changes. Uses a private index per process, so the user's
    index, branches and working tree are untouched and two sci commands can snapshot at the same time."""
    bd = repo.backup_dir
    bd.mkdir(parents=True, exist_ok=True)
    tmp = bd / ("snap.%d.index" % os.getpid())
    real = repo.git_path("index")
    try:
        if real.exists():
            # copy2 keeps the index mtime: git's racy-entry detection depends on it. A fresh mtime would make a
            # same-size rewrite within the same second as the cached stat look unchanged.
            shutil.copy2(str(real), str(tmp))
        elif tmp.exists():
            tmp.unlink()
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(tmp)
        ign = cfg.get("ignore_globs") or []
        held = ""
        if ign and tmp.exists():
            specs = []
            for g in (x.strip() for x in ign):
                g = g[2:] if g.startswith("./") else g
                if g:
                    specs.append((":(glob)" if any(c in g for c in "*?[") else ":(literal)") + g)
            ign_tracked = [p for p in git(repo.root, ["ls-files", "-z", "--"] + specs, env=env, check=False)
                           .stdout.split("\0") if p and match_any(p, ign)]
            for chunk in chunked(ign_tracked, 200):
                lit = [":(literal)" + p for p in chunk]
                if git(repo.root, ["diff", "--name-only", "-z", "--"] + lit, env=env).stdout.strip("\0"):
                    held += git(repo.root, ["ls-files", "-s", "-z", "--"] + lit, env=env).stdout
        git(repo.root, ["add", "-u"], env=env)
        if held:
            git(repo.root, ["update-index", "-z", "--index-info"], env=env, input=held)
        git(repo.root, ["rm", "-r", "-q", "--cached", "--ignore-unmatch", "--", SD], env=env)
        out = git(repo.root, ["ls-files", "--others", "--exclude-standard", "-z", "--", ".",
                              ":(exclude)" + SD], env=env).stdout
        limit = int(cfg.get("snapshot_max_kb", 512)) * 1024
        small, big = [], []
        for p in out.split("\0"):
            if not p or p.endswith("/") or match_any(p, ign):
                continue
            try:
                size = os.lstat(str(repo.root / p)).st_size
            except OSError:
                continue
            if size <= limit:
                small.append(p)
            else:
                big.append((p, size))
        for chunk in chunked(small, 200):
            git(repo.root, ["add", "--"] + [":(literal)" + p for p in chunk], env=env)
        return git(repo.root, ["write-tree"], env=env).stdout.strip(), big
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def snap_commit(repo, tree, msg):
    env = dict(os.environ)
    env.update(SCI_GIT_ENV)
    args = ["commit-tree", "--no-gpg-sign", tree]
    h = head_sha(repo)
    if h:
        args += ["-p", h]
    return git(repo.root, args + ["-m", msg], env=env).stdout.strip()


def set_ref(repo, name, tree, msg):
    sha = snap_commit(repo, tree, msg)
    git(repo.root, ["update-ref", "refs/sci/" + name, sha])
    return sha


def drop_refs(repo, prefix):
    out = git(repo.root, ["for-each-ref", "--format=%(refname)", "refs/sci/" + prefix], check=False).stdout
    for r in out.split():
        git(repo.root, ["update-ref", "-d", r], check=False)


def tree_changes(repo, a, b, paths=None):
    """[(status, path)] between two tree-ish ids; status is A, D, M or T (no rename detection)."""
    args = ["diff", "--name-status", "--no-renames", "-z", a, b, "--"] + list(paths or [])
    toks = git(repo.root, args).stdout.split("\0")
    res, i = [], 0
    while i + 1 < len(toks):
        if toks[i]:
            res.append((toks[i][0], toks[i + 1]))
        i += 2
    return res


def tree_numstat(repo, a, b):
    out = {}
    for tok in git(repo.root, ["diff", "--numstat", "--no-renames", "-z", a, b]).stdout.split("\0"):
        parts = tok.split("\t", 2)
        if len(parts) == 3:
            out[parts[2]] = (int(parts[0]) if parts[0].isdigit() else 0,
                             int(parts[1]) if parts[1].isdigit() else 0)
    return out


def tree_diff_text(repo, a, b, path):
    return git(repo.root, ["diff", "-U0", "--no-renames", "--no-color", "--no-ext-diff",
                           a, b, "--", path]).stdout


def blob_text(repo, tree, path):
    p = git(repo.root, ["cat-file", "blob", "%s:%s" % (tree, path)], check=False)
    return p.stdout if p.returncode == 0 else None


def tracked_set(repo):
    return set(p for p in git(repo.root, ["ls-files", "-z"]).stdout.split("\0") if p)


def untracked_list(repo, cfg):
    out = git(repo.root, ["ls-files", "--others", "--exclude-standard", "-z", "--", ".",
                          ":(exclude)" + SD]).stdout
    return sorted(p for p in out.split("\0")
                  if p and not p.endswith("/") and not match_any(p, cfg.get("ignore_globs")))


def dirty_tracked(repo, cfg=None):
    out = git(repo.root, ["diff", "--name-only", "-z", "HEAD", "--"], check=False).stdout
    ign = (cfg or {}).get("ignore_globs")
    return sorted(p for p in out.split("\0") if p and not p.startswith(SD + "/") and not match_any(p, ign))


# --------------------------------------------------------------------------- state

def new_state(objective):
    t = now_iso()
    return {
        "schema": SCHEMA, "version": VERSION, "objective": objective or "", "objective_history": [],
        "created": t, "updated": t, "session": 1, "active": None, "tasks": [],
        "next_task": 1, "next_hyp": 1, "next_note": 1, "hyps": [], "notes": [], "now": None,
        "goldens": {}, "protected": {}, "protected_regions": {}, "acks": {}, "review": [], "baseline": None, "runs": 0,
        "paused": False, "last_verify": None, "violations": [], "last_build_tree": None,
        "passes": {}, "durations": {}, "regions_v": 2, "auto_ignored": [],
    }


def upgrade_regions(repo, st):
    """sci 3.4.0 started a Rust test region at its test attribute; since 3.4.1 it starts at the item's first
    attribute. Guarded regions whose tests are unchanged get the new boundaries, so the upgrade flags nothing."""
    def redo(p, sha):
        text = read_text(repo.root / p)
        if text is None or sha256_text(test_region_text(p, text, legacy=True)) != sha:
            return None
        cur = test_region_text(p, text)
        if cur:
            store_protected_text(repo, cur)
        return sha256_text(cur)
    for p, info in (st.get("protected_regions") or {}).items():
        info["sha"] = redo(p, info["sha"]) or info["sha"]
    for t in st.get("tasks") or []:
        for p, h in list((t.get("locked_regions") or {}).items()):
            t["locked_regions"][p] = redo(p, h) or h
    st["regions_v"] = 2


TASK_DEFAULTS = (("scope", []), ("invariants", []), ("deps", []), ("locked", {}), ("lock_none", None), ("unlocked", []), ("locked_regions", {}),
                 ("strikes", 0), ("stop", None), ("stop_cycles", 0), ("resets", 0), ("was_locked", []),
                 ("last_check", None), ("attempts", 0),
                 ("why", ""), ("block_reason", ""), ("status", "todo"))


def upgrade_task(t):
    for k, v in TASK_DEFAULTS:
        if k not in t:
            t[k] = json.loads(json.dumps(v))
    return t


def upgrade_state(st):
    for k, v in new_state("").items():
        st.setdefault(k, v)
    for t in st["tasks"]:
        upgrade_task(t)
    return st


@contextlib.contextmanager
def file_lock(path, wait=0.0):
    """Exclusive advisory lock on a file (flock; msvcrt on Windows). Yields True if it was acquired within `wait`
    seconds, else False."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
    f = open(str(path), "a+")
    held, win_locked = fcntl is None and msvcrt is None, False
    deadline = time.time() + wait
    try:
        while not held:
            try:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    win_locked = True
                held = True
            except OSError:
                if time.time() >= deadline:
                    break
                time.sleep(0.05)
        yield held
    finally:
        if win_locked:
            with contextlib.suppress(OSError):
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


@contextlib.contextmanager
def state_lock(repo, required=True):
    """Short lock around reading and writing state.json. Long commands take it only to read the state before they
    run something and to save the result, so `note`, `hyp` and `now` work while a long verify runs."""
    repo.dir.mkdir(parents=True, exist_ok=True)
    with file_lock(repo.dir / ".lock", wait=30.0 if required else 3.0) as held:
        if not held and required:
            raise Refuse("Another sci command kept the state locked for 30 s. Retry in a moment.")
        yield held


@contextlib.contextmanager
def locked(store):
    """The state lock, with state and config re-read from disk: another command (say, a verify finishing in the
    background) may have saved since this one started. Look tasks up again inside the block."""
    with state_lock(store.repo):
        store.load()
        yield store.st, store.cfg


_RUN_LOCK_HELD = []  # non-empty while this process holds the run lock


@contextlib.contextmanager
def run_lock(repo):
    """Held for the whole of check/verify/run/baseline/golden, and by commands that change files or task status
    (done, restore, lock, unlock, task start/block). Only one of them runs at a time in a repo."""
    if _RUN_LOCK_HELD or not repo.dir.is_dir():
        yield  # not set up yet: the command itself refuses
        return
    with file_lock(repo.dir / ".run.lock", wait=2.0) as held:
        if not held:
            refuse(*busy_text(repo))
        _RUN_LOCK_HELD.append(True)
        try:
            mark_stale_jobs(repo)
            if starting_job(repo):
                refuse(*busy_text(repo))
            if JOB:
                job_update(state="running", pid=os.getpid())
            yield
        finally:
            del _RUN_LOCK_HELD[:]


class Store:
    def __init__(self, repo):
        self.repo = repo
        self.state_path = repo.dir / "state.json"
        self.config_path = repo.dir / "config.json"
        self.events_path = repo.dir / "events.jsonl"
        self.st = None
        self.cfg = None
        self.warnings = []
        self.max_run = 0  # highest run number this process used; survives a reload of the state

    def exists(self):
        return self.state_path.exists()

    def _read_json(self, path, label):
        cands = [path, path.with_name(path.name + ".bak"), self.repo.backup_dir / path.name]
        for i, c in enumerate(cands):
            try:
                with open(str(c), encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if i > 0:
                self.warnings.append("%s was missing or corrupt; recovered it from %s" % (label, c))
            return data
        return None

    def load(self):
        st = self._read_json(self.state_path, "state.json")
        if st is None:
            refuse("No sci state found in %s." % self.repo.root,
                   'Initialize from the repo root: %s <skill-dir>/scripts/sci.py init --objective "<goal>"' % PY_HINT)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(self._read_json(self.config_path, "config.json") or {})
        old_regions = "regions_v" not in st
        self.st, self.cfg = upgrade_state(st), cfg
        if old_regions:
            upgrade_regions(self.repo, self.st)
        self.st["runs"] = max(int(self.st.get("runs") or 0), self.max_run)
        return self.st, self.cfg

    def save(self, live_tree=None):
        st = self.st
        st["updated"] = now_iso()
        st["version"] = VERSION
        live_ok = still_verified(self, live_tree) if live_tree else None
        st["next"] = compute_next(st, self.cfg, live_tree, live_ok, running_job(self.repo))
        st["anchor"] = anchor_block(st)
        self.repo.dir.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            shutil.copyfile(str(self.state_path), str(self.state_path.with_name("state.json.bak")))
        state_text = json.dumps(st, indent=1)
        cfg_text = json.dumps(self.cfg, indent=1)
        atomic_write(self.state_path, state_text)
        atomic_write(self.config_path, cfg_text)
        try:
            atomic_write(self.repo.backup_dir / "state.json", state_text)
            atomic_write(self.repo.backup_dir / "config.json", cfg_text)
        except OSError:
            pass
        atomic_write(self.repo.dir / "HANDOFF.md", render_handoff(st, self.cfg))

    def log(self, ev, task=None, msg="", **data):
        rec = {"t": now_iso(), "ev": ev}
        if task:
            rec["task"] = task
        if msg:
            rec["msg"] = msg
        if data:
            rec["data"] = data
        self.repo.dir.mkdir(parents=True, exist_ok=True)
        with open(str(self.events_path), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec) + "\n")


def find_task(st, tid):
    if tid is None:
        return None
    k = str(tid).strip().upper()
    if k.isdigit():
        k = "T" + k
    for t in st["tasks"]:
        if t["id"] == k:
            return t
    return None


def need_task(st, tid):
    t = find_task(st, tid)
    if t is None:
        refuse("No task %s." % tid, "List tasks: .sci/sci task list")
    return t


def active_task(st):
    return find_task(st, st.get("active"))


def require_active(st):
    t = active_task(st)
    if t is None:
        refuse("No active task.", "Start one: .sci/sci task start")
    return t


def next_eligible(st):
    done = set(t["id"] for t in st["tasks"] if t["status"] == "done")
    for t in st["tasks"]:
        if t["status"] == "todo" and all(d in done for d in t["deps"]):
            return t
    return None


def spec_hash(t):
    return sha256_text(json.dumps({"title": t["title"], "verify": t["verify"],
                                   "inv": t["invariants"]}, sort_keys=True))


# Default test globs that match by file name. They make a file a test only if it is source code or lies in a test
# directory: test_output.json, written by a test run, is not a test.
NAME_TEST_GLOBS = frozenset(("**/test_*", "**/*_test.*", "**/*_tests.*", "**/*.test.*", "**/*.spec.*",
                             "**/*_spec.*", "**/*Test.*", "**/*Tests.*"))
TEST_DIR_RE = re.compile(r"(?:^|/)(?:tests?|testing|spec|__tests__|testdata)/")
_NON_NAME_GLOBS = {}


RS_TEST_FILE_RE = re.compile(r"(?:^|/)tests?\.rs$")  # `#[cfg(test)] mod tests;` keeps a module's tests there
SNAPSHOT_RE = re.compile(r"(?:^|/)__snapshots__/|\.snap$")  # jest/vitest and Rust insta snapshots: expected values


def is_test(path, cfg):
    pats = cfg.get("test_globs")
    if SNAPSHOT_RE.search(path) or RS_TEST_FILE_RE.search(path):
        return True
    if not match_any(path, pats):
        return False
    if SRC_EXT_RE.search(path) or TEST_DIR_RE.search(path):
        return True
    key = tuple(pats or ())
    if key not in _NON_NAME_GLOBS:
        _NON_NAME_GLOBS[key] = tuple(p for p in key if p not in NAME_TEST_GLOBS)
    return match_any(path, _NON_NAME_GLOBS[key])


def is_doc(path, cfg):
    return match_any(path, cfg.get("doc_globs")) and not is_test(path, cfg)


# Commands that may read source as text, so that a comment changes what they check. Broad on purpose: a false
# match only costs a re-verify. `cov` must not follow a letter, or unittest's `discover` would match.
SOURCE_TEXT_RE = re.compile(r"lint|clippy|flake|format|fmt|style|mypy|pyright|pytype|pyre|black|isort|yapf|bandit|vulture|"
                            r"ruff|pycodestyle|pyflakes|pydocstyle|interrogate|doctest|(?<![a-z])cov|"
                            r"(?<![\w.])(?:grep|egrep|fgrep|rg|ag|ack|awk|sed|diff|cmp|wc|cat|head|tail|git|md5sum|"
                            r"sha\d*sum)(?![\w.])", re.I)


RS_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d[\w.]*|::|->|=>|\.\.=?|[&|<>=!+\-*/%^]=?|\S")


def rust_tokens(text, lines=False):
    """Rust source as tokens without plain comments and layout. Doc comments (///, //!, /** */, /*! */) stay: cargo
    runs the examples in them as doc tests. With `lines`, each token carries its line number. None if unreadable."""
    if text is None:
        return None
    out, i, n, line = [], 0, len(text), 1

    def add(tok):
        out.append((tok, line) if lines else tok)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            if text.startswith(("///", "//!"), i) and not text.startswith("////", i):
                add(text[i:j].rstrip())
            i = j
        elif text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            if text.startswith(("/**", "/*!"), i) and not text.startswith(("/***", "/**/"), i):
                add(text[i:j])
            line += text.count("\n", i, j)
            i = j
        elif c in "rbc" and (i == 0 or not RS_IDENT.match(text[i - 1])) and RS_RAW_RE.match(text, i):
            m = RS_RAW_RE.match(text, i)
            end = text.find('"' + m.group(1), m.end())
            end = n if end < 0 else end + 1 + len(m.group(1))
            add(text[i:end])
            line += text.count("\n", i, end)
            i = end
        elif c == '"' or (c == "b" and text.startswith('b"', i)):
            j = i + (2 if c == "b" else 1)
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            add(text[i:j + 1])
            line += text.count("\n", i, j + 1)
            i = j + 1
        elif c == "'" and i + 2 < n and (text[i + 1] == "\\" or text[i + 2] == "'"):
            j = text.find("'", i + 3 if text[i + 1] == "\\" else i + 2)
            j = n if j < 0 else j
            add(text[i:j + 1])
            i = j + 1
        else:
            m = RS_TOKEN_RE.match(text, i)
            add(m.group(0))
            i = m.end()
    return _drop_trailing_commas(out)


def _drop_trailing_commas(toks):
    """Tokens without the trailing comma of a bracketed list of two or more items, which rustfmt adds when it wraps
    a list. A one-item group keeps it: `(x,)` is a tuple, `(x)` is not."""
    out, commas = [], []

    def val(t):
        return t[0] if isinstance(t, tuple) else t
    for t in toks:
        v = val(t)
        if v in ("(", "[", "{"):
            commas.append(0)
        elif v in (")", "]", "}"):
            if out and val(out[-1]) == "," and commas and commas[-1] >= 2:
                out.pop()
            if commas:
                commas.pop()
        elif v == "," and commas:
            commas[-1] += 1
        out.append(t)
    return out


def same_syntax(a, b, lines=False, path=".py"):
    """True when two versions of a source file differ only in comments or layout: Python files that parse to the
    same syntax tree (docstrings included), Rust files with the same tokens (doc comments included). With
    `lines`, everything must also keep its line number (warnings, tracebacks and panics print line numbers,
    which a golden output would compare)."""
    if path.endswith(".rs"):
        ta, tb = rust_tokens(a, lines), rust_tokens(b, lines)
        return ta is not None and ta == tb
    try:
        ta, tb = ast.parse(a), ast.parse(b)
    except (SyntaxError, ValueError, TypeError):
        return False
    if lines and [getattr(n, "lineno", 0) for n in ast.walk(ta)] != [getattr(n, "lineno", 0) for n in ast.walk(tb)]:
        return False
    return ast.dump(ta) == ast.dump(tb)


def unseen_changes(store, old, new):
    """The files that differ between snapshots `old` and `new` when no check can see the difference: documentation
    that no check reads, and comments or layout in Python files when no check reads source as text (a linter,
    grep). Locked and protected tests and file-mode changes always count; with goldens, so do moved lines.
    Otherwise None."""
    repo, st = store.repo, store.st
    changes = tree_changes(repo, old, new)
    files = tree_entries(repo, new)
    inert = inert_docs(store, new, files)
    code = [(s_, p) for s_, p in changes if p not in inert]
    if code:
        cmds = step_commands(st, store.cfg)
        guarded = set(st.get("protected") or {}) | set((active_task(st) or {}).get("locked") or {})
        before = tree_entries(repo, old)
        if any(s_ != "M" or not p.endswith((".py", ".rs")) or p in guarded or before.get(p, (0,))[0] != files[p][0]
               for s_, p in code) or any(
                SOURCE_TEXT_RE.search(c) or any(w.rsplit("/", 1)[-1].lower() in DOC_TOOLS for w in verify_tokens(c))
                for c in cmds):
            return None
        keep_lines = bool(st.get("goldens"))
        if not all(same_syntax(blob_text(repo, old, p), blob_text(repo, new, p), keep_lines, p) for _, p in code):
            return None
    return [p for _, p in changes]


def verified_changes_since(store, t, cur_tree):
    """None if the task is not verified on these files. Otherwise the files changed since its verify that no check
    can see (empty if nothing changed)."""
    vt = t.get("verified_tree")
    if not vt:
        return None
    if vt == cur_tree:
        return []
    if t.get("verified_fp") and code_fp(store, cur_tree) == t["verified_fp"]:
        return [p for _, p in tree_changes(store.repo, vt, cur_tree)]
    return unseen_changes(store, vt, cur_tree)


def still_verified(store, live_tree):
    """Whether the last verify still holds on snapshot `live_tree`: the active task's verify, or with no active task
    the final one."""
    t = active_task(store.st)
    if t:
        return verified_changes_since(store, t, live_tree) is not None
    lv = store.st.get("last_verify") or {}
    if not lv.get("tree"):
        return False
    if lv["tree"] == live_tree or (lv.get("fp") and code_fp(store, live_tree) == lv["fp"]):
        return True
    return unseen_changes(store, lv["tree"], live_tree) is not None


def is_locked(t):
    """Acceptance is frozen: files or the tests inside source files are locked, or `lock` established that there
    is nothing to lock."""
    return bool(t.get("locked")) or bool(t.get("locked_regions")) or bool(t.get("lock_none"))


def lock_desc(t):
    if t.get("locked") or t.get("locked_regions"):
        return ", ".join(sorted(t.get("locked") or {}) +
                         ["%s (its tests only)" % p for p in sorted(t.get("locked_regions") or {})])
    if t.get("lock_none"):
        return "(nothing to lock: %s)" % trunc(t["lock_none"], 90)
    return "(nothing yet)"


def add_review(st, task, kind, msg):
    st["review"].append({"t": now_iso(), "task": task, "kind": kind, "msg": msg})


# --------------------------------------------------------------------------- running commands

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

def _dotted(name, where):
    return where if where.endswith("." + name) else where + "." + name


# (pattern, match -> test id; None takes group 1): the failing-test lines of the runners sci knows.
FAIL_RES = [
    (re.compile(r"^(?:FAILED|ERROR)\s+(?!\()(\S+?)(?:\s+-\s.*)?\s*$"), None),  # pytest short summary
    (re.compile(r"^(\S+::\S+?)\s+(?:FAILED|ERROR)\b"), None),                 # pytest -v
    (re.compile(r"^(?:FAIL|ERROR):\s+(\w+)\s+\(([\w.]+)\)"), lambda m: _dotted(m.group(1), m.group(2))),  # unittest
    (re.compile(r"^\s*--- FAIL:\s+(\S+)"), None),                             # go test
    (re.compile(r"^test\s+(.+?)\s+\.\.\.\s+FAILED\s*$"), None),               # cargo test (doc tests have spaces)
    (re.compile(r"^(\S+) --- FAILED\s*$"), None),                             # cargo test -q
    (re.compile(r"^---- (.+?) stdout ----\s*$"), None),                       # cargo test failure report
    (re.compile(r"^\s*FAIL \[\s*[\d.]+m?s\]\s+(?:\(\s*\d+/\d+\)\s+)?\S+\s+(\S+)"), None),  # cargo nextest
    (re.compile(r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(\S+)\s+\.*\**\s*(?:Failed|Exception|Timeout|SEGFAULT)"), None),
    (re.compile(r"^\s*\d+\s+-\s+(\S+)\s+\((?:Failed|Timeout|SEGFAULT|Subprocess aborted|Exception|Not Run)\)"), None),
    (re.compile(r"^\s*FAIL\s+(\S+\.[cm]?[jt]sx?(?:\s+>\s+[^\[(]*?)?)\s*(?:\[.*\])?\s*(?:\([\d.]+\s*m?s\))?\s*$"),
     lambda m: " ".join(m.group(1).split())),                                     # jest (file), vitest (test)
    (re.compile(r"^\s*not ok \d+ - (.+?)\s*$"), None),                         # TAP: node --test, tape, bats
    (re.compile(r"^\[ERROR\]\s+(?:(\w+)\(([\w$.]+)\)|([\w$.]+))\s+(?:--\s+)?Time elapsed:.*<<<\s*(?:FAILURE|ERROR)!"),
     lambda m: m.group(3) or _dotted(m.group(1), m.group(2))),                     # maven surefire
    (re.compile(r"^\s*([\w$]+(?:\.[\w$]+)+|[\w$]*(?:Test|Spec|IT)\w*)\s+>\s+(\w+)(?:\(\))?\s+FAILED\s*$"),
     lambda m: m.group(1) + "." + m.group(2)),                                     # gradle
    (re.compile(r"^\s*Failed\s+([\w`<>+]+(?:\.[\w`<>+]+)+)(?:\(.*\))?\s+\[[\d.]+\s*m?s\]"), None),  # dotnet test
    (re.compile(r"^rspec\s+(\./\S+?:\d+)\s+#"), None),                            # rspec
    (re.compile(r"^\d+\)\s+([\w\\]*Test\w*::\w+)"), None),                       # phpunit
]

SUMMARY_RES = [
    re.compile(r"^=+ (.*\b(?:passed|failed|errors?|skipped|no tests ran|deselected|xfailed)\b.*?) =+$"),
    re.compile(r"^(Ran \d+ tests? in [\d.]+s)$"),
    re.compile(r"^((?:OK|FAILED)(?: \(.*\))?)$"),
    re.compile(r"^(test result: .*)$"),
    re.compile(r"^(\d+% tests passed, \d+ tests failed out of \d+)"),
    re.compile(r"^(Tests?:\s+.*\b(?:passed|failed)\b.*)$"),
    re.compile(r"^(Tests\s{2,}.*\b(?:passed|failed)\b.*)$"),
    re.compile(r"^(?:\[\w+\]\s+)?(Tests run: \d+, Failures: \d+, Errors: \d+, Skipped: \d+)\s*$"),
    re.compile(r"^(\d+ tests? completed, \d+ failed.*)$"),
    re.compile(r"^((?:Passed|Failed)!\s+-\s+Failed:.*)$"),
    re.compile(r"^(\d+ examples?, \d+ failures?.*)$"),
    re.compile(r"^(Tests: \d+, Assertions: \d+.*)$"),
    re.compile(r"^(Summary \[.*\b(?:passed|failed)\b.*)$"),                    # cargo nextest
    re.compile(r"^ℹ (fail \d+)$"),                                            # node --test
    re.compile(r"^# (fail \d+)$"),                                                 # TAP (node --test, tape)
]

FAILCOUNT_RE = re.compile(r"(\d+)\s+(?:tests?\s+)?(?:failed|failures?|errors?)\b|"
                          r"\b(?:failures?|errors?|failed)\s*[=:]\s*(\d+)|^fail\s+(\d+)$", re.I)


def failure_count(lines):
    """Failed tests counted from every summary line of a run (each cargo test target prints one), or None if the
    run printed no summary with a count. Only compared between runs of the same command."""
    n, seen = 0, False
    for ln in lines:
        s = ln.strip()
        for rx in SUMMARY_RES:
            m = rx.match(s)
            if m:
                for g in FAILCOUNT_RE.finditer(m.group(1)):
                    n += int(next(x for x in g.groups() if x is not None))
                    seen = True
                break
    return n if seen else None

KEY_RE = re.compile(r"^E\s{2,}\S|\b\w*(?:Error|Exception)\b|Traceback \(most recent|panicked at|"
                    r"Segmentation fault|core dumped|\bKilled\b|\bAborted\b|FloatingPointError|"
                    r"RuntimeWarning|not close|Mismatch")


class Run(object):
    def __init__(self, n, cmd, rc, secs, log, timed_out, lines):
        self.n, self.cmd, self.rc, self.secs, self.log, self.timed_out = n, cmd, rc, secs, log, timed_out
        self.ok = (rc == 0 and not timed_out)
        self.lines = lines
        self.failing = extract_failures(lines)
        self.summary = extract_summary(lines)


def read_log_lines(path, max_bytes=40 * 1024 * 1024):
    try:
        size = os.path.getsize(str(path))
        with open(str(path), "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                data = f.read().split(b"\n", 1)[-1]
            else:
                data = f.read()
    except OSError:
        return []
    text = ANSI_RE.sub("", data.decode("utf-8", "replace")).replace("\r\n", "\n")
    return [ln.rsplit("\r", 1)[-1] for ln in text.split("\n")]


JEST_FILE_RE = re.compile(r"^\s*(FAIL|PASS)\s+(\S+\.[cm]?[jt]sx?)\s*(?:\([\d.]+\s*m?s\))?\s*$")  # jest adds (5.1 s)
JEST_TEST_RE = re.compile(r"^\s+● (.+?)\s*$")                       # "  ● describe › test"
NODE_AT_RE = re.compile(r"^test at (.+?):\d+:\d+\s*$")                   # node --test, spec reporter
NODE_FAIL_RE = re.compile(r"^✖ (.+?)(?: \([\d.]+m?s\))?\s*$")       # "✖ name (1.2ms)"
TAP_DIRECTIVE_RE = re.compile(r"#\s*(?:SKIP|TODO)\b", re.I)


def extract_failures(lines):
    """Ids of the failing tests in a runner's output. Jest and `node --test` name a failing test on a line after its
    file, so their ids are `file › test` and `file > test`; a file alone (a suite that did not run) is an id too."""
    seen, out = set(), []
    jest_file = node_file = None

    def add(fid):
        if fid and fid not in seen:
            seen.add(fid)
            out.append(fid)
    for ln in lines:
        m = JEST_FILE_RE.match(ln)
        if m:
            jest_file = m.group(2) if m.group(1) == "FAIL" else None
        elif jest_file and JEST_TEST_RE.match(ln) and not ln.strip().startswith("● Console"):
            add("%s › %s" % (jest_file, JEST_TEST_RE.match(ln).group(1)))
            continue
        m = NODE_AT_RE.match(ln)
        if m:
            node_file = m.group(1)
            continue
        if node_file:
            m, f, node_file = NODE_FAIL_RE.match(ln), node_file, None
            if m:
                add("%s > %s" % (f, m.group(1)))
                continue
        for rx, to_id in FAIL_RES:
            m = rx.match(ln)
            if not m:
                continue
            if rx.pattern.startswith(r"^\s*not ok") and TAP_DIRECTIVE_RE.search(ln):
                break  # an expected failure or a skipped test
            add(to_id(m) if to_id else m.group(1))
            break
    return out


def extract_summary(lines):
    found = []
    for ln in lines:
        s = ln.strip()
        for rx in SUMMARY_RES:
            m = rx.match(s)
            if m:
                found.append(m.group(1))
                break
    return found[-2:]


@functools.lru_cache(maxsize=None)
def windows_bash():
    """Git for Windows' bash.exe, or None. Never WSL's System32\\bash.exe, which would run the command in Linux."""
    cands = []
    g = shutil.which("git")
    if g:
        root = Path(g).resolve().parent.parent  # <Git>\cmd\git.exe or <Git>\mingw64\bin\git.exe
        for base in (root, root.parent):
            cands += [base / "bin" / "bash.exe", base / "usr" / "bin" / "bash.exe"]
    w = shutil.which("bash")
    if w and not re.search(r"[\\/](?:system32|windowsapps)[\\/]", w, re.I):
        cands.append(Path(w))
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        if os.environ.get(var):
            base = Path(os.environ[var])
            cands += [base / "Git" / "bin" / "bash.exe", base / "Programs" / "Git" / "bin" / "bash.exe"]
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def shell_argv(cfg, cmd):
    """(args, use_shell) for subprocess to run a command string. POSIX: bash -c (else sh). Windows: Git Bash -c,
    else cmd.exe. The `shell` config key overrides: a POSIX shell path, "cmd", "powershell" or "pwsh"."""
    want = ((cfg or {}).get("shell") or "").strip()
    kind = os.path.basename(want).lower()
    kind = kind[:-4] if kind.endswith(".exe") else kind
    if kind == "cmd" or (not want and WINDOWS and not windows_bash()):
        return cmd, True  # Python's own cmd.exe quoting (/c "...") is the one cmd understands
    if kind in ("powershell", "pwsh"):
        enc = base64.b64encode(cmd.encode("utf-16-le")).decode("ascii")
        return [want, "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], False
    if want:
        return [want, "-c", cmd], False
    if WINDOWS:
        return [windows_bash(), "-c", cmd], False
    return [shutil.which("bash") or "/bin/sh", "-c", cmd], False


def shell_label(cfg):
    args, use_shell = shell_argv(cfg, "")
    return (os.environ.get("COMSPEC") or "cmd.exe") if use_shell else args[0]


def uses_bash(cfg):
    args, use_shell = shell_argv(cfg, "")
    return not use_shell and os.path.basename(args[0]).lower() in ("bash", "bash.exe", "sh", "sh.exe")


def python_argv():
    """How a shell here calls Python 3.8+: python3 on POSIX; py -3, python or python3 on Windows. Each candidate
    is run once, because the Microsoft Store stubs named python/python3 only print an install hint."""
    cands = [["py", "-3"], ["python"], ["python3"]] if WINDOWS else [["python3"], ["python"]]
    for c in cands:
        if not shutil.which(c[0]):
            continue
        try:
            p = subprocess.run(c + ["-c", "import sys; sys.exit(sys.version_info < (3, 8))"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p.returncode == 0:
            return c
    return None


PY_HINT = ("py -3" if shutil.which("py") else "python") if WINDOWS else "python3"


def kill_tree(p):
    try:
        if os.name == "posix":
            os.killpg(p.pid, signal.SIGTERM)
        else:
            # terminate() would end only the shell; its children would keep running and holding the log open
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(p.pid, signal.SIGKILL)
            else:
                p.kill()
        except OSError:
            pass
        p.wait()


def next_run_number(store):
    """Run numbers come from the state and the saved logs, so a runner never needs the state lock to number its
    logs (only one runner works at a time: it holds the run lock)."""
    top = max(int(store.st.get("runs") or 0), store.max_run)
    with contextlib.suppress(OSError):
        for p in (store.repo.dir / "runs").glob("*.log"):
            head = p.name.split("-", 1)[0]
            if head.isdigit():
                top = max(top, int(head))
    store.max_run = top + 1
    store.st["runs"] = top + 1
    return top + 1


def run_cmd(store, cmd, kind, task_id=None, timeout=900):
    """Run a shell command from the repo root; full output goes to .sci/runs/, never to the screen."""
    cfg, repo = store.cfg, store.repo
    n = next_run_number(store)
    runs = repo.dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    log = runs / ("%04d-%s%s.log" % (n, kind, ("-" + task_id) if task_id else ""))
    env = dict(os.environ)
    for k in ("FORCE_COLOR", "CLICOLOR_FORCE"):
        env.pop(k, None)
    env.update({"NO_COLOR": "1", "PY_COLORS": "0", "TERM": "dumb", "CLICOLOR": "0",
                "CARGO_TERM_COLOR": "never", "GTEST_COLOR": "no", "SCI_HARNESS": "1",
                "PYTHONUNBUFFERED": "1"})
    args, use_shell = shell_argv(cfg, cmd)
    t0 = time.time()
    timed_out = False
    with open(str(log), "wb") as f:
        f.write(("$ %s\n" % cmd).encode("utf-8"))
        f.flush()
        try:
            p = subprocess.Popen(args, shell=use_shell, cwd=str(repo.root), stdout=f, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, env=env, start_new_session=(os.name == "posix"))
        except OSError as e:
            f.write(("sci: could not start command: %s\n" % e).encode("utf-8"))
            p = None
        rc = 127
        if p is not None:
            job_update(child=p.pid)
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_tree(p)
                rc = 124
                f.write(("\nsci: TIMEOUT after %ss; process group killed\n" % timeout).encode("utf-8"))
            except KeyboardInterrupt:
                kill_tree(p)
                raise
            finally:
                job_update(child=None)
    secs = time.time() - t0
    keep = int(cfg.get("keep_runs") or 80)
    old = sorted(runs.glob("*.log"))
    for stale in old[:-keep] if len(old) > keep else []:
        with contextlib.suppress(OSError):
            stale.unlink()
    return Run(n, cmd, rc, secs, log, timed_out, read_log_lines(log))


def report_run(repo, cfg, r, label, quiet_pass=False):
    head = "%s %s: exit %d in %.1fs | log %s" % ("PASS" if r.ok else "FAIL", label, r.rc, r.secs, repo.rel(r.log))
    if r.timed_out:
        head += " | TIMED OUT (killed). Use a narrower command or raise check_timeout/verify_timeout."
    out = [head, "  $ " + trunc(r.cmd, 220)]
    if r.summary:
        out.append("  summary: " + " | ".join(trunc(s, 120) for s in r.summary))
    if r.ok and quiet_pass:
        return out
    if r.failing:
        more = " ..." if len(r.failing) > 12 else ""
        out.append("  failing (%d): %s%s" % (len(r.failing), ", ".join(r.failing[:12]), more))
    prefix = str(repo.root.resolve()) + os.sep
    body = [ln.replace(prefix, "") for ln in r.lines[1:] if ln.strip()]
    n = int(cfg.get("tail_lines") or 25)
    if not r.ok:
        if len(body) <= n + 15:
            out.append("  --- output (%d lines) ---" % len(body))
            out += ["  | " + trunc(ln, 220) for ln in body]
        else:
            key, seen = [], set()
            for ln in body[:-n]:
                if KEY_RE.search(ln) and ln not in seen:
                    seen.add(ln)
                    key.append("  | " + trunc(ln, 220))
                    if len(key) >= 15:
                        break
            if key:
                out.append("  --- key lines (of %d) ---" % len(body))
                out += key
            out.append("  --- last %d lines ---" % n)
            out += ["  | " + trunc(ln, 220) for ln in body[-n:]]
        out.append("  (full log: .sci/sci out %d --grep <pattern>)" % r.n)
        if WINDOWS and "\\" in r.cmd and uses_bash(cfg):
            out.append("  note: sci runs commands with bash (%s), where \\ is an escape character. Write paths "
                       "with forward slashes: tests/test_x.py" % shell_label(cfg))
    else:
        out += ["  | " + trunc(ln, 220) for ln in body[-3:]]
    return out


# --------------------------------------------------------------------------- test-weakening guard

SKIP_RE = re.compile(
    r"@pytest\.mark\.(?:skip|skipif|xfail)\b|\bpytest\.(?:skip|xfail|importorskip)\s*\(|"
    r"@unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b|\bself\.skipTest\s*\(|"
    r"\braise\s+(?:unittest\.)?SkipTest\b|\b(?:it|describe|test|suite|bench)\.(?:skip|todo|skipIf|fails)\b|"
    r"\bx(?:it|describe|test)\s*\(|#\[ignore\b|\bt\.Skip(?:Now|f)?\s*\(|@test_skip\b|"
    r"@test_broken\b|@Disabled\b|@Ignore\b|\bGTEST_SKIP\b|\bDISABLED_\w+|\bSkip\s*=|\[Ignore\b|"
    r"markTest(?:Skipped|Incomplete)\s*\(|\b(?:it|describe|test|context)\.only\s*\(|\bf(?:it|describe)\s*\(")
ASSERT_RE = re.compile(r"\b[aA]ssert\w*|\bexpect\s*\(|\bASSERT_\w+|\bEXPECT_\w+|@test\b|\bisapprox\b|"
                       r"\bcheck_close\b|\ballclose\b|\bisclose\b|\bapprox\s*\(|\bshould\b|"
                       r"\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(|\bprop_assert\w*")
TRIVIAL_RE = re.compile(r"^\s*(?:assert\s+(?:True|1)\s*(?:#.*)?$|self\.assert(?:True|_)\(\s*(?:True|1)\s*\)|"
                        r"expect\(\s*true\s*\)|@test\s+true\b|assert!\(\s*true\s*\))")
DEAD_RE = re.compile(r"^\s*(?:\}\s*else\s+)?if\s*(?:\(\s*(?:false|0)\s*\)|false\b)")  # Rust, Go, JS, C: `if false {`
TOL_KEYS = (r"rtol|atol|rel_tol|abs_tol|rel|abs|tol|tolerance|eps|epsilon|delta|places|decimal|significant|ulps?|"
            r"maxulp|max_relative|max_ulps|max_abs_diff")
TOL_RE = re.compile(r"\b(%s)\s*[=:]\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][-+]?\d+)?)" % TOL_KEYS, re.I)
TOL_KEY_RE = re.compile(r"\b(%s)\s*[=:](?!=)" % TOL_KEYS, re.I)
CLOSE_TO_RE = re.compile(r"\b(toBeCloseTo)\(\s*[^,()]+,\s*(\d+)\s*\)")  # jest/vitest: the number of digits
DIGIT_TOLS = ("places", "decimal", "significant", "tobecloseto")
NUM_RE = re.compile(r"(?<![\w.])[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eEdD][-+]?\d+)?(?![\w.])")


def _num(s):
    try:
        return float(s.replace("d", "e").replace("D", "e"))
    except ValueError:
        return None


def parse_hunks(diff_text):
    hunks, cur = [], None
    for ln in diff_text.splitlines():
        if ln.startswith("diff --git"):
            cur = None
        elif ln.startswith("@@"):
            cur = {"rem": [], "add": []}
            hunks.append(cur)
        elif cur is not None:
            if ln.startswith("-"):
                cur["rem"].append(ln[1:])
            elif ln.startswith("+"):
                cur["add"].append(ln[1:])
    return hunks


def tol_values(lines):
    vals = {}
    for ln in lines:
        for m in list(TOL_RE.finditer(ln)) + list(CLOSE_TO_RE.finditer(ln)):
            v = _num(m.group(2))
            if v is not None:
                vals.setdefault(m.group(1).lower(), []).append(v)
    return vals


def _strip_tols(ln):
    ln = CLOSE_TO_RE.sub(lambda m: m.group(0)[:m.start(2) - m.start(0)] + "#)", ln)
    return " ".join(TOL_RE.sub(lambda m: m.group(1).lower() + "=#", ln).split())


CMP_RE = re.compile(r"<=|>=|==|!=|<|>")


def _skeleton(ln):
    """The line with numbers and comparison operators blanked: `err < 1e-6` and `err <= 1e-1` look the same."""
    return " ".join(CMP_RE.sub("<op>", NUM_RE.sub("#", ln)).split())


def pair_lines(rem, add):
    """Pair each removed line with the most similar added line of the same hunk (not by position, so an inserted
    comment does not shift the pairs)."""
    if len(rem) * len(add) > 2500:
        return list(zip(rem, add))
    cands = sorted(((difflib.SequenceMatcher(None, a, b).ratio(), i, j) for i, a in enumerate(rem)
                    for j, b in enumerate(add)), reverse=True)
    used_r, used_a, pairs = set(), set(), []
    for ratio, i, j in cands:
        if ratio < 0.6:
            break
        if i not in used_r and j not in used_a:
            used_r.add(i)
            used_a.add(j)
            pairs.append((i, rem[i], add[j]))
    return [(a, b) for _, a, b in sorted(pairs)]


def _looser(k, o, n):
    return min(n) < min(o) if k in DIGIT_TOLS else max(n) > max(o)


def _swallows_failures(node):
    """A try statement whose handler catches assertion failures without re-raising."""
    if not isinstance(node, ast.Try) or not any(isinstance(x, ast.Assert) or (
            isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr.startswith("assert"))
            for b in node.body for x in ast.walk(b)):
        return False
    def fails(body):  # raise, self.fail(...) or pytest.fail(...)
        return any(isinstance(x, ast.Raise) or (isinstance(x, ast.Call) and getattr(x.func, "attr",
                   getattr(x.func, "id", "")) == "fail") for b in body for x in ast.walk(b))
    if fails(node.orelse):
        return False  # "this must fail" pattern
    for h in node.handlers:
        names = [h.type] if not isinstance(h.type, ast.Tuple) else list(h.type.elts)
        catches = h.type is None or any(getattr(x, "id", getattr(x, "attr", "")) in
                                        ("AssertionError", "Exception", "BaseException") for x in names)
        if catches and not fails(h.body):
            return True
    return False


def _const_truth(node):
    """True/False for a constant condition (`True`, `0`, `not True`), else None."""
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        v = _const_truth(node.operand)
        return None if v is None else not v
    return None


def _cond_text(node):
    if isinstance(node, ast.UnaryOp):
        return "not " + _cond_text(node.operand)
    return repr(getattr(node, "value", "?"))


def py_test_issues(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    issues = set()

    def target_name(t):
        if isinstance(t, ast.Name):
            return t.id
        if isinstance(t, ast.Attribute):
            return "%s.%s" % (target_name(t.value) or "?", t.attr)
        return None

    def scan(body, where):
        seen, classes = set(), set()
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                if node.name in seen:
                    issues.add("%s is defined twice in %s (the later definition silently replaces the earlier test)"
                               % (node.name, where))
                seen.add(node.name)
                for stmt in node.body[:-1]:
                    if isinstance(stmt, ast.Return):
                        issues.add("early `return` in %s turns the rest of the test into dead code" % node.name)
                        break
                for sub in ast.walk(node):
                    if isinstance(sub, ast.If) and _const_truth(sub.test) is False:
                        issues.add("`if %s:` block in %s never runs" % (_cond_text(sub.test), node.name))
                    elif (isinstance(sub, ast.If) and _const_truth(sub.test) is True
                          and any(isinstance(x, ast.Return) for x in sub.body)):
                        issues.add("early `return` in %s turns the rest of the test into dead code" % node.name)
                    elif _swallows_failures(sub):
                        issues.add("try/except in %s catches assertion failures without re-raising" % node.name)
            elif isinstance(node, ast.ClassDef):
                if node.name in classes:
                    issues.add("class %s is defined twice in %s (the later definition silently replaces the earlier "
                               "tests)" % (node.name, where))
                classes.add(node.name)
                scan(node.body, "class " + node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.Delete)):
                targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
                for t in targets:
                    name = target_name(t)
                    last = (name or "").rsplit(".", 1)[-1]
                    if name and (last.startswith("test") and ("." in name or last in seen)
                                 or (isinstance(node, ast.Delete) and name in classes)):
                        issues.add("%s is %s in %s (the test no longer runs as written)"
                                   % (name, "deleted" if isinstance(node, ast.Delete) else "replaced by an assignment",
                                      where))

    scan(tree.body, "the module")
    return issues


# --------------------------------------------------------------------------- tests inside source files (Rust)
#
# Rust unit tests live in the file they test: `#[cfg(test)] mod tests { ... }` and `#[test]` functions. Such a file
# is code the task changes, so it cannot be locked whole; its test regions are locked, protected and checked for
# weakening instead, while the code around them stays editable.

RS_IDENT = re.compile(r"[A-Za-z0-9_]")
RS_RAW_RE = re.compile(r'[bc]?r(#*)"')
RS_CFG_TEST_RE = re.compile(r"#\[\s*cfg\s*\((?P<arg>[^\]]*)\)\s*\]")
RS_TEST_ATTR_RE = re.compile(r"#\[\s*(?P<path>(?:\w+\s*::\s*)*\w+)")
RS_TEST_ATTRS = ("test", "rstest", "quickcheck", "wasm_bindgen_test", "test_case", "proptest", "bench")
RS_PROPTEST_RE = re.compile(r"\bproptest\s*!\s*\{")
REGION_SEP = "// ---- sci: next test region ----"


def rust_mask(text):
    """`text` with comments, string and char literals blanked (newlines kept), so braces and attributes can be
    found with plain searches."""
    out, i, n = list(text), 0, len(text)

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "
    while i < n:
        c = text[i]
        if c == "/" and text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif c == "/" and text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif c in "rbc" and (i == 0 or not RS_IDENT.match(text[i - 1])) and RS_RAW_RE.match(text, i):
            m = RS_RAW_RE.match(text, i)
            end = text.find('"' + m.group(1), m.end())
            end = n if end < 0 else end
            blank(m.end(), end)
            i = end + 1 + len(m.group(1))
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            blank(i + 1, j)
            i = j + 1
        elif c == "'":
            if i + 1 < n and text[i + 1] == "\\":
                j = text.find("'", i + 3)  # past the escaped character: '\'' and '\\' end at i + 3
                j = n if j < 0 else j
                blank(i + 1, j)
                i = j + 1
            elif i + 2 < n and text[i + 2] == "'":
                blank(i + 1, i + 2)
                i += 3
            else:
                i += 1  # a lifetime
        else:
            i += 1
    return "".join(out)


def _rs_item_end(m, start):
    """End offset of the item that starts at `start` in masked text `m`: its matching `}` or its `;`."""
    depth, i, n = 0, start, len(m)
    while i < n:
        c = m[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == ";" and depth <= 0:
            return i + 1
        elif c == "{" and depth <= 0:
            d = 0
            while i < n:
                if m[i] == "{":
                    d += 1
                elif m[i] == "}":
                    d -= 1
                    if d == 0:
                        return i + 1
                i += 1
            return n
        i += 1
    return n


def _rs_skip_attrs(m, i):
    """Offset of the item after the attributes at `i` (each `#[...]`, brackets balanced)."""
    n = len(m)
    while True:
        while i < n and m[i].isspace():
            i += 1
        if not m.startswith("#[", i):
            return i
        depth = 0
        while i < n:
            if m[i] == "[":
                depth += 1
            elif m[i] == "]":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            i += 1


def _rs_attrs_start(m, i):
    """Offset of the first outer attribute of the item whose attribute at `i` is one of them. Attributes may come in
    any order, so `#[cfg(feature = "x")]` or `#[ignore]` above `#[cfg(test)]` or `#[test]` belongs to the tests."""
    while True:
        j = i - 1
        while j >= 0 and m[j].isspace():
            j -= 1
        if j < 0 or m[j] != "]":
            return i
        depth, k = 0, j
        while k >= 0:
            if m[k] == "]":
                depth += 1
            elif m[k] == "[":
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        if k <= 0 or m[k - 1] != "#":
            return i
        i = k - 1


def rust_test_spans(text, legacy=False):
    """[(start, end)] character spans of the test code in Rust source: items under #[cfg(test)], functions with a
    test attribute (#[test], #[tokio::test], #[rstest], #[test_case(..)] ...) and proptest! blocks. A span starts at
    the item's first attribute (`legacy`: at the test attribute, as sci 3.4.0 did)."""
    if "test" not in text:
        return []
    m = rust_mask(text)
    spans = []
    first = (lambda i: i) if legacy else (lambda i: _rs_attrs_start(m, i))
    for a in RS_CFG_TEST_RE.finditer(m):
        arg = a.group("arg")
        if re.search(r"\btest\b", arg) and not re.search(r"\bnot\s*\(\s*test\b", arg):
            spans.append((first(a.start()), _rs_item_end(m, _rs_skip_attrs(m, a.start()))))
    for a in RS_TEST_ATTR_RE.finditer(m):
        last = re.split(r"\s*::\s*", a.group("path"))[-1]
        if last in RS_TEST_ATTRS or last.startswith("test_"):
            spans.append((first(a.start()), _rs_item_end(m, _rs_skip_attrs(m, a.start()))))
    for a in RS_PROPTEST_RE.finditer(m):
        spans.append((a.start(), _rs_item_end(m, a.start())))
    spans.sort()
    merged = []
    for s_, e in spans:
        if merged and s_ <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s_, e))
    return merged


RS_ITEM_HEAD_RE = re.compile(r"\s*(?:pub(?:\s*\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
                             r"(?:extern\s+\"[^\"]*\"\s+)?(fn|mod)\s+([A-Za-z_]\w*)")
RS_OFF_ATTR_RE = re.compile(r"^#\[\s*(?:cfg_attr\b|cfg\s*\((?!\s*test\s*\)\s*\])|should_panic\b)")


def _rs_attr_end(m, i):
    """End offset of the attribute `#[...]` at `i` in masked text (brackets balanced)."""
    depth, n = 0, len(m)
    while i < n:
        if m[i] == "[":
            depth += 1
        elif m[i] == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def rust_item_attrs(text):
    """{"fn name" | "mod name" (#2 for a repeated name): set of its outer attributes} for the items of Rust code."""
    m = rust_mask(text)
    found, seen, i, n = {}, {}, 0, len(m)
    while True:
        i = m.find("#[", i)
        if i < 0:
            return found
        attrs, j = set(), i
        while m.startswith("#[", j):
            e = _rs_attr_end(m, j)
            attrs.add(" ".join(text[j:e].split()))
            j = e
            while j < n and m[j].isspace():
                j += 1
        head = RS_ITEM_HEAD_RE.match(m, j)
        if head:
            key = "%s %s" % head.groups()
            seen[key] = seen.get(key, 0) + 1
            found.setdefault(key if seen[key] == 1 else "%s #%d" % (key, seen[key]), attrs)
        i = max(j, i + 2)


def rust_attr_issues(old_text, new_text):
    """An existing Rust test or test module that gained an attribute that can switch it off or invert it:
    #[cfg(feature = "x")] (or any cfg but `test`), #[cfg_attr(...)], #[should_panic]. New tests may carry them."""
    old, new = rust_item_attrs(old_text), rust_item_attrs(new_text)
    issues = []
    for key in sorted(set(old) & set(new)):
        for a in sorted(new[key] - old[key]):
            if RS_OFF_ATTR_RE.match(a):
                issues.append("%s added to existing %s (it may no longer run, or pass when it fails)" % (a, key))
    return issues


def has_inline_tests(path):
    """Whether files like `path` can hold tests inside source code that sci finds (Rust)."""
    return path.endswith(".rs")


def test_region_text(path, text, legacy=False):
    """The test code inside source file `path` (whole lines), regions separated by REGION_SEP; '' if none."""
    if text is None or not has_inline_tests(path):
        return ""
    blocks = []
    for s_, e in rust_test_spans(text, legacy):
        a = text.rfind("\n", 0, s_) + 1
        b = text.find("\n", e)
        blocks.append(text[a:len(text) if b < 0 else b].rstrip("\n"))
    return ("\n%s\n" % REGION_SEP).join(blocks)


def splice_regions(path, text, regions):
    """`text` with its test regions replaced by `regions` (a test_region_text), or None if they do not line up."""
    want = regions.split("\n%s\n" % REGION_SEP) if regions else []
    spans = rust_test_spans(text)
    if len(spans) != len(want):
        return None
    out, pos = [], 0
    for (s_, e), block in zip(spans, want):
        a = text.rfind("\n", 0, s_) + 1
        b = text.find("\n", e)
        b = len(text) if b < 0 else b
        out += [text[pos:a], block]
        pos = b
    out.append(text[pos:])
    return "".join(out)


def weakening_issues(path, hunks, old_text=None, new_text=None, strict=False):
    """Signs that an existing test was weakened. strict=True also flags any changed/removed line."""
    issues = []
    rem = [ln for h in hunks for ln in h["rem"]]
    add = [ln for h in hunks for ln in h["add"]]
    if strict and rem:
        issues.append("%d existing line(s) changed or removed" % len(rem))
    for ln in add:
        if SKIP_RE.search(ln):
            issues.append("skip/xfail added: " + trunc(ln.strip(), 90))
        if TRIVIAL_RE.search(ln):
            issues.append("trivial assertion added: " + trunc(ln.strip(), 90))
        if DEAD_RE.search(ln):
            issues.append("block that never runs added: " + trunc(ln.strip(), 90))
    kr, ka = {}, {}  # numeric tolerances removed vs tolerance keywords (any value) added
    for lines, d, rxs in ((rem, kr, (TOL_RE, CLOSE_TO_RE)), (add, ka, (TOL_KEY_RE, CLOSE_TO_RE))):
        for ln in lines:
            for rx in rxs:
                for m in rx.finditer(ln):
                    d[m.group(1).lower()] = d.get(m.group(1).lower(), 0) + 1
    for k in sorted(kr):
        if ka.get(k, 0) < kr[k]:
            issues.append("tolerance removed: %s (the default may be looser)" % k)
    n_rem = sum(len(ASSERT_RE.findall(ln)) for ln in rem)
    n_add = sum(len(ASSERT_RE.findall(ln)) for ln in add)
    if n_rem > n_add:
        issues.append("%d assertion(s) removed" % (n_rem - n_add))
    old_t, new_t = tol_values(rem), tol_values(add)
    for k in sorted(set(old_t) & set(new_t)):
        o, n = old_t[k], new_t[k]
        if _looser(k, o, n):
            pick = min if k in DIGIT_TOLS else max
            issues.append("tolerance loosened: %s %g -> %g" % (k, pick(o), pick(n)))
    for h in hunks:
        for a, b in pair_lines(h["rem"], h["add"]):
            # line by line too: a looser tolerance elsewhere in the file must not hide this one
            old_t, new_t = tol_values([a]), tol_values([b])
            for k in sorted(set(old_t) & set(new_t)):
                if _looser(k, old_t[k], new_t[k]):
                    pick = min if k in DIGIT_TOLS else max
                    issues.append("tolerance loosened: %s %g -> %g" % (k, pick(old_t[k]), pick(new_t[k])))
            if not (ASSERT_RE.search(a) or ASSERT_RE.search(b) or TOL_RE.search(a)):
                continue
            if _skeleton(a) != _skeleton(b):
                continue
            if NUM_RE.findall(a) == NUM_RE.findall(b):
                continue
            if _strip_tols(a) == _strip_tols(b):
                continue  # only tolerance values changed; handled by the tolerance rule above
            issues.append("number changed in an existing assertion: `%s` -> `%s`"
                          % (trunc(a.strip(), 70), trunc(b.strip(), 70)))
    if new_text is not None and path.endswith(".py"):
        issues += sorted(py_test_issues(new_text) - py_test_issues(old_text or ""))
    if new_text is not None and old_text is not None and path.endswith(".rs"):
        issues += rust_attr_issues(old_text, new_text)
    out = []
    for i in issues:
        if i not in out:
            out.append(i)
    return out


def protected_copy_path(repo, sha):
    return repo.dir / "protected" / sha


def viol(kind, path=None, detail="", task=None):
    return {"kind": kind, "path": path, "detail": detail, "task": task}


def fmt_viol(v):
    if isinstance(v, str):
        return v
    s = v["kind"]
    if v.get("task") and v["kind"].startswith("PROTECTED"):
        s += " (locked by %s)" % v["task"]
    if v.get("path"):
        s += ": " + v["path"]
    if v.get("detail"):
        s += ": " + v["detail"]
    return s


def run_guard(repo, st, cfg, t, cur_tree):
    """Return a list of violations (dicts, see viol()). Empty list means clean."""
    found = []
    for task in st["tasks"]:
        if task.get("spec_hash") and spec_hash(task) != task["spec_hash"]:
            found.append(viol("SPEC CHANGED OUTSIDE sci", None,
                              "%s verify/invariants were edited by hand" % task["id"], task["id"]))
    acks = st.get("acks") or {}
    checked = set(t.get("unlocked") or []) if t else set()  # unlocked for this task: open until `done`
    ign = cfg.get("ignore_globs") or []
    checked |= set(p for p in list((t or {}).get("locked") or {}) + list((t or {}).get("locked_regions") or {}) +
                   list(st.get("protected") or {}) + list(st.get("protected_regions") or {}) if match_any(p, ign))
    if t:
        for p, h in sorted((t.get("locked") or {}).items()):
            if p in checked:
                continue
            checked.add(p)
            cur = file_sha(repo.root / p)
            if cur != h:
                found.append(viol("LOCKED FILE DELETED" if cur is None else "LOCKED FILE CHANGED", p, "", t["id"]))
    for p, info in sorted((st.get("protected") or {}).items()):
        if p in checked:
            continue
        checked.add(p)
        cur = file_sha(repo.root / p)
        if cur == info["sha"] or (cur is not None and acks.get(p) == cur):
            continue
        if cur is None:
            found.append(viol("PROTECTED TEST DELETED", p, "", info["task"]))
            continue
        old = read_text(protected_copy_path(repo, info["sha"]))
        new = read_text(repo.root / p)
        if old is None:
            found.append(viol("PROTECTED TEST CHANGED", p, "", info["task"]))
            continue
        diff = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0))
        for issue in weakening_issues(p, parse_hunks(diff), old, new, strict=True):
            found.append(viol("PROTECTED TEST CHANGED", p, issue, info["task"]))
    # Tests inside source files (Rust): locked by this task or protected by an earlier one. Like protected files,
    # new tests may be added; existing lines may not change.
    regions = {}
    for p, info in sorted((st.get("protected_regions") or {}).items()):
        regions[p] = (info["sha"], "PROTECTED TESTS CHANGED", info["task"])
    if t:
        for p, h in sorted((t.get("locked_regions") or {}).items()):
            regions[p] = (h, "LOCKED TESTS CHANGED", t["id"])
    for p, (h, kind, owner) in sorted(regions.items()):
        if p in checked:
            continue
        checked.add(p)
        text = read_text(repo.root / p)
        cur = test_region_text(p, text)
        if text is not None and sha256_text(cur) == h:
            continue
        if text is None or not cur:
            found.append(viol(kind.replace("CHANGED", "DELETED"), p, "", owner))
            continue
        old = read_text(protected_copy_path(repo, h))
        if old is None:
            found.append(viol(kind, p, "", owner))
            continue
        diff = "\n".join(difflib.unified_diff(old.splitlines(), cur.splitlines(), lineterm="", n=0))
        for issue in weakening_issues(p, parse_hunks(diff), old, cur, strict=True):
            found.append(viol(kind, p, issue, owner))
    if t and t.get("start_tree") and cur_tree:
        for status, p in tree_changes(repo, t["start_tree"], cur_tree):
            if p in checked:
                continue
            if status == "M" and has_inline_tests(p) and not is_test(p, cfg):
                old_r = test_region_text(p, blob_text(repo, t["start_tree"], p))
                new_r = test_region_text(p, read_text(repo.root / p))
                if old_r and old_r != new_r:
                    diff = "\n".join(difflib.unified_diff(old_r.splitlines(), new_r.splitlines(), lineterm="", n=0))
                    for issue in weakening_issues(p, parse_hunks(diff), old_r, new_r):
                        found.append(viol("EXISTING TEST WEAKENED", p, issue))
                continue
            if not is_test(p, cfg):
                continue
            if status == "D":
                found.append(viol("EXISTING TEST FILE DELETED", p))
                continue
            if status != "M":
                continue
            cur = file_sha(repo.root / p)
            if cur is not None and acks.get(p) == cur:
                continue
            hunks = parse_hunks(tree_diff_text(repo, t["start_tree"], cur_tree, p))
            old = blob_text(repo, t["start_tree"], p)
            new = read_text(repo.root / p)
            for issue in weakening_issues(p, hunks, old, new):
                found.append(viol("EXISTING TEST WEAKENED", p, issue))
    return found


def guard_fix_hint(viols):
    if any(isinstance(v, dict) and v["kind"].startswith("SPEC") for v in viols):
        return "Undo hand edits to .sci/state.json, or change the task properly: .sci/sci task amend <ID> ... --reason \"...\""
    files = []
    for v in viols:
        p = v.get("path") if isinstance(v, dict) else None
        if p and p not in files:
            files.append(p)
    target = " ".join(files[:4]) or "<file>"
    return ("Undo it: `.sci/sci restore --paths %s`. If the test has to change because it is wrong or must follow "
            "an intended change of this task (a new signature, a renamed function): `.sci/sci unlock %s --reason "
            "\"<why>\"` (listed for human review)." % (target, target))


# --------------------------------------------------------------------------- change-aware verification
#
# verify never repeats work whose answer it already has:
#   - a step whose command passed on byte-identical code is reused. "Code" is every file in the snapshot except
#     documentation that no test or check command reads, so editing a README or CHANGELOG does not force a rerun;
#   - an earlier task's check whose tests the regression suite runs anyway is left to the suite, so no test runs
#     twice in one verify.

def tree_entries(repo, tree):
    """{path: (mode, blob id, size)} of every file in a tree object."""
    files = {}
    for rec in git(repo.root, ["ls-tree", "-r", "-l", "-z", tree]).stdout.split("\0"):
        meta, sep, path = rec.partition("\t")
        parts = meta.split()
        if sep and len(parts) >= 4:
            files[path] = (parts[0], parts[2], int(parts[3]) if parts[3].isdigit() else 0)
    return files


def cat_blobs(repo, shas):
    """{blob id: text}, read with one `git cat-file --batch`."""
    want = sorted(set(shas))
    if not want:
        return {}
    p = subprocess.run(["git", "cat-file", "--batch"], cwd=str(repo.root), input=("\n".join(want) + "\n").encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    data, res, i = p.stdout, {}, 0
    while i < len(data):
        nl = data.find(b"\n", i)
        if nl < 0:
            break
        head = data[i:nl].split()
        i = nl + 1
        if len(head) < 3 or not head[2].isdigit():
            continue  # "<id> missing"
        size = int(head[2])
        res[head[0].decode("ascii", "replace")] = data[i:i + size].decode("utf-8", "replace")
        i += size + 1
    return res


def step_commands(st, cfg):
    """The commands `verify` runs now: the active task's check, finished tasks' checks, goldens, the regression
    suite and the build. A document one of them names is something a check reads."""
    t = active_task(st)
    cmds = [d.get("verify") or "" for d in st["tasks"] if d["status"] == "done" or (t and d["id"] == t["id"])]
    cmds += [(g or {}).get("cmd") or "" for g in (st.get("goldens") or {}).values()]
    cmds += [cfg.get(k) or "" for k in ("regress_cmd", "build_cmd")]
    return [c for c in cmds if c]


def all_commands(st, cfg):
    """Every command sci knows, including those of tasks not started yet: a script one of them names is read
    like source code."""
    cmds = [d.get("verify") or "" for d in st["tasks"]]
    cmds += [(g or {}).get("cmd") or "" for g in (st.get("goldens") or {}).values()]
    cmds += [cfg.get(k) or "" for k in ("regress_cmd", "test_cmd", "build_cmd")]
    return [c for c in cmds if c]


def py_strings(text):
    """String literals of Python source, except docstrings: those cite documents, they don't read them."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return text
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docs.add(id(first.value))
    return "\n".join(n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs)


DOC_TOOLS = frozenset("codespell cspell markdownlint markdownlint-cli2 mdl mdformat prettier vale proselint doc8 "
                      "rstcheck sphinx-build mkdocs lychee markdown-link-check linkchecker pandoc typos remark "
                      "textlint pre-commit".split())
BUILD_FILE_RE = re.compile(r"(?:^|/)(?:GNUmakefile|[Mm]akefile|[^/]+\.mk|[Jj]ustfile|tox\.ini|CMakeLists\.txt|"
                           r"[^/]+\.cmake|Taskfile\.ya?ml)$")


def doc_mentions(text, refs):
    """The document names (lowercase basenames) and extensions that `text` mentions."""
    low = text.lower()
    found = set(tok for tok in (m.group(0).rstrip(".-") for m in re.finditer(r"[\w.-]+", low)) if tok in refs["names"])
    found.update(n for n in refs["odd"] if n in low)
    found.update(e for e in re.findall(r"(?<![\w-])(\.\w+)", low) if e in refs["exts"])
    return found


_REFS_MEMO = {}


def doc_refs(store, tree, files):
    """What snapshot `tree` says about its documentation files (doc_globs): which ones exist, the names and
    extensions that source code, tests, build files (Makefile, tox.ini ...) or scripts named by a sci command
    mention, and the directories that hold source code. Python docstrings and comments do not count: they cite
    documents, they don't read them. Neither does setup.py: it reads the README to build a package."""
    cfg = store.cfg
    cmds = all_commands(store.st, cfg)
    key = (tree, json.dumps([cfg.get("doc_globs"), cfg.get("test_globs"), cmds]))
    if key in _REFS_MEMO:
        return _REFS_MEMO[key]
    docs = [p for p in files if is_doc(p, cfg)]
    names = set(p.rsplit("/", 1)[-1].lower() for p in docs)
    refs = {"docs": docs, "names": names, "exts": set(os.path.splitext(n)[1] for n in names) - {""},
            "odd": set(n for n in names if not re.match(r"^[\w.-]+$", n)),  # spaces etc.: substring search
            "code_dirs": set(p.rsplit("/", 1)[0] for p in files if "/" in p and SRC_EXT_RE.search(p)),
            "hits": set()}
    if docs:
        refs["hits"] = doc_mentions(" ".join(pytest_ini(store.repo.root).get("addopts") or []), refs)
        doc_set = set(docs)
        named = set(_norm_target(w) for c in cmds for w in verify_tokens(c))

        def reader(p):
            return p not in doc_set and p.rsplit("/", 1)[-1] != "setup.py" and bool(
                SRC_EXT_RE.search(p) or is_test(p, cfg) or BUILD_FILE_RE.search(p) or p in named)
        for mentions in mentions_by_file(store, tree, files, refs, reader, key[1]).values():
            refs["hits"].update(mentions)
    _REFS_MEMO[key] = refs
    return refs


def mentions_by_file(store, tree, files, refs, reader, salt):
    """{path: [document names and extensions it mentions]} for the files of `tree` that `reader` accepts and that
    mention any. Kept in .sci/mentions.json for the last tree, so after an edit only the changed files are read:
    on a large repository a full scan (git grep, then parsing every file that matches) takes seconds."""
    repo = store.repo
    needles = sorted(refs["names"] | refs["exts"])
    key = sha256_text(json.dumps([VERSION, needles, salt]))[:16]
    path = repo.dir / "mentions.json"
    try:
        cache = json.loads(read_text(path) or "{}")
    except ValueError:
        cache = {}
    old_tree, hits = cache.get("tree"), cache.get("hits") or {}
    todo = None
    if cache.get("key") == key and old_tree:
        try:
            changed = tree_changes(repo, old_tree, tree)
        except Refuse:
            changed = None
        if changed is not None and len(changed) <= 2000:
            for _, p in changed:
                hits.pop(p, None)
            todo = [p for _, p in changed if p in files and reader(p)]
    if todo is None:
        # `git grep` finds the few files that mention any document at all; only those are read and parsed.
        hits = {}
        pf = repo.dir / ("needles.%d" % os.getpid())
        try:
            atomic_write(pf, "\n".join(needles) + "\n", sync=False)
            g = git(repo.root, ["grep", "-I", "-l", "-i", "-F", "-z", "-f", str(pf), tree], check=False)
        finally:
            with contextlib.suppress(OSError):
                pf.unlink()
        todo = [p for p in (rec.split(":", 1)[-1] for rec in g.stdout.split("\0") if rec) if p in files and reader(p)]
    blobs = cat_blobs(repo, [files[p][1] for p in todo])
    for p in todo:
        text = blobs.get(files[p][1]) or ""
        low = text.lower()
        if not any(n in low for n in needles):
            continue
        found = doc_mentions(py_strings(text) if p.endswith(".py") else text, refs)
        if found:
            hits[p] = sorted(found)
    with contextlib.suppress(OSError):
        atomic_write(path, json.dumps({"key": key, "tree": tree, "hits": hits}), sync=False)
    return hits


def inert_docs(store, tree, files, cmds=None):
    """Documentation files that the commands `cmds` (default: everything verify runs) cannot read, so editing them
    cannot change a result. A document is not inert if it sits in a test directory or below a directory that
    holds source code (package data such as a prompt template), if source code, a test or a build file names it
    or its extension (see doc_refs), if a command runs a documentation tool, or if a command names the document,
    its directory or (like a link checker that globs `*.md`) its extension."""
    refs = doc_refs(store, tree, files)
    if not refs["docs"]:
        return set()
    cmds = step_commands(store.st, store.cfg) if cmds is None else [c for c in cmds if c]
    if any(w.rsplit("/", 1)[-1].lower() in DOC_TOOLS for c in cmds for w in verify_tokens(c)):
        return set()
    named_dirs = set()
    for c in cmds:
        if parse_test_cmd(c):
            continue  # pytest and unittest read Python files; a doctest glob (*.md) counts as an extension
        for w in verify_tokens(c)[1:]:
            v = _norm_target(w.split("=", 1)[-1] if w.startswith("-") else w)
            if v not in (".", "..") and not v.startswith(("../", "/")):
                named_dirs.add(v)
    hits = refs["hits"] | doc_mentions("\n".join(cmds), refs)

    def inert(p):
        name = p.rsplit("/", 1)[-1].lower()
        if name in hits or os.path.splitext(name)[1] in hits:
            return False
        d = p
        while "/" in d:
            d = d.rsplit("/", 1)[0]
            if d in refs["code_dirs"] or d in named_dirs:
                return False
        return True

    return set(p for p in refs["docs"] if inert(p))


_FP_MEMO = {}


def code_fp(store, tree, cmds=None):
    """Fingerprint of what checks can see in snapshot `tree`: every file except documentation they cannot read.
    `cmds` are the commands of one verify step (with the build); by default everything verify runs."""
    cfg = store.cfg
    cmds = step_commands(store.st, cfg) if cmds is None else [c for c in cmds if c]
    salt = sha256_text(json.dumps([cfg.get("doc_globs"), cfg.get("test_globs"), all_commands(store.st, cfg), cmds]))
    key = "%s:%s" % (tree, salt[:16])
    if key in _FP_MEMO:
        return _FP_MEMO[key]
    path = store.repo.dir / "fingerprints.json"
    try:
        cache = json.loads(read_text(path) or "{}")
    except ValueError:
        cache = {}
    fp = cache.pop(key, None)
    if not fp:
        files = tree_entries(store.repo, tree)
        skip = inert_docs(store, tree, files, cmds)
        h = hashlib.sha256()
        for p in sorted(files):
            if p not in skip:
                h.update(("%s %s %s\n" % (files[p][0], files[p][1], p)).encode("utf-8"))
        fp = h.hexdigest()
    cache[key] = fp
    with contextlib.suppress(OSError):
        atomic_write(path, json.dumps(dict(list(cache.items())[-200:])), sync=False)
    _FP_MEMO[key] = fp
    return fp


def step_fp(store, tree, cmd):
    """Fingerprint for reusing one step's PASS: only documents that this command (or the build) reads count. A
    different build command makes different binaries, so it is part of the fingerprint."""
    build = (store.cfg.get("build_cmd") or "").strip()
    fp = code_fp(store, tree, [cmd, build])
    return sha256_text(fp + "\0" + build) if build else fp


def cacheable(cmd):
    """Reuse a PASS only for commands that read nothing outside the repository except the program itself
    (`../venv/bin/python -m pytest` qualifies; `test -s ../data/run.h5` does not)."""
    for tok in verify_tokens(cmd)[1:]:
        v = tok.split("=", 1)[-1] if tok.startswith("-") else tok
        if v == ".." or v.startswith(("../", "/", "~", "$")) or re.match(r"^[A-Za-z]:/", v):
            return False
    return True


_ENV_MEMO = {}


def env_stamp(root, cmd):
    """When the packages a check's program can import last changed ('' if sci cannot tell): the change times of
    the site-packages folders of the program's virtualenv or conda environment, or of a plain interpreter's own
    site folders. Installing, upgrading or removing a package changes them, so an earlier PASS is not reused."""
    toks = verify_tokens(cmd)
    while toks and re.match(r"^\w+=", toks[0]):
        toks = toks[1:]
    if not toks:
        return ""
    prog = toks[0]
    if prog in _ENV_MEMO:
        return _ENV_MEMO[prog]
    root, envs, sites = Path(root), [], []
    if prog in ("uv", "poetry", "pipenv", "pdm", "rye", "hatch", "pixi"):
        envs = [root / ".venv", root / "venv"] + sorted((root / ".pixi" / "envs").glob("*"))
    else:
        found = prog if "/" in prog or "\\" in prog else shutil.which(prog)
        exe = Path(found) if found else None
        if exe is not None and not exe.is_absolute():
            exe = root / exe
        envs = [d for d in ((exe.parent, exe.parent.parent) if exe else ())
                if (d / "pyvenv.cfg").is_file() or (d / "conda-meta").is_dir()][:1]
        if exe and not envs and PY_PROG_RE.search(prog):
            argv = [str(exe)] + [a for a in toks[1:2] if re.match(r"^-\d[\d.]*$", a)]  # py -3
            code = "import site; print('\\n'.join(site.getsitepackages() + [site.getusersitepackages()]))"
            with contextlib.suppress(OSError, subprocess.SubprocessError, AttributeError):
                p = subprocess.run(argv + ["-c", code], cwd=str(root), stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace", timeout=20)
                sites = [Path(s) for s in p.stdout.splitlines() if s.strip()]
    for e in envs:
        sites += [e / "conda-meta", e / "Lib" / "site-packages", e / "lib" / "site-packages"]
        sites += sorted(e.glob("lib/python*/site-packages")) + sorted(e.glob("lib64/python*/site-packages"))
    marks = []
    for s in sites:
        with contextlib.suppress(OSError):
            marks.append("%s=%d" % (s, s.stat().st_mtime_ns))
    _ENV_MEMO[prog] = sha256_text("\n".join(marks))[:12] if marks else ""
    return _ENV_MEMO[prog]


def cached_pass(st, key, fp, env=""):
    rec = (st.get("passes") or {}).get(key)
    return rec if rec and rec.get("fp") == fp and rec.get("env", "") == env else None


def note_result(st, key, fp, ok, secs, env=""):
    """Remember the code fingerprint and package environment a check last passed on, and how long it took."""
    for name, rec in (("passes", {"fp": fp, "env": env, "t": now_iso(), "secs": round(secs, 1)} if ok and fp
                       else None), ("durations", round(secs, 1))):
        d = st.setdefault(name, {})
        d.pop(key, None)
        if rec is not None:
            d[key] = rec
        for k in list(d)[:-300]:
            del d[k]


PY_PROG_RE = re.compile(r"(?:^|/)(?:python[\d.]*|pypy[\d.]*|py)(?:\.exe)?$", re.I)
PYTEST_FLAGS = frozenset("-q -qq -qqq -v -vv -vvv -s -l --showlocals --verbose --quiet --no-header --no-summary "
                         "--disable-warnings --disable-pytest-warnings --strict-markers --strict --ff --failed-first "
                         "--nf --new-first".split())
PYTEST_VALUED = frozenset("--tb --color --durations --durations-min --capture -n --numprocesses --dist "
                          "--maxprocesses".split())
PYTEST_NARROWING = frozenset("-x --exitfirst --sw --stepwise --lf --last-failed -k -m --deselect --ignore "
                             "--ignore-glob --maxfail".split())
UNITTEST_FLAGS = frozenset("-v --verbose -q --quiet -b --buffer -c --catch --locals".split())
PYTEST_NORECURSE = ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}")


def cmd_words(cmd):
    """Shell words of a plain command; None if it has shell operators, redirections or env assignments."""
    if WINDOWS:
        cmd = cmd.replace("\\", "/")
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return None
    if not toks or re.match(r"^\w+=", toks[0]) or any(t and all(c in "();<>|&" for c in t) for t in toks):
        return None
    return toks


def parse_test_cmd(cmd):
    """What a plain `pytest ...` or `python -m pytest|unittest ...` command runs, and whether it narrows the
    selection or stops early. None for anything else (scripts, other runners, pipelines)."""
    toks = cmd_words(cmd or "")
    if not toks:
        return None
    pre = []
    if len(toks) > 2 and toks[0] in ("uv", "poetry", "pipenv", "pdm", "rye", "hatch") and toks[1] == "run":
        pre, toks = toks[:2], toks[2:]
    base = toks[0].rsplit("/", 1)[-1].lower()
    base = base[:-4] if base.endswith(".exe") else base
    if base in ("pytest", "py.test"):
        spec, i = {"kind": "pytest", "form": "prog", "interp": " ".join(pre + toks[:1])}, 1
    elif PY_PROG_RE.search(toks[0]):
        i = 1
        while i < len(toks) and (toks[i] in ("-B", "-u", "-E", "-s", "-I")
                                 or (base == "py" and re.match(r"^-\d[\d.]*$", toks[i]))):
            i += 1
        if i + 1 >= len(toks) or toks[i] != "-m" or toks[i + 1] not in ("pytest", "py.test", "unittest"):
            return None
        spec = {"kind": "unittest" if toks[i + 1] == "unittest" else "pytest", "form": "python",
                "interp": " ".join(pre + toks[:i])}
        i += 2
    else:
        return None
    rest = toks[i:]
    spec.update({"targets": [], "raw": [], "discover": None, "narrow": False, "stop": False, "unknown": False})
    j = 0
    if spec["kind"] == "unittest":
        disc = bool(rest) and rest[0] == "discover"
        d, pos, j = {"start": ".", "pattern": "test*.py"}, [], (1 if disc else 0)
        while j < len(rest):
            a = rest[j]
            if disc and a in ("-s", "--start-directory", "-p", "--pattern", "-t", "--top-level-directory"):
                if j + 1 < len(rest) and a in ("-s", "--start-directory", "-p", "--pattern"):
                    d["start" if a in ("-s", "--start-directory") else "pattern"] = rest[j + 1]
                j += 2
                continue
            if a in ("-f", "--failfast"):
                spec["stop"] = True
            elif a == "-k" or a == "--durations":
                spec["narrow"] = spec["narrow"] or a == "-k"
                j += 1
            elif a.startswith("-k"):
                spec["narrow"] = True
            elif a.startswith("-") and a not in UNITTEST_FLAGS:
                spec["unknown"] = True
            elif not a.startswith("-"):
                pos.append(a)
            j += 1
        if disc or not pos:  # a bare `python -m unittest` discovers from the current directory
            d.update(zip(("start", "pattern"), pos if disc else []))
            spec["discover"] = d
        else:
            spec["targets"] = pos
        return spec
    while j < len(rest):
        a = rest[j]
        if (a in PYTEST_FLAGS or re.match(r"^-r\w+$|^-n(?:\d+|auto|logical)$|^-pno:", a)
                or ("=" in a and a.split("=", 1)[0] in PYTEST_VALUED)):
            pass
        elif a in PYTEST_VALUED or (a == "-p" and rest[j + 1:j + 2] and rest[j + 1].startswith("no:")):
            j += 1
        elif a in ("-k", "--deselect") or a.startswith(("-k", "--deselect=")) or a in ("--lf", "--last-failed"):
            spec["narrow"] = True
            j += 1 if a in ("-k", "--deselect") else 0
        elif a in ("-x", "--exitfirst", "--sw", "--stepwise") or a.startswith("--maxfail"):
            spec["stop"] = True
            j += 1 if a == "--maxfail" else 0
        elif a.startswith("-"):
            spec["unknown"] = True
            break
        else:
            spec["targets"].append(a.split("::", 1)[0].split("[", 1)[0])
            spec["raw"].append(a)
        j += 1
    return spec


def pytest_ini(root):
    """testpaths / python_files / norecursedirs / addopts of the first pytest config file with a pytest section."""
    for name, sect in (("pytest.ini", "pytest"), ("pyproject.toml", "tool.pytest.ini_options"), ("tox.ini", "pytest"),
                       ("setup.cfg", "tool:pytest")):
        text = read_text(Path(root) / name)
        m = re.search(r"(?ms)^\[" + re.escape(sect) + r"\][ \t]*$(.*?)(?=^\[|\Z)", text or "")
        if not m:
            continue
        res = {}
        for key in ("testpaths", "python_files", "norecursedirs", "addopts"):
            mm = re.search(r"(?m)^[ \t]*" + key + r"[ \t]*=[ \t]*(.*(?:\n[ \t]+\S.*)*)", m.group(1))
            if mm:
                res[key] = re.findall(r"[^\s,\[\]\"']+", mm.group(1))
        return res
    return {}


def _norm_target(t):
    t = t.replace("\\", "/")
    while t.startswith("./"):
        t = t[2:]
    return t.rstrip("/") or "."


def _under(path, d):
    return d == "." or path == d or path.startswith(d + "/")


def unittest_runs(d, path, files):
    """Would `python -m unittest discover -s START -p PATTERN`, run from the repo root, import test file `path`?
    Discovery imports only files whose names are module names (test-io.py is skipped) inside packages."""
    start = _norm_target(d["start"])
    name = path.rsplit("/", 1)[-1]
    if not re.match(r"[_a-z]\w*\.py\Z", name, re.I) or not fnmatch.fnmatchcase(name, d["pattern"]):
        return False
    if not _under(path, start):
        return False
    base = "" if start == "." else start + "/"
    parts = path[len(base):].split("/")[:-1]
    return all(base + "/".join(parts[:k]) + "/__init__.py" in files for k in range(1, len(parts) + 1))


def pytest_runs(spec, ini, path):
    """Would this pytest command collect test file `path`?"""
    targets = ([_norm_target(t) for t in spec["targets"]] or [_norm_target(t) for t in ini.get("testpaths") or []]
               or ["."])
    pats = ini.get("python_files") or ["test_*.py", "*_test.py"]
    skip = ini.get("norecursedirs") or PYTEST_NORECURSE
    for tg in targets:
        if path == tg:
            return True
        if not _under(path, tg):
            continue
        dirs = (path if tg == "." else path[len(tg) + 1:]).split("/")[:-1]
        if not any(fnmatch.fnmatchcase(x, s) for x in dirs for s in skip) and any(
                fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], p) for p in pats):
            return True
    return False


def test_files_of(spec, files, ini):
    """Repo paths of the test files a parsed test command runs; None when that cannot be told."""
    if spec["discover"] is not None:
        return [p for p in files if unittest_runs(spec["discover"], p, files)] or None
    res = []
    for t in (_norm_target(x) for x in spec["targets"]):
        if spec["kind"] == "unittest" and not t.endswith(".py"):
            parts = t.split(".")
            hit = next((c for c in ("/".join(parts[:k]) + ".py" for k in range(len(parts), 0, -1)) if c in files), None)
            if not hit:
                return None
            res.append(hit)
        elif t in files:
            res.append(t)
        else:
            sub = [p for p in files if spec["kind"] == "pytest" and p.endswith(".py") and _under(p, t)
                   and pytest_runs({"targets": [t]}, ini, p)]
            if not sub:
                return None
            res += sub
    return res or None


def _stems(paths):
    return set(p.rsplit("/", 1)[-1][:-3] for p in paths if p.endswith(".py"))


def _id_parts(test_id):
    return set(re.split(r"[/.:\\]+", test_id))


def suite_covers(suite, cmd, files, ini, baseline):
    """The test files of check `cmd` if the regression suite (parsed) is sure to run every one of them, else None.

    Only plain pytest/unittest commands with the same interpreter qualify. A suite that narrows the selection
    (-k, -m, --ignore ...), stops at the first failure (-x, -f) or has options sci does not know covers nothing,
    and neither does a test file with a known baseline failure (the suite tolerates that file failing)."""
    task = parse_test_cmd(cmd)
    if not suite or not task or task["unknown"] or suite["unknown"] or suite["narrow"] or suite["stop"]:
        return None
    if suite["form"] != task["form"] or suite["interp"] != task["interp"]:
        return None
    if suite["kind"] == "unittest" and task["kind"] != "unittest":
        return None
    if suite["kind"] == "pytest" and any(o.split("=", 1)[0] in PYTEST_NARROWING or re.match(r"^-[km]\S", o)
                                         for o in ini.get("addopts") or []):
        return None
    tf = test_files_of(task, files, ini)
    if not tf:
        return None
    if suite["kind"] == "pytest":
        if not all(pytest_runs(suite, ini, p) for p in tf):
            return None
    elif suite["discover"] is not None:
        if not all(unittest_runs(suite["discover"], p, files) for p in tf):
            return None
    elif not set(tf) <= set(test_files_of(suite, files, ini) or []):
        return None
    stems = _stems(tf)
    if any(stems & _id_parts(f) for f in baseline or []):
        return None
    return tf


def narrow_suite(cmd, failing, files):
    """A command that reruns only the `failing` tests of suite `cmd` (pytest node ids, or unittest ids that import
    from the repo root), or None when sci cannot build one that selects exactly those tests."""
    spec = parse_test_cmd(cmd)
    toks = cmd_words(cmd)
    if not spec:
        return narrow_cargo(cmd, failing) or narrow_js(cmd, failing, files)
    if spec["unknown"] or not failing or len(failing) > 40:
        return None
    if spec["kind"] == "pytest":
        if not all("::" in f and f.split("::", 1)[0] in files for f in failing):
            return None
        head = [w for w in toks if w not in spec["raw"]]
    else:
        ids = []
        for f in failing:
            parts = f.split(".")
            if parts[-1] in ("setUpClass", "tearDownClass", "setUpModule", "tearDownModule"):
                parts = parts[:-1]  # the fixture is not a test; its class or module is
            if not any("/".join(parts[:k]) + ".py" in files for k in range(len(parts), 0, -1)):
                return None
            if ".".join(parts) not in ids:
                ids.append(".".join(parts))
        head, failing = toks[:toks.index("-m") + 2], ids
    return " ".join(shlex.quote(w) for w in head + list(failing))


JS_ID_SPLIT_RE = re.compile(r"\s+(?:›|>)\s+")


def js_test_script(root=None):
    """The runner package.json's "test" script starts (jest, vitest ...), or None."""
    root = root or (_ROOT[0] if _ROOT else None)
    try:
        script = json.loads(read_text(Path(root) / "package.json") or "{}").get("scripts", {}).get("test") or ""
    except (ValueError, AttributeError, TypeError):
        return None
    prog, _ = _runner_word(cmd_words(script) or [])
    return prog


def narrow_js(cmd, failing, files):
    """`cmd` with the test files of the `failing` jest/vitest/node --test ids appended: those files run again, not
    the whole suite (`npm test -- <files>` when package.json's test script is jest or vitest). None if sci cannot
    tell which files hold every failing test."""
    words = cmd_words(cmd) or []
    prog, rest = _runner_word(words)
    if not failing or len(failing) > 40:
        return None
    paths = []
    for f in failing:
        path = JS_ID_SPLIT_RE.split(f, 1)[0].strip()
        if path not in files:
            return None
        if path not in paths:
            paths.append(path)
    if prog in ("jest", "vitest") or (prog == "node" and "--test" in rest) or (prog == "bun" and rest[:1] == ["test"]):
        return " ".join(shlex.quote(w) for w in words + paths)
    script = prog == "test" or (prog == "npm" and rest[:1] in (["test"], ["t"]) or rest[:2] == ["run", "test"])
    if script and js_test_script() in ("jest", "vitest"):
        sep = [] if "--" in words or prog == "test" else ["--"]
        return " ".join(shlex.quote(w) for w in words + sep + paths)
    return None


def narrow_cargo(cmd, failing):
    """`cargo test ... -- --exact <ids>`: reruns only the failing tests of a cargo test suite (every target, each
    running just the tests with those exact names), or None."""
    words = cmd_words(cmd) or []
    prog, rest = _runner_word(words)
    if prog != "cargo" or rest[:1] != ["test"] or not failing or len(failing) > 40:
        return None
    if not all(re.match(r"^[A-Za-z_][\w:]*$", f) for f in failing):
        return None  # doc tests ("src/lib.rs - add (line 3)") cannot be selected exactly
    cut = words.index("--") if "--" in words else len(words)
    head, tail = words[:cut], words[cut + 1:]
    if "--no-fail-fast" not in head:
        head.append("--no-fail-fast")
    tail = [w for w in tail if w != "--exact"] + ["--exact"]
    return " ".join(shlex.quote(w) for w in head + ["--"] + tail + list(failing))


# Test runners whose checks are usually the suite's command plus a selection, as in `cargo test` and
# `cargo test energy`. Only selections that cannot reach a test the suite leaves out are listed: cargo's --test
# and --lib can run a target that has `test = false`, and Maven's -Dtest overrides the suite's excludes.
# start: the words that begin the command. value: selecting flags that take a value. flags: selecting flags
# without one. words: what a bare word is (a test-name or path filter, a go package that a suite pattern must
# cover, or None: an ordinary argument). quiet/quietv: flags a check may add that change neither which tests
# run nor how they build (quietv ones take a value).
# skip: flags that leave tests out of a suite.
RUNNERS = [
    {"start": [["cargo", "test"]], "value": set(), "flags": {"--", "--exact"}, "words": "filter",
     "quiet": {"-q", "--quiet", "-v", "--verbose", "--nocapture", "--show-output", "--no-fail-fast"}, "quietv": set(),
     "skip": {"--exclude", "--skip"}},
    {"start": [["go", "test"]], "value": {"-run"}, "flags": set(), "words": "package",
     "quiet": {"-v", "-count", "-json"}, "quietv": set(), "skip": {"-skip"}},
    {"start": [["ctest"]], "value": {"-R", "--tests-regex"}, "flags": set(), "words": None,
     "quiet": {"-V", "-VV", "--verbose", "--extra-verbose", "--output-on-failure", "-Q", "--quiet"},
     "quietv": {"-j", "--parallel"},
     "skip": {"-E", "--exclude-regex", "-L", "--label-regex", "-LE", "--label-exclude", "-I", "--tests-information",
              "-U", "--union", "-FA", "--fixture-exclude-any", "--rerun-failed"}},
    {"start": [[l, "jest"] for l in ("npx", "yarn", "pnpm", "bunx")] + [["jest"], ["pnpm", "exec", "jest"]],
     "value": {"-t", "--testNamePattern", "--testPathPattern", "--testPathPatterns"}, "flags": set(),
     "words": "filter", "quiet": {"--ci", "--silent", "--verbose"}, "quietv": set(),
     "skip": {"-o", "--onlyChanged", "--changedSince", "--findRelatedTests", "--lastCommit", "-f", "--onlyFailures",
              "--testPathIgnorePatterns", "--shard"}},
    {"start": [[l, "vitest", "run"] for l in ("npx", "yarn", "pnpm", "bunx")] + [["vitest", "run"]],
     "value": {"-t", "--testNamePattern"}, "flags": set(), "words": "filter", "quiet": {"--silent", "--reporter"},
     "quietv": set(), "skip": {"--changed", "--related", "--exclude", "--shard"}},
    {"start": [["dotnet", "test"]], "value": {"--filter"}, "flags": set(), "words": None,
     "quiet": {"--nologo"}, "quietv": {"-v", "--verbosity"}, "skip": set()},
    {"start": [["gradle"], ["./gradlew"], ["gradlew"], ["gradlew.bat"]], "value": {"--tests"}, "flags": set(),
     "words": None, "quiet": {"-q", "--quiet", "-i", "--info", "--console"}, "quietv": set(),
     "skip": {"-x", "--exclude-task"}},
]


def runner_args(r, words):
    """A runner's arguments: sel (what picks tests), pkgs (go packages), other (options the suite must share) and
    bad (a flag that leaves tests out). Flags that change neither which tests run nor how they build are dropped."""
    res, i = {"sel": [], "pkgs": [], "other": [], "bad": False}, 0
    while i < len(words):
        w = words[i]
        key = w.split("=", 1)[0]
        if key in r["value"]:
            if "=" in w:
                res["sel"].append(w.split("=", 1)[1])
            elif i + 1 < len(words):
                res["sel"].append(words[i + 1])
                i += 1
            else:
                res["bad"] = True
        elif w in r["flags"]:
            res["sel"].append("")
        elif key in r["quiet"]:
            pass
        elif w in r["quietv"]:
            i += 1
        elif key in r["skip"]:
            res["bad"] = True
        elif not w.startswith("-") and r["words"] == "filter":
            res["sel"].append(w)
        elif not w.startswith("-") and r["words"] == "package":
            res["pkgs"].append(w)
        else:
            res["other"].append(w)
        i += 1
    return res


def go_covers(patterns, pkg, files):
    """Whether `go test PATTERNS` tests package `pkg`. A `/...` pattern skips vendor/ and testdata/, directories
    whose names start with . or _, and nested modules (directories with their own go.mod)."""
    if pkg in patterns:
        return True
    path = pkg[:-4] if pkg.endswith("/...") else pkg
    if path != "." and not path.startswith("./"):
        return False
    parts = [x for x in path[2:].split("/") if x]
    if any(x in ("vendor", "testdata") or x[0] in "._" for x in parts) or any(
            "/".join(parts[:k]) + "/go.mod" in files for k in range(1, len(parts) + 1)):
        return False
    for pat in patterns:
        base = pat[:-4] if pat.endswith("/...") else None
        if base == "." or (base and (path == base or path.startswith(base + "/"))):
            return True
    return False


def runner_covers(suite_cmd, cmd, known, files=()):
    """The selection of check `cmd` (test-name filters, paths, packages) when the suite surely runs every test it
    runs: the same runner with the same options, a suite that selects nothing and a check that only narrows it.
    None otherwise, and when the baseline has failures and the suite stops at a failing target (it might never
    run the tests after it)."""
    if known and stops_early(suite_cmd):
        return None
    ts, tt = cmd_words(suite_cmd), cmd_words(cmd)
    if not ts or not tt:
        return None
    for r in RUNNERS:
        start = next((x for x in r["start"] if ts[:len(x)] == x), None)
        if start:
            break
    else:
        return None
    if tt[:len(start)] != start:
        return None
    a, b = runner_args(r, ts[len(start):]), runner_args(r, tt[len(start):])
    if a["bad"] or b["bad"] or a["sel"] or a["other"] != b["other"]:
        return None
    if r["words"] == "package" and not all(go_covers(a["pkgs"] or ["."], x, files) for x in b["pkgs"] or ["."]):
        return None
    return [k for k in b["sel"] if k] + b["pkgs"]


def blame_tasks(covered, failures):
    """[(task id, failing test ids)] for covered earlier tasks whose tests are among the suite's failures: Python
    test files by module name, other runners by the names in their selection (a check with none owns them all)."""
    res = []
    for tid, tf, keys in covered:
        stems = _stems(tf)
        words = [w for k in keys for w in re.split(r"[|,#+*()^$\s~=!&]+", k) if len(w) >= 3]  # shorter ones match all
        mine = [f for f in failures if stems & _id_parts(f) or any(w in f for w in words)
                or not (tf or keys)]
        if mine:
            res.append((tid, mine))
    return res


# --------------------------------------------------------------------------- next step / anchor / handoff

def compute_next(st, cfg, live_tree=None, live_ok=None, job=None):
    if st.get("paused"):
        return "Harness paused (`.sci/sci unpause` to resume)."
    if job:
        return ("Wait for the running %s: `.sci/sci wait` (repeat until it finishes). Meanwhile read code or use "
                "note/hyp/now; do not edit files." % (job.get("label") or "sci command"))
    if not st.get("objective"):
        return 'Set the objective: .sci/sci objective "<one-line goal>"'
    tasks = st["tasks"]
    if not tasks:
        nxt = ('Plan: explore (.sci/sci map, .sci/sci find <symbol>), then add 3-12 small tasks: '
               '.sci/sci task add "<title>" --verify "<cmd>" --scope <path>')
        if not st.get("baseline") and (cfg.get("regress_cmd") or cfg.get("test_cmd")):
            nxt += ". Once, before task 1: .sci/sci baseline"
        return nxt
    t = active_task(st)
    if t:
        if t.get("stop"):
            if int(t.get("resets") or 0) >= 2:
                return ("ESCALATE: %s still fails after %d fresh starts. Stop and ask the human: `.sci/sci task block %s "
                        "--reason \"<what you tried and what blocks you>\"`, then continue with another task."
                        % (t["id"], t["resets"], t["id"]))
            if t.get("stop_cycles", 0) >= 1 or int(t.get("strikes") or 0) >= 6:
                return ("ESCALATE: %s failed 6+ fix attempts. Run `.sci/sci restore --to start`, write down a different "
                        "approach (.sci/sci note --kind decision), or `.sci/sci task block %s --reason \"<question>\"` and ask "
                        "the human." % (t["id"], t["id"]))
            return ("STOP (3 failed fixes on %s): do not edit code. `.sci/sci hyp add` >=2 causes, test each with a "
                    "small `.sci/sci run` experiment, then `.sci/sci hyp refute|confirm H<n> \"<evidence>\"`." % t["id"])
        if st.get("violations"):
            return "Guard violation: %s. %s" % (trunc(fmt_viol(st["violations"][0]), 140), guard_fix_hint(st["violations"]))
        lc = t.get("last_check")
        if not is_locked(t):
            if lc is None:
                return ("Read the code (.sci/sci find <symbol>), write or pick the acceptance test, then `.sci/sci check` "
                        "(FAIL expected for new behavior, PASS for a refactor).")
            if lc.get("ok"):
                return "Check passes. Lock the acceptance tests: `.sci/sci lock`, then run `.sci/sci verify`."
            return ("If the check fails for the expected reason (feature missing, not a broken test), lock the "
                    "acceptance tests: `.sci/sci lock`, then implement in small steps.")
        p = t.get("pending")  # the step the last verify failed on (set_pending)
        if lc is None or not lc.get("ok"):
            g = p["name"] if p and p["kind"] == "golden" else None
            if g and g in st["goldens"] and not (st["goldens"][g].get("last") or {}).get("ok"):
                return ('Golden %s changed. Not intended: fix the code, then `.sci/sci check` (it reruns the golden). '
                        'Intended (bug fix, better method): `.sci/sci golden update %s --reason "..."`, then '
                        '`.sci/sci verify`. Strike %d/3.' % (g, g, t.get("strikes", 0)))
            if g and lc and (lc.get("by_verify") or lc.get("pending_failed")):
                return "Golden %s is up to date. Run `.sci/sci verify` again." % g
            if p:
                return ("Fix the code (not the tests), then `.sci/sci check`; it also reruns what failed the last "
                        "verify (%s). Strike %d/3." % (p["label"], t.get("strikes", 0)))
            return "Fix the code (not the tests), then `.sci/sci check`. Strike %d/3." % t.get("strikes", 0)
        if p and p.get("passed_tree") != lc.get("tree"):
            return "Run `.sci/sci check`: it also reruns what failed the last verify (%s)." % p["label"]
        vt = t.get("verified_tree")
        if not vt:
            return "Checks pass. Run `.sci/sci verify` (full gate)."
        if live_tree and live_tree != vt and not live_ok:
            return "Files changed since the last verify. Run `.sci/sci verify` again."
        if not t.get("verified_full"):
            return "Quick verify passed. Run the full `.sci/sci verify` before `.sci/sci done`."
        return 'Verified. Run `.sci/sci done -m "<summary>"`.'
    nxt = next_eligible(st)
    if nxt:
        return "Start the next task: `.sci/sci task start` (%s: %s)." % (nxt["id"], trunc(nxt["title"], 70))
    if all(x["status"] == "done" for x in tasks):
        lv = st.get("last_verify") or {}
        if (lv.get("ok") and not lv.get("quick") and not lv.get("task")
                and (lv.get("t") or "") >= max(x.get("done_at") or "" for x in tasks)
                and (live_tree is None or lv.get("tree") in (None, live_tree) or live_ok)):
            return ("All tasks done and the final verify passed. Run `.sci/sci report` and give the human the report, "
                    "including every NEEDS YOUR REVIEW item.")
        return "All tasks done. Run `.sci/sci verify` (final regression), then report to the human with `.sci/sci report`."
    blocked = [x for x in tasks if x["status"] == "blocked"]
    if blocked:
        return "Remaining tasks are blocked. Ask the human: " + "; ".join(
            "%s: %s" % (x["id"], trunc(x.get("block_reason", ""), 100)) for x in blocked[:3])
    return "No eligible task (check dependencies): .sci/sci task list"


def task_counts(st):
    c = {"done": 0, "todo": 0, "active": 0, "blocked": 0}
    for t in st["tasks"]:
        c[t["status"]] = c.get(t["status"], 0) + 1
    return c


def check_state(t, rel=True):
    lc = t.get("last_check")
    if not lc:
        return "not run"
    when = ago(lc.get("t")) if rel else "at " + (lc.get("t") or "?")[11:16] + "Z"
    return "%s %s" % ("PASS" if lc.get("ok") else "FAIL", when)


def anchor_block(st):
    """Short re-orientation block. Hooks inject this before model calls; resume prints a longer version."""
    if st.get("paused"):
        return "[sci] harness paused in this repo (`.sci/sci unpause` to resume)."
    c = task_counts(st)
    lines = ["[sci] Objective: " + trunc(st.get("objective") or "(not set)", 180),
             "[sci] Progress: %d/%d tasks done" % (c["done"], len(st["tasks"]))]
    t = active_task(st)
    if t:
        lines.append("[sci] Active %s: %s | verify: %s" % (t["id"], trunc(t["title"], 90), trunc(t["verify"], 90)))
        lines.append("[sci] Last check: %s | strikes %d/3 | scope: %s" % (
            check_state(t, rel=False), t.get("strikes", 0), trunc(", ".join(t["scope"]) or "(none)", 80)))
        if st.get("now") and st["now"].get("task") == t["id"]:
            lines.append("[sci] Doing: " + trunc(st["now"]["text"], 160))
    lines.append("[sci] NEXT: " + (st.get("next") or ""))
    lines.append("[sci] Lost context? Run `.sci/sci resume`. Never edit .sci/ by hand.")
    return "\n".join(lines)


def anchor_line(st):
    t = active_task(st)
    if st.get("paused"):
        return "[sci] paused"
    if t:
        return "[sci] %s \"%s\" | check %s | strikes %d/3 | NEXT: %s" % (
            t["id"], trunc(t["title"], 50), check_state(t), t.get("strikes", 0), trunc(st.get("next", ""), 260))
    return "[sci] no active task | NEXT: " + trunc(st.get("next", ""), 260)


def hyps_for(st, tid=None, status=None):
    return [h for h in st["hyps"] if (tid is None or h.get("task") == tid) and (status is None or h["status"] == status)]


def render_handoff(st, cfg):
    c = task_counts(st)
    L = ["# sci handoff", "",
         "_Generated %s by sci %s. Do not edit; run `.sci/sci resume` for a live briefing._" % (now_iso(), VERSION),
         "", "**Objective:** " + (st.get("objective") or "(not set)"), "",
         "**Next:** " + (st.get("next") or ""), "",
         "## Tasks (%d/%d done)" % (c["done"], len(st["tasks"]))]
    mark = {"done": "[x]", "active": "[>]", "blocked": "[!]", "todo": "[ ]"}
    for t in st["tasks"]:
        extra = ""
        if t["status"] == "done" and t.get("commit"):
            extra = " (commit %s)" % t["commit"][:8]
        if t["status"] == "blocked":
            extra = " BLOCKED: " + t.get("block_reason", "")
        L.append("- %s %s %s%s" % (mark.get(t["status"], "[?]"), t["id"], t["title"], extra))
    t = active_task(st)
    if t:
        L += ["", "## Active task %s" % t["id"], "- Title: " + t["title"], "- Verify: `%s`" % t["verify"],
              "- Scope: " + (", ".join(t["scope"]) or "(none)"),
              "- Invariants: " + ("; ".join(t["invariants"]) or "(none)"),
              "- Locked: " + lock_desc(t),
              "- Unlocked for this task: " + (", ".join(t.get("unlocked") or []) or "(none)"),
              "- Last check: %s; strikes %d/3" % (check_state(t), t.get("strikes", 0))]
        if st.get("now") and st["now"].get("task") == t["id"]:
            L.append("- Doing: " + st["now"]["text"])
    ref = hyps_for(st, status="refuted")
    if ref:
        L += ["", "## Ruled out (tested and false; do not re-test)"]
        L += ["- %s [%s] %s -- evidence: %s" % (h["id"], h.get("task") or "-", h["text"], h.get("evidence", ""))
              for h in ref]
    conf = hyps_for(st, status="confirmed")
    if conf:
        L += ["", "## Confirmed findings"]
        L += ["- %s [%s] %s -- evidence: %s" % (h["id"], h.get("task") or "-", h["text"], h.get("evidence", ""))
              for h in conf]
    opn = hyps_for(st, status="open")
    if opn:
        L += ["", "## Open hypotheses"] + ["- %s [%s] %s" % (h["id"], h.get("task") or "-", h["text"]) for h in opn]
    notes = [n for n in st["notes"] if not n.get("dropped")]
    if notes:
        L += ["", "## Notes"] + ["- N%d [%s] %s" % (n["id"], n["kind"], n["text"]) for n in notes]
    if st["goldens"]:
        L += ["", "## Golden outputs"]
        for name, g in sorted(st["goldens"].items()):
            last = g.get("last") or {}
            L.append("- %s: %d numbers, rtol=%g atol=%g, last check %s" % (
                name, g.get("n", 0), g.get("rtol", 0), g.get("atol", 0),
                ("OK" if last.get("ok") else "FAIL") + " " + ago(last.get("t")) if last else "never"))
    if st["review"]:
        L += ["", "## Needs human review"] + ["- [%s] %s %s: %s" % (r["kind"], r.get("task") or "-", r["t"], r["msg"])
                                              for r in st["review"]]
    L += ["", "## Commands", "`.sci/sci resume` | `check` | `verify` | `done -m` | `restore` | `hyp add` | `note` | "
          "`help`"]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- golden numbers

GOLD_NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])[-+]?(?:(?:\d+\.\d*|\.\d+|\d+)(?:[eEdD][-+]?\d+)?|"
                         r"(?:nan|inf(?:inity)?)\b)(?![A-Za-z0-9_]|\.\d)", re.I)


def parse_numbers(lines, filt=None):
    rx = re.compile(filt) if filt else None
    vals = []
    for ln in lines:
        if rx is not None and not rx.search(ln):
            continue
        for tok in GOLD_NUM_RE.findall(ln):
            try:
                vals.append(float(tok.replace("d", "e").replace("D", "e")))
            except ValueError:
                pass
    return vals


def compare_numbers(ref, new, rtol, atol):
    if len(ref) != len(new):
        return False, "count changed: %d -> %d numbers (output format changed?)" % (len(ref), len(new)), []
    bad, max_abs, max_rel = [], 0.0, 0.0
    for i, (a, b) in enumerate(zip(ref, new)):
        if math.isnan(a) or math.isnan(b):
            ok = math.isnan(a) and math.isnan(b)
        elif math.isinf(a) or math.isinf(b):
            ok = a == b
        else:
            d = abs(a - b)
            ok = d <= atol + rtol * abs(a)
            max_abs = max(max_abs, d)
            if a != 0:
                max_rel = max(max_rel, d / abs(a))
            elif d != 0:
                max_rel = float("inf")
        if not ok:
            bad.append(i)
    msg = "%d/%d numbers outside tolerance; max abs err %.3g, max rel err %.3g" % (len(bad), len(ref), max_abs, max_rel)
    return not bad, msg, bad


def golden_path(repo, name):
    return repo.dir / "golden" / (name + ".json")


def golden_check_one(store, name, label_prefix="golden"):
    """Run golden `name` and compare its numbers. Returns (ok, lines, record for goldens[name]["last"], seconds);
    the caller stores the record (it runs without the state lock)."""
    repo, cfg = store.repo, store.cfg
    try:
        with open(str(golden_path(repo, name)), encoding="utf-8") as f:
            ref = json.load(f)
    except (OSError, ValueError):
        return False, ["FAIL %s %s: reference file .sci/golden/%s.json is missing or corrupt" % (
            label_prefix, name, name)], {"ok": False, "t": now_iso()}, 0.0
    r = run_cmd(store, ref["cmd"], "golden-" + name, None, int(ref.get("timeout") or cfg["verify_timeout"]))
    if not r.ok:
        return (False, report_run(repo, cfg, r, "%s %s (command failed)" % (label_prefix, name)),
                {"ok": False, "t": now_iso()}, r.secs)
    vals = parse_numbers(r.lines[1:], ref.get("filter"))
    refv = [float(v) for v in ref["values"]]
    ok, msg, bad = compare_numbers(refv, vals, float(ref["rtol"]), float(ref["atol"]))
    last = {"ok": ok, "t": now_iso(), "msg": msg}
    if ok:
        return True, ["PASS %s %s: %d numbers within rtol=%g atol=%g" % (label_prefix, name, len(refv),
                                                                        float(ref["rtol"]), float(ref["atol"]))], last, r.secs
    out = ["FAIL %s %s: %s | log %s" % (label_prefix, name, msg, repo.rel(r.log))]
    for i in bad[:8]:
        out.append("  value #%d: reference %r, now %r" % (i, refv[i], vals[i]))
    out.append("  If this change of numbers is intended (better scheme, bug fix), justify it: "
               ".sci/sci golden update %s --reason \"...\"" % name)
    return False, out, last, r.secs


# --------------------------------------------------------------------------- toolchain detection

def detect_toolchain(root):
    root = Path(root)
    notes, cmd = [], ""

    def has(*names):
        return any((root / n).exists() for n in names)

    py = None
    for cand in (".venv/bin/python", "venv/bin/python", "env/bin/python",
                 ".venv/Scripts/python.exe", "venv/Scripts/python.exe", "env/Scripts/python.exe"):
        if (root / cand).exists():
            py = cand
            break
    pyproj = has("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini", "conftest.py")
    root_tests = any(root.glob("test*.py"))
    if pyproj or root_tests or ((root / "tests").is_dir() and any((root / "tests").glob("*.py"))):
        found = python_argv()
        interp_s = py or (" ".join(found) if found else PY_HINT)
        if py and WINDOWS and not windows_bash():
            interp_s = py.replace("/", "\\")  # cmd.exe reads /Scripts as a switch
        interp_exec = [str(root / py)] if py else (found or [sys.executable])
        try:
            probe = subprocess.run(interp_exec + ["-m", "pytest", "--version"], cwd=str(root),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   encoding="utf-8", errors="replace", timeout=60)
            has_pytest = probe.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            has_pytest = False
        if has_pytest:
            cmd = "%s -m pytest -q" % interp_s
        elif has("uv.lock") and shutil.which("uv"):
            cmd = "uv run pytest -q"
        elif has("pixi.toml") and shutil.which("pixi"):
            cmd = "pixi run pytest -q"
        else:
            start = "tests" if (root / "tests").is_dir() and not root_tests else "."
            cmd = "%s -m unittest discover -s %s -q" % (interp_s, start)
            notes.append("pytest not importable with %s; using unittest. Set test_cmd if the project uses pytest "
                         "in another environment (conda/module/venv)." % interp_s)
        notes.append("Python project detected -> %s" % cmd)
    alts = []
    if has("Cargo.toml"):
        # Every test target runs even after one fails, so a known failure cannot hide a new one.
        nextest = has(".config/nextest.toml") and shutil.which("cargo-nextest")
        alts.append("cargo nextest run --no-fail-fast" if nextest else "cargo test --no-fail-fast -q")
        pyproject = read_text(root / "pyproject.toml") or ""
        if re.search(r"maturin|setuptools[-_]rust|pyo3", pyproject, re.I):
            notes.append("Rust extension for Python (%s). Python tests import the built module, so set a rebuild "
                         "command: .sci/sci config set build_cmd \"maturin develop\" (or \"pip install -e .\"); "
                         "cargo tests run with: cargo test --no-fail-fast -q"
                         % ("maturin" if "maturin" in pyproject.lower() else "setuptools-rust/PyO3"))
    if has("go.mod"):
        alts.append("go test ./...")
    if has("Project.toml"):
        alts.append("julia --project=. -e 'using Pkg; Pkg.test()'")
    if has("fpm.toml"):
        alts.append("fpm test")
    if has("CMakeLists.txt"):
        alts.append("ctest --test-dir build --output-on-failure")
    mk = root / "Makefile"
    if mk.exists() and re.search(r"(?m)^(test|check)\s*:", read_text(mk) or ""):
        alts.append("make test")
    pkg = root / "package.json"
    if pkg.exists():
        try:
            if json.loads(read_text(pkg) or "{}").get("scripts", {}).get("test"):
                pm = ("pnpm" if has("pnpm-lock.yaml") else "yarn" if has("yarn.lock") else
                      "bun" if has("bun.lockb", "bun.lock") else "npm")
                alts.append("%s test%s" % (pm, " --silent" if pm == "npm" else ""))
        except ValueError:
            pass
    if has("DESCRIPTION") and (root / "tests").is_dir():
        alts.append("Rscript -e 'devtools::test()'")
    if not cmd and alts:
        cmd = alts[0]
    if alts:
        notes.append("Other runners seen: " + " | ".join(alts))
    compiled = []
    for pat in ("*.pyx", "*.f90", "*.F90", "*.f", "*.c", "*.cpp", "*.cu"):
        out = [x for x in git(root, ["ls-files", "-z", "--", pat, "**/" + pat], check=False).stdout.split("\0") if x]
        if out:
            compiled.append("%d %s" % (len(out), pat))
    if compiled and pyproj:
        notes.append("Compiled sources tracked (%s). If tests import compiled extensions, set a rebuild command: "
                     ".sci/sci config set build_cmd \"<e.g. pip install -e . --no-build-isolation>\"" % ", ".join(compiled))
    return cmd, notes


def repo_map(repo, limit_dirs=14):
    files = [p for p in git(repo.root, ["ls-files", "-z"]).stdout.split("\0") if p]
    lines = ["%d tracked files" % len(files)]
    if not files:
        return lines
    top, exts = {}, {}
    for p in files:
        parts = p.split("/")
        key = parts[0] + ("/" if len(parts) > 1 else "")
        top.setdefault(key, []).append(p)
        ext = os.path.splitext(p)[1].lower() or "(none)"
        exts[ext] = exts.get(ext, 0) + 1
    lines.append("by extension: " + ", ".join("%s %d" % (e, n) for e, n in
                                              sorted(exts.items(), key=lambda kv: -kv[1])[:12]))
    lines.append("top-level (files):")
    ranked = sorted(top.items(), key=lambda kv: -len(kv[1]))
    for key, fl in ranked[:limit_dirs]:
        e = {}
        for p in fl:
            x = os.path.splitext(p)[1].lower() or "(none)"
            e[x] = e.get(x, 0) + 1
        lines.append("  %-28s %6d  %s" % (key, len(fl), ", ".join("%s %d" % kv for kv in
                                                                  sorted(e.items(), key=lambda kv: -kv[1])[:4])))
        if key.endswith("/") and len(fl) > 40 and len(lines) < 40:
            sub = {}
            for p in fl:
                parts = p.split("/")
                if len(parts) > 2:
                    sub[parts[1] + "/"] = sub.get(parts[1] + "/", 0) + 1
            for s, n in sorted(sub.items(), key=lambda kv: -kv[1])[:5]:
                lines.append("      %-24s %6d" % (s, n))
    if len(ranked) > limit_dirs:
        lines.append("  ... %d more top-level entries" % (len(ranked) - limit_dirs))
    keys = [p for p in files if os.path.basename(p) in (
        "pyproject.toml", "setup.py", "setup.cfg", "CMakeLists.txt", "Makefile", "meson.build", "Cargo.toml",
        "go.mod", "Project.toml", "package.json", "environment.yml", "requirements.txt", "conftest.py",
        "pytest.ini", "tox.ini", "noxfile.py", "fpm.toml") and p.count("/") <= 1]
    if keys:
        lines.append("build/test config: " + ", ".join(keys[:12]))
    tdirs = sorted(set(p.split("/")[0] for p in files if p.split("/")[0] in ("tests", "test", "testing")))
    nested = sorted(set("/".join(p.split("/")[:2]) for p in files
                        if len(p.split("/")) > 2 and p.split("/")[1] in ("tests", "test")))
    if tdirs or nested:
        lines.append("test dirs: " + ", ".join((tdirs + nested)[:10]))
    big = []
    for p in files:
        try:
            s = os.path.getsize(str(repo.root / p))
        except OSError:
            continue
        if s > 2 * 1024 * 1024:
            big.append((s, p))
    if big:
        big.sort(reverse=True)
        lines.append("large tracked files (do not read): " + ", ".join("%s (%.0f MB)" % (p, s / 1e6)
                                                                       for s, p in big[:5]))
    return lines


def def_patterns(sym):
    s = re.escape(sym)
    return [
        re.compile(r"^\s*(?:async\s+)?def\s+%s\b" % s),
        re.compile(r"^\s*class\s+%s\b" % s),
        re.compile(r"^%s\s*(?::[^=]*)?=[^=]" % s),
        re.compile(r"^\s*(?:(?:pure|elemental|recursive|impure|module)\s+)*(?:subroutine|function|module|program|"
                   r"type|interface)\s*(?:,[^:]*::)?\s*%s\b" % s, re.I),
        re.compile(r"^\s*(?:[\w()*,=\s]+\s)?function\s+%s\b" % s, re.I),
        re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:default\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
                   r"(?:extern\s+\"[^\"]*\"\s+)?(?:fn|struct|enum|trait|type|mod|const|static|union)\s+%s\b" % s),
        re.compile(r"^\s*(?:unsafe\s+)?impl\b(?:\s*<[^>]*>)?\s+(?:[\w:<>,&' ]+\s+for\s+)?%s\b" % s),
        re.compile(r"^\s*macro_rules!\s*%s\b" % s),
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+%s\b" % s),
        re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?%s\b" % s),
        re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let|var|class|interface|type)\s+%s\b" % s),
        re.compile(r"^\s*(?:mutable\s+struct|struct|abstract\s+type|primitive\s+type|macro|module)\s+%s\b" % s),
        re.compile(r"^\s*%s\s*\([^)]*\)\s*=[^=]" % s),
        re.compile(r"^\s*(?:typedef\s+)?(?:struct|enum|union|namespace|class)\s+%s\b" % s),
        re.compile(r"^\s*#\s*define\s+%s\b" % s),
        re.compile(r"^[A-Za-z_][\w:<>,\s\*&]*?\b%s\s*\([^;]*$" % s),
    ]


# --------------------------------------------------------------------------- background jobs
#
# check, verify, run, baseline and golden add/update/check run in a detached worker process. The command that
# started the worker relays its output for up to `wait_secs` seconds and then returns, so neither a shell-tool
# timeout nor a session restart can kill a long verify, and `.sci/sci wait` picks the output up again later.

JOB = {}  # set in the worker: {"n", "repo", "data", "lock"}
JOB_RUNNING = 3  # exit code of a command whose background job is still running


def jobs_dir(repo):
    return repo.dir / "jobs"


def job_path(repo, n, ext="json"):
    return jobs_dir(repo) / ("%d.%s" % (n, ext))


def read_job(repo, n):
    try:
        with open(str(job_path(repo, n)), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def write_job(repo, n, data):
    with contextlib.suppress(OSError):
        atomic_write(job_path(repo, n), json.dumps(data), sync=False)


def job_numbers(repo):
    try:
        return sorted(int(p.stem) for p in jobs_dir(repo).glob("*.json") if p.stem.isdigit())
    except OSError:
        return []


def job_update(**kw):
    """Record the progress of this worker's job (no-op outside a worker)."""
    if not JOB:
        return
    with JOB["lock"]:
        JOB["data"].update(kw)
        write_job(JOB["repo"], JOB["n"], JOB["data"])


def job_step(label, i, n, est):
    job_update(step=label, step_i=i, step_n=n, step_t=time.time(), step_est=est)


def run_lock_busy(repo):
    """True while another process holds the run lock."""
    p = repo.dir / ".run.lock"
    if _RUN_LOCK_HELD or not p.exists():
        return False
    with file_lock(p, wait=0) as held:
        return not held


def job_alive(repo, j):
    if not j or j.get("state") == "done":
        return False
    if j.get("state") == "starting":
        return time.time() - float(j.get("t0") or 0) < 30
    return run_lock_busy(repo)


def running_job(repo):
    """The background job that is still running (never this worker's own), else None."""
    for n in reversed(job_numbers(repo)[-6:]):
        if JOB and JOB["n"] == n:
            continue
        j = read_job(repo, n)
        if job_alive(repo, j):
            return j
    return None


def starting_job(repo):
    """An earlier background job whose worker has not taken the run lock yet: it goes first."""
    for n in job_numbers(repo)[-6:]:
        if JOB and n >= JOB["n"]:
            continue
        j = read_job(repo, n)
        if j and j.get("state") == "starting" and time.time() - float(j.get("t0") or 0) < 30:
            return j
    return None


def mark_stale_jobs(repo):
    """Called with the run lock held: any other unfinished job lost its process."""
    for n in job_numbers(repo)[-6:]:
        j = read_job(repo, n)
        if j and j.get("state") == "running" and not (JOB and JOB["n"] == n):
            j.update(state="done", rc=1, interrupted=True)
            write_job(repo, n, j)


def job_progress(j):
    now = time.time()
    s = "job %s, %s so far" % (j.get("n"), fmt_secs(now - float(j.get("t0") or now)))
    if j.get("step"):
        k = "%s/%s " % (j.get("step_i"), j.get("step_n")) if (j.get("step_n") or 0) > 1 else ""
        s += "; now %s%s for %s" % (k, j["step"], fmt_secs(now - float(j.get("step_t") or now)))
        if j.get("step_est"):
            s += ", it took %s last time" % fmt_secs(j["step_est"])
    return s


def busy_text(repo):
    j = running_job(repo)
    if j:
        return ("%s is still running (%s)." % (j.get("label") or "A sci command", job_progress(j)),
                "Wait for it: `.sci/sci wait` (repeat until it finishes). Meanwhile read code or use note/hyp/now; "
                "do not edit files. To stop it: `.sci/sci cancel`.")
    return "Another sci command (check, verify, run, ...) is running in this repo.", "Wait for it to finish, then retry."


def detach_kwargs(breakaway=False):
    if os.name == "posix":
        return {"start_new_session": True}
    flags = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW (children keep a hidden console)
    return {"creationflags": flags | (0x01000000 if breakaway else 0)}  # CREATE_BREAKAWAY_FROM_JOB


def start_job(repo, argv, label):
    jd = jobs_dir(repo)
    jd.mkdir(parents=True, exist_ok=True)
    nums = job_numbers(repo)
    for old in nums[:-30]:
        for ext in ("json", "out", "seen"):
            with contextlib.suppress(OSError):
                job_path(repo, old, ext).unlink()
    n = (nums[-1] if nums else 0) + 1
    while True:
        try:
            fd = os.open(str(job_path(repo, n)), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            n += 1
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"n": n, "argv": argv, "label": label, "state": "starting", "t": now_iso(), "t0": time.time()}, f)
    open(str(job_path(repo, n, "out")), "wb").close()
    try:
        p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_spawn", str(n)] + argv,
                             cwd=os.getcwd(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **detach_kwargs())
        if p.wait(timeout=60) != 0:
            raise OSError("the worker did not start (exit %s)" % p.returncode)
    except (OSError, subprocess.SubprocessError):
        write_job(repo, n, {"n": n, "argv": argv, "label": label, "state": "done", "rc": 1, "t0": time.time()})
        raise
    return n


def spawn_worker(n, argv):
    """Runs in a short-lived middle process, so the worker is not a child of the command that waits for it: a shell
    tool that kills that command's process tree on a timeout cannot reach the worker."""
    repo = Repo(find_root())
    args = [sys.executable, str(Path(__file__).resolve()), "_job", str(n)] + argv
    with open(str(job_path(repo, n, "out")), "ab") as f:
        kw = dict(cwd=os.getcwd(), stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
        try:
            subprocess.Popen(args, **dict(kw, **detach_kwargs(breakaway=True)))
        except OSError:
            if os.name == "posix":
                raise
            subprocess.Popen(args, **dict(kw, **detach_kwargs()))  # the job object does not allow breakaway
    return 0


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def job_worker(n, argv):
    repo = Repo(find_root())
    JOB.update({"n": n, "repo": repo, "lock": threading.Lock(), "data": read_job(repo, n) or {"n": n, "t0": time.time()}})
    for s in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            s.reconfigure(line_buffering=True)
    if os.name == "posix":
        signal.signal(signal.SIGTERM, _raise_interrupt)  # `.sci/sci cancel`: stop the command, then exit
    stop = threading.Event()

    def beat():
        while not stop.wait(5):
            job_update(beat=time.time())
    threading.Thread(target=beat, daemon=True).start()
    rc = 1
    try:
        rc = dispatch(argv)
    finally:
        stop.set()
        for s in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                s.flush()
        job_update(state="done", rc=rc, ended=time.time())
    return rc


def job_label(repo, args):
    try:
        tid = (json.loads(read_text(repo.dir / "state.json") or "{}") or {}).get("active")
    except ValueError:
        tid = None
    c = command_name(args)
    if c == "run":
        return "run " + trunc(" ".join(args.command), 60)
    if c == "golden":
        return " ".join(["golden", args.action] + list(args.names or []))
    return c + (" " + tid if tid and c in ("check", "verify") else "")


def wait_done(repo, n, secs):
    end = time.time() + secs
    while time.time() < end:
        if (read_job(repo, n) or {}).get("state") == "done" and not run_lock_busy(repo):
            return True
        time.sleep(0.2)
    return False


def cancel_job(repo, n):
    j = read_job(repo, n) or {}
    pids = [int(x) for x in (j.get("pid"), j.get("child")) if x]
    if os.name == "posix":
        if j.get("pid"):
            with contextlib.suppress(OSError):
                os.killpg(int(j["pid"]), signal.SIGTERM)  # the worker stops its command and exits
        if not wait_done(repo, n, 10):
            for pid in pids:  # the worker and its command each lead their own process group
                with contextlib.suppress(OSError):
                    os.killpg(pid, signal.SIGKILL)
    else:
        for pid in pids:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30)
    wait_done(repo, n, 5)
    j = read_job(repo, n) or j
    if j.get("state") != "done":
        j.update(state="done", rc=130, cancelled=True)
        write_job(repo, n, j)


def job_seen(repo, n):
    try:
        return json.loads(read_text(job_path(repo, n, "seen")) or "{}")
    except ValueError:
        return {}


def job_interrupted(repo, n, j, left_at):
    j = dict(j, state="done", rc=1, interrupted=True)
    write_job(repo, n, j)
    print("INTERRUPTED: background job %d (%s) stopped before it finished: its process is gone (killed, or the "
          "machine slept or restarted)." % (n, j.get("label") or "?"))
    print("Run the command again. Steps that already passed on these files are reused.")
    beat = float(j.get("beat") or j.get("t0") or 0)
    if left_at and beat and beat - float(left_at) < 20:
        print("It died right after the command that started it returned, so this environment probably ends "
              "background processes. Run sci in the foreground: `.sci/sci config set background false` (and give "
              "your shell tool a long timeout).")
    return 1


def follow_job(repo, n, secs):
    """Relay a job's output until it finishes (exit with its code) or `secs` pass (exit JOB_RUNNING)."""
    out_p, seen_p = job_path(repo, n, "out"), job_path(repo, n, "seen")
    seen = job_seen(repo, n)
    cur = {"pos": int(seen.get("pos") or 0), "buf": b""}
    deadline = time.time() + max(secs, 0)

    def pull(final=False):
        try:
            with open(str(out_p), "rb") as f:
                f.seek(cur["pos"])
                data = f.read()
        except OSError:
            data = b""
        cur["pos"] += len(data)
        lines = (cur["buf"] + data).split(b"\n")
        cur["buf"] = b"" if final else lines.pop()
        if final and lines and not lines[-1]:
            lines.pop()
        for ln in lines:
            print(ln.decode("utf-8", "replace").rstrip("\r"), flush=True)
        with contextlib.suppress(OSError):
            atomic_write(seen_p, json.dumps({"pos": cur["pos"] - len(cur["buf"]), "until": time.time()}), sync=False)

    delay, gone = 0.05, None
    try:
        while True:
            pull()
            j = read_job(repo, n) or {}
            if j.get("state") == "done":
                pull(final=True)
                return int(j["rc"]) if isinstance(j.get("rc"), int) else 1
            if job_alive(repo, j):
                gone = None
            elif gone is None:
                gone = time.time()
            elif time.time() - gone > 3:
                pull(final=True)
                return job_interrupted(repo, n, j, seen.get("until"))
            if time.time() >= deadline:
                print("... still running: %s (%s)" % (j.get("label") or "job %d" % n, job_progress(j)))
                print("STILL RUNNING in the background. It finishes and records its result even if this session ends. "
                      "Meanwhile you can read code and use note/hyp/now. Do not edit files until it finishes.")
                print("[sci] NEXT: `.sci/sci wait` for the result (repeat until it finishes), or `.sci/sci cancel`.")
                return JOB_RUNNING
            time.sleep(delay)
            delay = min(delay * 1.5, 0.5)
    except KeyboardInterrupt:
        cancel_job(repo, n)
        print("Interrupted: stopped background job %d." % n)
        return 130


def job_state_lines(repo):
    """resume/status lines about a running or interrupted background job."""
    live = running_job(repo)
    if live:
        return ["RUNNING: %s (%s). `.sci/sci wait` shows its output; its result is saved when it finishes."
                % (live.get("label"), job_progress(live))], live
    nums = job_numbers(repo)
    j = read_job(repo, nums[-1]) if nums else None
    if j and j.get("state") != "done" and not job_alive(repo, j):
        j.update(state="done", rc=1, interrupted=True)
        write_job(repo, nums[-1], j)
    if j and j.get("interrupted") and time.time() - float(j.get("t0") or 0) < 86400:
        return ["INTERRUPTED: background job %d (%s) stopped before it finished. Run it again; steps that passed on "
                "these files are reused." % (nums[-1], j.get("label"))], None
    return [], None


def cmd_wait(args):
    repo = Repo(find_root())
    nums = job_numbers(repo)
    live = running_job(repo)
    n = int(str(args.job).lstrip("#")) if args.job else (live["n"] if live else (nums[-1] if nums else None))
    j = read_job(repo, n) if n is not None else None
    if not j:
        print("No background job has run in this repo yet.")
        return 0
    try:
        out_size = os.path.getsize(str(job_path(repo, n, "out")))
    except OSError:
        out_size = 0
    if j.get("state") == "done" and int(job_seen(repo, n).get("pos") or 0) >= out_size:
        rc = int(j["rc"]) if isinstance(j.get("rc"), int) else 1
        print("Job %d (%s) already finished (exit %d) and its output was shown. Its last lines:" % (
            n, j.get("label") or "?", rc))
        lines = read_log_lines(job_path(repo, n, "out"))
        while lines and not lines[-1].strip():
            lines.pop()
        for ln in lines[-30:]:
            print(ln)
        return rc
    bg = background_cfg()
    return follow_job(repo, n, args.secs if args.secs is not None else (bg[1] if bg else DEFAULT_CONFIG["wait_secs"]))


def cmd_cancel(args):
    repo = Repo(find_root())
    j = running_job(repo)
    if not j:
        print("No background job is running.")
        return 0
    cancel_job(repo, j["n"])
    print("Stopped job %d (%s). Its partial output: .sci/sci wait %d" % (j["n"], j.get("label") or "?", j["n"]))
    return 0


def background_cfg():
    """(enabled, wait_secs) from this repo's sci config, or None when sci is not set up here."""
    try:
        root = find_root()
    except Refuse:
        return None
    try:
        cfg = json.loads(read_text(root / SD / "config.json") or "null")
    except ValueError:
        cfg = None
    if not isinstance(cfg, dict) or not (root / SD / "state.json").exists():
        return None
    env = os.environ.get("SCI_BACKGROUND", "").strip().lower()
    on = cfg.get("background", True) if not env else env not in ("0", "false", "no", "off")
    try:
        secs = int(cfg.get("wait_secs") or DEFAULT_CONFIG["wait_secs"])
    except (TypeError, ValueError):
        secs = DEFAULT_CONFIG["wait_secs"]
    return bool(on), secs


def run_in_background(argv, args, wait_default):
    repo = Repo(find_root())
    if run_lock_busy(repo) or running_job(repo):
        refuse(*busy_text(repo))
    try:
        n = start_job(repo, argv, job_label(repo, args))
    except (OSError, subprocess.SubprocessError) as e:
        print("note: could not start a background worker (%s); running in the foreground." % e)
        with run_lock(repo):
            return int(args.fn(args) or 0)
    return follow_job(repo, n, args.wait if getattr(args, "wait", None) is not None else wait_default)


# --------------------------------------------------------------------------- commands

def out(lines):
    if isinstance(lines, str):
        lines = [lines]
    for ln in lines:
        print(ln)


def footer(store):
    st = store.st
    print(anchor_line(st))


def load_store():
    repo = Repo(find_root())
    _ROOT[:] = [repo.root]
    store = Store(repo)
    store.load()
    for w in store.warnings:
        print("WARNING: " + w)
    return store


def find_root():
    here = Path(__file__).resolve()
    if here.parent.name == SD:
        return here.parent.parent
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        refuse("Not inside a git repository (cwd: %s)." % os.getcwd(),
               "cd into the repository root first. sci needs git; if the project has no git repo, ask the human "
               "to run `git init && git add -A && git commit -m baseline`.")
    return Path(p.stdout.strip())


# Each interpreter is tried, because on Windows (Git Bash) python3/python may be Microsoft Store stubs.
SCI_WRAPPER = """#!/bin/sh
# sci launcher (generated). Usage: .sci/sci <command>   (see: .sci/sci help)
d=$(dirname "$0")
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
    exec "$py" "$d/sci.py" "$@"
  fi
done
if command -v py >/dev/null 2>&1; then exec py -3 "$d/sci.py" "$@"; fi
echo "sci: Python 3.8+ not found (tried python3, python, py -3)" >&2
exit 127
"""

# For cmd.exe and PowerShell, which find .sci/sci.cmd when told to run .sci/sci. The interpreter that ran
# `init` comes first. The last line runs sci and exits with an explicit code: a bare `exit /b` makes `cmd /c`
# (how PowerShell runs .cmd files) report 0. After `purge` this file is gone and cmd.exe would fail reading on
# ("The system cannot find the path specified."), so `(goto) 2>nul` ends the batch first and a child cmd sets the code.
SCI_CMD_WRAPPER = "\r\n".join([
    "@echo off",
    "rem sci launcher (generated). Usage: .sci\\sci COMMAND   (see: .sci\\sci help)",
    "setlocal",
    'set "SCI_PY=%s"',
    'set "SCI_PY_ARG="',
    'if exist "%%SCI_PY%%" goto run',
    'set "SCI_PY=python"',
    "where py >nul 2>nul",
    'if not errorlevel 1 (set "SCI_PY=py" & set "SCI_PY_ARG=-3")',
    ":run",
    '"%%SCI_PY%%" %%SCI_PY_ARG%% "%%~dp0sci.py" %%* & if not exist "%%~dp0sci.py" (if errorlevel 1 '
    '((goto) 2>nul & cmd /d /c exit 1) else ((goto) 2>nul & cmd /d /c exit 0)) else if errorlevel 130 '
    '(exit /b 130) else if errorlevel 2 (exit /b 2) else if errorlevel 1 (exit /b 1) else if not errorlevel 0 '
    '(exit /b 1) else (exit /b 0)',
    ""])


def vendor_self(repo):
    """Copy this script into .sci/ so `.sci/sci` works without knowing the skill directory."""
    src = Path(__file__).resolve()
    dst = repo.dir / "sci.py"
    changed = False
    same_file = dst.exists() and dst.resolve() == src
    if not same_file and (not dst.exists() or read_text(dst) != read_text(src)):
        shutil.copyfile(str(src), str(dst))
        changed = True
    w = repo.dir / "sci"
    if read_text(w) != SCI_WRAPPER:
        atomic_write(w, SCI_WRAPPER)
        changed = True
    os.chmod(str(w), 0o755)
    wc = repo.dir / "sci.cmd"
    launcher = SCI_CMD_WRAPPER % sys.executable.replace("%", "%%")
    if read_text(wc) != launcher:
        atomic_write(wc, launcher)
    return changed


def ensure_excluded(repo):
    ex = repo.git_path("info/exclude")
    text = read_text(ex) or ""
    if not re.search(r"(?m)^/?\.sci/?$", text):
        ex.parent.mkdir(parents=True, exist_ok=True)
        with open(str(ex), "a", encoding="utf-8", newline="\n") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            f.write("# sci harness state (local only)\n/.sci/\n")


def import_v2(repo, st):
    p = repo.root / ".agent" / "feature_list.json"
    if not p.exists():
        return 0
    try:
        data = json.loads(read_text(p) or "{}")
    except ValueError:
        return 0
    idmap = {}
    feats = data.get("features") or []
    for f in feats:
        idmap[f.get("id")] = "T%d" % (st["next_task"] + len(idmap))
    for f in feats:
        status = {"passing": "done", "blocked": "blocked"}.get(f.get("status"), "todo")
        t = {"id": idmap[f.get("id")], "title": "%s (%s)" % (f.get("title") or f.get("id"), f.get("id")),
             "verify": f.get("verification_cmd") or "", "scope": [], "invariants": list(f.get("invariants") or []),
             "deps": [idmap[d] for d in f.get("dependencies") or [] if d in idmap], "status": status,
             "why": f.get("description") or "", "created": now_iso()}
        t["spec_hash"] = spec_hash(t)
        st["tasks"].append(upgrade_task(t))
    st["next_task"] += len(feats)
    return len(feats)


def cmd_init(args):
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        refuse("Not inside a git repository (cwd: %s)." % os.getcwd(),
               "cd into the repository root. If the project has no git repo, ask the human to run "
               "`git init && git add -A && git commit -m baseline` first.")
    repo = Repo(p.stdout.strip())
    store = Store(repo)
    ver = re.search(r"(\d+)\.(\d+)", git(repo.root, ["--version"]).stdout)
    if ver and (int(ver.group(1)), int(ver.group(2))) < (2, 23):
        print("WARNING: git %s.%s is old; `.sci/sci restore` needs git >= 2.23." % (ver.group(1), ver.group(2)))
    with state_lock(repo):
        ensure_excluded(repo)
        for d in ("scratch", "runs", "attempts", "golden", "patches", "protected"):
            (repo.dir / d).mkdir(parents=True, exist_ok=True)
        if store.exists() or (repo.backup_dir / "state.json").exists():
            store.load()
            restored = bool(store.warnings)
            for w in store.warnings:
                print("WARNING: " + w)
            if args.objective and not store.st.get("objective"):
                store.st["objective"] = args.objective
            upgraded = vendor_self(repo)
            store.log("INIT_AGAIN", msg="restored" if restored else "")
            store.save()
            print("sci already initialized in %s%s." % (repo.root, " (state restored from backup)" if restored else ""))
            if upgraded:
                print("Updated .sci/sci.py to sci %s." % VERSION)
            print("Run: .sci/sci resume")
            return 0
        st = new_state(args.objective)
        cfg = dict(DEFAULT_CONFIG)
        cmd, notes = detect_toolchain(repo.root)
        cfg["test_cmd"] = args.test_cmd or cmd
        if args.regress_cmd:
            cfg["regress_cmd"] = args.regress_cmd
        n_imp = import_v2(repo, st)
        store.st, store.cfg = st, cfg
        vendor_self(repo)
        mp = repo_map(repo)
        atomic_write(repo.dir / "map.txt", "\n".join(mp) + "\n")
        store.log("INIT", msg=args.objective or "", data_version=VERSION)
        store.save()
    print("sci %s initialized in %s" % (VERSION, repo.root))
    print("  state: .sci/ (git-ignored via .git/info/exclude; never commit it)")
    print("  tool : .sci/sci <command>   (use this path from now on%s)" % ("; in cmd.exe: .sci\\sci" if WINDOWS else ""))
    print("  test_cmd: %s" % (cfg["test_cmd"] or "(none detected: .sci/sci config set test_cmd \"<cmd>\")"))
    if WINDOWS:
        print("  shell: sci runs check/verify/golden commands with %s%s" % (
            shell_label(cfg), " (use forward slashes and bash quoting)" if uses_bash(cfg) else ""))
    for n in notes:
        print("  note: " + n)
    if n_imp:
        print("  imported %d tasks from the old .agent/feature_list.json. The old .agent/ folder is read by some "
              "agent tools as a customization folder; ask the human whether to delete it." % n_imp)
    br = git(repo.root, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout.strip()
    if br in ("main", "master"):
        print("  note: you are on %s. Unless the human said otherwise, work on a branch: git switch -c sci/<topic>" % br)
    dirty = dirty_tracked(repo, cfg) if head_sha(repo) else []
    if dirty:
        print("  WARNING: %d files have uncommitted changes from before sci (%s). Ask the human to commit or stash "
              "them before you start tasks." % (len(dirty), ", ".join(dirty[:5])))
    print("Repo map saved to .sci/map.txt (view: .sci/sci map).")
    print("NEXT: " + store.st["next"])
    return 0


def installed_newer():
    """(installed version, this version, installed sci.py) when the installed skill is newer than this copy."""
    src = (Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "agent-skills" / "sci"
           / "scripts" / "sci.py")
    try:
        with open(str(src), encoding="utf-8", errors="replace") as f:
            m = re.search(r'(?m)^VERSION = "(\d+(?:\.\d+)*)"', f.read(8192))
        if not m or src.resolve() == Path(__file__).resolve():
            return None
    except OSError:
        return None
    ver = lambda v: tuple(int(x) for x in v.split("."))  # noqa: E731
    return (m.group(1), VERSION, str(src)) if ver(m.group(1)) > ver(VERSION) else None


def cmd_resume(args):
    store = load_store()
    repo = store.repo
    live_tree = None
    with state_lock(repo, required=False) as held:
        st, cfg = store.load()
        t = active_task(st)
        if st["tasks"] and not args.brief:
            live_tree = snapshot(repo, cfg)[0]
        if held and not args.brief:
            st["session"] = int(st.get("session") or 0) + 1
            store.log("RESUME", task=st.get("active"), msg="session %d" % st["session"])
            store.save(live_tree)
    job_lines, live = job_state_lines(repo)
    live_ok = still_verified(store, live_tree) if live_tree else None
    st["next"] = compute_next(st, cfg, live_tree, live_ok, live)
    if not held:
        print("note: another sci command is saving its state; showing the last saved state.")
    if args.brief:
        print(anchor_block(st))
        return 0
    br = git(repo.root, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout.strip() or "?"
    h = head_sha(repo)
    status = git(repo.root, ["status", "--porcelain=v1", "-z", "--", ".", ":(exclude)" + SD], check=False).stdout
    ndirty = len([x for x in status.split("\0") if len(x) > 3 and x[2] == " "])
    c = task_counts(st)
    L = ["=== .sci/sci resume | session %d | %s @ %s | %d changed/untracked files | sci %s ===" % (
        st["session"], br, h[:8] if h else "(no commits)", ndirty, VERSION)]
    newer = installed_newer()
    if newer:
        L.append("note: sci %s is installed; this project still runs %s. Update its copy (the state is kept): "
                 "python3 %s init" % newer)
    L.append("OBJECTIVE: " + (st.get("objective") or "(not set)"))
    L.append("PROGRESS: %d/%d done, %d blocked, %d todo" % (c["done"], len(st["tasks"]), c["blocked"], c["todo"]))
    mark = {"done": "[x]", "active": "[>]", "blocked": "[!]", "todo": "[ ]"}
    shown = st["tasks"]
    if len(shown) > 14:
        idx = [i for i, x in enumerate(shown) if x["status"] != "done"]
        first = max(0, (idx[0] if idx else len(shown)) - 2)
        shown = shown[first:first + 14]
        if first:
            L.append("  ... (%d earlier tasks done)" % first)
    for x in shown:
        tag = "  <- ACTIVE" if x["status"] == "active" else (
            "  BLOCKED: " + trunc(x.get("block_reason", ""), 70) if x["status"] == "blocked" else "")
        L.append("  %s %s %s%s" % (mark.get(x["status"], "[?]"), x["id"], trunc(x["title"], 80), tag))
    if t:
        L.append("ACTIVE %s: %s" % (t["id"], t["title"]))
        if t.get("why"):
            L.append("  why: " + trunc(t["why"], 200))
        L.append("  verify: " + t["verify"])
        L.append("  scope : " + (", ".join(t["scope"]) or "(none: keep changes minimal)"))
        if t["invariants"]:
            L.append("  invariants: " + "; ".join(t["invariants"]))
        L.append("  locked: " + lock_desc(t))
        if t.get("unlocked"):
            L.append("  unlocked for this task (the human reviews them): " + ", ".join(t["unlocked"]))
        lv = "never"
        if t.get("verified_tree"):
            lv = ("on current files" if live_tree == t["verified_tree"] else
                  "on current code (only docs, comments or layout changed since)" if live_ok else
                  "STALE (files changed since)")
        L.append("  last check: %s | strikes %d/3 | verify: %s" % (check_state(t), t.get("strikes", 0), lv))
        if t.get("last_check") and not t["last_check"].get("ok") and t["last_check"].get("failing"):
            L.append("  failing: " + ", ".join(t["last_check"]["failing"][:6]))
        if st.get("now") and st["now"].get("task") == t["id"]:
            L.append("  doing (%s): %s" % (ago(st["now"]["t"]), st["now"]["text"]))
    ref = hyps_for(st, t["id"] if t else None, "refuted") if t else hyps_for(st, None, "refuted")[-5:]
    if ref:
        L.append("RULED OUT (tested and false; do not re-test):")
        L += ["  %s: %s -- %s" % (x["id"], trunc(x["text"], 110), trunc(x.get("evidence", ""), 90)) for x in ref[-8:]]
    opn = hyps_for(st, t["id"], "open") if t else []
    conf = hyps_for(st, t["id"], "confirmed") if t else []
    if conf:
        L += ["CONFIRMED: %s: %s" % (x["id"], trunc(x["text"], 150)) for x in conf[-3:]]
    if opn:
        L += ["OPEN HYPOTHESES: " + "; ".join("%s %s" % (x["id"], trunc(x["text"], 60)) for x in opn[-4:])]
    notes = [n for n in st["notes"] if not n.get("dropped")]
    if notes:
        L.append("NOTES (%d; all in .sci/HANDOFF.md):" % len(notes))
        pri = [n for n in notes if n["kind"] in ("units", "decision", "gotcha", "env")]
        rest = [n for n in notes if n not in pri]
        for n in (pri + rest[::-1])[:14]:
            L.append("  N%d [%s] %s" % (n["id"], n["kind"], trunc(n["text"], 150)))
    if st["goldens"]:
        L.append("GOLDENS: " + " | ".join("%s %s" % (k, ("OK" if (g.get("last") or {}).get("ok") else
                                                           ("FAIL" if g.get("last") else "unchecked")))
                                          for k, g in sorted(st["goldens"].items())))
    if st.get("baseline"):
        L.append("BASELINE: %d known pre-existing failures (not your job unless a task says so)" %
                 len(st["baseline"].get("failures") or []))
    if st["review"]:
        L.append("NEEDS HUMAN REVIEW: %d item(s) (.sci/sci review)" % len(st["review"]))
    if st.get("violations"):
        L.append("GUARD: " + " | ".join(trunc(fmt_viol(v), 140) for v in st["violations"][:3]))
    if st.get("paused"):
        L.append("PAUSED: the harness is paused.")
    L += job_lines
    L.append("NEXT: " + st["next"])
    out([x for x in L if x is not None])
    return 0


def cmd_objective(args):
    store = load_store()
    with locked(store) as (st, _):
        if st.get("objective") and st["objective"] != args.text:
            st["objective_history"].append({"t": now_iso(), "text": st["objective"]})
        st["objective"] = args.text
        store.log("OBJECTIVE", msg=args.text)
        store.save()
    print("Objective set.")
    footer(store)
    return 0


WHOLE_SUITE_RE = re.compile(r"^\s*(?:(?:\S*python(?:3[\d.]*)?(?:\.exe)?|py(?:\s+-3[\d.]*)?)\s+-m\s+)?"
                            r"(?:pytest|py\.test)(?:\s+-{1,2}[\w=-]+)*\s*$|"
                            r"^\s*(?:make\s+(?:test|check)|npm\s+test.*|cargo\s+test(?:\s+-q)?|go\s+test\s+\./\.\.\.)\s*$")


def cmd_task_add(args):
    store = load_store()
    st, cfg = store.st, store.cfg
    title = args.title or args.title_opt
    if not title:
        refuse("Missing task title.", '.sci/sci task add "<title>" --verify "<cmd>"')
    if not args.verify:
        refuse("Every task needs --verify: a command that passes only when the task is done (usually targeted tests).",
               '.sci/sci task add "%s" --verify "python -m pytest tests/test_x.py -q" --scope src/x.py' % trunc(title, 40))
    deps = []
    for d in args.dep or []:
        for x in re.split(r"[,\s]+", d):
            if x:
                deps.append(need_task(st, x)["id"])
    with locked(store) as (st, cfg):
        tid = "T%d" % st["next_task"]
        st["next_task"] += 1
        t = {"id": tid, "title": title.strip(), "verify": args.verify.strip(), "scope": split_scope(args.scope),
             "invariants": [s.strip() for s in args.inv or []], "deps": deps, "status": "todo",
             "why": (args.why or "").strip(), "created": now_iso()}
        t["spec_hash"] = spec_hash(t)
        st["tasks"].append(upgrade_task(t))
        store.log("TASK_ADD", task=tid, msg=title, spec_hash=t["spec_hash"], verify=t["verify"])
        store.save()
    print("Added %s: %s" % (tid, title))
    if WHOLE_SUITE_RE.match(args.verify) or args.verify.strip() == (cfg.get("test_cmd") or "").strip():
        print("  WARNING: --verify runs a whole test suite. Target this task's tests (a file or test id) so checks "
              "stay fast and meaningful: .sci/sci task amend %s --verify \"...\" --reason \"narrow\"" % tid)
    if not t["scope"]:
        print("  WARNING: no --scope. Scope keeps edits surgical; add it now: .sci/sci task amend %s --scope <path> "
              "--reason \"scope\"" % tid)
    footer(store)
    return 0


def task_card(t):
    L = ["%s [%s] %s" % (t["id"], t["status"], t["title"])]
    if t.get("why"):
        L.append("  why: " + t["why"])
    L.append("  verify: " + t["verify"])
    L.append("  scope : " + (", ".join(t["scope"]) or "(none)"))
    if t["invariants"]:
        L.append("  invariants: " + "; ".join(t["invariants"]))
    if t["deps"]:
        L.append("  deps: " + ", ".join(t["deps"]))
    if is_locked(t):
        L.append("  locked: " + lock_desc(t))
    if t.get("block_reason") and t["status"] == "blocked":
        L.append("  blocked: " + t["block_reason"])
    if t.get("commit"):
        L.append("  commit: " + t["commit"])
    return L


def cmd_task_list(args):
    store = load_store()
    st = store.st
    if not st["tasks"]:
        print("No tasks yet.")
    for t in st["tasks"]:
        out("%-4s %-8s %s%s" % (t["id"], t["status"], trunc(t["title"], 90),
                                (" (deps %s)" % ",".join(t["deps"])) if t["deps"] and t["status"] == "todo" else ""))
    footer(store)
    return 0


def cmd_task_show(args):
    store = load_store()
    t = need_task(store.st, args.id) if args.id else require_active(store.st)
    out(task_card(t))
    return 0


def cmd_task_start(args):
    store = load_store()
    repo = store.repo
    with locked(store) as (st, cfg):
        cur = active_task(st)
        t = need_task(st, args.id) if args.id else (cur or next_eligible(st))
        if t is None:
            refuse("No eligible task to start.", ".sci/sci task list  (add tasks with .sci/sci task add, or unblock one)")
        if cur and cur["id"] != t["id"]:
            refuse("%s is already active. One task at a time." % cur["id"],
                   "Finish it (.sci/sci verify, then .sci/sci done -m ...) or park it: .sci/sci task block %s --reason \"...\"" % cur["id"])
        if cur and cur["id"] == t["id"]:
            out(task_card(t))
            print("Already active.")
            footer(store)
            return 0
        if t["status"] == "done":
            refuse("%s is already done." % t["id"], ".sci/sci task list")
        missing = [d for d in t["deps"] if find_task(st, d)["status"] != "done"]
        if missing:
            refuse("%s depends on unfinished %s." % (t["id"], ", ".join(missing)), "Start %s first." % missing[0])
        tree, big = snapshot(repo, cfg)
        if t["status"] == "blocked":
            store.log("TASK_UNBLOCK", task=t["id"], msg="started")
        t.update({"status": "active", "started": now_iso(), "start_tree": tree, "start_head": head_sha(repo),
                  "preexisting_untracked": untracked_list(repo, cfg), "strikes": 0, "stop": None, "stop_cycles": 0,
                  "resets": 0, "was_locked": [],
                  "last_check": None, "verified_tree": None, "verified_full": False, "last_green_tree": None,
                  "locked": {}, "lock_none": None, "unlocked": [], "locked_regions": {}, "red": False,
                  "pending": None})
        st["active"] = t["id"]
        st["violations"] = []
        notes = unignore_scoped(store, t)
        if notes and any("no longer ignores" in n for n in notes):
            tree, big = snapshot(repo, cfg)
            t.update({"start_tree": tree, "preexisting_untracked": untracked_list(repo, cfg)})
        set_ref(repo, t["id"] + "/start", tree, "sci %s start" % t["id"])
        dirty = dirty_tracked(repo, cfg) if head_sha(repo) else []
        t["preexisting_dirty"] = dirty
        store.log("TASK_START", task=t["id"], msg=t["title"], tree=tree)
        store.save()
    out(task_card(t))
    out(notes)
    if dirty:
        print("  WARNING: uncommitted changes from before this task: %s. Avoid editing them; `.sci/sci done` commits whole "
              "files." % ", ".join(dirty[:6]))
    refuted = hyps_for(st, None, "refuted")
    if refuted:
        print("  Ruled out earlier (tested and false; do not re-test): " +
              "; ".join(trunc(h["text"], 60) for h in refuted[-4:]))
    footer(store)
    return 0


def save_attempt_patch(repo, t, a, b, label):
    try:
        diff = git(repo.root, ["diff", "--binary", "--no-color", "--no-ext-diff", a, b]).stdout
    except Refuse:
        return None
    if not diff.strip():
        return None
    d = repo.dir / "attempts"
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("%s-%s.patch" % (t["id"], label))
    atomic_write(path, diff)
    pats = sorted(d.glob(t["id"] + "-*.patch"), key=lambda p: p.stat().st_mtime)
    for stale in pats[:-25]:
        with contextlib.suppress(OSError):
            stale.unlink()
    return path


def cmd_task_block(args):
    store = load_store()
    repo = store.repo
    with locked(store) as (st, cfg):
        t = need_task(st, args.id) if args.id else require_active(st)
        if t["status"] == "done":
            refuse("%s is done; nothing to block." % t["id"])
        if st.get("active") == t["id"]:
            tree, _ = snapshot(repo, cfg)
            p = save_attempt_patch(repo, t, t["start_tree"], tree, "parked-%d" % int(time.time()))
            if p:
                print("Saved %s's current changes to %s (they stay in the working tree)." % (t["id"], repo.rel(p)))
            st["active"] = None
        t["status"] = "blocked"
        t["block_reason"] = args.reason
        store.log("TASK_BLOCK", task=t["id"], msg=args.reason)
        store.save()
    print("%s blocked: %s" % (t["id"], args.reason))
    print("Ask the human this question in your reply. Meanwhile continue with another eligible task if there is one.")
    footer(store)
    return 0


def cmd_task_unblock(args):
    store = load_store()
    with locked(store) as (st, _):
        t = need_task(st, args.id)
        if t["status"] != "blocked":
            refuse("%s is not blocked." % t["id"])
        t["status"] = "todo"
        if args.answer:
            st["notes"].append({"id": st["next_note"], "t": now_iso(), "kind": "decision", "task": t["id"],
                                "text": "Answer to '%s': %s" % (trunc(t.get("block_reason", ""), 80), args.answer)})
            st["next_note"] += 1
        store.log("TASK_UNBLOCK", task=t["id"], msg=args.answer or "")
        store.save()
    print("%s unblocked." % t["id"])
    footer(store)
    return 0


def cmd_task_amend(args):
    store = load_store()
    if not args.reason or len(args.reason.strip()) < 4:
        refuse("Amending a task needs --reason.", ".sci/sci task amend %s ... --reason \"<why>\"" % args.id)
    changes = []
    with locked(store) as (st, _):
        t = need_task(st, args.id)
        if args.title:
            changes.append("title")
            t["title"] = args.title
        if args.verify:
            changes.append("verify: %s -> %s" % (t["verify"], args.verify))
            t["verify"] = args.verify
        if args.inv:
            changes.append("invariants added: " + "; ".join(args.inv))
            t["invariants"] += args.inv
        if args.drop_inv:
            for i in args.drop_inv:
                if i in t["invariants"]:
                    t["invariants"].remove(i)
                    changes.append("invariant dropped: " + i)
        notes = []
        if args.scope:
            t["scope"] += [s for s in split_scope(args.scope) if s not in t["scope"]]
            changes.append("scope += " + ", ".join(split_scope(args.scope)))
            if t["status"] == "active":
                notes = unignore_scoped(store, t)
        if args.dep:
            for d in args.dep:
                did = need_task(st, d)["id"]
                if did not in t["deps"] and did != t["id"]:
                    t["deps"].append(did)
            changes.append("deps = " + ", ".join(t["deps"]))
        if not changes:
            refuse("Nothing to amend.", "Pass --title/--verify/--inv/--drop-inv/--scope/--dep")
        spec_changed = any(c.startswith(("verify", "invariant", "title")) for c in changes)
        t["spec_hash"] = spec_hash(t)
        if spec_changed and (t["status"] in ("active", "done") or t.get("last_check")):
            add_review(st, t["id"], "spec-change", "; ".join(changes) + " | reason: " + args.reason)
        if st.get("violations"):
            st["violations"] = [v for v in st["violations"] if not fmt_viol(v).startswith("SPEC CHANGED")]
        store.log("TASK_AMEND", task=t["id"], msg=args.reason, changes=changes, spec_hash=t["spec_hash"])
        store.save()
    print("Amended %s: %s" % (t["id"], "; ".join(changes)))
    out(notes)
    footer(store)
    return 0


def split_scope(items):
    """Scope globs from repeated or comma-separated arguments (`--scope a.py,b.py` is two entries)."""
    out = []
    for s in items or []:
        out += [x.strip() for x in s.split(",") if x.strip() and x.strip() not in out]
    return out


def cmd_task_scope(args):
    store = load_store()
    with locked(store) as (st, _):
        t = require_active(st)
        added = [s for s in split_scope(args.globs) if s not in t["scope"]]
        t["scope"] += added
        store.log("TASK_SCOPE", task=t["id"], msg=", ".join(added))
        if added and t.get("last_check"):
            add_review(st, t["id"], "scope", "scope widened mid-task: " + ", ".join(added))
        notes = unignore_scoped(store, t) if added else []
        store.save()
    print("%s scope: %s" % (t["id"], ", ".join(t["scope"])))
    out(notes)
    footer(store)
    return 0


SRC_EXT_RE = re.compile(r"\.(?:py|pyx|jl|[rR]|m|sh|bash|c|cc|cpp|cxx|h|hpp|cu|f|for|f90|F90|f95|rs|go|js|mjs|cjs|"
                        r"jsx|ts|tsx|mts|cts|vue|svelte|java|kt|kts|scala|cs|fs|swift|dart|php|ex|exs|hs|ml|zig|rb|"
                        r"pl|lua|ipynb|bats|ps1|groovy|clj|cljs|erl|nim|cr)$")
RUNNER_RE = re.compile(r"(?:^|[\s;&|(/])(?:pytest|py\.test|unittest|nose2|nosetests|tox|nox|ctest|jest|vitest|mocha|"
                       r"rspec|phpunit|runtests|testthat|go\s+test|cargo\s+(?:test|nextest)|make\s+(?:test|check)|"
                       r"(?:npm|yarn|pnpm)\s+(?:run\s+)?test|meson\s+test|dotnet\s+test|mvnw?\s+test|gradlew?\s+test|"
                       r"(?:bun|deno|swift|mix|cabal|stack)\s+test|zig\s+build\s+test|dune\s+(?:test|runtest))\b")


def verify_tokens(cmd):
    """Shell words of a command, with quoted sub-commands (sh -c "...") split one level further."""
    if WINDOWS:
        cmd = cmd.replace("\\", "/")  # tests\test_x.py; shlex would read the backslashes as escapes
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    res = []
    for tok in toks:
        if any(ch.isspace() for ch in tok):
            try:
                res += shlex.split(tok)
            except ValueError:
                res += tok.split()
        else:
            res.append(tok)
    return res


def lock_candidates(repo, cfg, t, cur_tree):
    """Files that define the task's acceptance. Returns (files, missing).
    files: test files named in the verify command or inside directories it names, tests changed in this task,
    and non-test scripts it names that lie outside a non-empty task scope. Files the task is meant to change
    (in scope, not tests), untracked files (interpreters, venvs) and ignored files are never locked.
    missing: files the verify command names that do not exist yet."""
    changed = [p for s_, p in tree_changes(repo, t["start_tree"], cur_tree) if s_ != "D"] if t.get("start_tree") else []
    allowed = tracked_set(repo) | set(changed)
    cands, missing, prev = [], [], ""
    for raw in verify_tokens(t["verify"]):
        tok, after_cd, prev = raw.split("::")[0].split("[")[0], prev == "cd", raw
        if not tok or after_cd or tok.startswith("-") or os.path.isabs(tok):
            continue
        dotted = bool(re.match(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$", tok)) and not (repo.root / tok).exists()
        if dotted:
            parts = tok.split(".")
            for k in range(len(parts), 0, -1):
                mod = "/".join(parts[:k]) + ".py"
                if (repo.root / mod).is_file():
                    tok = mod
                    break
        rel = Path(os.path.normpath(tok)).as_posix()
        if rel == ".." or rel.startswith("../"):
            continue
        p = repo.root / rel
        if p.is_file():
            if rel not in allowed or match_any(rel, cfg.get("ignore_globs")):
                continue
            if is_test(rel, cfg) or (t["scope"] and not match_any(rel, t["scope"])):
                cands.append(rel)
        elif p.is_dir():
            inside = [c for c in changed if c.startswith(rel.rstrip("/") + "/") and is_test(c, cfg)]
            if not inside:
                inside = [x for x in git(repo.root, ["ls-files", "-z", "--", rel]).stdout.split("\0")
                          if x and is_test(x, cfg)][:60]
            cands += inside
        elif dotted and (repo.root / tok.split(".")[0]).is_dir():
            missing.append(tok)
        elif re.match(r"^[\w.+-]+(?:/[\w.+-]+)+$", rel) or (re.match(r"^[\w.+-]+$", rel) and SRC_EXT_RE.search(rel)):
            missing.append(rel)
    cands += [c for c in changed if is_test(c, cfg)]
    cands += rust_lock_candidates(repo, cfg, t, changed)
    seen, res = set(), []
    for c in cands:
        if c not in seen and (repo.root / c).is_file():
            seen.add(c)
            res.append(c)
    return res, missing


def region_lockable(repo, cfg, p):
    """Whether `p` is source code with tests inside (Rust): lock its tests, not the whole file."""
    return has_inline_tests(p) and not is_test(p, cfg) and bool(test_region_text(p, read_text(repo.root / p)))


def rust_lock_candidates(repo, cfg, t, changed):
    """Rust files whose tests define a task's acceptance: source files whose inline tests changed in this task,
    integration tests named by `cargo test --test NAME`, and the module files a test-name filter names
    (`cargo test parser::tests::roundtrip` -> src/parser.rs or src/parser/mod.rs)."""
    words = cmd_words(t.get("verify") or "") or []
    prog, rest = _runner_word(words)
    res = []
    for p in changed:
        if has_inline_tests(p) and not is_test(p, cfg):
            old = test_region_text(p, blob_text(repo, t["start_tree"], p)) if t.get("start_tree") else ""
            if test_region_text(p, read_text(repo.root / p)) not in ("", old):
                res.append(p)
    if prog != "cargo" or rest[:1] not in (["test"], ["nextest"]):
        return res
    files = [x for x in tracked_set(repo) | set(changed) if x.endswith(".rs")]
    filters, i = [], 1
    while i < len(rest):
        w = rest[i]
        if w in ("--test", "--bin", "--example", "--bench", "-p", "--package", "--features", "-F", "--manifest-path",
                 "-j", "--jobs", "--target", "--profile", "--exclude", "--skip") and i + 1 < len(rest):
            if w == "--test":
                res += [f for f in files if re.search(r"(?:^|/)tests/%s(?:\.rs|/main\.rs)$" % re.escape(rest[i + 1]), f)]
            i += 2
            continue
        if not w.startswith("-"):
            filters.append(w)
        i += 1
    for f in filters:
        parts = [x for x in f.split("::") if x]
        mods = parts[:parts.index("tests")] if "tests" in parts else parts[:-1]
        if not mods:
            if parts[:1] == ["tests"]:  # tests::x: the crate root's own tests
                res += [x for x in files if x in ("src/lib.rs", "src/main.rs")]
            continue
        tail = "/".join(mods)
        res += [x for x in files if x.endswith("/" + tail + ".rs") or x.endswith("/" + tail + "/mod.rs")
                or (x in ("src/%s.rs" % tail, "src/%s/mod.rs" % tail))]
    return res


def cmd_lock(args):
    store = load_store()
    repo = store.repo
    none_reason = None
    with locked(store) as (st, cfg):
        t = require_active(st)
        if args.none:
            if len(args.none.strip()) < 10:
                refuse("`--none` needs a real reason (it is shown to the human).",
                       ".sci/sci lock --none \"<why this task has no test file to lock>\"")
            paths, none_reason = [], args.none.strip()
        elif args.paths:
            paths = [norm_rel(repo.root, p) for p in args.paths]
        else:
            paths, missing = lock_candidates(repo, cfg, t, snapshot(repo, cfg)[0])
            if not paths and missing:
                refuse("The verify command names files that do not exist yet: %s" % ", ".join(missing[:5]),
                       "Write the acceptance test first and `.sci/sci check` it (FAIL expected), then `.sci/sci lock`. "
                       "If other files hold the tests, name them: .sci/sci lock <files>")
            if not paths and RUNNER_RE.search(" " + t["verify"]):
                refuse("The verify command runs tests, but no test file was found in it or among this task's changes.",
                       "Name the files that hold the acceptance tests: .sci/sci lock tests/test_x.py [more]. If the task "
                       "adds no test (e.g. a refactor guarded by existing tests): .sci/sci lock --none \"<why>\"")
            if not paths:
                none_reason = "verify command names no test file"
        for p in paths:
            if not (repo.root / p).is_file():
                refuse("Not a file: %s" % p)
            if region_lockable(repo, cfg, p):
                region = test_region_text(p, read_text(repo.root / p))
                t["locked_regions"][p] = sha256_text(region)
                store_protected_text(repo, region)
            else:
                t["locked"][p] = file_sha(repo.root / p)
                store_protected_copy(repo, repo.root / p, t["locked"][p])
            if p in t["unlocked"]:
                t["unlocked"].remove(p)
        if paths:
            t["lock_none"] = None
        if none_reason and not (t["locked"] or t["locked_regions"]):
            t["lock_none"] = none_reason
            add_review(st, t["id"], "no-lock", "nothing locked (%s); acceptance rests on `%s` alone"
                       % (none_reason, t["verify"]))
        lc = t.get("last_check")
        t["red"] = bool(lc and not lc.get("ok"))
        store.log("LOCK", task=t["id"], msg=", ".join(paths) or "(none: %s)" % none_reason)
        store.save()
    if not paths:
        print("Nothing to lock for %s: %s. The verify command `%s` is the acceptance check; changing it needs "
              "`.sci/sci task amend` (flagged for review)." % (t["id"], none_reason, trunc(t["verify"], 100)))
        if t["scope"]:
            print("Files in the task scope (%s) are what the task changes, so they are never locked."
                  % trunc(", ".join(t["scope"]), 80))
    else:
        print("Locked for %s: %s" % (t["id"], lock_desc(t)))
        if t["locked_regions"]:
            print("In %s only the tests (#[cfg(test)] modules, #[test] functions) are locked; edit the code around "
                  "them freely. New tests may be added." % ", ".join(sorted(t["locked_regions"])))
        print("Do not edit locked tests. Implement in small steps and run `.sci/sci check` after each.")
    if not t["red"]:
        print("  note: no failing check was seen before locking. For a behavior change the new test should FAIL "
              "first (RED); for a refactor PASS is expected.")
    footer(store)
    return 0


def cmd_unlock(args):
    store = load_store()
    repo = store.repo
    if not args.reason or len(args.reason.strip()) < 10:
        refuse("Unlocking needs a real --reason (it is shown to the human).",
               ".sci/sci unlock <file> --reason \"<why the test itself is wrong>\"")
    with locked(store) as (st, _):
        t = active_task(st)
        paths = [norm_rel(repo.root, p) for p in args.paths] if args.paths else (
            sorted(set(t["locked"]) | set(t.get("locked_regions") or {})) if t else [])
        if not paths:
            refuse("Nothing to unlock.", ".sci/sci unlock <file> --reason \"...\"")
        done_paths, relock = [], []
        for p in paths:
            cur = file_sha(repo.root / p)
            if t:
                # Open for this task, before or after the edit: the guard skips the file until `done`, which then
                # guards whatever the file holds.
                for k in ("locked", "locked_regions"):
                    if p in (t.get(k) or {}):
                        del t[k][p]
                        relock.append(p)
                        if p not in t.setdefault("was_locked", []):
                            t["was_locked"].append(p)  # `done` guards it again from its final version
                if p not in t["unlocked"]:
                    t["unlocked"].append(p)
            else:
                # No task is active: accept the file as it is now.
                if p in st["protected"] and cur:
                    st["protected"][p]["sha"] = cur
                    store_protected_copy(repo, repo.root / p, cur)
                if p in st["protected_regions"]:
                    region = test_region_text(p, read_text(repo.root / p))
                    if region:
                        st["protected_regions"][p]["sha"] = sha256_text(region)
                        store_protected_text(repo, region)
                if cur:
                    st["acks"][p] = cur
            done_paths.append(p)
        add_review(st, t["id"] if t else None, "test-change", "%s: %s" % (", ".join(done_paths), args.reason))
        st["violations"] = []
        store.log("UNLOCK", task=t["id"] if t else None, msg=args.reason, paths=done_paths)
        store.save()
    print("Unlocked: %s (flagged for human review)." % ", ".join(done_paths))
    if t:
        print("Change %s as %s needs; checks accept its changes until `done`, which guards the new version."
              % ("them" if len(done_paths) > 1 else "it", t["id"]))
    if relock:
        print("It held this task's acceptance tests: when they are right, lock them again: .sci/sci lock %s"
              % " ".join(sorted(set(relock))))
    footer(store)
    return 0


def store_protected_text(repo, text):
    dst = protected_copy_path(repo, sha256_text(text))
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(dst, text, sync=False)


def store_protected_copy(repo, src, sha):
    dst = protected_copy_path(repo, sha)
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(src), str(dst))


def build_needed(store, cur_tree):
    """The build command if compiled sources changed since the last successful build, else None."""
    repo, st, cfg = store.repo, store.st, store.cfg
    cmd = (cfg.get("build_cmd") or "").strip()
    last = st.get("last_build_tree")
    if not cmd:
        return None
    if st.get("last_build_cmd") not in (None, cmd):
        return cmd  # a different build (new flags, another target): its output is not what was tested
    if last == cur_tree:
        return None
    if last and not any(match_any(p, cfg.get("build_globs")) for _, p in tree_changes(repo, last, cur_tree)):
        return None
    return cmd


def run_build(store, cmd):
    job_step("build", 0, 0, (store.st.get("durations") or {}).get("build:" + cmd))
    r = run_cmd(store, cmd, "build", store.st.get("active"), int(store.cfg["verify_timeout"]))
    return r, report_run(store.repo, store.cfg, r, "build", quiet_pass=True)


def record_attempt(store, t, ok, tree, run, guard_only=False):
    """Update strikes. Once the tests are locked, a failure is a strike when the code changed since the last attempt,
    unless only the guard failed (`guard_only`: the tests passed). Returns lines to print."""
    repo, st = store.repo, store.st
    msgs = []
    prev = t.get("last_check")
    changed = prev is not None and prev.get("tree") != tree
    if ok:
        t["strikes"] = 0
        t["last_green_tree"] = tree
        set_ref(repo, t["id"] + "/green", tree, "sci %s green" % t["id"])
        if t.get("stop"):
            t["stop"] = None
            store.log("STOP_CLEARED", task=t["id"], msg="check passed")
            msgs.append("STOP cleared: the check passes now.")
    elif is_locked(t) and changed and not guard_only:
        t["strikes"] = int(t.get("strikes") or 0) + 1
        base = t.get("last_green_tree") or t["start_tree"]
        p = save_attempt_patch(repo, t, base, tree, "strike%d-%d" % (t["strikes"], int(time.time())))
        if p:
            msgs.append("Attempt saved: %s" % repo.rel(p))
        if t["strikes"] >= 3 and not t.get("stop"):
            t["stop"] = {"since_hyp": st["next_hyp"], "runs": 0, "t": now_iso()}
            store.log("STOP", task=t["id"], msg="%d failed attempts" % t["strikes"])
    t["last_check"] = {"ok": ok, "tree": tree, "t": now_iso(), "run": run.n if run else None,
                       "failing": (run.failing[:20] if run else [])}
    return msgs


STOP_BANNER = ("!!! STOP: 3 failed fix attempts in a row. Do NOT edit code now.\n"
               "!!! 1) .sci/sci hyp add \"<specific, testable cause>\"  (at least 2 hypotheses)\n"
               "!!! 2) test each with a tiny experiment, not a code fix: .sci/sci run \"python3 .sci/scratch/probe.py\"\n"
               "!!! 3) .sci/sci hyp refute H<n> \"<evidence>\"  or  .sci/sci hyp confirm H<n> \"<evidence>\"\n"
               "!!! Then fix only the confirmed cause. Undo bad edits with: .sci/sci restore")


def scope_warnings(store, t, cur_tree):
    repo, cfg = store.repo, store.cfg
    if not t.get("start_tree"):
        return []
    ns = tree_numstat(repo, t["start_tree"], cur_tree)
    w = []
    total = sum(a + d for a, d in ns.values())
    if total > int(cfg.get("max_diff_lines") or 400):
        w.append("DIFF LARGE: %d changed lines in %d files. Small steps are safer; consider splitting the task or "
                 "`.sci/sci restore` and redoing it smaller." % (total, len(ns)))
    if t["scope"]:
        outside = [p for p in ns if not is_test(p, cfg) and not match_any(p, t["scope"]) and not is_doc(p, cfg)]
        if outside:
            w.append("OUT OF SCOPE: %s. Revert unless needed (`.sci/sci restore --paths <file>`); if truly needed, "
                     "`.sci/sci task scope <glob>`." % ", ".join(outside[:6]))
    return w


def run_changes(repo, cfg, tree):
    """What changed while a command ran, from snapshot `tree`: (edited, outputs). edited: code that existed when it
    started and was edited or deleted meanwhile (tracked files and untracked source files; new files and
    documentation do not count). outputs: untracked files other than source code and test snapshots that the
    command created or rewrote (logs, reports, artifacts). rewritten: the outputs that existed before."""
    after, _ = snapshot(repo, cfg)
    if after == tree:
        return [], [], []
    tracked = tracked_set(repo)
    edited, outputs, rewritten = [], [], []
    for s, p in tree_changes(repo, tree, after):
        if p in tracked or SRC_EXT_RE.search(p):
            if s != "A" and not is_doc(p, cfg):
                edited.append(p)
        elif s != "D" and not SNAPSHOT_RE.search(p) and (s != "A" or not is_test(p, cfg)):
            # A new file in a test directory may be an expected-output file the test writes once; it counts as
            # output only when a later run rewrites it.
            outputs.append(p)
            if s == "M":
                rewritten.append(p)
    return edited, outputs, rewritten


def rewritten_text(t, rewritten):
    """A note when a run rewrote files of the task's scope: `done` commits the version it then sees, and a file
    that changes on every run never matches the verified one."""
    mine = [p for p in rewritten if t and match_any(p, t.get("scope") or [])]
    if not mine:
        return []
    return ["note: the command rewrote %s, which is in %s's scope. If every run changes it (a timestamp, a random "
            "value), `done` never sees the verified version: make its content repeatable." % (", ".join(mine[:4]), t["id"])]


def guarded_paths(st, t):
    """Files whose tests sci guards: protected by finished tasks or locked by task `t` (whole files and Rust
    files whose tests are guarded)."""
    return (set(st.get("protected") or {}) | set(st.get("protected_regions") or {}) |
            set((t or {}).get("locked") or {}) | set((t or {}).get("locked_regions") or {}))


def ignore_outputs(store, t, outputs):
    """Ignore from now on the untracked files a check wrote, unless the task's scope names them. Otherwise every run
    changes the files sci compares, `done` never sees the files it verified, and the files get committed.

    A folder of 5 or more such files is ignored as `folder/**`, but only when it holds no tracked file, no guarded
    test and is no test folder: an ignored file is invisible to the guard, so a pattern must never cover a test.
    A guarded file the check itself rewrites is output, not a test: its guard is dropped and listed for review.
    Returns (globs added to ignore_globs, guarded files dropped)."""
    cfg, st, repo = store.cfg, store.st, store.repo
    scope = (t or {}).get("scope") or []
    new = [p for p in outputs if not match_any(p, scope)]
    if not new:
        return [], []
    guarded = guarded_paths(st, t)
    by_dir = {}
    for p in new:
        by_dir.setdefault(p.rsplit("/", 1)[0] if "/" in p else "", []).append(p)
    pats, keep = [], None
    for d, ps in sorted(by_dir.items()):
        whole = bool(d) and len(ps) >= 5 and not TEST_DIR_RE.search(d + "/") and not any(
            match_any(d + "/x", [x]) or norm_glob(x).startswith(d + "/") for x in scope)
        if whole:
            if keep is None:
                keep = tracked_set(repo) | guarded
            whole = not any(q.startswith(d + "/") for q in keep)
        pats += [d + "/**"] if whole else sorted(ps)
    cur = list(cfg.get("ignore_globs") or [])
    add = [x for x in pats if x not in cur][:50]
    dropped = []
    if add:
        cfg["ignore_globs"] = cur + add
        auto = st.setdefault("auto_ignored", [])
        auto += [x for x in add if x not in auto]
        for p in [q for q in (st.get("protected") or {}) if match_any(q, add)]:
            del st["protected"][p]
            dropped.append(p)
        for p in [q for q in ((t or {}).get("locked") or {}) if match_any(q, add)]:
            del t["locked"][p]
            dropped.append(p)
        if dropped:
            add_review(st, t["id"] if t else None, "ignored-test",
                       "every check rewrites %s, so sci treats it as output and no longer guards it"
                       % ", ".join(sorted(set(dropped))[:8]))
    return add, sorted(set(dropped))


def ignored_text(add, dropped=()):
    msg = ("note: the command wrote %s (untracked, not source code); sci ignores %s from now on, so it is never "
           "compared or committed. Undo: .sci/sci config remove ignore_globs %s"
           % (", ".join(add[:4]), "it" if len(add) == 1 else "them", add[0]))
    if dropped:
        msg += ("\nnote: %s was guarded as a test, but the check rewrites it on every run, so it is output: no longer "
                "guarded (listed for human review). If the task has no other test file: .sci/sci lock --none \"<why>\""
                % ", ".join(dropped[:4]))
    return msg


def norm_glob(g):
    g = (g or "").strip()
    return g[2:] if g.startswith("./") else g


def unignore_scoped(store, t):
    """Outputs sci ignored on its own that task `t`'s scope names are files the task makes: sci sees them again.
    Returns lines to print (also a hint when a file the scope names matches an ignore glob the human set)."""
    cfg, st = store.cfg, store.st
    scope = [norm_glob(x) for x in (t or {}).get("scope") or [] if norm_glob(x)]
    if not scope:
        return []
    cur = list(cfg.get("ignore_globs") or [])
    auto = st.get("auto_ignored") or []
    lits = [x for x in scope if not any(c in x for c in "*?[")]
    back = [e for e in cur if e in auto and (match_any(norm_glob(e), scope) or any(match_any(x, [e]) for x in lits))]
    lines = []
    if back:
        cfg["ignore_globs"] = [e for e in cur if e not in back]
        st["auto_ignored"] = [e for e in auto if e not in back]
        lines.append("note: %s is in the scope of %s, so sci no longer ignores it: %s" % (
            ", ".join(back[:4]), t["id"], "the task can change and commit it."))
    for x in lits:
        hit = [e for e in cfg.get("ignore_globs") or [] if match_any(x, [e])]
        if hit:
            lines.append("note: %s matches ignore_globs %s, so sci does not see it. If %s changes it: "
                         ".sci/sci config remove ignore_globs %s" % (x, hit[0], t["id"], hit[0]))
    return lines


def edited_text(what, paths):
    return ("FILES EDITED WHILE %s RAN: %s. Its result does not count. Run it again and do not edit files until it "
            "finishes. (If the command itself rewrites them: `.sci/sci config add ignore_globs <glob>`.)"
            % (what, ", ".join(paths[:6])))


def cmd_check(args):
    store = load_store()
    repo = store.repo
    with state_lock(repo):
        st, cfg = store.load()
        t = require_active(st)
        tid, cmd = t["id"], args.cmd or t["verify"]
        cur_tree, big = snapshot(repo, cfg)
        cmd_fp = step_fp(store, cur_tree, cmd)
        env = env_stamp(repo.root, cmd)
        viol = run_guard(repo, st, cfg, t, cur_tree)
        build = build_needed(store, cur_tree)
        pend = None if args.cmd else t.get("pending")
        if pend and pend["kind"] == "golden" and pend.get("name") not in st["goldens"]:
            pend = None
        if pend:
            pfp, penv = step_fp(store, cur_tree, pend["full"]), env_stamp(repo.root, pend["full"])
    # The command runs without the state lock, so note/hyp/now keep working meanwhile.
    lines, r, built, ok_b, ref, p_ok = [], None, None, True, cur_tree, None
    if build:
        built, blines = run_build(store, build)
        ok_b = built.ok
        lines += blines
        ref = snapshot(repo, cfg)[0]  # what the build writes is not an edit
    if not ok_b:
        lines.append("Build failed; fix the build first.")
    else:
        job_step("check " + tid, 1, 2 if pend else 1, (st.get("durations") or {}).get("cmd:" + cmd))
        r = run_cmd(store, cmd, "check", tid, int(cfg["check_timeout"]))
        lines += report_run(repo, cfg, r, "check " + tid)
        if pend and r.ok:
            job_step(pend["label"], 2, 2, (st.get("durations") or {}).get(pend["key"]))
            p_ok, plines, p_last, p_secs, p_whole = run_pending(store, pend, tid)
            lines += plines
    edited, outs, rewrote = run_changes(repo, cfg, ref) if r is not None else ([], [], [])
    with state_lock(repo):
        st, cfg = store.load()
        t = need_task(st, tid)
        ignored, dropped = ignore_outputs(store, t, outs)
        if ignored and not edited:
            # The ignored files are out of the snapshot from now on: compare later checks with that tree.
            cur_tree = snapshot(repo, cfg)[0]
            cmd_fp = step_fp(store, cur_tree, cmd)
            viol = run_guard(repo, st, cfg, t, cur_tree) if dropped else viol
        if built is not None:
            note_result(st, "build:" + build, None, built.ok, built.secs)
            if built.ok:
                st["last_build_tree"], st["last_build_cmd"] = cur_tree, build
        if r is not None and not edited:
            note_result(st, "cmd:" + cmd, cmd_fp, r.ok, r.secs, env)
        if p_ok is not None and not edited:
            cur = t.get("pending")
            if cur and cur.get("key") == pend["key"]:
                cur["passed_tree"] = cur_tree if p_ok else None
                if p_whole:
                    cur.update(cmd=cur["full"], narrow=False, whole=True)
            if p_last is not None and pend["name"] in st["goldens"]:
                st["goldens"][pend["name"]]["last"] = p_last
            if p_ok and pend["kind"] != "golden" and not pend["narrow"]:
                note_result(st, pend["key"], pfp, True, p_secs, penv)
        st["violations"] = viol
        ok = bool(r is not None and r.ok) and p_ok is not False and not viol and not edited
        if viol and r is not None and r.ok:
            lines.append("CHECK COUNTS AS FAIL: the tests pass, but protected/locked tests were changed (see GUARD).")
        if edited:
            lines.append(edited_text("THE CHECK", edited))
        else:
            lines += record_attempt(store, t, ok, cur_tree, r,
                                    guard_only=bool(viol) and r is not None and r.ok and p_ok is not False)
            t["last_check"]["pending_failed"] = p_ok is False
        warn = scope_warnings(store, t, cur_tree)
        store.log("CHECK", task=tid, msg="PASS" if ok else "FAIL", run=r.n if r else None, cmd=cmd,
                  strikes=t["strikes"])
        store.save(cur_tree)
    if t.get("stop"):
        print("!!! STOP is active for %s (details at the end of this output)." % tid)
    out(lines)
    for v in viol:
        print("GUARD: " + fmt_viol(v))
    if viol:
        print("GUARD FIX: " + guard_fix_hint(viol))
    for w in warn:
        print("WARNING: " + w)
    if ignored:
        print(ignored_text(ignored, dropped))
    out(rewritten_text(t, rewrote))
    if not is_locked(t) and r is not None:
        print("Not locked yet: when the acceptance test is right, run `.sci/sci lock`.")
    if t.get("stop"):
        print(STOP_BANNER)
    footer(store)
    return 0 if ok else 1


def verify_plan(store, t, tree, args):
    """Steps of a verify, in order, each marked with a reusable PASS if one exists; plus the earlier tasks whose
    tests the regression suite runs anyway (they get no separate step)."""
    st, cfg = store.st, store.cfg
    steps, covered = [], []
    if t:
        steps.append({"label": "task " + t["id"], "kind": "task", "cmd": t["verify"], "key": "cmd:" + t["verify"],
                      "tid": t["id"]})
    if not args.quick:
        suite_cmd = (cfg.get("regress_cmd") or "").strip()
        suite = parse_test_cmd(suite_cmd) if suite_cmd else None
        files = tree_entries(store.repo, tree) if suite_cmd else {}
        ini = pytest_ini(store.repo.root) if suite and suite["kind"] == "pytest" else {}
        known = (st.get("baseline") or {}).get("failures") or []
        seen = set(s["cmd"] for s in steps)
        for d in st["tasks"]:
            if d["status"] != "done" or not d.get("verify") or d["verify"] in seen:
                continue
            seen.add(d["verify"])
            tf = suite_covers(suite, d["verify"], files, ini, known) if suite else None
            keys = runner_covers(suite_cmd, d["verify"], known, files) if suite_cmd and not suite else None
            if tf or keys is not None:
                covered.append((d["id"], tf or [], keys or []))
            else:
                steps.append({"label": "regression " + d["id"], "kind": "done", "cmd": d["verify"],
                              "key": "cmd:" + d["verify"], "tid": d["id"]})
        for name in sorted(st["goldens"]):
            ref = read_text(golden_path(store.repo, name)) or ""
            steps.append({"label": "golden " + name, "kind": "golden", "name": name,
                          "cmd": st["goldens"][name].get("cmd") or "", "key": "golden:%s|%s" % (name, sha256_text(ref)[:16])})
        if suite_cmd:
            steps.append({"label": "regression suite", "kind": "suite", "cmd": suite_cmd,
                          "key": "suite:%s|%s" % (suite_cmd, sha256_text(json.dumps(sorted(known)))[:12])})
    durs = st.get("durations") or {}
    for s in steps:
        s["est"] = durs.get(s["key"]) or (durs.get("baseline:" + s["cmd"]) if s["kind"] == "suite" else None)
        s["fp"], s["env"] = step_fp(store, tree, s["cmd"]), env_stamp(store.repo.root, s["cmd"])
        s["cached"] = (None if args.fresh or not cacheable(s["cmd"])
                       else cached_pass(st, s["key"], s["fp"], s["env"]))
    return steps, covered


def crashed(rc):
    """The exit code of a process killed by a signal (bash reports 128+n) or of a crash on Windows."""
    return rc < 0 or 128 < rc < 160 or rc > 255


STOP_RUNNERS = {"jest": {"--bail", "-b"}, "vitest": {"--bail"}, "mocha": {"--bail", "-b"}, "rspec": {"--fail-fast"},
                "go": {"-failfast", "-failfast=true"}, "ctest": {"--stop-on-failure"}, "phpunit": {"--stop-on-failure",
                "--stop-on-error", "--stop-on-defect"}}


def _runner_word(words):
    """The test runner a command starts (through npx, yarn, pnpm, bunx, uv run ...), or None."""
    w = [x for x in words or () if not re.match(r"^\w+=", x)]
    while w and w[0].rsplit("/", 1)[-1] in ("npx", "bunx", "yarn", "pnpm", "uv", "poetry", "pipenv", "pdm", "rye",
                                             "hatch", "pixi", "exec", "run", "bundle", "env"):
        w = w[1:]
    return (w[0].rsplit("/", 1)[-1].lower(), w[1:]) if w else (None, [])


def stops_early(cmd):
    """Whether suite `cmd` stops at its first failing test, file or target, so tests after a known failure never
    run and a new failure among them would go unseen."""
    spec = parse_test_cmd(cmd)
    if spec:
        return spec["stop"]
    words = cmd_words(cmd) or []
    prog, rest = _runner_word(words)
    if prog == "cargo" and rest[:1] in (["test"], ["nextest"]):
        if "--no-fail-fast" in rest:
            return False
        one_target = rest[:1] == ["test"] and any(a in ("--lib", "--doc", "--test", "--bin", "--example", "--bench")
                                                  or a.startswith(("--test=", "--bin=")) for a in rest)
        # `--lib` in a workspace means one lib target per crate, and cargo stops at the first failing crate.
        one_package = any(a in ("-p", "--package") or a.startswith("--package=") for a in rest) or not (
            any(a in ("--workspace", "--all") for a in rest) or cargo_virtual_workspace())
        return not (one_target and one_package)
    if prog in ("mvn", "mvnw", "./mvnw"):
        return not any(a in ("-fae", "--fail-at-end", "-fn", "--fail-never") for a in rest)
    if prog in ("gradle", "gradlew", "./gradlew", "gradlew.bat"):
        return "--continue" not in rest
    flags = STOP_RUNNERS.get(prog)
    if flags is not None:
        return any(a.split("=", 1)[0] in flags or a in flags for a in rest)
    return False


_ROOT = []  # the repository root of this command, once the store is loaded


def cargo_virtual_workspace(root=None):
    """Whether the repository's Cargo.toml is a workspace without a package: plain `cargo test` covers every crate."""
    root = root or (_ROOT[0] if _ROOT else None)
    if root is None:
        return False
    text = read_text(Path(root) / "Cargo.toml") or ""
    return bool(re.search(r"^\s*\[workspace\]", text, re.M)) and not re.search(r"^\s*\[package\]", text, re.M)


def no_stop_variant(cmd):
    """`cmd` changed to run every test after a failure, or None when sci cannot build that."""
    if not stops_early(cmd):
        return None
    words = cmd_words(cmd) or []
    spec = parse_test_cmd(cmd)
    out, skip = [], False
    if spec:
        drop = ("-x", "--exitfirst", "--sw", "--stepwise") if spec["kind"] == "pytest" else ("-f", "--failfast")
        for w in words:
            if skip:
                skip = False
                continue
            if w in drop or w.startswith("--maxfail="):
                continue
            if w == "--maxfail" and spec["kind"] == "pytest":
                skip = True
                continue
            out.append(w)
    else:
        prog, rest = _runner_word(words)
        if prog == "cargo" and rest[:1] in (["test"], ["nextest"]):
            cut = words.index("--") if "--" in words else len(words)
            out = words[:cut] + ["--no-fail-fast"] + words[cut:]
        elif prog in ("mvn", "mvnw", "./mvnw"):
            out = words + ["--fail-at-end"]
        elif prog in ("gradle", "gradlew", "./gradlew", "gradlew.bat"):
            out = words + ["--continue"]
        elif prog in STOP_RUNNERS:
            out = [w for w in words if w.split("=", 1)[0] not in STOP_RUNNERS[prog]]
        else:
            return None
    res = " ".join(shlex.quote(w) for w in out)
    return res if res != " ".join(shlex.quote(w) for w in words) and not stops_early(res) else None


def suite_doubts(st, r):
    """Why a failing suite run whose named failures are all known baseline failures still does not pass."""
    b = st.get("baseline") or {}
    why = []
    if crashed(r.rc):
        why.append("the suite was killed (exit %d), so the tests after that point never ran" % r.rc)
    if stops_early(r.cmd):
        why.append("the suite stops at its first failure, so the tests after a known failure never ran")
    n0, n = b.get("failed_n"), failure_count(r.lines)
    if b.get("summary") and not r.summary:
        why.append("the suite ended without its summary line (it aborted or crashed)")
    elif n0 is not None and n is not None and n > n0:
        why.append("%d tests fail now, %d in the baseline: the runner names files or suites, not each test, so "
                   "new failures hide behind the known ones" % (n, n0))
    return why


def gate_sig(st, cfg):
    """What a full verify checks besides the tasks' own commands: suite, build, goldens and baseline."""
    gold = sorted((k, g.get("cmd"), g.get("rtol"), g.get("atol"), g.get("n")) for k, g in (st.get("goldens") or {}).items())
    return sha256_text(json.dumps([cfg.get("regress_cmd"), cfg.get("build_cmd"), gold,
                                   sorted((st.get("baseline") or {}).get("failures") or [])]))[:16]


def judge_suite(store, r, covered):
    """(ok, lines) for a regression-suite run: known baseline failures pass, new ones fail and are traced back to
    the earlier task whose tests they are."""
    repo, st, cfg = store.repo, store.st, store.cfg
    if r.ok:
        return True, report_run(repo, cfg, r, "regression suite", quiet_pass=True)[:1]
    known = set((st.get("baseline") or {}).get("failures") or [])
    new = [f for f in r.failing if f not in known]
    doubts = suite_doubts(st, r) if r.failing and not new and st.get("baseline") and not r.timed_out else []
    if r.failing and not new and st.get("baseline") and not r.timed_out and not doubts:
        return True, ["PASS regression suite: only the %d known pre-existing failures (baseline) | log %s"
                      % (len(r.failing), repo.rel(r.log))]
    lines = report_run(repo, cfg, r, "regression suite")
    if doubts:
        lines.append("NOT ONLY KNOWN FAILURES: the failing tests it names are all in the baseline, but %s."
                     % "; and ".join(doubts))
        variant = no_stop_variant(r.cmd) if stops_early(r.cmd) else None
        if variant:
            lines.append("Make the suite run every test: .sci/sci config set regress_cmd \"%s\", then record the "
                         "baseline again between tasks (.sci/sci baseline)." % variant)
        else:
            lines.append("Read the log (.sci/sci out %d --grep \"FAIL|Error|panicked\") and fix what your change "
                         "broke." % r.n)
    elif new:
        lines.append("NEW FAILURES (not in baseline): " + ", ".join(new[:12]))
        for tid, fails in blame_tasks(covered, new):
            lines.append("REGRESSION: your change broke %s's tests (%s). Fix the code. Change %s's tests only if "
                         "they must follow an intended change of this task (`.sci/sci unlock <file> --reason "
                         "\"...\"`)." % (tid, ", ".join(fails[:4]), tid))
    elif not st.get("baseline"):
        lines.append("No baseline recorded. If these failures existed before your work, record them "
                     "on a clean checkout with `.sci/sci baseline` (ask the human if unsure).")
    else:
        lines.append("Could not attribute the failure to known baseline tests; treat it as yours.")
    return False, lines


def set_pending(store, t, s, tree):
    """Remember the verify step that failed (an earlier task's check, a golden or the suite) so that `check` reruns
    it until it passes. A failing suite is rerun as just its failing tests when narrow_suite can select them,
    unless those passed alone on this very code: then they depend on other tests and only the whole suite tells.
    Returns lines to print."""
    if s["kind"] == "task":
        return []
    prev = t.get("pending") or {}
    same = prev.get("key") == s["key"]
    whole = s["kind"] == "suite" and same and bool(prev.get("whole") or (prev.get("narrow")
                                                                         and prev.get("passed_tree") == tree))
    narrow = None
    if s["kind"] == "suite" and not whole:
        narrow = narrow_suite(s["cmd"], s.get("new") or [], tree_entries(store.repo, tree))
    t["pending"] = {"key": s["key"], "kind": s["kind"], "label": s["label"], "name": s.get("name"),
                    "cmd": narrow or s["cmd"], "full": s["cmd"], "narrow": bool(narrow), "whole": whole,
                    "tree": tree, "passed_tree": None}
    if whole and not prev.get("whole"):
        return ["The failing tests pass when they run alone but fail in the full suite, so they depend on other "
                "tests (shared state, files or processes left behind). `.sci/sci check` now reruns the whole suite."]
    return []


def run_pending(store, p, tid):
    """Rerun the step that failed the last verify. Returns (ok, lines, golden record or None, seconds, whole):
    `whole` is True when a narrowed rerun failed without naming a test, so only the whole suite can tell."""
    if p["kind"] == "golden":
        return golden_check_one(store, p["name"]) + (False,)
    r = run_cmd(store, p["cmd"], "check", tid, int(store.cfg["verify_timeout"]))
    if p["narrow"] and not r.ok and not r.failing:
        return False, report_run(store.repo, store.cfg, r, p["label"] + " (only the tests that failed)") + [
            "The rerun of just the failing tests failed without naming a test, so `.sci/sci check` reruns the "
            "whole suite from now on."], None, r.secs, True
    if p["kind"] == "suite" and not p["narrow"]:
        ok, lines = judge_suite(store, r, [])
    else:
        label = p["label"] + (" (only the tests that failed)" if p["narrow"] else "")
        ok, lines = r.ok, report_run(store.repo, store.cfg, r, label, quiet_pass=True)
        if not ok and p["kind"] == "done":
            lines.append("REGRESSION: your change still breaks %s." % p["label"].split()[-1])
    return ok, lines, None, r.secs, False


def cmd_verify(args):
    store = load_store()
    repo = store.repo
    with state_lock(repo):
        st, cfg = store.load()
        t = active_task(st)
        tid = t["id"] if t else None
        spec0 = spec_hash(t) if t else None
        cur_tree, big = snapshot(repo, cfg)
        fp = code_fp(store, cur_tree)
        viol = run_guard(repo, st, cfg, t, cur_tree)
        build = build_needed(store, cur_tree)
        steps, covered = verify_plan(store, t, cur_tree, args)

    # Steps run without the state lock and print as they finish (a background job relays them live).
    def say(ls):
        for ln in ls:
            print(ln, flush=True)

    ok, results, glast, built, ref = not viol, [], {}, None, cur_tree
    if viol:
        say(["FAIL guard: " + fmt_viol(v) for v in viol] + ["GUARD FIX: " + guard_fix_hint(viol)])
    if ok and build:
        built, blines = run_build(store, build)
        ok = built.ok
        say(blines)
        ref = snapshot(repo, cfg)[0]
    if ok and covered:
        say(["note: the regression suite runs the tests of %s, so their checks do not run separately."
             % ", ".join(c[0] for c in covered)])
    for i, s in enumerate(steps if ok else [], 1):
        if s["cached"]:
            say(["PASS %s: reused (passed on identical code %s)" % (s["label"], ago(s["cached"].get("t")))])
            continue
        job_step(s["label"], i, len(steps), s["est"])
        if s["kind"] == "golden":
            s_ok, glines, glast[s["name"]], secs = golden_check_one(store, s["name"])
            say(glines)
        else:
            r = run_cmd(store, s["cmd"], "regress" if s["kind"] == "suite" else "verify", tid,
                        int(cfg["verify_timeout"]))
            secs = r.secs
            if s["kind"] == "suite":
                s_ok, slines = judge_suite(store, r, covered)
                say(slines)
                known = set((st.get("baseline") or {}).get("failures") or [])
                s["new"] = [f for f in r.failing if f not in known]
            elif r.ok:
                s_ok = True
                say(report_run(repo, cfg, r, s["label"], quiet_pass=True)[:1])
            else:
                s_ok = False
                say(report_run(repo, cfg, r, s["label"]))
                if s["kind"] == "done":
                    say(["REGRESSION: your change broke %s's check. Fix the code. Change %s's tests only if they "
                         "must follow an intended change of this task (`.sci/sci unlock <file> --reason \"...\"`)."
                         % (s["tid"], s["tid"])])
        results.append((s, s_ok, secs))
        if not s_ok:
            ok = False
            break
    edited, outs, rewrote = run_changes(repo, cfg, ref) if results else ([], [], [])
    lines = []
    with state_lock(repo):
        st, cfg = store.load()
        t = find_task(st, tid) if tid else None
        ignored, dropped = ignore_outputs(store, t, outs)
        if ignored:
            lines.append(ignored_text(ignored, dropped))
            if not edited:
                # Verified are the files without the ignored ones: what `done` and later commands see.
                cur_tree = snapshot(repo, cfg)[0]
                fp = code_fp(store, cur_tree)
                for s, _, _ in results:
                    s["fp"] = step_fp(store, cur_tree, s["cmd"])
        lines += rewritten_text(t, rewrote)
        if built is not None:
            note_result(st, "build:" + build, None, built.ok, built.secs)
            if built.ok:
                st["last_build_tree"], st["last_build_cmd"] = cur_tree, build
        for s, s_ok, secs in results if not edited else []:
            note_result(st, s["key"], s["fp"], s_ok, secs, s["env"])
        for name, last in glast.items():
            if name in st["goldens"] and not edited:
                st["goldens"][name]["last"] = last
        if edited:
            ok = False
            lines.append(edited_text("VERIFY", edited))
        if t and spec_hash(t) != spec0:
            ok = False
            lines.append("%s's verify command changed while verify ran. Run `.sci/sci verify` again." % tid)
        st["violations"] = viol
        if t:
            if ok:
                t.update({"verified_tree": cur_tree, "verified_fp": fp, "verified_full": not args.quick,
                          "verified_at": now_iso(), "verified_sig": gate_sig(st, cfg)})
                if not args.quick:
                    t["pending"] = None
                lc = t.get("last_check")
                if not lc or lc.get("tree") != cur_tree or not lc.get("ok"):
                    record_attempt(store, t, True, cur_tree, None)
            else:
                t["verified_tree"] = None
                lines += record_attempt(store, t, False, cur_tree, None) if not viol and not edited else []
                if results and not results[-1][1] and not viol and not edited:
                    lines += set_pending(store, t, results[-1][0], cur_tree)
                    t["last_check"]["by_verify"] = True
        st["last_verify"] = {"t": now_iso(), "ok": ok, "task": tid, "quick": bool(args.quick), "tree": cur_tree,
                             "fp": fp}
        store.log("VERIFY", task=tid, msg="PASS" if ok else "FAIL", quick=bool(args.quick),
                  reused=len([s for s in steps if s["cached"]]), covered=[c[0] for c in covered])
        warn = scope_warnings(store, t, cur_tree) if t else []
        store.save(cur_tree)
    out(lines)
    for w in warn:
        print("WARNING: " + w)
    if big:
        print("note: large untracked files are not tracked by sci snapshots: " +
              ", ".join("%s (%.1f MB)" % (p, s / 1e6) for p, s in big[:4]))
    print("VERIFY %s%s" % ("PASS" if ok else "FAIL", " (quick: earlier tasks, goldens and regression suite skipped)"
                           if args.quick else ""))
    if t and t.get("stop"):
        print(STOP_BANNER)
    footer(store)
    return 0 if ok else 1


DEBUG_RE = re.compile(r"\bbreakpoint\(\)|\bpdb\.set_trace\(|\bipdb\.set_trace\(|^\s*import\s+i?pdb\b|"
                      r"\bdebugger;|\bbinding\.pry\b")
PRINT_RE = re.compile(r"^\s*(?:print\(|console\.log\(|println!\(|fmt\.Print|std::cout|write\s*\(\s*\*\s*,\s*\*\s*\)|"
                      r"print\s*\*\s*,)")


def cmd_done(args):
    store = load_store()
    repo = store.repo
    with locked(store) as (st, cfg):
        t = require_active(st)
        cur_tree, big = snapshot(repo, cfg)
        doc_only = verified_changes_since(store, t, cur_tree)
        if doc_only is None:
            refuse("%s is not verified on the current files." % t["id"],
                   "Run `.sci/sci verify` (it must PASS on exactly these files), then `.sci/sci done -m \"...\"`.")
        if not t.get("verified_full"):
            refuse("Only a quick verify passed.", "Run the full `.sci/sci verify` first.")
        if not is_locked(t) and not args.no_lock:
            refuse("Nothing is locked for %s, so its acceptance could change silently." % t["id"],
                   "Run `.sci/sci lock` (it also handles tasks with nothing to lock), then `.sci/sci verify` again.")
        changes = tree_changes(repo, t["start_tree"], cur_tree)
        tracked = tracked_set(repo)
        pre_untracked = set(t.get("preexisting_untracked") or [])
        added_list = [norm_rel(repo.root, a) for a in args.add or []]
        # Untracked files to commit: new ones, and ones that existed before but are in the task's scope.
        new_untracked = [p for s, p in changes if s != "D" and p not in tracked and (
            (s == "A" and p not in pre_untracked) or match_any(p, t["scope"]))]
        junk = [p for p in new_untracked if not (match_any(p, t["scope"]) or is_test(p, cfg) or p in added_list)]
        if junk:
            refuse("Untracked files created during %s are not in its scope: %s" % (t["id"], ", ".join(junk[:10])),
                   "Delete them, move scratch work into .sci/scratch/, or include them: .sci/sci done -m \"...\" --add <file>")
        outside = [p for s, p in changes if t["scope"] and not is_test(p, cfg) and not match_any(p, t["scope"])
                   and p not in added_list and not (s != "A" and is_doc(p, cfg))]
        if outside and not args.allow_outside:
            refuse("Changes outside %s's scope: %s" % (t["id"], ", ".join(outside[:10])),
                   "Revert them (`.sci/sci restore --paths <file>`), or justify: .sci/sci done -m \"...\" --allow-outside \"<why>\"")
        notes = []
        if doc_only:
            notes.append("Only documentation, comments or layout changed since verify (%s); the verification "
                         "still holds."
                         % ", ".join(doc_only[:5]))
        dbg, prints = [], []
        for s, p in changes:
            if s == "D":
                continue
            for h in parse_hunks(tree_diff_text(repo, t["start_tree"], cur_tree, p)):
                for ln in h["add"]:
                    if DEBUG_RE.search(ln):
                        dbg.append("%s: %s" % (p, trunc(ln.strip(), 80)))
                    elif PRINT_RE.search(ln) and not is_test(p, cfg):
                        prints.append(p)
        if dbg:
            refuse("Debugger statements left in the change: " + "; ".join(dbg[:5]), "Remove them, then .sci/sci verify.")
        if args.allow_outside and outside:
            add_review(st, t["id"], "outside-scope", "%s: %s" % (", ".join(outside), args.allow_outside))
        if args.no_lock and not is_locked(t):
            add_review(st, t["id"], "no-lock", args.no_lock)
        commit, left = None, []
        paths = sorted(set(p for _, p in changes))
        if paths and cfg.get("commit", True) and not args.no_commit:
            for chunk in chunked(new_untracked, 200):
                git(repo.root, ["add", "--"] + chunk)
            to_commit = set(git(repo.root, ["diff", "--name-only", "-z", "HEAD", "--"] + paths, check=False)
                            .stdout.split("\0")) - {""} if head_sha(repo) else set(paths)
            to_commit |= set(new_untracked)
            to_commit = sorted(p for p in paths if p in to_commit)
            pre_dirty = [p for p in to_commit if p in set(t.get("preexisting_dirty") or [])]
            if pre_dirty:
                notes.append("Committed files that already had uncommitted edits before the task: " + ", ".join(pre_dirty))
            if to_commit:
                msg = args.message.strip()
                if "[%s]" % t["id"] not in msg:
                    msg += " [%s]" % t["id"]
                body = ["", "Task %s: %s" % (t["id"], t["title"]), "Verified: %s" % t["verify"]]
                n_reg = len([d for d in st["tasks"] if d["status"] == "done" and d.get("verify")])
                body.append("Regression: %d earlier task checks, %d goldens%s" % (
                    n_reg, len(st["goldens"]), ", regression suite" if cfg.get("regress_cmd") else ""))
                revs = [r for r in st["review"] if r.get("task") == t["id"]]
                if revs:
                    body.append("Needs review: " + "; ".join("%s: %s" % (r["kind"], trunc(r["msg"], 120)) for r in revs))
                p = git(repo.root, ["commit", "-q", "-m", msg + "\n" + "\n".join(body), "--"] + to_commit,
                        check=False, timeout=900)
                if p.returncode != 0:
                    for chunk in chunked(new_untracked, 200):  # leave the user's index as it was
                        git(repo.root, ["rm", "-q", "--cached", "--ignore-unmatch", "--"] + chunk, check=False)
                    refuse("git commit failed (pre-commit hook?):\n" + trunc((p.stdout + p.stderr)[-1500:], 1500),
                           "Fix what the hook reports, run `.sci/sci verify`, then `.sci/sci done` again.")
                commit = head_sha(repo)
                left = [x for x in git(repo.root, ["diff", "--name-only", "-z", "HEAD", "--"] + to_commit,
                                       check=False).stdout.split("\0") if x]
                if left:
                    notes.append("Hooks changed files after commit: %s. Run .sci/sci verify." % ", ".join(left))
            else:
                commit = head_sha(repo)
                notes.append("Changes were already committed; recorded HEAD.")
        elif paths:
            pp = save_attempt_patch(repo, t, t["start_tree"], cur_tree, "done")
            if pp:
                dst = repo.dir / "patches" / ("%s.patch" % t["id"])
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(pp), str(dst))
                notes.append("Not committed (commit disabled). Patch: %s" % repo.rel(dst))
        # Tests unlocked in this task are guarded again from their final version on; the review item says why.
        was = set(t.get("was_locked") or [])
        for p in t.get("unlocked") or []:
            cur = file_sha(repo.root / p)
            st["acks"].pop(p, None)
            if p in was and p not in st["protected"] and p not in st["protected_regions"]:
                # This task locked it, then unlocked it to change it: it guards later tasks like its other tests.
                if has_inline_tests(p) and not is_test(p, cfg):
                    st["protected_regions"][p] = {"sha": "", "task": t["id"]}
                else:
                    st["protected"][p] = {"sha": "", "task": t["id"]}
            if p in st["protected"]:
                if cur:
                    st["protected"][p] = {"sha": cur, "task": t["id"]}
                    store_protected_copy(repo, repo.root / p, cur)
                else:
                    del st["protected"][p]
            if p in st["protected_regions"]:
                region = test_region_text(p, read_text(repo.root / p))
                if region:
                    st["protected_regions"][p] = {"sha": sha256_text(region), "task": t["id"]}
                    store_protected_text(repo, region)
                else:
                    del st["protected_regions"][p]
        # Locked files and test files created by this task guard every later task.
        guarded = sorted(set(t.get("locked") or {}) | set(p for s, p in changes if s == "A" and is_test(p, cfg)))
        protected_now = []
        for p in guarded:
            cur = file_sha(repo.root / p)
            if cur:
                st["protected"][p] = {"sha": cur, "task": t["id"]}
                store_protected_copy(repo, repo.root / p, cur)
                protected_now.append(p)
        # Tests inside source files (Rust) that this task locked or wrote guard every later task too.
        for p in sorted(set(t.get("locked_regions") or {}) | set(
                p for s, p in changes if s != "D" and has_inline_tests(p) and not is_test(p, cfg))):
            region = test_region_text(p, read_text(repo.root / p))
            old = test_region_text(p, blob_text(repo, t["start_tree"], p)) if p not in t.get("locked_regions", {}) else None
            if region and region != old:
                st["protected_regions"][p] = {"sha": sha256_text(region), "task": t["id"]}
                store_protected_text(repo, region)
                protected_now.append(p + " (tests)")
        t.update({"status": "done", "done_at": now_iso(), "commit": commit, "done_tree": cur_tree})
        final = (all(x["status"] == "done" for x in st["tasks"]) and t.get("verified_full")
                 and t.get("verified_sig") == gate_sig(st, cfg) and not left)
        if final:
            # The last task's full verify ran every task's check, the goldens and the suite on these files: it is
            # the final verify, and running it again would only reuse every step.
            st["last_verify"] = {"t": now_iso(), "ok": True, "task": None, "quick": False, "tree": cur_tree,
                                 "fp": code_fp(store, cur_tree), "by_done": t["id"]}
        st["active"] = None
        st["violations"] = []
        drop_refs(repo, t["id"])
        if st.get("now") and st["now"].get("task") == t["id"]:
            st["now"] = None
        store.log("DONE", task=t["id"], msg=args.message, commit=commit, files=paths)
        store.save()
    print("DONE %s: %s%s" % (t["id"], t["title"], (" | commit %s" % commit[:10]) if commit else ""))
    print("  files: " + (", ".join(paths[:12]) + (" ..." if len(paths) > 12 else "") if paths else "(no changes)"))
    if protected_now:
        print("  protected tests (later tasks must not change them): " + ", ".join(protected_now[:8]) +
              (" ..." if len(protected_now) > 8 else ""))
    if prints:
        print("  note: added print/console output in %s; keep only if intended." % ", ".join(sorted(set(prints))[:5]))
    for n in notes:
        print("  note: " + n)
    if big:
        print("  note: large untracked files remain: " + ", ".join(p for p, _ in big[:5]))
    footer(store)
    return 0


def locked_targets(st, t):
    """{path: sha} that must hold exactly their locked content: active locks plus protected files."""
    m = {p: info["sha"] for p, info in (st.get("protected") or {}).items()}
    if t:
        m.update(t.get("locked") or {})
        for p in t.get("unlocked") or []:
            m.pop(p, None)
    return m


def cmd_restore(args):
    store = load_store()
    repo = store.repo
    with locked(store) as (st, cfg):
        t = require_active(st)
        to = args.to or ("green" if t.get("last_green_tree") else "start")
        target = t.get("last_green_tree") if to == "green" else t.get("start_tree")
        if to == "green" and not target:
            print("No green state yet for %s; restoring to the task start." % t["id"])
            to, target = "start", t.get("start_tree")
        if not target:
            refuse("No restore point recorded for %s." % t["id"])
        cur_tree, big = snapshot(repo, cfg)
        paths = [norm_rel(repo.root, p) for p in args.paths] if args.paths else None
        locks = locked_targets(st, t)
        # Locked/protected files always go back to their exact locked content.
        lock_fix = [p for p in (paths if paths is not None else sorted(locks))
                    if p in locks and file_sha(repo.root / p) != locks[p]]
        region_fix, whole = {}, []
        if paths is not None:
            rlocks = {q: info["sha"] for q, info in (st.get("protected_regions") or {}).items()}
            rlocks.update(t.get("locked_regions") or {})
            for p in paths:
                if p in rlocks and p not in (t.get("unlocked") or []):
                    text, want = read_text(repo.root / p), read_text(protected_copy_path(repo, rlocks[p]))
                    if text is not None and want is not None and sha256_text(test_region_text(p, text)) != rlocks[p]:
                        new = splice_regions(p, text, want)
                        if new is not None:
                            region_fix[p] = new
                        else:
                            whole.append(p)  # a test was added or removed: only the whole file can go back
        other = [p for p in paths if p not in locks and p not in region_fix] if paths is not None else None
        changes = []
        if other is None or other:
            changes = [(s_, p) for s_, p in tree_changes(repo, target, cur_tree, other) if p not in locks]
        if not changes and not lock_fix and not region_fix:
            print("Nothing to restore: files already match the %s state%s." % (to, " for those paths" if paths else ""))
            if to == "start" and not paths and (t.get("strikes") or t.get("stop")):
                t.update({"strikes": 0, "stop": None, "resets": int(t.get("resets") or 0) + 1})
                store.save()
                print("Strikes reset: this is a fresh start. Write down the new approach before coding: "
                      ".sci/sci note \"<approach and why the last one failed>\" --kind decision")
            footer(store)
            return 0
        t["attempts"] = int(t.get("attempts") or 0) + 1
        n = t["attempts"]
        set_ref(repo, "%s/attempt-%d" % (t["id"], n), cur_tree, "sci %s attempt %d" % (t["id"], n))
        patch = save_attempt_patch(repo, t, target, cur_tree, "attempt%d" % n)
        restore = [p for s_, p in changes if s_ != "A"]
        delete = [p for s_, p in changes if s_ == "A"]
        for chunk in chunked(restore, 150):
            git(repo.root, ["restore", "--source=%s" % target, "--worktree", "--"] + chunk)
        for p in delete:
            fp = repo.root / p
            with contextlib.suppress(OSError):
                fp.unlink()
            d = fp.parent
            while d != repo.root and d.is_dir() and not any(d.iterdir()):
                with contextlib.suppress(OSError):
                    d.rmdir()
                d = d.parent
        for p, new in sorted(region_fix.items()):
            with open(str(repo.root / p), "w", encoding="utf-8", newline="") as f:
                f.write(new)
        unrecoverable = []
        for p in lock_fix:
            src = protected_copy_path(repo, locks[p])
            if src.exists():
                (repo.root / p).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(src), str(repo.root / p))
            else:
                unrecoverable.append(p)
        st["violations"] = []
        t["verified_tree"] = None
        if t.get("last_check"):
            # Restoring is not a fix attempt: the check that confirms the restored files compares with them.
            t["last_check"]["tree"] = snapshot(repo, cfg)[0]
        fresh = to == "start" and not paths and (t.get("strikes") or t.get("stop"))
        if fresh:  # back to the start: a new approach, so strikes count from zero (stop_cycles stays)
            t.update({"strikes": 0, "stop": None, "resets": int(t.get("resets") or 0) + 1})
        store.log("RESTORE", task=t["id"], msg=to, files=len(changes), locked=lock_fix, attempt=n)
        store.save()
    if changes:
        print("Restored %d file(s) to the %s state of %s%s." % (len(changes), to, t["id"],
                                                            " (paths: %s)" % ", ".join(other) if other else ""))
    fixed = [p for p in lock_fix if p not in unrecoverable]
    if fixed:
        print("Restored locked content: %s" % ", ".join(fixed))
    if region_fix:
        print("Restored the locked tests in %s; the code around them is unchanged." % ", ".join(sorted(region_fix)))
    if unrecoverable:
        print("WARNING: no saved locked copy for %s; restore it by hand from git." % ", ".join(unrecoverable))
    if whole and changes:
        print("WARNING: the tests in %s no longer line up with the locked ones (a test was added or removed), so the "
              "whole file was restored, code included. Your version is in %s: copy your code changes back from it, "
              "and leave the tests as they are now." % (", ".join(whole), repo.rel(patch) if patch else "the patch"))
    if patch:
        print("Your previous version is saved: %s (re-apply: git apply %s)" % (repo.rel(patch), repo.rel(patch)))
    if fresh:
        print("Strikes reset: this is a fresh start. Write down the new approach before coding: "
              ".sci/sci note \"<approach and why the last one failed>\" --kind decision")
    print("Next: run `.sci/sci check` to confirm the state, then try a different approach.")
    footer(store)
    return 0


def cmd_run(args):
    store = load_store()
    repo, st, cfg = store.repo, store.st, store.cfg
    t = active_task(st)
    tid = t["id"] if t else None
    cmd = " ".join(args.command).strip()
    if not cmd:
        refuse("Nothing to run.", '.sci/sci run "python3 .sci/scratch/probe.py"')
    extra = [str(repo.root)] + ([str(repo.root / "src")] if (repo.root / "src").is_dir() else [])
    old = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join(extra + ([old] if old else []))
    try:
        job_step("run", 1, 1, (st.get("durations") or {}).get("run:" + cmd))
        r = run_cmd(store, cmd, "run", tid, int(args.timeout or cfg["check_timeout"]))
        with state_lock(repo):
            st, cfg = store.load()
            t = find_task(st, tid) if tid else None
            note_result(st, "run:" + cmd, None, r.ok, r.secs)
            store.log("RUN", task=tid, msg=cmd, run=r.n, rc=r.rc)
            msg = None
            if t and t.get("stop"):
                t["stop"]["runs"] = int(t["stop"].get("runs") or 0) + 1
                msg = maybe_clear_stop(store, t)
            store.save()
    finally:
        if old is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = old
    head = "run %d: exit %d in %.1fs | log %s" % (r.n, r.rc, r.secs, repo.rel(r.log))
    body = [ln.replace(str(repo.root.resolve()) + os.sep, "") for ln in r.lines[1:]]
    while body and not body[-1].strip():
        body.pop()
    n = args.tail or 40
    print(head)
    if len(body) > n:
        print("  ... %d earlier lines (.sci/sci out %d --grep <pattern>)" % (len(body) - n, r.n))
    for ln in body[-n:]:
        print("  | " + trunc(ln, 240))
    print("Cite it as evidence: .sci/sci hyp confirm|refute H<n> \"run %d: <what it showed>\"" % r.n)
    if msg:
        print(msg)
    return 0 if r.ok else 1


def maybe_clear_stop(store, t):
    st = store.st
    if not t or not t.get("stop"):
        return None
    since = t["stop"].get("since_hyp", 0)
    mine = [h for h in st["hyps"] if h.get("task") == t["id"] and int(h["id"][1:]) >= since]
    resolved = [h for h in mine if h["status"] != "open"]
    ran = int(t["stop"].get("runs") or 0) > 0  # evidence must come from an experiment, not just a written verdict
    if len(mine) >= 2 and resolved and ran:
        t["stop"] = None
        t["strikes"] = 0
        t["stop_cycles"] = int(t.get("stop_cycles") or 0) + 1
        store.log("STOP_CLEARED", task=t["id"], msg="hypotheses tested")
        return ("STOP cleared: strikes reset. Fix only what the evidence supports%s." %
                (" (confirmed: %s)" % ", ".join(h["id"] for h in resolved if h["status"] == "confirmed")
                 if any(h["status"] == "confirmed" for h in resolved) else ""))
    need = []
    if len(mine) < 2:
        need.append("%d more hypothesis(es)" % (2 - len(mine)))
    if not ran:
        need.append("an experiment (`.sci/sci run \"<probe>\"`)")
    if not resolved:
        need.append("refute or confirm at least one")
    return "STOP still active: need " + ", ".join(need[:-1]) + (" and " if len(need) > 1 else "") + need[-1] + "."


def cmd_hyp(args):
    store = load_store()
    action = args.action
    rest = args.rest or []
    msg = None
    with locked(store) as (st, _):
        t = active_task(st)
        if action == "add":
            text = " ".join(rest).strip()
            if not text:
                refuse("Empty hypothesis.", '.sci/sci hyp add "<specific, testable cause>" [--test "<cmd>"]')
            hid = "H%d" % st["next_hyp"]
            st["next_hyp"] += 1
            st["hyps"].append({"id": hid, "task": t["id"] if t else None, "text": text, "test": args.test or "",
                               "status": "open", "t": now_iso()})
            store.log("HYP_ADD", task=t["id"] if t else None, msg=text, id=hid)
            print("Added %s: %s" % (hid, text))
            print("Test it with a small experiment (probe/print/script in .sci/scratch), then: .sci/sci hyp refute|confirm %s \"<evidence>\"" % hid)
        elif action in ("refute", "confirm"):
            if not rest:
                refuse("Which hypothesis?", ".sci/sci hyp %s H1 \"<evidence>\"" % action)
            hid = rest[0].upper()
            if not hid.startswith("H"):
                hid = "H" + hid
            ev = " ".join(rest[1:]).strip() or (args.evidence or "")
            if not ev:
                refuse("Evidence is required.", ".sci/sci hyp %s %s \"<what you ran and saw>\"" % (action, hid))
            h = next((x for x in st["hyps"] if x["id"] == hid), None)
            if h is None:
                refuse("No hypothesis %s." % hid, ".sci/sci hyp list")
            if t and h.get("task") and h["task"] != t["id"]:
                mine = [x["id"] for x in st["hyps"] if x.get("task") == t["id"] and x["status"] == "open"]
                refuse("%s belongs to %s, not to the active task %s. Check the ID." % (hid, h["task"], t["id"]),
                       "Open hypotheses of %s: %s" % (t["id"], ", ".join(mine)) if mine else
                       ".sci/sci hyp add \"<cause>\" adds one for %s" % t["id"])
            if h["status"] != "open" and not args.revise:
                refuse("%s is already %s (evidence: %s). Check the ID: .sci/sci hyp list" % (
                    hid, h["status"], trunc(h.get("evidence", ""), 120)),
                       "Only to change the verdict on new evidence: .sci/sci hyp %s %s \"<evidence>\" --revise"
                       % (action, hid))
            if h["status"] != "open":
                h.setdefault("history", []).append({"status": h["status"], "evidence": h.get("evidence", ""),
                                                    "t": h.get("resolved")})
            h["status"] = "refuted" if action == "refute" else "confirmed"
            h["evidence"] = ev
            h["resolved"] = now_iso()
            store.log("HYP_" + action.upper(), task=h.get("task"), msg=ev, id=hid)
            print("%s %s." % (hid, h["status"]))
        elif action == "list":
            hs = st["hyps"] if args.all else [h for h in st["hyps"] if not t or h.get("task") == t["id"]]
            for h in hs:
                print("%s [%s] (%s) %s%s" % (h["id"], h["status"], h.get("task") or "-", h["text"],
                                             (" -- " + h["evidence"]) if h.get("evidence") else ""))
            if not hs:
                print("No hypotheses.")
        else:
            refuse("Unknown hyp action %s." % action, ".sci/sci hyp add|refute|confirm|list")
        if action != "list":
            msg = maybe_clear_stop(store, t)
            store.save()
    if msg:
        print(msg)
    footer(store)
    return 0


def cmd_note(args):
    store = load_store()
    with locked(store) as (st, _):
        t = active_task(st)
        if args.list:
            for n in st["notes"]:
                if not n.get("dropped") or args.all:
                    print("N%d [%s]%s %s" % (n["id"], n["kind"], " (dropped)" if n.get("dropped") else "", n["text"]))
            if not st["notes"]:
                print("No notes.")
            return 0
        if args.drop:
            nid = int(str(args.drop).upper().lstrip("N"))
            n = next((x for x in st["notes"] if x["id"] == nid), None)
            if n is None:
                refuse("No note N%d." % nid, ".sci/sci note --list")
            n["dropped"] = now_iso()
            store.log("NOTE_DROP", msg=n["text"], id=nid)
            store.save()
            print("Dropped N%d." % nid)
            return 0
        text = " ".join(args.text or []).strip()
        if not text:
            refuse("Empty note.", '.sci/sci note "<fact worth keeping>" --kind units|code|derivation|decision|env|gotcha|perf')
        kind = (args.kind or "code").lower()
        if kind not in NOTE_KINDS:
            refuse("Unknown kind %s." % kind, "Use one of: " + ", ".join(NOTE_KINDS))
        nid = st["next_note"]
        st["next_note"] += 1
        st["notes"].append({"id": nid, "t": now_iso(), "kind": kind, "task": t["id"] if t else None, "text": text})
        store.log("NOTE", task=t["id"] if t else None, msg=text, kind=kind, id=nid)
        store.save()
    print("Noted N%d [%s]." % (nid, kind))
    footer(store)
    return 0


def cmd_now(args):
    store = load_store()
    text = " ".join(args.text or []).strip()
    if not text:
        refuse("Empty.", '.sci/sci now "<what you are doing and the next command you will run>"')
    with locked(store) as (st, _):
        t = active_task(st)
        st["now"] = {"t": now_iso(), "task": t["id"] if t else None, "text": text}
        store.log("NOW", task=t["id"] if t else None, msg=text)
        store.save()
    footer(store)
    return 0


GOLD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def cmd_golden(args):
    store = load_store()
    repo, st, cfg = store.repo, store.st, store.cfg
    a = args.action
    if a == "list":
        if not st["goldens"]:
            print("No goldens. Add one: .sci/sci golden add <name> --cmd \"<command that prints key numbers>\"")
        for name, g in sorted(st["goldens"].items()):
            last = g.get("last") or {}
            print("%s: n=%d rtol=%g atol=%g | last: %s" % (name, g.get("n", 0), g["rtol"], g["atol"],
                                                          ("OK " if last.get("ok") else "FAIL ") + ago(last.get("t"))
                                                          if last else "never"))
        return 0
    if a == "check":
        names = args.names or sorted(st["goldens"])
        if not names:
            print("No goldens defined.")
            return 0
        for name in names:
            if name not in st["goldens"]:
                refuse("No golden %s." % name, ".sci/sci golden list")
        allok, lasts = True, {}
        for i, name in enumerate(names, 1):
            job_step("golden " + name, i, len(names), None)
            ok, lines, lasts[name], _ = golden_check_one(store, name)
            out(lines)
            allok = allok and ok
        with state_lock(repo):
            st, cfg = store.load()
            for name, last in lasts.items():
                if name in st["goldens"]:
                    st["goldens"][name]["last"] = last
            store.log("GOLDEN_CHECK", msg="PASS" if allok else "FAIL", names=names)
            store.save()
        footer(store)
        return 0 if allok else 1
    if not args.names:
        refuse("Missing golden name.", ".sci/sci golden %s <name> ..." % a)
    name = args.names[0]
    if not GOLD_NAME_RE.match(name):
        refuse("Bad golden name %r (letters, digits, _ . - only)." % name)
    if a in ("add", "update"):
        if a == "add" and name in st["goldens"]:
            refuse("Golden %s exists." % name, "To re-record (flagged for review): .sci/sci golden update %s --reason \"...\"" % name)
        if a == "update" and name not in st["goldens"]:
            refuse("No golden %s." % name, ".sci/sci golden add %s --cmd \"...\"" % name)
        if a == "update" and (not args.reason or len(args.reason) < 10):
            refuse("Re-recording a golden needs --reason (shown to the human).")
        old = {}
        if a == "update":
            with contextlib.suppress(OSError, ValueError):
                old = json.loads(read_text(golden_path(repo, name)) or "{}")
        cmd = args.cmd or old.get("cmd")
        if not cmd:
            refuse("Missing --cmd.", ".sci/sci golden add %s --cmd \"python examples/run.py --steps 50\"" % name)
        rtol = args.rtol if args.rtol is not None else float(old.get("rtol", 1e-12))
        atol = args.atol if args.atol is not None else float(old.get("atol", 0.0))
        filt = args.filter if args.filter is not None else old.get("filter")
        if filt:
            try:
                re.compile(filt)
            except re.error as e:
                refuse("Bad --filter regex: %s" % e)
        job_step("golden %s %s" % (a, name), 1, 2, None)
        r = run_cmd(store, cmd, "golden-" + name, None, int(args.timeout or cfg["verify_timeout"]))
        if not r.ok:
            out(report_run(repo, cfg, r, "golden %s" % a))
            refuse("The golden command failed; nothing recorded.")
        vals = parse_numbers(r.lines[1:], filt)
        if not vals:
            refuse("The command printed no numbers%s." % (" matching --filter" if filt else ""),
                   "Print the key outputs (energies, norms, residuals) one per line.")
        job_step("golden %s %s (repeat)" % (a, name), 2, 2, None)
        r2 = run_cmd(store, cmd, "golden-" + name, None, int(args.timeout or cfg["verify_timeout"]))
        vals2 = parse_numbers(r2.lines[1:], filt) if r2.ok else []
        det_ok, det_msg, _ = compare_numbers(vals, vals2, rtol, atol)
        if not det_ok:
            refuse("Output is not reproducible run-to-run within rtol=%g atol=%g (%s)." % (rtol, atol, det_msg),
                   "Fix seeds/threads, filter out timings with --filter, or choose a tolerance justified by a "
                   "script (see references/scientific-verification.md).")
        dirty = bool(head_sha(repo) and dirty_tracked(repo, cfg))
        with state_lock(repo):
            st, cfg = store.load()
            rec = {"name": name, "cmd": cmd, "rtol": rtol, "atol": atol, "filter": filt,
                   "values": [repr(v) for v in vals], "n": len(vals), "recorded_at": now_iso(), "head": head_sha(repo),
                   "dirty_tree": dirty, "timeout": args.timeout}
            atomic_write(golden_path(repo, name), json.dumps(rec, indent=1))
            st["goldens"][name] = {"rtol": rtol, "atol": atol, "n": len(vals), "cmd": cmd,
                                   "last": {"ok": True, "t": now_iso()}}
            if a == "update":
                add_review(st, st.get("active"), "golden-update", "%s re-recorded: %s" % (name, args.reason))
            store.log("GOLDEN_" + a.upper(), task=st.get("active"), msg=name, n=len(vals), reason=args.reason or "")
            store.save()
        print("Golden %s %s: %d numbers, rtol=%g atol=%g (reproducible across 2 runs)." % (
            name, "recorded" if a == "add" else "re-recorded", len(vals), rtol, atol))
        zeros = sum(1 for v in vals if v == 0.0)
        if zeros and atol == 0.0:
            print("  note: %d of the numbers are exactly 0, and with atol=0 any round-off there (1e-17) fails. If "
                  "round-off is expected, record again with an --atol on the scale of the other numbers." % zeros)
        odd = sum(1 for v in vals if math.isnan(v) or math.isinf(v))
        if odd:
            print("  WARNING: %d of the numbers are NaN or infinite. The golden accepts exactly that again, so a broken "
                  "result keeps passing. Unless they are intended, fix the code first and record again." % odd)
        if dirty and a == "add":
            print("  note: recorded with uncommitted changes in the tree. Goldens should capture known-good "
                  "behavior (usually recorded before you change numerical code).")
        footer(store)
        return 0
    with state_lock(repo):
        st, cfg = store.load()
        if a == "retol":
            if name not in st["goldens"]:
                refuse("No golden %s." % name)
            if not args.reason or len(args.reason) < 10:
                refuse("Changing a tolerance needs --reason (shown to the human).")
            rec = json.loads(read_text(golden_path(repo, name)) or "{}")
            o_r, o_a = float(rec["rtol"]), float(rec["atol"])
            n_r = args.rtol if args.rtol is not None else o_r
            n_a = args.atol if args.atol is not None else o_a
            rec["rtol"], rec["atol"] = n_r, n_a
            atomic_write(golden_path(repo, name), json.dumps(rec, indent=1))
            st["goldens"][name].update({"rtol": n_r, "atol": n_a})
            looser = n_r > o_r or n_a > o_a
            if looser:
                add_review(st, st.get("active"), "tolerance-loosened",
                           "%s rtol %g->%g atol %g->%g: %s" % (name, o_r, n_r, o_a, n_a, args.reason))
            store.log("GOLDEN_RETOL", msg=name, rtol=n_r, atol=n_a, reason=args.reason)
            store.save()
            print("Golden %s tolerance: rtol %g -> %g, atol %g -> %g%s" % (
                name, o_r, n_r, o_a, n_a, " (LOOSENED: flagged for human review)" if looser else ""))
        elif a == "rm":
            if name not in st["goldens"]:
                refuse("No golden %s." % name)
            if not args.reason or len(args.reason) < 10:
                refuse("Removing a golden needs --reason (shown to the human).")
            del st["goldens"][name]
            for x in st["tasks"]:
                if (x.get("pending") or {}).get("name") == name:
                    x["pending"] = None
            with contextlib.suppress(OSError):
                golden_path(repo, name).rename(golden_path(repo, name).with_suffix(".removed.json"))
            add_review(st, st.get("active"), "golden-removed", "%s: %s" % (name, args.reason))
            store.log("GOLDEN_RM", msg=name, reason=args.reason)
            store.save()
            print("Removed golden %s (flagged for review)." % name)
        else:
            refuse("Unknown golden action %s." % a)
    footer(store)
    return 0


def cmd_order(args):
    if args.n and args.h:
        refuse("Give either --h or --n, not both.")
    hs = args.h or ([1.0 / n for n in args.n] if args.n else None)
    if not hs or not args.err or len(hs) != len(args.err) or len(hs) < 2:
        refuse("Need matching lists with >= 2 entries.", ".sci/sci order --h 0.1 0.05 0.025 --err 1e-2 2.6e-3 6.4e-4 --expect 2")
    pairs = sorted(zip(hs, args.err), key=lambda he: -he[0])  # coarse to fine, whatever order they were given in
    hs, errs = [h for h, _ in pairs], [e for _, e in pairs]
    rows, ps = [], []
    for i in range(len(hs) - 1):
        h1, h2, e1, e2 = hs[i], hs[i + 1], errs[i], errs[i + 1]
        if min(h1, h2, e1, e2) <= 0 or h1 == h2:
            refuse("Resolutions must differ and errors must be > 0.")
        p = math.log(e1 / e2) / math.log(h1 / h2)
        ps.append(p)
        rows.append("  h %.4g -> %.4g | err %.4g -> %.4g | observed order p = %.3f" % (h1, h2, e1, e2, p))
    out(rows)
    mono = all(errs[i + 1] < errs[i] for i in range(len(errs) - 1))
    if not mono:
        print("WARNING: error does not decrease monotonically with refinement (pre-asymptotic, round-off floor, or bug).")
    if args.expect is not None:
        p = ps[-1]
        if p >= args.expect - args.tol and p <= args.expect + 1.0:
            print("PASS: finest-pair order %.3f vs expected %g (tol %g)." % (p, args.expect, args.tol))
            return 0
        if p > args.expect + 1.0:
            print("CHECK: order %.3f is much higher than expected %g (superconvergence, cancellation, or "
                  "the error is at round-off level). Investigate before trusting it." % (p, args.expect))
            return 1
        print("FAIL: finest-pair order %.3f < expected %g - %g." % (p, args.expect, args.tol))
        return 1
    return 0


def cmd_find(args):
    repo = Repo(find_root())
    sym = args.symbol
    gargs = ["grep", "-n", "-I", "--no-color", "--untracked"]
    if args.ignore_case:
        gargs.append("-i")
    if args.regex:
        gargs += ["-E", "-e", sym]
    else:
        gargs += ["-F", "-w", "-e", sym]
    gargs += ["--"] + (args.path or ["."]) + [":(exclude)" + SD]
    p = git(repo.root, gargs, check=False)
    hits = []
    for ln in p.stdout.splitlines():
        m = re.match(r"^(.*?):(\d+):(.*)$", ln)
        if m:
            hits.append((m.group(1), int(m.group(2)), m.group(3)))
    if not hits:
        print("No matches for %r%s." % (sym, " in " + ", ".join(args.path) if args.path else ""))
        return 1
    cfg = dict(DEFAULT_CONFIG)
    with contextlib.suppress(Exception):
        cfg.update(json.loads(read_text(repo.dir / "config.json") or "{}"))
    defs = []
    if not args.regex:
        pats = def_patterns(sym)
        for f, n, txt in hits:
            if any(rx.search(txt) for rx in pats):
                defs.append((f, n, txt))
    files = {}
    for f, n, txt in hits:
        files.setdefault(f, []).append(n)
    print("%d matches for %r in %d files." % (len(hits), sym, len(files)))
    if defs:
        print("DEFINITIONS (likely):")
        for f, n, txt in defs[:10]:
            print("  %s:%d: %s" % (f, n, trunc(txt.strip(), 140)))
        if len(defs) > 10:
            print("  ... %d more" % (len(defs) - 10))
    print("USAGES by file (most first):")
    ranked = sorted(files.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for f, ns in ranked[:args.max]:
        print("  %s (%d)%s: lines %s%s" % (f, len(ns), " [test]" if is_test(f, cfg) else "",
                                          ", ".join(str(x) for x in ns[:8]), " ..." if len(ns) > 8 else ""))
    if len(ranked) > args.max:
        print("  ... %d more files. Narrow with: .sci/sci find %s --path <dir>" % (len(ranked) - args.max, sym))
    print("Read only the relevant ranges (about 80 lines around each line number), not whole files.")
    return 0


def cmd_map(args):
    repo = Repo(find_root())
    lines = repo_map(repo)
    if repo.dir.is_dir():
        atomic_write(repo.dir / "map.txt", "\n".join(lines) + "\n")
    out(lines)
    return 0


def cmd_out(args):
    repo = Repo(find_root())
    runs = sorted((repo.dir / "runs").glob("*.log"))
    if not runs:
        refuse("No saved runs yet.")
    if args.run in (None, "last"):
        path = runs[-1]
    else:
        want = "%04d-" % int(args.run)
        cand = [p for p in runs if p.name.startswith(want)]
        if not cand:
            refuse("No run %s (kept: %s..%s)." % (args.run, runs[0].name[:4], runs[-1].name[:4]))
        path = cand[0]
    lines = read_log_lines(path)
    print("== %s (%d lines) ==" % (repo.rel(path), len(lines)))
    sel = []
    if args.grep:
        rx = re.compile(args.grep, re.I)
        for i, ln in enumerate(lines, 1):
            if rx.search(ln):
                lo = max(1, i - args.context)
                for j in range(lo, min(len(lines), i + args.context) + 1):
                    sel.append(j)
        seen, ordered = set(), []
        for j in sel:
            if j not in seen:
                seen.add(j)
                ordered.append(j)
        sel = ordered[:args.max]
    elif args.head:
        sel = list(range(1, min(len(lines), args.head) + 1))
    elif args.lines:
        a, _, b = args.lines.partition(":")
        sel = list(range(max(1, int(a)), min(len(lines), int(b or a)) + 1))[:args.max]
    else:
        n = args.tail or 60
        sel = list(range(max(1, len(lines) - n + 1), len(lines) + 1))
    for j in sel:
        print("%6d| %s" % (j, trunc(lines[j - 1], 300) if lines[j - 1] else ""))
    if args.grep and not sel:
        print("(no lines match %r)" % args.grep)
    return 0


def cmd_baseline(args):
    store = load_store()
    repo, st, cfg = store.repo, store.st, store.cfg
    cmd = args.cmd or cfg.get("regress_cmd") or cfg.get("test_cmd")
    if not cmd:
        refuse("No command to run.", ".sci/sci config set regress_cmd \"<suite cmd>\"  (or pass --cmd)")
    if active_task(st) and not args.force:
        refuse("A task is active; a baseline must describe the code before your changes.",
               "Record it between tasks, or pass --force if the active task has no changes yet.")
    job_step("baseline", 1, 1, (st.get("durations") or {}).get("baseline:" + cmd))
    r = run_cmd(store, cmd, "baseline", None, int(cfg["verify_timeout"]))
    switched = None
    if not r.ok and stops_early(cmd):
        # Tests after the first failure never ran, so this baseline cannot tell known failures from new ones.
        variant = no_stop_variant(cmd)
        if variant:
            print("The suite stops at its first failure, so the tests after it never ran. Running every test: %s"
                  % variant, flush=True)
            job_step("baseline (every test)", 1, 1, (st.get("durations") or {}).get("baseline:" + variant))
            switched, cmd = cmd, variant
            r = run_cmd(store, cmd, "baseline", None, int(cfg["verify_timeout"]))
    with state_lock(repo):
        st, cfg = store.load()
        note_result(st, "baseline:" + cmd, None, r.ok, r.secs)
        st["baseline"] = {"t": now_iso(), "cmd": cmd, "rc": r.rc, "failures": r.failing, "head": head_sha(repo),
                          "failed_n": failure_count(r.lines), "summary": bool(r.summary)}
        enrolled = args.cmd or not cfg.get("regress_cmd") or switched
        if enrolled:
            store.cfg["regress_cmd"] = cmd  # the suite the baseline describes is the one verify reruns
        atomic_write(repo.dir / "baseline.txt", "\n".join(r.failing) + ("\n" if r.failing else ""))
        store.log("BASELINE", msg=cmd, rc=r.rc, failures=len(r.failing))
        store.save()
    out(report_run(repo, cfg, r, "baseline", quiet_pass=True))
    if r.ok:
        print("Baseline is green: any failure of this suite later is a regression.")
    elif r.failing:
        print("Recorded %d pre-existing failures (.sci/baseline.txt). They are not your job unless a task says so; "
              "only NEW failures block verify." % len(r.failing))
    else:
        print("WARNING: the suite failed (exit %d) but no failing test names could be parsed. verify will treat ANY "
              "failure of this suite as a regression. Ask the human whether the suite is expected to pass." % r.rc)
    if not r.ok and stops_early(cmd):
        print("WARNING: this suite stops at its first failure, so tests after a known failure never run and verify "
              "cannot pass while any known failure remains. Use a command that runs every test: "
              ".sci/sci baseline --cmd \"<suite without the stop option>\"")
    if switched:
        print("Recorded with `%s` instead of `%s`, so every test ran." % (cmd, switched))
    if enrolled:
        print("`.sci/sci verify` reruns this suite from now on (it took %.0fs). Too slow? Pick a narrower one: "
              ".sci/sci baseline --cmd \"<subset>\"" % r.secs)
    footer(store)
    return 0


def review_ignored_tests(st, cfg, key, old):
    """Ignored files are not guarded, so ignoring a locked or protected test is listed for the human. So is a
    narrower test_globs: existing tests it no longer names are no longer checked for weakening."""
    if key == "test_globs":
        gone = [g for g in old or [] if g not in (cfg.get("test_globs") or [])]
        if gone:
            t = active_task(st)
            add_review(st, t["id"] if t else None, "test-globs", "test_globs no longer has: %s" % ", ".join(gone[:8]))
            print("note: files matching only %s no longer count as tests; listed for human review." % ", ".join(gone[:4]))
        return
    if key != "ignore_globs":
        return
    t = active_task(st)
    guarded = set(st.get("protected") or {}) | set(st.get("protected_regions") or {}) | set(
        ((t or {}).get("locked") or {})) | set(((t or {}).get("locked_regions") or {}))
    hit = sorted(p for p in guarded if match_any(p, cfg.get("ignore_globs")) and not match_any(p, old or []))
    if hit:
        add_review(st, t["id"] if t else None, "ignored-test",
                   "ignore_globs now covers guarded tests: %s" % ", ".join(hit[:8]))
        print("note: %s no longer guarded (ignored); listed for human review." % ", ".join(hit[:4]))


def cmd_config(args):
    store = load_store()
    cfg = store.cfg
    if args.action in ("add", "remove"):
        if not isinstance(DEFAULT_CONFIG.get(args.key), list):
            refuse("%s is not a list setting." % args.key, "List settings: " + ", ".join(
                sorted(k for k, v in DEFAULT_CONFIG.items() if isinstance(v, list))))
        items = split_scope([" ".join(args.value)])
        with locked(store) as (st, cfg):
            cur = list(cfg.get(args.key) or [])
            new = cur + [x for x in items if x not in cur] if args.action == "add" else [x for x in cur if x not in items]
            cfg[args.key] = new
            review_ignored_tests(st, cfg, args.key, cur)
            store.log("CONFIG", msg="%s %s %s" % (args.key, args.action, ", ".join(items)))
            store.save()
        print("%s = %s" % (args.key, json.dumps(new)))
        return 0
    if args.action == "set":
        if args.key not in DEFAULT_CONFIG:
            refuse("Unknown config key %s." % args.key, "Keys: " + ", ".join(sorted(DEFAULT_CONFIG)))
        raw = " ".join(args.value)
        default = DEFAULT_CONFIG[args.key]
        if isinstance(default, bool):
            val = raw.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(default, int):
            try:
                val = int(raw)
            except ValueError:
                refuse("%s needs an integer." % args.key)
        elif isinstance(default, list):
            try:
                val = json.loads(raw)
                assert isinstance(val, list)
            except (ValueError, AssertionError):
                val = [x.strip() for x in raw.split(",") if x.strip()]
        else:
            val = raw
        with locked(store) as (st, cfg):
            old = cfg.get(args.key)
            cfg[args.key] = val
            review_ignored_tests(st, cfg, args.key, old)
            store.log("CONFIG", msg="%s=%s" % (args.key, val))
            store.save()
        print("%s = %s" % (args.key, json.dumps(val)))
        return 0
    for k in sorted(cfg):
        v = cfg[k]
        if isinstance(v, list) and len(v) > 6 and not args.all:
            v = v[:6] + ["... (%d total; --all)" % len(cfg[k])]
        print("%s = %s" % (k, json.dumps(v)))
    return 0


def cmd_review(args):
    store = load_store()
    st = store.st
    if args.clear:
        if not args.yes:
            refuse("HUMAN ONLY: clearing review items hides them from the human (a human adds --yes).",
                   "Do not clear them. Leave them for the human to read in `.sci/sci report`.")
        with locked(store) as (st, _):
            n = len(st["review"])
            st["review"] = []
            store.log("REVIEW_CLEAR", msg="%d items" % n)
            store.save()
        print("Cleared %d review items." % n)
        return 0
    if not st["review"]:
        print("Nothing needs human review.")
    for r in st["review"]:
        print("[%s] %s %s: %s" % (r["kind"], r.get("task") or "-", r["t"], r["msg"]))
    return 0


def cmd_report(args):
    store = load_store()
    st, repo = store.st, store.repo
    c = task_counts(st)
    L = ["# .sci/sci report", "", "Objective: " + (st.get("objective") or "(not set)"),
         "Tasks: %d/%d done, %d blocked, %d todo" % (c["done"], len(st["tasks"]), c["blocked"], c["todo"]), ""]
    for t in st["tasks"]:
        L.append("- %s [%s] %s%s" % (t["id"], t["status"], t["title"],
                                     (" (commit %s)" % t["commit"][:10]) if t.get("commit") else
                                     (" -- blocked: " + t.get("block_reason", "")) if t["status"] == "blocked" else ""))
    lv = st.get("last_verify")
    if lv:
        L += ["", "Last verify: %s %s%s" % ("PASS" if lv["ok"] else "FAIL", ago(lv["t"]), " (quick)" if lv.get("quick") else "")]
    if st["goldens"]:
        L += ["Goldens: " + ", ".join("%s %s" % (k, "OK" if (g.get("last") or {}).get("ok") else "not OK")
                                      for k, g in sorted(st["goldens"].items()))]
    conf = hyps_for(st, None, "confirmed")
    if conf:
        L += ["", "Root causes found:"] + ["- %s: %s (%s)" % (h["id"], h["text"], h.get("evidence", "")) for h in conf]
    dec = [n for n in st["notes"] if n["kind"] == "decision" and not n.get("dropped")]
    if dec:
        L += ["", "Decisions:"] + ["- " + n["text"] for n in dec]
    if st["review"]:
        L += ["", "NEEDS YOUR REVIEW:"] + ["- [%s] %s: %s" % (r["kind"], r.get("task") or "-", r["msg"]) for r in st["review"]]
    blocked = [t for t in st["tasks"] if t["status"] == "blocked"]
    if blocked:
        L += ["", "Open questions:"] + ["- %s: %s" % (t["id"], t.get("block_reason", "")) for t in blocked]
    out(L)
    return 0


def cmd_log(args):
    repo = Repo(find_root())
    path = repo.dir / "events.jsonl"
    lines = (read_text(path) or "").splitlines()[-args.n:]
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        print("%s %-14s %-4s %s" % (e.get("t", ""), e.get("ev", ""), e.get("task") or "", trunc(e.get("msg", ""), 120)))
    return 0


def cmd_pause(args, value=True):
    store = load_store()
    with locked(store) as (st, _):
        st["paused"] = value
        store.log("PAUSE" if value else "UNPAUSE")
        store.save()
    print("sci %s." % ("paused (hooks stay quiet)" if value else "resumed"))
    return 0


def cmd_purge(args):
    repo = Repo(find_root())
    if not args.yes:
        refuse("HUMAN ONLY: purge deletes .sci/ and all sci refs and backups in this repo (a human adds --yes).",
               "Do not purge. If the human asked for it, ask them to run it themselves.")
    with run_lock(repo):  # refuses while a job runs; released first, since Windows cannot delete an open file
        drop_refs(repo, "")
    shutil.rmtree(str(repo.backup_dir), ignore_errors=True)
    for _ in range(20):  # a worker that just finished may still be exiting
        shutil.rmtree(str(repo.dir), ignore_errors=True)
        if not repo.dir.exists():
            break
        time.sleep(0.25)
    else:
        print("Could not remove all of .sci/ (a file in it is in use). Close what uses it and run purge again.")
        return 1
    print("Removed .sci/, refs/sci/* and the backup in the git directory.")
    return 0


HELP = """sci %s - keeps long coding tasks on track. State lives in .sci/; run it as .sci/sci <command>.

START / RECOVER
  resume                          live briefing + NEXT (run after any compaction or when unsure)
  init --objective "..."          once per repo (from the skill: %s <skill>/scripts/sci.py init)
PLAN
  objective "..."                 set the overall goal
  task add "title" --verify "cmd" [--scope GLOB]... [--inv "invariant"]... [--dep T1]... [--why "..."]
  task list | task show [ID] | task start [ID]
  task block ID --reason "question for the human" | task unblock ID [--answer "..."]
  task amend ID [--verify ..] [--title ..] [--inv ..] [--drop-inv ..] [--scope ..] [--dep ..] --reason "..."
  task scope GLOB...              widen the active task's scope (logged)
UNDERSTAND
  map                             repo overview (also saved in .sci/map.txt)
  find SYMBOL [--path DIR]...     definitions + usages by file, capped output
  note "fact" [--kind units|code|derivation|decision|env|gotcha|perf]    | note --list | note --drop N
  now "what I am doing + next command"
WORK
  check [--cmd "narrower cmd"]    run the active task's verify command, and what the last verify failed on
                                  (fast loop; counts strikes)
  lock [FILES] | lock --none "why"   lock the acceptance tests (after RED); --none: task has no test file.
                                  In Rust source files only the tests (#[cfg(test)], #[test]) are locked
  verify [--quick] [--fresh]      full gate: task + earlier tasks + goldens + regression suite. Reuses a step
                                  that passed on identical code (docs no test reads do not count); --fresh reruns
                                  all. An earlier task's tests that the suite runs anyway run only there
  wait [JOB] [--secs N]           output of the background check/verify/run (they return after wait_secs
                                  with STILL RUNNING; their result is saved when they finish) | cancel
  done -m "summary" [--add FILE]... [--allow-outside "why"] [--no-lock "why"] [--no-commit]
  restore [--to green|start] [--paths P]...    undo safely (current version saved as a patch first);
                                  --to start after STOP is a fresh start: strikes reset
  unlock [FILES] --reason "..."   before or after editing: a test that is wrong or must follow an intended
                                  change; open until `done`, which guards the new version (human reviews it)
DEBUG (required after STOP)
  run "cmd" [--tail N]            run an experiment from the repo root (PYTHONPATH includes the repo), logged
  hyp add "cause" [--test "cmd"] | hyp refute H1 "evidence" | hyp confirm H1 "evidence" | hyp list [--all]
                                  (--revise changes the verdict of an already resolved hypothesis)
NUMBERS
  golden add NAME --cmd "cmd that prints key numbers" [--rtol 1e-12] [--atol 0] [--filter REGEX]
  golden check [NAME] | golden list | golden update NAME --reason ".." | golden retol NAME --rtol X --reason ".."
  order --h H1 H2 H3 --err E1 E2 E3 [--expect P]   (or --n N1 N2 N3) observed convergence order
INSPECT
  out [RUN] [--grep PAT] [--tail N] [--lines A:B]   read a saved log
  log [-n N] | review | report | baseline [--cmd ..] | pause | unpause
  config [set KEY VALUE | add KEY ITEM | remove KEY ITEM]   e.g. config add ignore_globs "build/**"
""" % (VERSION, PY_HINT)


def cmd_help(args):
    print(HELP)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="sci", add_help=True, description="Keeps long coding tasks on track.")
    p.add_argument("--version", action="version", version="sci " + VERSION)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("help")
    s.set_defaults(fn=cmd_help)
    s = sub.add_parser("init")
    s.add_argument("--objective", default="")
    s.add_argument("--test-cmd", default="")
    s.add_argument("--regress-cmd", default="")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("resume", aliases=["status"])
    s.add_argument("--brief", action="store_true")
    s.set_defaults(fn=cmd_resume)
    s = sub.add_parser("objective")
    s.add_argument("text")
    s.set_defaults(fn=cmd_objective)

    tk = sub.add_parser("task")
    tsub = tk.add_subparsers(dest="tcmd")
    tk.set_defaults(fn=cmd_task_list)
    s = tsub.add_parser("add")
    s.add_argument("title", nargs="?")
    s.add_argument("--title", dest="title_opt")
    s.add_argument("--verify", "--verify-cmd", dest="verify")
    s.add_argument("--scope", action="append")
    s.add_argument("--inv", "--invariant", dest="inv", action="append")
    s.add_argument("--dep", "--deps", dest="dep", action="append")
    s.add_argument("--why", "--desc", dest="why")
    s.set_defaults(fn=cmd_task_add)
    s = tsub.add_parser("list")
    s.set_defaults(fn=cmd_task_list)
    s = tsub.add_parser("show")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_task_show)
    s = tsub.add_parser("start")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_task_start)
    s = tsub.add_parser("block")
    s.add_argument("id", nargs="?")
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_task_block)
    s = tsub.add_parser("unblock")
    s.add_argument("id")
    s.add_argument("--answer")
    s.set_defaults(fn=cmd_task_unblock)
    s = tsub.add_parser("amend")
    s.add_argument("id")
    s.add_argument("--title")
    s.add_argument("--verify")
    s.add_argument("--inv", action="append")
    s.add_argument("--drop-inv", action="append")
    s.add_argument("--scope", action="append")
    s.add_argument("--dep", action="append")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_task_amend)
    s = tsub.add_parser("scope")
    s.add_argument("globs", nargs="+")
    s.set_defaults(fn=cmd_task_scope)

    s = sub.add_parser("lock")
    s.add_argument("paths", nargs="*")
    s.add_argument("--none", metavar="REASON")
    s.set_defaults(fn=cmd_lock)
    s = sub.add_parser("unlock")
    s.add_argument("paths", nargs="*")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_unlock)
    s = sub.add_parser("check")
    s.add_argument("--cmd")
    s.add_argument("--wait", type=int, metavar="SECS")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("verify")
    s.add_argument("--quick", action="store_true")
    s.add_argument("--fresh", action="store_true")
    s.add_argument("--wait", type=int, metavar="SECS")
    s.set_defaults(fn=cmd_verify)
    s = sub.add_parser("wait")
    s.add_argument("job", nargs="?")
    s.add_argument("--secs", type=int)
    s.set_defaults(fn=cmd_wait)
    s = sub.add_parser("cancel")
    s.set_defaults(fn=cmd_cancel)
    s = sub.add_parser("done")
    s.add_argument("-m", "--message", required=True)
    s.add_argument("--add", action="append")
    s.add_argument("--allow-outside")
    s.add_argument("--no-lock")
    s.add_argument("--no-commit", action="store_true")
    s.set_defaults(fn=cmd_done)
    s = sub.add_parser("restore")
    s.add_argument("--to", choices=["green", "start"])
    s.add_argument("--paths", nargs="+")
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("run")
    s.add_argument("--timeout", type=int)
    s.add_argument("--tail", type=int)
    s.add_argument("--wait", type=int, metavar="SECS")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("hyp")
    s.add_argument("action", choices=["add", "refute", "confirm", "list"])
    s.add_argument("rest", nargs="*")
    s.add_argument("--test")
    s.add_argument("--evidence")
    s.add_argument("--all", action="store_true")
    s.add_argument("--revise", action="store_true")
    s.set_defaults(fn=cmd_hyp)
    s = sub.add_parser("note")
    s.add_argument("text", nargs="*")
    s.add_argument("--kind")
    s.add_argument("--list", action="store_true")
    s.add_argument("--all", action="store_true")
    s.add_argument("--drop")
    s.set_defaults(fn=cmd_note)
    s = sub.add_parser("now")
    s.add_argument("text", nargs="*")
    s.set_defaults(fn=cmd_now)

    s = sub.add_parser("golden")
    s.add_argument("action", choices=["add", "check", "list", "update", "retol", "rm"])
    s.add_argument("names", nargs="*")
    s.add_argument("--cmd")
    s.add_argument("--rtol", type=float)
    s.add_argument("--atol", type=float)
    s.add_argument("--filter")
    s.add_argument("--timeout", type=int)
    s.add_argument("--reason")
    s.add_argument("--wait", type=int, metavar="SECS")
    s.set_defaults(fn=cmd_golden)
    s = sub.add_parser("order")
    s.add_argument("--h", type=float, nargs="+")
    s.add_argument("--n", type=float, nargs="+")
    s.add_argument("--err", type=float, nargs="+")
    s.add_argument("--expect", type=float)
    s.add_argument("--tol", type=float, default=0.2)
    s.set_defaults(fn=cmd_order)

    s = sub.add_parser("find")
    s.add_argument("symbol")
    s.add_argument("--path", action="append")
    s.add_argument("--regex", action="store_true")
    s.add_argument("-i", "--ignore-case", action="store_true")
    s.add_argument("--max", type=int, default=15)
    s.set_defaults(fn=cmd_find)
    s = sub.add_parser("map")
    s.set_defaults(fn=cmd_map)
    s = sub.add_parser("out")
    s.add_argument("run", nargs="?")
    s.add_argument("--grep")
    s.add_argument("--context", type=int, default=2)
    s.add_argument("--tail", type=int)
    s.add_argument("--head", type=int)
    s.add_argument("--lines")
    s.add_argument("--max", type=int, default=150)
    s.set_defaults(fn=cmd_out)
    s = sub.add_parser("baseline")
    s.add_argument("--cmd")
    s.add_argument("--force", action="store_true")
    s.add_argument("--wait", type=int, metavar="SECS")
    s.set_defaults(fn=cmd_baseline)
    s = sub.add_parser("config")
    s.add_argument("action", nargs="?", choices=["show", "set", "add", "remove"], default="show")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="*")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_config)
    s = sub.add_parser("review")
    s.add_argument("--clear", action="store_true")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("report")
    s.set_defaults(fn=cmd_report)
    s = sub.add_parser("log")
    s.add_argument("-n", type=int, default=25)
    s.set_defaults(fn=cmd_log)
    s = sub.add_parser("pause")
    s.set_defaults(fn=lambda a: cmd_pause(a, True))
    s = sub.add_parser("unpause")
    s.set_defaults(fn=lambda a: cmd_pause(a, False))
    s = sub.add_parser("purge")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_purge)
    return p


def command_name(args):
    """The subcommand, from its handler (`args.cmd` can be the --cmd option of check/golden/baseline)."""
    return {cmd_check: "check", cmd_verify: "verify", cmd_run: "run", cmd_baseline: "baseline",
            cmd_golden: "golden", cmd_done: "done", cmd_restore: "restore", cmd_lock: "lock", cmd_unlock: "unlock",
            cmd_purge: "purge", cmd_task_start: "task start", cmd_task_block: "task block",
            cmd_config: "config"}.get(getattr(args, "fn", None), "")


def is_runner(args):
    """Commands that run project commands: they go to a background worker and hold the run lock."""
    c = command_name(args)
    return c in ("check", "verify", "run", "baseline") or (c == "golden" and args.action in ("add", "update", "check"))


def needs_run_lock(args):
    return is_runner(args) or command_name(args) in ("done", "restore", "lock", "unlock", "task start", "task block")


def split_run_options(args):
    """`.sci/sci run "python3 probe.py" --tail 20`: options after a quoted command are sci's, not the command's
    (argparse hands everything after the command to it)."""
    words = list(args.command or [])
    if len(words) < 2 or not any(ch.isspace() for ch in words[0]):
        return
    keep, i = [words[0]], 1
    while i < len(words):
        key, eq, val = words[i].partition("=")
        if key in ("--tail", "--timeout", "--wait"):
            v = val if eq else (words[i + 1] if i + 1 < len(words) else "")
            if v.isdigit():
                setattr(args, key[2:], int(v))
                i += 1 if eq else 2
                continue
        keep.append(words[i])
        i += 1
    args.command = keep


def dispatch(argv):
    args = build_parser().parse_args(argv or ["help"])
    if command_name(args) == "run":
        split_run_options(args)
    if command_name(args) == "config" and args.action in ("set", "add", "remove") and (
            not args.key or not args.value):
        print("REFUSED: usage: .sci/sci config %s KEY VALUE" % args.action)
        return 2
    try:
        if is_runner(args) and not JOB:
            bg = background_cfg()
            if bg and bg[0]:
                return run_in_background(argv, args, bg[1])
        if needs_run_lock(args):
            with run_lock(Repo(find_root())):
                return int(args.fn(args) or 0)
        return int(args.fn(args) or 0)
    except Refuse as e:
        print("REFUSED: %s" % e)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows pipes default to the ANSI code page
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_spawn"] and len(argv) > 1:
        return spawn_worker(int(argv[1]), argv[2:])
    if argv[:1] == ["_job"] and len(argv) > 1:
        return job_worker(int(argv[1]), argv[2:])
    return dispatch(argv)


if __name__ == "__main__":
    sys.exit(main())
