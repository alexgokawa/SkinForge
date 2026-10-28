"""Read the game's own SCS archives (HashFS v2, ETS2 / ATS 1.50+) straight from the install folder.

Only what SkinForge needs: plain files (defs, .pmd / .pmg models, .mat) and directory listings.
Packed textures (.tobj + image data) are not unpacked - templates are made from the models.
Format notes (verified on ATS def.scs / dlc_peterbilt_389.scs, Oct 2026):
  header  "SCS#" u16 version=2, u16 salt, "CITY", u32 entries, u32 entry-table zlib size,
          u32 metadata words, u32 metadata zlib size, u64 entry-table offset, u64 metadata offset
  entry   u64 cityhash(path), u32 metadata index, u16 metadata count, u16 flags (bit 0 = directory)
  meta    u32 word: type << 24 | index of the record; type 128 = file, 129 = directory listing,
          record = u32 (zsize | compression << 28), u32 size, u32 -, u32 offset / 16
  listing u32 count, count x u8 name length, names ("/name" = subdirectory)
Pure Python, no extra packages.
"""
from __future__ import annotations

import os
import re
import struct
import zlib

M = 0xFFFFFFFFFFFFFFFF
K0, K1, K2, K3 = 0xc3a5c85c97cb3127, 0xb492b66fbe98f273, 0x9ae16a3b2f90404f, 0xc949d7c7509e6557


def _f64(s, i):
    return int.from_bytes(s[i:i + 8], "little")


def _f32(s, i):
    return int.from_bytes(s[i:i + 4], "little")


def _rot(v, n):
    return v if n == 0 else ((v >> n) | (v << (64 - n))) & M


def _smix(v):
    return v ^ (v >> 47)


def _h16(u, v):
    mul = 0x9ddfea08eb382d69
    a = ((u ^ v) * mul) & M
    a ^= a >> 47
    b = ((v ^ a) * mul) & M
    b ^= b >> 47
    return (b * mul) & M


def _len0to16(s, n):
    if n > 8:
        a, b = _f64(s, 0), _f64(s, n - 8)
        return _h16(a, _rot((b + n) & M, n)) ^ b
    if n >= 4:
        a = _f32(s, 0)
        return _h16((n + (a << 3)) & M, _f32(s, n - 4))
    if n > 0:
        a, b, c = s[0], s[n >> 1], s[n - 1]
        y = (a + (b << 8)) & 0xFFFFFFFF
        z = (n + (c << 2)) & 0xFFFFFFFF
        return (_smix(((y * K2) ^ (z * K3)) & M) * K2) & M
    return K2


def _len17to32(s, n):
    a = (_f64(s, 0) * K1) & M
    b = _f64(s, 8)
    c = (_f64(s, n - 8) * K2) & M
    d = (_f64(s, n - 16) * K0) & M
    return _h16((_rot((a - b) & M, 43) + _rot(c, 30) + d) & M, (a + _rot(b ^ K3, 20) - c + n) & M)


def _len33to64(s, n):
    z = _f64(s, 24)
    a = (_f64(s, 0) + (n + _f64(s, n - 16)) * K0) & M
    b = _rot((a + z) & M, 52)
    c = _rot(a, 37)
    a = (a + _f64(s, 8)) & M
    c = (c + _rot(a, 7)) & M
    a = (a + _f64(s, 16)) & M
    vf = (a + z) & M
    vs = (b + _rot(a, 31) + c) & M
    a = (_f64(s, 16) + _f64(s, n - 32)) & M
    z = _f64(s, n - 8)
    b = _rot((a + z) & M, 52)
    c = _rot(a, 37)
    a = (a + _f64(s, n - 24)) & M
    c = (c + _rot(a, 7)) & M
    a = (a + _f64(s, n - 16)) & M
    wf = (a + z) & M
    ws = (b + _rot(a, 31) + c) & M
    r = _smix(((vf + ws) * K2 + (wf + vs) * K0) & M)
    return (_smix((r * K0 + vs) & M) * K2) & M


def _weak(s, i, a, b):
    w, x, y, z = _f64(s, i), _f64(s, i + 8), _f64(s, i + 16), _f64(s, i + 24)
    a = (a + w) & M
    b = _rot((b + a + z) & M, 21)
    c = a
    a = (a + x + y) & M
    b = (b + _rot(a, 44)) & M
    return (a + z) & M, (b + c) & M


def cityhash64(s: bytes) -> int:
    n = len(s)
    if n <= 32:
        return _len0to16(s, n) if n <= 16 else _len17to32(s, n)
    if n <= 64:
        return _len33to64(s, n)
    x = _f64(s, n - 40)
    y = (_f64(s, n - 16) + _f64(s, n - 56)) & M
    z = _h16((_f64(s, n - 48) + n) & M, _f64(s, n - 24))
    v = _weak(s, n - 64, n, z)
    w = _weak(s, n - 32, (y + K1) & M, x)
    x = (x * K1 + _f64(s, 0)) & M
    left = (n - 1) & ~63
    i = 0
    while True:
        x = (_rot((x + y + v[0] + _f64(s, i + 8)) & M, 37) * K1) & M
        y = (_rot((y + v[1] + _f64(s, i + 48)) & M, 42) * K1) & M
        x ^= w[1]
        y = (y + v[0] + _f64(s, i + 40)) & M
        z = (_rot((z + w[0]) & M, 33) * K1) & M
        v = _weak(s, i, (v[1] * K1) & M, (x + w[0]) & M)
        w = _weak(s, i + 32, (z + w[1]) & M, (y + _f64(s, i + 16)) & M)
        z, x = x, z
        i += 64
        left -= 64
        if left == 0:
            break
    return _h16((_h16(v[0], w[0]) + _smix(y) * K1 + z) & M, (_h16(v[1], w[1]) + x) & M)



