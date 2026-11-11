#include "ose_component_internal.h"
#include "ose_inputs.h"
#include "ose_transport.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif
#define MAX_SIZE (1024U * 1024U)
#define MAX_LOG_MESSAGE 8192U
static const char log_truncation[] = "\xe2\x80\xa6 [truncated]";
static size_t log_prefix(const char *message, size_t limit) {
    while (limit && ((unsigned char)message[limit] & 0xc0U) == 0x80U) --limit;
    return limit;
}
static uint32_t number(const uint8_t *b) {
    return ((uint32_t)b[0]<<24)|((uint32_t)b[1]<<16)|((uint32_t)b[2]<<8)|b[3];
}
static void header(uint8_t *b, uint32_t n) {
    b[0]=(uint8_t)(n>>24); b[1]=(uint8_t)(n>>16); b[2]=(uint8_t)(n>>8); b[3]=(uint8_t)n;
}
static int transport(void) {
    static int fd = -1, initialized = 0;
    if (!initialized) {
        initialized = 1;
        fd = ose_transport_open();
        if (fd < 0) fprintf(stderr, "OSE_IPC_FD: expected an inherited Unix stream socket; rebuild/run with OSE\n");
    }
    return fd;
}
static int read_all(void *data, size_t size) {
    size_t offset=0;
    int fd = transport();
    if (fd < 0) return 0;
    while (offset<size) {
        ssize_t n=ose_transport_read(fd,(uint8_t *)data+offset,size-offset);
        if (n <= 0) return 0;
        offset+=(size_t)n;
    }
    return 1;
}
static int write_all(const void *data, size_t size) {
    size_t offset = 0;
    int fd = transport();
    if (fd < 0) return 0;
    while (offset < size) {
        ssize_t n = ose_transport_write(fd, (const uint8_t *)data + offset, size - offset);
        if (n <= 0) return 0;
        offset += (size_t)n;
    }
    return 1;
}
int ose_read(OseEvent *event) {
    uint8_t b[4], *data;
    uint32_t n;
    if (!event || !read_all(b,4)) return 0;
    n=number(b);
    if (!n || n>MAX_SIZE) return 0;
    data=malloc(n);
    if (!data) return 0;
    if (!read_all(data,n)) { free(data); return 0; }
    event->op=(char)data[0]; memmove(data,data+1,n-1); event->data=data; event->size=n-1;
#ifdef __linux__
    /* INIT precedes all callbacks, including Ada containers using this SDK. */
    if (event->op == 'I') {
        const char *name = getenv("OSE_PROCESS_NAME");
        if (name && *name && strlen(name) <= 15 &&
            strspn(name, "abcdefghijklmnopqrstuvwxyz0123456789-") == strlen(name))
            (void)prctl(PR_SET_NAME, name, 0UL, 0UL, 0UL);
    }
#endif
    return 1;
}
void ose_release(OseEvent *event) {
    free(event->data); event->data=NULL; event->size=0;
}
int ose_send(char op, const void *data, size_t size) {
    uint8_t b[4];
    if (size>=MAX_SIZE || (size && !data)) return 0;
    header(b,(uint32_t)size+1);
    return write_all(b,4) && write_all(&op,1) && (!size || write_all(data,size));
}
int ose_internal_log(OseLogLevel level, const char *logger, const char *message) {
    size_t n, m, prefix;
    uint8_t *data;
    int ok;
    if (!logger || !message || (level != OSE_LOG_DEBUG && level != OSE_LOG_INFO &&
        level != OSE_LOG_WARNING && level != OSE_LOG_ERROR && level != OSE_LOG_CRITICAL && level != OSE_LOG_FATAL)) return 0;
    n = strlen(logger); m = strlen(message);
    if (n > 200 || !m) return 0;
    prefix = m;
    if (m > MAX_LOG_MESSAGE) {
        prefix = log_prefix(message, MAX_LOG_MESSAGE - (sizeof(log_truncation) - 1));
        m = prefix + sizeof(log_truncation) - 1;
    }
    data = malloc(3 + n + m);
    if (!data) return 0;
    data[0] = (uint8_t)level; data[1] = (uint8_t)(n >> 8); data[2] = (uint8_t)n;
    memcpy(data + 3, logger, n); memcpy(data + 3 + n, message, prefix);
    if (prefix != m) memcpy(data + 3 + n + prefix, log_truncation, sizeof(log_truncation) - 1);
    ok = ose_send('L', data, 3 + n + m);
    free(data); return ok;
}
int ose_internal_vlogf(OseLogLevel level, const char *logger, const char *format, va_list arguments) {
    char message[MAX_LOG_MESSAGE + 1];
    va_list copy;
    int size;
    if (!format) return 0;
    va_copy(copy, arguments);
    size = vsnprintf(message, sizeof(message), format, copy);
    va_end(copy);
    if (size <= 0 || memchr(message, '\0', (size_t)size > MAX_LOG_MESSAGE ? MAX_LOG_MESSAGE : (size_t)size)) return 0;
    if ((size_t)size > MAX_LOG_MESSAGE) {
        size_t prefix = log_prefix(message, MAX_LOG_MESSAGE - (sizeof(log_truncation) - 1));
        memcpy(message + prefix, log_truncation, sizeof(log_truncation));
    }
    return ose_internal_log(level, logger, message);
}
static int named_send(char op, const char *port, const void *payload, size_t size) {
    size_t n=strlen(port);
    uint8_t *data;
    int ok;
    if (n>65535 || size>MAX_SIZE-3-n || !size || !payload) return 0;
    data=malloc(2+n+size);
    if (!data) return 0;
    data[0]=(uint8_t)(n>>8); data[1]=(uint8_t)n;
    memcpy(data+2,port,n); memcpy(data+2+n,payload,size);
    ok=ose_send(op,data,2+n+size); free(data); return ok;
}
int ose_record_bytes(const char *name, const void *payload, size_t size) {
    return named_send('C', name, payload, size);
}
int ose_measurement_declare(const char *name) {
    uint8_t data[202];
    size_t n;
    if (!name || !(n = strlen(name)) || n > 200) return 0;
    data[0] = (uint8_t)(n >> 8); data[1] = (uint8_t)n;
    memcpy(data + 2, name, n);
    return ose_send('M', data, n + 2);
}
int ose_measurement_value(const char *name, double value) {
    uint64_t bits;
    uint8_t data[8];
    size_t i;
    if (!name || sizeof(value) != sizeof(bits) || !isfinite(value)) return 0;
    memcpy(&bits, &value, sizeof(bits));
    for (i = 0; i < 8; ++i) data[i] = (uint8_t)(bits >> (56 - i * 8));
    return named_send('V', name, data, sizeof(data));
}
int ose_declare_recording(const char *name, const char *root, const char *catalogue, const char *description) {
    size_t r = strlen(root), c = strlen(catalogue), d = strlen(description);
    char *body;
    int ok;
    if (r + c + d > MAX_SIZE - 3) return 0;
    body = malloc(r + c + d + 2);
    if (!body) return 0;
    memcpy(body, root, r + 1); memcpy(body + r + 1, description, d + 1);
    memcpy(body + r + d + 2, catalogue, c);
    ok = named_send('N', name, body, r + c + d + 2);
    free(body); return ok;
}
int ose_set_parameters(const char *name, const void *payload, size_t size) {
    OseEvent result;
    int ok;
    if (!named_send('S', name, payload, size) || !ose_read(&result)) return 0;
    ok = result.op == 'A' && result.size == 1 && result.data[0] == 1;
    if (!ok && result.op == 'A' && result.size > 1 && result.data[0] == 0)
        fprintf(stderr, "parameter write: %.*s\n", (int)(result.size - 1), (const char *)result.data + 1);
    ose_release(&result);
    return ok;
}
int ose_input(const OseEvent *event, const uint8_t **port, size_t *port_size,
                const uint8_t **catalogue, size_t *catalogue_size,
                const uint8_t **payload, size_t *payload_size) {
    const uint8_t *data;
    size_t n,c;
    if (event->op!='U' || event->size<6) return 0;
    data=event->data;
    n=((size_t)data[0]<<8)|data[1];
    if (n>event->size-6) return 0;
    c=number(data+2+n);
    if (c>event->size-6-n) return 0;
    *port=data+2; *port_size=n;
    *catalogue=data+6+n; *catalogue_size=c;
    *payload=data+6+n+c; *payload_size=event->size-6-n-c;
    return 1;
}

