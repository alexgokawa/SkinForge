"""Local HTTP backend + app window. Everything stays on 127.0.0.1."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import traceback

from . import log as L
import webbrowser
import zipfile
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from PIL import Image

from . import __version__
from . import builder as B
from . import catalog
from . import flips as F
from . import logos
from . import platform_util as P
from . import texture as T
from .scs import SourceError

WEB_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                       "skinforge", "web")
if not os.path.isdir(WEB_DIR):
    WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

GAME_KEY = {"ats": "ATS", "ets2": "ETS2"}
TEMPLATE_RE = re.compile(r"paint_?job/(?:.*/)?template[^/]*\.(?:sii|dds|tobj)$")
UPRIGHT = -2   # pseudo override index: the "upright sheet" for mirrored pieces
ART_EXT = (".png", ".jpg", ".jpeg", ".tga", ".dds", ".psd", ".webp", ".bmp", ".tif", ".tiff")
PREVIEW = 1024


class LRU(OrderedDict):
    def __init__(self, cap):
        super().__init__()
        self.cap = cap

    def get(self, k):
        if k in self:
            self.move_to_end(k)
            return self[k]
        return None

    def put(self, k, v):
        self[k] = v
        self.move_to_end(k)
        while len(self) > self.cap:
            self.popitem(last=False)


def _contains(r, bbox):
    """True if island result r's bbox strictly contains bbox (a box mark cut it out of r)."""
    if not r:
        return False
    b = r[1]
    return b != bbox and b[0] <= bbox[0] and b[1] <= bbox[1] and b[2] >= bbox[2] and b[3] >= bbox[3]


