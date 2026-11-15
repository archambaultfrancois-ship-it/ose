#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include "type_engine.h"
#include "sdl_wire.h"
#include <stdint.h>
#include <string.h>

typedef struct {
    const uint8_t *id; size_t id_len;
    const uint8_t *topic; size_t topic_len;
    const uint8_t *catalogue; size_t catalogue_len;
    const uint8_t *body; size_t body_len;
    uint64_t app_id, received_ns;
    uint8_t has_id;
} Record;
typedef struct { SdlContext *context; uint32_t identity; char *root; } Plan;
static void destroy(PyObject *capsule) {
    Plan *p = PyCapsule_GetPointer(capsule, "ose.native");
    if (!p) { PyErr_Clear(); return; }
    type_context_free(p->context);
    PyMem_Free(p->root); PyMem_Free(p);
}
static PyObject *prepare(PyObject *self, PyObject *args) {
    (void)self;
    const char *text, *root; Py_ssize_t text_len, root_len;
    if (!PyArg_ParseTuple(args, "s#s#", &text, &text_len, &root, &root_len)) return NULL;
    Plan *p = PyMem_Calloc(1, sizeof(*p));
    if (!p) return PyErr_NoMemory();
    p->context = type_prepare(text, (size_t)text_len);
    p->root = PyMem_Malloc((size_t)root_len + 1);
    if (p->root) { memcpy(p->root, root, (size_t)root_len); p->root[root_len] = 0; }
    if (!p->context || !p->root || !type_catalogue_id(text, (size_t)text_len, &p->identity)
        || !type_context_message_id(p->context, p->root) || memchr(root, 0, (size_t)root_len)) {
        type_context_free(p->context); PyMem_Free(p->root); PyMem_Free(p);
        PyErr_SetString(PyExc_ValueError, "invalid native validation plan"); return NULL;
    }
    PyObject *out = PyCapsule_New(p, "ose.native", destroy);
    if (!out) { type_context_free(p->context); PyMem_Free(p->root); PyMem_Free(p); }
    return out;
}
static PyObject *validate(PyObject *self, PyObject *args) {
    (void)self; PyObject *capsule, *body;
    if (!PyArg_ParseTuple(args, "OO", &capsule, &body)) return NULL;
    Plan *p = PyCapsule_GetPointer(capsule, "ose.native");
    if (!p) return NULL;
    if (!PyBytes_Check(body)) { PyErr_SetString(PyExc_TypeError, "expected immutable bytes"); return NULL; }
    const uint8_t *data = (const uint8_t *)PyBytes_AS_STRING(body);
    size_t len = (size_t)PyBytes_GET_SIZE(body); int valid = 0;
    /* Immutable bytes and an immutable context stay alive throughout this call. */
    Py_BEGIN_ALLOW_THREADS
    {
        uint32_t identity; const void *payload; size_t size;
        if (type_envelope_unpack(data, len, &identity, &payload, &size) && identity == p->identity
            && type_context_validate(p->context, payload, size)) {
            const char *name = type_message_name(p->context, payload, size);
            valid = name && !strcmp(name, p->root);
        }
    }
    Py_END_ALLOW_THREADS
    if (!valid) { PyErr_SetString(PyExc_ValueError, "invalid native SDL envelope/payload/root"); return NULL; }
    Py_RETURN_NONE;
}

