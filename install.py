#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Portable installer for the caofeng-ppt Agent Skill.

Zero third-party dependencies, zero private configuration, zero hardcoded
absolute paths. Works on Windows / macOS / Linux with Python >= 3.9.
The installed version is read from the bundled VERSION file, never hardcoded.

  python install.py --list
  python install.py --agent qoder                 # ~/.qoder/skills/caofeng-ppt
  python install.py --agent qoder-cn             # ~/.qoder-cn/skills/caofeng-ppt
  python install.py --agent workbuddy            # ~/.workbuddy/skills/caofeng-ppt
  python install.py --agent qoder --project <workspace>   # project-level
  python install.py --target <any/skills/dir>    # any SKILL.md-compatible agent
  python install.py --all                        # all known user-level roots
  python install.py --agent qoder-cn --replace-conflict   # back up a legacy
                                                 # 'caofeng-PPT' folder first
  python install.py --agent qoder --verify       # install + end-to-end check
  python install.py --agent qoder --dry-run
"""

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIRNAME = "caofeng-ppt"

# (user-level skills root relative to home, project-level skills root relative
#  to workspace). Sources: Qoder official docs, WorkBuddy skills layout, and
#  the common ~/.<product>/skills convention used by SKILL.md-compatible agents.
AGENTS = {
    "qoder":     (".qoder/skills", ".qoder/skills"),
    "qoder-cn":  (".qoder-cn/skills", ".qoder/skills"),
    "workbuddy": (".workbuddy/skills", ".workbuddy/skills"),
    "claude":    (".claude/skills", ".claude/skills"),
    "cursor":    (".cursor/skills", ".cursor/skills"),
    "codex":     (".codex/skills", ".codex/skills"),
    "generic":   (None, None),
}

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "Thumbs.db")


def source_skill():
    """Locate the bundled skill directory, extracting the pure zip if needed."""
    src = HERE / SKILL_DIRNAME
    if (src / "SKILL.md").is_file():
        return src
    # Discover the bundled pure zip by pattern (highest version first), never a hardcoded
    # version name — hardcoding drifted every release and broke the zip-only install path.
    for zp in sorted(HERE.glob("caofeng-ppt*.skill.zip"), reverse=True):
        with zipfile.ZipFile(zp) as z:
            for member in z.namelist():
                p = Path(member)
                if p.is_absolute() or ".." in p.parts:
                    raise SystemExit("[error] unsafe member in zip: %s" % member)
            z.extractall(HERE)
        if (src / "SKILL.md").is_file():
            return src
    raise SystemExit("[error] bundled skill not found next to install.py; "
                     "keep caofeng-ppt/ or the .skill.zip in the same folder")


def version_of(path):
    f = path / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.is_file() else "unknown"


def conflicts_in(root):
    """Same-name skills with different letter case (a real hazard on Windows)."""
    if not root.is_dir():
        return []
    return [d for d in root.iterdir() if d.is_dir()
            and d.name.casefold() == SKILL_DIRNAME.casefold()
            and d.name != SKILL_DIRNAME]


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def install_one(src, root, force=False, dry_run=False, replace_conflict=False):
    root = Path(root).expanduser().resolve()
    dest = root / SKILL_DIRNAME
    result = {"root": str(root), "dest": str(dest), "action": "", "version": version_of(src),
              "backups": []}

    # Never overwrite a different skill that merely differs in letter case.
    conflicts = conflicts_in(root)
    if conflicts:
        result["conflicts"] = [d.name for d in conflicts]
        if not replace_conflict:
            result["action"] = "CONFLICT (same-name skill exists: %s)" % ", ".join(
                d.name for d in conflicts)
            result["hint"] = "Re-run with --replace-conflict to back it up (rename, not delete)"
            return result
        if dry_run:
            result["action"] = "WOULD BACKUP CONFLICT"
            return result
        for d in conflicts:
            backup = root / (d.name + ".legacy-" + stamp())
            d.rename(backup)
            result["backups"].append(str(backup))

    if dest.exists():
        old = version_of(dest)
        result["existing_version"] = old
        if not force:
            result["action"] = "SKIPPED (already installed; use --force to replace)"
            return result
        if dry_run:
            result["action"] = "WOULD REPLACE"
            return result
        if not dest.is_dir():
            raise SystemExit("[error] destination exists but is not a directory: %s" % dest)
        # Non-destructive upgrade: rename the old copy to a timestamped backup.
        backup = root / (SKILL_DIRNAME + ".backup-" + stamp())
        dest.rename(backup)
        result["backups"].append(str(backup))
    if dry_run:
        result["action"] = "WOULD INSTALL"
        return result
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=IGNORE)
    result["action"] = "INSTALLED"
    result["files"] = sum(1 for p in dest.rglob("*") if p.is_file())
    return result


def main():
    ap = argparse.ArgumentParser(description="Install caofeng-ppt into any agent's skills directory")
    ap.add_argument("--agent", choices=sorted(AGENTS), default="generic",
                    help="target agent (default: generic, requires --target)")
    ap.add_argument("--target", help="explicit skills root that will contain caofeng-ppt/")
    ap.add_argument("--project", help="workspace root for a project-level install")
    ap.add_argument("--all", action="store_true", help="install into every known user-level root")
    ap.add_argument("--force", action="store_true",
                    help="replace an existing installation (old copy is backed up, not deleted)")
    ap.add_argument("--replace-conflict", action="store_true",
                    help="back up a same-name skill with different letter case, then install")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="run verify_install.py after installing")
    ap.add_argument("--list", action="store_true", help="show agent -> path mapping")
    a = ap.parse_args()

    if a.list:
        print(json.dumps({"agent_paths": {k: {"user": "~/" + v[0] if v[0] else None,
                                              "project": "<workspace>/" + v[1] if v[1] else None}
                                          for k, v in AGENTS.items()},
                          "note": "Use --target for any agent not listed."},
                         ensure_ascii=False, indent=2))
        return 0

    src = source_skill()
    home = Path.home()
    results = []

    if a.all:
        targets = [(k, home / v[0]) for k, v in AGENTS.items() if v[0]]
    elif a.target:
        targets = [(a.agent, Path(a.target))]
    elif a.project and AGENTS[a.agent][1]:
        targets = [(a.agent, Path(a.project).expanduser() / AGENTS[a.agent][1])]
    elif a.agent != "generic" and AGENTS[a.agent][0]:
        targets = [(a.agent, home / AGENTS[a.agent][0])]
    else:
        raise SystemExit("[error] specify --agent, --target, --project or --all (see --list)")

    for agent, root in targets:
        r = install_one(src, root, a.force, a.dry_run, a.replace_conflict)
        r["agent"] = agent
        results.append(r)
        print("[%s] %-10s %s -> %s" % (r["action"].split()[0], agent, root, r["dest"]))
        if r["action"].startswith("CONFLICT"):
            print("    -> %s" % r.get("hint", ""))
        for b in r.get("backups", []):
            print("    backup: %s" % b)

    blocked = [r for r in results if r["action"].startswith("CONFLICT")]
    if blocked and not a.dry_run:
        print("[warn] %d target(s) blocked by a name conflict; nothing was deleted." % len(blocked))
        print(json.dumps({"skill": "caofeng-ppt", "version": version_of(src),
                          "results": results}, ensure_ascii=False, indent=2))
        return 2

    if a.verify and not a.dry_run:
        verifier = HERE / "verify_install.py"
        for r in results:
            if r["action"] != "INSTALLED":
                continue
            p = subprocess.run(
                [sys.executable, str(verifier), "--skill", r["dest"]],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(p.stdout.strip())
            if p.returncode != 0:
                print(p.stderr.strip())
                return 1

    print(json.dumps({"skill": "caofeng-ppt", "version": version_of(src),
                      "source": str(src), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
