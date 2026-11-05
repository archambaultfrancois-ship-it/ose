#!/bin/sh
# Bootstrap fallback: a missing Python must not prevent resource installation.
# Rerun ose-install after providing Python to install isolated dependencies.
set -eu
release=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
prefix=${HOME:?}/.local/opt/ose
bin_dir=$HOME/.local/bin
profile=sdk
languages=all
features=all
preview=no
while [ "$#" -gt 0 ]; do
    case "$1" in
        install|--offline) shift ;;
        --prefix|--bin-dir|--profile|--languages|--features|--packages-dir)
            option=$1
            [ "$#" -ge 2 ] || { printf '%s\n' "Missing value: $option" >&2; exit 1; }
            case "$option" in
                --prefix) prefix=$2 ;;
                --bin-dir) bin_dir=$2 ;;
                --profile) profile=$2 ;;
                --languages) languages=$2 ;;
                --features) features=$2 ;;
                --packages-dir) printf '%s\n' 'WARNING: Python is needed to install wheels from --packages-dir.' >&2 ;;
            esac
            shift 2 ;;
        --check-only|--dry-run) preview=yes; shift ;;
        -h|--help)
            printf '%s\n' 'Usage: sh ose-install [install] [--prefix PATH] [--bin-dir PATH]' \
                '  [--profile sdk|runtime] [--languages all|LIST] [--features all|LIST]' \
                '  [--offline] [--packages-dir PATH] [--check-only] [--dry-run]'
            exit 0 ;;
        *) printf '%s\n' "Unknown option: $1" >&2; exit 1 ;;
    esac
done
case "$profile" in sdk|runtime) ;; *) printf '%s\n' 'Invalid profile' >&2; exit 1 ;; esac
# The fallback keeps the complete archive available, including resources whose
# selection and integrity require Python; it does not claim capabilities ready.
if [ "$profile:$languages:$features" != sdk:all:all ]; then
    printf '%s\n' 'WARNING: Resource selection is deferred until Python is available; the full payload will be retained.' >&2
fi
printf '%s\n' 'WARNING: Python dependency checks are deferred until Python is available.' >&2
[ "$preview" = no ] || { printf '%s\n' 'No installation changes made.'; exit 0; }
if command -v sha256sum >/dev/null 2>&1; then
    (cd "$release" && sha256sum -c SHA256SUMS >/dev/null) || { printf '%s\n' 'Corrupt release resources' >&2; exit 1; }
else
    printf '%s\n' 'WARNING: Checksum verification requires sha256sum or Python; it is deferred.' >&2
fi
[ ! -L "$prefix" ] || { printf '%s\n' 'Destination cannot be a symlink' >&2; exit 1; }
if [ -d "$prefix" ] && [ -n "$(ls -A "$prefix")" ] && [ ! -f "$prefix/install-report.json" ]; then
    printf '%s\n' 'Destination is not an OSE installation or an empty directory' >&2
    exit 1
fi
mkdir -p "$prefix/bin"
prefix=$(CDPATH= cd -- "$prefix" && pwd -P)
case "$prefix/" in "$release/"*) printf '%s\n' 'Destination overlaps the release' >&2; exit 1 ;; esac
case "$release/" in "$prefix/"*) printf '%s\n' 'Destination overlaps the release' >&2; exit 1 ;; esac
cp -R "$release/framework" "$prefix/"
cp -R "$release/packages" "$prefix/"
if [ -d "$release/cargo-vendor" ]; then cp -R "$release/cargo-vendor" "$prefix/"; fi
cp "$release/launch.py" "$prefix/launch.py"
cp "$release/manifest.json" "$prefix/release-manifest.json"
printf '%s\n' '{"format":1,"profile":"sdk","languages":["python","c","rust","java","ada"],"features":["web","recording","filter","sync"],"python_dependencies":false,"warnings":["Python unavailable; rerun the installer after installing Python."]}' > "$prefix/install-report.json"
for name in ose ose-ws sdlc sdl-recording; do
    case "$name" in ose) action=ose ;; ose-ws) action=web ;; sdlc) action=sdlc ;; *) action=recording ;; esac
    # Resolve links when invoked from the user's command directory.
    {
        printf '#!%s\n' "$(command -v sh)"
        printf '%s\n' 'set -eu' 'script=$0' \
            'while [ -L "$script" ]; do' \
            '  parent=$(CDPATH= cd -- "$(dirname -- "$script")" && pwd -P)' \
            '  script=$(readlink "$script")' \
            '  case "$script" in /*) ;; *) script=$parent/$script ;; esac' 'done' \
            'root=$(CDPATH= cd -- "$(dirname -- "$script")/.." && pwd -P)'
        printf 'exec "${OSE_INSTALL_PYTHON:-python3}" "$root/launch.py" %s "$@"\n' "$action"
    } > "$prefix/bin/$name"
    chmod +x "$prefix/bin/$name"
done
if mkdir -p "$bin_dir"; then
    for name in ose ose-ws sdlc sdl-recording; do
        if [ ! -e "$bin_dir/$name" ] && [ ! -L "$bin_dir/$name" ]; then
            ln -s "$prefix/bin/$name" "$bin_dir/$name" || printf '%s\n' "WARNING: Cannot link $name" >&2
        fi
    done
else
    printf '%s\n' "WARNING: Commands remain in $prefix/bin" >&2
fi
printf '%s\n' "Resources installed in $prefix. Rerun the installer after providing Python."