static int uint64_value(PyObject *value, uint64_t *out) {
    if (!PyLong_CheckExact(value)) { PyErr_SetString(PyExc_ValueError, "expected uint64"); return -1; }
    unsigned long long n = PyLong_AsUnsignedLongLong(value);
    if (PyErr_Occurred()) { PyErr_Clear(); PyErr_SetString(PyExc_ValueError, "expected uint64"); return -1; }
    *out = (uint64_t)n; return 0;
}
static int text_value(PyObject *value, const uint8_t **out, size_t *len, int wide) {
    if (!PyUnicode_Check(value)) { PyErr_SetString(PyExc_ValueError, "CH/2 text must be nonempty"); return -1; }
    Py_ssize_t size; const char *data = PyUnicode_AsUTF8AndSize(value, &size);
    if (!data) return -1;
    if (!size || size > (wide ? 1048576 : 65535)) { PyErr_SetString(PyExc_ValueError, "invalid CH/2 text length"); return -1; }
    *out = (const uint8_t *)data; *len = (size_t)size; return 0;
}
static PyObject *optional(PyObject *mapping, const char *key) {
    PyObject *out = PyMapping_GetItemString(mapping, key);
    if (!out && PyErr_ExceptionMatches(PyExc_KeyError)) { PyErr_Clear(); return Py_NewRef(Py_None); }
    return out;
}
typedef struct { Record wire; PyObject *owners[6]; size_t size; int absent; } OwnedRecord;
static void clear_record(OwnedRecord *r) { for (int i = 0; i < 6; ++i) Py_XDECREF(r->owners[i]); }
static int read_record(PyObject *record, OwnedRecord *r) {
    memset(r, 0, sizeof(*r));
    if (record == Py_None) { r->absent = 1; r->size = 1; return 0; }
    const char *keys[] = {"id", "topic", "catalogue", "body"};
    for (int i = 0; i < 4; ++i) if (!(r->owners[i] = PyMapping_GetItemString(record, keys[i]))) goto fail;
    if (text_value(r->owners[0], &r->wire.id, &r->wire.id_len, 0)
        || text_value(r->owners[1], &r->wire.topic, &r->wire.topic_len, 0)
        || text_value(r->owners[2], &r->wire.catalogue, &r->wire.catalogue_len, 1)) goto fail;
    if (!PyBytes_Check(r->owners[3]) || !PyBytes_GET_SIZE(r->owners[3])) {
        PyErr_SetString(PyExc_ValueError, "expected nonempty immutable envelope bytes"); goto fail;
    }
    r->wire.body = (const uint8_t *)PyBytes_AS_STRING(r->owners[3]);
    r->wire.body_len = (size_t)PyBytes_GET_SIZE(r->owners[3]);
    r->owners[4] = optional(record, "app_id");
    r->owners[5] = PyMapping_GetItemString(record, "received_ns");
    if (!r->owners[5] && PyErr_ExceptionMatches(PyExc_KeyError)) {
        PyErr_Clear(); r->owners[5] = PyLong_FromLong(0);
    }
    if (!r->owners[4] || !r->owners[5]) goto fail;
    r->wire.has_id = r->owners[4] != Py_None;
    if ((r->wire.has_id && uint64_value(r->owners[4], &r->wire.app_id))
        || uint64_value(r->owners[5], &r->wire.received_ns)) goto fail;
    r->size = 1 + 2 + r->wire.id_len + 2 + r->wire.topic_len + 1 + r->wire.has_id * 8
              + 8 + 4 + r->wire.catalogue_len;
    if (r->wire.body_len > (size_t)PY_SSIZE_T_MAX - r->size) { PyErr_NoMemory(); goto fail; }
    r->size += r->wire.body_len;
    return 0;
fail: clear_record(r); return -1;
}
static void put_u64(uint8_t *out, uint64_t n) {
    sdl_wire_write_u32(out, (uint32_t)(n >> 32)); sdl_wire_write_u32(out + 4, (uint32_t)n);
}
static uint8_t *put_text(uint8_t *out, const uint8_t *text, size_t len, int wide) {
    if (wide) { sdl_wire_write_u32(out, (uint32_t)len); out += 4; }
    else { out[0] = (uint8_t)(len >> 8); out[1] = (uint8_t)len; out += 2; }
    memcpy(out, text, len); return out + len;
}
static void write_record(const OwnedRecord *r, uint8_t *out) {
    if (r->absent) { *out = 0; return; }
    const Record *v = &r->wire;
    *out++ = 1; out = put_text(out, v->id, v->id_len, 0); out = put_text(out, v->topic, v->topic_len, 0);
    *out++ = v->has_id;
    if (v->has_id) { put_u64(out, v->app_id); out += 8; }
    put_u64(out, v->received_ns); out += 8;
    out = put_text(out, v->catalogue, v->catalogue_len, 1); memcpy(out, v->body, v->body_len);
}
static PyObject *record_data(PyObject *self, PyObject *args) {
    (void)self; PyObject *record;
    if (!PyArg_ParseTuple(args, "O", &record)) return NULL;
    OwnedRecord r; if (read_record(record, &r)) return NULL;
    PyObject *out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)r.size);
    if (out) write_record(&r, (uint8_t *)PyBytes_AS_STRING(out));
    clear_record(&r); return out;
}
static PyObject *activation_data(PyObject *self, PyObject *args) {
    (void)self; PyObject *reference, *index;
    if (!PyArg_ParseTuple(args, "OO", &reference, &index)) return NULL;
    const char *keys[] = {"time_ns", "due_ns", "elapsed", "port", "event", "record"};
    PyObject *values[6] = {NULL}; PyObject *out = NULL;
    uint64_t n, time, due, elapsed; size_t port_len; const uint8_t *port;
    for (int i = 0; i < 6; ++i) if (!(values[i] = PyObject_GetAttrString(reference, keys[i]))) goto done;
    if (uint64_value(index, &n) || uint64_value(values[0], &time) || uint64_value(values[1], &due)
        || uint64_value(values[2], &elapsed) || text_value(values[3], &port, &port_len, 0)) goto done;
    if (!n || !elapsed || due > time) { PyErr_SetString(PyExc_ValueError, "invalid CH/2 activation context"); goto done; }
    int event = PyUnicode_Check(values[4]) && PyUnicode_CompareWithASCIIString(values[4], "message") == 0 ? 1 :
                PyUnicode_Check(values[4]) && PyUnicode_CompareWithASCIIString(values[4], "timeout") == 0 ? 2 : 0;
    if (!event) { PyErr_SetString(PyExc_KeyError, "invalid event"); goto done; }
    OwnedRecord r; if (read_record(values[5], &r)) goto done;
    if (r.size > (size_t)PY_SSIZE_T_MAX - 35 - port_len) PyErr_NoMemory();
    else out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)(35 + port_len + r.size));
    if (out) {
        uint8_t *data = (uint8_t *)PyBytes_AS_STRING(out);
        put_u64(data, time); put_u64(data + 8, n); put_u64(data + 16, due); put_u64(data + 24, elapsed);
        data[32] = (uint8_t)event; data = put_text(data + 33, port, port_len, 0); write_record(&r, data);
    }
    clear_record(&r);
