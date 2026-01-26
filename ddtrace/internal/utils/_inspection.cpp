#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define Py_BUILD_CORE

#include <frameobject.h>
#if PY_VERSION_HEX >= 0x030b0000
#include <cstddef>
#include <internal/pycore_code.h>
#include <internal/pycore_frame.h>

#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_interpframe.h>
#include <internal/pycore_interpframe_structs.h>
#include <internal/pycore_stackref.h>
#endif // PY_VERSION_HEX >= 0x030e0000
#endif // PY_VERSION_HEX >= 0x030b0000

#include "structmember.h"

#include <stddef.h>

#include <unordered_map>

// ----------------------------------------------------------------------------
typedef struct
{
    PyObject_HEAD

      PyObject* file;
    PyObject* name;
    long line;
    long line_end;
    long column;
    long column_end;
} Frame;

// TODO: Use a (non-global?) bounded-size cache with eviction policy instead.
std::unordered_map<uintptr_t, PyObject*> frame_cache;

// ----------------------------------------------------------------------------
static PyMemberDef Frame_members[] = {
    { "file", T_OBJECT_EX, offsetof(Frame, file), READONLY, "file path" },
    { "name", T_OBJECT_EX, offsetof(Frame, name), READONLY, "code object name" },
    { "line", T_LONG, offsetof(Frame, line), READONLY, "starting line number" },
    { "line_end", T_LONG, offsetof(Frame, line_end), READONLY, "ending line number" },
    { "column", T_LONG, offsetof(Frame, column), READONLY, "starting column number" },
    { "column_end", T_LONG, offsetof(Frame, column_end), READONLY, "ending column number" },

    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static int
Frame_init(Frame* self, PyObject* args, PyObject* kwargs)
{
    static const char* kwlist[] = { "file", "name", "line", "line_end", "column", "column_end", NULL };

    self->line = 0;
    self->line_end = 0;
    self->column = 0;
    self->column_end = 0;

    if (!PyArg_ParseTupleAndKeywords(args,
                                     kwargs,
                                     "OOllll",
                                     (char**)kwlist,
                                     &self->file,
                                     &self->name,
                                     &self->line,
                                     &self->line_end,
                                     &self->column,
                                     &self->column_end))
        return -1;

    Py_INCREF(self->file);
    Py_INCREF(self->name);

    if (self->line < 0) {
        PyErr_SetString(PyExc_ValueError, "line must be non-negative");
        return -1;
    }
    if (self->line_end < 0) {
        PyErr_SetString(PyExc_ValueError, "line_end must be non-negative");
        return -1;
    }
    if (self->column < 0) {
        PyErr_SetString(PyExc_ValueError, "column must be non-negative");
        return -1;
    }
    if (self->column_end < 0) {
        PyErr_SetString(PyExc_ValueError, "column_end must be non-negative");
        return -1;
    }

    return 0;
}

// ----------------------------------------------------------------------------
static void
Frame_dealloc(Frame* self)
{
    Py_XDECREF(self->file);
    Py_XDECREF(self->name);

    Py_TYPE(self)->tp_free((PyObject*)self);
}

// ----------------------------------------------------------------------------
static PyMethodDef Frame_methods[] = {
    { NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static PyTypeObject FrameType = {
    .ob_base = PyObject_HEAD_INIT(NULL).tp_name = "ddtrace.internal._inspection.Frame",
    .tp_basicsize = sizeof(Frame),
    .tp_itemsize = 0,
    .tp_dealloc = (destructor)Frame_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .tp_doc = PyDoc_STR("Frame object representing a stack frame"),
    .tp_methods = Frame_methods,
    .tp_members = Frame_members,
    .tp_init = (initproc)Frame_init,
    .tp_new = PyType_GenericNew,
};

// ----------------------------------------------------------------------------
static inline PyCodeObject*
get_code_from_frame(PyObject* frame_like)
{
#if PY_VERSION_HEX >= 0x030e0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    PyCodeObject* code_obj = reinterpret_cast<PyCodeObject*>(BITS_TO_PTR_MASKED(py_frame->f_executable));
#elif PY_VERSION_HEX >= 0x030d0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    PyCodeObject* code_obj = reinterpret_cast<PyCodeObject*>(py_frame->f_executable);
#elif PY_VERSION_HEX >= 0x030b0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    PyCodeObject* code_obj = py_frame->f_code;
#else // PY_VERSION_HEX < 0x030b0000
    PyFrameObject* py_frame = reinterpret_cast<PyFrameObject*>(frame_like);
    PyCodeObject* code_obj = py_frame->f_code;
#endif

    return code_obj;
}

// ----------------------------------------------------------------------------
static inline PyObject*
get_frame_from_thread_state(PyThreadState* thread_state)
{
#if PY_VERSION_HEX >= 0x030d0000
    PyObject* py_frame = reinterpret_cast<PyObject*>(thread_state->current_frame);
#elif PY_VERSION_HEX >= 0x030b0000
    PyObject* py_frame = reinterpret_cast<PyObject*>(thread_state->cframe->current_frame);
#else // Python < 3.11
    PyObject* py_frame = reinterpret_cast<PyObject*>(thread_state->frame);
#endif

    return py_frame;
}

// ----------------------------------------------------------------------------
static inline PyObject*
get_previous_frame(PyObject* frame_like)
{
#if PY_VERSION_HEX >= 0x030b0000
    PyObject* py_frame = reinterpret_cast<PyObject*>(reinterpret_cast<_PyInterpreterFrame*>(frame_like)->previous);
#else // PY_VERSION_HEX < 0x030b0000
    PyObject* py_frame = reinterpret_cast<PyObject*>(reinterpret_cast<PyFrameObject*>(frame_like)->f_back);
#endif

    return py_frame;
}

// ----------------------------------------------------------------------------
static inline bool
should_skip_frame(PyObject* frame_like)
{
    // In recent Python versions, the code object can actually be something
    // else than a PyCodeObject. We cannot handle thse frames, so we skip them.
    PyObject* code = (PyObject*)get_code_from_frame(frame_like);
    if (code == NULL || !PyCode_Check(code)) {
        return true;
    }

    // Check for shim frames
#if PY_VERSION_HEX >= 0x030e0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    return py_frame->owner & (FRAME_OWNED_BY_CSTACK | FRAME_OWNED_BY_INTERPRETER);
#elif PY_VERSION_HEX >= 0x030c0000
    _PyInterpreterFrame* py_frame = reinterpret_cast<_PyInterpreterFrame*>(frame_like);
    return py_frame->owner & FRAME_OWNED_BY_CSTACK;
#else
    return false;
#endif
}

// ----------------------------------------------------------------------------
#if PY_VERSION_HEX >= 0x030b0000
static inline int
_read_varint(unsigned char* table, Py_ssize_t size, Py_ssize_t* i)
{
    Py_ssize_t guard = size - 1;
    if (*i >= guard)
        return 0;

    int val = table[++*i] & 63;
    int shift = 0;
    while (table[*i] & 64 && *i < guard) {
        shift += 6;
        val |= (table[++*i] & 63) << shift;
    }
    return val;
}

// ----------------------------------------------------------------------------
static inline int
_read_signed_varint(unsigned char* table, Py_ssize_t size, Py_ssize_t* i)
{
    int val = _read_varint(table, size, i);
    return (val & 1) ? -(val >> 1) : (val >> 1);
}
#endif

// ----------------------------------------------------------------------------
static inline int
get_location_from_code(PyCodeObject* code_obj,
                       int lasti,
                       int* out_line,
                       int* out_line_end,
                       int* out_column,
                       int* out_column_end)
{
    unsigned int lineno = code_obj->co_firstlineno;
    Py_ssize_t len = 0;
    unsigned char* table = nullptr;

#if PY_VERSION_HEX >= 0x030b0000
    if (PyBytes_AsStringAndSize(code_obj->co_linetable, (char**)&table, &len) == -1) {
        return 0;
    }

    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        bc += (table[i] & 7) + 1;
        int code = (table[i] >> 3) & 15;
        unsigned char next_byte = 0;
        switch (code) {
            case 15:
                break;

            case 14: // Long form
                lineno += _read_signed_varint(table, len, &i);

                *out_line = lineno;
                *out_line_end = lineno + _read_varint(table, len, &i);
                *out_column = _read_varint(table, len, &i);
                *out_column_end = _read_varint(table, len, &i);

                break;

            case 13: // No column data
                lineno += _read_signed_varint(table, len, &i);

                *out_line = lineno;
                *out_line_end = lineno;
                *out_column = *out_column_end = 0;

                break;

            case 12: // New lineno
            case 11:
            case 10:
                lineno += code - 10;

                *out_line = lineno;
                *out_line_end = lineno;
                *out_column = 1 + table[++i];
                *out_column_end = 1 + table[++i];

                break;

            default:
                next_byte = table[++i];

                *out_line = lineno;
                *out_line_end = lineno;
                *out_column = 1 + (code << 3) + ((next_byte >> 4) & 7);
                *out_column_end = *out_column + (next_byte & 15);
        }

        if (bc > lasti)
            break;
    }

#elif PY_VERSION_HEX >= 0x030a0000
    if (PyBytes_AsStringAndSize(code_obj->co_linetable, (char**)&table, &len) == -1) {
        return 0;
    }

    lasti <<= 1;
    for (int i = 0, bc = 0; i < len; i++) {
        int sdelta = table[i++];
        if (sdelta == 0xff)
            break;

        bc += sdelta;

        int ldelta = table[i];
        if (ldelta == 0x80)
            ldelta = 0;
        else if (ldelta > 0x80)
            lineno -= 0x100;

        lineno += ldelta;
        if (bc > lasti)
            break;
    }

    *out_line = lineno;
    *out_line_end = *out_column = *out_column_end = 0;

#else
    if (PyBytes_AsStringAndSize(code_obj->co_lnotab, (char**)&table, &len) == -1) {
        return 0;
    }

    for (int i = 0, bc = 0; i < len; i++) {
        bc += table[i++];
        if (bc > lasti)
            break;

        if (table[i] >= 0x80)
            lineno -= 0x100;

        lineno += table[i];
    }

    *out_line = lineno;
    *out_line_end = *out_column = *out_column_end = 0;

#endif

    return 1;
}

// ----------------------------------------------------------------------------
static inline int
get_lasti_from_frame(PyObject* frame_like)
{
#if PY_VERSION_HEX >= 0x030b0000
    return _PyInterpreterFrame_LASTI(reinterpret_cast<_PyInterpreterFrame*>(frame_like));
#else
    return reinterpret_cast<PyFrameObject*>(frame_like)->f_lasti;
#endif
}

// ----------------------------------------------------------------------------
static inline PyObject*
get_code_name(PyCodeObject* code_obj)
{
#if PY_VERSION_HEX >= 0x030b0000
    return code_obj->co_qualname;
#else
    return code_obj->co_name;
#endif
}

// ----------------------------------------------------------------------------
static PyObject*
Frame_new(PyCodeObject* code, int lasti)
{
    int line = 0, line_end = 0, column = 0, column_end = 0;

    if (!get_location_from_code(code, lasti, &line, &line_end, &column, &column_end)) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to get location from code object");
        return NULL;
    }

    // Build the frame data object
    PyObject* args =
      Py_BuildValue("OOllll", code->co_filename, get_code_name(code), line, line_end, column, column_end);
    if (args == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to build arguments for Frame");
        return NULL;
    }
    PyObject* frame = FrameType.tp_new(&FrameType, args, NULL);
    if (frame == NULL) {
        Py_DECREF(args);
        PyErr_SetString(PyExc_RuntimeError, "Failed to create Frame object");
        return NULL;
    }
    if (Py_TYPE(frame)->tp_init(frame, args, NULL) < 0) {
        Py_DECREF(args);
        Py_DECREF(frame);
        PyErr_SetString(PyExc_RuntimeError, "Failed to initialize Frame object");
        return NULL;
    }
    Py_DECREF(args);

    return frame;
}

// ----------------------------------------------------------------------------
static inline uintptr_t
Frame_key(PyCodeObject* code, int lasti)
{
    return (((uintptr_t)code) << 4) | lasti;
}

// ----------------------------------------------------------------------------
static PyObject*
unwind_current_thread(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(arg))
{
    PyObject* frames_list = PyList_New(0);
    if (frames_list == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "Failed to create list for frames");
        return NULL;
    }

    PyThreadState* thread_state = PyThreadState_Get();

    for (PyObject* py_frame = get_frame_from_thread_state(thread_state); py_frame != NULL;
         py_frame = get_previous_frame(py_frame)) {
        if (should_skip_frame(py_frame))
            continue;

        PyCodeObject* code_obj = get_code_from_frame(py_frame);
        if (code_obj == NULL) {
            Py_DECREF(frames_list);
            PyErr_SetString(PyExc_RuntimeError, "Failed to get code object from frame");
            return NULL;
        }

        int lasti = get_lasti_from_frame(py_frame);

        uintptr_t frame_key = Frame_key(code_obj, lasti);

        // DEV: As long as we use the cache we don't need to touch the reference
        // count on frame objects. Their reference count starts at one and only
        // needs to be decreased when removed from the cache.
        PyObject* frame = NULL;
        auto frame_cache_entry = frame_cache.find(frame_key);
        if (frame_cache_entry == frame_cache.end()) {
            frame = Frame_new(code_obj, lasti);
            if (frame == NULL) {
                Py_DECREF(frames_list);
                return NULL;
            }
            frame_cache[frame_key] = frame;
        } else {
            frame = frame_cache_entry->second;
            if (frame == NULL) {
                Py_DECREF(frames_list);
                PyErr_SetString(PyExc_RuntimeError, "Failed to retrieve a valid frame from the cache");
                return NULL;
            }
        }

        // Append the frame to the list
        if (PyList_Append(frames_list, frame) != 0) {
            Py_DECREF(frames_list);
            PyErr_SetString(PyExc_RuntimeError, "Failed to append Frame to list");
            return NULL;
        }
    }

    return frames_list;
}

// ----------------------------------------------------------------------------
static PyMethodDef _inspection_methods[] = {
    { "unwind_current_thread", (PyCFunction)unwind_current_thread, METH_NOARGS, NULL },
    { NULL, NULL, 0, NULL } /* Sentinel */
};

// ----------------------------------------------------------------------------
static struct PyModuleDef inspectionmodule = {
    PyModuleDef_HEAD_INIT, "_inspection", NULL, 0, _inspection_methods,
};

// ----------------------------------------------------------------------------
PyMODINIT_FUNC
PyInit__inspection(void)
{
    PyObject* m = NULL;

    if (PyType_Ready(&FrameType) < 0)
        return NULL;

    m = PyModule_Create(&inspectionmodule);
    if (m == NULL)
        goto error;

    Py_INCREF(&FrameType);
    if (PyModule_AddObject(m, "Frame", (PyObject*)&FrameType) < 0) {
        Py_DECREF(&FrameType);
        goto error;
    }

    return m;

error:
    Py_XDECREF(m);

    return NULL;
}
