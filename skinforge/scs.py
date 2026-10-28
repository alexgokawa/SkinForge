"""Read access to SCS mod sources: ZIP-format .scs/.zip archives and unpacked folders.

HashFS (SCS#) archives - the format of base-game files and some mods - are detected
and reported, not read.
"""
from __future__ import annotations

import os
import zipfile


class SourceError(Exception):
    pass


def archive_kind(path: str) -> str:
    if os.path.isdir(path):
        return "folder"
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError as e:
        raise SourceError(str(e))
    if head[:2] == b"PK":
        return "zip"
    if head == b"SCS#":
        return "hashfs"
    return "unknown"


class Source:
    """Uniform read-only view: paths are '/def/...' style (leading slash, forward slashes)."""

    def __init__(self, path: str):
        self.path = path
        self.kind = archive_kind(path)
        self._zip = None
        self._index: dict[str, str] = {}
        raw = {}
        if self.kind == "zip":
            self._zip = zipfile.ZipFile(path)
            for n in self._zip.namelist():
                if n.endswith("/"):
                    continue
                raw["/" + n.replace("\\", "/").lstrip("/").lower()] = n
        elif self.kind == "folder":
            for root, _dirs, files in os.walk(path):
                for fn in files:
                    full = os.path.join(root, fn)
                    rel = os.path.relpath(full, path).replace("\\", "/")
                    raw["/" + rel.lower()] = full
        if self.kind in ("zip", "folder"):
            self._index = _unpackage(raw)
        elif self.kind == "hashfs":
            raise SourceError("HashFS archive (SCS#) - not readable; use the mod's Open Definition / ZIP version")
        else:
            raise SourceError("Not a ZIP .scs archive or folder")

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("/\\"))

    def files(self):
        return self._index.keys()

    def stat(self, p: str):
        key = self._index.get(self._norm(p))
        if key is None:
            return None
        if self._zip is not None:
            zi = self._zip.getinfo(key)
            return (self.path, key, zi.file_size, zi.CRC)
        st = os.stat(key)
        return (key, st.st_size, int(st.st_mtime))

    def exists(self, p: str) -> bool:
        if p.lower().startswith(VIRTUAL):
            return p.lower() in VIRTUAL_FILES
        return self._norm(p) in self._index

    def _norm(self, p: str) -> str:
        p = p.replace("\\", "/")
        if not p.startswith("/"):
            p = "/" + p
        return p.lower()

    def read(self, p: str):
        if p.lower().startswith(VIRTUAL):
            return read_virtual(p)
        key = self._index.get(self._norm(p))
        if key is None:
            return None
        if self._zip is not None:
            return self._zip.read(key)
        with open(key, "rb") as f:
            return f.read()

    def listdir(self, d: str):
        d = self._norm(d).rstrip("/") + "/"
        out = set()
        for k in self._index:
            if k.startswith(d):
                rest = k[len(d):]
                out.add(rest.split("/", 1)[0] + ("/" if "/" in rest else ""))
        return sorted(out)

    def close(self):
        if self._zip is not None:
            self._zip.close()


def _unpackage(raw: dict) -> dict:
    """Mod packages with DLC-dependent folders (manifest.sii + base/ + dlc_<name>/, e.g. Eugene's
    Scania NG): the game mounts base/ and the folder of every owned DLC at the root. Mounted the
    same way here (DLC folders on top of base). Normal mods are returned unchanged."""
    if "/manifest.sii" not in raw or any(k.startswith(("/def/", "/vehicle/")) for k in raw):
        return raw
    tops = {k.split("/")[1] for k in raw if k.count("/") >= 2}
    mods = sorted(t for t in tops if t == "base" or t.startswith("dlc_"))
    if "base" not in mods:
        return raw
    mods.remove("base")
    out = {k: v for k, v in raw.items() if k.count("/") == 1}
    for t in ["base"] + mods:
        pre = f"/{t}/"
        for k, v in raw.items():
            if k.startswith(pre):
                out["/" + k[len(pre):]] = v
    return out


VIRTUAL_FILES: dict = {}    # generated files (templates from 3D models): path -> maker() -> bytes
_VCACHE: dict = {}


def register_virtual(path: str, make):
    VIRTUAL_FILES[path.lower()] = make
    _VCACHE.pop(path.lower(), None)


def read_virtual(p: str):
    lp = p.replace("\\", "/").lower()
    if lp not in VIRTUAL_FILES:
        return None
    if lp not in _VCACHE:
        try:
            _VCACHE[lp] = VIRTUAL_FILES[lp]()
        except Exception:
            import traceback
            traceback.print_exc()
            _VCACHE[lp] = None
    return _VCACHE[lp]


GAME_TOP = {"vehicle", "automat", "material", "model", "model2", "unit", "prefab", "prefab2", "sound", "def"}
VIRTUAL = "/@skinforge/"


def _has_def(d: str) -> bool:
    return os.path.isdir(os.path.join(d, "def", "vehicle"))


def _children(d: str):
    try:
        return [os.path.join(d, n) for n in sorted(os.listdir(d)) if os.path.isdir(os.path.join(d, n))]
    except OSError:
        return []


