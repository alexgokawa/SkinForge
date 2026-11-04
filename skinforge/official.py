"""Template folder: paint template PNG sets for SCS vehicles (community sets on Trucky, e.g. "ATS templates" /
"ETS 2 Templates": one folder per truck / trailer, Cabin A (...).png, Accessories/...).

SCS draws them from the same UV sets the game uses, so they line up 1:1 with what SkinForge
paints on. We never trust file names for the match: an official sheet is used for a slot only
when its painted area (alpha > 0) covers the same area as SkinForge's own template of that slot
(same shape, overlap >= MATCH). Name matching only narrows which folders are compared.
"""
from __future__ import annotations

import json
import os
import re
import threading

import numpy as np
from PIL import Image

from . import platform_util as P

OWN_IN = 0.97         # everything SkinForge paints must be on the sheet (see score())
SHEET_IN = 0.75       # and most of the sheet must be SkinForge's parts (cab sheets: accessories
SHEET_IN_PART = 0.9   # on the cab texture may be extra); a part's sheet holds only that part
_GRID = 192           # masks are compared at this long side
_lock = threading.Lock()
_index: dict = {}     # png path -> {"size": [w, h], "mt": mtime, "mask": hex bits}
_index_file = None


# German / Nordic letters in community folder names: "Kögel", "Kässbohrer", "Schwarzmüller"
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss", "å": "a", "ø": "o", "æ": "ae", "é": "e", "è": "e"})
# SCS's DLC ids that shorten the brand: id word -> the brand as folders spell it
BRAND_ALIASES = {"schwmuller": "schwarzmuller", "schw": "schwarzmuller", "kogel": "kogel", "kass": "kassbohrer",
                 "schmitz": "schmitz", "scb": "schmitz"}


def _fold(s):
    import unicodedata
    s = (s or "").lower().translate(_FOLD)
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _compact(s):
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def _load_index():
    global _index_file
    if _index_file is None:
        _index_file = os.path.join(P.app_data_dir(), "cache", "official_index.json")
        try:
            with open(_index_file, encoding="utf-8") as f:
                _index.update(json.load(f))
        except (OSError, ValueError):
            pass


def _save_index():
    try:
        os.makedirs(os.path.dirname(_index_file), exist_ok=True)
        with open(_index_file, "w", encoding="utf-8") as f:
            json.dump(_index, f)
    except OSError:
        pass


def forget(root):
    """Drop the cached masks of one folder (next match reads its PNGs again)."""
    if not root:
        return
    pre = os.path.normcase(os.path.abspath(root))
    with _lock:
        _load_index()
        for k in [k for k in _index if os.path.normcase(os.path.abspath(k)).startswith(pre)]:
            _index.pop(k, None)
        _save_index()


def _grid_size(size):
    w, h = size
    if w >= h:
        return _GRID, max(1, round(_GRID * h / w))
    return max(1, round(_GRID * w / h)), _GRID


def _pack(mask):
    return np.packbits(mask.astype(np.uint8).ravel()).tobytes().hex()


def _unpack(hexs, shape):
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(hexs), np.uint8))
    return bits[: shape[0] * shape[1]].reshape(shape).astype(bool)


def _official_mask(path):
    """(size, mask at the grid size) of one official PNG, cached by path + mtime."""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None, None
    e = _index.get(path)
    if e and e.get("mt") == mt:
        size = tuple(e["size"])
        gw, gh = _grid_size(size)
        return size, _unpack(e["mask"], (gh, gw))
    try:
        with Image.open(path) as im:
            size = im.size
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            a = im.getchannel("A")
    except Exception:
        return None, None
    gw, gh = _grid_size(size)
    m = np.array(a.resize((gw, gh), Image.Resampling.BOX)) > 0
    _index[path] = {"size": list(size), "mt": mt, "mask": _pack(m)}
    return size, m


def fill_holes(m):
    """Painted area incl. everything the line art encloses: sets that draw only the mesh lines
    (faces transparent) and sets with filled faces then give the same area; window openings
    count as inside on both sides, so they compare alike."""
    out = ~m
    reach = np.zeros_like(m)
    reach[0, :], reach[-1, :], reach[:, 0], reach[:, -1] = out[0, :], out[-1, :], out[:, 0], out[:, -1]
    while True:
        grow = reach.copy()
        grow[1:, :] |= reach[:-1, :]
        grow[:-1, :] |= reach[1:, :]
        grow[:, 1:] |= reach[:, :-1]
        grow[:, :-1] |= reach[:, 1:]
        grow &= out
        if (grow == reach).all():
            return m | (out & ~reach)
        reach = grow


