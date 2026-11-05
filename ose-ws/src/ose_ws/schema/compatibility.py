"""Explain compiled link contracts; SDL's directional comparison decides validity."""
from ose_ws.schema.adapter import SchemaAdapter


MAX_FIELDS = 256
MAX_DEPTH = 16


def diagnose(source_catalogue, source_root, destination_catalogue, destination_root):
    source = SchemaAdapter(source_catalogue, source_root)
    destination = SchemaAdapter(destination_catalogue, destination_root)
    api = source.values.api
    if source_root != destination_root:
        diagnostic = {"code": "ROOT_MISMATCH", "path": source_root,
            "publisher_type": source_root, "consumer_type": destination_root}
        compatible = False
    else:
        check = api.compare_catalogues(source_catalogue, destination_catalogue,
                                       root=source_root, require_required=True)
        diagnostic, compatible = check._asdict(), check.compatible
    rows, truncated = [], False

    def kind(adapter, name):
        return "message" if name in adapter.values.messages else "enum" if name in adapter.values.enums else "primitive"

    def cardinality(field):
        modifier = field["modifier"]
        return "sequence" if modifier in ("repeated", "packed") else modifier

    def field_view(field):
        if field is None:
            return None
        return {"name": field["name"], "type": field["type"], "cardinality": field["modifier"],
                "dimensions": field["dimensions"], "wire": field.get("wire", {})}

    def visit(source_name, destination_name, path, destination_path, depth=0):
        nonlocal truncated
        if depth >= MAX_DEPTH:
            truncated = True
            return
        left = {f["id"]: f for f in source.values.messages[source_name]}
        right = {f["id"]: f for f in destination.values.messages[destination_name]}
        for identity in sorted(left.keys() | right.keys()):
            if len(rows) >= MAX_FIELDS:
                truncated = True
                return
            before, after = left.get(identity), right.get(identity)
            at = path + "." + (before or after)["name"]
            target_at = destination_path + "." + (after or before)["name"]
            row = {"path": at, "destination_path": target_at, "field_id": identity,
                   "source": field_view(before), "destination": field_view(after), "findings": []}
            rows.append(row)

            def note(code, message, error=False):
                row["findings"].append({"code": code, "message": message, "severity": "error" if error else "info"})

            if before is None:
                if after["modifier"] == "required":
                    note("REQUIRED_FIELD_MISSING", "The destination requires a field the source does not publish.", True)
                else:
                    note("FIELD_DEFAULTED", "Absent from the source; the destination keeps its default (absent optional or empty sequence).")
            elif after is None:
                note("FIELD_IGNORED", "The destination skips this source field because its ID is unknown.")
            else:
                if before["type"] != after["type"]:
                    note("FIELD_TYPE_MISMATCH", "Logical field types differ; SDL does not implicitly convert them.", True)
                if before["dimensions"] != after["dimensions"]:
                    note("FIELD_DIMENSIONS_MISMATCH", "Fixed array dimensions differ.", True)
                if cardinality(before) != cardinality(after):
                    note("FIELD_CARDINALITY_MISMATCH", "Required, optional and sequence cardinalities must agree.", True)
                elif before["modifier"] != after["modifier"]:
                    note("SEQUENCE_ENCODING", "Packed and repeated fields share the sequence cardinality.")
                if before["name"] != after["name"]:
                    note("FIELD_RENAMED", "Field names differ; matching uses the numeric field ID.")
                if before.get("wire", {}) != after.get("wire", {}):
                    note("WIRE_DIFFERENCE", "Wire settings differ. The source catalogue controls decoding; source quantization remains.")
                if before["type"] == after["type"]:
                    name = before["type"]
                    source_kind, target_kind = kind(source, name), kind(destination, name)
                    if source_kind != target_kind:
                        note("TYPE_KIND_MISMATCH", "The same logical type name describes different kinds of type.", True)
                    elif source_kind == "enum":
                        published, accepted = source.values.enums[name], destination.values.enums[name]
                        unknown = sorted(published.keys() - accepted.keys())
                        if unknown:
                            shown = ", ".join(str(value) for value in unknown[:16])
                            if len(unknown) > 16:
                                shown += ", …"
                            note("ENUM_VALUE_UNKNOWN", "Destination enum does not accept source values: " + shown + ".", True)
                        elif published != accepted:
                            note("ENUM_DIFFERENCE", "All source enum values are accepted; labels or additional destination values differ.")
                    elif source_kind == "message" and before["dimensions"] == after["dimensions"] and cardinality(before) == cardinality(after):
                        visit(name, name, at, target_at, depth + 1)
            row["status"] = "incompatible" if any(n["severity"] == "error" for n in row["findings"]) else "different" if row["findings"] else "matching"

    if source_root == destination_root:
        visit(source_root, destination_root, source_root, destination_root)
    return {"compatible": compatible, "identical": source_root == destination_root and source_catalogue == destination_catalogue,
            "require_required": True, "diagnostic": diagnostic,
            "source": {"root": source_root, "schema_digest": source.digest},
            "destination": {"root": destination_root, "schema_digest": destination.digest},
            "fields": rows, "truncated": truncated}
