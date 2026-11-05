#!/bin/sh
# OSE self-extracting release. All arguments are forwarded to ose-install.
set -eu
fail() {
    printf 'OSE extraction failed: %s\n' "$1" >&2
    exit 1
}
temporary_base=${TMPDIR:-/tmp}
case "$temporary_base" in /*) ;; *) temporary_base=$PWD/$temporary_base ;; esac
work=$(mktemp -d "$temporary_base/ose-extract.XXXXXXXX") || fail 'cannot create temporary directory'
trap 'rm -rf -- "$work"' 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
payload=$work/payload.tar.gz
tail -n +@PAYLOAD_LINE@ "$0" > "$payload" || fail 'cannot read embedded archive'
expected=@PAYLOAD_SHA256@
actual=
if command -v sha256sum >/dev/null 2>&1; then
    actual=$(sha256sum "$payload") || fail 'cannot checksum embedded archive'
    actual=${actual%% *}
elif command -v shasum >/dev/null 2>&1; then
    actual=$(shasum -a 256 "$payload") || fail 'cannot checksum embedded archive'
    actual=${actual%% *}
elif command -v openssl >/dev/null 2>&1; then
    actual=$(openssl dgst -sha256 "$payload") || fail 'cannot checksum embedded archive'
    actual=${actual##* }
else
    for interpreter in "${OSE_INSTALL_PYTHON:-python3}" python; do
        if command -v "$interpreter" >/dev/null 2>&1; then
            if actual=$("$interpreter" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$payload"); then
                break
            fi
        fi
    done
fi
if [ -n "$actual" ]; then
    [ "$actual" = "$expected" ] || fail 'embedded archive checksum mismatch'
else
    printf '%s\n' 'WARNING: SHA-256 tools are unavailable; archive checksums will be checked by the installer when possible.' >&2
fi
tar -xzf "$payload" -C "$work" || fail 'cannot unpack embedded archive'
[ -f "$work/ose-release/ose-install" ] || fail 'embedded installer is missing'
status=0
sh "$work/ose-release/ose-install" "$@" || status=$?
exit "$status"
__OSE_PAYLOAD__
