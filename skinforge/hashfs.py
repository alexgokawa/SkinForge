"""Read files by path from SCS HashFS v1 archives ("SCS#", CityHash64 path hashes).

Base-game archives and most paid / locked mods use this format. Locked mods drop the directory
listings and pad the table with fake entries, so files are found by hashing their known path
(the paths come from the defs/models that reference them). Pure Python, no extra packages.
"""
from __future__ import annotations

import struct
import zlib

from .scsarchive import cityhash64  # noqa: F401  (shared with the public game-archive reader)


class HashFSError(Exception):
    pass


class HashFS:
    """HashFS v1 reader. read(path) -> bytes or None; listdir works only where the archive still
    has its directory listings (unlocked archives)."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "rb")
        head = self._f.read(20)
        if head[:4] != b"SCS#":
            raise HashFSError("not a HashFS archive")
        magic, ver, salt, method, count, start = struct.unpack("<4sHH4sII", head)
        if ver != 1:
            raise HashFSError(f"HashFS version {ver} is not supported yet (SkinForge reads version 1)")
        if method != b"CITY":
            raise HashFSError("unknown HashFS hash method")
        self.salt = salt
        self._f.seek(start)
        table = self._f.read(32 * count)
        self._e = {}
        for i in range(min(count, len(table) // 32)):
            h, off, flags, crc, size, zsize = struct.unpack_from("<QQIIII", table, 32 * i)
            self._e[h] = (off, flags, size, zsize)

    def _hash(self, p: str) -> int:
        p = p.replace("\\", "/").strip("/")
        if self.salt:
            p = str(self.salt) + p
        return cityhash64(p.encode("utf-8"))

    def _entry(self, p):
        return self._e.get(self._hash(p))

    def exists(self, p: str) -> bool:
        e = self._entry(p)
        return e is not None and not (e[1] & 1)

    def _raw(self, e):
        off, flags, size, zsize = e
        self._f.seek(off)
        data = self._f.read(zsize)
        if flags & 2:
            try:
                data = zlib.decompress(data)
            except zlib.error:
                try:
                    data = zlib.decompressobj(-15).decompress(data)
                except zlib.error:
                    return None
        return data

    def read(self, p: str):
        e = self._entry(p)
        if e is None or (e[1] & 1):
            return None
        return self._raw(e)

    def stat(self, p: str):
        e = self._entry(p)
        if e is None:
            return None
        return (self.path, e[0], e[2], e[3])

    def listdir(self, d: str):
        """Directory listing ("name" / "name/" entries), or [] if the archive has none."""
        e = self._entry(d)
        if e is None or not (e[1] & 1):
            return []
        data = self._raw(e)
        if not data:
            return []
        out = []
        for line in data.decode("utf-8", "replace").splitlines():
            if not line:
                continue
            out.append(line[1:].lower() + "/" if line.startswith("*") else line.lower())
        return sorted(set(out))

    def close(self):
        self._f.close()
