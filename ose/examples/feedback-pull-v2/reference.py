#!/usr/bin/env python3
"""Run the v2 feedback example in the in-process reference callback host."""
import argparse
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from ose.codec.catalogue import CatalogueTypes
from ose.codec.sdl2 import SDL2
from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.input_callbacks import InputCallbacks
from ose.modeling.model import load
from ose.modeling.validation import validate_graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdl-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    model = load(root / "model.json", version=2)
    validate_graph(model, dict(version=1, components={name: dict(command=["reference-only"])
                                                     for name in model["components"]}))
    codec = SDL2(args.sdl_root)
    with tempfile.TemporaryDirectory(prefix="ose-feedback-v2-") as temporary:
        catalogue = codec.generate(root / model["schemas"], temporary)
    Value = CatalogueTypes(codec.api, catalogue).types["Value"]
    hosts, trace = {}, []
    now, sequence, callbacks = 0, 0, 0

    def emit(source, publication):
        nonlocal sequence
        sequence += 1
        value = codec.validate(publication.catalogue, "Value", publication.body)
        trace.append(dict(time_ns=now, topic=source + "." + publication.port,
                          id=publication.app_id, count=value.fields["count"]))
        for sub in model["subscriptions"]:
            if sub["from"] == source + "." + publication.port:
                target, port = sub["to"].split(".")
                record = dict(id=f"reference:{sequence}", epoch="reference", sequence=sequence,
                              topic=sub["from"], catalogue=publication.catalogue, body=publication.body)
                if publication.app_id is not None:
                    record["app_id"] = publication.app_id
                if hosts[target].admit(port, record) != "accepted":
                    raise RuntimeError("example inbox unexpectedly refused a message")

    def on_clock(ctx, input):
        previous = ctx.inbox("feedback").pull()
        trace.append(dict(time_ns=now, callback="a.clock", feedback=previous.message.count if previous else None))
        ctx.publish("out", Value(count=ctx.activation_index), id=ctx.activation_index)
        if ctx.activation_index == 3:
            ctx.disarm("clock")

    def on_input(ctx, input):
        ctx.publish("out", Value(count=input.message.count + 100), id=input.app_id)

    handlers = dict(a=SimpleNamespace(on_clock=on_clock), b=SimpleNamespace(on_input=on_input))
    try:
        for name, node in model["components"].items():
            buffers = {port: Buffer(SubscriptionPolicy(), MemoryStore(), clock=lambda: now / 1e9)
                       for port, desc in node["inputs"].items() if desc.get("kind") != "timer"}
            hosts[name] = InputCallbacks(node, buffers, handlers[name], {port: Value for port in buffers},
                {port: Value for port in node["outputs"]}, clock=lambda: now,
                emit=lambda publication, source=name: emit(source, publication))
        while True:
            times = [time for host in hosts.values() if (time := host.scheduler.next_event(now)) is not None]
            if not times:
                break
            now = min(times)
            for name in sorted(hosts):
                callbacks += hosts[name].dispatch()
            if callbacks > 20:
                raise RuntimeError("example did not quiesce")
        print(json.dumps(dict(reason="quiescent", callbacks=callbacks, trace=trace,
            feedback_depth=hosts["a"].scheduler.buffers["feedback"].store.size()), indent=2))
    finally:
        for host in hosts.values():
            host.close()


if __name__ == "__main__":
    main()
