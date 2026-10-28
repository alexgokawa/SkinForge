"""Minimal tolerant parser for plain-text SiiNunit (.sii/.sui) files.

Handles units, scalar attributes, array attributes (key[]: value and key[N]: value),
comments (#, //, /* */) and @include (resolved through a loader callback).
Encrypted/binary .sii (ScsC / BSII) are detected and reported as unreadable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_UNIT_HEAD = re.compile(r"^\s*([A-Za-z0-9_]+)\s*:\s*([A-Za-z0-9_.]+)\s*\{?\s*$")
_ATTR = re.compile(r"^\s*([A-Za-z0-9_]+)(\[(\d*)\])?\s*:\s*(.*?)\s*$")


@dataclass
class Unit:
    cls: str
    name: str
    attrs: dict = field(default_factory=dict)   # scalars: str, arrays: list[str]

    def get(self, key, default=None):
        v = self.attrs.get(key, default)
        return v

    def get_str(self, key, default=None):
        v = self.attrs.get(key)
        if v is None or isinstance(v, list):
            return default
        return unquote(v)

    def get_list(self, key):
        v = self.attrs.get(key)
        if v is None:
            return []
        if isinstance(v, list):
            return [unquote(x) for x in v]
        return [unquote(v)]


def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]
    return v


def is_text_sii(data: bytes) -> bool:
    head = data[:8]
    if head.startswith(b"ScsC") or head.startswith(b"BSII"):
        return False
    return True


def _strip_line_comment(line: str) -> str:
    out, in_str = [], False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            in_str = not in_str
        elif not in_str:
            if c == "#":
                break
            if c == "/" and i + 1 < len(line) and line[i + 1] == "/":
                break
        out.append(c)
        i += 1
    return "".join(out)


def decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def expand_includes(text: str, base_dir: str, loader: Optional[Callable[[str], Optional[bytes]]], depth=0) -> str:
    if loader is None or depth > 8:
        return text
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("@include"):
            m = re.match(r'@include\s+"([^"]+)"', s)
            if m:
                inc = m.group(1)
                path = inc if inc.startswith("/") else (base_dir.rstrip("/") + "/" + inc)
                data = loader(path)
                if data is not None and is_text_sii(data):
                    inc_dir = path.rsplit("/", 1)[0]
                    out.append(expand_includes(decode(data), inc_dir, loader, depth + 1))
                continue
        out.append(line)
    return "\n".join(out)


def parse(text: str) -> list[Unit]:
    text = _BLOCK_COMMENT.sub("", text)
    units: list[Unit] = []
    cur: Optional[Unit] = None
    pending_head = None
    for raw in text.splitlines():
        line = _strip_line_comment(raw).strip()
        if not line:
            continue
        if line.startswith("SiiNunit"):
            continue
        if cur is None:
            if line == "{" and pending_head:
                cur = Unit(*pending_head)
                pending_head = None
                continue
            m = _UNIT_HEAD.match(line)
            if m:
                if line.rstrip().endswith("{"):
                    cur = Unit(m.group(1), m.group(2))
                else:
                    pending_head = (m.group(1), m.group(2))
            continue
        if line == "}" or line.startswith("}"):
            units.append(cur)
            cur = None
            continue
        if line == "{":
            continue
        m = _ATTR.match(line)
        if not m:
            continue
        key, arr, idx, val = m.group(1), m.group(2), m.group(3), m.group(4)
        if arr is not None:
            lst = cur.attrs.get(key)
            if not isinstance(lst, list):
                lst = []
                cur.attrs[key] = lst
            if idx:
                i = int(idx)
                while len(lst) <= i:
                    lst.append("")
                lst[i] = val
            else:
                lst.append(val)
        else:
            cur.attrs[key] = val
    return units


def parse_file(data: bytes, path: str = "/", loader=None) -> list[Unit]:
    if not is_text_sii(data):
        return []
    text = decode(data)
    base_dir = path.rsplit("/", 1)[0] if "/" in path else "/"
    text = expand_includes(text, base_dir, loader)
    return parse(text)


def parse_color(v: str):
    """'(0.8, 0.8, 0.8)' -> (0.8,0.8,0.8). Hex-float (&3f4ccccd) supported."""
    if v is None:
        return None
    nums = re.findall(r"&[0-9a-fA-F]{8}|-?[0-9]*\.?[0-9]+(?:[eE]-?\d+)?", v)
    out = []
    import struct
    for n in nums:
        if n.startswith("&"):
            out.append(struct.unpack(">f", bytes.fromhex(n[1:]))[0])
        else:
            out.append(float(n))
    return tuple(out)
