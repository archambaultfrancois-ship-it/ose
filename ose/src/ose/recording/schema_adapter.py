"""Validate source payloads and adapt compatible catalogues before injection."""
from collections import OrderedDict
import hashlib

from ose.codec.catalogue import CatalogueTypes


def schema_policy(value):
    if value not in ('exact', 'compatible'):
        raise ValueError('schema policy must be exact or compatible')
    return value


class SchemaAdapter:
    def __init__(self, codec, source, target, root, policy='exact'):
        self.codec, self.source, self.target, self.root = codec, source, target, root
        self.exact = source == target
        if schema_policy(policy) == 'exact' and not self.exact:
            raise ValueError('recorded catalogue must match the external topic (schema policy exact)')
        self.validator = codec.api.prepare(source)
        if not self.exact:
            codec.compare(source, target, root)
            self.types = CatalogueTypes(codec.api, target)
            self.fields = {}
            for name, fields in self.types.messages.items():
                source_names = {f['id']: f['name'] for f in self.validator.messages.get(name, ())}
                self.fields[name] = [(source_names[f['id']], f) for f in fields if f['id'] in source_names]
        self.source_sha256 = hashlib.sha256(source.encode()).hexdigest()
        self.target_sha256 = hashlib.sha256(target.encode()).hexdigest()

    def _value(self, name, value, dimensions=()):
        if dimensions:
            return [self._value(name, item, dimensions[1:]) for item in value]
        if name in self.types.messages:
            return self._message(name, value)
        if name in self.types.enums:
            return self.types.types[name](value.value)
        return value

    def _message(self, name, value):
        fields = {}
        for source_name, field in self.fields[name]:
            item = value.fields[source_name]
            if item is not None:
                if field['modifier'] in ('repeated', 'packed'):
                    item = [self._value(field['type'], element, field['dimensions']) for element in item]
                else:
                    item = self._value(field['type'], item, field['dimensions'])
            fields[field['name']] = item
        return self.types.types[name](**fields)

    def adapt(self, payload):
        api = self.codec.api
        # Decode all source fields, including those the consumer discards.
        value = api.decode_dynamic(self.validator, payload)
        if value.type_name != self.root:
            raise ValueError('recorded payload has an unexpected root message')
        if not self.exact:
            # Project only retained fields. Binding every target type to the
            # source would also check unrelated declarations outside this root.
            payload = api.encode(self.types.context, self._message(self.root, value))
            if api.decode_dynamic(self.types.context, payload).type_name != self.root:
                raise ValueError('adapted payload has an unexpected root message')
        return self.codec.wrap(self.target, payload)


class SchemaAdapters:
    """Bound expensive prepared contexts independently of capture length."""
    def __init__(self, codec, policy='exact', capacity=64):
        self.codec, self.policy = codec, schema_policy(policy)
        if type(capacity) is not int or capacity < 1:
            raise ValueError('schema adapter capacity must be positive')
        self.capacity, self.cache = capacity, OrderedDict()

    def get(self, source, target, root):
        # Full texts are essential: the wire catalogue ID is only 32 bits.
        key = source, target, root
        if key not in self.cache:
            self.cache[key] = SchemaAdapter(self.codec, source, target, root, self.policy)
            if len(self.cache) > self.capacity:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]
