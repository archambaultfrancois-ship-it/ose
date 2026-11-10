#ifndef OSE_COMPONENT_H
#define OSE_COMPONENT_H
#include <stddef.h>
#include <stdint.h>
#if defined(__GNUC__) || defined(__clang__)
#define OSE_PRINTF_FORMAT(format_index, first_argument) \
    __attribute__((__format__(__printf__, format_index, first_argument)))
#else
#define OSE_PRINTF_FORMAT(format_index, first_argument)
#endif
/* Returned frames own their allocation; call ose_release after use. */
typedef struct { char op; uint8_t *data; size_t size; } OseEvent;
int ose_read(OseEvent *event);
void ose_release(OseEvent *event);
int ose_send(char op, const void *data, size_t size);
typedef enum { OSE_LOG_DEBUG=10, OSE_LOG_INFO=20, OSE_LOG_WARNING=30,
               OSE_LOG_ERROR=40, OSE_LOG_CRITICAL=50, OSE_LOG_FATAL=60 } OseLogLevel;
int ose_record_bytes(const char *name, const void *payload, size_t size);
int ose_measurement_declare(const char *name);
int ose_measurement_value(const char *name, double value);
int ose_declare_recording(const char *name, const char *root, const char *catalogue, const char *description);
int ose_set_parameters(const char *name, const void *payload, size_t size);
/* Parse a U shared-parameter update. Borrowed fields last until event release.
 * Catalogue is not NUL terminated. Message activations use ose_inputs.h. */
int ose_input(const OseEvent *event, const uint8_t **port, size_t *port_size,
                const uint8_t **catalogue, size_t *catalogue_size,
                const uint8_t **payload, size_t *payload_size);
#endif
