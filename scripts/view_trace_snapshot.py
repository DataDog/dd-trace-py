#!/usr/bin/env python3
import argparse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse
import webbrowser


def _normalize_traces(raw: object) -> list[list[dict[str, object]]]:
    if not isinstance(raw, list):
        raise ValueError("Snapshot root must be a list.")
    if not raw:
        return []
    if isinstance(raw[0], dict):
        return [raw]
    if isinstance(raw[0], list):
        for trace in raw:
            if not isinstance(trace, list):
                raise ValueError("Expected each trace to be a list of spans.")
        return raw
    raise ValueError("Unsupported snapshot format.")


def _short(value: object, max_len: int = 64) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _color_for_service(service: str) -> str:
    h = 0
    for c in service:
        h = (h * 33 + ord(c)) % 360
    return f"hsl({h}, 70%, 55%)"


def _to_int(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _build_trace_search_text(spans: list[dict[str, object]]) -> str:
    parts: list[str] = []
    for span in spans:
        for key in ("name", "service", "resource"):
            parts.append(str(span.get(key, "")))
    return " ".join(parts).lower()


def _layout_trace(trace_index: int, spans: list[dict[str, object]]) -> dict[str, object]:
    normalized = []
    for span in spans:
        start = span.get("start")
        duration = span.get("duration")
        if not isinstance(start, int) or not isinstance(duration, int):
            continue
        normalized.append(dict(span))

    if not normalized:
        return {
            "index": trace_index,
            "label": f"#{trace_index}",
            "search_text": "",
            "rows_count": 0,
            "trace_duration_ms": 0.0,
            "spans": [],
        }

    normalized.sort(key=lambda s: (_to_int(s.get("start")), -_to_int(s.get("duration"))))
    trace_start = min(_to_int(span.get("start")) for span in normalized)
    trace_end = max(_to_int(span.get("start")) + _to_int(span.get("duration")) for span in normalized)
    trace_duration_ns = max(1, trace_end - trace_start)

    id_to_span: dict[int, dict[str, object]] = {}
    children: dict[int, list[dict[str, object]]] = {}
    roots: list[dict[str, object]] = []
    for span in normalized:
        span_id = _to_int(span.get("span_id"), default=-1)
        if span_id == -1:
            span_id = id(span)
        span["__normalized_span_id"] = span_id
        id_to_span[span_id] = span

    for span in normalized:
        span_id = _to_int(span.get("__normalized_span_id"), default=-1)
        parent_id = _to_int(span.get("parent_id"), default=0)
        if parent_id <= 0 or parent_id == span_id or parent_id not in id_to_span:
            roots.append(span)
            continue
        children.setdefault(parent_id, []).append(span)

    for child_list in children.values():
        child_list.sort(key=lambda s: (_to_int(s.get("start")), -_to_int(s.get("duration"))))
    roots.sort(key=lambda s: (_to_int(s.get("start")), -_to_int(s.get("duration"))))

    ordered: list[tuple[dict[str, object], int]] = []
    visited: set[int] = set()

    def walk(node: dict[str, object], depth: int) -> None:
        node_id = _to_int(node.get("__normalized_span_id"), default=-1)
        if node_id in visited:
            return
        visited.add(node_id)
        ordered.append((node, depth))
        for child in children.get(node_id, []):
            walk(child, depth + 1)

    for root in roots:
        walk(root, depth=0)
    for span in normalized:
        walk(span, depth=0)

    rendered_spans = []
    for row, (span, depth) in enumerate(ordered):
        start = _to_int(span.get("start"))
        duration = _to_int(span.get("duration"))
        resource_name = str(span.get("resource", "") or "")
        operation_name = str(span.get("name", "") or "")
        display_name = resource_name or operation_name
        left = ((start - trace_start) / trace_duration_ns) * 100.0
        width = max((duration / trace_duration_ns) * 100.0, 0.25)
        meta_obj = span.get("meta")
        meta = meta_obj if isinstance(meta_obj, dict) else {}
        metrics_obj = span.get("metrics")
        metrics = metrics_obj if isinstance(metrics_obj, dict) else {}
        rendered_spans.append(
            {
                "name": display_name,
                "service": span.get("service", ""),
                "resource": resource_name,
                "trace_id": span.get("trace_id", 0),
                "span_id": span.get("span_id", 0),
                "parent_id": span.get("parent_id", 0),
                "error": _to_int(span.get("error"), default=0),
                "start_ms": round((start - trace_start) / 1_000_000.0, 3),
                "duration_ms": round(duration / 1_000_000.0, 3),
                "left": round(left, 5),
                "width": round(width, 5),
                "row": row,
                "depth": depth,
                "label": _short(display_name),
                "error_type": _short(meta.get("error.type", ""), max_len=100),
                "color": _color_for_service(str(span.get("service", "unknown"))),
                "meta_fields": meta,
                "metrics": metrics,
            }
        )

    root_span = rendered_spans[0] if rendered_spans else {}
    root_name = str(root_span.get("name", "")) if root_span else ""
    root_service = str(root_span.get("service", "")) if root_span else ""
    label = f"#{trace_index} - {root_service}:{root_name}" if root_name or root_service else f"#{trace_index}"
    return {
        "index": trace_index,
        "label": _short(label, max_len=120),
        "search_text": _build_trace_search_text(normalized),
        "rows_count": len(ordered),
        "trace_duration_ms": round((trace_end - trace_start) / 1_000_000.0, 3),
        "spans": rendered_spans,
    }


def _build_html(initial_snapshot_id: int | None) -> bytes:
    initial_text = "null" if initial_snapshot_id is None else str(initial_snapshot_id)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Trace Snapshot UI</title>
  <style>
    :root {{
      --row-height: 24px;
      --name-col: 280px;
      --snapshot-col: 300px;
    }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      color: #1f2937;
      background: #f8fafc;
    }}
    .app {{ display: grid; grid-template-columns: var(--snapshot-col) 1fr; min-height: 100vh; }}
    .sidebar {{ border-right: 1px solid #e5e7eb; background: #fff; padding: 14px; overflow-y: auto; }}
    .main {{ padding: 16px; overflow: auto; }}
    h1 {{ margin: 0 0 6px 0; font-size: 18px; }}
    .sub {{ color: #6b7280; font-size: 12px; margin-bottom: 12px; word-break: break-word; }}
    .search {{
      width: 100%;
      box-sizing: border-box;
      padding: 8px 10px;
      border: 1px solid #d1d5db;
      border-radius: 8px;
      margin-bottom: 8px;
      font-size: 13px;
    }}
    .summary {{
      margin-bottom: 10px;
      color: #6b7280;
      font-size: 12px;
    }}
    .item {{
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      padding: 8px 10px;
      margin-bottom: 8px;
      cursor: pointer;
      background: #fff;
      font-size: 12px;
      word-break: break-word;
    }}
    .item:hover {{ background: #f9fafb; }}
    .item.active {{ border-color: #111827; background: #f3f4f6; }}
    .controls {{
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
      margin-bottom: 10px;
      font-size: 13px;
    }}
    .timeline-wrap {{
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      overflow-x: auto;
      background: #fff;
    }}
    .trace-grid {{
      display: grid;
      grid-template-columns: var(--name-col) 8px minmax(640px, 1fr);
      min-width: 960px;
    }}
    .names-col {{
      position: relative;
      border-right: 1px solid #e5e7eb;
      background: #fafafa;
    }}
    .splitter {{
      cursor: col-resize;
      background: #f3f4f6;
      border-left: 1px solid #e5e7eb;
      border-right: 1px solid #e5e7eb;
    }}
    .spans-col {{
      position: relative;
      background-image: linear-gradient(to right, rgba(0,0,0,0.06) 1px, transparent 1px);
      background-size: 10% 100%;
      border-top: 1px solid #f3f4f6;
    }}
    .row-line {{
      position: absolute;
      left: 0;
      right: 0;
      border-top: 1px dashed #f3f4f6;
    }}
    .row-label {{
      position: absolute;
      left: 8px;
      right: 8px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      line-height: 20px;
      font-size: 12px;
      color: #111827;
    }}
    .span {{
      position: absolute;
      height: 20px;
      border-radius: 4px;
      border: 1px solid rgba(0, 0, 0, 0.15);
      cursor: pointer;
    }}
    .span.error {{ outline: 2px solid #b91c1c; }}
    .span.selected {{ box-shadow: 0 0 0 2px #111827 inset; }}
    .legend {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 8px;
      font-size: 12px;
      color: #4b5563;
    }}
    .legend-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
      margin-right: 5px;
      vertical-align: middle;
    }}
    .meta {{
      margin-top: 10px;
      font-size: 12px;
      color: #374151;
    }}
    .meta-card {{
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      background: #fff;
      padding: 12px;
    }}
    .meta-title {{
      font-size: 13px;
      color: #111827;
      margin-bottom: 10px;
    }}
    .meta-sections {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .meta-section {{
      border: 1px solid #e5e7eb;
      border-radius: 6px;
      overflow: hidden;
      background: #fafafa;
    }}
    .meta-section-title {{
      padding: 8px 10px;
      font-weight: 600;
      font-size: 12px;
      color: #111827;
      background: #f3f4f6;
      border-bottom: 1px solid #e5e7eb;
    }}
    .meta-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }}
    .meta-table td {{
      padding: 6px 8px;
      border-bottom: 1px solid #eceff1;
      vertical-align: top;
      word-break: break-word;
    }}
    .meta-table tr:last-child td {{ border-bottom: none; }}
    .meta-key {{
      width: 35%;
      color: #374151;
      background: #f9fafb;
    }}
    .meta-value {{ color: #111827; white-space: pre-wrap; }}
    .meta-empty {{
      padding: 10px;
      color: #6b7280;
      font-size: 12px;
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <h1>Trace Snapshot UI</h1>
      <div class="sub">Browse snapshot files and inspect traces.</div>
      <input id="snapshotSearch" class="search" type="search" placeholder="Search snapshot path..." />
      <div id="snapshotSummary" class="summary"></div>
      <div id="snapshotList"></div>
    </aside>
    <main class="main">
      <div class="controls">
        <div id="selectedSnapshotLabel"></div>
        <input
          id="traceSearch"
          class="search"
          type="search"
          placeholder="Search traces..."
          style="max-width:320px; margin:0;"
        />
        <label><input type="checkbox" id="onlyErrors" /> Only error spans</label>
        <div id="traceSummary"></div>
      </div>
      <div id="traceListSummary" class="summary"></div>
      <div id="traceList" style="max-height: 120px; overflow-y:auto; margin-bottom:8px;"></div>
      <div class="timeline-wrap">
        <div id="traceGrid" class="trace-grid">
          <div id="namesCol" class="names-col"></div>
          <div id="splitter" class="splitter" title="Drag to resize name column"></div>
          <div id="spansCol" class="spans-col"></div>
        </div>
      </div>
      <div id="legend" class="legend"></div>
      <div id="meta" class="meta"></div>
    </main>
  </div>
  <script>
    const INITIAL_SNAPSHOT_ID = {initial_text};
    const rowHeight = 24;
    const snapshotSearch = document.getElementById("snapshotSearch");
    const snapshotSummary = document.getElementById("snapshotSummary");
    const snapshotList = document.getElementById("snapshotList");
    const selectedSnapshotLabel = document.getElementById("selectedSnapshotLabel");
    const traceSearch = document.getElementById("traceSearch");
    const traceListSummary = document.getElementById("traceListSummary");
    const traceList = document.getElementById("traceList");
    const onlyErrors = document.getElementById("onlyErrors");
    const traceSummary = document.getElementById("traceSummary");
    const traceGrid = document.getElementById("traceGrid");
    const splitter = document.getElementById("splitter");
    const namesCol = document.getElementById("namesCol");
    const spansCol = document.getElementById("spansCol");
    const legend = document.getElementById("legend");
    const meta = document.getElementById("meta");

    let snapshots = [];
    let filteredSnapshotIds = [];
    let selectedSnapshotId = null;
    let selectedSnapshotData = null;
    let filteredTraceIndexes = [];
    let selectedTraceIndex = 0;
    let selectedSpanId = null;
    let selectedSpanData = null;

    function toDisplayValue(value) {{
      if (value === null || value === undefined) return "";
      if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
      try {{ return JSON.stringify(value); }} catch (_err) {{ return String(value); }}
    }}

    function renderKeyValueSection(container, title, obj) {{
      const section = document.createElement("div");
      section.className = "meta-section";
      const heading = document.createElement("div");
      heading.className = "meta-section-title";
      heading.textContent = title;
      section.appendChild(heading);
      const entries =
        obj && typeof obj === "object"
          ? Object.entries(obj).sort((a, b) => a[0].localeCompare(b[0]))
          : [];
      if (!entries.length) {{
        const empty = document.createElement("div");
        empty.className = "meta-empty";
        empty.textContent = "No data";
        section.appendChild(empty);
        container.appendChild(section);
        return;
      }}
      const table = document.createElement("table");
      table.className = "meta-table";
      const tbody = document.createElement("tbody");
      for (const [key, value] of entries) {{
        const tr = document.createElement("tr");
        const keyCell = document.createElement("td");
        keyCell.className = "meta-key";
        keyCell.textContent = key;
        const valueCell = document.createElement("td");
        valueCell.className = "meta-value";
        valueCell.textContent = toDisplayValue(value);
        tr.appendChild(keyCell);
        tr.appendChild(valueCell);
        tbody.appendChild(tr);
      }}
      table.appendChild(tbody);
      section.appendChild(table);
      container.appendChild(section);
    }}

    function renderSelectedSpan() {{
      if (!selectedSpanData) {{
        meta.textContent = "Click a span to inspect metadata.";
        return;
      }}
      meta.innerHTML = "";
      const card = document.createElement("div");
      card.className = "meta-card";
      const title = document.createElement("div");
      title.className = "meta-title";
      title.textContent = `Selected span: ${{selectedSpanData.name}} (id=${{selectedSpanData.span_id}})`;
      card.appendChild(title);
      const sections = document.createElement("div");
      sections.className = "meta-sections";
      renderKeyValueSection(sections, "meta", selectedSpanData.meta_fields);
      renderKeyValueSection(sections, "metrics", selectedSpanData.metrics);
      card.appendChild(sections);
      meta.appendChild(card);
    }}

    function renderSnapshotList() {{
      snapshotList.innerHTML = "";
      snapshotSummary.textContent = `${{filteredSnapshotIds.length}} / ${{snapshots.length}} snapshot(s)`;
      if (!filteredSnapshotIds.length) {{
        const item = document.createElement("div");
        item.className = "item";
        item.textContent = "No snapshot matches this search.";
        snapshotList.appendChild(item);
        return;
      }}
      for (const id of filteredSnapshotIds) {{
        const snap = snapshots[id];
        const item = document.createElement("div");
        item.className = "item" + (id === selectedSnapshotId ? " active" : "");
        item.textContent = snap.label;
        item.title = snap.path;
        item.addEventListener("click", () => loadSnapshot(id));
        snapshotList.appendChild(item);
      }}
    }}

    function applySnapshotSearch() {{
      const q = (snapshotSearch.value || "").trim().toLowerCase();
      filteredSnapshotIds = snapshots
        .map((snap) => snap.id)
        .filter((id) => {{
          const snap = snapshots[id];
          return !q || snap.search_text.includes(q) || snap.label.toLowerCase().includes(q);
        }});
      if (!filteredSnapshotIds.includes(selectedSnapshotId)) {{
        selectedSnapshotId = filteredSnapshotIds.length ? filteredSnapshotIds[0] : null;
      }}
      renderSnapshotList();
      if (
        selectedSnapshotId !== null &&
        (!selectedSnapshotData || selectedSnapshotData.snapshot_id !== selectedSnapshotId)
      ) {{
        loadSnapshot(selectedSnapshotId);
      }}
    }}

    function applyTraceSearch() {{
      if (!selectedSnapshotData) {{
        filteredTraceIndexes = [];
        renderTraceList();
        renderTimeline();
        return;
      }}
      const q = (traceSearch.value || "").trim().toLowerCase();
      const traces = selectedSnapshotData.traces;
      filteredTraceIndexes = traces
        .map((trace) => trace.index)
        .filter((index) => {{
          const trace = traces[index];
          return !q || trace.search_text.includes(q) || trace.label.toLowerCase().includes(q);
        }});
      if (!filteredTraceIndexes.includes(selectedTraceIndex)) {{
        selectedTraceIndex = filteredTraceIndexes.length ? filteredTraceIndexes[0] : 0;
        selectedSpanId = null;
        selectedSpanData = null;
      }}
      renderTraceList();
      renderTimeline();
    }}

    function renderTraceList() {{
      traceList.innerHTML = "";
      if (!selectedSnapshotData) {{
        traceListSummary.textContent = "";
        return;
      }}
      const traces = selectedSnapshotData.traces;
      traceListSummary.textContent = `${{filteredTraceIndexes.length}} / ${{traces.length}} trace(s)`;
      for (const traceIndex of filteredTraceIndexes) {{
        const trace = traces[traceIndex];
        const item = document.createElement("div");
        item.className = "item" + (traceIndex === selectedTraceIndex ? " active" : "");
        item.textContent = trace.label;
        item.addEventListener("click", () => {{
          selectedTraceIndex = traceIndex;
          selectedSpanId = null;
          selectedSpanData = null;
          renderTraceList();
          renderTimeline();
        }});
        traceList.appendChild(item);
      }}
      if (!filteredTraceIndexes.length) {{
        const item = document.createElement("div");
        item.className = "item";
        item.textContent = "No trace matches this search.";
        traceList.appendChild(item);
      }}
    }}

    function renderTimeline() {{
      const emptyTrace = {{ index: 0, spans: [], rows_count: 0, trace_duration_ms: 0 }};
      const traces = selectedSnapshotData ? selectedSnapshotData.traces : [];
      const trace = traces[selectedTraceIndex] || emptyTrace;
      const rawSpans = onlyErrors.checked ? trace.spans.filter((span) => span.error) : trace.spans;
      const spans = [...rawSpans].sort((a, b) => a.row - b.row);
      const rowsCount = Math.max(1, spans.length || trace.rows_count);
      const trackWidth = Math.max(120, spansCol.clientWidth - 8);

      namesCol.style.height = `${{rowsCount * rowHeight + 8}}px`;
      spansCol.style.height = `${{rowsCount * rowHeight + 8}}px`;
      namesCol.innerHTML = "";
      spansCol.innerHTML = "";
      legend.innerHTML = "";

      for (let r = 1; r < rowsCount; r++) {{
        const line = document.createElement("div");
        line.className = "row-line";
        line.style.top = `${{r * rowHeight}}px`;
        namesCol.appendChild(line.cloneNode());
        spansCol.appendChild(line);
      }}

      const services = new Map();
      for (let renderRow = 0; renderRow < spans.length; renderRow++) {{
        const span = spans[renderRow];
        const label = document.createElement("div");
        label.className = "row-label";
        label.style.top = `${{renderRow * rowHeight + 2}}px`;
        label.style.paddingLeft = `${{span.depth * 16}}px`;
        label.textContent = span.name;
        label.title = `span_id: ${{span.span_id}} parent_id: ${{span.parent_id}}`;
        namesCol.appendChild(label);

        const el = document.createElement("div");
        el.className = "span" + (span.error ? " error" : "");
        if (selectedSpanId !== null && selectedSpanId === span.span_id) el.className += " selected";
        el.style.left = `${{(span.left / 100.0) * trackWidth}}px`;
        el.style.width = `${{Math.max(2, (span.width / 100.0) * trackWidth)}}px`;
        el.style.top = `${{renderRow * rowHeight + 2}}px`;
        el.style.background = span.color;
        el.addEventListener("click", () => {{
          selectedSpanId = span.span_id;
          selectedSpanData = span;
          renderTimeline();
        }});
        spansCol.appendChild(el);
        if (!services.has(span.service)) services.set(span.service, span.color);
      }}

      const errorCount = spans.filter((span) => span.error).length;
      traceSummary.textContent =
        `Trace #${{trace.index}} | Spans: ${{spans.length}} | ` +
        `Errors: ${{errorCount}} | Duration: ${{trace.trace_duration_ms}} ms`;
      renderSelectedSpan();
      for (const [service, color] of services) {{
        const item = document.createElement("div");
        item.innerHTML = `<span class="legend-dot" style="background:${{color}}"></span>${{service || "(no service)"}}`;
        legend.appendChild(item);
      }}
    }}

    async function loadSnapshot(id) {{
      selectedSnapshotId = id;
      selectedSpanId = null;
      selectedSpanData = null;
      renderSnapshotList();
      const res = await fetch(`/api/snapshot?id=${{encodeURIComponent(String(id))}}`);
      if (!res.ok) {{
        selectedSnapshotData = null;
        selectedSnapshotLabel.textContent = "Failed to load snapshot.";
        applyTraceSearch();
        return;
      }}
      selectedSnapshotData = await res.json();
      selectedSnapshotLabel.textContent = `Snapshot: ${{selectedSnapshotData.snapshot_label}}`;
      selectedTraceIndex = selectedSnapshotData.traces.length ? selectedSnapshotData.traces[0].index : 0;
      applyTraceSearch();
    }}

    splitter.addEventListener("mousedown", (event) => {{
      event.preventDefault();
      const onMove = (moveEvent) => {{
        const bounds = traceGrid.getBoundingClientRect();
        const raw = moveEvent.clientX - bounds.left;
        const clamped = Math.max(180, Math.min(800, raw));
        traceGrid.style.setProperty("--name-col", `${{clamped}}px`);
        renderTimeline();
      }};
      const onUp = () => {{
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      }};
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    }});

    async function bootstrap() {{
      const res = await fetch("/api/snapshots");
      snapshots = await res.json();
      filteredSnapshotIds = snapshots.map((s) => s.id);
      selectedSnapshotId =
        INITIAL_SNAPSHOT_ID !== null
          ? INITIAL_SNAPSHOT_ID
          : (filteredSnapshotIds.length ? filteredSnapshotIds[0] : null);
      renderSnapshotList();
      if (selectedSnapshotId !== null) {{
        await loadSnapshot(selectedSnapshotId);
      }}
    }}

    snapshotSearch.addEventListener("input", applySnapshotSearch);
    traceSearch.addEventListener("input", applyTraceSearch);
    onlyErrors.addEventListener("change", renderTimeline);
    bootstrap();
  </script>
</body>
</html>
"""
    return html.encode("utf-8")


@dataclass(frozen=True)
class SnapshotEntry:
    id: int
    path: Path
    label: str
    search_text: str


@dataclass
class SnapshotCatalog:
    entries: list[SnapshotEntry]
    initial_snapshot_id: int | None


def _label_for(path: Path, repo_root: Path) -> str:
    snapshots_root = repo_root / "tests" / "snapshots"
    if path.is_relative_to(snapshots_root):
        return str(path.relative_to(snapshots_root))
    if path.is_relative_to(repo_root):
        return str(path.relative_to(repo_root))
    return str(path)


def _collect_snapshot_files(roots: list[Path], extra_file: Path | None) -> list[Path]:
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
    if extra_file is not None and extra_file.exists() and extra_file.is_file():
        candidates.add(extra_file.resolve())
    return sorted(candidates, key=lambda p: str(p))


def _build_catalog(repo_root: Path, roots: list[Path], snapshot_arg: str | None) -> SnapshotCatalog:
    extra_file = Path(snapshot_arg).resolve() if snapshot_arg else None
    files = _collect_snapshot_files(roots, extra_file)
    entries = [
        SnapshotEntry(
            id=index,
            path=path,
            label=_label_for(path, repo_root),
            search_text=_label_for(path, repo_root).lower(),
        )
        for index, path in enumerate(files)
    ]

    initial_snapshot_id = None
    if entries:
        if extra_file is not None:
            for entry in entries:
                if entry.path == extra_file:
                    initial_snapshot_id = entry.id
                    break
        if initial_snapshot_id is None:
            initial_snapshot_id = entries[0].id
    return SnapshotCatalog(entries=entries, initial_snapshot_id=initial_snapshot_id)


def _json_bytes(payload: dict[str, object] | list[object]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _load_snapshot_payload(entry: SnapshotEntry) -> dict[str, object]:
    raw = json.loads(entry.path.read_text(encoding="utf-8"))
    traces = _normalize_traces(raw)
    prepared = [_layout_trace(i, trace) for i, trace in enumerate(traces)]
    return {
        "snapshot_id": entry.id,
        "snapshot_path": str(entry.path),
        "snapshot_label": entry.label,
        "traces": prepared,
    }


def _make_handler(catalog: SnapshotCatalog, html: bytes) -> type[BaseHTTPRequestHandler]:
    by_id = {entry.id: entry for entry in catalog.entries}
    snapshots_payload = _json_bytes(
        [
            {
                "id": entry.id,
                "label": entry.label,
                "path": str(entry.path),
                "search_text": entry.search_text,
            }
            for entry in catalog.entries
        ]
    )

    class SnapshotHandler(BaseHTTPRequestHandler):
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
            if parsed.path == "/api/snapshots":
                self._send(200, "application/json; charset=utf-8", snapshots_payload)
                return
            if parsed.path == "/api/snapshot":
                query = parse_qs(parsed.query)
                raw_id = (query.get("id") or [""])[0]
                try:
                    snapshot_id = int(raw_id)
                except ValueError:
                    self._send(400, "application/json; charset=utf-8", _json_bytes({"error": "Invalid id"}))
                    return
                entry = by_id.get(snapshot_id)
                if entry is None:
                    self._send(404, "application/json; charset=utf-8", _json_bytes({"error": "Snapshot not found"}))
                    return
                try:
                    payload = _load_snapshot_payload(entry)
                except Exception as exc:
                    self._send(
                        500,
                        "application/json; charset=utf-8",
                        _json_bytes({"error": f"Failed to load snapshot: {exc}"}),
                    )
                    return
                self._send(200, "application/json; charset=utf-8", _json_bytes(payload))
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    return SnapshotHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a browser UI to browse and inspect trace snapshot JSON files.")
    parser.add_argument(
        "snapshot",
        nargs="?",
        default=None,
        help="Optional snapshot path to pre-select in the UI.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765).")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    roots = [repo_root / "tests" / "snapshots"]
    catalog = _build_catalog(repo_root=repo_root, roots=roots, snapshot_arg=args.snapshot)
    if not catalog.entries:
        parser.error("No snapshot files found under tests/snapshots.")

    html = _build_html(catalog.initial_snapshot_id)
    handler = _make_handler(catalog, html)
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        url = f"http://{args.host}:{args.port}/"
        print(f"Serving {len(catalog.entries)} snapshot file(s) at {url}")
        print("Press Ctrl+C to stop.")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
