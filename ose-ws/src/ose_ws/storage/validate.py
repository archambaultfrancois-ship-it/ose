"""Read-only artifact validation worker; the parent owns its deadline and FD."""
import json
import sys


def main():
    try:
        from sdl_recording import RecordingReader
        count = RecordingReader(sys.argv[1]).validate()
        result = {"integrity": "valid", "message_count": count}
    except ImportError:
        result = {"integrity": "unknown", "error": "Install the compatible SDL recording runtime and optional Python dependencies."}
    except (ValueError, OSError) as error:
        result = {"integrity": "invalid", "error": str(error)[:500]}
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
