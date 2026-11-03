#!/usr/bin/env bash
# Source from Bash: source /path/to/app/setenv.sh

if [ -z "${BASH_VERSION:-}" ]; then
    printf '%s\n' 'setenv.sh requires Bash: source it from a Bash terminal.' >&2
    return 1 2>/dev/null || exit 1
fi
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf '%s\n' 'Use: source /path/to/app/setenv.sh' >&2
    exit 1
fi

_app_source_environment() {
    local application_root framework_root
    application_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return
    framework_root="$(cd -- "$application_root/.." && pwd -P)" || return
    if [[ ! -r "$framework_root/setenv.sh" ]]; then
        printf 'Framework environment not found: %s/setenv.sh\nUse vibration-watch inside the SDL/OSE repository.\n' \
            "$framework_root" >&2
        return 1
    fi
    export OSE_APP_ROOT="$application_root"
    source "$framework_root/setenv.sh"
}

if _app_source_environment; then
    unset -f _app_source_environment
else
    unset -f _app_source_environment
    return 1
fi

# The web console uses its optional environment without changing app-run's Python.
ose-ws() {
    local web_python
    if [[ -n "${OSE_WS_PYTHON:-}" ]]; then
        web_python="$OSE_WS_PYTHON"
    elif [[ -x "$OSE_APP_ROOT/.venv/bin/python" ]]; then
        web_python="$OSE_APP_ROOT/.venv/bin/python"
    else
        web_python="$OSE_PYTHON"
    fi
    "$web_python" "$OSE_APP_ROOT/tools/web_console.py" "$@"
}
