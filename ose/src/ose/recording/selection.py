"""Shared exact-name, ordinal, SDL-type and glob recording selection."""
from fnmatch import fnmatchcase


def resolve(points, patterns, *, selected=None, match_types=True):
    names = sorted(points)
    candidates = sorted(selected) if selected is not None else names
    found = set()
    for pattern in patterns:
        if pattern == "all":
            matches = candidates
        elif pattern.isdigit():
            number = int(pattern)
            matches = [names[number - 1]] if 1 <= number <= len(names) else []
        else:
            matches = [name for name in candidates if fnmatchcase(name, pattern) or
                       (match_types and fnmatchcase(points.get(name, {}).get("root", ""), pattern))]
        if not matches:
            raise ValueError(f"no recording point matches {pattern!r}")
        found.update(matches)
    return sorted(found)