done: for (int i = 0; i < 6; ++i) Py_XDECREF(values[i]); return out;
}
static PyObject *check_record_size(PyObject *self, PyObject *args) {
    (void)self; PyObject *port, *record; const uint8_t *unused; size_t port_len;
    if (!PyArg_ParseTuple(args, "OO", &port, &record) || text_value(port, &unused, &port_len, 0)) return NULL;
    OwnedRecord r; if (read_record(record, &r)) return NULL;
    int oversize = r.size > 1048576 - 36 - port_len;
    clear_record(&r);
    if (oversize) { PyErr_SetString(PyExc_ValueError, "message exceeds CH/2 input reference limit"); return NULL; }
    Py_RETURN_NONE;
}
static PyObject *unpack_frames(PyObject *self, PyObject *object) {
    (void)self; Py_buffer buffer;
    if (PyObject_GetBuffer(object, &buffer, PyBUF_SIMPLE)) return NULL;
    const uint8_t *data = buffer.buf; size_t size = (size_t)buffer.len, offset = 0;
    PyObject *frames = PyList_New(0), *result = NULL;
    if (!frames) goto done;
    while (size - offset >= 4) {
        uint32_t length = sdl_wire_read_u32(data + offset);
        if (!length || length > 1048576) {
            /* Earlier complete frames must remain visible before the failure. */
            if (PyList_GET_SIZE(frames)) break;
            PyErr_SetString(PyExc_ValueError, "invalid IPC size"); goto done;
        }
        if (size - offset - 4 < length) break;
        PyObject *op = PyUnicode_FromOrdinal(data[offset + 4]);
        PyObject *body = PyBytes_FromStringAndSize((const char *)data + offset + 5, length - 1);
        PyObject *frame = op && body ? PyTuple_Pack(2, op, body) : NULL;
        Py_XDECREF(op); Py_XDECREF(body);
        if (!frame) goto done;
        int status = PyList_Append(frames, frame); Py_DECREF(frame);
        if (status) goto done;
        offset += 4 + length;
    }
    result = Py_BuildValue("On", frames, (Py_ssize_t)offset);
done:
    Py_XDECREF(frames); PyBuffer_Release(&buffer); return result;
}
/* Compact CENV/2 metadata. Unsupported shapes return None so extensions and
 * generic control messages retain the existing Python representation. */
typedef struct { const char *name; int number, optional; } CenvField;
static const CenvField cenv_data[] = {
    {"subscription",0,0}, {"id",0,0}, {"epoch",0,0}, {"sequence",1,0},
    {"topic",0,0}, {"app_id",1,1}, {NULL,0,0}};
static const CenvField cenv_ack[] = {{"subscription",0,0}, {"id",0,0}, {NULL,0,0}};
static const CenvField cenv_publish[] = {
    {"request",1,0}, {"topic",0,0}, {"app_id",1,1}, {NULL,0,0}};
static const CenvField cenv_result[] = {
    {"request",1,0}, {"content",0,1}, {"error",0,1}, {"error_code",0,1}, {NULL,0,0}};
