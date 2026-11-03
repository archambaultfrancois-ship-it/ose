"""Bounded component log transport and volatile runtime history."""
from collections import deque
from fnmatch import fnmatchcase
import json
import struct
import time
import uuid


LEVELS = {10: 'DEBUG', 20: 'INFO', 30: 'WARNING', 40: 'ERROR', 50: 'CRITICAL', 60: 'FATAL'}
MAX_MESSAGE = 8192
TRUNCATION_SUFFIX = '… [truncated]'
MAX_LOGGER = 200
MAX_BATCH = 32


def level_number(level):
    if isinstance(level, str):
        names = {name: number for number, name in LEVELS.items()}
        names.update(WARN=30)
        level = names.get(level.upper())
    if type(level) is not int or level not in LEVELS:
        raise ValueError('log level must be DEBUG, INFO, WARNING, ERROR, CRITICAL or FATAL')
    return level


def validate_record(record):
    if not isinstance(record, dict):
        raise ValueError('log entry must be an object')
    level = level_number(record.get('level'))
    logger, message = record.get('logger', ''), record.get('message')
    if not isinstance(logger, str) or len(logger.encode()) > MAX_LOGGER or '\0' in logger:
        raise ValueError('log category must be UTF-8 text of at most 200 bytes without NUL')
    if not isinstance(message, str) or not message or len(message.encode()) > MAX_MESSAGE or '\0' in message:
        raise ValueError('log message must be nonempty UTF-8 text of at most 8192 bytes without NUL')
    return {'level': LEVELS[level], 'logger': logger, 'message': message}


def log_body(level, message, logger=''):
    if isinstance(message, str) and '\0' not in message:
        encoded = message.encode()
        if len(encoded) > MAX_MESSAGE:
            limit = MAX_MESSAGE - len(TRUNCATION_SUFFIX.encode())
            message = encoded[:limit].decode('utf-8', errors='ignore') + TRUNCATION_SUFFIX
    value = validate_record(dict(level=level, message=message, logger=logger))
    name = value['logger'].encode()
    return bytes([level_number(level)]) + struct.pack('>H', len(name)) + name + message.encode()


class ComponentLogs:
    """The IPC reader never waits for a remote log consumer."""
    def __init__(self):
        self.pending = deque()
        self.bytes = self.dropped = 0

    def emit(self, body):
        if len(body) < 4:
            raise ValueError('truncated log frame')
        size = struct.unpack('>H', body[1:3])[0]
        record = validate_record(dict(level=body[0], logger=body[3:3 + size].decode(),
                                      message=body[3 + size:].decode()))
        if size > MAX_LOGGER or len(body) > MAX_MESSAGE + MAX_LOGGER + 3:
            raise ValueError('oversized log frame')
        if len(self.pending) >= 128 or self.bytes + len(body) > 256 * 1024:
            self.dropped += 1
            return
        record['time_ns'] = str(time.time_ns())
        self.pending.append((record, len(body)))
        self.bytes += len(body)

    def drain(self):
        records = []
        size = 0
        while self.pending and len(records) < MAX_BATCH:
            record, cost = self.pending[0]
            if records and size + cost > 64 * 1024:
                break
            self.pending.popleft()
            self.bytes -= cost
            size += cost
            records.append(record)
        return records


class LogStore:
    def __init__(self, epoch=None, capacity=10000, byte_budget=8 * 1024 * 1024):
        if capacity < 1 or byte_budget < 16384:
            raise ValueError('invalid log retention limits')
        self.epoch = epoch or uuid.uuid4().hex
        self.capacity, self.byte_budget = capacity, byte_budget
        self.records = deque()
        self.sequence = self.bytes = self.evicted = 0

    def append(self, component, generation, entries):
        if not isinstance(entries, list) or len(entries) > MAX_BATCH:
            raise ValueError('log batch must contain at most 32 records')
        prepared = []
        for entry in entries:
            record = validate_record(entry)
            stamp = entry.get('time_ns')
            if not isinstance(stamp, str) or not stamp.isascii() or not stamp.isdecimal() or len(stamp) > 20:
                raise ValueError('log timestamp must be an unsigned nanosecond string')
            prepared.append(dict(record, time_ns=stamp, component=component, generation=generation))
        for record in prepared:
            self.sequence += 1
            record['sequence'] = str(self.sequence)
            cost = len(json.dumps(record, ensure_ascii=False).encode())
            self.records.append((record, cost))
            self.bytes += cost
            while len(self.records) > self.capacity or self.bytes > self.byte_budget:
                _, cost = self.records.popleft()
                self.bytes -= cost
                self.evicted += 1

    def query(self, *, components=None, scopes=None, levels=None, search='', logger='*', after=None, epoch=None, limit=200):
        components = ['*'] if components is None else components
        scopes = ['*'] if scopes is None else scopes
        for patterns in (components, scopes):
            if not isinstance(patterns, list) or len(patterns) > 1000 or not all(isinstance(p, str) and len(p) <= 200 for p in patterns):
                raise ValueError('invalid component filters')
        if levels is not None and (not isinstance(levels, list) or len(levels) > len(LEVELS)):
            raise ValueError('invalid log levels')
        levels = {LEVELS[level_number(level)] for level in levels} if levels else set(LEVELS.values())
        if not isinstance(search, str) or len(search) > 500 or not isinstance(logger, str) or len(logger) > 200:
            raise ValueError('invalid log text/category filter')
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('log limit must be between 1 and 200')
        if after is not None and (not isinstance(after, str) or not after.isascii() or not after.isdecimal() or len(after) > 20):
            raise ValueError('invalid log cursor')
        reset = epoch is not None and epoch != self.epoch
        position = None if reset or after is None else int(after)
        if position is not None and position > self.sequence:
            raise ValueError('log cursor is ahead of this runtime; provide its epoch')
        oldest = int(self.records[0][0]['sequence']) if self.records else self.sequence + 1
        truncated = reset or position is not None and position < oldest - 1
        def matches(record):
            return (record['level'] in levels and any(fnmatchcase(record['component'], p) for p in components)
                    and any(fnmatchcase(record['component'], p) for p in scopes)
                    and fnmatchcase(record['logger'], logger) and search.casefold() in record['message'].casefold())
        if position is None:
            selected, size = [], 0
            for record, _ in reversed(self.records):
                if matches(record):
                    cost = len(json.dumps(record).encode())
                    if len(selected) == limit or size + cost > 192 * 1024:
                        break
                    selected.append(record)
                    size += cost
            selected.reverse()
            cursor = self.sequence
        else:
            selected, cursor, size = [], self.sequence, 0
            for record, _ in self.records:
                number = int(record['sequence'])
                if number > position and matches(record):
                    cost = len(json.dumps(record).encode())
                    if size + cost > 192 * 1024:
                        cursor = int(selected[-1]['sequence'])
                        break
                    selected.append(record)
                    size += cost
                    if len(selected) == limit:
                        cursor = number
                        break
        return {'epoch': self.epoch, 'items': selected, 'cursor': str(cursor), 'more': cursor < self.sequence,
                'truncated': bool(truncated), 'evicted': self.evicted, 'retained': len(self.records)}
