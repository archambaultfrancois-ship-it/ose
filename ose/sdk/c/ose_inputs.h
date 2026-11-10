#ifndef OSE_INPUTS_H
#define OSE_INPUTS_H
#include "ose_component.h"

typedef enum { OSE_INPUT_MESSAGE = 1, OSE_INPUT_TIMEOUT = 2 } OseInputEvent;
typedef struct {
    const char *port;
    OseInputEvent event;
    const void *message;
    const char *handle, *source;
    int has_app_id;
    uint64_t app_id, elapsed, due_ns;
} OseInputReference;
typedef struct {
    int has_app_id;
    uint64_t app_id;
    const char *handle, *source;
} OseSelector;
/* Wire records own their strings; envelope bytes borrow the enclosing frame.
 * Zero-initialize before parsing and release after use, including parse failure. */
typedef struct {
    int present, has_app_id;
    char *handle, *source, *catalogue;
    uint64_t app_id, received_ns;
    const uint8_t *envelope;
    size_t envelope_size, catalogue_size;
} OseInputRecord;
typedef struct {
    uint64_t time_ns, index, due_ns, elapsed;
    OseInputEvent event;
    char *port;
    OseInputRecord record;
} OseActivation;
int ose_inputs_record(const void *, size_t, OseInputRecord *);
void ose_inputs_record_release(OseInputRecord *);
int ose_inputs_activation(const OseEvent *, OseActivation *);
void ose_inputs_activation_release(OseActivation *);
int ose_inputs_publish(const char *, const void *, size_t, const uint64_t *app_id);
/* Zero-initialize reply; it is owned by the caller even on a failed request. Operations:
 * 1 get, 2 pull, 3 remove, 4 arm, 5 disarm. NULL selector selects oldest. */
int ose_inputs_request(unsigned operation, const char *, const OseSelector *,
                       const uint64_t *delay_ns, OseEvent *reply);
#endif
