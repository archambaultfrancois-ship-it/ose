"""Isolated adapter using generated classes and public SDL runtime operations."""
from enum import IntEnum
import importlib.util
import json
from pathlib import Path
import sys


def catalogue(directory):
    import sdl_runtime as api
    directory = Path(directory)
    sys.path.insert(0, str(directory))
    classes = []
    for file in sorted(directory.rglob('*.py')):
        if file.name == '__init__.py':
            continue
        name = '.'.join(file.relative_to(directory).with_suffix('').parts)
        spec = importlib.util.spec_from_file_location(name, file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, api.SdlMessage) and value is not api.SdlMessage and value.__module__ == module.__name__:
                classes.append(value)
    return api.description(classes)


def parameter_preview(api, text, root, value):
    shape = api.prepare(text)
    namespace = {name: IntEnum('DynamicEnum', {label: number for number, label in pairs.items()}) for name, pairs in shape.enums.items()}
    for name, fields in shape.messages.items():
        namespace[name] = type('DynamicMessage', (api.SdlMessage,), {
            '_SDL_NAME': name, '_SDL_DESCRIPTOR': text, '_SDL_TYPES': namespace,
            '_SDL_FIELDS': tuple((f['id'], f['name'], f['modifier'], f['type'], tuple(f['dimensions'])) for f in fields)})
    def convert(name, item, dimensions=()):
        if dimensions:
            if not isinstance(item, list) or len(item) != dimensions[0]:
                raise ValueError('Invalid fixed array dimensions')
            return [convert(name, child, dimensions[1:]) for child in item]
        if name in shape.messages:
            fields = {}
            for field in shape.messages[name]:
                child = item.get(field['name'])
                if child is None and field['modifier'] == 'optional':
                    fields[field['name']] = None
                elif field['modifier'] in ('repeated', 'packed'):
                    fields[field['name']] = [convert(field['type'], part, field['dimensions']) for part in child]
                else:
                    fields[field['name']] = convert(field['type'], child, field['dimensions'])
            return namespace[name](**fields)
        if name in shape.enums:
            return namespace[name][item] if isinstance(item, str) else namespace[name](item)
        if name in ('c32', 'c64'):
            return getattr(api, 'Complex32' if name == 'c32' else 'Complex64')(item['real'], item['imag'])
        return item
    def plain(item):
        if isinstance(item, api.SdlDynamicMessage):
            return {key: plain(value) for key, value in item.fields.items()}
        if isinstance(item, api.SdlDynamicEnum):
            return item.name
        if isinstance(item, (api.Complex32, api.Complex64)):
            return {'real': item.real, 'imag': item.imag}
        if isinstance(item, list):
            return [plain(child) for child in item]
        if isinstance(item, int) and not isinstance(item, bool) and abs(item) > 9007199254740991:
            return {'$number': str(item)}
        return item
    context = api.prepare(text, [namespace[name] for name in shape.messages])
    return plain(api.decode_dynamic(context, api.encode(context, convert(root, value))))


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    import sdl_runtime as api
    if request['mode'] == 'catalogue':
        text = catalogue(request['directory'])
        context = api.prepare(text)
        result = {'catalogue': text, 'identity': str(api.catalogue_id(text)), 'messages': context.messages, 'enums': context.enums, 'initial': {}}
        for path, block in request.get('parameters', {}).items():
            try:
                result['initial'][path] = {'value': parameter_preview(api, text, block['root'], block['initial'])}
            except (ValueError, TypeError, KeyError) as error:
                result['initial'][path] = {'error': str(error)}
    else:
        result = {}
        for item in request['subscriptions']:
            report = api.compare_catalogues(item['publisher'], item['consumer'], root=item['root'], require_required=True)
            result[item['id']] = {**report._asdict(), 'compatible': report.compatible, 'direction': 'publisher → consumer', 'root': item['root']}
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    main()
