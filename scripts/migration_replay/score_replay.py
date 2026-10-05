#!/usr/bin/env python3
"""Score a migration-replay run against ground_truth.yaml.

Produces report.md with gates, coverage, actions rubric, contamination audit.
No PyYAML dependency — parses the harness YAML subset used here.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
import re
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Minimal YAML subset loader (mappings / lists / scalars / comments)
# ---------------------------------------------------------------------------


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        # Minimal JSON/YAML double-quote unescapes for regex patterns.
        inner = s[1:-1]
        return (
            inner.replace("\\\\", "\0")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace("\0", "\\")
        )
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        # YAML single-quoted: only '' → '
        return s[1:-1].replace("''", "'")
    if s in ("true", "True", "yes"):
        return True
    if s in ("false", "False", "no"):
        return False
    if s in ("null", "Null", "~"):
        return None
    try:
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d+\.\d+", s):
            return float(s)
    except ValueError:
        pass
    return s


def load_simple_yaml(text: str) -> Any:
    """Parse indentation-based YAML with lists/dicts/scalars only."""

    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # strip inline comments only when preceded by space (keep URL #anchors out)
        cut = raw
        if " #" in raw:
            cut = raw.split(" #", 1)[0].rstrip()
        indent = len(cut) - len(cut.lstrip(" "))
        lines.append((indent, cut.lstrip(" ")))

    def parse_block(i: int, indent: int) -> tuple[Any, int]:
        if i >= len(lines):
            return None, i
        _, content = lines[i]
        if content.startswith("- "):
            items: list[Any] = []
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                item_raw = lines[i][1][2:]
                if item_raw == "" or item_raw.endswith(":"):
                    # nested structure under list item
                    key = item_raw[:-1] if item_raw.endswith(":") else None
                    child_indent = indent + 2
                    if key is not None:
                        # inline empty mapping start: "- key:" then nested
                        nested, i = parse_block(i + 1, child_indent)
                        items.append({key: nested})
                    else:
                        nested, i = parse_block(i + 1, child_indent)
                        items.append(nested)
                elif ":" in item_raw and not item_raw.strip().startswith("{"):
                    # inline mapping on list item: "- id: foo"
                    # may be followed by more keys at child indent
                    key, _, val = item_raw.partition(":")
                    key = key.strip()
                    val = val.strip()
                    obj: dict[str, Any] = {}
                    if val == "":
                        nested, i = parse_block(i + 1, indent + 2)
                        obj[key] = nested
                    else:
                        obj[key] = _parse_scalar(val)
                        i += 1
                    while i < len(lines) and lines[i][0] >= indent + 2 and not lines[i][1].startswith("- "):
                        k, _, v = lines[i][1].partition(":")
                        k = k.strip()
                        v = v.strip()
                        if v == "":
                            nested, i = parse_block(i + 1, lines[i][0] + 2)
                            obj[k] = nested
                        else:
                            obj[k] = _parse_scalar(v)
                            i += 1
                    items.append(obj)
                else:
                    items.append(_parse_scalar(item_raw))
                    i += 1
            return items, i

        # mapping
        obj = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            key, _, val = lines[i][1].partition(":")
            key = key.strip()
            val = val.strip()
            if val == "":
                # peek child
                if i + 1 < len(lines) and lines[i + 1][0] > indent:
                    nested, i = parse_block(i + 1, lines[i + 1][0])
                    obj[key] = nested
                else:
                    obj[key] = None
                    i += 1
            else:
                obj[key] = _parse_scalar(val)
                i += 1
        return obj, i

    root, _ = parse_block(0, lines[0][0] if lines else 0)
    return root


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    id: str
    ok: bool
    detail: str
    category: str = ""
    kind: str = ""


@dataclass
class ScoreReport:
    results: list[CheckResult] = field(default_factory=list)
    contaminated: bool = False
    contamination_hits: list[str] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def score_contamination(gt: dict[str, Any], transcript: str, sandbox: Path) -> tuple[bool, list[str]]:
    hits: list[str] = []
    corpus = transcript + "\n" + _read(sandbox / "REPLAY_FRICTION.md")
    for n in gt.get("contamination_pr_numbers") or []:
        # Match #NNNN or bare pull/NNNN; avoid matching longer numbers.
        pat = re.compile(rf"(?:#|pull/){n}\b")
        if pat.search(corpus):
            hits.append(f"pr #{n}")
    for s in gt.get("contamination_url_substrings") or []:
        if s in corpus:
            hits.append(f"url:{s}")
    return (len(hits) > 0), hits


def score_coverage(gt: dict[str, Any], sandbox: Path) -> list[CheckResult]:
    out: list[CheckResult] = []
    for fact in gt.get("coverage") or []:
        fid = str(fact.get("id"))
        cat = str(fact.get("category") or "")
        checks = fact.get("checks") or []
        ok_any = False
        details: list[str] = []
        for ch in checks:
            rel = ch.get("file")
            regex = ch.get("regex", ".")
            if not rel:
                continue
            path = sandbox / rel
            if not path.is_file():
                details.append(f"missing {rel}")
                continue
            text = path.read_text(errors="replace")
            if re.search(regex, text, re.MULTILINE):
                ok_any = True
                details.append(f"hit {rel}")
            else:
                details.append(f"no-match {rel} / {regex!r}")
        out.append(
            CheckResult(
                id=fid,
                ok=ok_any,
                detail="; ".join(details) if details else "no checks",
                category=cat,
                kind="coverage",
            )
        )
    return out


def score_gates(gt: dict[str, Any], sandbox: Path, run_commands: bool) -> list[CheckResult]:
    out: list[CheckResult] = []
    for gate in gt.get("gates") or []:
        gid = str(gate.get("id"))
        cat = str(gate.get("category") or "")
        optional = bool(gate.get("optional"))
        if gate.get("expect_file"):
            glob_pat = gate.get("file_glob") or ""
            matches = list(sandbox.glob(glob_pat)) if glob_pat else []
            ok = len(matches) > 0
            out.append(
                CheckResult(
                    id=gid,
                    ok=ok or optional,
                    detail=f"files={len(matches)} optional={optional}",
                    category=cat,
                    kind="gate",
                )
            )
            continue
        cmd = gate.get("command")
        if not run_commands or not cmd:
            # Presence-only / deferred: mark as skipped (not fail).
            out.append(
                CheckResult(
                    id=gid,
                    ok=True if optional else False,
                    detail=f"skipped_command optional={optional}: {cmd}",
                    category=cat,
                    kind="gate",
                )
            )
            continue
        # Live command execution is opt-in (iter1); keep harness import-safe.
        out.append(
            CheckResult(
                id=gid,
                ok=False,
                detail=f"not_executed: {cmd}",
                category=cat,
                kind="gate",
            )
        )
    return out


def score_actions_rubric(gt: dict[str, Any], sandbox: Path) -> list[CheckResult]:
    actions = _read(sandbox / "REPLAY_ACTIONS.md")
    out: list[CheckResult] = []
    for item in gt.get("actions_rubric") or []:
        iid = str(item.get("id"))
        cat = str(item.get("category") or "")
        kws = [str(k) for k in (item.get("keywords") or [])]
        # Pass if at least half the keywords appear (case-insensitive).
        hits = [k for k in kws if k.lower() in actions.lower()]
        need = max(1, (len(kws) + 1) // 2)
        ok = len(hits) >= need
        out.append(
            CheckResult(
                id=iid,
                ok=ok,
                detail=f"keywords_hit={hits} need>={need}",
                category=cat,
                kind="rubric",
            )
        )
    for item in gt.get("rubric_only") or []:
        iid = str(item.get("id"))
        cat = str(item.get("category") or "")
        kws = [str(k) for k in (item.get("keywords") or [])]
        hits = [k for k in kws if k.lower() in actions.lower()]
        ok = len(hits) >= 1
        out.append(
            CheckResult(
                id=iid,
                ok=ok,
                detail=f"rubric_only issue=#{item.get('issue')} hits={hits}",
                category=cat,
                kind="rubric_only",
            )
        )
    return out


def score_pr_split(gt: dict[str, Any], sandbox: Path) -> CheckResult:
    prs = _read(sandbox / "REPLAY_PRS.md")
    kws = [str(k) for k in (gt.get("expected_pr_order_keywords") or [])]
    hits = [k for k in kws if k.lower() in prs.lower()]
    ok = len(hits) >= max(1, len(kws) // 2)
    return CheckResult(
        id="pr_split_order",
        ok=ok,
        detail=f"hits={hits}",
        category="profiling",
        kind="prs",
    )


def render_report(report: ScoreReport, meta: dict[str, Any], out_path: Path) -> str:
    cov = [r for r in report.results if r.kind == "coverage"]
    gates = [r for r in report.results if r.kind == "gate"]
    rubric = [r for r in report.results if r.kind.startswith("rubric")]
    prs = [r for r in report.results if r.kind == "prs"]

    cov_ok = sum(1 for r in cov if r.ok)
    lines: list[str] = []
    lines.append("# Migration replay score")
    lines.append("")
    lines.append(f"- runner: `{meta.get('runner', '?')}`")
    lines.append(f"- arm: `{meta.get('arm', '?')}`")
    lines.append(f"- contaminated: **{report.contaminated}**")
    if report.contamination_hits:
        lines.append(f"- contamination hits: {', '.join(report.contamination_hits)}")
    lines.append(f"- coverage: **{cov_ok}/{len(cov)}**")
    lines.append(f"- gates pass: **{sum(1 for r in gates if r.ok)}/{len(gates)}**")
    lines.append(f"- rubric pass: **{sum(1 for r in rubric if r.ok)}/{len(rubric)}**")
    lines.append("")
    if report.contaminated:
        lines.append("> Blind run VOID: transcript/friction mentions ground-truth PR numbers or pull URLs.")
        lines.append("")

    def section(title: str, items: list[CheckResult]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| id | ok | category | detail |")
        lines.append("|---|---|---|---|")
        for r in items:
            detail = r.detail.replace("|", "\\|")
            lines.append(f"| `{r.id}` | {'PASS' if r.ok else 'FAIL'} | {r.category} | {detail} |")
        lines.append("")

    section("Gates", gates)
    section("Coverage", cov)
    section("Rubric (RC/final/manual + open Tier A)", rubric)
    section("PR split", prs)

    # Category breakdown
    by_cat: dict[str, list[CheckResult]] = {}
    for r in cov:
        by_cat.setdefault(r.category or "?", []).append(r)
    lines.append("## Coverage by category")
    lines.append("")
    for cat, items in sorted(by_cat.items()):
        ok = sum(1 for r in items if r.ok)
        lines.append(f"- `{cat}`: {ok}/{len(items)}")
    lines.append("")

    text = "\n".join(lines) + "\n"
    out_path.write_text(text)
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sandbox", type=Path, required=True, help="Agent sandbox checkout")
    ap.add_argument("--run-out", type=Path, required=True, help="run_replay.sh --out directory")
    ap.add_argument(
        "--ground-truth",
        type=Path,
        default=Path(__file__).with_name("ground_truth.yaml"),
    )
    ap.add_argument("--run-commands", action="store_true", help="Execute gate commands (iter1+)")
    args = ap.parse_args(argv)

    gt = load_simple_yaml(args.ground_truth.read_text())
    if not isinstance(gt, dict):
        print("ERROR: ground_truth root must be a mapping", file=sys.stderr)
        return 2

    meta_path = args.run_out / "meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())

    transcript = _read(args.run_out / "transcript.jsonl")
    if not transcript:
        transcript = _read(args.run_out / "transcript.txt")

    report = ScoreReport()
    contaminated, hits = score_contamination(gt, transcript, args.sandbox)
    report.contaminated = contaminated and str(meta.get("arm", "")) == "blind"
    report.contamination_hits = hits

    for r in score_gates(gt, args.sandbox, args.run_commands):
        report.add(r)
    for r in score_coverage(gt, args.sandbox):
        report.add(r)
    for r in score_actions_rubric(gt, args.sandbox):
        report.add(r)
    report.add(score_pr_split(gt, args.sandbox))

    report_path = args.run_out / "report.md"
    text = render_report(report, meta, report_path)
    summary = {
        "contaminated": report.contaminated,
        "contamination_hits": report.contamination_hits,
        "coverage_pass": sum(1 for r in report.results if r.kind == "coverage" and r.ok),
        "coverage_total": sum(1 for r in report.results if r.kind == "coverage"),
        "gates_pass": sum(1 for r in report.results if r.kind == "gate" and r.ok),
        "gates_total": sum(1 for r in report.results if r.kind == "gate"),
        "report": str(report_path),
    }
    (args.run_out / "score.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(text)
    print(json.dumps(summary, indent=2))
    if report.contaminated:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
