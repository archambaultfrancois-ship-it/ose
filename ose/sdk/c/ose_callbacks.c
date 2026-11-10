#include "ose_callbacks.h"
#include "ose_component_internal.h"
#include "type_engine.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

typedef struct {
    char *name, *root;
    SdlContext *encoder;
    int active;
} RecordingPoint;
typedef struct {
    SdlContext *codec;
    char *catalogue;
    size_t catalogue_size;
} InputCodec;
typedef struct {
    void *message;
    int present;
} ParameterValue;
typedef struct InputRead {
    OseInputRecord record;
    void *message;
    struct InputRead *next;
} InputRead;
typedef struct {
    const OsePortSpec *inputs, *outputs;
    size_t input_count, output_count;
    InputCodec *input_codecs;
    SdlContext **encoders;
    const OseParameterSpec *parameters;
    size_t parameter_count;
    ParameterValue *parameter_values;
    SdlContext **parameter_codecs;
    int active, failed, parameter_writes;
    RecordingPoint recording[256];
    size_t recording_count, recording_bytes;
    int ready;
    char *measurements[128];
    size_t measurement_count;
    const OseInputPolicy *policies;
    const char *const *allowed_outputs;
    size_t allowed_count;
    InputRead *reads;
} Runtime;

static RecordingPoint *recording_slot(Runtime *runtime, const char *name) {
    uint32_t hash = 2166136261u;
    const unsigned char *p = (const unsigned char *)name;
    size_t index;
    while (*p) { hash ^= *p++; hash *= 16777619u; }
    index = hash % 256;
    while (runtime->recording[index].name && strcmp(runtime->recording[index].name, name))
        index = (index + 1) % 256;
    return &runtime->recording[index];
}
static int recording_name(const char *name) {
    const unsigned char *p = (const unsigned char *)name;
    int first = 1;
    if (!name || !*name || strlen(name) > 200) return 0;
    for (; *p; ++p) {
        if (*p == '.') { if (first) return 0; first = 1; continue; }
        if (!((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') || *p == '_' ||
              (!first && *p >= '0' && *p <= '9'))) return 0;
        first = 0;
    }
    return !first;
}
static char *recording_copy(const char *text) {
    size_t size = strlen(text) + 1;
    char *copy = malloc(size);
    if (copy) memcpy(copy, text, size);
    return copy;
}
int ose_declare_measurement(OseContext *ctx, const char *name) {
    Runtime *runtime = ctx->private_runtime;
    size_t i;
    char *copy;
    if (runtime->ready || !runtime->parameter_writes || !recording_name(name) || runtime->measurement_count >= 128)
        return ose_fail(ctx, "invalid measurement declaration");
    for (i = 0; i < runtime->measurement_count; ++i)
        if (!strcmp(runtime->measurements[i], name)) return ose_fail(ctx, "duplicate measurement name");
    copy = recording_copy(name);
    if (!copy) return ose_fail(ctx, "measurement allocation failed");
    runtime->measurements[runtime->measurement_count++] = copy;
    if (!ose_measurement_declare(name)) return ose_fail(ctx, "measurement declaration failed");
    return 1;
}
int ose_measure(OseContext *ctx, const char *name, double value) {
    Runtime *runtime = ctx->private_runtime;
    size_t i;
    if (!runtime->active || !recording_name(name) || !isfinite(value))
        return ose_fail(ctx, "measurement requires a finite fl64 during an input callback");
    for (i = 0; i < runtime->measurement_count; ++i)
        if (!strcmp(runtime->measurements[i], name)) {
            if (!ose_measurement_value(name, value)) return ose_fail(ctx, "measurement emission failed");
            return 1;
        }
    return ose_fail(ctx, "unknown measurement name");
}
int ose_declare_recording_value(OseContext *ctx, const char *name, const char *root,
                               const char *catalogue, const char *description) {
    Runtime *runtime = ctx->private_runtime;
    RecordingPoint *point;
    if (runtime->ready || !runtime->parameter_writes || !recording_name(name) || !root || !catalogue ||
        !description || strlen(description) > 1024 || runtime->recording_count >= 128)
        return ose_fail(ctx, "invalid recording declaration");
    point = recording_slot(runtime, name);
    if (point->name) return ose_fail(ctx, "duplicate recording point");
    point->name = recording_copy(name); point->root = recording_copy(root);
    point->encoder = type_prepare(catalogue, strlen(catalogue));
    if (!point->name || !point->root || !point->encoder ||
        !ose_declare_recording(name, root, catalogue, description))
        return ose_fail(ctx, "recording declaration failed");
    runtime->recording_count++;
    return 1;
}
int ose_recording_enabled(OseContext *ctx, const char *name) {
    RecordingPoint *point;
    if (!recording_name(name)) return ose_fail(ctx, "invalid recording name");
    point = recording_slot(ctx->private_runtime, name);
    if (!point->name) return ose_fail(ctx, "unknown recording point");
    return point->active;
}
OseRecordResult ose_record_value(OseContext *ctx, const char *name, const char *root, const void *value) {
    Runtime *runtime = ctx->private_runtime;
    RecordingPoint *point;
    size_t size, overhead;
    void *payload;
    int ok;
    if (!runtime->active || !recording_name(name) || !root || !value) goto invalid;
    point = recording_slot(runtime, name);
    if (!point->name || strcmp(point->root, root)) goto invalid;
    if (!point->active) return OSE_RECORD_DISABLED;
    overhead = strlen(name) + 2;
    if (runtime->recording_bytes + overhead >= 256 * 1024) return OSE_RECORD_DROPPED;
    payload = type_encode(point->encoder, root, value, &size);
    if (!payload) goto invalid;
    if (size > 256 * 1024 - runtime->recording_bytes - overhead) {
        type_free(payload); return OSE_RECORD_DROPPED;
    }
    ok = ose_record_bytes(name, payload, size); type_free(payload);
    if (!ok) goto invalid;
    runtime->recording_bytes += size + overhead;
    return OSE_RECORD_QUEUED;
invalid:
    ose_fail(ctx, "invalid recording emission"); return OSE_RECORD_ERROR;
}
static int recording_update(OseContext *ctx, const OseEvent *event) {
    Runtime *runtime = ctx->private_runtime;
    size_t i, offset = 0;
    char name[201];
    for (i = 0; i < 256; ++i) runtime->recording[i].active = 0;
    while (offset < event->size) {
        size_t end = offset;
        RecordingPoint *point;
        while (end < event->size && event->data[end]) ++end;
        if (end == offset || end - offset > 200 || (end < event->size && end + 1 == event->size)) return 0;
        memcpy(name, event->data + offset, end - offset); name[end - offset] = 0;
        point = recording_slot(runtime, name);
        if (!point->name || point->active) return 0;
        point->active = 1; offset = end + 1;
    }
    return 1;
}

double ose_now(const OseContext *ctx) { return (double)ctx->time_ns / 1000000000.0; }
int ose_logf(OseContext *ctx, OseLogLevel level, const char *logger, const char *format, ...) {
    va_list arguments;
    int ok;
    va_start(arguments, format);
    ok = ose_internal_vlogf(level, logger, format, arguments);
    va_end(arguments);
    return ok ? 1 : ose_fail(ctx, "log deposit failed");
}
uint64_t ose_time_ns(const OseContext *ctx) { return ctx->time_ns; }
uint64_t ose_tick_index(const OseContext *ctx) { return ctx->tick_index; }

int ose_fail(OseContext *ctx, const char *message) {
    Runtime *runtime = ctx->private_runtime;
    runtime->failed = 1;
    fprintf(stderr, "%s: %s\n", ctx->name, message);
    return 0;
}

static int publish_value(OseContext *ctx, size_t index, const void *value, const uint64_t *id) {
    Runtime *runtime = ctx->private_runtime;
    size_t size;
    void *payload;
    int result;
    if (!runtime->active || index >= runtime->output_count || !value)
        return ose_fail(ctx, "invalid publication outside an input callback or output port");
    {
        size_t i;
        for (i = 0; i < runtime->allowed_count; ++i)
            if (!strcmp(runtime->allowed_outputs[i], runtime->outputs[index].name)) break;
        if (i == runtime->allowed_count) return ose_fail(ctx, "input event cannot activate output");
    }
    payload = type_encode(runtime->encoders[index], runtime->outputs[index].root, value, &size);
    if (!payload) return ose_fail(ctx, "SDL output encoding failed");
    result = ose_inputs_publish(runtime->outputs[index].name, payload, size, id);
    type_free(payload);
    return result ? 1 : ose_fail(ctx, "output IPC failed");
}
int ose_publish_value(OseContext *ctx, size_t index, const void *value) {
    return publish_value(ctx, index, value, NULL);
}
int ose_publish_value_with_id(OseContext *ctx, size_t index, const void *value, uint64_t id) {
    return publish_value(ctx, index, value, &id);
}

const void *ose_parameter_value(const OseContext *ctx, size_t index) {
    Runtime *runtime = ctx->private_runtime;
    return index < runtime->parameter_count && runtime->parameters[index].readable
        ? runtime->parameter_values[index].message : NULL;
}
int ose_set_parameter_value(OseContext *ctx, size_t index, const void *value) {
    Runtime *runtime = ctx->private_runtime;
    void *payload;
    size_t size;
    int ok;
    if (!runtime->parameter_writes || index >= runtime->parameter_count || !runtime->parameters[index].writable || !value) return 0;
    payload = type_encode(runtime->parameter_codecs[index], runtime->parameters[index].root, value, &size);
    if (!payload) return 0;
    ok = ose_set_parameters(runtime->parameters[index].name, payload, size);
    type_free(payload);
    return ok;
}
static int parameter_update(OseContext *ctx, const OseEvent *event, int initial) {
    Runtime *runtime = ctx->private_runtime;
    const uint8_t *name, *text, *payload;
    const char *root;
    size_t n, text_size, payload_size, i;
    void *value;
    if (event->op != 'U' || !ose_input(event, &name, &n, &text, &text_size, &payload, &payload_size)) return 0;
    for (i = 0; i < runtime->parameter_count; ++i)
        if (strlen(runtime->parameters[i].name) == n && !memcmp(runtime->parameters[i].name, name, n)) break;
    if (i == runtime->parameter_count || !runtime->parameters[i].readable ||
        (initial && runtime->parameter_values[i].present) ||
        strlen(runtime->parameters[i].catalogue) != text_size ||
        memcmp(runtime->parameters[i].catalogue, text, text_size)) return 0;
    root = type_message_name(runtime->parameter_codecs[i], payload, payload_size);
    if (!root || strcmp(root, runtime->parameters[i].root)) return 0;
    value = type_decode(runtime->parameter_codecs[i], payload, payload_size);
    if (!value) return 0;
    type_free(runtime->parameter_values[i].message);
    runtime->parameter_values[i].message = value;
    runtime->parameter_values[i].present = 1;
    return 1;
}

static void *decode_input(OseContext *ctx, size_t index, const void *text, size_t text_size,
                         const void *payload, size_t payload_size) {
    Runtime *runtime = ctx->private_runtime;
    InputCodec *input = &runtime->input_codecs[index];
    const OsePortSpec *spec = &runtime->inputs[index];
    const char *root;
    void *message;
    if (!input->codec || input->catalogue_size != text_size || memcmp(input->catalogue, text, text_size)) {
        SdlCompatibilityDiagnostic *check = type_compare_catalogues(text, text_size,
            spec->catalogue, strlen(spec->catalogue), spec->root, true);
        SdlContext *codec;
        char *catalogue;
        int compatible = check && check->code == SDL_COMPAT_COMPATIBLE;
        type_compatibility_free(check);
        if (!compatible) { ose_fail(ctx, "incompatible input catalogue"); return NULL; }
        codec = type_prepare(text, text_size);
        catalogue = malloc(text_size ? text_size : 1);
        if (!codec || !catalogue) {
            type_context_free(codec); free(catalogue);
            ose_fail(ctx, "input codec initialization failed"); return NULL;
        }
        memcpy(catalogue, text, text_size);
        type_context_free(input->codec); free(input->catalogue);
        input->codec = codec; input->catalogue = catalogue; input->catalogue_size = text_size;
    }
    root = type_message_name(input->codec, payload, payload_size);
    if (!root || strcmp(root, spec->root)) { ose_fail(ctx, "wrong input message type"); return NULL; }
    message = type_decode(input->codec, payload, payload_size);
    if (!message) ose_fail(ctx, "SDL input decoding failed");
    return message;
}

static void clear_reads(Runtime *runtime) {
    while (runtime->reads) {
        InputRead *read = runtime->reads;
        runtime->reads = read->next;
        type_free(read->message); ose_inputs_record_release(&read->record); free(read);
    }
}
static int reference_record(OseContext *ctx, size_t index, OseInputRecord *record, OseInputReference *out) {
    Runtime *runtime = ctx->private_runtime;
    InputRead *read;
    uint32_t identity, expected;
    const void *payload;
    size_t payload_size;
    if (!record->present) return 1;
    if (record->received_ns > ctx->time_ns) return ose_fail(ctx, "message received in the future");
    if (!type_envelope_unpack(record->envelope, record->envelope_size, &identity, &payload, &payload_size) ||
        !type_catalogue_id(record->catalogue, record->catalogue_size, &expected) || identity != expected)
        return ose_fail(ctx, "invalid message envelope or catalogue identity");
    read = calloc(1, sizeof(*read));
    if (!read) return ose_fail(ctx, "input reference allocation failed");
    read->message = decode_input(ctx, index, record->catalogue, record->catalogue_size, payload, payload_size);
    if (!read->message) { free(read); return 0; }
    read->record = *record; memset(record, 0, sizeof(*record));
    /* The frame can now be released: only decoded data and owned strings remain. */
    read->record.envelope = NULL; read->record.envelope_size = 0;
    read->next = runtime->reads; runtime->reads = read;
    out->message = read->message; out->handle = read->record.handle; out->source = read->record.source;
    out->has_app_id = read->record.has_app_id; out->app_id = read->record.app_id;
    out->due_ns = read->record.received_ns;
    return 1;
}
static int pull_access(OseContext *ctx, size_t index) {
    Runtime *r = ctx->private_runtime;
    if (!r->active || index >= r->input_count || r->policies[index].kind != OSE_PULL)
        return ose_fail(ctx, "message access requires a Pull input and active callback");
    return 1;
}
int ose_get_input(OseContext *ctx, size_t index, int consume, const OseSelector *selector, OseInputReference *out) {
    Runtime *r = ctx->private_runtime;
    OseEvent reply = {0, NULL, 0};
    OseInputRecord record = {0};
    int ok = 0;
    if (!out || (consume != 0 && consume != 1)) return ose_fail(ctx, "invalid input operation");
    memset(out, 0, sizeof(*out));
    if (!pull_access(ctx, index)) return 0;
    out->port = r->inputs[index].name; out->event = OSE_INPUT_MESSAGE; out->elapsed = 1;
    if (ose_inputs_request(consume ? 2 : 1, out->port, selector, NULL, &reply) &&
        ose_inputs_record(reply.data + 1, reply.size - 1, &record))
        ok = reference_record(ctx, index, &record, out);
    ose_inputs_record_release(&record); ose_release(&reply);
    return ok ? 1 : ose_fail(ctx, "input access or decoding failed");
}
int ose_remove_input(OseContext *ctx, size_t index, const OseSelector *selector, int *removed) {
    Runtime *r = ctx->private_runtime;
    OseEvent reply = {0, NULL, 0};
    int ok;
    if (!removed) return ose_fail(ctx, "missing removal result");
    *removed = 0;
    if (!pull_access(ctx, index)) return 0;
    ok = ose_inputs_request(3, r->inputs[index].name, selector, NULL, &reply) &&
         reply.size == 2 && reply.data[1] <= 1;
    if (ok) *removed = reply.data[1];
    ose_release(&reply);
    return ok ? 1 : ose_fail(ctx, "input removal failed");
}
static int timer_control(OseContext *ctx, const char *port, unsigned op, const uint64_t *delay) {
    Runtime *r = ctx->private_runtime;
    OseEvent reply = {0, NULL, 0};
    size_t i;
    int ok;
    if (!port || (!r->active && (r->ready || !r->parameter_writes)))
        return ose_fail(ctx, "timer control requires initialization or an active callback");
    for (i = 0; i < r->input_count; ++i) if (!strcmp(r->inputs[i].name, port)) break;
    if (i == r->input_count || (r->policies[i].kind != OSE_PERIODIC && r->policies[i].kind != OSE_ONE_SHOT))
        return ose_fail(ctx, "timer control requires a timer input");
    ok = ose_inputs_request(op, port, NULL, delay, &reply) && reply.size == 1;
    ose_release(&reply);
    return ok ? 1 : ose_fail(ctx, "timer control failed");
}
int ose_arm(OseContext *ctx, const char *port, const uint64_t *delay) { return timer_control(ctx, port, 4, delay); }
int ose_disarm(OseContext *ctx, const char *port) { return timer_control(ctx, port, 5, NULL); }

static int activate(OseContext *ctx, const OseEvent *event, OseInputHook dispatch) {
    Runtime *r = ctx->private_runtime;
    OseActivation activation = {0};
    OseInputReference reference = {0};
    const OseInputPolicy *policy;
    size_t index;
    int ok = 0;
    if (!ose_inputs_activation(event, &activation) || ctx->tick_index == UINT64_MAX ||
        activation.index != ctx->tick_index + 1 || activation.time_ns < ctx->time_ns) goto done;
    for (index = 0; index < r->input_count; ++index)
        if (!strcmp(r->inputs[index].name, activation.port)) break;
    if (index == r->input_count) goto done;
    policy = &r->policies[index];
    if (policy->kind == OSE_PULL) goto done;
    if (activation.event == OSE_INPUT_MESSAGE && policy->kind != OSE_PUSH) goto done;
    if (activation.event == OSE_INPUT_TIMEOUT && policy->kind == OSE_PUSH && r->inputs[index].timeout <= 0) goto done;
    if (policy->kind == OSE_ONE_SHOT && activation.elapsed != 1) goto done;
    ctx->time_ns = activation.time_ns; ctx->tick_index = activation.index;
    reference.port = r->inputs[index].name; reference.event = activation.event; reference.elapsed = activation.elapsed;
    if (!reference_record(ctx, index, &activation.record, &reference)) goto done;
    reference.due_ns = activation.due_ns;
    r->allowed_outputs = activation.event == OSE_INPUT_MESSAGE ? policy->message_outputs : policy->timeout_outputs;
    r->allowed_count = activation.event == OSE_INPUT_MESSAGE ? policy->message_output_count : policy->timeout_output_count;
    r->active = 1; r->parameter_writes = 1; r->recording_bytes = 0;
    ok = dispatch(ctx, &reference);
done:
    r->active = 0; r->parameter_writes = 0; r->allowed_outputs = NULL; r->allowed_count = 0;
    ose_inputs_activation_release(&activation); clear_reads(r);
    return ok ? 1 : ose_fail(ctx, "invalid activation or input callback failed");
}

int ose_run_input_callbacks(const char *name,
    const OsePortSpec *inputs, size_t input_count, const OseInputPolicy *policies,
    const OsePortSpec *outputs, size_t output_count,
    const OseParameterSpec *parameters, size_t parameter_count,
    OseHook start, OseInputHook dispatch, OseHook stop) {
    Runtime runtime;
    OseContext ctx;
    OseEvent event;
    char *config = NULL;
    size_t index;
    int result = 1, initialized = 0;
    if (!policies || !dispatch) return 1;
    memset(&runtime, 0, sizeof(runtime));
    memset(&ctx, 0, sizeof(ctx));
    runtime.policies = policies;
    runtime.parameters = parameters; runtime.parameter_count = parameter_count;
    runtime.parameter_values = calloc(parameter_count ? parameter_count : 1, sizeof(ParameterValue));
    runtime.parameter_codecs = calloc(parameter_count ? parameter_count : 1, sizeof(SdlContext *));
    runtime.inputs = inputs; runtime.outputs = outputs;
    runtime.input_count = input_count; runtime.output_count = output_count;
    ctx.name = name; ctx.private_runtime = &runtime;
    runtime.input_codecs = calloc(input_count ? input_count : 1, sizeof(InputCodec));
    runtime.encoders = calloc(output_count ? output_count : 1, sizeof(SdlContext *));
    if (!runtime.input_codecs || !runtime.encoders || !runtime.parameter_values || !runtime.parameter_codecs) goto cleanup;
    if (!ose_read(&event)) goto cleanup;
    if (event.op != 'I' || memchr(event.data, 0, event.size)) { ose_release(&event); goto cleanup; }
    config = malloc(event.size + 1);
    if (!config) { ose_release(&event); goto cleanup; }
    memcpy(config, event.data, event.size); config[event.size] = 0;
    ose_release(&event); ctx.config_json = config;
    for (index = 0; index < output_count; ++index) {
        runtime.encoders[index] = type_prepare(outputs[index].catalogue, strlen(outputs[index].catalogue));
        if (!runtime.encoders[index]) goto cleanup;
    }
    for (index = 0; index < parameter_count; ++index) {
        runtime.parameter_codecs[index] = type_prepare(parameters[index].catalogue, strlen(parameters[index].catalogue));
        if (!runtime.parameter_codecs[index]) goto cleanup;
    }
    for (index = 0; index < parameter_count; ++index) {
        int valid;
        if (!parameters[index].readable) continue;
        if (!ose_read(&event)) goto cleanup;
        valid = parameter_update(&ctx, &event, 1);
        ose_release(&event);
        if (!valid) goto cleanup;
    }
    initialized = 1; runtime.parameter_writes = 1;
    if ((start && !start(&ctx)) || runtime.failed) goto cleanup;
    runtime.parameter_writes = 0;
    runtime.ready = 1;
    { const uint8_t capability = 2;
      if (!ose_send('R', &capability, 1)) goto cleanup; }
    while (ose_read(&event)) {
        char op = event.op;
        int valid = 1;
        if (op == 'E') valid = recording_update(&ctx, &event);
        else if (op == 'U') valid = parameter_update(&ctx, &event, 0);
        else if (op == 'T') valid = activate(&ctx, &event, dispatch);
        else if (event.size || op != 'X') valid = ose_fail(&ctx, "unexpected component event");
        ose_release(&event);
        if (!valid) goto cleanup;
        if (op == 'X') { result = 0; break; }
        if (op != 'T') continue;
        if (runtime.failed || !ose_send('F', NULL, 0)) goto cleanup;
    }
cleanup:
    runtime.active = 0; runtime.parameter_writes = 0;
    if (initialized && stop && !stop(&ctx)) result = 1;
    if (runtime.failed) result = 1;
    if (result) fprintf(stderr, "%s: component initialization, callback or IPC failed\n", name);
    clear_reads(&runtime);
    for (index = 0; runtime.input_codecs && index < input_count; ++index) {
        type_context_free(runtime.input_codecs[index].codec); free(runtime.input_codecs[index].catalogue);
    }
    for (index = 0; runtime.encoders && index < output_count; ++index)
        type_context_free(runtime.encoders[index]);
    for (index = 0; index < parameter_count; ++index) {
        if (runtime.parameter_values) type_free(runtime.parameter_values[index].message);
        if (runtime.parameter_codecs) type_context_free(runtime.parameter_codecs[index]);
    }
    free(runtime.parameter_values); free(runtime.parameter_codecs);
    for (index = 0; index < 256; ++index) {
        free(runtime.recording[index].name); free(runtime.recording[index].root);
        type_context_free(runtime.recording[index].encoder);
    }
    for (index = 0; index < runtime.measurement_count; ++index) free(runtime.measurements[index]);
    free(runtime.input_codecs); free(runtime.encoders); free(config);
    return result;
}
