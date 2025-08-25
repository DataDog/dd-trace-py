#!/usr/bin/env python3
"""
Render a dependency DAG from taint_tracking.json using pygraphviz.
- Hierarchical layout (top->bottom by default)
- Node label: [Class::]Function  (file_basename:line)
- Tooltip: full path, id, action, origin, type_propagation
- Clickable nodes: open file://<absolute_path>  (adjust to your code browser/editor if you want)
- Colors by action: source (green), propagate (blue), sink (red), unknown (gray)
"""

import argparse
import json
import os
import subprocess
import sys

from pygraphviz import AGraph


# ---------- Config ----------
RANKDIR = "TB"  # "TB" (top->bottom) or "LR" (left->right)

# Optional: map 'action' -> fill color
ACTION_COLOR = {
    "source": "#dff7df",  # light green
    "propagate": "#eef5ff",  # light blue
    "sink": "#ffe8e8",  # light red
}
DEFAULT_FILL = "#eeeeee"  # unknown action

# ---------- CLI ----------
parser = argparse.ArgumentParser(description="Render a dependency DAG from a taint tracking JSON file.")
parser.add_argument("-f", "--file", required=True, help="Path to input JSON file")
parser.add_argument("-o", "--output", required=True, help="Path to output SVG file")
parser.add_argument("-e", dest="e", action="store_true", help="Open the output SVG in Google Chrome after generation")
args = parser.parse_args()

INPUT_PATH = args.file
OUTPUT_SVG = args.output

# ANSI colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# ---------- Load data ----------
if not os.path.exists(INPUT_PATH):
    print(f"{RED}ERROR:{RESET} {INPUT_PATH} not found. Provide a valid path with -f.", file=sys.stderr)
    sys.exit(1)

with open(INPUT_PATH, "r", encoding="utf-8") as f:
    raw = json.load(f)

# Support either a list of nodes or an object with "nodes" key
if isinstance(raw, dict) and "nodes" in raw:
    data = raw["nodes"]
elif isinstance(raw, list):
    data = raw
else:
    print(f"{RED}ERROR:{RESET} JSON must be a list of node objects or an object with a 'nodes' list.", file=sys.stderr)
    sys.exit(1)

# Validate minimal fields
required = {"id", "parent_ids", "file", "line", "function", "class", "action"}
missing_any = any(not required.issubset(d.keys()) for d in data)
if missing_any:
    print(
        f"{YELLOW}WARNING:{RESET} Some nodes are missing expected keys; labels/tooltips may be incomplete.",
        file=sys.stderr,
    )

# ---------- Build graph ----------
G = AGraph(directed=True, strict=False)
G.graph_attr.update(
    rankdir=RANKDIR,
    splines="ortho",  # "spline" / "polyline" are alternatives
    nodesep="0.35",
    ranksep="0.6",
    bgcolor="white",
)
G.node_attr.update(
    shape="box",
    style="rounded,filled",
    fontname="Helvetica",
    fontsize="10",
)
G.edge_attr.update(
    arrowsize="0.7",
    penwidth="1.2",
)

# Helper: index by id
by_id = {}
for n in data:
    nid = n.get("id")
    if nid in by_id:
        # Not fatal, but unusual. You can choose to dedupe/merge here.
        print(f"WARNING: duplicate id encountered: {nid}", file=sys.stderr)
    by_id[nid] = n


def safe(s):
    if s is None:
        return ""
    return str(s)


# Add nodes
for n in data:
    nid = n.get("id")
    file_path = safe(n.get("file"))
    file_base = os.path.basename(file_path) if file_path else ""
    line = safe(n.get("line"))
    func = safe(n.get("function"))
    text_string = safe(n.get("string"))
    cls = safe(n.get("class"))
    action = safe(n.get("action")).lower()
    origin = safe(n.get("origin"))
    type_prop = safe(n.get("type_propagation"))

    # Display "Class::function" if class is non-empty
    header = f"{cls}::{func}" if cls else func
    header = header if header else "(anonymous)"

    # Node label shown in the box
    if action == "source":
        label = f"{action}({origin}):\\n{text_string}"
    elif action == "sink":
        label = f"{action}:{type_prop}\\n{file_base}:{line} ({header})"
    else:
        label = f"{action}:{type_prop}\\n{file_base}:{line} ({header})"

    # Tooltip with more details
    tooltip = (
        f"id={nid}\\n"
        f"path={file_path}\\n"
        f"action={action or 'n/a'}"
        f"{' | origin='+origin if origin else ''}"
        f"{' | type='+type_prop if type_prop else ''}"
    )

    # Clickable URL (SVG): open file path. Replace with repo web URL or editor URI scheme if you prefer.
    href = f"file://{file_path}" if file_path else ""

    fill = ACTION_COLOR.get(action, DEFAULT_FILL)

    G.add_node(
        nid,
        label=label,
        tooltip=tooltip,
        URL=href,
        target="_blank",
        fillcolor=fill,
    )

# Add edges: parent -> child
for n in data:
    child = n.get("id")
    for pid in n.get("parent_ids", []):
        if pid in by_id:
            G.add_edge(pid, child)
        else:
            print(f"{YELLOW}WARNING:{RESET} parent id {pid} (for child {child}) not found in data", file=sys.stderr)

# Optional: highlight graph roots (no parents) and sinks (no children) with a darker border
roots = {n["id"] for n in data if not n.get("parent_ids")}
children_set = {cid for n in data for cid in n.get("parent_ids", [])}
sinks = {n["id"] for n in data if n["id"] not in children_set}

for rid in roots:
    G.get_node(rid).attr.update(color="#2e7d32", penwidth="2.0")  # dark green border
for sid in sinks:
    G.get_node(sid).attr.update(color="#c62828", penwidth="2.0")  # dark red border

# ---------- Layout & render ----------
try:
    G.layout(prog="dot")  # hierarchical
except Exception:
    print(
        f"{RED}ERROR:{RESET} Graphviz 'dot' layout failed. Ensure Graphviz is installed and 'dot' is on PATH.",
        file=sys.stderr,
    )
    raise

try:
    G.draw(OUTPUT_SVG)
except Exception as e:
    print(f"{RED}ERROR:{RESET} Failed to write output SVG '{OUTPUT_SVG}': {e}", file=sys.stderr)
    sys.exit(1)

print(f"{GREEN}Wrote {OUTPUT_SVG}{RESET} (open in a browser).")

# Open in Google Chrome if requested
if args.e:
    try:
        subprocess.run(["google-chrome", OUTPUT_SVG], check=False)
        print(f"{GREEN}Opened {OUTPUT_SVG} with google-chrome{RESET}")
    except FileNotFoundError:
        print(
            f"{RED}ERROR:{RESET} 'google-chrome' not found on PATH. Install it or open the SVG manually.",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"{RED}ERROR:{RESET} Failed to open '{OUTPUT_SVG}' with google-chrome: {e}", file=sys.stderr)