def is_game_folder(path: str) -> bool:
    """An SCS game extract: def.scs extracted (a def\\vehicle folder) in this folder or one of its
    subfolders (ATS: <folder>\\def + <folder>\\base_vehicle; ETS2 backup: <folder>\\game files\\def +
    <folder>\\base_vehicle)."""
    if not os.path.isdir(path):
        return False
    if _has_def(path):
        return True
    return any(_has_def(c) for c in _children(path))


def game_root_for(path: str) -> str | None:
    """The user may pick the game install folder, the extract folder itself, or one of its
    subfolders (def, base_vehicle)."""
    from .scsarchive import is_game_install
    if is_game_install(path):
        return path
    if is_game_folder(path):
        return path
    parent = os.path.dirname(os.path.normpath(path))
    if parent and is_game_folder(parent):
        return parent
    return None


def _looks_extracted(d: str) -> bool:
    try:
        return any(n.lower() in GAME_TOP and os.path.isdir(os.path.join(d, n)) for n in os.listdir(d))
    except OSError:
        return False


class GameSource:
    """Extracted SCS game archives (SCS Extractor output), read lazily.

    <root>/def/...           -> /def/...      (def.scs)
    <root>/base_vehicle/...  -> /...          (base_vehicle.scs; same for every other extracted archive)
    Nothing is indexed up front - the extract has tens of thousands of files and often sits on
    a cloud drive. Generated files (templates made from the 3D models) live under /@skinforge/.
    """

    kind = "game"

    def __init__(self, path: str):
        root = game_root_for(path)
        if not root:
            raise SourceError("Not an SCS game extract (expected a folder with def\\ and base_vehicle\\ inside)")
        self.path = root
        self.mounts = []
        for c in _children(root):
            if os.path.basename(c).lower() != "def" and _looks_extracted(c):
                self.mounts.append(c)          # base_vehicle, DLC extracts, "game files" (holds def)
        if _has_def(root) or _looks_extracted(root):
            self.mounts.append(root)
        self._dirs: dict[str, list] = {}
        self._virtual: dict = {}
        self._vcache: dict = {}
        self.game = self

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("/\\"))

    def _rel(self, p: str) -> str:
        return p.replace("\\", "/").lstrip("/")

    def _find(self, p: str):
        rel = self._rel(p)
        for m in self.mounts:
            fp = os.path.join(m, *rel.split("/"))
            if os.path.isfile(fp):
                return fp
        return None

    def stat(self, p: str):
        fp = self._find(p)
        if fp is None:
            return None
        st = os.stat(fp)
        return (fp, st.st_size, int(st.st_mtime))

    def register_virtual(self, path: str, make):
        register_virtual(path, make)

    def exists(self, p: str) -> bool:
        if p.lower().startswith(VIRTUAL):
            return p.lower() in VIRTUAL_FILES
        return self._find(p) is not None

    def read(self, p: str):
        if p.lower().startswith(VIRTUAL):
            return read_virtual(p)
        fp = self._find(p)
        if fp is None:
            return None
        with open(fp, "rb") as f:
            return f.read()

    def listdir(self, d: str):
        rel = self._rel(d).rstrip("/").lower()
        if rel in self._dirs:
            return self._dirs[rel]
        out = set()
        for m in self.mounts:
            fp = os.path.join(m, *rel.split("/")) if rel else m
            try:
                for n in os.listdir(fp):
                    out.add(n.lower() + ("/" if os.path.isdir(os.path.join(fp, n)) else ""))
            except OSError:
                pass
        res = sorted(out)
        self._dirs[rel] = res
        return res

    def files(self):
        return iter(())

    def close(self):
        pass


class HashFSSource:
    """A HashFS (SCS#) archive: base-game archives and locked mods (Jon Ruda's main packs).
    Files are read by path; listings only where the archive kept them."""

    kind = "hashfs"

    def __init__(self, path: str):
        try:
            from .hashfs import HashFS, HashFSError
        except ImportError:   # public build: no reader for locked mod packs
            raise SourceError("Locked pack (SCS# format) - SkinForge only reads open packs. Use the mod's "
                              "Open Definition, or ask the author for an unlocked version")
        try:
            self._h = HashFS(path)
        except HashFSError as e:
            raise SourceError(str(e))
        self.path = path
        self._files = None
        self._dirs: dict = {}

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("/\\"))

    def _norm(self, p):
        return p.replace("\\", "/").strip("/").lower()

    def exists(self, p: str) -> bool:
        if p.lower().startswith(VIRTUAL):
            return p.lower() in VIRTUAL_FILES
        return self._h.exists(self._norm(p))

    def read(self, p: str):
        if p.lower().startswith(VIRTUAL):
            return read_virtual(p)
        return self._h.read(self._norm(p))

    def stat(self, p: str):
        return self._h.stat(self._norm(p))

    def listdir(self, d: str):
        k = self._norm(d)
        if k not in self._dirs:
            names = self._h.listdir(k)
            self._dirs[k] = [n for n in names if _plain_name(n)]   # locked packs add junk names
        return self._dirs[k]

    def files(self):
        """Walk the listings that exist (def/ and vehicle/ - what SkinForge scans)."""
        if self._files is None:
            out = []
            stack = [x for x in ("def", "vehicle") if self._h._entry(x)]
            seen = set()
            while stack and len(out) < 200000:
                d = stack.pop()
                if d in seen:
                    continue
                seen.add(d)
                for n in self.listdir(d):
                    full = f"{d}/{n}"
                    if n.endswith("/"):
                        stack.append(full[:-1])
                    else:
                        out.append("/" + full)
            self._files = out
        return self._files

    def close(self):
        self._h.close()