class App:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache_lock = threading.RLock()
        self._keylocks = {}
        self.settings_path = os.path.join(P.app_data_dir(), "settings.json")
        self.settings = self._load_settings()
        self.project = None
        self.project_file = None
        self.trailers = []
        self.source_status = []
        self._src = None
        self.tmpl_cache = LRU(48)     # key -> 1024 template
        self.guide_cache = LRU(48)    # key -> 1024 guide
        self.art_cache = LRU(24)      # (path, mtime) -> 1024 art
        self.png_cache = LRU(64)
        self.build_state = {"running": False, "log": [], "result": None, "error": None}
        self.last_ping = time.time()
        self.bye_at = 0.0
        last = self.settings.get("last_project")
        self._starting = True   # first load in the background: the window opens at once
        try:
            if last and os.path.isfile(last):
                self.open_project(last)
        except Exception:
            traceback.print_exc()
        if self.project is None:
            self.new_project("Untitled")
        self._starting = False

    # ---------- settings / project ----------
    def _load_settings(self):
        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"recent": []}

    def _save_settings(self):
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2)
        except OSError:
            pass

    @property
    def project_dir(self):
        return os.path.dirname(self.project_file)

    def _remember(self):
        self.settings["last_project"] = self.project_file
        rec = [r for r in self.settings.get("recent", []) if r != self.project_file and os.path.isfile(r)]
        self.settings["recent"] = [self.project_file] + rec[:9]
        self._save_settings()

    def new_project(self, name: str, folder: str | None = None):
        name = name.strip() or "Untitled"
        folder = folder or os.path.join(P.projects_dir(), B.safe_filename(name))
        base, i = folder, 2
        while os.path.exists(os.path.join(folder, "project.json")):
            folder = f"{base} ({i})"
            i += 1
        os.makedirs(os.path.join(folder, "art"), exist_ok=True)
        code = self._unique_code(name)
        p = B.new_project(paint_name=name, mod_name=f"{name} skins", code=code)
        game = (self.project or {}).get("game") or self.settings.get("last_game") or "ats"
        p["game"] = game
        p["output_dir"] = P.game_mod_dirs().get(GAME_KEY[game]) or os.path.join(folder, "output")
        # carry over the packs from the last project (same game) so a new one is ready to paint
        if self.project and self.project.get("sources") and self.project.get("game", "ats") == game:
            p["sources"] = list(self.project["sources"])
            p["sources_by_game"] = json.loads(json.dumps(self.project.get("sources_by_game", {})))
        else:
            p["sources"] = self.find_sources(game)
            gf = self._game_folder(game)
            if gf and gf not in p["sources"]:
                p["sources"].append(gf)
        self.project, self.project_file = p, os.path.join(folder, "project.json")
        B.save_project(p, self.project_file)
        self._remember()
        self.load_sources(background=getattr(self, "_starting", False))

    def _unique_code(self, name: str) -> str:
        """Initials + 2 random chars (e.g. 'vht7k'), never equal to a code used by another known
        project. Two mods with the same code would define the same paint-job units and clash."""
        import random
        import string
        base = re.sub(r"[^a-z0-9]", "", "".join(w[0] for w in name.lower().split() if w))[:3]
        if not base or base == "u":
            base = "sf"
        used = set()
        for r in self.settings.get("recent", []):
            try:
                with open(r, "r", encoding="utf-8") as f:
                    used.add(json.load(f).get("code", ""))
            except Exception:
                pass
        if self.project:
            used.add(self.project.get("code", ""))
        alphabet = string.ascii_lowercase + string.digits
        while True:
            code = base + "".join(random.choice(alphabet) for _ in range(2))
            if code not in used:
                return code

    def open_project(self, path: str):
        if os.path.isdir(path):
            path = os.path.join(path, "project.json")
        p = B.load_project(path)
        self.project, self.project_file = p, path
        self._remember()
        self.load_sources(background=getattr(self, "_starting", False))

    def rename_project(self, name: str):
        """Rename the project folder (and the paint job name if it was still the default)."""
        name = name.strip()
        if not name:
            raise ValueError("Name can't be empty")
        old_dir = self.project_dir
        parent = os.path.dirname(old_dir)
        new_dir = os.path.join(parent, B.safe_filename(name))
        if os.path.normcase(new_dir) != os.path.normcase(old_dir):
            base, i = new_dir, 2
            while os.path.exists(new_dir):
                new_dir = f"{base} ({i})"
                i += 1
            os.rename(old_dir, new_dir)
        old_file = self.project_file
        self.project_file = os.path.join(new_dir, "project.json")
        if self.project.get("paint_name", "").startswith("Untitled"):
            self.project["paint_name"] = name
            for sk in self.project["skins"]:
                if (sk.get("name") or "").startswith("Untitled"):
                    sk["name"] = name
        if self.project.get("mod_name", "").startswith("Untitled"):
            self.project["mod_name"] = f"{name} skins"
        self.save()
        self.settings["recent"] = [r for r in self.settings.get("recent", []) if r != old_file]
        self._remember()

    def save(self):
        B.save_project(self.project, self.project_file)

    # ---------- sources ----------
    def _game_folders(self):
        gf = self.settings.setdefault("game_folders", {})
        if self.settings.get("game_folder") and "ats" not in gf:
            gf["ats"] = self.settings.pop("game_folder")
        return gf

    def _stash_key(self, game, sources):
        p = self.project
        return (game, tuple(sources), int(p.get("resolution", 4096)), id(p))

    def _game_folder(self, game):
        """The game files for this game: the folder the user picked before, else the Steam
        install found on this PC (read in place - nothing to extract)."""
        gf = self._game_folders().get(game)
        if gf and os.path.isdir(gf):
            return gf
        from .scsarchive import find_game_installs
        found = find_game_installs().get(game)
        if found:
            self._game_folders()[game] = found
            self._save_settings()
        return found

    def set_game(self, game):
        """ATS / ETS2 switch: each game keeps its own sources, game files and mod folder."""
        if game not in GAME_KEY:
            raise ValueError("Unknown game")
        p = self.project
        old = p.get("game", "ats")
        if game == old:
            return
        sbg = p.setdefault("sources_by_game", {})
        sbg[old] = list(p.get("sources", []))
        p["game"] = game
        srcs = sbg.get(game)
        if srcs is None:
            srcs = self.find_sources(game)
            gf = self._game_folder(game)
            if gf:
                srcs.append(gf)
        p["sources"] = srcs
        mod = P.game_mod_dirs().get(GAME_KEY[game])
        if mod:
            p["output_dir"] = mod
        self.settings["last_game"] = game
        self._save_settings()
        self.save()
        # keep what was loaded for the game we leave; switching back is then instant
        stash = getattr(self, "_game_stash", {})
        self._game_stash = stash
        if self._src is not None and not self.busy:
            stash[old] = (self._stash_key(old, sbg[old]), self._src, self.trailers, self.source_status)
        cached = stash.get(game)
        if cached and cached[0] == self._stash_key(game, srcs):
            _k, self._src, self.trailers, self.source_status = cached
            self._gen = getattr(self, "_gen", 0) + 1
            with self.cache_lock:
                self.tmpl_cache.clear(); self.guide_cache.clear(); self.png_cache.clear()
            return
        self.load_sources()

    def load_truck(self, tid):
        """Read one SCS truck's models (first time can take a while on a cloud drive)."""
        from . import scsgame
        game = getattr(self._src, "game", None)
        if game is None:
            from .scs import LayeredGame
            if self._src is None:
                raise ValueError("Add the truck's mod packs or your extracted game files first")
            game = LayeredGame(getattr(self._src, "sources", [self._src]), None)
        lst = self.project.setdefault("scs_trucks", [])
        if tid not in lst:
            lst.append(tid)
        res = int(self.project.get("resolution", 4096))
        t0 = time.time()
        L.info(f"Load truck {tid} at {res}")
        t = scsgame.scan_truck(game, tid, res, progress=lambda m: setattr(self, "busy", m))
        L.info(f"Loaded truck {tid}: {len(t.slots)} slots in {time.time() - t0:.1f}s"
               + (f" - {t.error}" if getattr(t, "error", "") else ""))
        kind = "mod3d_truck" if scsgame.is_mod(game, f"/def/vehicle/truck/{tid}") else "scs_truck"
        t.lazy, t.vkind = False, kind
        if not t.slots:
            t.error = scsgame.empty_reason(game, tid)
        self.trailers = [t if (x.id == tid and getattr(x, "vkind", "") in ("scs_truck", "mod3d_truck")) else x
                         for x in self.trailers]
        self.busy = ""
        self.save()

    def load_car(self, tid):
        """Read one Road Trip car's models (Load button)."""
        from . import scsgame
        game = getattr(self._src, "game", None)
        if game is None:
            raise ValueError("Add your ATS game folder first (Road Trip cars come from the game files)")
        lst = self.project.setdefault("scs_cars", [])
        if tid not in lst:
            lst.append(tid)
        res = int(self.project.get("resolution", 4096))
        t0 = time.time()
        L.info(f"Load car {tid} at {res}")
        t = scsgame.scan_truck(game, tid, res, progress=lambda m: setattr(self, "busy", m), vroot="car")
        L.info(f"Loaded car {tid}: {len(t.slots)} slots in {time.time() - t0:.1f}s")
        t.lazy, t.vkind = False, "scs_car"
        if not t.slots:
            t.error = scsgame.empty_reason(game, tid, "car")
        self.trailers = [t if (x.id == tid and getattr(x, "vkind", "") == "scs_car") else x for x in self.trailers]
        self.busy = ""
        self.save()

    def _replace_missing_game_folder(self):
        """A game-files folder that no longer exists (moved / renamed extract) is swapped for
        the game install found on this PC, so the user isn't left with a red source."""
        p = self.project
        srcs = list(p.get("sources", []))
        gone = [s for s in srcs if not os.path.exists(s) and not s.lower().endswith((".scs", ".zip"))]
        if not gone:
            return
        game = p.get("game", "ats")
        gf = self._game_folders()
        if gf.get(game) in gone:
            gf.pop(game, None)
        found = self._game_folder(game)
        if not found or found in srcs:
            return
        p["sources"] = [found if s == gone[0] else s for s in srcs if s not in gone[1:]]
        self.save()

    @staticmethod
    def _is_game_src(s):
        from .scs import game_root_for
        try:
            return os.path.isdir(s) and bool(game_root_for(s))
        except OSError:
            return False

    def _ensure_game_folder(self):
        """Every project reads the game files unless the user took them out on purpose. A new
        project copies the open project's sources, so one project that lost its game folder
        used to pass that on to every project made after it. Also drops duplicate sources."""
        p = self.project
        srcs, seen = [], set()
        for s in p.get("sources", []):
            k = os.path.normcase(os.path.normpath(s))
            if k not in seen:
                seen.add(k)
                srcs.append(s)
        changed = len(srcs) != len(p.get("sources", []))
        if not p.get("no_game") and not any(self._is_game_src(s) for s in srcs):
            gf = self._game_folder(p.get("game", "ats"))
            if gf and os.path.normcase(os.path.normpath(gf)) not in seen:
                srcs.append(gf)
                changed = True
                L.info(f"Game folder added to sources: {gf}")
        if changed:
            p["sources"] = srcs
            self.save()

    def _trailers_in_use(self):
        """SCS trailers read at start: the ones loaded before + any this project has art for."""
        p = self.project
        keys = set(p.get("slots", {}))
        for art in p.get("skin_art", {}).values():
            keys |= set(art)
        return set(p.get("scs_trailers", [])) | {k.split("/")[0] for k in keys if k.startswith("scs.")}

    def load_trailer(self, tid):
        """Read one SCS trailer's models (Load button)."""
        from . import scsgame
        game = getattr(self._src, "game", None)
        if game is None:
            raise ValueError("Add your game folder first")
        lst = self.project.setdefault("scs_trailers", [])
        if tid not in lst:
            lst.append(tid)
        res = int(self.project.get("resolution", 4096))
        t0 = time.time()
        L.info(f"Load trailer {tid} at {res}")
        t = scsgame.scan_trailer(game, tid, res, progress=lambda m: setattr(self, "busy", m))
        L.info(f"Loaded trailer {tid}: {len(t.slots)} slots in {time.time() - t0:.1f}s")
        t.lazy = False
        self.trailers = [t if (x.id == tid and getattr(x, "vkind", "") == "scs") else x for x in self.trailers]
        self.busy = ""
        self.save()

    def find_sources(self, game=None):
        """ZIP .scs files in the game mod folders that ship template paint jobs."""
        found = []
        for _game, d in P.game_mod_dirs().items():
            if game and _game != GAME_KEY[game]:
                continue
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for fn in names:
                if not fn.lower().endswith((".scs", ".zip")):
                    continue
                fp = os.path.join(d, fn)
                try:
                    with open(fp, "rb") as f:
                        if f.read(2) != b"PK":
                            continue
                    with zipfile.ZipFile(fp) as z:
                        names = z.namelist()
                        has_def = any(n.startswith(("def/vehicle/trailer_owned/", "def/vehicle/truck/"))
                                      and "/paint_job/" in n for n in names)
                        has_tmpl = any(TEMPLATE_RE.search(n.lower()) for n in names)
                        # a vehicle mod / open definition also defines the vehicle (cabin, chassis,
                        # body...); a skin pack only adds paint jobs - not a source
                        has_vehicle = any(re.match(r"def/vehicle/(?:trailer_owned|truck)/[^/]+/(?!paint_job/)[^/]+/.+\.sii$", n)
                                          or re.match(r"def/vehicle/trailer_owned/[^/]+/(?!paint_job/)[^/]+\.sii$", n)
                                          for n in names)
                        if has_def and has_tmpl and has_vehicle:
                            found.append(fp)
                except Exception:
                    continue
        return found

    def load_sources(self, background=False):
        """Open the sources and scan them. The scan runs without holding the app lock (reading
        game files from a cloud drive can take a while); results are swapped in at the end.
        background=True (app start): the window opens at once and shows the progress."""
        self._gen = getattr(self, "_gen", 0) + 1
        gen = self._gen
        with self.cache_lock:
            self.tmpl_cache.clear(); self.guide_cache.clear(); self.png_cache.clear()
        if background:
            self.busy = "Reading game files…"
            self.trailers, self.source_status = [], []
            threading.Thread(target=self._load_sources, args=(gen,), daemon=True).start()
            return
        self._load_sources(gen)

    def _load_sources(self, gen):
        p = self.project
        status, ok, trailers = [], [], []
        self._replace_missing_game_folder()
        self._ensure_game_folder()
        for s in p.get("sources", []):
            try:
                from .scs import open_source
                src = open_source(s)
                ok.append(src)
                status.append({"path": s, "ok": True, "kind": getattr(src, "kind", "")})
            except (SourceError, OSError) as e:
                status.append({"path": s, "ok": False, "error": str(e)})
        src = None
        if ok:
            from .scs import MultiSource
            src = ok[0] if len(ok) == 1 else MultiSource(ok)
            try:
                trailers = catalog.scan(src, int(p.get("resolution", 4096)),
                                        trucks_loaded=tuple(p.get("scs_trucks", [])),
                                        cars_loaded=tuple(p.get("scs_cars", [])),
                                        progress=lambda m: setattr(self, "busy", m),
                                        trailers_loaded=self._trailers_in_use())
            except Exception as e:
                traceback.print_exc()
                status.append({"path": "", "ok": False, "error": f"scan failed: {e}"})
        if src is not None:
            logos.detect(src)   # author "your*.dds" textures in the packs (Torpedo, ...)
        else:
            logos.DETECTED.clear()
        lg = logos.group(p.get("game", "ats"))   # Jon Ruda truck logos need no source
        if src is not None:
            for sl in lg.slots:   # Jon's own "Your Logo" image (ships in his Open Definitions) = template
                sl.template_dds = next((pth for _t, _n, pth in logos.texture_paths(sl.token)
                                        if src.exists(pth)), None)
        if lg.slots:
            trailers.append(lg)
        with self.lock:
            if gen != self._gen or p is not self.project:
                return            # sources changed meanwhile: a newer scan owns the result
            self._src, self.trailers, self.source_status = src, trailers, status
            L.info(f"Sources ({p.get('game')}): " + "; ".join(
                f"{x['path']} {'ok ' + x.get('kind', '') if x.get('ok') else 'FAILED: ' + x.get('error', '')}" for x in status)
                + f" -> {len(trailers)} vehicles/groups")
            self.busy = ""
        threading.Thread(target=self._prewarm, args=(trailers,), daemon=True).start()

    def _prewarm(self, trailers):
        """Decode every template once in the background (cached on disk for next start),
        so thumbnails and previews don't queue behind first-time 4096 decodes."""
        for t in trailers:
            for s in t.slots:
                if self.trailers is not trailers:
                    return   # sources changed meanwhile
                if s.kind == "logo":
                    continue
                try:
                    self.template_small(s.key)
                    self.guide_small(s.key)
                except Exception:
                    pass

    def slot(self, key):
        for t in self.trailers:
            for s in t.slots:
                if s.key == key:
                    return s
        return None

    # ---------- images ----------
    def _keylock(self, k):
        with self.cache_lock:
            l = self._keylocks.get(k)
            if l is None:
                l = self._keylocks[k] = threading.Lock()
            return l

    def _template_path(self, key, ovr):
        s = self.slot(key)
        if not s:
            return None
        return s.template_dds if ovr < 0 else next((o.template_mask for o in s.overrides if o.index == ovr), None)

    def _disk_cache(self, kind, key, ovr):
        """Path for a cached 1024 PNG, keyed by source file identity (so a new pack version invalidates it)."""
        path = self._template_path(key, ovr)
        ident = []
        for sp in self.project.get("sources", []):
            try:
                ident.append(f"{sp}|{os.path.getsize(sp)}|{os.path.getmtime(sp)}")
            except OSError:
                pass
        h = hashlib.sha1(f"{kind}|v4|{path}|{'|'.join(ident)}".encode()).hexdigest()[:20]
        d = os.path.join(P.app_data_dir(), "cache")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{kind}_{h}.png")

    def template_small(self, key, ovr=-1):
        ck = (key, ovr)
        with self.cache_lock:
            im = self.tmpl_cache.get(ck)
        if im is not None:
            return im
        with self._keylock(("t",) + ck):
            with self.cache_lock:
                im = self.tmpl_cache.get(ck)
            if im is not None:
                return im
            cp = self._disk_cache("tmpl", key, ovr)
            if os.path.isfile(cp):
                try:
                    im = Image.open(cp).convert("RGBA")
                except Exception:
                    im = None
            if im is None:
                path = self._template_path(key, ovr)
                data = self._src.read(path) if (path and self._src) else None
                if data:
                    try:
                        full = T.load_image(data)
                        im = full.resize(T.scaled_size(full.size, PREVIEW), Image.Resampling.LANCZOS)
                        im.save(cp, compress_level=1)
                    except Exception:
                        traceback.print_exc()
                        im = None
            if im is None:
                im = Image.new("RGBA", (PREVIEW, PREVIEW), (255, 255, 255, 255))
            with self.cache_lock:
                self.tmpl_cache.put(ck, im)
            return im

    def guide_small(self, key, ovr=-1):
        ck = (key, ovr)
        with self.cache_lock:
            g = self.guide_cache.get(ck)
        if g is not None:
            return g
        with self._keylock(("g",) + ck):
            with self.cache_lock:
                g = self.guide_cache.get(ck)
            if g is not None:
                return g
            cp = self._disk_cache("guide", key, ovr)
            if os.path.isfile(cp):
                try:
                    g = Image.open(cp).convert("RGBA")
                except Exception:
                    g = None
            if g is None:
                if self._template_path(key, ovr):
                    g = T.make_guide(self.template_small(key, ovr), PREVIEW, unused_alpha=200, downscaled=True)
                    g.save(cp, compress_level=1)
                else:
                    g = Image.new("RGBA", self.template_small(key, ovr).size, (0, 0, 0, 0))
            with self.cache_lock:
                self.guide_cache.put(ck, g)
            return g

    # ---------- mirrored pieces ----------
    def used_small(self, key):
        """Island map of a slot's template (1024), cached on disk."""
        ck = ("u", key)
        with self.cache_lock:
            m = self.guide_cache.get(ck)
        if m is not None:
            return m
        with self._keylock(ck):
            cp = self._disk_cache("used", key, -1)
            if os.path.isfile(cp):
                m = Image.open(cp).convert("L")
            else:
                path = self._template_path(key, -1)
                data = self._src.read(path) if (path and self._src) else None
                if not data:
                    return None
                m = F.used_mask(T.load_image(data))
                m.save(cp)
            with self.cache_lock:
                self.guide_cache.put(ck, m)
            return m

    def flip_marks(self, key):
        return list((self.project["slots"].get(key) or {}).get("flips") or [])

    def islands(self, key, marks=None):
        marks = self.flip_marks(key) if marks is None else marks
        if not marks:
            return []
        used = self.used_small(key)
        return F.islands_for(used, marks) if used is not None else []

    def flips_json(self, key):
        used = self.used_small(key)
        out = []
        for mk in self.flip_marks(key):
            r = F.island_for_mark(used, mk) if used is not None else None
            out.append({**mk, "ok": bool(r), "bbox": F.norm_bbox(r[1], used.size) if r else None})
        return out

    def add_flip(self, key, x, y, op, box=None):
        used = self.used_small(key)
        if used is None:
            raise ValueError("This slot has no template to find pieces on")
        if box:
            box = [round(min(box[0], box[2]), 5), round(min(box[1], box[3]), 5), round(max(box[0], box[2]), 5), round(max(box[1], box[3]), 5)]
            x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        r = F.island_in_box(used, box) if box else F.island(used, x, y)
        if not r:
            raise ValueError("No piece found there - click inside a piece, or drag a box around it")
        mask, bbox = r
        marks = self.flip_marks(key)
        for mk in marks:   # same piece clicked twice: just update its flip
            r2 = F.island_for_mark(used, mk)
            if r2 and r2[1] == bbox:
                mk["op"] = op
                break
        else:
            new = {"x": round(x, 5), "y": round(y, 5), "op": op}
            if box:
                new["box"] = box
            # a box that cuts a piece out of a bigger, already-marked blob replaces that blob's mark
            if box:
                marks = [mk for mk in marks if not (not mk.get("box") and _contains(F.island_for_mark(used, mk), bbox))]
            marks.append(new)
        self.project["slots"].setdefault(key, {})["flips"] = marks
        self.save()

    def copy_flips(self, key, to_keys):
        """Copy marks to other templates; a mark is kept only where it hits a piece of the same size."""
        src_isl = self.islands(key)
        report = {}
        for k in to_keys:
            if k == key:
                continue
            used = self.used_small(k)
            if used is None:
                report[k] = 0
                continue
            # merge: the target's own marks always stay; only add pieces it doesn't have yet
            existing = self.flip_marks(k)
            have = set()
            for mk in existing:
                r = F.island_for_mark(used, mk)
                if r:
                    have.add(r[1])
            added = 0
            for mk, mask, bbox in src_isl:
                r = F.island_for_mark(used, mk)
                if not r or r[1] in have:
                    continue
                b = r[1]
                if abs((b[2] - b[0]) - (bbox[2] - bbox[0])) <= 6 and abs((b[3] - b[1]) - (bbox[3] - bbox[1])) <= 6:
                    existing.append(dict(mk))
                    have.add(b)
                    added += 1
            if added:
                self.project["slots"].setdefault(k, {})["flips"] = existing
            report[k] = added
        self.save()
        return report

    def pivots(self, key):
        return (self.project["slots"].get(key) or {}).get("pivots") or None

    def freeze_pivots(self, key, force=False):
        """Lock the flip boxes when an upright sheet is written. Kept while the slot has upright
        art (so painted sheets never move); recomputed on export when there is none."""
        cfg = self.project["slots"].setdefault(key, {})
        has_up = any((a.get(key) or {}).get("upright") for a in self.project["skin_art"].values())
        if cfg.get("pivots") and has_up and not force:
            return
        used = self.used_small(key)
        isl = self.islands(key)
        if used is None or not isl:
            return
        cfg["pivots"] = F.pivots_for(isl, used.size)
        self.save()

    def upright_from_art(self, key):
        """Cut the marked pieces out of the slot's existing artwork, turn them upright and use
        that as the upright sheet. Also saves a copy next to the exported templates for editing."""
        cfg = B.slot_view(self.project, key)
        fp = self.art_abs(cfg.get("art") or "")
        if not fp or not os.path.isfile(fp):
            raise ValueError("This slot has no artwork yet")
        isl = self.islands(key)
        if not isl:
            raise ValueError("Mark the mirrored pieces first")
        with open(fp, "rb") as f:
            art = T.load_image(f.read())
        self.freeze_pivots(key, force=True)
        up = F.to_upright(art, isl, pivots=self.pivots(key))   # transparent outside the pieces; inside keeps the art's own alpha
        data = T.png_bytes(up)
        info = self.set_art(key, UPRIGHT, "upright.png", data)
        s = self.slot(key)
        names = {t.id: t.name for t in self.trailers}
        out = os.path.join(self.project_dir, "templates", B.safe_filename(names.get(s.trailer, s.trailer)))
        os.makedirs(out, exist_ok=True)
        label = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", s.label).strip("_") or "main")
        sid = self.project.get("active_skin", "")
        copy = os.path.join(out, f"{label}{'_' + B.safe_filename(B.skin(self.project)['name']) if sid else ''}_upright_from_art.png")
        with open(copy, "wb") as f:
            f.write(data)
        return info, copy

    def compose_full(self, key, im, sid=None):
        """Build hook: put the upright sheet's pieces back into the layout."""
        cfg = B.slot_view(self.project, key, sid)
        if not cfg.get("flips") or not cfg.get("upright"):
            return im
        fp = self.art_abs(cfg["upright"])
        if not os.path.isfile(fp):
            return im
        with open(fp, "rb") as f:
            up = T.load_image(f.read())
        return F.from_upright(im, up, self.islands(key), self.pivots(key))

    def compose_sig(self, key, sid=None):
        cfg = B.slot_view(self.project, key, sid)
        if not cfg.get("flips") or not cfg.get("upright"):
            return ""
        fp = self.art_abs(cfg["upright"])
        mt = os.path.getmtime(fp) if os.path.isfile(fp) else 0
        return json.dumps([cfg["flips"], cfg.get("pivots")], sort_keys=True) + f"|{fp}|{mt}"

    def art_rel(self, key, ovr=-1):
        cfg = B.art_cfg(self.project, key)
        if ovr == UPRIGHT:
            return cfg.get("upright") or ""
        if ovr < 0:
            return cfg.get("art") or ""
        return (cfg.get("overrides") or {}).get(str(ovr), "")

    def art_abs(self, rel):
        if not rel:
            return ""
        return rel if os.path.isabs(rel) else os.path.join(self.project_dir, rel)

    def art_small(self, rel):
        fp = self.art_abs(rel)
        if not fp or not os.path.isfile(fp):
            return None
        ck = (fp, os.path.getmtime(fp))
        with self.cache_lock:
            im = self.art_cache.get(ck)
        if im is None:
            with self._keylock(("a",) + ck):
                with self.cache_lock:
                    im = self.art_cache.get(ck)
                if im is None:
                    with open(fp, "rb") as f:
                        im = T.load_image(f.read())
                        im = im.resize(T.scaled_size(im.size, PREVIEW), Image.Resampling.LANCZOS)
                    with self.cache_lock:
                        self.art_cache.put(ck, im)
        return im

    def preview_png(self, key, ovr=-1, size=PREVIEW, mode="paint", color=None):
        color = color or self.project.get("base_color", "#9a9a9a")
        rel = self.art_rel(key, ovr)
        fp = self.art_abs(rel)
        mt = os.path.getmtime(fp) if fp and os.path.isfile(fp) else 0
        ck = (key, ovr, size, mode, color, rel, mt, tuple(self.project.get("sources", [])),
              self.compose_sig(key) if ovr < 0 else "", json.dumps(self.flip_marks(key)) if ovr < 0 else "")
        with self.cache_lock:
            cached = self.png_cache.get(ck)
        if cached:
            return cached
        s = self.slot(key)
        if s is not None and s.kind == "logo":
            art = self.art_small(rel) if mode != "template" else None
            im = _checker(PREVIEW)
            if art is not None:
                im.alpha_composite(B._fit_contain(art, PREVIEW))
            elif s.template_dds:   # no art yet (or Template view): show Jon's original logo
                im.alpha_composite(B._fit_contain(self.template_small(key, ovr), PREVIEW))
            im = im.convert("RGB")
            if size != PREVIEW:
                im = im.resize((size, size), Image.Resampling.LANCZOS)
            data = T.png_bytes(im)
            with self.cache_lock:
                self.png_cache.put(ck, data)
            return data
        tmpl = self.template_small(key, ovr)
        guide = self.guide_small(key, ovr) if mode in ("paint", "upright") else None
        art = self.art_small(rel) if mode != "template" else None
        rgb = None if color == "none" else B.hex_to_rgb(color)
        isl = self.islands(key) if ovr < 0 else []
        if isl and art is not None and mode in ("paint", "clean"):
            up_rel = self.art_rel(key, UPRIGHT)
            up = self.art_small(up_rel) if up_rel else None
            if up is not None:   # show what the game will get
                art = F.from_upright(art.resize(tmpl.size) if art.size != tmpl.size else art, up, isl, self.pivots(key))
        if mode == "upright":
            up_rel = self.art_rel(key, UPRIGHT)
            up = self.art_small(up_rel) if up_rel else None
            base = T.checker(tmpl.size) if rgb is None else Image.new("RGBA", tmpl.size, rgb + (255,))
            if up is not None:
                base.alpha_composite(up.resize(tmpl.size, Image.Resampling.LANCZOS) if up.size != tmpl.size else up)
            if isl:
                g = F.to_upright(guide, isl, pivots=self.pivots(key))
                a = g.getchannel("A").point(lambda v: int(v * 0.5))
                g.putalpha(a)
                base.alpha_composite(g)
                # dim everything that is not an upright piece
                pieces = F.footprint(isl, tmpl.size, self.pivots(key))
                dim = Image.new("RGBA", tmpl.size, (22, 25, 31, 255))
                dim.putalpha(pieces.point(lambda v: 0 if v else 225))
                base.alpha_composite(dim)
            im = base.convert("RGB")
        elif mode == "template":
            im = tmpl.convert("RGB")
        elif mode == "clean":
            im = T.checker(tmpl.size) if rgb is None else Image.new("RGBA", tmpl.size, rgb + (255,))
            if art is not None:
                im.alpha_composite(art.resize(tmpl.size, Image.Resampling.LANCZOS) if art.size != tmpl.size else art)
            im = im.convert("RGB")
        else:
            im = T.preview(tmpl, art, rgb, PREVIEW, guide_strength=0.4, guide=guide)
            if isl and size >= 512:
                im = im.convert("RGBA")
                im.alpha_composite(F.outline(im.size, isl))
                im = im.convert("RGB")
        if size != PREVIEW:
            im = im.resize(T.scaled_size(im.size, size), Image.Resampling.LANCZOS)
        data = T.png_bytes(im)
        with self.cache_lock:
            self.png_cache.put(ck, data)
        return data

    # ---------- art management ----------
    def set_art(self, key, ovr, filename, data: bytes):
        s = self.slot(key)
        if not s:
            raise ValueError("Unknown slot")
        im = T.load_image(data)  # validates
        kind = s.kind
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ART_EXT:
            ext = ".png"
        suffix = f"_o{ovr}" if ovr >= 0 else ("_upright" if ovr == UPRIGHT else "")
        sid = B.art_ns(key, self.project.get("active_skin", ""))
        stem = re.sub(r"[^A-Za-z0-9_\-]", "_", (f"{sid}__" if sid else "") + f"{s.trailer}_{s.token}" + suffix)
        rel = f"art/{stem}{ext}"
        fp = os.path.join(self.project_dir, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        # drop older copies with another extension
        for e in ART_EXT:
            old = os.path.join(self.project_dir, f"art/{stem}{e}")
            if e != ext and os.path.exists(old) and not self._art_in_use(f"art/{stem}{e}", key, ovr):
                os.remove(old)
        with open(fp, "wb") as f:
            f.write(data)
        self._assign(key, ovr, rel)
        return self.art_info(im, kind, self.template_small(key, ovr).size if kind != "logo" else None)

    def _art_in_use(self, rel, except_key, except_ovr):
        me = B.art_ns(except_key, self.project.get("active_skin", ""))
        for sid, arts in self.project["skin_art"].items():
            for k, cfg in arts.items():
                mine = sid == me and k == except_key
                if cfg.get("art") == rel and not (mine and except_ovr == -1):
                    return True
                if cfg.get("upright") == rel and not (mine and except_ovr == UPRIGHT):
                    return True
                for o, r in (cfg.get("overrides") or {}).items():
                    if r == rel and not (mine and int(o) == except_ovr):
                        return True
        for sk in self.project["skins"]:
            if sk.get("icon") == rel:
                return True
        return False

    def _assign(self, key, ovr, rel):
        cfg = B.art_cfg(self.project, key, create=True)
        if ovr == UPRIGHT:
            if rel:
                cfg["upright"] = rel
            else:
                cfg.pop("upright", None)
        elif ovr < 0:
            if rel:
                cfg["art"] = rel
            else:
                cfg.pop("art", None)
        else:
            o = cfg.setdefault("overrides", {})
            if rel:
                o[str(ovr)] = rel
            else:
                o.pop(str(ovr), None)
        self.save()

    @staticmethod
    def art_info(im, kind="trailer", tmpl_size=None):
        w, h = im.size
        a_min, a_max = im.getchannel("A").getextrema()
        warn = []
        if kind == "logo":
            if w != h:
                warn.append(f"{w}x{h}: will be centred on a transparent {logos.LOGO_SIZE} square")
            if a_min == 255:
                warn.append("No transparency: the logo shows as a solid rectangle on the truck")
            return {"w": w, "h": h, "warnings": warn}
        if tmpl_size and abs(w / h - tmpl_size[0] / tmpl_size[1]) > 0.02 * tmpl_size[0] / tmpl_size[1]:
            tw, th = tmpl_size
            want = "square" if tw == th else f"{round(tw / th)}:1 like the template" if tw > th else f"1:{round(th / tw)} like the template"
            warn.append(f"{w}x{h} doesn't match the template shape - should be {want}; it will be stretched")
        elif not (T.is_pot(w) and T.is_pot(h)):
            warn.append(f"{w}x{h} is not power-of-two - it will be resized")
        if a_min == 255:
            warn.append("No transparency: the whole trailer gets fixed colours and the colour picker does nothing")
        return {"w": w, "h": h, "warnings": warn}

    def export_templates(self, keys):
        root = os.path.join(self.project_dir, "templates")
        os.makedirs(root, exist_ok=True)
        names = {t.id: t.name for t in self.trailers}
        folders = set()
        written = []
        done_paths = set()
        res = int(self.project.get("resolution", 4096))
        try:
            want = int(self.project.get("template_size") or 0)   # 0 = same as the texture size
        except (TypeError, ValueError):
            want = 0
        from . import scsgame
        for key in keys:
            s = self.slot(key)
            if not s or (s.kind == "logo" and not s.template_dds):
                continue
            vres = int((self.project.get("res_by_vehicle") or {}).get(s.trailer) or res)
            eff = want or vres   # this vehicle's template size
            jobs = [(-1, s.template_dds, "")] + [(o.index, o.template_mask, f"_part{o.index}") for o in s.overrides]
            for ovr, path, suffix in jobs:
                if not path or path.lower() in done_paths:
                    continue
                done_paths.add(path.lower())
                data = self._src.read(path)
                if not data:
                    continue
                out = os.path.join(root, B.safe_filename(names.get(s.trailer, s.trailer)))   # one folder per truck/trailer
                os.makedirs(out, exist_ok=True)
                folders.add(out)
                full = T.load_image(data)
                if s.kind == "logo":
                    lw, lh = logos.size_for(s.token)
                    full = B._fit_contain(full, lw) if lw == lh else full.resize((lw, lh))   # the author's texture size
                elif s.kind == "scs" and eff != res:
                    # model-made: the size this part gets at that texture size (cab = 2K/4K/8K,
                    # parts in proportion), re-drawn from the model so the lines stay sharp
                    f = eff / res
                    size = (max(4, int(full.size[0] * f)), max(4, int(full.size[1] * f)))
                    full = render_at_or_resize(scsgame, path, full, size)
                elif s.kind != "scs" and (want or vres != res) and max(full.size) != eff:   # long side = chosen size
                    full = full.resize(T.scaled_size(full.size, eff), Image.Resampling.LANCZOS)
                elif s.kind != "scs" and max(full.size) < res:   # paint at the project resolution (e.g. 2048 templates -> 4096)
                    full = full.resize(T.scaled_size(full.size, res), Image.Resampling.LANCZOS)
                label = re.sub(r"[^A-Za-z0-9]+", "_", s.label).strip("_") or "main"
                if suffix:   # parts are shared per vehicle: name them by their own texture
                    label = "part_" + path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                stem = re.sub(r"_+", "_", label)
                isl = self.islands(key) if ovr < 0 else []
                if isl:
                    F.grey_out(full, isl).convert("RGB").save(os.path.join(out, f"{stem}.png"), compress_level=3)
                    self.freeze_pivots(key)
                    F.to_upright(full, isl, (0, 0, 0, 0), self.pivots(key)).save(   # transparent around the pieces
                        os.path.join(out, f"{stem}_upright.png"), compress_level=3)
                else:
                    full.convert("RGB").save(os.path.join(out, f"{stem}.png"), compress_level=3)
                if self.project.get("export_guides"):
                    T.make_guide(full).save(os.path.join(out, f"{stem}_GUIDE.png"), compress_level=3)
                written.append(stem)
        readme = os.path.join(root, "HOW TO USE.txt")
        with open(readme, "w", encoding="utf-8") as f:
            f.write(
                "SkinForge templates\n===================\n\n"
                "<name>.png       -> the template at the size picked under Template size (default: your\n"
                "                   project texture size). Paint over it,\n"
                "                   then hide/delete the template layer before exporting your PNG.\n"
                "<name>_GUIDE.png -> (only if 'Also export guide overlays' is on) line art on transparent,\n"
                "                   for use as a locked top layer instead of the template.\n\n"
                "Painting rules\n--------------\n"
                "- Canvas: same size as the guide (square, e.g. 4096 x 4096). Export PNG with transparency.\n"
                "- TRANSPARENT pixels take the paint colour picked in the truck/trailer shop.\n"
                "- Opaque pixels are fixed colours (logos, stripes, lettering).\n"
                "- Don't paint the guide's line art into your image.\n")
        out = next(iter(folders)) if len(folders) == 1 else root
        return out, written

    # ---------- build ----------
    def start_build(self):
        with self.lock:
            if self.build_state["running"]:
                return False
            self.build_state = {"running": True, "log": [], "result": None, "error": None}
        threading.Thread(target=self._build, daemon=True).start()
        return True

    def output_path(self):
        p = self.project
        out_dir = p.get("output_dir") or os.path.join(self.project_dir, "output")
        return os.path.join(out_dir, B.safe_filename(p["mod_name"]) + ".scs")

    def check_output(self):
        """Is there already a .scs at the build path, and was it built from THIS project? A
        SkinForge mod names its paint jobs <unit code><NN>, so the codes inside tell them apart
        (a forgotten mod name would otherwise overwrite another mod)."""
        import zipfile
        out = self.output_path()
        r = {"path": out, "exists": os.path.isfile(out), "ours": False, "codes": [], "skinforge": False}
        if not r["exists"]:
            return r
        st = os.stat(out)
        r["mtime"], r["size"] = st.st_mtime, st.st_size
        code = (self.project.get("code") or "").lower()
        try:
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
            r["skinforge"] = any(n.lower().endswith("skinforge_matte.tobj") or "/paint_job/" in n for n in names)
            toks = {n.rsplit("/", 1)[-1][:-4].lower() for n in names
                    if "/paint_job/" in n and "/accessory/" not in n and n.lower().endswith(".sii")}
            codes = sorted({re.match(r"^([a-z0-9_]*?)\d{2}", t).group(1) for t in toks if re.match(r"^([a-z0-9_]*?)\d{2}", t)})
            r["codes"] = codes
            r["ours"] = bool(code) and code in codes
        except (zipfile.BadZipFile, OSError):
            pass      # not a ZIP mod (or locked): certainly not this project's
        # who built it last on this PC: a copied project keeps the unit code, so the code alone
        # can't tell "my last build" from "the mod I made from the project I copied"
        by = (self.settings.get("built_by") or {}).get(os.path.normcase(os.path.abspath(out)))
        same_file = bool(by) and abs(by.get("mtime", 0) - st.st_mtime) < 2
        r["last_by_this"] = bool(by) and same_file and by.get("project") == self.project_file
        if r["ours"] and by and same_file and by.get("project") != self.project_file:
            r["ours"] = False
            r["other_project"] = by.get("project")
        r["ask"] = (not r["ours"]) or bool(self.project.get("ask_overwrite", True))
        return r

    def _remember_build(self, out):
        try:
            bb = self.settings.setdefault("built_by", {})
            bb[os.path.normcase(os.path.abspath(out))] = {"project": self.project_file, "mtime": os.stat(out).st_mtime}
            if len(bb) > 300:
                for k in list(bb)[:-300]:
                    bb.pop(k, None)
            self._save_settings()
        except OSError:
            pass

    def _build(self):
        st = self.build_state
        try:
            p = self.project
            out = self.output_path()
            os.makedirs(os.path.dirname(out), exist_ok=True)
            st["log"].append(f"Building {os.path.basename(out)}")
            L.info(f"Build {out} · res {p.get('resolution')} · finish {p.get('finish')} · "
                   f"per vehicle res {p.get('res_by_vehicle') or {}} finish {p.get('finish_by_vehicle') or {}} "
                   f"lock {p.get('lock_by_vehicle') or {}} · skins {len([x for x in p['skins'] if not x.get('deleted')])}")
            r = B.build(p, self.project_dir, self.trailers, out, progress=lambda m: st["log"].append(m),
                        compose=(self.compose_sig, self.compose_full))
            self.save()
            self._remember_build(r.path)
            st["result"] = {"path": r.path, "units": r.units, "warnings": r.warnings,
                            "seconds": round(r.seconds, 1), "size": r.size}
            st["log"].append(f"Done in {r.seconds:.1f}s - {r.size / 1048576:.1f} MB")
            L.info(f"Built {len(r.units)} units in {r.seconds:.1f}s, {r.size / 1048576:.1f} MB: "
                   + ", ".join(f"{u['token']}={u['trailer']}" for u in r.units)
                   + "".join(f"\nwarning: {w}" for w in r.warnings))
        except B.BuildError as e:
            L.warn(f"Build stopped: {e}")
            st["error"] = str(e)
        except Exception as e:
            L.error("Build failed", e)
            st["error"] = f"{type(e).__name__}: {e} - details in the log (Mod tab > Problems > Save error report)"
        finally:
            st["running"] = False

    # ---------- state ----------
    def view(self):
        """Project as the UI sees it: slots carry the active skin's artwork, paint_name / icon
        are the active skin's."""
        p = dict(self.project)
        sid = p.get("active_skin", "")
        p["slots"] = {k: B.slot_view(self.project, k, sid)
                      for k in set(self.project["slots"]) | set(self.project["skin_art"].get(sid, {}))
                      | set(self.project["skin_art"].get("", {}))}
        sk = B.skin(self.project)
        p["paint_name"] = sk.get("name", "")
        p["icon"] = sk.get("icon", "")
        p["skin_counts"] = {x["id"]: sum(1 for k, c in self.project["skin_art"].get(x["id"], {}).items()
                                         if c.get("art") and not k.startswith(logos.GROUP_ID + "/"))
                            for x in self.project["skins"]}
        cols = {}
        for t in self.trailers:
            for sl in t.slots:
                if sl.kind != "logo":
                    c = B.art_cfg(self.project, B.color_key(sl), sid).get("color")
                    if c:
                        cols[sl.key] = c
        p["slot_colors"] = cols
        p["shipped_skins"] = sorted({v.split("|", 1)[0] if "|" in v else "" for v in self.project.get("built", {}).values()})
        return p

    def set_skin_name(self, name):
        sk = B.skin(self.project)
        sk["name"] = name
        if sk is self.project["skins"][0]:
            self.project["paint_name"] = name

    def skins_action(self, d):
        p = self.project
        act = d.get("action")
        live = [x for x in p["skins"] if not x.get("deleted")]
        if act == "select":
            if not any(x["id"] == d["id"] for x in live):
                raise ValueError("Unknown skin")
            p["active_skin"] = d["id"]
        elif act in ("add", "duplicate"):
            sid = B.new_skin_id(p)
            src = B.skin(p)
            name = (d.get("name") or "").strip() or (f"{src['name']} copy" if act == "duplicate" else f"Skin {len(live) + 1}")
            new = {"id": sid, "name": name, "icon": src.get("icon", "") if act == "duplicate" else ""}
            p["skins"].append(new)
            arts = {}
            if act == "duplicate":
                arts = {k: json.loads(json.dumps(c)) for k, c in p["skin_art"].get(src["id"], {}).items()
                        if not k.startswith(logos.GROUP_ID + "/")}
            p["skin_art"][sid] = arts
            p["active_skin"] = sid
        elif act == "rename":
            sk = next((x for x in live if x["id"] == d["id"]), None)
            if not sk or not (d.get("name") or "").strip():
                raise ValueError("Give the skin a name")
            old = p["active_skin"]
            p["active_skin"] = sk["id"]
            self.set_skin_name(d["name"].strip())
            p["active_skin"] = old
        elif act == "delete":
            if len(live) <= 1:
                raise ValueError("A project needs at least one skin")
            sk = next((x for x in live if x["id"] == d["id"]), None)
            if not sk:
                raise ValueError("Unknown skin")
            shipped = any((v.split("|", 1)[0] if "|" in v else "") == sk["id"] for v in p.get("built", {}).values())
            if shipped:   # saves may use its paint jobs: keep them as plain colour, hide the skin
                sk["deleted"] = True
                p["skin_art"][sk["id"]] = {k: c for k, c in p["skin_art"].get(sk["id"], {}).items()
                                           if k.startswith(logos.GROUP_ID + "/")}
            else:
                p["skins"].remove(sk)
                if sk["id"]:
                    p["skin_art"].pop(sk["id"], None)
                else:
                    p["skin_art"][""] = {k: c for k, c in p["skin_art"].get("", {}).items()
                                         if k.startswith(logos.GROUP_ID + "/")}
                    p["skins"].insert(0, {"id": "", "name": "", "icon": "", "deleted": True})
            if p["active_skin"] == sk["id"]:
                p["active_skin"] = next(x["id"] for x in p["skins"] if not x.get("deleted"))
        else:
            raise ValueError("unknown skin action")
        self.save()
        return shipped if act == "delete" else None

    def state(self):
        p = self.view()
        return {
            "version": __version__,
            "project": p,
            "project_file": self.project_file,
            "project_dir": self.project_dir,
            "trailers": [t.to_json() for t in self.trailers],
            "sources": self.source_status,
            "mod_dirs": P.game_mod_dirs(),
            "recent": self.settings.get("recent", []),
            "ui_open": self.settings.get("ui_open", []),
            "build": self.build_state,
            "out_exists": os.path.isfile(self.output_path()),
            "busy": getattr(self, "busy", ""),
            "dialogs": P.native_dialog.__module__ is not None and _has_tk(),
        }


def _dds_to_png(data: bytes) -> bytes:
    return T.png_bytes(T.load_image(data))


def _checker(size, cell=64):
    im = Image.new("RGBA", (size, size), (58, 62, 70, 255))
    from PIL import ImageDraw
    d = ImageDraw.Draw(im)
    for y in range(0, size, cell):
        for x in range((y // cell) % 2 * cell, size, cell * 2):
            d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(72, 77, 86, 255))
    return im


def _has_tk():
    try:
        import tkinter  # noqa
        return True
    except Exception:
        return False


SETTABLE = {"export_guides", "truck_uvset", "paint_name", "mod_name", "author", "mod_version", "code", "price", "finish", "base_color",
            "color_locked", "metallic", "resolution", "output_dir", "description", "template_size", "res_by_vehicle", "finish_by_vehicle", "lock_by_vehicle", "ask_overwrite"}


def render_at_or_resize(scsgame, path, im, size):
    try:
        r = scsgame.render_at(path, size)
    except Exception:
        traceback.print_exc()
        r = None
    return r if r is not None else im.resize(size, Image.Resampling.LANCZOS)


LINKS = {"kofi": "https://ko-fi.com/gokawa", "github": "https://github.com/alexgokawa/SkinForge"}


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "SkinForge"

        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype="application/json", cache=False):
            try:
                self._send_raw(code, body, ctype, cache)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _send_raw(self, code, body, ctype, cache):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode("utf-8"))

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _jbody(self):
            b = self._body()
            return json.loads(b.decode("utf-8")) if b else {}

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            app.last_ping = time.time()
            try:
                if u.path in ("/", "/index.html"):
                    return self._static("index.html")
                if u.path.startswith("/static/"):
                    return self._static(u.path[len("/static/"):])
                if u.path == "/api/state":
                    with app.lock:
                        return self._json(app.state())
                if u.path == "/api/ping":
                    return self._json({"ok": True})
                if u.path == "/api/preview":
                    data = app.preview_png(q["key"], int(q.get("ovr", -1)), int(q.get("size", PREVIEW)),
                                           q.get("mode", "paint"), q.get("color"))
                    return self._send(200, data, "image/png")
                if u.path == "/api/find_sources":
                    return self._json({"found": app.find_sources(app.project.get("game", "ats"))})
                if u.path == "/api/icon_preview":
                    which = "mod_icon" if q.get("which") == "mod_icon" else "icon"
                    w, h = (276, 162) if which == "mod_icon" else (256, 64)
                    p = app.project
                    fp = app.art_abs((B.skin(p).get("icon") if which == "icon" else p.get(which)) or "")
                    if fp and os.path.isfile(fp):
                        im = B._fit(Image.open(fp).convert("RGBA"), w, h)   # exactly as it gets built
                    else:
                        im = B._default_icon(B.skin(p)["name"] if which == "icon" else p["mod_name"], p["base_color"], w, h)
                    return self._send(200, T.png_bytes(im), "image/png")
                if u.path == "/api/build":
                    return self._json(app.build_state)
                return self._json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as e:
                L.error(f"GET {self.path} failed", e)
                return self._json({"error": str(e)}, 500)

        def _static(self, name):
            name = name.split("?")[0]
            fp = os.path.normpath(os.path.join(WEB_DIR, name))
            if not fp.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(fp):
                return self._json({"error": "not found"}, 404)
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                     ".ico": "image/x-icon"}.get(os.path.splitext(fp)[1], "application/octet-stream")
            with open(fp, "rb") as f:
                self._send(200, f.read(), ctype)

        def do_POST(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            app.last_ping = time.time()
            try:
                r = self._post(u.path, q)
                if r is not None:
                    return self._json(r)
            except (ValueError, KeyError, B.BuildError, SourceError, OSError) as e:
                L.warn(f"POST {u.path}: {type(e).__name__}: {e}")
                return self._json({"error": str(e)}, 400)
            except Exception as e:
                L.error(f"POST {u.path} failed", e)
                return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

        def _post(self, path, q):
            if path == "/api/log":     # errors / events from the window
                d = self._jbody()
                L.write("UI", str(d.get("msg", ""))[:4000])
                return {"ok": True}
            if path == "/api/log/open":
                P.open_folder(L.log_dir())
                return {"ok": True, "dir": L.log_dir()}
            if path == "/api/log/report":
                with app.lock:
                    out = L.report(P.projects_dir(), app)
                P.open_folder(out, select=True)
                return {"ok": True, "path": out}
            if path == "/api/settings":
                d = self._jbody()
                with app.lock:
                    old_res = app.project.get("resolution")
                    for k, v in d.items():
                        if k not in SETTABLE:
                            continue
                        if k == "metallic":
                            app.project["metallic"].update(v)
                        elif k in ("finish_by_vehicle", "lock_by_vehicle"):
                            dv = app.project.setdefault(k, {})
                            for tid, x in (v or {}).items():
                                if x is None or x == "":
                                    dv.pop(str(tid), None)   # back to the mod's setting
                                elif k == "finish_by_vehicle":
                                    names = [n for n in ("gloss", "metallic", "matte") if n in str(x).split("+")]
                                    if names:
                                        dv[str(tid)] = "+".join(names)
                                else:
                                    dv[str(tid)] = bool(x)
                        elif k == "res_by_vehicle":
                            rv = app.project.setdefault("res_by_vehicle", {})
                            for tid, r in (v or {}).items():
                                r = int(r or 0)
                                if r in (2048, 4096, 8192):
                                    rv[str(tid)] = r
                                else:
                                    rv.pop(str(tid), None)   # back to the project size
                        elif k == "paint_name":
                            app.set_skin_name(str(v))
                        elif k == "code":
                            v = re.sub(r"[^a-z0-9_]", "", str(v).lower())[:6]
                            # changing the code after shipping would orphan saved paint jobs
                            if app.project.get("built") and v != app.project["code"]:
                                raise ValueError("Unit code is locked after the first build (saves reference it). "
                                                 "Start a new project for a new code.")
                            app.project[k] = v
                        else:
                            app.project[k] = v
                    app.save()
                    if "resolution" in d and d["resolution"] != old_res and any(
                            t.slots and t.slots[0].kind == "scs" for t in app.trailers):
                        app.load_sources()   # SCS part textures are sized from the resolution
                    if "paint_name" in d and os.path.basename(app.project_dir).startswith("Untitled") \
                            and str(d["paint_name"]).strip() and not str(d["paint_name"]).startswith("Untitled"):
                        app.rename_project(str(d["paint_name"]))
                    return {"ok": True, "project": app.view(), "project_file": app.project_file,
                            "out_exists": os.path.isfile(app.output_path()),
                            "project_dir": app.project_dir, "recent": app.settings.get("recent", [])}
            if path == "/api/sources":
                d = self._jbody()
                with app.lock:
                    had_game = any(app._is_game_src(s) for s in app.project.get("sources", []))
                    app.project["sources"] = [s for s in d.get("sources", []) if s]
                    has_game = any(app._is_game_src(s) for s in app.project["sources"])
                    if had_game and not has_game:
                        app.project["no_game"] = True      # removed on purpose: don't add it back
                    elif has_game:
                        app.project.pop("no_game", None)
                    from .scs import game_root_for
                    for sp in app.project["sources"]:
                        if os.path.isdir(sp) and game_root_for(sp):
                            app._game_folders()[app.project.get("game", "ats")] = sp
                            app._save_settings()
                    app.save()
                    app.load_sources()
                    return app.state()
            if path == "/api/art":
                data = self._body()
                with app.lock:
                    info = app.set_art(q["key"], int(q.get("ovr", -1)), q.get("filename", "art.png"), data)
                    return {"ok": True, "info": info, "project": app.view()}
            if path == "/api/art/path":
                d = self._jbody()
                fp = d["path"]
                with open(fp, "rb") as f:
                    data = f.read()
                with app.lock:
                    info = app.set_art(d["key"], int(d.get("ovr", -1)), os.path.basename(fp), data)
                    return {"ok": True, "info": info, "project": app.view()}
            if path == "/api/art/share":
                d = self._jbody()
                with app.lock:
                    rel = app.art_rel(d["from"])
                    if not rel:
                        raise ValueError("That slot has no artwork yet")
                    for k in d["to"]:
                        app._assign(k, -1, rel)
                    return {"ok": True, "project": app.view()}
            if path == "/api/logos/import":
                d = self._jbody()
                import zipfile as _zf
                done = []
                with _zf.ZipFile(d["path"]) as z:
                    for n in z.namelist():
                        role = logos.role_for_filename(n) if n.lower().endswith(".dds") else None
                        key = f"{logos.GROUP_ID}/{role}" if role else None
                        if not key or key in done:
                            continue
                        with app.lock:
                            app.set_art(key, -1, f"{role}.png", _dds_to_png(z.read(n)))
                        done.append(key)
                if not done:
                    raise ValueError("No Jon Ruda 'yourlogo' textures found in that file")
                return {"ok": True, "imported": done, "project": app.view()}
            if path == "/api/flips":
                d = self._jbody()
                key = d["key"]
                act = d.get("action")
                with app.lock:
                    if act == "add":
                        app.add_flip(key, float(d.get("x", 0)), float(d.get("y", 0)), d.get("op", "v"), d.get("box"))
                    elif act == "op":
                        marks = app.flip_marks(key)
                        marks[int(d["index"])]["op"] = d["op"]
                        app.project["slots"][key]["flips"] = marks
                        app.save()
                    elif act == "remove":
                        marks = app.flip_marks(key)
                        marks.pop(int(d["index"]))
                        app.project["slots"][key]["flips"] = marks
                        if not marks:
                            app.project["slots"][key].pop("flips", None)
                        app.save()
                    elif act == "extract":
                        info, copy = app.upright_from_art(key)
                        return {"ok": True, "info": info, "file": copy, "project": app.view()}
                    elif act == "copy":
                        rep_ = app.copy_flips(key, d.get("to") or [])
                        return {"ok": True, "report": rep_, "project": app.view()}
                    return {"ok": True, "flips": app.flips_json(key), "project": app.view()}
            if path == "/api/flips/get":
                d = self._jbody()
                with app.lock:
                    return {"flips": app.flips_json(d["key"])}
            if path == "/api/slot/color":
                d = self._jbody()
                with app.lock:
                    sl = app.slot(d["key"])
                    if not sl or sl.kind == "logo":
                        raise ValueError("No paint colour on this slot")
                    c = d.get("color")
                    if c and not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
                        raise ValueError("Bad colour")
                    cfg = B.art_cfg(app.project, B.color_key(sl), create=True)
                    if c:
                        cfg["color"] = c.lower()
                    else:
                        cfg.pop("color", None)
                    app.save()
                    return {"ok": True, "project": app.view()}
            if path == "/api/slot/cabins":
                d = self._jbody()
                with app.lock:
                    cfg = app.project["slots"].setdefault(d["key"], {})
                    if d.get("cabins") is None:
                        cfg.pop("cabins", None)          # back to the template / built-in fix
                    else:
                        cfg["cabins"] = [str(c) for c in d["cabins"]]
                    app.save()
                    return {"ok": True, "project": app.view()}
            if path == "/api/art/clear":
                d = self._jbody()
                with app.lock:
                    app._assign(d["key"], int(d.get("ovr", -1)), "")
                    return {"ok": True, "project": app.view()}
            if path == "/api/art/info":
                d = self._jbody()
                with app.lock:
                    fp = app.art_abs(app.art_rel(d["key"], int(d.get("ovr", -1))))
                if not fp or not os.path.isfile(fp):
                    return {"info": None}
                s = app.slot(d["key"])
                kind = s.kind if s else "trailer"
                ts = app.template_small(d["key"], int(d.get("ovr", -1))).size if kind != "logo" else None
                with open(fp, "rb") as f:
                    return {"info": App.art_info(T.load_image(f.read()), kind, ts), "file": fp}
            if path == "/api/icon":
                which = q.get("which", "icon")
                data = self._body()
                T.load_image(data)
                with app.lock:
                    sid = app.project.get("active_skin", "")
                    rel = f"art/{'mod_icon' if which == 'mod_icon' else 'icon' + ('_' + sid if sid else '')}.png"
                    Image.open(io.BytesIO(data)).save(os.path.join(app.project_dir, rel))
                    if which == "mod_icon":
                        app.project[which] = rel
                    else:
                        B.skin(app.project)["icon"] = rel
                    app.save()
                    return {"ok": True, "project": app.view()}
            if path == "/api/icon/clear":
                which = "mod_icon" if q.get("which") == "mod_icon" else "icon"
                with app.lock:
                    if which == "mod_icon":
                        app.project[which] = ""
                    else:
                        B.skin(app.project)["icon"] = ""
                    app.save()
                    return {"ok": True, "project": app.view()}
            if path == "/api/skins":
                d = self._jbody()
                with app.lock:
                    kept = app.skins_action(d)
                    st = app.state()
                    st["kept"] = bool(kept)
                    return st
            if path == "/api/ui/open":
                # which vehicle groups are expanded in the list: kept in settings.json, because the
                # app window gets a new port (= new browser storage) on every start
                d = self._jbody()
                with app.lock:
                    cur = [x for x in app.settings.get("ui_open", []) if x != d["id"]]
                    if d.get("open"):
                        cur.append(d["id"])
                    app.settings["ui_open"] = cur[-200:]
                    app._save_settings()
                return {"ok": True}
            if path == "/api/game":
                d = self._jbody()
                with app.lock:
                    app.set_game(d.get("game", "ats"))
                    return app.state()
            if path == "/api/scs/truck":
                d = self._jbody()
                with app.lock:
                    app.load_truck(d["id"])
                    return app.state()
            if path == "/api/scs/car":
                d = self._jbody()
                with app.lock:
                    app.load_car(d["id"])
                    return app.state()
            if path == "/api/scs/trailer":
                d = self._jbody()
                with app.lock:
                    app.load_trailer(d["id"])
                    return app.state()
            if path == "/api/templates":
                d = self._jbody()
                with app.lock:
                    out, written = app.export_templates(d.get("keys", []))
                P.open_folder(out)
                return {"ok": True, "dir": out, "written": written}
            if path == "/api/build/check":
                with app.lock:
                    r = app.check_output()
                if r["exists"] and not r["ours"]:
                    L.warn(f"Build target exists and isn't from this project: {r['path']} (codes {r['codes']})")
                return r
            if path == "/api/build":
                with app.lock:
                    app.save()
                return {"started": app.start_build()}
            if path == "/api/project/new":
                d = self._jbody()
                with app.lock:
                    app.new_project(d.get("name", "Untitled"), d.get("folder") or None)
                    return app.state()
            if path == "/api/project/rename":
                d = self._jbody()
                with app.lock:
                    app.rename_project(d.get("name", ""))
                    return app.state()
            if path == "/api/project/open":
                d = self._jbody()
                with app.lock:
                    app.open_project(d["path"])
                    return app.state()
            if path == "/api/link":     # fixed links only, opened in the default browser
                url = LINKS.get(self._jbody().get("name"))
                if not url:
                    raise ValueError("unknown link")
                webbrowser.open(url)
                return {"ok": True}
            if path == "/api/open":
                d = self._jbody()
                P.open_folder(d["path"], select=d.get("select", False))
                return {"ok": True}
            if path == "/api/dialog":
                d = self._jbody()
                ft = None
                if d.get("filter") == "scs":
                    ft = [("SCS mod", "*.scs *.zip"), ("All files", "*.*")]
                elif d.get("filter") == "image":
                    ft = [("Images", " ".join("*" + e for e in ART_EXT)), ("All files", "*.*")]
                elif d.get("filter") == "project":
                    ft = [("SkinForge project", "project.json"), ("All files", "*.*")]
                r = P.native_dialog(d.get("kind", "open"), d.get("title", ""), d.get("initial", ""), ft)
                return {"path": r, "supported": r is not None}
            if path == "/api/bye":
                app.bye_at = time.time()
                return {"ok": True}
            if path == "/api/quit":
                threading.Thread(target=lambda: (time.sleep(0.2), os._exit(0)), daemon=True).start()
                return {"ok": True}
            raise KeyError(f"unknown endpoint {path}")

    return H


def serve(port: int = 0, open_window: bool = True, browser_fallback: bool = True):
    app = App()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"SkinForge {__version__} running at {url}")
    L.info(f"Settings: {app.settings_path} · project: {app.project_file}")
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    proc = P.launch_app_window(url) if open_window else None
    if open_window and proc is None and browser_fallback:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1.0)
            now = time.time()
            if app.bye_at and now - app.bye_at > 3 and app.last_ping <= app.bye_at:
                break                                   # window closed (not just reloaded)
            if proc is not None:
                if proc.poll() is None:
                    continue
                if now - app.last_ping > 5:
                    break                               # our window process ended
                proc = None                             # handed off to a running browser
            elif open_window and now - app.last_ping > 900:
                break                                   # nobody has talked to us for 15 min
    except KeyboardInterrupt:
        pass
    httpd.shutdown()
    return url
