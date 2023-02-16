#include <Python.h>
#include <unordered_map>
#include <vector>
#include <tuple>
#include <iostream>

#define IS_TAINTED(obj) (TaintMapping.count(obj) && TaintMapping[obj].size())

typedef PyObject *input_info;
typedef std::tuple<input_info, Py_ssize_t, Py_ssize_t> tainted_range;
typedef std::vector<tainted_range> tainted_range_list;

std::unordered_map<PyObject *, tainted_range_list> TaintMapping{};

static PyObject *clear_taint_mapping(PyObject *Py_UNUSED(module),
                                     PyObject *Py_UNUSED(args))
{
  TaintMapping.clear();
  Py_RETURN_NONE;
}

static PyObject *taint_pyobject(PyObject *Py_UNUSED(module),
                                PyObject *args)
{
  PyObject *tainted_object;
  input_info input_info;
  PyArg_UnpackTuple(args, "ref", 2, 2, &tainted_object, &input_info);
  // DEV: could use PyUnicode_GET_LENGTH if we are only using unicode string
  TaintMapping[tainted_object] = {{input_info, 0, PyObject_Length(tainted_object)}};
  Py_RETURN_NONE;
}

static PyObject *add_taint_pyobject(PyObject *Py_UNUSED(module),
                                    PyObject *args)
{
  PyObject *tainted_object;
  PyObject *op1;
  PyObject *op2;
  PyArg_UnpackTuple(args, "ref", 3, 3, &tainted_object, &op1, &op2);
  // if both operand are untainted, do not taint
  if (!(IS_TAINTED(op1) || IS_TAINTED(op2)))
    Py_RETURN_FALSE;
  if IS_TAINTED (op1)
    TaintMapping[tainted_object] = TaintMapping[op1];
  else
    TaintMapping[tainted_object] = {};
  if IS_TAINTED (op2)
  {
    Py_ssize_t offset = PyObject_Length(op1);
    for (auto [input_info, start, size] : TaintMapping[op2])
    {
      TaintMapping[tainted_object].emplace_back(tainted_range(input_info, start + offset, size));
    }
  }
  Py_RETURN_TRUE;
}

static PyObject *is_pyobject_tainted(PyObject *Py_UNUSED(module),
                                     PyObject *py_object)
{
  if IS_TAINTED (py_object)
    Py_RETURN_TRUE;
  Py_RETURN_FALSE;
}

static PyObject *get_tainted_ranges(PyObject *Py_UNUSED(module),
                                    PyObject *args)
{
  PyObject *py_object;
  PyObject *result;
  if (!PyArg_UnpackTuple(args, "ref", 2, 2, &py_object, &result))
    return NULL;
  // if IS_TAINTED (py_object)
  // {
  //   for (auto [input_info, start, size] : TaintMapping[py_object])
  //   {
  //     //PyList_Append(result, PyTuple_Pack(3, input_info, PyLong_FromSsize_t(start), PyLong_FromSsize_t(size)));
  //     PyList_Append(result, input_info);
  //     std::cout << result->ob_type->tp_name <<".append(" << input_info->ob_type->tp_name<<")" << std::endl;
  //   }
  // }
  std::cout << "return result " << result->ob_type->tp_name << " " << result->ob_refcnt << std::endl;
  return result;
}

static PyMethodDef TaintTrackingMethods[] = {
    {"clear_taint_mapping", (PyCFunction)clear_taint_mapping, METH_NOARGS, "clear taint mappings"},
    // We are using  METH_VARARGS because we need compatibility with python 3.5, 3.6.
    // but METH_FASTCALL could be used instead for python >= 3.7
    {"taint_pyobject", (PyCFunction)taint_pyobject, METH_VARARGS, "taint pyobject"},
    {"add_taint_pyobject", (PyCFunction)add_taint_pyobject, METH_VARARGS, "taint pyobject obtained from +"},
    {"is_pyobject_tainted", (PyCFunction)is_pyobject_tainted, METH_O, "is pyobject tainted"},
    {"get_tainted_ranges", (PyCFunction)get_tainted_ranges, METH_VARARGS, "get tainted ranges as a list of tuples"},
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef taint_tracking = {
    PyModuleDef_HEAD_INIT, "ddtrace.appsec.iast._taint_tracking",
    "taint tracking module", -1, TaintTrackingMethods};

PyMODINIT_FUNC PyInit__taint_tracking(void)
{
  PyObject *m;
  m = PyModule_Create(&taint_tracking);
  if (m == NULL)
    return NULL;
  return m;
}
