#include "AspectExtend.h"
#include "Initializer/Initializer.h"
#include <iostream>  // FIXME: remove

PyObject* api_extend_aspect(PyObject* self, PyObject* const* args, Py_ssize_t nargs) {
    cout << "Nargs: " << nargs << endl;
    cout << "!self: " << !self << endl;
    cout << "!args: " << !args << endl;
    cout << "!PyByteArray_Check(self): " << !PyByteArray_Check(self) << endl;
    if (nargs != 2 or !self or !args or !PyByteArray_Check(self)) {
        cout << "Exit\n";
        // TODO: any other more sane error handling?
        return nullptr;
    }

    PyObject* to_add = args[0];
    // TODO: Check if this works with bytes and bytearrays or we have to convert bytes to
    // a new bytearray!
    PyObject* result = PyByteArray_Concat(self, to_add);

    if (PyUnicode_GET_LENGTH(result) == 0) {
        // Empty result cannot have taint ranges
        return result;
    }

    auto ctx_map = initializer->get_tainting_map();
    if (not ctx_map or ctx_map->empty()) {
        return result;
    }

    const auto& to_self = get_tainted_object(self, ctx_map);
    auto to_result = initializer->allocate_tainted_object(to_self);
    const auto& to_toadd = get_tainted_object(to_add, ctx_map);
    to_result->add_ranges_shifted(to_toadd, (long) PyByteArray_Size(self));
    return result;
}