static const CenvField cenv_deliver[] = {
    {"request",1,0}, {"subscription",0,0}, {"id",0,0}, {"epoch",0,0},
    {"sequence",1,0}, {"source_token",0,0}, {"target_token",0,0},
    {"source",0,0}, {"topic",0,0}, {"app_id",1,1}, {NULL,0,0}};

static PyObject *cenv_prefix(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *header; Py_ssize_t payload, limit;
    if (!PyArg_ParseTuple(args, "Onn", &header, &payload, &limit)) return NULL;
    if (!PyDict_CheckExact(header)) Py_RETURN_NONE;
    PyObject *operation = PyDict_GetItemString(header, "op");
    if (!operation || !PyUnicode_CheckExact(operation)) Py_RETURN_NONE;
    const CenvField *fields = NULL; unsigned code = 0;
    if (!PyUnicode_CompareWithASCIIString(operation, "data")) { fields=cenv_data; code=1; }
    else if (!PyUnicode_CompareWithASCIIString(operation, "ack")) { fields=cenv_ack; code=2; }
    else if (!PyUnicode_CompareWithASCIIString(operation, "publish")) { fields=cenv_publish; code=3; }
    else if (!PyUnicode_CompareWithASCIIString(operation, "result")) { fields=cenv_result; code=4; }
    else if (!PyUnicode_CompareWithASCIIString(operation, "deliver")) { fields=cenv_deliver; code=5; }
    else Py_RETURN_NONE;
    const char *texts[10] = {NULL}; Py_ssize_t lengths[10] = {0};
    uint64_t numbers[10] = {0}; unsigned present=0, flags=0, optional=0;
    Py_ssize_t size = code == 2 ? 0 : 1, count = 1;
    for (unsigned i=0; fields[i].name; ++i) {
        PyObject *value = PyDict_GetItemString(header, fields[i].name);
        if (fields[i].optional) {
            if (value) flags |= 1u << optional;
            ++optional;
        } else if (!value) Py_RETURN_NONE;
        if (!value) continue;
        present |= 1u << i;
        ++count;
        if (fields[i].number) {
            if (!PyLong_CheckExact(value)) Py_RETURN_NONE;
            numbers[i] = PyLong_AsUnsignedLongLong(value);
            if (PyErr_Occurred()) { PyErr_Clear(); Py_RETURN_NONE; }
            size += 8;
        } else {
            if (!PyUnicode_CheckExact(value)) Py_RETURN_NONE;
            texts[i] = PyUnicode_AsUTF8AndSize(value, &lengths[i]);
            if (!texts[i]) return NULL;
            if (lengths[i] > 65535) Py_RETURN_NONE;
            size += 2 + lengths[i];
        }
    }
    if (count != PyDict_Size(header)) Py_RETURN_NONE;
    if (size > 65536) {
        PyErr_SetString(PyExc_ValueError, "metadata exceeds 64 KiB"); return NULL;
    }
    if (payload < 0 || limit < 8 + size || payload > limit - 8 - size) {
        PyErr_SetString(PyExc_ValueError, "transport message exceeds configured size limit"); return NULL;
    }
    PyObject *out = PyBytes_FromStringAndSize(NULL, 8 + size);
    if (!out) return NULL;
    uint8_t *data = (uint8_t *)PyBytes_AS_STRING(out);
    memcpy(data, "CE\x02", 3); data[3] = (uint8_t)code;
    sdl_wire_write_u32(data+4, (uint32_t)size); data += 8;
    if (optional) *data++ = (uint8_t)flags;
    for (unsigned i=0; fields[i].name; ++i) {
        if (!(present & (1u << i))) continue;
        if (fields[i].number) { put_u64(data, numbers[i]); data += 8; }
        else data = put_text(data, (const uint8_t *)texts[i], (size_t)lengths[i], 0);
    }
    return out;
}

static PyMethodDef methods[] = {
    {"cenv_prefix", cenv_prefix, METH_VARARGS, NULL},
    {"prepare", prepare, METH_VARARGS, NULL}, {"validate", validate, METH_VARARGS, NULL},
    {"record_data", record_data, METH_VARARGS, NULL}, {"activation_data", activation_data, METH_VARARGS, NULL},
    {"check_record_size", check_record_size, METH_VARARGS, NULL},
    {"unpack_frames", unpack_frames, METH_O, NULL}, {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_native", NULL, -1, methods, NULL, NULL, NULL, NULL};
PyMODINIT_FUNC PyInit__native(void) { return PyModule_Create(&module); }
