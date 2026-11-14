#!/usr/bin/env bash
# From the application root: source /path/to/ose/setenv.sh
# Optional setting before sourcing: OSE_APP_ROOT.

if [ -z "${BASH_VERSION:-}" ]; then
    printf '%s\n' 'setenv.sh requires Bash: source it from a Bash terminal.' >&2
    return 1 2>/dev/null || exit 1
fi
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf '%s\n' 'Use: source /path/to/ose/setenv.sh' >&2
    exit 1
fi

export FRAMEWORK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PYTHONPYCACHEPREFIX="$FRAMEWORK_ROOT/build/pycache"
if ! PYTHON="$(sh "$FRAMEWORK_ROOT/tools/find-python.sh")"; then
    return 1
fi
export PYTHON

export SDL_ROOT="$FRAMEWORK_ROOT/sdl"
export OSE_ROOT="$FRAMEWORK_ROOT/ose"
export OSE_APP_ROOT="${OSE_APP_ROOT:-$PWD}"

if [[ ! -f "$OSE_ROOT/src/ose/cli.py" || ! -f "$SDL_ROOT/runtime/python/sdl_runtime.py" ]]; then
    printf 'Invalid framework checkout: %s\n' "$FRAMEWORK_ROOT" >&2
    return 1
fi
if [[ "$OSE_APP_ROOT" != /* ]]; then
    export OSE_APP_ROOT="$PWD/$OSE_APP_ROOT"
fi

# Include optional build directories even before they exist, so record can
# prepare them without requiring another source. Never add an empty path entry.
_ose_env_prepend_path() {
    local variable="$1" entry="$2" remaining item result
    remaining="${!variable-}"
    result="$entry"
    if [[ -n "$remaining" ]]; then
        while :; do
            item="${remaining%%:*}"
            # Remove every existing occurrence, then prepend exactly once.
            # Preserve unrelated entries and their order, including inherited
            # empty entries; an initially empty variable stays free of them.
            if [[ "$item" != "$entry" ]]; then
                result+=":$item"
            fi
            [[ "$remaining" == *:* ]] || break
            remaining="${remaining#*:}"
        done
    fi
    export "$variable=$result"
}

for _ose_env_path in \
    "$FRAMEWORK_ROOT/build/python-deps" \
    "$SDL_ROOT/build/recording-deps" \
    "$SDL_ROOT/build/filter/release" \
    "$SDL_ROOT/runtime/filter/python" \
    "$SDL_ROOT/runtime/python" \
    "$OSE_ROOT/src"; do
    _ose_env_prepend_path PYTHONPATH "$_ose_env_path"
done
_ose_env_prepend_path PATH "$SDL_ROOT/tools/recording"
unset _ose_env_path
unset -f _ose_env_prepend_path

_ose_env_cli() {
    "$PYTHON" -m ose.cli "$@"
}

_ose_env_app_cd() {
    cd -- "$OSE_APP_ROOT"
}

# Every checkout command uses the same detected PATH Python.
ose-editor() {
    "$PYTHON" "$FRAMEWORK_ROOT/ose-editor/tools/editor.py" "$@"
}

ose-ws() {
    "$PYTHON" "$FRAMEWORK_ROOT/ose-ws/tools/console.py" "$@"
}

# Application commands resolve credentials afresh on every invocation.
if [[ ${_ose_env_parameter_token_auto+x} &&
        "${OSE_PARAMETER_TOKEN-}" == "${_ose_env_parameter_token_auto-}" ]]; then
    unset OSE_PARAMETER_TOKEN
fi
unset _ose_env_parameter_token_auto

alias ose='_ose_env_cli'
alias app-cd='_ose_env_app_cd'
alias app-build='_ose_env_cli app build'
alias app-run='_ose_env_cli app run'

if [[ $- == *i* ]]; then
    eval "$(_ose_env_cli completion bash)"
    printf 'SDL/OSE ready. Application: %s\nType ose app env for paths and ose app --help for commands.\n' "$OSE_APP_ROOT"
fi
