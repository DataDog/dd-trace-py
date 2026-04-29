#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import io
import json
import keyword
from pathlib import Path
import re
import tokenize
from token import COMMENT
from token import NAME
from token import NUMBER
from token import OP
from token import STRING
from urllib.parse import parse_qs
from urllib.parse import urlparse
import webbrowser


@dataclass(frozen=True)
class WalkthroughEntry:
    id: int
    path: Path
    label: str
    search_text: str


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / ".git").exists():
            return candidate
    return Path.cwd().resolve()


def _label_for(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(repo_root):
        return str(resolved.relative_to(repo_root))
    return str(resolved)


def _collect_walkthrough_files(roots: list[Path]) -> list[Path]:
    candidates: set[Path] = set()
    for root in roots:
        if root.is_file() and root.suffix == ".json":
            candidates.add(root.resolve())
            continue
        if not root.exists():
            continue
        for json_file in root.rglob("*.json"):
            if json_file.is_file():
                candidates.add(json_file.resolve())
    return sorted(candidates, key=lambda p: str(p))


def _looks_like_walkthrough(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    has_blocks = isinstance(raw.get("blocks"), list)
    has_legacy_locations = isinstance(raw.get("locations"), list)
    return all(key in raw for key in ("entrypoint", "analysis", "call_graph", "unresolved_calls")) and (
        has_blocks or has_legacy_locations
    )


def _build_catalog(repo_root: Path, roots: list[Path]) -> list[WalkthroughEntry]:
    entries: list[WalkthroughEntry] = []
    for path in _collect_walkthrough_files(roots):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not _looks_like_walkthrough(raw):
            continue
        label = _label_for(path, repo_root)
        entrypoint = raw.get("entrypoint", {})
        analysis = raw.get("analysis", {})
        search_text = " ".join(
            [
                label,
                str(entrypoint.get("symbol", "")) if isinstance(entrypoint, dict) else "",
                str(analysis.get("summary", "")) if isinstance(analysis, dict) else "",
            ]
        ).lower()
        entries.append(WalkthroughEntry(id=len(entries), path=path, label=label, search_text=search_text))
    return entries


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _dedent_for_tokenize(lines: list[str]) -> tuple[list[str], int]:
    indents = [len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()]
    if not indents:
        return lines, 0
    indent = min(indents)
    return [line[indent:] if len(line) >= indent else line for line in lines], indent


def _build_keyword_pattern(words: set[str]) -> re.Pattern[str] | None:
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(sorted(re.escape(word) for word in words)) + r")\b")


GENERIC_KEYWORDS: dict[str, set[str]] = {
    ".js": {"function", "return", "if", "else", "for", "while", "const", "let", "var", "class", "new", "await", "async", "try", "catch", "throw", "import", "export"},
    ".jsx": {"function", "return", "if", "else", "for", "while", "const", "let", "var", "class", "new", "await", "async", "try", "catch", "throw", "import", "export"},
    ".ts": {"function", "return", "if", "else", "for", "while", "const", "let", "var", "class", "new", "await", "async", "try", "catch", "throw", "import", "export", "interface", "type", "implements", "extends"},
    ".tsx": {"function", "return", "if", "else", "for", "while", "const", "let", "var", "class", "new", "await", "async", "try", "catch", "throw", "import", "export", "interface", "type", "implements", "extends"},
    ".java": {"class", "interface", "enum", "public", "private", "protected", "static", "final", "return", "if", "else", "for", "while", "try", "catch", "throw", "new", "implements", "extends"},
    ".go": {"func", "return", "if", "else", "for", "range", "switch", "case", "type", "struct", "interface", "go", "defer", "select", "package", "import"},
    ".rs": {"fn", "let", "mut", "if", "else", "match", "loop", "while", "for", "return", "struct", "enum", "impl", "trait", "use", "pub", "mod"},
    ".rb": {"def", "class", "module", "if", "elsif", "else", "end", "do", "while", "until", "return", "yield", "begin", "rescue", "ensure"},
    ".c": {"if", "else", "for", "while", "return", "struct", "typedef", "static", "const", "switch", "case", "break", "continue"},
    ".h": {"if", "else", "for", "while", "return", "struct", "typedef", "static", "const", "switch", "case", "break", "continue"},
    ".cc": {"class", "namespace", "if", "else", "for", "while", "return", "struct", "static", "const", "switch", "case", "break", "continue", "template"},
    ".cpp": {"class", "namespace", "if", "else", "for", "while", "return", "struct", "static", "const", "switch", "case", "break", "continue", "template"},
    ".hpp": {"class", "namespace", "if", "else", "for", "while", "return", "struct", "static", "const", "switch", "case", "break", "continue", "template"},
    ".cs": {"class", "interface", "namespace", "public", "private", "protected", "static", "return", "if", "else", "for", "while", "using", "new", "async", "await"},
    ".php": {"function", "class", "public", "private", "protected", "static", "return", "if", "else", "for", "while", "new", "try", "catch"},
    ".swift": {"func", "class", "struct", "enum", "protocol", "let", "var", "if", "else", "for", "while", "return", "guard", "switch", "case"},
    ".kt": {"fun", "class", "interface", "object", "val", "var", "if", "else", "for", "while", "return", "when", "package", "import"},
}

COMMENT_MARKERS: dict[str, tuple[str, ...]] = {
    ".py": ("#",),
    ".rb": ("#",),
    ".sh": ("#",),
    ".yaml": ("#",),
    ".yml": ("#",),
    ".toml": ("#",),
    ".js": ("//",),
    ".jsx": ("//",),
    ".ts": ("//",),
    ".tsx": ("//",),
    ".java": ("//",),
    ".go": ("//",),
    ".rs": ("//",),
    ".c": ("//",),
    ".h": ("//",),
    ".cc": ("//",),
    ".cpp": ("//",),
    ".hpp": ("//",),
    ".cs": ("//",),
    ".php": ("//", "#"),
    ".swift": ("//",),
    ".kt": ("//",),
}

STRING_PATTERN = re.compile(r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')')
NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\b")
WORD_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def _find_comment_start(line: str, markers: tuple[str, ...]) -> int:
    positions = [line.find(marker) for marker in markers if marker in line]
    valid = [position for position in positions if position >= 0]
    return min(valid) if valid else -1


def _append_plain_with_names(parts: list[dict[str, str]], text: str) -> None:
    last = 0
    for match in WORD_PATTERN.finditer(text):
        if match.start() > last:
            parts.append({"text": text[last : match.start()], "kind": "plain"})
        parts.append({"text": match.group(0), "kind": "name"})
        last = match.end()
    if last < len(text):
        parts.append({"text": text[last:], "kind": "plain"})


def _highlight_generic(lines: list[str], suffix: str) -> list[list[dict[str, str]]]:
    keyword_pattern = _build_keyword_pattern(GENERIC_KEYWORDS.get(suffix, set()))
    markers = COMMENT_MARKERS.get(suffix, ("//", "#"))
    rendered: list[list[dict[str, str]]] = []
    for line in lines:
        comment_col = _find_comment_start(line, markers)
        code = line if comment_col == -1 else line[:comment_col]
        cursor = 0
        parts: list[dict[str, str]] = []
        spans: list[tuple[int, int, str]] = []
        if keyword_pattern:
            spans.extend((match.start(), match.end(), "keyword") for match in keyword_pattern.finditer(code))
        spans.extend((match.start(), match.end(), "string") for match in STRING_PATTERN.finditer(code))
        spans.extend((match.start(), match.end(), "number") for match in NUMBER_PATTERN.finditer(code))
        for start, end, kind in sorted(spans, key=lambda item: (item[0], item[1] - item[0])):
            if start < cursor:
                continue
            if start > cursor:
                _append_plain_with_names(parts, code[cursor:start])
            parts.append({"text": code[start:end], "kind": kind})
            cursor = end
        if comment_col != -1:
            if comment_col > cursor:
                _append_plain_with_names(parts, code[cursor:comment_col])
            parts.append({"text": line[comment_col:], "kind": "comment"})
        elif cursor < len(code):
            _append_plain_with_names(parts, code[cursor:])
        rendered.append(parts or [{"text": "", "kind": "plain"}])
    return rendered


def _highlight_python_fallback(lines: list[str]) -> list[list[dict[str, str]]]:
    return [[{"text": line, "kind": "plain"}] for line in lines]


def _highlight_python(lines: list[str]) -> list[list[dict[str, str]]]:
    highlighted = [[{"text": line, "kind": "plain"}] for line in lines]
    token_lines, stripped_indent = _dedent_for_tokenize(lines)
    by_line: dict[int, list[tuple[int, int, str]]] = {}
    try:
        for token_info in tokenize.generate_tokens(io.StringIO("\n".join(token_lines)).readline):
            token_type = token_info.type
            if token_type not in {COMMENT, NAME, NUMBER, OP, STRING}:
                continue
            start_line, start_col = token_info.start
            end_line, end_col = token_info.end
            if start_line != end_line:
                continue
            start_col += stripped_indent
            end_col += stripped_indent
            kind = {
                COMMENT: "comment",
                NUMBER: "number",
                OP: "operator",
                STRING: "string",
            }.get(token_type, "keyword" if keyword.iskeyword(token_info.string) else "name")
            by_line.setdefault(start_line, []).append((start_col, end_col, kind))
    except (IndentationError, SyntaxError, tokenize.TokenError):
        return _highlight_python_fallback(lines)

    rendered: list[list[dict[str, str]]] = []
    for line_no, line in enumerate(lines, start=1):
        cursor = 0
        parts: list[dict[str, str]] = []
        for start_col, end_col, kind in sorted(by_line.get(line_no, [])):
            if start_col > cursor:
                parts.append({"text": line[cursor:start_col], "kind": "plain"})
            parts.append({"text": line[start_col:end_col], "kind": kind})
            cursor = end_col
        if cursor < len(line):
            parts.append({"text": line[cursor:], "kind": "plain"})
        rendered.append(parts or [{"text": "", "kind": "plain"}])
    return rendered


def _highlight_source(lines: list[str], suffix: str) -> list[list[dict[str, str]]]:
    if suffix == ".py":
        return _highlight_python(lines)
    return _highlight_generic(lines, suffix)


def _read_source_block(path: Path, start_line: object, end_line: object) -> list[dict[str, object]]:
    try:
        start = int(start_line)
        end = int(end_line)
    except (TypeError, ValueError):
        return []
    if start < 1 or end < start or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(errors="replace").splitlines()
    selected = [lines[line_no - 1] for line_no in range(start, min(end, len(lines)) + 1)]
    highlighted = _highlight_source(selected, path.suffix.lower())
    return [
        {"number": line_no, "text": text, "tokens": tokens}
        for line_no, text, tokens in zip(range(start, min(end, len(lines)) + 1), selected, highlighted)
    ]


def _normalize_blocks(raw: dict[str, object]) -> list[dict[str, object]]:
    blocks = raw.get("blocks")
    if isinstance(blocks, list):
        return [block for block in blocks if isinstance(block, dict)]

    legacy = raw.get("locations")
    if not isinstance(legacy, list):
        return []
    normalized: list[dict[str, object]] = []
    for index, location in enumerate(legacy, start=1):
        if not isinstance(location, dict):
            continue
        block = dict(location)
        block["id"] = str(block.get("id") or f"block-{index:03d}")
        block["title"] = str(block.get("symbol") or block["id"])
        block["explanation"] = str(block.get("comment") or "")
        normalized.append(block)
    return normalized


def _prepare_walkthrough(raw: dict[str, object], repo_root: Path) -> dict[str, object]:
    prepared = dict(raw)
    prepared["blocks"] = _normalize_blocks(raw)
    prepared.pop("locations", None)
    for block in prepared["blocks"]:
        file_value = block.get("file")
        if not isinstance(file_value, str) or not file_value:
            block["source_lines"] = []
            continue
        path = Path(file_value)
        if not path.is_absolute():
            path = repo_root / path
        block["abs_file"] = str(path.resolve())
        block["source_lines"] = _read_source_block(path, block.get("start_line"), block.get("end_line"))
    return prepared


def _build_html(initial_walkthrough_id: int | None) -> bytes:
    initial_text = "null" if initial_walkthrough_id is None else str(initial_walkthrough_id)
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Code Walkthrough</title>
<style>
:root {{ --bg:#f7f8fa; --panel:#fff; --ink:#17202a; --muted:#667085; --line:#d9dee7; --soft:#f0f3f7; --accent:#087f6f; --accent-soft:#e3f5f1; --code:#111827; --comment:#fffdf7; --comment-line:#d0a23f; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:var(--ink); font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; }}
button,select {{ font:inherit; }} button {{ width:36px; height:34px; border:1px solid var(--line); border-radius:7px; background:var(--panel); color:var(--ink); cursor:pointer; }} button:hover {{ border-color:var(--accent); }} button:disabled {{ color:#a3adba; cursor:not-allowed; }}
.bar {{ position:sticky; top:0; z-index:2; display:grid; grid-template-columns:minmax(180px,320px) 1fr auto; gap:12px; align-items:center; padding:10px 14px; border-bottom:1px solid var(--line); background:rgba(255,255,255,.96); backdrop-filter:blur(8px); }}
select {{ min-width:0; width:100%; border:1px solid var(--line); border-radius:7px; padding:7px 9px; background:var(--panel); color:var(--ink); }}
.heading {{ min-width:0; }} .symbol {{ font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; font-size:13px; font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }} .summary {{ margin-top:2px; color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.nav {{ display:flex; gap:6px; align-items:center; }} .count {{ min-width:62px; text-align:center; color:var(--muted); font-size:12px; }}
.skip-inline {{ width:auto; min-width:74px; height:28px; padding:0 10px; font-size:12px; color:#7a5a12; background:#fff7e3; border-color:#ead8ae; }}
.reader {{ max-width:1040px; margin:0 auto; padding:18px 16px 40px; }}
.context {{ display:grid; grid-template-columns:220px minmax(0,1fr); gap:12px; align-items:stretch; margin-bottom:12px; }}
.stack {{ border:1px solid var(--line); background:var(--panel); border-radius:8px; overflow:hidden; }}
.stack-title {{ padding:7px 10px; color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; border-bottom:1px solid var(--line); }}
.stack-row {{ display:grid; grid-template-columns:22px minmax(0,1fr); gap:7px; align-items:center; padding:7px 10px; color:var(--muted); font-size:12px; }}
.stack-row.active {{ color:#07594f; background:var(--accent-soft); font-weight:700; }} .depth-dot {{ width:14px; height:14px; border:2px solid #b8c2cf; border-radius:50%; }} .stack-row.active .depth-dot {{ border-color:var(--accent); background:var(--accent); }}
.stack-name {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.transition {{ min-width:0; border:1px solid var(--line); background:var(--panel); border-radius:8px; padding:10px 12px; }}
.transition-label {{ color:var(--muted); font-size:11px; font-weight:700; text-transform:uppercase; margin-bottom:5px; }}
.transition-main {{ font-size:15px; font-weight:720; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }} .transition-detail {{ margin-top:3px; color:var(--muted); font-size:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.section {{ margin-top:14px; }} .section:first-of-type {{ margin-top:0; }}
.step {{ border:1px solid var(--line); background:var(--panel); border-radius:8px; overflow:hidden; }}
.step-head {{ display:flex; justify-content:space-between; gap:10px; align-items:center; padding:8px 10px; border-bottom:1px solid var(--line); background:#fbfcfd; }}
.step-head.hidden {{ display:none; }}
.file {{ min-width:0; font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; font-size:12px; color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }} .file a {{ color:var(--accent); text-decoration:none; }}
.pills {{ display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }} .pill {{ border:1px solid var(--line); border-radius:999px; padding:2px 8px; color:#344054; background:var(--soft); font-size:12px; white-space:nowrap; }}
pre {{ margin:0; max-height:60vh; overflow:auto; background:var(--code); color:#e5e7eb; font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; font-size:13px; line-height:1.55; tab-size:4; }}
.line {{ display:grid; grid-template-columns:54px minmax(0,1fr); min-width:max-content; }} .ln {{ color:#93a4b8; user-select:none; text-align:right; padding:0 12px 0 8px; border-right:1px solid #374151; }} .src {{ white-space:pre; padding:0 14px; }}
.tok-keyword {{ color:#c084fc; }} .tok-name {{ color:#e5e7eb; }} .tok-string {{ color:#86efac; }} .tok-number {{ color:#fbbf24; }} .tok-comment {{ color:#94a3b8; font-style:italic; }} .tok-operator {{ color:#93c5fd; }} .tok-next-target {{ color:#fde68a; background:rgba(253,230,138,.16); border-radius:4px; box-shadow:0 0 0 1px rgba(253,230,138,.22); }}
.comment-wrap {{ display:grid; grid-template-columns:54px minmax(0,1fr); margin-top:0; }} .thread-line {{ display:flex; justify-content:center; background:#fbfcfd; }} .thread-line span {{ width:2px; background:var(--comment-line); }}
.comment-list {{ display:grid; gap:8px; padding:10px; border-top:1px solid #ead8ae; background:#fbfcfd; }}
.comment {{ border:1px solid #f0dfb7; border-left:4px solid #e2bf64; border-radius:7px; background:var(--comment); padding:12px 14px; }} .comment.active {{ border-left-color:var(--comment-line); background:#fff8e8; }} .comment h1 {{ margin:0 0 6px; font-size:14px; font-weight:700; line-height:1.3; color:#6f4f10; }} .comment p {{ margin:0; color:#2f3a4a; line-height:1.6; }}
.next-note {{ display:grid; grid-template-columns:54px minmax(0,1fr); background:#fffaf0; border-top:1px solid #ead8ae; color:#7a5a12; font-size:13px; }} .next-note-gutter {{ border-right:1px solid #ead8ae; background:#fbfcfd; }} .next-note-text {{ display:flex; justify-content:space-between; align-items:center; gap:12px; padding:9px 14px; }} .next-note-label {{ min-width:0; }}
.comment-context {{ margin-bottom:8px; color:#8a6a28; font-size:12px; font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; }}
.empty {{ margin:18px; padding:18px; color:var(--muted); background:var(--panel); border:1px dashed var(--line); border-radius:8px; }}
@media (max-width:760px) {{ .bar {{ grid-template-columns:1fr; }} .nav {{ justify-content:space-between; }} .reader {{ padding:12px; }} .context {{ grid-template-columns:1fr; }} }}
</style></head><body><header class="bar"><select id="walkthroughSelect" aria-label="Walkthrough"></select><div class="heading"><div id="symbol" class="symbol"></div><div id="summary" class="summary"></div></div><div class="nav"><button id="prev" title="Previous block" aria-label="Previous block">&larr;</button><span id="count" class="count"></span><button id="next" title="Next block" aria-label="Next block">&rarr;</button></div></header><main id="content"></main>
<script>
const initialWalkthroughId={initial_text}; let catalog=[]; let current=null; let stepIndex=0;
function escapeHtml(value) {{ return String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;"); }}
function vscodeLink(file,line) {{ if (!file) return "#"; return `vscode://file/${{encodeURI(file)}}:${{Number.isInteger(line) ? line : 1}}`; }}
function currentBlocks() {{ return current?.walkthrough?.blocks || []; }}
function currentCallGraph() {{ return Array.isArray(current?.walkthrough?.call_graph) ? current.walkthrough.call_graph : []; }}
function renderCatalog() {{ document.getElementById("walkthroughSelect").innerHTML=catalog.map((entry)=>`<option value="${{entry.id}}" ${{current && current.walkthrough_id===entry.id ? "selected" : ""}}>${{escapeHtml(entry.label)}}</option>`).join(""); }}
function nameOf(block) {{ return block ? (block.symbol || block.title || block.id || "unknown") : ""; }}
function shortName(value) {{ return String(value || "").split(".").pop(); }}
function targetLabel(value) {{ const parts=String(value || "").split(".").filter(Boolean); if (parts.length >= 2 && /^[A-Z]/.test(parts[parts.length - 2])) return `${{parts[parts.length - 2]}}.${{parts[parts.length - 1]}}`; return parts[parts.length - 1] || "unknown"; }}
function lineInBlock(lineNumber, block) {{ const line=Number(lineNumber); const start=Number(block?.start_line ?? NaN); const end=Number(block?.end_line ?? NaN); return Number.isInteger(line) && Number.isInteger(start) && Number.isInteger(end) && line >= start && line <= end; }}
// AIDEV-NOTE: "Entering" is only valid when the current block contains a real call edge into the next block.
function isDirectCallTransition(fromBlock, toBlock) {{ if (!fromBlock || !toBlock || samePage(fromBlock,toBlock)) return false; const fromDepth=Number(fromBlock.depth ?? 0); const toDepth=Number(toBlock.depth ?? 0); if (toDepth !== fromDepth + 1) return false; const fromName=nameOf(fromBlock); const toName=nameOf(toBlock); const calledFrom=String(toBlock.called_from || ""); if (calledFrom && calledFrom !== fromName) return false; const matchingEdges=currentCallGraph().filter((edge)=>edge && edge.resolved !== false && String(edge.caller || "")===fromName && String(edge.callee || "")===toName && lineInBlock(edge.call_line, fromBlock)); if (matchingEdges.length) return true; return !currentCallGraph().length && calledFrom === fromName; }}
function findCallerIndex(blocks, fromIndex, callerName, callerDepth) {{ for (let i=fromIndex; i>=0; i--) {{ const block=blocks[i]; if (nameOf(block)===callerName && Number(block.depth ?? 0)===callerDepth) return i; }} return Math.max(0, fromIndex); }}
function buildSteps(blocks) {{ const steps=[]; for (let i=0; i<blocks.length; i++) {{ steps.push({{kind:"block", activeIndex:i, visibleIndex:i}}); const currentBlock=blocks[i]; const nextBlock=blocks[i+1]; if (!nextBlock) continue; const currentDepth=Number(currentBlock.depth ?? 0); const nextDepth=Number(nextBlock.depth ?? 0); const siblingNestedJump=currentDepth===nextDepth && currentDepth>0 && !samePage(currentBlock,nextBlock) && currentBlock.called_from && nextBlock.called_from && currentBlock.called_from===nextBlock.called_from; if (!siblingNestedJump) continue; const callerIndex=findCallerIndex(blocks, i, String(nextBlock.called_from), nextDepth-1); steps.push({{kind:"return", activeIndex:callerIndex, visibleIndex:callerIndex, nextIndex:i+1}}); }} return steps; }}
function currentSteps() {{ return buildSteps(currentBlocks()); }}
function currentStep() {{ return currentSteps()[stepIndex]; }}
function nextStep() {{ return currentSteps()[stepIndex + 1]; }}
function shouldOfferSkip() {{ const stepAhead=nextStep(); const currentBlock=currentBlocks()[activeBlockIndex()]; const nextBlock=currentBlocks()[stepAhead?.activeIndex ?? -1]; return Boolean(stepAhead && stepAhead.kind==="block" && isDirectCallTransition(currentBlock, nextBlock)); }}
function skipTargetIndex() {{ const steps=currentSteps(); const blocks=currentBlocks(); const currentBlock=blocks[activeBlockIndex()]; const callerDepth=Number(currentBlock?.depth ?? 0); for (let i=stepIndex + 1; i<steps.length; i++) {{ const step=steps[i]; const block=blocks[step.visibleIndex]; if (!block) continue; if (Number(block.depth ?? 0) <= callerDepth) return i; }} return stepIndex; }}
function skipCall() {{ const target=skipTargetIndex(); if (target!==stepIndex) {{ stepIndex=target; renderBlock(); }} }}
function activeBlockIndex() {{ return currentStep()?.activeIndex ?? 0; }}
function visibleBlockIndex() {{ return currentStep()?.visibleIndex ?? activeBlockIndex(); }}
function upcomingBlockIndex() {{ const step=currentStep(); if (!step) return -1; if (step.kind==="return") return step.nextIndex ?? -1; return activeBlockIndex() + 1; }}
function stackFor(blocks) {{ const activeIndex=activeBlockIndex(); const block=blocks[activeIndex]; const stack=[]; for (let i=0;i<=activeIndex;i++) {{ const candidate=blocks[i]; const depth=Number(candidate.depth ?? 0); if (depth <= Number(block.depth ?? 0)) {{ stack[depth]=nameOf(candidate); stack.length=depth+1; }} }} return stack; }}
function transition(blocks) {{ const step=currentStep(); const block=blocks[activeBlockIndex()]; if (!step || !block) return {{main:"", detail:""}}; if (step.kind==="return") {{ const nextBlock=blocks[step.nextIndex]; return {{main:`Returned to ${{nameOf(block)}}`, detail:nextBlock ? `Next: ${{shortName(nameOf(nextBlock))}}` : "End of walkthrough"}}; }} const previous=blocks[activeBlockIndex()-1]; const next=blocks[activeBlockIndex()+1]; const currentDepth=Number(block.depth ?? 0); const previousDepth=Number(previous?.depth ?? currentDepth); if (previous && isDirectCallTransition(previous, block)) return {{main:`Entered ${{nameOf(block)}}`, detail:`Called from ${{nameOf(previous)}}`}}; if (previous && currentDepth<previousDepth) return {{main:`Returned to ${{nameOf(block)}}`, detail:`Back from ${{nameOf(previous)}}`}}; if (currentDepth>0) return {{main:`Inside nested function ${{nameOf(block)}}`, detail:block.called_from ? `Called from ${{block.called_from}}` : "Nested call"}}; if (next && isDirectCallTransition(block, next)) return {{main:`Next enters ${{nameOf(next)}}`, detail:`This block calls ${{nameOf(next)}}`}}; return {{main:currentDepth===0 ? "In entrypoint" : `Inside ${{nameOf(block)}}`, detail:next ? `Next: ${{nameOf(next)}}` : "End of walkthrough"}}; }}
function renderStack(blocks) {{ const stack=stackFor(blocks); return `<div class="stack"><div class="stack-title">Call stack</div>${{stack.map((name,index)=>`<div class="stack-row ${{index===stack.length-1 ? "active" : ""}}"><span class="depth-dot"></span><span class="stack-name">${{escapeHtml(name)}}</span></div>`).join("")}}</div>`; }}
function renderTokens(line,targetName="") {{ const tokens=line.tokens || [{{text:line.text || "", kind:"plain"}}]; return tokens.map((token)=>{{ const classes=[`tok-${{token.kind || "plain"}}`]; if (targetName && token.kind==="name" && token.text===targetName) classes.push("tok-next-target"); return `<span class="${{classes.map(escapeHtml).join(" ")}}">${{escapeHtml(token.text)}}</span>`; }}).join(""); }}
function samePage(previous,block) {{ return previous && previous.file===block.file && nameOf(previous)===nameOf(block) && Number(previous.depth ?? 0)===Number(block.depth ?? 0); }}
function visibleGroups(blocks, limitIndex) {{ const groups=[]; for (const block of blocks.slice(0,limitIndex+1)) {{ const group=groups[groups.length-1]; const previous=group?.blocks[group.blocks.length-1]; if (group && samePage(previous,block)) {{ group.blocks.push(block); group.endLine=Number(block.end_line ?? group.endLine); continue; }} groups.push({{blocks:[block], startLine:Number(block.start_line ?? 0), endLine:Number(block.end_line ?? 0)}}); }} return groups; }}
function nextTargetFor(block) {{ const blocks=currentBlocks(); const stepAhead=nextStep(); const next=blocks[upcomingBlockIndex()]; if (block!==blocks[activeBlockIndex()] || !next || !stepAhead || stepAhead.kind==="return" || !isDirectCallTransition(block, next)) return ""; return shortName(nameOf(next)); }}
function renderCodeLines(lines,targetName="") {{ if (!lines.length) return '<div class="empty">Source lines were not available for this block.</div>'; return `<pre>${{lines.map((line)=>`<span class="line"><span class="ln">${{escapeHtml(line.number)}}</span><span class="src">${{renderTokens(line,targetName)}}</span></span>`).join("")}}</pre>`; }}
function nextNote(blocks) {{ const stepAhead=nextStep(); if (!stepAhead) return "End of walkthrough."; if (stepAhead.kind==="return") {{ const caller=blocks[stepAhead.visibleIndex]; return `Returning to ${{targetLabel(nameOf(caller))}}.`; }} const block=blocks[activeBlockIndex()]; const next=blocks[upcomingBlockIndex()]; if (!next) return "End of walkthrough."; if (samePage(block,next)) return "See next lines."; if (isDirectCallTransition(block, next)) return `Entering ${{targetLabel(nameOf(next))}}.`; if (Number(next.depth ?? 0) < Number(block.depth ?? 0)) return `Returning to ${{targetLabel(nameOf(next))}}.`; return `Continuing to ${{targetLabel(nameOf(next))}}.`; }}
function renderNextNote(block) {{ if (block!==currentBlocks()[activeBlockIndex()]) return ""; const showSkip=shouldOfferSkip(); return `<div class="next-note"><div class="next-note-gutter"></div><div class="next-note-text"><span class="next-note-label">${{escapeHtml(nextNote(currentBlocks()))}}</span>${{showSkip ? '<button class="skip-inline" type="button" onclick="skipCall()">Skip Call</button>' : ""}}</div></div>`; }}
function commentFor(block) {{ const isActive=block===currentBlocks()[activeBlockIndex()]; return `<div class="comment ${{isActive ? "active" : ""}}">${{block.called_from ? `<div class="comment-context">from ${{escapeHtml(block.called_from)}}</div>` : ""}}<h1>${{escapeHtml(block.title || block.symbol || block.id || "Untitled block")}}</h1><p>${{escapeHtml(block.explanation || block.comment || "")}}</p></div>`; }}
function linesForBlock(block,previousBlock,seenInRun) {{ const sameRun=previousBlock && previousBlock.file===block.file && nameOf(previousBlock)===nameOf(block) && Number(previousBlock.depth ?? 0)===Number(block.depth ?? 0); if (!sameRun) seenInRun.clear(); const lines=[]; for (const line of (block.source_lines || [])) {{ const key=`${{block.file}}:${{nameOf(block)}}:${{line.number}}`; if (seenInRun.has(key)) continue; seenInRun.add(key); lines.push(line); }} return lines; }}
function renderGroup(group) {{ const first=group.blocks[0]; const seenInRun=new Set(); let previousBlock=null; const body=group.blocks.map((block)=>{{ const lines=linesForBlock(block,previousBlock,seenInRun); const targetName=nextTargetFor(block); previousBlock=block; return `${{renderCodeLines(lines,targetName)}}<div class="comment-wrap"><div class="thread-line"><span></span></div><div class="comment-list">${{commentFor(block)}}</div></div>${{renderNextNote(block)}}`; }}).join(""); return `<section class="section"><article class="step"><div class="step-head"><div class="file"><a href="${{vscodeLink(first.abs_file || first.file,group.startLine)}}">${{escapeHtml(first.file)}}</a></div></div>${{body}}</article></section>`; }}
function renderBlock() {{ const content=document.getElementById("content"); const blocks=currentBlocks(); const data=current?.walkthrough || {{}}; const entrypoint=data.entrypoint || {{}}; const analysis=data.analysis || {{}}; const steps=currentSteps(); document.getElementById("symbol").textContent=entrypoint.symbol || "Unknown entrypoint"; document.getElementById("summary").textContent=analysis.summary || ""; document.getElementById("count").textContent=steps.length ? `${{stepIndex + 1}} / ${{steps.length}}` : "0 / 0"; document.getElementById("prev").disabled=stepIndex<=0; document.getElementById("next").disabled=stepIndex>=steps.length-1; if (!blocks.length || !steps.length) {{ content.innerHTML='<div class="empty">This walkthrough has no blocks.</div>'; return; }} const move=transition(blocks); const groups=visibleGroups(blocks, visibleBlockIndex()); const currentPage=groups[groups.length-1]; content.innerHTML=`<section class="reader"><div class="context">${{renderStack(blocks)}}<div class="transition"><div class="transition-label">Position</div><div class="transition-main">${{escapeHtml(move.main)}}</div><div class="transition-detail">${{escapeHtml(move.detail)}}</div></div></div>${{renderGroup(currentPage)}}</section>`; }}
async function loadWalkthrough(id) {{ const response=await fetch(`/api/walkthrough?id=${{id}}`); current=await response.json(); stepIndex=0; if (current.error) {{ document.getElementById("content").innerHTML=`<div class="empty">${{escapeHtml(current.error)}}</div>`; return; }} renderCatalog(); renderBlock(); }}
async function loadCatalog() {{ catalog=await (await fetch("/api/walkthroughs")).json(); renderCatalog(); if (catalog.length) await loadWalkthrough(initialWalkthroughId ?? catalog[0].id); else document.getElementById("content").innerHTML='<div class="empty">No walkthrough JSON files found.</div>'; }}
document.getElementById("walkthroughSelect").addEventListener("change",(event)=>loadWalkthrough(Number(event.target.value)));
document.getElementById("prev").addEventListener("click",()=>{{ if (stepIndex>0) {{ stepIndex--; renderBlock(); }} }});
document.getElementById("next").addEventListener("click",()=>{{ if (stepIndex<currentSteps().length-1) {{ stepIndex++; renderBlock(); }} }});
document.addEventListener("keydown",(event)=>{{ if (event.key==="ArrowLeft") document.getElementById("prev").click(); if (event.key==="ArrowRight") document.getElementById("next").click(); }});
loadCatalog();
</script></body></html>"""
    return html.encode("utf-8")


def _make_handler(repo_root: Path, entries: list[WalkthroughEntry], html: bytes) -> type[BaseHTTPRequestHandler]:
    by_id = {entry.id: entry for entry in entries}
    catalog_payload = _json_bytes(
        [
            {
                "id": entry.id,
                "label": entry.label,
                "path": str(entry.path),
                "search_text": entry.search_text,
            }
            for entry in entries
        ]
    )

    class WalkthroughHandler(BaseHTTPRequestHandler):
        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send(200, "text/html; charset=utf-8", html)
                return
            if parsed.path == "/api/walkthroughs":
                self._send(200, "application/json; charset=utf-8", catalog_payload)
                return
            if parsed.path == "/api/walkthrough":
                query = parse_qs(parsed.query)
                try:
                    walkthrough_id = int((query.get("id") or [""])[0])
                except ValueError:
                    self._send(400, "application/json; charset=utf-8", _json_bytes({"error": "Invalid id"}))
                    return
                entry = by_id.get(walkthrough_id)
                if entry is None:
                    self._send(404, "application/json; charset=utf-8", _json_bytes({"error": "Walkthrough not found"}))
                    return
                try:
                    raw = json.loads(entry.path.read_text(encoding="utf-8"))
                    payload = {
                        "walkthrough_id": entry.id,
                        "walkthrough_path": str(entry.path),
                        "walkthrough_label": entry.label,
                        "walkthrough": _prepare_walkthrough(raw, repo_root),
                    }
                except Exception as exc:
                    self._send(
                        500,
                        "application/json; charset=utf-8",
                        _json_bytes({"error": f"Failed to load walkthrough: {exc}"}),
                    )
                    return
                self._send(200, "application/json; charset=utf-8", _json_bytes(payload))
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    return WalkthroughHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a browser UI to inspect code-walkthrough JSON files.")
    parser.add_argument(
        "paths",
        nargs="+",
        help="Walkthrough JSON files or directories containing walkthrough JSON files.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8766, help="Port to bind (default: 8766).")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open a browser.")
    args = parser.parse_args()

    repo_root = _repo_root()
    roots = [Path(path).resolve() for path in args.paths]
    entries = _build_catalog(repo_root=repo_root, roots=roots)
    if not entries:
        parser.error("No valid code-walkthrough JSON files found.")

    html = _build_html(entries[0].id)
    handler = _make_handler(repo_root, entries, html)
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        url = f"http://{args.host}:{args.port}/"
        print(f"Serving {len(entries)} code walkthrough file(s) at {url}")
        print("Use left and right arrow keys to move between code blocks.")
        print("Press Ctrl+C to stop.")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
