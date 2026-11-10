#ifndef OSE_CALLBACKS_H
#define OSE_CALLBACKS_H
#include <stddef.h>
#include <stdint.h>
#include "ose_component.h"
#include "ose_inputs.h"

/* config_json is the received INIT document, valid through on_stop.
 * user_data belongs to the user; on_stop can release it. */
typedef struct OseContext {
    const char *name;
    const char *config_json;
    void *user_data;
    void *private_runtime;
    uint64_t time_ns, tick_index;
} OseContext;

typedef struct {
    const char *name, *root, *catalogue;
    double timeout; /* Inactivity timeout in seconds; zero disables expiration. */
} OsePortSpec;
typedef struct {
    const char *name, *root, *catalogue;
    int readable, writable;
} OseParameterSpec;
typedef int (*OseHook)(OseContext *);

const void *ose_parameter_value(const OseContext *, size_t index);
int ose_set_parameter_value(OseContext *, size_t index, const void *value);
int ose_publish_value(OseContext *, size_t index, const void *value);
/* CH/2 only. References and decoded values borrow callback-scoped storage.
 * get/pull return 1 on success, including absence (message == NULL), 0 on error.
 * remove reports successful absence through *removed == 0. Errors fail the
 * callback even when its handler subsequently returns nonzero. */
typedef enum { OSE_PUSH, OSE_PULL, OSE_PERIODIC, OSE_ONE_SHOT } OseInputKind;
typedef struct {
    OseInputKind kind;
    const char *const *message_outputs;
    size_t message_output_count;
    const char *const *timeout_outputs;
    size_t timeout_output_count;
} OseInputPolicy;
typedef int (*OseInputHook)(OseContext *, const OseInputReference *);
int ose_run_input_callbacks(const char *name,
    const OsePortSpec *inputs, size_t input_count, const OseInputPolicy *,
    const OsePortSpec *outputs, size_t output_count,
    const OseParameterSpec *parameters, size_t parameter_count,
    OseHook start, OseInputHook dispatch, OseHook stop);
int ose_get_input(OseContext *, size_t index, int consume, const OseSelector *, OseInputReference *);
int ose_remove_input(OseContext *, size_t index, const OseSelector *, int *removed);
int ose_publish_value_with_id(OseContext *, size_t index, const void *, uint64_t id);
/* NULL delay uses the model delay; an explicit delay is positive nanoseconds. */
int ose_arm(OseContext *, const char *port, const uint64_t *delay_ns);
int ose_disarm(OseContext *, const char *port);
/* Report a user error and fail initialization or the active input callback. */
int ose_fail(OseContext *, const char *message);
/* Printf-style log deposit. Use "%s", message for preformatted text.
 * Messages over 8192 bytes are truncated at UTF-8 boundaries with a marker.
 * Truncation succeeds; formatting/deposit errors fail the current callback. */
int ose_logf(OseContext *, OseLogLevel level, const char *logger, const char *format, ...)
    OSE_PRINTF_FORMAT(4, 5);
double ose_now(const OseContext *);
uint64_t ose_time_ns(const OseContext *);
uint64_t ose_tick_index(const OseContext *);
/* Declare during on_start, emit finite fl64 values during input callbacks. */
int ose_declare_measurement(OseContext *, const char *name);
int ose_measure(OseContext *, const char *name, double value);
typedef enum {
    OSE_RECORD_ERROR = -1, OSE_RECORD_DISABLED = 0,
    OSE_RECORD_QUEUED = 1, OSE_RECORD_DROPPED = 2
} OseRecordResult;
/* Context-owned hash registry; declaration only during on_start. */
int ose_declare_recording_value(OseContext *, const char *name, const char *root,
                               const char *catalogue, const char *description);
int ose_recording_enabled(OseContext *, const char *name);
OseRecordResult ose_record_value(OseContext *, const char *name, const char *root, const void *value);
#endif
