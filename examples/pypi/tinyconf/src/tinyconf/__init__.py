"""tinyconf 2.0.0.dev0 (your change): values are typed - "true" -> True, "3" -> 3."""


def _coerce(value):
    low = value.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        return value


def load(text):
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = _coerce(value.strip())
    return out
