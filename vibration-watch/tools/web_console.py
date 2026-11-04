#!/usr/bin/env python3
"""Start the optional web console for the running Vibration Watch application."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import venv


ROOT = Path(__file__).resolve().parents[1]


def setup(framework):
    environment = ROOT / ".venv"
    python = environment / "bin/python"
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    install_environment = dict(os.environ)
    # Do not let prepared SDL dependency directories satisfy the venv's install.
    install_environment.pop("PYTHONPATH", None)
    subprocess.run([str(python), "-m", "pip", "install", str(framework / "ose"),
                    str(framework / "ose-ws") + "[recording]"], check=True, env=install_environment)
    print("Web console dependencies are ready. Run ose-ws after source ./setenv.sh.")
    return 0


def launch(args, framework):
    # Use the selected checkout, including its SDL and prepared recording services.
    sys.path[:0] = [str(framework / "ose-ws/src"), str(framework / "ose/src")]
    from ose.app import Application
    from ose_ws import cli
    from ose_ws.config import read

    config = Path(args.config).resolve() if args.config else ROOT / "ose-ws.json"
    endpoint = str(Path(args.endpoint).resolve()) if args.endpoint else None
    if args.command == "serve" and not args.config and not config.exists():
        cli.initialize(config)
        settings = json.loads(config.read_text())
        settings["application"]["name"] = "Vibration Watch"
        config.write_text(json.dumps(settings, indent=2) + "\n")

    context = Application(SimpleNamespace(root=str(ROOT), app_sdl_root=str(framework / "sdl")))
    arguments = [args.command, "--config", str(config)]
    for key, value in (("endpoint", endpoint), ("host", args.host), ("port", args.port)):
        if value is not None:
            arguments.extend(["--" + key, str(value)])
    with context.activate():
        os.environ.setdefault("OSE_PROCESS_PREFIX", "vw")
        if args.command != "init":
            settings = read(config)
            # Use the configured application's current plan, then its deployment
            # defaults. Explicit environment credentials take precedence.
            credentials = Application(SimpleNamespace(root=settings["application"]["root"],
                                                       app_sdl_root=str(framework / "sdl")))
            credentials.parameter_token(SimpleNamespace(client=settings["parameters"]["client"],
                                                        token_env=settings["parameters"]["token_env"]))
        return cli.main(arguments)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, prog="ose-ws")
    parser.add_argument("command", nargs="?", choices=("setup", "init", "check", "serve"), default="serve")
    parser.add_argument("--config", help="console configuration (default: the application's ose-ws.json)")
    parser.add_argument("--endpoint", help="override OSE endpoint file, relative to the calling directory")
    parser.add_argument("--host", help="HTTP bind address; network access requires proxy authentication")
    parser.add_argument("--port", type=int, help="HTTP port (default: 8080)")
    args = parser.parse_args(argv)
    framework = Path(os.environ.get("FRAMEWORK_ROOT", ROOT.parent)).resolve()
    try:
        if not (framework / "ose-ws/src/ose_ws/cli.py").is_file():
            raise ValueError("The selected framework has no web console. Use the parent SDL/OSE checkout or set FRAMEWORK_ROOT.")
        return setup(framework) if args.command == "setup" else launch(args, framework)
    except ImportError as error:
        print(f"Web console dependency unavailable: {error.name}. Run ose-ws setup first.", file=sys.stderr)
        return 2
    except (ValueError, TypeError, KeyError) as error:
        print(f"Invalid web console configuration: {error}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Web console initialization failed: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
