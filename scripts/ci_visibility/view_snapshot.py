import json
import os
import sys

from PrettyPrint import PrettyPrintTree


class Tree:
    def __init__(self, span, component, resource, parent_id=None):
        self.span = span
        self.component = component
        self.resource = resource
        self.parent_id = parent_id
        self.children = []

    def add_child(self, child):
        self.children.append(child)
        return child

    def __repr__(self):
        return f"{self.component}\n- {self.resource}"


class Snapshots(Tree):
    def __init__(self):
        super().__init__(None, None, None)

    def __repr__(self):
        return "Snapshots"


pt = PrettyPrintTree(lambda x: x.children, lambda x: x.__repr__(), orientation=PrettyPrintTree.Horizontal)

PARENT_SPAN_IDS = {"test": "test_suite_id", "test_suite_end": "test_module_id", "test_module_end": "test_session_id"}
TYPE_IDS = {
    "test_suite_end": "test_suite_id",
    "test_module_end": "test_module_id",
    "test_session_end": "test_session_id",
}


def load_snapshot(snapshot_file):
    with open(snapshot_file) as f:
        snapshot = json.load(f)
    return snapshot


def get_nodes(traces):
    nodes = {}
    for trace in traces:
        for span in trace:
            try:
                span_meta = span.get("meta", {})
                span_type = span_meta.get("type")
                span_id = span_meta.get(TYPE_IDS.get(span_type), f"{span['trace_id']}.{span['span_id']}")
                parent_id = span_meta.get(PARENT_SPAN_IDS.get(span_type), f"{span['trace_id']}.{span['parent_id']}")
                nodes[span_id] = Tree(span, span_meta.get("component"), span["resource"], parent_id)
            except Exception as e:
                print("Error processing span", span, e)
    return nodes


def sort_nodes(nodes):
    roots = []
    for node_id, node in nodes.items():
        if node.parent_id is None or node.parent_id not in nodes:
            roots.append(node)
        else:
            nodes[node.parent_id].add_child(node)

    return roots


def build_snapshot_tree(snapshot):
    nodes = get_nodes(snapshot)

    t = Snapshots()
    t.children = sort_nodes(nodes)

    return t


if __name__ == "__main__":
    try:
        w, _ = os.get_terminal_size()
    except OSError:
        w = 80

    snapshot_files = sys.argv[1:]
    for snapshot_file in snapshot_files:
        print(f"  Showing snapshot: {snapshot_file}  ".center(w, "="), "\n")
        snapshot = load_snapshot(snapshot_file)

        tree = build_snapshot_tree(snapshot)

        pt(tree)

        print("\n\n")
