"""Shared exact-name, ordinal, SDL-type and glob recording selection."""
from fnmatch import fnmatchcase


def ordered(points):
    return sorted(points, key=lambda key: (points[key].get("topic", key), points[key].get("kind", "output")))


def resolve(points, patterns, *, selected=None, match_types=True, kind=None):
    names = ordered(points)
    candidates = [name for name in names if (selected is None or name in selected)
                  and (kind is None or points[name].get("kind", "output") == kind)]
    found = set()
    for pattern in patterns:
        if pattern == "all":
            matches = candidates
        elif pattern.isdigit():
            number = int(pattern)
            matches = [names[number - 1]] if 1 <= number <= len(names) and names[number - 1] in candidates else []
        else:
            matches = [name for name in candidates if fnmatchcase(points.get(name, {}).get("topic", name), pattern) or
                       (match_types and fnmatchcase(points.get(name, {}).get("root", ""), pattern))]
        if not matches:
            raise ValueError(f"no recording point matches {pattern!r}")
        found.update(matches)
    return [name for name in names if name in found]
