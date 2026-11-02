"""The SDL codec remains authoritative, including custom wire representations."""
import hashlib
import math
import re
from ose.codec.runtime import Runtime
from ose.parameters.values import Values
from ose_ws.util import APIError, dumps


class SchemaAdapter:
    def __init__(self, catalogue, root, presentation=None):
        self.root, self.catalogue = root, catalogue
        self.values = Values(Runtime().api, catalogue, root)
        self.digest = "sha256:" + hashlib.sha256((root + "\0" + catalogue).encode()).hexdigest()
        self.presentation = presentation or {}

    def description(self):
        types = {}
        for name, fields in self.values.messages.items():
            types[name] = {"kind": "message", "fields": [
                {"id": field["id"], "name": field["name"], "type": field["type"],
                 "cardinality": field["modifier"], "dimensions": field["dimensions"],
                 "wire": field.get("wire", {})} for field in fields]}
        for name, values in self.values.enums.items():
            types[name] = {"kind": "enum", "values": [{"name": label, "value": value} for value, label in values.items()]}
        return {"root": self.root, "schema_digest": self.digest, "catalogue": self.catalogue,
                "types": types, "presentation_version": hashlib.sha256(dumps(self.presentation).encode()).hexdigest(),
                "presentation": self.presentation}

    def convert(self, value, *, to_web, name=None, dimensions=(), depth=0, pointer=""):
        if depth > 64:
            raise APIError(413, "VALUE_DEPTH", "SDL value exceeds the web nesting limit.", pointer=pointer)
        name = name or self.root
        if dimensions:
            if not isinstance(value, list) or len(value) != dimensions[0]:
                raise APIError(422, "PARAMETER_INVALID", f"Expected exactly {dimensions[0]} array elements.", pointer=pointer)
            return [self.convert(item, to_web=to_web, name=name, dimensions=dimensions[1:], depth=depth + 1,
                                 pointer=pointer + f"/{i}") for i, item in enumerate(value)]
        if name in self.values.messages:
            if not isinstance(value, dict):
                raise APIError(422, "PARAMETER_INVALID", "Expected an object.", pointer=pointer)
            fields = {f["name"]: f for f in self.values.messages[name]}
            for key, field in fields.items():
                if key not in value and field["modifier"] != "optional":
                    raise APIError(422, "PARAMETER_INVALID", "A required field is missing.", pointer=pointer + "/" + key)
            result = {}
            for key, item in value.items():
                path = pointer + "/" + key.replace("~", "~0").replace("/", "~1")
                if key not in fields:
                    raise APIError(422, "PARAMETER_INVALID", "Unknown field.", pointer=path)
                field = fields[key]
                if item is None and field["modifier"] == "optional":
                    result[key] = None
                elif field["modifier"] in ("repeated", "packed"):
                    if not isinstance(item, list):
                        raise APIError(422, "PARAMETER_INVALID", "Expected a sequence.", pointer=path)
                    result[key] = [self.convert(v, to_web=to_web, name=field["type"], dimensions=field["dimensions"],
                                               depth=depth + 1, pointer=path + f"/{i}") for i, v in enumerate(item)]
                else:
                    result[key] = self.convert(item, to_web=to_web, name=field["type"], dimensions=field["dimensions"],
                                               depth=depth + 1, pointer=path)
            return result
        if name in ("int64", "uint64"):
            if to_web:
                return str(value)
            if not isinstance(value, str) or not re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
                raise APIError(422, "PARAMETER_INVALID", "64-bit integers require exact decimal strings.", pointer=pointer)
            number = int(value)
            low, high = (0, 2**64 - 1) if name == "uint64" else (-2**63, 2**63 - 1)
            if not low <= number <= high:
                raise APIError(422, "PARAMETER_INVALID", "Integer exceeds its logical type bounds.", pointer=pointer)
            return number
        if not to_web:
            valid = True
            if name in self.values.enums:
                valid = value in self.values.enums[name].values() if isinstance(value, str) else type(value) is int and value in self.values.enums[name]
            elif name == "bool":
                valid = type(value) is bool
            elif name.startswith(("int", "uint")):
                bits = int(name.removeprefix("uint").removeprefix("int"))
                low, high = (0, 2**bits - 1) if name.startswith("uint") else (-2**(bits - 1), 2**(bits - 1) - 1)
                valid = type(value) is int and low <= value <= high
            elif name in ("fl32", "fl64"):
                try:
                    valid = type(value) in (int, float) and math.isfinite(value)
                except OverflowError:
                    valid = False
            elif name == "string":
                valid = isinstance(value, str)
            elif name in ("c32", "c64"):
                valid = isinstance(value, dict) and set(value) == {"real", "imag"}
                if valid:
                    return {key: self.convert(item, to_web=False, name="fl64", depth=depth + 1, pointer=pointer + "/" + key) for key, item in value.items()}
            else:
                valid = False
            if not valid:
                raise APIError(422, "PARAMETER_INVALID", f"Invalid value for SDL type {name}.", pointer=pointer)
        return value

    def business(self, value, pointer=""):
        for path, rules in self.presentation.items():
            if not isinstance(rules, dict) or not rules.get("required"):
                continue
            def visit(item, segments, current):
                if not segments:
                    if "choices" in rules and item not in rules["choices"]:
                        raise APIError(422, "BUSINESS_CONSTRAINT", "Value is not an allowed choice.", pointer=current)
                    for key, compare in (("min", lambda a, b: a < b), ("max", lambda a, b: a > b)):
                        if key in rules and compare(item, rules[key]):
                            raise APIError(422, "BUSINESS_CONSTRAINT", f"Value violates {key}.", pointer=current)
                    return
                part, rest = segments[0], segments[1:]
                if part == "*" and isinstance(item, list):
                    for i, child in enumerate(item):
                        visit(child, rest, current + f"/{i}")
                elif isinstance(item, dict) and part in item:
                    visit(item[part], rest, current + "/" + part)
            visit(value, [p.replace("~1", "/").replace("~0", "~") for p in path.split("/")[1:]], "")

    def validate(self, value, *, ose_format=False):
        plain = value if ose_format else self.convert(value, to_web=False)
        self.business(plain)
        payload = self.values.encode(plain)
        normalized = self.values.decode(payload)
        self.business(normalized)
        web = self.convert(normalized, to_web=True)
        preview_hash = hashlib.sha256((self.digest + dumps(self.presentation)).encode() + payload).hexdigest()
        return {"value": web, "schema_digest": self.digest, "preview_hash": preview_hash,
                "encoded_bytes": len(payload), "normalized": plain != normalized}, payload

    def read(self, payload):
        return self.convert(self.values.decode(payload), to_web=True)
