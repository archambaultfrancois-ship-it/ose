#!/bin/sh
# Prefer python3, then accept a versioned Python from PATH (for example python3.12).
compatible() {
    "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

emit() {
    candidate=$1
    case "$candidate" in */*) ;; *) candidate=./$candidate ;; esac
    directory=$(CDPATH= cd -- "${candidate%/*}" && pwd -P) || return 1
    printf '%s/%s\n' "$directory" "${candidate##*/}"
}

default=$(command -v python3 2>/dev/null) || default=
if [ -n "$default" ] && compatible "$default"; then
    emit "$default"
    exit $?
fi

remaining=${PATH-}
while :; do
    directory=${remaining%%:*}
    [ -n "$directory" ] || directory=.
    for candidate in "$directory"/python3.[0-9]*; do
        suffix=${candidate##*/python3.}
        case "$suffix" in ''|*[!0-9]*) continue ;; esac
        if [ -f "$candidate" ] && [ -x "$candidate" ] && compatible "$candidate"; then
            emit "$candidate"
            exit $?
        fi
    done
    case "$remaining" in *:*) remaining=${remaining#*:} ;; *) break ;; esac
done
printf '%s\n' 'Python 3.10+ is required. Install python3 or a compatible python3.x command in PATH.' >&2
exit 1
