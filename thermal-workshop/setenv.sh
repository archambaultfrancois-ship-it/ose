#!/usr/bin/env bash
# Source this file from Bash; every tool uses the parent framework checkout.
if [ -z "${BASH_VERSION:-}" ]; then
    printf '%s\n' 'Use Bash to source this environment.' >&2
    return 1 2>/dev/null || exit 1
fi
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf '%s\n' 'Use: source ./setenv.sh' >&2
    exit 1
fi
_thermal_workshop_environment() {
    local application_root
    application_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return
    export OSE_APP_ROOT="$application_root"
    export OSE_PROCESS_PREFIX=tw
    source "$application_root/../setenv.sh"
}
_thermal_workshop_environment
unset -f _thermal_workshop_environment
