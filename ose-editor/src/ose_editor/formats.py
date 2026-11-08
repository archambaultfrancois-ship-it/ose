"""Maintained descriptor adapters: YAML ranges and annotated XML element controls."""
from copy import deepcopy
from decimal import Decimal
import html
import json
import re
import xml.etree.ElementTree as ET

from .json_source import JsonSource, Node, dump, loads


class YamlSource(JsonSource):
    def __init__(self, text):
        import yaml
        self.text = text
        self.flow = {}
        class Loader(yaml.SafeLoader):
            yaml_implicit_resolvers = deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
        for key, entries in Loader.yaml_implicit_resolvers.items():
            Loader.yaml_implicit_resolvers[key] = [(tag, pattern) for tag, pattern in entries if not tag.endswith((':bool', ':timestamp', ':int', ':float', ':null'))]
        for name, pattern, initials in (
                ('bool', r'(?:true|false)\Z', 'tf'), ('null', r'null\Z', 'n'),
                ('int', r'-?(?:0|[1-9][0-9]*)\Z', '-0123456789'),
                ('float', r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+(?:[eE][+-]?[0-9]+)?|[eE][+-]?[0-9]+)\Z', '-0123456789')):
            Loader.add_implicit_resolver('tag:yaml.org,2002:' + name, re.compile(pattern), list(initials))
        try:
            syntax = yaml.compose(text, Loader=Loader)
        except yaml.YAMLError as error:
            raise ValueError('Invalid YAML: ' + str(error)) from error
        if syntax is None:
            raise ValueError('Empty YAML document')
        def build(item, ancestors=frozenset(), depth=0):
            if id(item) in ancestors or depth > 64:
                raise ValueError('Recursive or deeply nested YAML is unsupported')
            if item.tag.startswith('tag:yaml.org,2002:') is False or item.tag.endswith((':merge', ':timestamp')):
                raise ValueError('Unsupported YAML tag: ' + item.tag)
            children, keys = {}, {}
            if isinstance(item, yaml.MappingNode):
                value = {}
                for key, child in item.value:
                    if not isinstance(key, yaml.ScalarNode):
                        raise ValueError('YAML mapping keys must be strings')
                    if key.value == '<<' or key.value in value:
                        raise ValueError('Duplicate or merge YAML key: ' + key.value)
                    built = build(child, ancestors | {id(item)}, depth + 1)
                    children[key.value] = built
                    keys[key.value] = (key.start_mark.index, key.end_mark.index)
                    value[key.value] = built.value
            elif isinstance(item, yaml.SequenceNode):
                value = []
                for index, child in enumerate(item.value):
                    built = build(child, ancestors | {id(item)}, depth + 1)
                    children[str(index)] = built
                    value.append(built.value)
            else:
                if item.tag.endswith(':str'):
                    value = item.value
                elif item.tag.endswith(':null'):
                    value = None
                elif item.tag.endswith(':bool'):
                    value = item.value == 'true'
                elif re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?', item.value):
                    value = loads(item.value)
                else:
                    value = item.value  # SDL YAML uses JSON-style numeric scalars.
            result = Node(item.start_mark.index, item.end_mark.index, value, children, keys)
            self.flow[result.start] = getattr(item, 'flow_style', False)
            return result
        self.root = build(syntax)

    def set(self, path, literal):
        value = loads(literal)
        try:
            item = self.node(path)
            return self.patch(item.start, item.end, literal)
        except KeyError:
            parent = self.node(path[:-1])
            if self.flow.get(parent.start):
                return super().set(path, literal)
            if not isinstance(parent.value, (dict, list)):
                raise ValueError('Parent is not a YAML collection')
            first = next(iter(parent.children), None)
            begin = parent.keys[first][0] if first is not None and parent.keys else parent.start
            indent = begin - (self.text.rfind('\n', 0, begin) + 1)
            if isinstance(parent.value, list):
                insertion = ' ' * indent + '- ' + literal + '\n'
            else:
                insertion = ' ' * indent + json.dumps(str(path[-1])) + ': ' + literal + '\n'
            position = parent.end
            if position and self.text[position - 1] != '\n':
                insertion = '\n' + insertion
            return self.patch(position, position, insertion)

    def delete(self, path):
        parent, key = self.node(path[:-1]), str(path[-1])
        if self.flow.get(parent.start):
            return super().delete(path)
        child = self.node(path)
        start = parent.keys[key][0] if parent.keys else child.start
        start = self.text.rfind('\n', 0, start) + 1
        end = child.end
        if end > 0 and self.text[end - 1] != '\n':
            end = self.text.find('\n', end)
            end = len(self.text) if end == -1 else end + 1
        return self.patch(start, end, '')

    def tree(self, path=()):
        result = super().tree(path)
        if result['kind'] not in ('object', 'array'):
            result['literal'] = dump(self.node(path).value)
        return result


class XsdSource(JsonSource):
    """Expose every XML attribute/text as structured fields without serialization.

    SDL metadata is also shown as a JSON object. Metadata edits update the
    corresponding primitive/name/cardinality attributes in the enclosing element.
    Complex format changes still require SDL's authoritative profile validation.
    """
    def __init__(self, text):
        self.text = text
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
            raise ValueError('DTDs and entities are outside the SDL XSD profile')
        try:
            ET.fromstring(text)
        except ET.ParseError as error:
            raise ValueError('Invalid XML: ' + str(error)) from error
        self.xml_elements = {}
        stack, roots = [], []
        token = re.compile(r'<!--.*?-->|<\?.*?\?>|</[^>]+>|<[^>]+>', re.S)
        for match in token.finditer(text):
            raw = match[0]
            if raw.startswith(('<!--', '<?')):
                continue
            if raw.startswith('</'):
                node = stack.pop()
                node['end'], node['text_end'] = match.end(), match.start()
                continue
            name = re.match(r'<([^\s/>]+)', raw)[1]
            attrs = {}
            for attr in re.finditer(r'([^\s=<>]+)\s*=\s*([\'"])(.*?)\2', raw, re.S):
                attrs[attr[1]] = (html.unescape(attr[3]), match.start() + attr.start(3), match.start() + attr.end(3))
            node = {'name': name, 'start': match.start(), 'open_end': match.end(), 'end': match.end(), 'text_end': match.end(), 'attrs': attrs, 'children': [], 'parent': stack[-1] if stack else None}
            (stack[-1]['children'] if stack else roots).append(node)
            if not raw.endswith('/>'):
                stack.append(node)
        self.bindings = {}
        def build_xml(node, path):
            self.xml_elements[tuple(path)] = node
            self.bindings[tuple([*path, 'tag'])] = ('tag', node, '')
            attributes = Node(node['start'], node['open_end'], {}, {}, {})
            for key, (value, a, b) in node['attrs'].items():
                attributes.children[key] = Node(a, b, value)
                attributes.value[key] = value
                self.bindings[tuple([*path, 'attributes', key])] = ('attribute', node, key)
            body = html.unescape(text[node['open_end']:node['text_end']]) if not node['children'] else ''
            text_node = Node(node['open_end'], node['text_end'], body)
            self.bindings[tuple([*path, 'text'])] = ('text', node, '')
            children = Node(node['open_end'], node['text_end'], [], {}, {})
            for i, child in enumerate(node['children']):
                built = build_xml(child, [*path, 'children', str(i)])
                children.children[str(i)] = built
                children.value.append(built.value)
            props = {'tag': Node(node['start'], node['open_end'], node['name']), 'attributes': attributes, 'text': text_node, 'children': children}
            if node['name'].startswith('sdl:') and body.strip().startswith('{'):
                metadata = JsonSource(body)
                props['metadata'] = metadata.root
                def bindings(item, pointer):
                    self.bindings[tuple(pointer)] = ('metadata', node, pointer[len(path) + 1:])
                    for key, child in item.children.items():
                        bindings(child, [*pointer, key])
                bindings(metadata.root, [*path, 'metadata'])
            return Node(node['start'], node['end'], {k: n.value for k, n in props.items()}, props)
        self.root = build_xml(roots[0], [])

    def set(self, path, literal):
        path = tuple(map(str, path))
        value = loads(literal)
        if path not in self.bindings:
            for length in range(len(path) - 1, 0, -1):
                parent = self.bindings.get(path[:length])
                if parent and parent[0] == 'metadata':
                    self.bindings[path] = ('metadata', parent[1], [*parent[2], *path[length:]])
                    break
        if path not in self.bindings:
            if len(path) >= 2 and path[-2] == 'attributes':
                element = self.xml_elements[path[:-2]]
                if not re.fullmatch(r'[\w:.-]+', path[-1]):
                    raise ValueError('Invalid XML attribute name')
                offset = element['open_end'] - (2 if self.text[element['open_end'] - 2:element['open_end']] == '/>' else 1)
                return self.patch(offset, offset, ' ' + path[-1] + '="' + html.escape(str(value), quote=True) + '"')
            if len(path) >= 2 and path[-2] == 'children':
                element = self.xml_elements[path[:-2]]
                if path[-1] not in ('-', str(len(element['children']))):
                    raise ValueError('Append a new XML child or edit an existing element property')
                def render(item):
                    tag = item['tag']
                    if not re.fullmatch(r'[\w:.-]+', tag):
                        raise ValueError('Invalid XML element name')
                    attrs = ''.join(' ' + key + '="' + html.escape(str(val), quote=True) + '"' for key, val in item.get('attributes', {}).items() if re.fullmatch(r'[\w:.-]+', key))
                    children = ''.join(render(child) for child in item.get('children', []))
                    return '<' + tag + attrs + '>' + html.escape(item.get('text', ''), quote=False) + children + '</' + tag + '>'
                inserted = render(value)
                if self.text[element['open_end'] - 2:element['open_end']] == '/>':
                    return self.patch(element['open_end'] - 2, element['open_end'], '>' + inserted + '</' + element['name'] + '>')
                return self.patch(element['text_end'], element['text_end'], inserted)
            raise ValueError('Choose an editable XML attribute, text, metadata property or child collection')
        kind, element, key = self.bindings[path]
        if kind == 'tag':
            if not re.fullmatch(r'[\w:.-]+', str(value)):
                raise ValueError('Invalid XML element name')
            result = self.text
            if element['text_end'] < element['end']:
                begin = element['text_end'] + 2
                result = result[:begin] + value + result[begin + len(element['name']):]
            begin = element['start'] + 1
            return result[:begin] + value + result[begin + len(element['name']):]
        if kind == 'attribute':
            _, a, b = element['attrs'][key]
            return self.patch(a, b, html.escape(str(value), quote=True))
        if kind == 'text':
            if element['children']:
                raise ValueError('Cannot replace child elements through the text property')
            return self.patch(element['open_end'], element['text_end'], html.escape(str(value), quote=False))
        body = html.unescape(self.text[element['open_end']:element['text_end']])
        updated = JsonSource(body).set(key, literal)
        patches = [(element['open_end'], element['text_end'], html.escape(updated, quote=False))]
        if element['name'] == 'sdl:field':
            data = loads(updated)
            owner = element['parent']['parent']['parent']
            mapping = {'bool': 'boolean', 'int8': 'byte', 'int16': 'short', 'int32': 'int', 'int64': 'long', 'uint8': 'unsignedByte', 'uint16': 'unsignedShort', 'uint32': 'unsignedInt', 'uint64': 'unsignedLong', 'fl32': 'float', 'fl64': 'double', 'string': 'string'}
            updates = {'name': data['name']}
            if data.get('type') in mapping and not data.get('dimensions') and data.get('modifier', 'required') in ('required', 'optional'):
                updates.update(type='xs:' + mapping[data['type']], minOccurs='0' if data.get('modifier') == 'optional' else '1', maxOccurs='1')
            for attr, item in updates.items():
                if attr in owner['attrs']:
                    _, a, b = owner['attrs'][attr]
                    patches.append((a, b, html.escape(str(item), quote=True)))
        result = self.text
        for a, b, item in sorted(patches, reverse=True):
            result = result[:a] + item + result[b:]
        return result

    def delete(self, path):
        path = tuple(map(str, path))
        if path in self.xml_elements and path:
            element = self.xml_elements[path]
            return self.patch(element['start'], element['end'], '')
        if path in self.bindings and self.bindings[path][0] == 'attribute':
            _, element, name = self.bindings[path]
            _, start, end = element['attrs'][name]
            name_start = self.text.rfind(name, element['start'], start)
            return self.patch(name_start, end + 1, '')
        if path in self.bindings and self.bindings[path][0] == 'metadata':
            _, element, pointer = self.bindings[path]
            body = html.unescape(self.text[element['open_end']:element['text_end']])
            updated = JsonSource(body).delete(pointer)
            return self.patch(element['open_end'], element['text_end'], html.escape(updated, quote=False))
        raise ValueError('Select an XML child, attribute or metadata property to remove')

    def rename(self, path, name):
        raise ValueError('Use the metadata name property to rename an annotated XSD field')

    def tree(self, path=()):
        node = self.node(path)
        kind = 'object' if isinstance(node.value, dict) else 'array' if isinstance(node.value, list) else 'boolean' if isinstance(node.value, bool) else 'null' if node.value is None else 'string' if isinstance(node.value, str) else 'number'
        return {'path': list(path), 'kind': kind, 'literal': dump(node.value) if not node.children else '', 'start': node.start, 'end': node.end,
                'children': [{'name': key, **self.tree((*path, key))} for key in node.children]}


def DescriptorSource(text, format):
    return YamlSource(text) if format == 'yaml' else XsdSource(text)
