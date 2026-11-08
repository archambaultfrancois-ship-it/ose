"""Source-preserving SDL syntax projection, separate from authoritative validation.

Ranges retain spelling, comments, aliases, expressions and omitted defaults.
Semantic acceptance is always delegated to the selected SDL compiler.
"""
from dataclasses import dataclass
import re


TOKEN = re.compile(r'(?P<space>\s+)|(?P<line>//[^\n]*)|(?P<block>/\*.*?\*/)|(?P<unclosed>/\*.*\Z)|(?P<number>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)|(?P<name>\w+)|(?P<other>.)', re.S)
BUILTINS = ('bool', 'int8', 'int16', 'int32', 'int64', 'uint8', 'uint16', 'uint32', 'uint64', 'fl32', 'fl64', 'c32', 'c64', 'string')
KEYWORDS = ('message', 'structure', 'struct')
MODIFIERS = ('required', 'optional', 'repeated', 'packed')


@dataclass
class Token:
    text: str
    start: int
    end: int
    doc: bool = False


class SdlSource:
    def __init__(self, text):
        self.text, self.tokens, self.position = text, [], 0
        for match in TOKEN.finditer(text):
            if match.lastgroup == 'unclosed':
                raise ValueError('Unterminated comment at offset ' + str(match.start()))
            if match.lastgroup in ('space', 'line'):
                continue
            if match.lastgroup == 'block' and not match[0].startswith('/**'):
                continue
            self.tokens.append(Token(match[0], match.start(), match.end(), match.lastgroup == 'block'))
        self.tokens.append(Token('', len(text), len(text)))
        self.nodes = []
        self.package = ''
        while self.peek().text or self.peek().doc:
            self.nodes.append(self.declaration())
        self.index = {}
        def register(nodes, parent=''):
            for i, node in enumerate(nodes):
                node['path'] = parent + '/' + str(i)
                self.index[node['path']] = node
                register(node['children'], node['path'])
        register(self.nodes)

    def peek(self, offset=0):
        return self.tokens[min(self.position + offset, len(self.tokens) - 1)]

    def take(self, value=None):
        token = self.peek()
        if not token.text or value is not None and token.text != value:
            line = self.text.count('\n', 0, token.start) + 1
            raise ValueError(f'Expected {value or "SDL token"} at line {line}, offset {token.start}')
        self.position += 1
        return token

    def name(self):
        token = self.take()
        if not re.fullmatch(r'\w+', token.text):
            raise ValueError('Expected SDL name at offset ' + str(token.start))
        return token

    def reference(self):
        start = self.peek().start
        if self.peek().text == '.':
            self.take()
        self.name()
        while self.peek().text == '.':
            self.take()
            self.name()
        return (start, self.tokens[self.position - 1].end)

    def leading_docs(self):
        docs = []
        while self.peek().doc:
            docs.append(self.take())
        return docs

    def property(self, start, end, **extra):
        return {'start': start, 'end': end, 'value': self.text[start:end], **extra}

    def finish(self, node, docs, last):
        while self.peek().doc and '\n' not in self.text[last.end:self.peek().start]:
            docs.append(self.take())
        node['end'] = last.end
        node['docs'] = [(d.start, d.end) for d in docs]
        node['properties']['doc'] = {'value': '\n\n'.join(self._doc(d.text) for d in docs), 'start': node['start'], 'end': node['start']}
        return node

    @staticmethod
    def _doc(text):
        return '\n'.join(re.sub(r'^\s*\* ?', '', line) for line in text[3:-2].strip().splitlines())

    def declaration(self):
        docs = self.leading_docs()
        keyword = self.take()
        node = {'kind': keyword.text, 'name': '', 'start': keyword.start, 'end': keyword.end, 'children': [], 'properties': {}}
        props = node['properties']
        if keyword.text == 'package':
            start, end = self.reference()
            self.package = re.sub(r'\s', '', self.text[start:end])
            node['name'] = self.package
            props['name'] = self.property(start, end)
            return self.finish(node, docs, self.take(';'))
        if keyword.text not in ('const', 'alias', 'enum', *KEYWORDS):
            raise ValueError('Unexpected SDL declaration: ' + keyword.text)
        name = self.name()
        node['name'] = name.text
        props['name'] = self.property(name.start, name.end)
        if keyword.text in ('const', 'alias'):
            self.take('=')
            start = self.peek().start
            while self.peek().text not in (';', ''):
                self.take()
            end = self.tokens[self.position - 1].end
            props['expression' if keyword.text == 'const' else 'target'] = self.property(start, end)
            return self.finish(node, docs, self.take(';'))
        self.take('{')
        node['body_start'] = self.tokens[self.position - 1].end
        if keyword.text == 'enum':
            while self.peek().text != '}':
                item_docs = self.leading_docs()
                item = self.name()
                self.take('=')
                value = self.take()
                child = {'kind': 'item', 'name': item.text, 'start': item.start, 'children': [],
                         'properties': {'name': self.property(item.start, item.end), 'value': self.property(value.start, value.end)}}
                node['children'].append(self.finish(child, item_docs, self.take(';')))
        else:
            node['kind'] = 'message'
            props['keyword'] = self.property(keyword.start, keyword.end, choices=list(KEYWORDS))
            self.fields(node)
        node['body_end'] = self.peek().start
        return self.finish(node, docs, self.take('}'))

    def fields(self, parent):
        effective_id = 0
        while self.peek().text != '}':
            docs = self.leading_docs()
            start = self.peek().start
            props = {}
            if self.peek(1).text == ':':
                number = self.take()
                colon = self.take(':')
                effective_id = int(number.text)
                props['id'] = self.property(number.start, colon.end, value=number.text)
            else:
                effective_id += 1
                props['id'] = self.property(start, start)
            modifier = self.peek()
            if modifier.text in MODIFIERS:
                self.take()
                props['modifier'] = self.property(modifier.start, modifier.end, choices=['', *MODIFIERS])
            else:
                props['modifier'] = self.property(modifier.start, modifier.start, choices=['', *MODIFIERS])
            node = {'kind': 'field', 'name': '', 'start': start, 'children': [], 'properties': props, 'effective_id': effective_id}
            type_start = self.peek().start
            anonymous = self.peek().text in KEYWORDS and self.peek(1).text == '{'
            if anonymous:
                keyword = self.take()
                props['keyword'] = self.property(keyword.start, keyword.end, choices=list(KEYWORDS))
                self.take('{')
                node['body_start'] = self.tokens[self.position - 1].end
                self.fields(node)
                node['body_end'] = self.peek().start
                self.take('}')
            else:
                type_start, type_end = self.reference()
                props['type'] = self.property(type_start, type_end)
                dim_start = self.peek().start
                while self.peek().text == '[':
                    self.take()
                    while self.peek().text not in (']', ''):
                        self.take()
                    self.take(']')
                dim_end = self.tokens[self.position - 1].end if self.peek().start != dim_start else dim_start
                props['dimensions'] = self.property(dim_start, dim_end)
            name = self.name()
            node['name'] = name.text
            node['anonymous'] = anonymous
            props['name'] = self.property(name.start, name.end)
            wire_start = name.end
            if self.peek().text == 'wire':
                self.take()
                self.take('(')
                content_start = self.peek().start
                while self.peek().text not in (')', ''):
                    self.take()
                content_end = self.tokens[self.position - 1].end
                closing = self.take(')')
                props['wire'] = self.property(wire_start, closing.end, value=self.text[content_start:content_end])
            else:
                props['wire'] = self.property(wire_start, wire_start)
            parent['children'].append(self.finish(node, docs, self.take(';')))

    def edit(self, path, key, value):
        node = self.index[path]
        prop = node['properties'][key]
        if key == 'doc':
            if '*/' in value:
                raise ValueError('Documentation cannot contain */')
            patches = [(a, b, '') for a, b in node['docs']]
            if patches:
                patches[0] = (*patches[0][:2], '/** ' + value + ' */' if value else '')
            elif value:
                patches = [(node['start'], node['start'], '/** ' + value + ' */\n')]
            return self.patches(patches)
        if key == 'id':
            value = value.strip() + ': ' if value.strip() else ''
        if key == 'modifier' and prop['start'] == prop['end'] and value:
            value += ' '
        if key == 'wire':
            value = ' wire(' + value.strip() + ')' if value.strip() else ''
        if key == 'dimensions' and value:
            value = value.strip()
            if not value.startswith('['):
                value = ''.join('[' + d.strip() + ']' for d in value.split(','))
        return self.patches([(prop['start'], prop['end'], value)])

    def patches(self, patches):
        result = self.text
        for start, end, value in sorted(patches, reverse=True):
            result = result[:start] + value + result[end:]
        return result

    def delete(self, path):
        node = self.index[path]
        ranges = [(node['start'], node['end'], '')] + [(a, b, '') for a, b in node['docs']]
        return self.patches(ranges)

    def add(self, parent, kind, name, value=''):
        if not re.fullmatch(r'[^\W\d]\w*', name):
            raise ValueError('Use a valid SDL name')
        snippets = {'message': f'message {name} {{\n}}', 'enum': f'enum {name} {{\n    READY = 0;\n}}',
                    'const': f'const {name} = {value or "1"};', 'alias': f'alias {name} = {value or "uint64"};',
                    'package': f'package {name};', 'field': f'{value or "fl64"} {name};',
                    'anonymous': f'struct {{\n        fl64 value;\n    }} {name};', 'item': f'{name} = {value or "0"};'}
        if kind not in snippets:
            raise ValueError('Unknown SDL declaration kind')
        position = self.index[parent]['body_end'] if parent else (0 if kind == 'package' else len(self.text))
        snippet = ('    ' if parent else '') + snippets[kind]
        newline = '\r\n' if '\r\n' in self.text else '\n'
        result = self.patches([(position, position, newline + snippet.replace('\n', newline) + newline)])
        SdlSource(result)
        return result

    def comments(self):
        return [{'start': m.start(), 'end': m.end(), 'value': m[0]} for m in TOKEN.finditer(self.text) if m.lastgroup in ('line', 'block')]
