#include <Python.h>
#include <iostream>
#include <tuple>
#include <unordered_map>
#include <vector>

#define IS_TAINTED(obj, thread_id)                                             \
  (TaintMapping.count(thread_id) && TaintMapping[thread_id].count(obj) &&      \
   TaintMapping[thread_id][obj].size())

typedef PyObject *T_input_info;
typedef std::tuple<T_input_info, Py_ssize_t, Py_ssize_t> tainted_range;
typedef std::vector<tainted_range> tainted_range_list;
typedef std::unordered_map<PyObject *, tainted_range_list>
    tainted_pyobject_dict;

PyObject *bytes_join = NULL;
PyObject *bytearray_join = NULL;
PyObject *empty_bytes = NULL;
PyObject *empty_bytearray = NULL;
PyObject *empty_unicode = NULL;

std::unordered_map<long long, tainted_pyobject_dict> TaintMapping{};

static PyObject *setup(PyObject *Py_UNUSED(module), PyObject *args) {
  PyArg_ParseTuple(args, "OO", &bytes_join, &bytearray_join);
  empty_bytes = PyBytes_FromString("");
  empty_bytearray = PyByteArray_FromObject(empty_bytes);
  empty_unicode = PyUnicode_New(0, 127);
  Py_RETURN_NONE;
}

static PyObject *clear_taint_mapping(PyObject *Py_UNUSED(module),
                                     PyObject *Py_UNUSED(args)) {
  // TODO: Not sure this is really necessary
  for (auto &[key, value] : TaintMapping) {
    value.clear();
  }

  TaintMapping.clear();
  Py_RETURN_NONE;
}

static PyObject *new_pyobject_id(PyObject *tainted_object,
                                 Py_ssize_t object_length) {
  if (PyUnicode_Check(tainted_object)) {
    if (PyUnicode_CHECK_INTERNED(tainted_object) == 0) { // SSTATE_NOT_INTERNED
      Py_INCREF(tainted_object);
      return tainted_object;
    }
    return PyUnicode_Join(empty_unicode,
                          Py_BuildValue("(OO)", tainted_object, empty_unicode));
  } else if (object_length > 1) {
    // Bytes and bytearrays with length > 1 are not interned
    Py_INCREF(tainted_object);
    return tainted_object;
  } else if (PyBytes_Check(tainted_object)) {
    return PyObject_CallFunctionObjArgs(
        bytes_join, empty_bytes,
        Py_BuildValue("(OO)", tainted_object, empty_bytes), NULL);
  } else {
    return PyObject_CallFunctionObjArgs(
        bytearray_join, empty_bytearray,
        Py_BuildValue("(OO)", tainted_object, empty_bytearray), NULL);
  }
}

static PyObject *taint_pyobject(PyObject *Py_UNUSED(module), PyObject *args) {
  PyObject *tainted_object;
  T_input_info input_info;
  long long thread_id = 0;
  PyArg_ParseTuple(args, "OOL", &tainted_object, &input_info, &thread_id);
  // DEV: could use PyUnicode_GET_LENGTH if we are only using unicode string
  Py_ssize_t object_length = PyObject_Length(tainted_object);
  if (object_length < 1) {
    Py_INCREF(tainted_object);
    return tainted_object;
  }

  Py_INCREF(input_info);
  tainted_object = new_pyobject_id(tainted_object, object_length);

  if (TaintMapping.count(thread_id) == 0) {
    TaintMapping[thread_id] = {};
  }
  TaintMapping[thread_id][tainted_object] = {{input_info, 0, object_length}};
  return tainted_object;
}

