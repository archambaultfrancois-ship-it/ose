"""Configuration is explicit, validated and independent of application builds."""
import copy
import ipaddress
import os
from pathlib import Path
from ose_ws.util import loads


DEFAULTS = {
    "listen": {"host": "127.0.0.1", "port": 8080},
    "application": {"root": ".", "endpoint": "build/endpoint.json", "name": "OSE application",
                    "distributed": False, "insecure": False},
    "state_dir": ".ose-ws",
    "collection": {"interval_ms": 500, "diagnostic_window_s": 5},
    "measurements": {"retention_s": 900, "max_series": 128, "max_total_samples": 250000,
                     "memory_budget_mib": 32, "preconfigured": []},
    "recording": {"max_active": 4, "per_user": 2, "min_free_disk_mib": 1024},
    "flows": {"max_sources": 4, "per_session": 2, "lease_s": 60, "max_payload_bytes": 1024 * 1024},
    "parameters": {"client": "operator", "token_env": "OSE_PARAMETER_TOKEN"},
    "auth": {"mode": "local_token", "token_file": ".ose-ws/access.token", "trusted_proxies": [],
             "users": {}, "max_sessions": 20},
    "limits": {"max_drafts": 100, "max_operations": 10000, "max_unresolved_operations": 1000, "audit_days": 30,
               "audit_events": 100000, "shutdown_s": 60, "validation_s": 300},
}


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def merge(base, values):
    for key, value in values.items():
        if key not in base:
            raise ValueError(f"Unknown configuration option: {key}")
        if isinstance(base[key], dict) and key != "users":
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object for {key}")
            merge(base[key], value)
        else:
            base[key] = value


def read(path, overrides=None):
    path = Path(path).resolve()
    values = loads(path.read_bytes())
    if not isinstance(values, dict):
        raise ValueError("Configuration must be a JSON object.")
    config = copy.deepcopy(DEFAULTS)
    # These optional fields deliberately have no effective default.
    config["application"].update({"host": None, "port": None, "tls_ca": None, "tls_cert": None, "tls_key": None})
    config["parameters"]["presentation"] = None
    merge(config, values)
    for env, target in (("OSE_WS_STATE_DIR", ("state_dir",)), ("OSE_WS_HOST", ("listen", "host"))):
        if env in os.environ:
            if len(target) == 1:
                config[target[0]] = os.environ[env]
            else:
                config[target[0]][target[1]] = os.environ[env]
    if "OSE_WS_PORT" in os.environ:
        config["listen"]["port"] = int(os.environ["OSE_WS_PORT"])
    merge(config, overrides or {})
    base = path.parent
    app = config["application"]
    app["root"] = str((base / app["root"]).resolve())
    if app["endpoint"]:
        app["endpoint"] = str((Path(app["root"]) / app["endpoint"]).resolve())
    for key in ("tls_ca", "tls_cert", "tls_key"):
        if app[key]:
            app[key] = str((base / app[key]).resolve())
    config["state_dir"] = str((base / config["state_dir"]).resolve())
    config["auth"]["token_file"] = str((base / config["auth"]["token_file"]).resolve())
    if config["parameters"]["presentation"]:
        config["parameters"]["presentation"] = str((base / config["parameters"]["presentation"]).resolve())
    validate(config)
    return config


def validate(config):
    def number(section, key, low, high):
        value = config[section][key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{section}.{key} must be an integer between {low} and {high}.")
    for section, key, low, high in (
        ("listen", "port", 1, 65535), ("collection", "interval_ms", 100, 60000),
        ("collection", "diagnostic_window_s", 1, 3600), ("measurements", "retention_s", 1, 86400),
        ("measurements", "max_series", 1, 4096), ("measurements", "max_total_samples", 1, 10000000),
        ("measurements", "memory_budget_mib", 1, 1024), ("recording", "max_active", 1, 32),
        ("recording", "per_user", 1, 32), ("recording", "min_free_disk_mib", 0, 1048576),
        ("flows", "max_sources", 1, 8), ("flows", "per_session", 1, 4),
        ("flows", "lease_s", 5, 120), ("flows", "max_payload_bytes", 1024, 1024 * 1024),
        ("auth", "max_sessions", 1, 200), ("limits", "max_drafts", 1, 10000),
        ("limits", "max_operations", 1, 1000000), ("limits", "audit_days", 1, 3650),
        ("limits", "max_unresolved_operations", 1, 1000000),
        ("limits", "audit_events", 1, 1000000), ("limits", "shutdown_s", 1, 3600),
        ("limits", "validation_s", 1, 3600)):
        number(section, key, low, high)
    auth, app = config["auth"], config["application"]
    if auth["mode"] not in ("local_token", "proxy"):
        raise ValueError("auth.mode must be local_token or proxy.")
    if auth["mode"] == "local_token" and not loopback(config["listen"]["host"]):
        raise ValueError("Network access requires proxy authentication and HTTPS.")
    if auth["mode"] == "proxy" and (not auth["trusted_proxies"] or not auth["users"]):
        raise ValueError("Proxy mode requires explicit trusted_proxies and users.")
    for address in auth["trusted_proxies"]:
        ipaddress.ip_address(address)
    for user, policy in auth["users"].items():
        if not isinstance(user, str) or not isinstance(policy, dict) or not isinstance(policy.get("permissions"), dict):
            raise ValueError("Each proxy user requires a permissions object.")
        for patterns in policy["permissions"].values():
            if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
                raise ValueError("Permission scopes must be lists of strings.")
    if app["host"] is not None or app["port"] is not None:
        if app["endpoint"] or not app["host"] or type(app["port"]) is not int or not 1 <= app["port"] <= 65535:
            raise ValueError("Choose an endpoint file or a complete host/port pair.")
    if not app["endpoint"] and not app["host"]:
        raise ValueError("An OSE endpoint or host/port pair is required.")
    if app["insecure"] and not app["distributed"]:
        raise ValueError("insecure requires distributed mode.")
    if app["distributed"] and not app["insecure"] and not all(app[key] for key in ("tls_ca", "tls_cert", "tls_key")):
        raise ValueError("Distributed TLS requires tls_ca, tls_cert and tls_key.")
    if any(app[key] for key in ("tls_ca", "tls_cert", "tls_key")) and (not app["distributed"] or app["insecure"]):
        raise ValueError("TLS options require secure distributed mode.")
    patterns = config["measurements"]["preconfigured"]
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ValueError("measurements.preconfigured must be a list of logical names or globs.")