class ArchiveError(Exception):
    pass


T_FILE, T_DIR = 128, 129


class HashFS2:
    """HashFS v2 archive. read(path) -> bytes or None, listdir(dir) -> ["name", "sub/"]."""

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "rb")
        head = self._f.read(64)
        if len(head) < 52 or head[:4] != b"SCS#":
            self._f.close()
            raise ArchiveError("not an SCS archive")
        _, ver, salt, method, ecount, elen, mcount, mlen, estart, mstart = struct.unpack_from("<4sHH4sIIIIQQ", head)
        if ver != 2 or method != b"CITY":
            self._f.close()
            raise ArchiveError(f"SCS archive version {ver} - SkinForge reads version 2 (game 1.50+)")
        self.salt = salt
        self._f.seek(estart)
        et = zlib.decompress(self._f.read(elen))
        self._f.seek(mstart)
        self._m = zlib.decompress(self._f.read(mlen))
        n = min(ecount, len(et) // 16)
        self._e = {h: (mi, mc, fl) for h, mi, mc, fl in struct.iter_unpack("<QIHH", et[:16 * n])}
        self._dirs: dict = {}

    def _hash(self, p: str) -> int:
        p = p.replace("\\", "/").strip("/").lower()
        if self.salt:
            p = str(self.salt) + p
        return cityhash64(p.encode("utf-8"))

    def _u(self, i):
        return struct.unpack_from("<I", self._m, 4 * i)[0]

    def _record(self, p, want):
        e = self._e.get(self._hash(p))
        if e is None:
            return None
        mi, mc, fl = e
        for k in range(mc):
            w = self._u(mi + k)
            if w >> 24 == want:
                return w & 0xFFFFFF
        return None

    def _data(self, idx):
        zs, size, _, block = (self._u(idx + j) for j in range(4))
        comp, zs = zs >> 28, zs & 0x0FFFFFFF
        self._f.seek(block * 16)
        raw = self._f.read(zs)
        if comp == 0:
            return raw
        if comp == 1:
            return zlib.decompress(raw)
        return None                       # other compressions are only used for packed textures

    def exists(self, p: str) -> bool:
        return self._record(p, T_FILE) is not None

    def is_dir(self, p: str) -> bool:
        return self._record(p, T_DIR) is not None

    def read(self, p: str):
        idx = self._record(p, T_FILE)
        return None if idx is None else self._data(idx)

    def stat(self, p: str):
        idx = self._record(p, T_FILE)
        if idx is None:
            return None
        return (self.path, self._u(idx + 3), self._u(idx + 1))

    def listdir(self, d: str):
        k = d.replace("\\", "/").strip("/").lower()
        if k in self._dirs:
            return self._dirs[k]
        out = []
        idx = self._record(k, T_DIR)
        data = self._data(idx) if idx is not None else None
        if data and len(data) >= 4:
            n = struct.unpack_from("<I", data)[0]
            o = 4 + n
            for ln in data[4:4 + n]:
                name = data[o:o + ln].decode("utf-8", "replace").lower()
                o += ln
                out.append(name[1:] + "/" if name.startswith("/") else name)
        self._dirs[k] = sorted(set(out))
        return self._dirs[k]

    def close(self):
        self._f.close()


# ---------------------------------------------------------------- game install

GAME_DIRS = {"ets2": "Euro Truck Simulator 2", "ats": "American Truck Simulator"}
_SKIP = {"base_cfg.scs", "base_map.scs", "core.scs", "effect.scs", "locale.scs", "version.scs"}


def is_game_install(path: str) -> bool:
    try:
        names = {n.lower() for n in os.listdir(path)}
    except OSError:
        return False
    return "def.scs" in names and "base.scs" in names


def game_of_install(path: str) -> str | None:
    """'ets2' / 'ats' from the install folder (by name, else by the exe next to it)."""
    base = os.path.basename(path.rstrip("/\\")).lower()
    for g, n in GAME_DIRS.items():
        if base == n.lower():
            return g
    for g, exe in (("ets2", "eurotrucks2.exe"), ("ats", "amtrucks.exe")):
        if os.path.isfile(os.path.join(path, "bin", "win_x64", exe)):
            return g
    return None


def archive_order(path: str) -> list[str]:
    """The archives SkinForge mounts, highest priority first: DLC over base, as in the game.
    Map, locale, effect and the like are skipped - nothing in them is a vehicle."""
    try:
        names = sorted(n for n in os.listdir(path) if n.lower().endswith(".scs") and n.lower() not in _SKIP)
    except OSError:
        return []
    dlc = [n for n in names if n.lower().startswith("dlc_")]
    base = [n for n in ("def.scs", "base_vehicle.scs", "base_share.scs", "base.scs") if n in names]
    rest = [n for n in names if n not in dlc and n not in base]
    return [os.path.join(path, n) for n in dlc + rest + base]


def _steam_roots() -> list[str]:
    roots = []
    try:
        import winreg
        for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(winreg.QueryValueEx(k, val)[0])
            except OSError:
                pass
    except ImportError:
        pass
    roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    libs = []
    for r in roots:
        r = os.path.normpath(r)
        if r not in libs and os.path.isdir(r):
            libs.append(r)
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                    p = os.path.normpath(m.group(1).replace("\\\\", "\\"))
                    if p not in libs and os.path.isdir(p):
                        libs.append(p)
        except OSError:
            pass
    return libs


def find_game_installs() -> dict:
    """{'ets2': path, 'ats': path} for the Steam installs found on this PC."""
    out = {}
    for lib in _steam_roots():
        for g, n in GAME_DIRS.items():
            p = os.path.join(lib, "steamapps", "common", n)
            if g not in out and is_game_install(p):
                out[g] = p
    return out
