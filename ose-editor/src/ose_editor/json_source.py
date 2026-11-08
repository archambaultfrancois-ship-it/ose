"""Lossless JSON ranges and minimal structural edits; values never cross JS numbers."""
from dataclasses import dataclass, field
from decimal import Decimal
import json
import re


def loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('Non-finite JSON number: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=invalid)


def dump(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k, ensure_ascii=False) + ': ' + dump(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(map(dump, value)) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def web(value):
    if isinstance(value, Decimal) or isinstance(value, int) and not isinstance(value, bool) and abs(value) > 9007199254740991:
        return {'$number': str(value)}
    if isinstance(value, dict):
        return {k: web(v) for k, v in value.items()}
    if isinstance(value, list):
        return [web(v) for v in value]
    return value


@dataclass
class Node:
    start: int
    end: int
    value: object
    children: dict = field(default_factory=dict)
    keys: dict = field(default_factory=dict)


class JsonSource:
    def __init__(self, text):
        self.text = text
        loads(text)  # Enforce JSON syntax, exact numbers and duplicate-key checks.
        self.root, position = self._parse(0)
        if text[position:].strip():
            raise ValueError('Unexpected content after JSON document')

    def _space(self, position):
        while position < len(self.text) and self.text[position].isspace():
            position += 1
        return position

    def _parse(self, position):
        start = position = self._space(position)
        char = self.text[position]
        if char in '{[':
            is_object = char == '{'
            children, keys, values = {}, {}, {} if is_object else []
            position = self._space(position + 1)
            while self.text[position] != ('}' if is_object else ']'):
                key = str(len(children))
                if is_object:
                    key_node, position = self._parse(position)
                    key = key_node.value
                    keys[key] = (key_node.start, key_node.end)
                    position = self._space(position) + 1
                child, position = self._parse(position)
                children[key] = child
                if is_object:
                    values[key] = child.value
                else:
                    values.append(child.value)
                position = self._space(position)
                if self.text[position] == ',':
                    position = self._space(position + 1)
                else:
                    break
            return Node(start, position + 1, values, children, keys), position + 1
        if char == '"':
            position += 1
            while self.text[position] != '"':
                position += 2 if self.text[position] == '\\' else 1
            end = position + 1
        else:
            match = re.match(r'[^\s,\]}]+', self.text[position:])
            end = position + len(match[0])
        return Node(start, end, loads(self.text[start:end])), end

    def node(self, path):
        result = self.root
        for key in path:
            result = result.children[str(key)]
        return result

    def patch(self, start, end, value):
        return self.text[:start] + value + self.text[end:]

    def set(self, path, literal):
        loads(literal)
        try:
            node = self.node(path)
        except KeyError:
            parent = self.node(path[:-1])
            if not isinstance(parent.value, (dict, list)):
                raise ValueError('The parent is not a collection')
            key = str(path[-1])
            if isinstance(parent.value, list) and key not in ('-', str(len(parent.children))):
                raise ValueError('Array insertion must append or use a valid existing index')
            insertion = (json.dumps(key, ensure_ascii=False) + ': ' if isinstance(parent.value, dict) else '') + literal
            newline = '\r\n' if '\r\n' in self.text else '\n'
            multiline = '\n' in self.text[parent.start:parent.end]
            if multiline:
                line = self.text.rfind('\n', 0, parent.start) + 1
                indent = re.match(r'[ \t]*', self.text[line:])[0]
                child_indent = indent + '  '
                if parent.children:
                    first = next(iter(parent.children))
                    child_start = parent.keys[first][0] if parent.keys else parent.children[first].start
                    child_line = self.text.rfind('\n', 0, child_start) + 1
                    child_indent = self.text[child_line:child_start]
                position = parent.end - 1
                while position > parent.start + 1 and self.text[position - 1].isspace():
                    position -= 1
                return self.patch(position, position, (',' if parent.children else '') + newline + child_indent + insertion)
            return self.patch(parent.end - 1, parent.end - 1, (', ' if parent.children else '') + insertion)
        return self.patch(node.start, node.end, literal)

    def delete(self, path):
        if not path:
            raise ValueError('Cannot delete the document root')
        parent, key = self.node(path[:-1]), str(path[-1])
        keys = list(parent.children)
        index = keys.index(key)
        child = parent.children[key]
        start = parent.keys[key][0] if parent.keys else child.start
        end = child.end
        if index + 1 < len(keys):
            following = keys[index + 1]
            end = parent.keys[following][0] if parent.keys else parent.children[following].start
        elif index:
            previous = parent.children[keys[index - 1]]
            start = self.text.index(',', previous.end, start)
        return self.patch(start, end, '')

    def rename(self, path, name):
        parent, old = self.node(path[:-1]), str(path[-1])
        if name in parent.children and name != old:
            raise ValueError('Name already exists: ' + name)
        start, end = parent.keys[old]
        return self.patch(start, end, json.dumps(name, ensure_ascii=False))

    def tree(self, path=()):
        node = self.node(path)
        kind = 'object' if isinstance(node.value, dict) else 'array' if isinstance(node.value, list) else 'boolean' if isinstance(node.value, bool) else 'null' if node.value is None else 'string' if isinstance(node.value, str) else 'number'
        return {'path': list(path), 'kind': kind, 'literal': self.text[node.start:node.end] if not node.children else '',
                'start': node.start, 'end': node.end,
                'children': [{'name': key, **self.tree((*path, key))} for key in node.children]}
