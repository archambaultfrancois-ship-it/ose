"""Construct runtime message types from a public SDL catalogue."""
from enum import IntEnum


class CatalogueTypes:
    def __init__(self, api, catalogue):
        shape = api.prepare(catalogue)
        self.messages, self.enums = shape.messages, shape.enums
        namespace = {name: IntEnum('CatalogueEnum', {label: value for value, label in pairs.items()})
                     for name, pairs in self.enums.items()}

        for name, fields in self.messages.items():
            namespace[name] = type('CatalogueMessage', (api.SdlMessage,), {
                '_SDL_NAME': name, '_SDL_DESCRIPTOR': catalogue, '_SDL_TYPES': namespace,
                '_SDL_FIELDS': tuple((f['id'], f['name'], f['modifier'], f['type'], tuple(f['dimensions'])) for f in fields)})
        self.types = namespace
        self.classes = tuple(namespace[name] for name in self.messages)
        self.context = api.prepare(catalogue, self.classes)
