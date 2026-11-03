#ifndef OSE_COMPONENT_INTERNAL_H
#define OSE_COMPONENT_INTERNAL_H

/* Private implementation shared by the C callback runtime and Ada binding.
 * Component handlers use ose_logf from ose_callbacks.h. */
#include "ose_component.h"
#include <stdarg.h>

#if defined(__GNUC__) || defined(__clang__)
#define OSE_INTERNAL __attribute__((visibility("hidden")))
#else
#define OSE_INTERNAL
#endif

OSE_INTERNAL int ose_internal_log(OseLogLevel level, const char *logger, const char *message);
OSE_INTERNAL int ose_internal_vlogf(OseLogLevel level, const char *logger, const char *format, va_list arguments)
    OSE_PRINTF_FORMAT(3, 0);

#endif
