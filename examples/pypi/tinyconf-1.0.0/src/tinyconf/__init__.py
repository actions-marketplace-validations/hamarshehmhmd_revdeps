"""tinyconf 1.0.0 (released): every value is a string."""


def load(text):
    out = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out