def own_mask(img):
    """Painted area of SkinForge's own template (faces are drawn near-white on a dark background)."""
    gw, gh = _grid_size(img.size)
    g = np.array(img.convert("L").resize((gw, gh), Image.Resampling.BOX))
    return g > 150


def _dilate(m, r=2):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            sh = np.zeros_like(m)
            ys, yd = (slice(dy, None), slice(None, -dy or None)) if dy >= 0 else (slice(None, dy), slice(-dy, None))
            xs, xd = (slice(dx, None), slice(None, -dx or None)) if dx >= 0 else (slice(None, dx), slice(-dx, None))
            sh[ys, xs] = m[yd, xd]
            out |= sh
    return out


def score(official_m, mine, mine_d=None):
    """(own-in-sheet, sheet-in-own) coverage, tolerant of line width and of label text drawn over
    faces. Community sheets often also draw accessories that share the cab texture, so the sheet
    may hold more than SkinForge's template - but everything SkinForge paints must be on it."""
    if mine_d is None:
        mine_d = _dilate(mine)
    a, b = official_m.sum(), mine.sum()
    if not a or not b:
        return 0.0, 0.0
    return (mine & _dilate(official_m)).sum() / b, (official_m & mine_d).sum() / a


def _folders(root):
    try:
        return [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))]
    except OSError:
        return []


def candidate_folders(root, vehicle_id, vehicle_name):
    """Folders of the official set that may hold this vehicle (narrowed by name, never exact)."""
    keys = {_compact(vehicle_id), _compact(vehicle_name)}
    words = [w for w in re.split(r"[^a-z0-9]+", _fold(f"{vehicle_id} {vehicle_name}")) if len(w) > 1]
    brand = {BRAND_ALIASES.get(words[0], words[0])} if words else set()
    if "scs" in words:      # SCS's own trailers: the type word is what matters
        brand = {w for w in words if w not in ("scs", "trailer", "ai")}
    out = []
    for d in _folders(root):
        c = _compact(d)
        if any(k and (k in c or c in k) for k in keys) or any(b and b in c for b in brand):
            out.append(os.path.join(root, d))
    return out


def _pngs(folder):
    out = []
    for dp, _dn, fns in os.walk(folder):
        out += [os.path.join(dp, f) for f in fns if f.lower().endswith(".png") and not f.startswith("._")]
    return out


ASPECT_MAX = 4.1      # sheets up to 4x wider / taller than SkinForge's are compared too (resampled)


def _fit_mask(m, shape):
    """An official mask at another aspect, resampled to SkinForge's grid (UVs are 0..1 on both:
    a 2048x1024 sheet of a part SkinForge makes square still lines up once stretched)."""
    if m.shape == shape:
        return m
    im = Image.fromarray(m.astype(np.uint8) * 255).resize((shape[1], shape[0]), Image.Resampling.BOX)
    return np.array(im) > 0


def match(root, vehicle_id, vehicle_name, own_img, cab=False, why=None):
    """Best official PNG for one slot, or None. own_img = SkinForge's template of that slot.
    why (a dict) gets the closest miss: {"png", "own_in", "sheet_in", "folders", "pngs"}."""
    if not root or not os.path.isdir(root) or own_img is None:
        return None
    w, h = own_img.size
    mine = fill_holes(own_mask(own_img))
    if mine.sum() < 20:
        return None
    mine_d = _dilate(mine)
    need = SHEET_IN if cab else SHEET_IN_PART
    best, best_s = None, (OWN_IN, need)
    with _lock:
        _load_index()
        dirty = False
        folders = candidate_folders(root, vehicle_id, vehicle_name)
        near, n_png = (0.0, None, 0.0, 0.0), 0
        for folder in folders:
            for png in _pngs(folder):
                had = png in _index
                size, m = _official_mask(png)
                dirty |= not had
                if size is None or not size[1] or not h:
                    continue
                n_png += 1
                ratio = (size[0] / size[1]) / (w / h)
                if not (1 / ASPECT_MAX <= ratio <= ASPECT_MAX):
                    continue
                own_in, sheet_in = score(fill_holes(_fit_mask(m, mine.shape)), mine, mine_d)
                if min(own_in / OWN_IN, sheet_in / need) > near[0]:
                    near = (min(own_in / OWN_IN, sheet_in / need), png, own_in, sheet_in)
                if own_in >= OWN_IN and sheet_in >= need and (sheet_in, own_in) > best_s[::-1]:
                    best, best_s = png, (own_in, sheet_in)
        if why is not None:
            why.update({"png": near[1], "own_in": near[2], "sheet_in": near[3],
                        "folders": [os.path.basename(f) for f in folders], "pngs": n_png})
        if dirty:
            _save_index()
    return best