class GameInstallSource:
    """The game install folder itself (Steam ...\\common\\American Truck Simulator): the SCS archives
    are read in place, no extraction. Every archive SkinForge needs is mounted as the game does it
    (DLC over base), so the user sees exactly the trucks, trailers and AI vehicles they own."""

    kind = "game"

    def __init__(self, path: str, progress=None):
        from .scsarchive import ArchiveError, HashFS2, archive_order
        self.path = path
        self.archives = []
        self.skipped = []
        for fp in archive_order(path):
            if progress:
                progress(f"Opening {os.path.basename(fp)}…")
            try:
                self.archives.append(HashFS2(fp))
            except (ArchiveError, OSError, ValueError) as e:
                self.skipped.append((os.path.basename(fp), str(e)))
        if not self.archives:
            raise SourceError("No readable SCS archives in this folder" +
                              (f" ({self.skipped[0][1]})" if self.skipped else ""))
        self._dirs: dict = {}
        self.game = self

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("/\\"))

    def register_virtual(self, path: str, make):
        register_virtual(path, make)

    def _arc(self, p):
        for a in self.archives:
            if a.exists(p):
                return a
        return None

    def exists(self, p: str) -> bool:
        if p.lower().startswith(VIRTUAL):
            return p.lower() in VIRTUAL_FILES
        return self._arc(p) is not None

    def read(self, p: str):
        if p.lower().startswith(VIRTUAL):
            return read_virtual(p)
        a = self._arc(p)
        return a.read(p) if a is not None else None

    def stat(self, p: str):
        a = self._arc(p)
        return a.stat(p) if a is not None else None

    def listdir(self, d: str):
        k = d.replace("\\", "/").strip("/").lower()
        if k not in self._dirs:
            out = set()
            for a in self.archives:
                out.update(a.listdir(k))
            self._dirs[k] = sorted(out)
        return self._dirs[k]

    def files(self):
        return iter(())

    def close(self):
        for a in self.archives:
            a.close()


def _plain_name(n: str) -> bool:
    import re
    return bool(re.fullmatch(r"[a-z0-9_.\-+ ]+/?", n)) and not n.startswith(" ")


def open_source(path: str):
    from .scsarchive import is_game_install
    if os.path.isdir(path) and is_game_install(path):
        return GameInstallSource(path)
    if os.path.isdir(path) and game_root_for(path):
        return GameSource(path)
    if archive_kind(path) == "hashfs":
        return HashFSSource(path)
    return Source(path)


class LayeredGame:
    """Game files with the loaded mod packs on top, as the game sees them: a truck mod that
    replaces or adds SCS-style trucks (Eugene's Scania NG P/G/R/S) shows up in SCS trucks."""

    kind = "game"

    def __init__(self, mods: list, game):
        self.layers = list(mods) + ([game] if game is not None else [])
        self.base = game
        self.path = game.path if game is not None else ""
        self.game = self
        self._dirs: dict = {}

    @property
    def name(self) -> str:
        return self.base.name if self.base is not None else "mods"

    def register_virtual(self, path: str, make):
        register_virtual(path, make)

    def read(self, p):
        for s in self.layers:
            d = s.read(p)
            if d is not None:
                return d
        return None

    def exists(self, p):
        return any(s.exists(p) for s in self.layers)

    def stat(self, p):
        for s in self.layers:
            if s.exists(p):
                st = getattr(s, "stat", None)
                return st(p) if st else None
        return None

    def listdir(self, d):
        k = d.lower().rstrip("/")
        if k not in self._dirs:
            out = set()
            for s in self.layers:
                out.update(s.listdir(d))
            self._dirs[k] = sorted(out)
        return self._dirs[k]

    def files(self):
        return iter(())

    def close(self):
        pass


class MultiSource:
    """Several sources searched in order (e.g. Open Definition + main texture pack)."""

    def __init__(self, sources: list):
        self.sources = sources
        game = next((s for s in sources if getattr(s, "kind", "") == "game"), None)
        mods = [s for s in sources if getattr(s, "kind", "") != "game"]
        self.game = LayeredGame(mods, game) if (game is not None and mods) else game

    def read(self, p):
        for s in self.sources:
            d = s.read(p)
            if d is not None:
                return d
        return None

    def exists(self, p):
        return any(s.exists(p) for s in self.sources)

    def files(self):
        seen = set()
        for s in self.sources:
            for k in s.files():
                if k not in seen:
                    seen.add(k)
                    yield k

    def listdir(self, d):
        out = set()
        for s in self.sources:
            out.update(s.listdir(d))
        return sorted(out)
