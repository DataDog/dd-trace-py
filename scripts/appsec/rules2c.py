#!/usr/bin/env python3
"""
Convert rules.json  →  a single C source file that is both
  • a CPython extension module (`import json2ddwaf`)
  • and a static ddwaf_object tree.

Usage:
    python rules2c.py rules.json json2ddwaf_autogen.c
"""

import json
from pathlib import Path
import sys


src = Path(sys.argv[1])
dest = Path(sys.argv[2])

# ---------------------------------------------------------------------
counter = 0
out = []  # list[str] to hold emitted C lines


def new(prefix="n"):
    global counter
    counter += 1
    return f"{prefix}{counter}"


def w(line="", *args, **kwargs):
    out.append(line.format(*args, **kwargs))


# ---------------------------------------------------------------------
# 1. File header & module boiler-plate
w(
    """\
/* --------------------------------------------------------------------
   !!! AUTO-GENERATED FILE – DO NOT EDIT BY HAND !!!
   Generated from: {json}
   ------------------------------------------------------------------ */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdbool.h>
#include "ddwaf.h"

""",
    json=src.name,
)

# ---------------------------------------------------------------------
# 2. Forward declarations
w("static void build_rules_tree(void);\n")

# ---------------------------------------------------------------------
# 3. Static storage for the tree
root_var = "rules_root"
w("static ddwaf_object {0};", root_var)
w("static bool rules_built = false;\n")


# ---------------------------------------------------------------------
# 4. Recursive code-gen helpers
def emit_node(node, var):
    """append C statements that initialise `var`"""
    if node is None:
        w("    ddwaf_object_null(&{0});", var)
    elif isinstance(node, bool):
        w("    ddwaf_object_bool(&{0}, {1});", var, "true" if node else "false")
    elif isinstance(node, int):
        w("    ddwaf_object_signed(&{0}, {1});", var, node)
    elif isinstance(node, float):
        w("    ddwaf_object_float(&{0}, {1});", var, node)
    elif isinstance(node, str):
        esc = node.encode("unicode_escape").decode().replace('"', r"\"")
        w('    ddwaf_object_string(&{0}, "{1}");', var, esc)
    elif isinstance(node, list):
        w("    ddwaf_object_array(&{0});", var)
        for item in node:
            child = new()
            w("    ddwaf_object {0};", child)
            emit_node(item, child)
            w("    ddwaf_object_array_add(&{0}, &{1});", var, child)
    elif isinstance(node, dict):
        w("    ddwaf_object_map(&{0});", var)
        for k, v in node.items():
            k_esc = k.encode("unicode_escape").decode().replace('"', r"\"")
            child = new()
            w("    ddwaf_object {0};", child)
            emit_node(v, child)
            w('    ddwaf_object_map_add(&{0}, "{1}", &{2});', var, k_esc, child)
    else:
        raise TypeError(f"Unsupported type {type(node)}")


# ---------------------------------------------------------------------
# 5. build_rules_tree() implementation
w("static void build_rules_tree(void)")
w("{{")
w("    if (rules_built) return;")
emit_node(json.loads(src.read_text()), root_var)
w("    rules_built = true;")
w("}}\n")

# ---------------------------------------------------------------------
# 6. Public accessor + Python wrapper
w(
    """\
static ddwaf_object * ddwaf_rules_root(void)
{{
    build_rules_tree();
    return &{root};
}}

static PyObject *py_get_rules(PyObject *self, PyObject *Py_UNUSED(ignored))
{{
    return PyCapsule_New(ddwaf_rules_root(), "ddwaf_object", NULL);
}}

static PyMethodDef methods[] = {{
    {{"get_rules",  py_get_rules, METH_NOARGS,
      "Return a capsule that wraps the pre-built ddwaf_object"}},
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef moddef = {{
    PyModuleDef_HEAD_INIT,
    "default_ddwaf_rules",
    "Auto-generated ddwaf rules module",
    -1,
    methods
}};

PyMODINIT_FUNC PyInit_default_ddwaf_rules(void) {{ return PyModule_Create(&moddef); }}
""",
    root=root_var,
)

# ---------------------------------------------------------------------
dest.write_text("\n".join(out))
print("generated", dest)