def render(png, size):
    """The official sheet as a template image: SCS's line art on white, at `size`."""
    with Image.open(png) as im:
        im = im.convert("RGBA")
        if im.size != tuple(size):
            im = im.resize(size, Image.Resampling.LANCZOS)
    a = im.getchannel("A")
    small = a.resize((max(1, size[0] // 2), max(1, size[1] // 2)), Image.Resampling.BOX)
    inside = fill_holes(np.array(small) > 0)            # line-only sets: shade the enclosed faces too
    inside = np.array(Image.fromarray(inside.astype(np.uint8) * 255).resize(size, Image.Resampling.BILINEAR)) > 127
    base = np.where(inside[..., None], 255, 150).astype(np.uint8).repeat(3, axis=2)   # faces white, rest grey
    bg = Image.fromarray(base, "RGB").convert("RGBA")
    bg.alpha_composite(im)
    return bg.convert("RGB")


def guide(png, size, unused_alpha=200):
    """Preview overlay from the folder's sheet (same format as texture.make_guide): its line art
    near-black, the area outside the parts shaded dark slate, everything else transparent."""
    with Image.open(png) as im:
        im = im.convert("RGBA")
        if im.size != tuple(size):
            im = im.resize(size, Image.Resampling.LANCZOS)
    a = np.array(im.getchannel("A")).astype(np.int32)
    lum = np.array(im.convert("L")).astype(np.int32)
    lines = (a > 90) & (lum < 140)
    small = im.getchannel("A").resize((max(1, size[0] // 2), max(1, size[1] // 2)), Image.Resampling.BOX)
    inside = fill_holes(np.array(small) > 0)
    inside = np.array(Image.fromarray(inside.astype(np.uint8) * 255).resize(size, Image.Resampling.BILINEAR)) > 127
    out = np.zeros((size[1], size[0], 4), np.uint8)
    out[~inside] = (34, 38, 46, unused_alpha)
    out[lines] = (0, 0, 0, 210)
    return Image.fromarray(out, "RGBA")


def candidates(root, vehicle_id, vehicle_name):
    """Every PNG a user could pick for this vehicle: its candidate folders first, and when the
    folder names don't narrow anything down, the whole template folder."""
    folders = candidate_folders(root, vehicle_id, vehicle_name) or [os.path.join(root, d) for d in _folders(root)]
    out = []
    for f in folders:
        out += sorted(_pngs(f))
    return out


def shape_size(png, size, pot=True):
    """`size` (SkinForge's sheet for the part) re-shaped like the template-folder sheet: same long
    side, the sheet's own width:height. An 8192x1024 community sheet on a part SkinForge makes
    4096x1024 -> 4096x512, so the template, the artwork and the built texture are all 8:1."""
    import math
    e = _index.get(png)
    if e:
        w0, h0 = e["size"]
    else:
        with Image.open(png) as im:
            w0, h0 = im.size
    L = max(size)
    if w0 >= h0:
        w, h = L, L * h0 / w0
    else:
        w, h = L * w0 / h0, L
    if pot:
        f = lambda n: max(4, 1 << max(2, round(math.log2(max(n, 1)))))
        return (f(w), f(h))
    return (max(4, round(w)), max(4, round(h)))


def label(png, root):
    rel = os.path.relpath(png, root) if root else os.path.basename(png)
    return rel.replace("\\", "/")
