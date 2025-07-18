/*
 * json2ddwaf_module.c  –  CPython extension:  path → ddwaf_object capsule
 *
 * Build (Linux / macOS):
 *   mkdir build && cd build
 *   cmake .. && cmake --build . -j
 *   # produces json2ddwaf.{so,pyd}
 *
 * Runtime (Python):
 *   import json2ddwaf
 *   cap = json2ddwaf.from_json("rules.json")
 *   ptr = json2ddwaf.addr(cap)        # integer address if you need it
 *   # pass `cap` or `ptr` to your ctypes layer / ddwaf_init / etc.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ddwaf.h"          /* public header from libddwaf        */
#include "cjson/cJSON.h"    /* header from https://github.com/DaveGamble/cJSON */

/* --------------------------------------------------------------------- */
/* Small helpers                                                         */

static char *slurp(const char *path, size_t *len_out)
{
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;

    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);

    char *buf = malloc(len + 1);
    if (!buf) { fclose(f); return NULL; }

    if (fread(buf, 1, len, f) != (size_t)len) { fclose(f); free(buf); return NULL; }
    fclose(f);
    buf[len] = '\0';
    if (len_out) *len_out = (size_t)len;
    return buf;
}

/* Forward decl so the function can recurse */
static void json_to_ddwaf(const cJSON *j, ddwaf_object *out);

static void add_child_array(ddwaf_object *parent, const cJSON *item) {
    ddwaf_object child;
    json_to_ddwaf(item, &child);
    ddwaf_object_array_add(parent, &child);
}
static void add_child_map(ddwaf_object *parent, const char *key, const cJSON *item) {
    ddwaf_object child;
    json_to_ddwaf(item, &child);
    ddwaf_object_map_add(parent, key, &child);
}

/* Recursive converter: cJSON → ddwaf_object --------------------------- */
static void json_to_ddwaf(const cJSON *j, ddwaf_object *out)
{
    if (cJSON_IsString(j))       ddwaf_object_string(out, j->valuestring);
    else if (cJSON_IsBool(j))    ddwaf_object_bool(out, cJSON_IsTrue(j));
    else if (cJSON_IsNull(j))    ddwaf_object_null(out);
    else if (cJSON_IsNumber(j)) {
        double d = j->valuedouble;
        long long ll = (long long)d;
        if (d == (double)ll)
            ddwaf_object_signed(out, ll);
        else
            ddwaf_object_float(out, d);
    }
    else if (cJSON_IsArray(j)) {
        ddwaf_object_array(out);
        for (const cJSON *it = j->child; it; it = it->next)
            add_child_array(out, it);
    }
    else if (cJSON_IsObject(j)) {
        ddwaf_object_map(out);
        for (const cJSON *it = j->child; it; it = it->next)
            add_child_map(out, it->string, it);
    }
    else
        ddwaf_object_invalid(out);
}

/* --------------------------------------------------------------------- */
/* Capsule destructor — called when Python GC frees the capsule          */

static void capsule_destruct(PyObject *capsule)
{
    ddwaf_object *root = PyCapsule_GetPointer(capsule, "ddwaf_object");
    if (!root) return;                       /* already reported */
    ddwaf_object_free(root);                 /* libddwaf utility */
    free(root);                              /* we malloc’d it   */
}

/* --------------------------------------------------------------------- */
/* Python method:  from_json(path:str) -> capsule                         */

static PyObject *py_from_json(PyObject *self, PyObject *args, PyObject *kw)
{
    static char *kwlist[] = {"path", NULL};
    const char *path;

    if (!PyArg_ParseTupleAndKeywords(args, kw, "s", kwlist, &path))
        return NULL;

    size_t len = 0;
    char *text = slurp(path, &len);
    if (!text)
        return PyErr_SetFromErrnoWithFilename(PyExc_OSError, path);

    cJSON *root_json = cJSON_ParseWithLength(text, len);
    free(text);
    if (!root_json) {
        PyErr_SetString(PyExc_ValueError, "invalid JSON");
        return NULL;
    }

    ddwaf_object *root = malloc(sizeof(ddwaf_object));
    if (!root) { cJSON_Delete(root_json); return PyErr_NoMemory(); }

    json_to_ddwaf(root_json, root);
    cJSON_Delete(root_json);

    return PyCapsule_New(root, "ddwaf_object", capsule_destruct);
}

/* Helper: addr(capsule) -> int (raw pointer value)                       */
static PyObject *py_addr(PyObject *self, PyObject *arg)
{
    ddwaf_object *ptr = PyCapsule_GetPointer(arg, "ddwaf_object");
    if (!ptr) return NULL;                   /* PyCapsule_* already set err */
    return PyLong_FromUnsignedLongLong((unsigned long long)ptr);
}

/* --------------------------------------------------------------------- */
/* Module boilerplate                                                    */

static PyMethodDef methods[] = {
    {"from_json", (PyCFunction)py_from_json, METH_VARARGS|METH_KEYWORDS,
     "Parse JSON file → ddwaf_object capsule"},
    {"addr",      (PyCFunction)py_addr,      METH_O,
     "Return integer address inside a capsule"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moddef = {
    PyModuleDef_HEAD_INIT,
    "json2ddwaf",
    "Fast JSON→ddwaf_object bridge",
    -1,
    methods
};

PyMODINIT_FUNC PyInit_json2ddwaf(void) { return PyModule_Create(&moddef); }