/* CH/2 wire primitives use the common outer frame and shared services. */
typedef struct { const uint8_t *data; size_t size, offset; int ok; } InputReader;
static const uint8_t *input_take(InputReader *r, size_t size) {
    const uint8_t *p;
    if (!r->ok || size > r->size - r->offset) { r->ok = 0; return NULL; }
    p = r->data + r->offset; r->offset += size; return p;
}
static uint64_t input_number(InputReader *r, size_t size) {
    const uint8_t *p = input_take(r, size);
    uint64_t value = 0;
    size_t i;
    if (p) for (i = 0; i < size; ++i) value = (value << 8) | p[i];
    return value;
}
static int input_utf8(const uint8_t *p, size_t size) {
    size_t i = 0;
    while (i < size) {
        uint32_t value = p[i++], minimum;
        size_t tail;
        if (!value) return 0; /* C strings cannot represent embedded NUL. */
        if (value < 128) continue;
        if (value >= 0xc2 && value <= 0xdf) { tail = 1; minimum = 0x80; value &= 31; }
        else if (value >= 0xe0 && value <= 0xef) { tail = 2; minimum = 0x800; value &= 15; }
        else if (value >= 0xf0 && value <= 0xf4) { tail = 3; minimum = 0x10000; value &= 7; }
        else return 0;
        if (tail > size - i) return 0;
        while (tail--) {
            if ((p[i] & 0xc0) != 0x80) return 0;
            value = (value << 6) | (p[i++] & 63);
        }
        if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) return 0;
    }
    return 1;
}
static char *input_text(InputReader *r, int wide, size_t *length) {
    size_t n = (size_t)input_number(r, wide ? 4 : 2);
    const uint8_t *p = input_take(r, n);
    char *copy;
    if (!p || !n || !input_utf8(p, n)) { r->ok = 0; return NULL; }
    copy = malloc(n + 1);
    if (!copy) { r->ok = 0; return NULL; }
    memcpy(copy, p, n); copy[n] = 0;
    if (length) *length = n;
    return copy;
}
static int input_id(InputReader *r, uint64_t *value) {
    unsigned flag = (unsigned)input_number(r, 1);
    if (flag > 1) r->ok = 0;
    if (flag == 1) *value = input_number(r, 8);
    return flag == 1;
}
static int input_record(InputReader *r, OseInputRecord *record) {
    unsigned flag = (unsigned)input_number(r, 1);
    if (flag > 1) r->ok = 0;
    record->present = flag == 1;
    if (record->present) {
        record->handle = input_text(r, 0, NULL);
        record->source = input_text(r, 0, NULL);
        record->has_app_id = input_id(r, &record->app_id);
        record->received_ns = input_number(r, 8);
        record->catalogue = input_text(r, 1, &record->catalogue_size);
        record->envelope_size = r->size - r->offset;
        record->envelope = input_take(r, record->envelope_size);
        if (!record->envelope_size) r->ok = 0;
    }
    return r->ok && r->offset == r->size;
}
int ose_inputs_record(const void *data, size_t size, OseInputRecord *record) {
    InputReader r = {data, size, 0, data != NULL && size < MAX_SIZE};
    return input_record(&r, record);
}
void ose_inputs_record_release(OseInputRecord *record) {
    free(record->handle); free(record->source); free(record->catalogue);
    memset(record, 0, sizeof(*record));
}
int ose_inputs_activation(const OseEvent *event, OseActivation *activation) {
    InputReader r = {event->data, event->size, 0,
        event->op == 'T' && event->data != NULL && event->size < MAX_SIZE};
    activation->time_ns = input_number(&r, 8);
    activation->index = input_number(&r, 8);
    activation->due_ns = input_number(&r, 8);
    activation->elapsed = input_number(&r, 8);
    activation->event = (OseInputEvent)input_number(&r, 1);
    activation->port = input_text(&r, 0, NULL);
    return input_record(&r, &activation->record) && activation->index && activation->elapsed &&
        activation->due_ns <= activation->time_ns &&
        ((activation->event == OSE_INPUT_MESSAGE && activation->record.present && activation->elapsed == 1 &&
          activation->record.received_ns <= activation->time_ns) ||
         (activation->event == OSE_INPUT_TIMEOUT && !activation->record.present));
}
void ose_inputs_activation_release(OseActivation *activation) {
    free(activation->port); ose_inputs_record_release(&activation->record);
    memset(activation, 0, sizeof(*activation));
}
static void input_u64(uint8_t *data, uint64_t value) {
    size_t i;
    for (i = 0; i < 8; ++i) data[i] = (uint8_t)(value >> (56 - 8 * i));
}
static int input_string_size(const char *text, size_t *size) {
    if (!text || !(*size = strlen(text)) || *size > 65535) return 0;
    return input_utf8((const uint8_t *)text, *size);
}
static size_t input_put_text(uint8_t *data, const char *text, size_t n) {
    data[0] = (uint8_t)(n >> 8); data[1] = (uint8_t)n;
    memcpy(data + 2, text, n); return n + 2;
}
int ose_inputs_publish(const char *port, const void *payload, size_t size, const uint64_t *app_id) {
    size_t n, offset, overhead;
    uint8_t *data;
    int ok;
    if (!input_string_size(port, &n) || !payload || !size) return 0;
    overhead = n + 3 + (app_id ? 8 : 0);
    if (size >= MAX_SIZE - overhead) return 0;
    data = malloc(overhead + size);
    if (!data) return 0;
    offset = input_put_text(data, port, n); data[offset++] = app_id != NULL;
    if (app_id) { input_u64(data + offset, *app_id); offset += 8; }
    memcpy(data + offset, payload, size);
    ok = ose_send('P', data, overhead + size); free(data); return ok;
}
int ose_inputs_request(unsigned operation, const char *port, const OseSelector *selector,
                       const uint64_t *delay_ns, OseEvent *reply) {
    size_t n, h = 0, s = 0, offset, size;
    uint8_t *data;
    unsigned flags = 0;
    int ok;
    if (operation < 1 || operation > 5 || !input_string_size(port, &n)) return 0;
    if (operation <= 3) {
        if (delay_ns) return 0;
        if (selector) {
            if (selector->has_app_id != 0 && selector->has_app_id != 1) return 0;
            flags = selector->has_app_id ? 1 : 0;
            if (selector->handle) { if (!input_string_size(selector->handle, &h)) return 0; flags |= 2; }
            if (selector->source) { if (!input_string_size(selector->source, &s)) return 0; flags |= 4; }
        }
    } else if (selector || (operation == 5 && delay_ns) || (delay_ns && !*delay_ns)) return 0;
    size = 3 + n + (operation <= 3 ? 1 + (flags & 1 ? 8 : 0) + (h ? h + 2 : 0) + (s ? s + 2 : 0)
                              : operation == 4 ? 1 + (delay_ns ? 8 : 0) : 0);
    data = malloc(size);
    if (!data) return 0;
    data[0] = (uint8_t)operation; offset = 1 + input_put_text(data + 1, port, n);
    if (operation <= 3) {
        data[offset++] = (uint8_t)flags;
        if (flags & 1) { input_u64(data + offset, selector->app_id); offset += 8; }
        if (h) offset += input_put_text(data + offset, selector->handle, h);
        if (s) offset += input_put_text(data + offset, selector->source, s);
    } else if (operation == 4) {
        data[offset++] = delay_ns != NULL;
        if (delay_ns) input_u64(data + offset, *delay_ns);
    }
    ok = ose_send('Q', data, size); free(data);
    if (!ok || !ose_read(reply)) return 0;
    if (reply->op != 'A' || !reply->size || reply->data[0] != 1) {
        if (reply->op == 'A' && reply->size > 1 && reply->size <= 4097 && reply->data[0] == 0)
            fprintf(stderr, "input operation: %.*s\n", (int)(reply->size - 1), (const char *)reply->data + 1);
        return 0;
    }
    return 1;
}
