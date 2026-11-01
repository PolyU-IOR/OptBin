#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "optbin.h"

/* The reader's public symbols are loaded by ctypes from this extension.
 * A real module initializer gives pip a platform/ABI-aware native package. */
static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_core", "Bundled OptBin C reader", -1,
    NULL, NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC PyInit__core(void) {
    PyObject *result = PyModule_Create(&module);
    if (!result) return NULL;
    if (PyModule_AddStringConstant(result, "__version__", OPTBIN_VERSION)) {
        Py_DECREF(result);
        return NULL;
    }
    return result;
}
