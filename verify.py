#!/usr/bin/env python3
"""Structural check for the UML set. Run: python verify.py

Checks, in order: every .puml parses; every sequence message resolves to an
operation declared in class-diagram.puml; actors only ever address their own
Profile (facade boundary); each render is at least as new as its source.
Exit 0 = all pass, 1 = failures.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "class-diagram.puml"
ACTOR_OK = re.compile(r"Profile$|^User$")
MSG = re.compile(r"\s*(\w+)\s*(?:->>|->|-->)\s*(\w+)\s*:\s*(.*)")
CALL = re.compile(r"([A-Za-z_]\w*)\s*\(")

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fails.append(name)
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail else ""))


def jar() -> Path | None:
    for c in (os.environ.get("PLANTUML_JAR"), ROOT / "plantuml.jar",
              Path(os.environ["TEMP"]) / "puml" / "plantuml.jar"):
        if c and Path(c).is_file():
            return Path(c)
    return None


def operations() -> set[str]:
    ops, cur = set(), None
    for line in MODEL.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"\s*(?:abstract\s+)?(?:class|interface)\s+(\w+)", line):
            cur = m.group(1)
        elif cur and line.strip().startswith("}"):
            cur = None
        elif cur and (m := re.search(r"([A-Za-z_]\w*)\s*\(", line)):
            ops.add(m.group(1))
    return ops


def main() -> int:
    j = jar()
    if j:
        for f in sorted(ROOT.glob("*.puml")):
            r = subprocess.run(["java", "-jar", str(j), "-checkonly", str(f)],
                               capture_output=True, text=True)
            check(f"parses: {f.name}", r.returncode == 0, f"rc={r.returncode}")
    else:
        print("SKIP parse checks: plantuml.jar not found (set PLANTUML_JAR)")

    ops, seqs = operations(), sorted(ROOT.glob("h_seq*.puml"))
    for f in seqs:
        lines = f.read_text(encoding="utf-8").splitlines()
        shown = {m.group(2): m.group(1) for line in lines
                 for m in [re.match(r'\s*participant\s+"([^"]+)"\s+as\s+(\w+)', line)] if m}
        actors = {m.group(1) for line in lines
                  for m in [re.match(r"\s*actor\s+.*\bas\s+(\w+)", line)] if m}

        unknown, leaked = [], []
        for line in lines:
            if not (m := MSG.match(line)):
                continue
            src, dst, text = m.groups()
            if (c := CALL.match(text)) and c.group(1) not in ops and c.group(1) != "new":
                unknown.append(c.group(1))
            if src in actors and not ACTOR_OK.search(shown.get(dst, dst)):
                leaked.append(f"{src}->{dst}")

        check(f"ops exist: {f.stem}", not unknown, str(sorted(set(unknown))))
        check(f"facade boundary: {f.stem}", not leaked, str(sorted(set(leaked))))

        png = f.with_suffix(".png")
        check(f"png fresh: {f.stem}", png.exists() and png.stat().st_mtime >= f.stat().st_mtime)

    for legacy in ("review-sequence.puml", "seq4.puml", "seq5.puml"):
        p = ROOT / legacy
        if p.exists() and "resolveDispute(reviewId" in p.read_text(encoding="utf-8"):
            print(f"WARN {legacy} still carries the old resolveDispute(reviewId) signature")

    print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
