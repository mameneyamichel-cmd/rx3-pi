"""Shared reader for config.env (see config.env.example). Used by flx6-rx3.py.

An environment variable that is already set wins over the file. The file is
$RX3_CONFIG if set, otherwise config.env beside this module.
"""
import os
import re
from pathlib import Path


def parse_env_file(path):
    """Read simple KEY=value lines (shell style, optional quotes, $VAR)."""
    values = {}
    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not sep or not key.isidentifier():
            continue
        if len(value) >= 2 and value[0] in '"\'' and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(' #', 1)[0].strip()
        known = {**os.environ, **values}
        values[key] = re.sub(
            r'\$\{(\w+)\}|\$(\w+)',
            lambda m: known.get(m.group(1) or m.group(2), m.group(0)),
            value,
        )
    return values


def load_settings():
    """Return setting(name, default=None): environment first, then config.env."""
    here = Path(__file__).absolute()
    values = {}
    explicit = os.environ.get('RX3_CONFIG')
    if explicit:
        if not Path(explicit).is_file():
            raise SystemExit(f'RX3_CONFIG file not found: {explicit}')
        values = parse_env_file(explicit)
    else:
        for path in (here.parent / 'config.env', here.resolve().parent / 'config.env'):
            if path.is_file():
                values = parse_env_file(path)
                break
    return lambda name, default=None: os.environ.get(name, values.get(name, default))