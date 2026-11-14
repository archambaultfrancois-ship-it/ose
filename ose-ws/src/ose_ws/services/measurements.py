"""Logical watches survive generation changes; bounded segments never join them."""
from collections import deque
import fnmatch
import math
import sys
import time
import uuid
from ose_ws.util import opaque, page, require, timestamp


class MeasurementStore:
    def __init__(self, config):
        self.config = config["measurements"]
        self.max_watches = config["auth"]["max_sessions"] * 4
        self.catalogue = {}
        self.watches = {}
        self.segments = {}
        self.order = deque()
        self.bytes = 0
        self.sequence = 0
        self.last_wall = None
        self.truncated = False
        self.allocated_bytes = 0
        self.last_simulation_snapshot = None

    def active(self):
        now = time.monotonic()
        self.watches = {key: value for key, value in self.watches.items() if value["until"] > now}
        result = set().union(*(set(w["ids"]) for w in self.watches.values())) if self.watches else set()
        for identity, item in self.catalogue.items():
            if any(fnmatch.fnmatchcase(item["name"], pattern) for pattern in self.config["preconfigured"]):
                if identity in result or len(result) < self.config["max_series"]:
                    result.add(identity)
        return result

    def watch(self, actor, identities, identity=None):
        require(isinstance(identities, list) and bool(identities) and len(identities) <= self.config["max_series"],
                "Select between one and the configured maximum number of series.")
        require(all(item in self.catalogue for item in identities), "Unknown measurement selection.")
        if identity:
            require(identity in self.watches and self.watches[identity]["actor"] == actor, "Watch not found.", 404)
        active = self.active()
        require(identity is not None or len(self.watches) < self.max_watches, "Watch lease quota reached.", 429, "WATCH_QUOTA")
        require(len(active | set(identities)) <= self.config["max_series"], "Series quota reached.", 429, "SERIES_QUOTA")
        identity = identity or uuid.uuid4().hex
        self.watches[identity] = {"actor": actor, "ids": list(dict.fromkeys(identities)), "until": time.monotonic() + 60}
        return {"id": identity, "measurement_ids": identities, "lease_s": 60}

    def unwatch(self, actor, identity):
        require(identity in self.watches and self.watches[identity]["actor"] == actor, "Watch not found.", 404)
        del self.watches[identity]

    def observe(self, snapshot, error=None):
        wall, mono = time.time(), time.monotonic()
        backwards = self.last_wall is not None and wall < self.last_wall
        self.last_wall = wall
        simulated = snapshot.get('mode') == 'simulated'
        stamp = (snapshot.get('epoch'), snapshot.get('snapshot_sequence'))
        duplicate = simulated and not error and self.last_simulation_snapshot == stamp
        if simulated:
            wall = int(snapshot.get('simulation_time_ns', '0')) / 1e9
            self.last_simulation_snapshot = stamp
            backwards = False
        self.sequence += 1
        inventory = snapshot.get("observation", {}).get("components", {})
        present = set()
        for component, raw in snapshot.get("components", {}).items():
            desc = inventory.get(component, {})
            metrics = raw.get("metrics", raw)
            for name, value in metrics.get("measurements", {}).items():
                identity = opaque("measurement", component, name)
                present.add(identity)
                age = desc.get("report_age_ms")
                quality = ("unavailable" if error or not desc.get("connected") else
                           "stale" if age is None or age > 3000 else
                           "missing" if value is None else "good")
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                    quality, value = "invalid", None
                self.catalogue[identity] = {"id": identity, "component": component, "local_name": name,
                    "name": f"{component}.{name}", "value": value if quality == "good" else None,
                    "generation": desc.get("generation"), "epoch": snapshot.get("epoch"),
                    "quality": quality, "observed_at": None if simulated else timestamp(wall), "report_age_ms": age,
                    "time_basis": 'simulated' if simulated else 'realtime',
                    "simulation_time_ns": snapshot.get('simulation_time_ns') if simulated else None}
        for identity, item in self.catalogue.items():
            if identity not in present:
                item.update(value=None, quality="unavailable")
        for identity in self.active():
            item = self.catalogue.get(identity)
            if item is None:
                continue
            segment_id = opaque("segment", identity, item["epoch"], item["generation"])
            if duplicate and segment_id in self.segments:
                continue
            if segment_id not in self.segments:
                self.segments[segment_id] = {"id": segment_id, "measurement_id": identity,
                    "name": item["name"], "epoch": item["epoch"], "generation": item["generation"],
                    "time_basis": item['time_basis'], "points": deque()}
            quality = "clock_reset" if backwards else item["quality"]
            point = (self.sequence, wall, mono, item["value"] if quality == "good" else None, quality)
            # Account for the point, deque reference and global eviction record conservatively.
            size = (sys.getsizeof(point) + sum(sys.getsizeof(v) for v in point)
                    + sys.getsizeof((segment_id, point, 0)) + sys.getsizeof(segment_id) + 32)
            self.segments[segment_id]["points"].append(point)
            self.order.append((segment_id, point, size))
            self.bytes += size
        self.evict(mono)

    def evict(self, mono=None):
        mono = time.monotonic() if mono is None else mono
        budget = self.config["memory_budget_mib"] * 1024**2
        def segment_size(segment):
            return (sys.getsizeof(segment) + sys.getsizeof(segment["points"])
                    + sum(sys.getsizeof(value) for key, value in segment.items() if key != "points"))
        containers = sum(segment_size(s) for s in self.segments.values())
        while self.order and (self.order[0][1][2] < mono - self.config["retention_s"] or
                              len(self.order) > self.config["max_total_samples"] or self.bytes + sys.getsizeof(self.order) + containers > budget):
            identity, point, size = self.order.popleft()
            segment = self.segments[identity]
            before = segment_size(segment)
            segment["points"].popleft()
            self.bytes -= size
            self.truncated = True
            if not segment["points"]:
                del self.segments[identity]
                containers -= before
            else:
                containers += segment_size(segment) - before
        # The catalogue is a current inventory, not an unbounded history of removed components.
        active = self.active()
        retained = {s["measurement_id"] for s in self.segments.values()}
        self.catalogue = {key: item for key, item in self.catalogue.items()
                          if item["quality"] != "unavailable" or key in active or key in retained}
        self.allocated_bytes = self.bytes + sys.getsizeof(self.order) + containers

    @staticmethod
    def reduce(points, maximum):
        if len(points) <= maximum:
            return points
        selected = []
        previous = None
        buckets = max(1, maximum // 8)
        for n in range(buckets):
            chunk = points[n * len(points) // buckets:(n + 1) * len(points) // buckets]
            if not chunk:
                continue
            indices = {0, len(chunk) - 1}
            good = [(i, p[3]) for i, p in enumerate(chunk) if p[3] is not None]
            if good:
                indices.add(min(good, key=lambda x: x[1])[0])
                indices.add(max(good, key=lambda x: x[1])[0])
            gaps = [i for i, p in enumerate(chunk) if p[3] is None]
            if gaps:
                indices.add(gaps[0])
            for index in sorted(indices):
                absolute = n * len(points) // buckets + index
                if previous is not None and selected[-1][3] is not None and points[absolute][3] is not None:
                    gap = next((p for p in points[previous + 1:absolute] if p[3] is None), None)
                    if gap is not None:
                        selected.append(gap)
                selected.append(points[absolute])
                previous = absolute
        return selected

    def snapshot(self, identities, cursor=None):
        self.evict()
        matching = [s for s in reversed(list(self.segments.values())) if s["measurement_id"] in identities]
        selection = page([s["id"] for s in matching], cursor, 200)
        chosen = set(selection["items"])
        return [{**s, "points": list(s["points"])} for s in matching if s["id"] in chosen], selection["next"]

    def query(self, identities, start=None, end=None, maximum=5000, cursor=None):
        require(8 <= maximum <= 5000, "Point budget must be between 8 and 5000.")
        require(len(identities) <= 128, "Select at most 128 measurements per history request.")
        matching, next_cursor = self.snapshot(identities, cursor)
        return self.render(matching, start, end, maximum, next_cursor)

    def render(self, matching, start=None, end=None, maximum=5000, next_cursor=None):
        per_segment = min(maximum, max(8, 50000 // max(1, len(matching))))
        result = []
        for segment in matching:
            simulated = segment.get('time_basis') == 'simulated'
            points = [p for p in segment["points"] if (start is None or p[1] >= start) and (end is None or p[1] <= end)]
            reduced = self.reduce(points, per_segment)
            result.append({k: v for k, v in segment.items() if k != "points"} | {
                "points": [{"sequence": str(p[0]), "time": p[1] * 1000, "observed_at": None if simulated else timestamp(p[1]),
                            "value": p[3], "quality": p[4]} for p in reduced],
                "raw_count": len(points), "reduced": len(reduced) < len(points),
                "available_from": (points[0][1] if simulated else timestamp(points[0][1])) if points else None,
                "available_to": (points[-1][1] if simulated else timestamp(points[-1][1])) if points else None})
        return {"segments": result, "next": next_cursor, "truncated": self.truncated, "buffer_samples": len(self.order),
                "buffer_bytes": self.allocated_bytes}
