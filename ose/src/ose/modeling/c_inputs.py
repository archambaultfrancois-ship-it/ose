"""Typed C declarations and dispatch for the shared CH/2 callback runtime."""
from ose.modeling.native_containers import c_string


def header(node, types):
    lines = []
    for port, desc in node["inputs"].items():
        typ = "void" if desc.get("kind") == "timer" else types[desc["root"]][1]
        lines += [f"typedef struct {{ OseInputReference meta; const {typ} *message; }} Input_{port};"]
    for port, desc in node["outputs"].items():
        lines += [f"typedef {types[desc['root']][1]} Output_{port};"]
    lines += ["typedef struct {", "    OseHook on_start, on_stop;"]
    for port, desc in node["inputs"].items():
        if desc.get("mode", "push") != "pull":
            lines.append(f"    int (*on_{port})(OseContext *, const Input_{port} *);")
    lines += ["} ComponentCallbacks;", "extern const ComponentCallbacks component_callbacks;"]
    for port, desc in node["outputs"].items():
        lines.append(f"int publish_{port}_with_id(OseContext *, const {types[desc['root']][1]} *, uint64_t id);")
    for port, desc in node["inputs"].items():
        if desc.get("mode") == "pull":
            for operation in ("get", "pull"):
                lines.append(f"int {operation}_{port}(OseContext *, const OseSelector *, Input_{port} *);")
            lines.append(f"int remove_{port}(OseContext *, const OseSelector *, int *removed);")
    return lines


def source(node, types):
    lines = []
    for index, (port, desc) in enumerate(node["inputs"].items()):
        for event in ("message", "timeout"):
            outputs = desc.get("activates", {}).get(event, [])
            if outputs:
                lines += [f"static const char *const ose_allow_{index}_{event}[] = {{" +
                          ", ".join(map(c_string, outputs)) + "};"]
    lines.append("static const OseInputPolicy policies[] = {")
    for index, (port, desc) in enumerate(node["inputs"].items()):
        kind = ("OSE_ONE_SHOT" if desc["timer"]["mode"] == "one_shot" else "OSE_PERIODIC") if desc.get("kind") == "timer" else (
            "OSE_PULL" if desc.get("mode") == "pull" else "OSE_PUSH")
        fields = [kind]
        for event in ("message", "timeout"):
            count = len(desc.get("activates", {}).get(event, []))
            fields += [f"ose_allow_{index}_{event}" if count else "NULL", str(count)]
        lines.append("    {" + ", ".join(fields) + "},")
    lines += ["    {OSE_PUSH, NULL, 0, NULL, 0}", "};"]
    for index, (port, desc) in enumerate(node["inputs"].items()):
        if desc.get("mode") != "pull":
            continue
        for operation in ("get", "pull"):
            lines += [f"int {operation}_{port}(OseContext *ctx, const OseSelector *selector, Input_{port} *out) {{",
                      f"    if (!out) return ose_fail(ctx, \"missing input reference\");",
                      "    memset(out, 0, sizeof(*out));",
                      f"    if (!ose_get_input(ctx, {index}, {int(operation == 'pull')}, selector, &out->meta)) return 0;",
                      "    out->message = out->meta.message; return 1;", "}"]
        lines += [f"int remove_{port}(OseContext *ctx, const OseSelector *selector, int *removed) {{",
                  f"    return ose_remove_input(ctx, {index}, selector, removed);", "}"]
    for index, (port, desc) in enumerate(node["outputs"].items()):
        lines += [f"int publish_{port}_with_id(OseContext *ctx, const {types[desc['root']][1]} *message, uint64_t id) {{",
                  f"    return ose_publish_value_with_id(ctx, {index}, message, id);", "}"]
    lines += ["static int dispatch(OseContext *ctx, const OseInputReference *reference) {"]
    for port, desc in node["inputs"].items():
        if desc.get("mode", "push") == "pull":
            continue
        lines += [f"    if (!strcmp(reference->port, {c_string(port)})) {{",
                  f"        Input_{port} input = {{ *reference, reference->message }};",
                  f"        return component_callbacks.on_{port}(ctx, &input);", "    }"]
    lines += ['    (void)reference; return ose_fail(ctx, "unknown activating input");', "}", "int main(void) {"]
    callbacks = ["!component_callbacks.on_" + port for port, desc in node["inputs"].items() if desc.get("mode", "push") != "pull"]
    if callbacks:
        lines += ["    if (" + " || ".join(callbacks) + ") {", '        fputs("missing component callbacks\\n", stderr); return 1;', "    }"]
    return lines
