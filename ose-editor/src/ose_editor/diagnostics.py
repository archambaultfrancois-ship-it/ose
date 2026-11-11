"""Preserve compiler messages and attach reliable, one-based source locations."""
import json
import re


def source_diagnostic(error, file, text='', origin='syntax', revision=None):
    result = {'severity': 'error', 'origin': origin, 'file': file, 'message': str(error)}
    if revision is not None:
        result['revision'] = revision
    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause, json.JSONDecodeError):
        result.update(line=cause.lineno, column=cause.colno)
    elif getattr(cause, 'problem_mark', None) is not None:
        result.update(line=cause.problem_mark.line + 1, column=cause.problem_mark.column + 1)
    elif re.search(r'\boffset (\d+)\b', str(error)) and text:
        offset = int(re.search(r'\boffset (\d+)\b', str(error))[1])
        if 0 <= offset <= len(text):
            result.update(line=text.count('\n', 0, offset) + 1,
                          column=offset - text.rfind('\n', 0, offset))
    else:
        position = re.search(r'\bline (\d+)(?:[, :]*(?:column|col) (\d+))?', str(error), re.I)
        if position:
            result['line'] = int(position[1])
            if position[2]:
                result['column'] = int(position[2])
    return result