static PyObject *add_taint_pyobject(PyObject *Py_UNUSED(module),
                                    PyObject *args) {
  PyObject *tainted_object;
  PyObject *op1;
  PyObject *op2;
  long long thread_id = 0;
  PyArg_ParseTuple(args, "OOOL", &tainted_object, &op1, &op2, &thread_id);
  // if both operand are untainted, do not taint
  if (!(IS_TAINTED(op1, thread_id) || IS_TAINTED(op2, thread_id))) {
    Py_INCREF(tainted_object);
    return tainted_object;
  }
  tainted_object =
      new_pyobject_id(tainted_object, PyObject_Length(tainted_object));
  if (TaintMapping.count(thread_id) == 0) {
    TaintMapping[thread_id] = {};
  }
  if IS_TAINTED (op1, thread_id)
    TaintMapping[thread_id][tainted_object] = TaintMapping[thread_id][op1];
  else
    TaintMapping[thread_id][tainted_object] = {};
  if IS_TAINTED (op2, thread_id) {
    Py_ssize_t offset = PyObject_Length(op1);
    for (auto [input_info, start, size] : TaintMapping[thread_id][op2]) {
      Py_INCREF(input_info);
      TaintMapping[thread_id][tainted_object].emplace_back(
          tainted_range(input_info, start + offset, size));
    }
  }
  return tainted_object;
}

static PyObject *is_pyobject_tainted(PyObject *Py_UNUSED(module),
                                     PyObject *args) {
  PyObject *py_object;
  long long thread_id = 0;
  PyArg_ParseTuple(args, "OL", &py_object, &thread_id);
  if IS_TAINTED (py_object, thread_id)
    Py_RETURN_TRUE;
  Py_RETURN_FALSE;
}

static PyObject *get_tainted_ranges(PyObject *Py_UNUSED(module),
                                    PyObject *args) {
  PyObject *result = PyList_New(0);
  PyObject *py_object;
  long long thread_id = 0;
  PyArg_ParseTuple(args, "OL", &py_object, &thread_id);
  if IS_TAINTED (py_object, thread_id) {
    for (auto [input_info, start, size] : TaintMapping[thread_id][py_object]) {
      PyList_Append(result, Py_BuildValue("(Onn)", input_info, start, size));
    }
  }
  return result;
}

static PyObject *set_tainted_ranges(PyObject *Py_UNUSED(module),
                                    PyObject *args) {
  PyObject *tainted_object;
  PyObject *list_ranges;
  long long thread_id = 0;
  PyArg_ParseTuple(args, "OOL", &tainted_object, &list_ranges, &thread_id);
  if (TaintMapping.count(thread_id) == 0) {
    TaintMapping[thread_id] = {};
  }
  TaintMapping[thread_id][tainted_object] = {};
  for (Py_ssize_t i = 0; i < PySequence_Length(list_ranges); i++) {
    PyObject *tuple = PySequence_GetItem(list_ranges, i);
    PyObject *input_info = PySequence_GetItem(tuple, 0);
    Py_INCREF(input_info);
    TaintMapping[thread_id][tainted_object].emplace_back(
        input_info, PyLong_AsLong(PySequence_GetItem(tuple, 1)),
        PyLong_AsLong(PySequence_GetItem(tuple, 2)));
  }
  Py_RETURN_NONE;
}

static PyMethodDef TaintTrackingMethods[] = {
    {"clear_taint_mapping", (PyCFunction)clear_taint_mapping, METH_NOARGS,
     "clear taint mappings"},
    // We are using  METH_VARARGS because we need compatibility with
    // python 3.5, 3.6. but METH_FASTCALL could be used instead for python
    // >= 3.7
    {"setup", (PyCFunction)setup, METH_VARARGS, "setup tainting module"},
    {"taint_pyobject", (PyCFunction)taint_pyobject, METH_VARARGS,
     "taint pyobject"},
    {"add_taint_pyobject", (PyCFunction)add_taint_pyobject, METH_VARARGS,
     "taint pyobject obtained from +"},
    {"is_pyobject_tainted", (PyCFunction)is_pyobject_tainted, METH_VARARGS,
     "is pyobject tainted"},
    {"get_tainted_ranges", (PyCFunction)get_tainted_ranges, METH_VARARGS,
     "get tainted ranges as a list of tuples"},
    {"set_tainted_ranges", (PyCFunction)set_tainted_ranges, METH_VARARGS,
     "set tainted ranges from a list of tuples"},
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef taint_tracking = {
    PyModuleDef_HEAD_INIT, "ddtrace.appsec.iast._taint_tracking",
    "taint tracking module", -1, TaintTrackingMethods};

PyMODINIT_FUNC PyInit__taint_tracking(void) {
  PyObject *m;
  m = PyModule_Create(&taint_tracking);
  if (m == NULL)
    return NULL;
  return m;
}
