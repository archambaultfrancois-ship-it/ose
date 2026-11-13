"""Terminal-independent diagnostics shared by the CLI and web console."""
from collections import deque
from datetime import datetime, timezone
import time

COUNTERS = ("accepted", "refused", "dropped_oldest", "failed", "retries", "replaced")


class Observer:
    def __init__(self, window=5):
        self.window = window
        self.histories = {}
        self.epoch = None

    def sample(self, snapshot, now=None, error=None):
        now = time.monotonic() if now is None else now
        if snapshot.get("epoch") != self.epoch:
            self.histories.clear()
            self.epoch = snapshot.get("epoch")
        observation = snapshot.get("observation", {})
        inventory = observation.get("components", {})
        if observation.get("version") != 1:
            raise ValueError("live diagnostics require an updated OSE broker/coordinator")
        rows = []
        for name, desc in sorted(inventory.items()):
            raw = snapshot.get("components", {}).get(name, {})
            metrics = raw.get("metrics", raw)
            queues = []
            for port, values in metrics.get("inputs", {}).items():
                sub = next((sub for sub in observation["subscriptions"].values()
                            if sub["to"] == f"{name}.{port}"), {})
                queues.append({"name": port, "scope": "input", "policy": sub.get("policy", {}), **values})
            for identity, sub in observation["subscriptions"].items():
                if sub["from"].split(".")[0] != name:
                    continue
                values = (metrics.get("direct", {}) if sub.get("routing") == "direct"
                          else snapshot.get("subscriptions", {})).get(identity)
                if values is not None:
                    queues.append({"name": identity, "scope": "delivery", "policy": sub["policy"], **values})
            counters = {"in": sum(queue.get("accepted", 0) for queue in queues if queue["scope"] == "input"),
                        "out": metrics.get("publications", 0),
                        "ticks": metrics.get("completed_ticks", metrics.get("ticks", 0))}
            for port, values in metrics.get('timers', {}).items():
                counters[f'timer:{port}:coalesced'] = values['coalesced']
            for key in ("invalid", "network_refused", "unavailable", "refused", "delivery_refused"):
                counters[key] = metrics.get(key, 0)
            for queue in queues:
                for key in COUNTERS:
                    counters[f"{queue['scope']}:{queue['name']}:{key}"] = queue.get(key, 0)
            generation, history = self.histories.get(name, (None, deque(maxlen=1024)))
            if generation != desc.get("generation"):
                history = deque(maxlen=1024)
            self.histories[name] = desc.get("generation"), history
            fresh = desc.get("report_age_ms") is not None and desc["report_age_ms"] <= 3000
            available = desc.get("connected") and fresh and not error
            if not available:
                history.clear()
            elif history and any(value < history[-1][1].get(key, 0) for key, value in counters.items()):
                history.clear()
            if available:
                history.append((now, counters))
                while len(history) > 2 and history[1][0] <= now - self.window:
                    history.popleft()
            elapsed = now - history[0][0] if len(history) > 1 else 0
            delta = {key: max(0, value - history[0][1].get(key, value)) if elapsed else 0
                     for key, value in counters.items()}
            alerts = []
            def alert(severity, message):
                alerts.append({"severity": severity, "message": message})
            for queue in queues:
                prefix = f"{queue['scope']}:{queue['name']}:"
                queue["recent"] = {key: delta[prefix + key] for key in COUNTERS}
                label = f"{queue['scope']} {queue['name']}"
                losses = queue["recent"]["dropped_oldest"] + queue["recent"]["failed"]
                if losses:
                    alert("error", f"{label}: {losses} dropped/failed")
                if queue["recent"]["refused"]:
                    alert("warning", f"{label}: {queue['recent']['refused']} admission refusals (attempts)")
                if queue["recent"]["retries"]:
                    alert("warning", f"{label}: {queue['recent']['retries']} retries")
                policy = queue["policy"]
                if policy.get("buffer") == "fifo" and queue.get("depth", 0) >= .8 * policy.get("capacity", float("inf")):
                    alert("warning", f"{label}: queue {queue['depth']}/{policy['capacity']}")
            for key in ("invalid", "network_refused", "unavailable", "refused", "delivery_refused"):
                if delta[key]:
                    alert("warning" if key in ("refused", "delivery_refused") else "error", f"{key}: +{delta[key]}")
            p95 = metrics.get("tick_p95_ms") if fresh and not error else None
            busy = metrics.get("busy_ms") if available else None
            waiting = metrics.get("waiting_inputs", []) if available else []
            control = snapshot.get('component_states', {}).get(name, {})
            publication = metrics.get('publication_wait', {}) if available else {}
            if error:
                state, severity = "UNAVAILABLE", "error"
            elif not desc.get("connected"):
                state, severity = ("UNAVAILABLE", "error") if snapshot.get("enabled") else ("STARTING", "idle")
            elif not desc.get('ready') or desc.get('report_age_ms') is None:
                state, severity = 'STARTING', 'idle'
            elif not fresh:
                state, severity = "STALE", "error"
            elif control.get('pending'):
                state, severity = ('ENABLING' if control.get('enabled') else 'DISABLING'), 'warning'
            elif control.get('active') is False:
                state, severity = 'DISABLED', 'idle'
            elif not desc.get("ready") or not metrics.get("running"):
                state, severity = "STARTING", "idle"
            elif any(item["severity"] == "error" for item in alerts):
                state, severity = "LOSS/ERROR", "error"
            elif alerts:
                state, severity = "ATTENTION", "warning"
            elif publication.get('current_ms') is not None:
                state, severity = 'WAIT PUB', 'waiting'
            elif waiting:
                state, severity = "WAIT INPUT", "waiting"
            elif delta["ticks"] or metrics.get("busy_ms") is not None:
                state, severity = "ACTIVE", "ok"
            else:
                state, severity = "IDLE", "idle"
            rows.append({"name": name, "state": state, "severity": severity,
                         "process": metrics.get("process"),
                         "rates": {key: delta[key] / elapsed if elapsed and available else None
                                   for key in ("in", "out", "ticks")},
                         "rate_span_seconds": elapsed,
                         "tick_p95_ms": p95, "busy_ms": busy,
                         "report_age_ms": desc.get("report_age_ms"), "waiting_inputs": waiting,
                         "generation": desc.get("generation"), "inputs": desc.get("inputs", {}),
                         "supervision": control,
                         "timers": {port: dict(values, recent_coalesced=delta[f'timer:{port}:coalesced'] if elapsed else None)
                                    for port, values in metrics.get('timers', {}).items()} if available else {},
                         "input_wait": metrics.get('input_wait', {}) if available else {},
                         "publication_wait": publication,
                         "resources": metrics.get('resources', {}) if available else {},
                         "queues": queues, "alerts": alerts,
                         "counters": {key: value for key, value in counters.items() if ":" not in key}})
        return {"timestamp": datetime.now(timezone.utc).isoformat(), "epoch": self.epoch,
                "process": snapshot.get("process"),
                "enabled": snapshot.get("enabled", False), "window_seconds": self.window,
                "error": error, "components": rows,
                "broker_errors": {"udp_errors": snapshot.get("udp_errors", 0),
                                  "errors": snapshot.get("errors", [])}}
