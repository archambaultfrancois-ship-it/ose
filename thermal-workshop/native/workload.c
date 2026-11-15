#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>
static PyObject *thermal_samples(PyObject *self, PyObject *args) {
    (void)self;
    double temperature; unsigned long long sequence; long channel; Py_ssize_t count;
    PyObject *identity;
    if (!PyArg_ParseTuple(args, "dOln", &temperature, &identity, &channel, &count)) return NULL;
    sequence = PyLong_AsUnsignedLongLong(identity);
    if (PyErr_Occurred()) return NULL;
    if (count < 1 || count > 8192) {
        PyErr_SetString(PyExc_ValueError, "thermal count outside supported range"); return NULL;
    }
    PyObject *out = PyList_New(count);
    if (!out) return NULL;
    for (Py_ssize_t k = 0; k < count; ++k) {
        double x = (double)k * .11;
        x = x + (double)sequence;
        x = x + (double)channel;
        double value = temperature + .2 * sin(x);
        PyObject *item = PyFloat_FromDouble(value);
        if (!item) { Py_DECREF(out); return NULL; }
        PyList_SET_ITEM(out, k, item);
    }
    return out;
}
static PyObject *thermal_burn(PyObject *self, PyObject *args) {
    (void)self; long iterations; double value;
    if (!PyArg_ParseTuple(args, "ld", &iterations, &value)) return NULL;
    if (iterations < 0 || iterations > 200000) {
        PyErr_SetString(PyExc_ValueError, "thermal iterations outside supported range"); return NULL;
    }
    Py_BEGIN_ALLOW_THREADS
    for (long n = 0; n < iterations; ++n) value = sin(value + (double)n * .001) * .5;
    Py_END_ALLOW_THREADS
    return PyFloat_FromDouble(value);
}
static PyMethodDef methods[] = {
    {"samples", thermal_samples, METH_VARARGS, NULL},
    {"burn", thermal_burn, METH_VARARGS, NULL}, {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_thermal_native", NULL, -1, methods, NULL, NULL, NULL, NULL};
PyMODINIT_FUNC PyInit__thermal_native(void) { return PyModule_Create(&module); }
