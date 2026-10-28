"""SCS base-game trailers: paint slots and templates straight from the game's own files.

Proven in game (SkinForge box test 2, 2026-10-27): an SCS paint job maps one texture per
part through `simple_paint_job_data` overrides (`acc_list[]: "<slot>.<unit>"`), sampled with
the model's second UV set (uv1) on pieces whose material effect contains `truckpaint`.
Texture file names are free, so SkinForge names them itself and can paint parts SCS never
gives a texture (the painted front wall).

Every accessory/body def of `def/vehicle/trailer_owned/scs.*` with a paintable model becomes a
part; parts of one slot whose UV layouts are the same (hole / lift-gate variants, the reefer
front walls) share one texture, like SCS does. The template is drawn from the uv1 triangles.
"""
from __future__ import annotations

import io
import math
import re
import statistics
import struct
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import sii
from .pmg import ModelError, is_paintable, mat_effect, read_pmd_materials, read_pmg

TEMPLATE_VERSION = 6
DENSITY = 256            # px per metre at "native" size (= SCS's own 53' box body: 4096 px over 16 m)
MERGE_IOU = 0.97
MIN_SIDE = 64

TRAILER_NAMES = {
    "scs.box": "SCS Box trailer",
    "scs.flatbed": "SCS Flatbed / curtainsider",
    "scs.dropdeck": "SCS Drop deck",
    "scs.lowboy": "SCS Lowboy",
    "scs.gooseneck": "SCS Gooseneck",
    "scs.livestock": "SCS Livestock",
    "scs.log": "SCS Log trailer",
    "scs.dumper": "SCS Dumper",
    "scs.bottomdumper": "SCS Bottom dumper",
    "scs.grainhopper": "SCS Grain hopper",
    "scs.drybulk": "SCS Dry bulk",
    "scs.bulkfeed": "SCS Bulk feed",
    "scs.chipvan": "SCS Chip van",
    "scs.chemtank": "SCS Chemical tanker",
    "scs.foodtank": "SCS Food tanker",
    "scs.fueltank": "SCS Fuel tanker",
    "scs.gastank": "SCS Gas tanker",
}
SLOT_NAMES = {"body": "Body", "f_body": "Front", "rear_body": "Rear", "chs_f_l": "Left skirt",
              "chs_f_r": "Right skirt", "chassis": "Chassis", "r_bumper": "Rear bumper",
              "r_mudflap": "Mudflaps", "markers": "Markers"}
SLOT_ORDER = ["body", "f_body", "rear_body", "chs_f_l", "chs_f_r", "chassis"]
WORDS = {"dryvan": "dry van", "insulated": "insulated", "reefer": "reefer", "curtain": "curtain",
         "paint": "painted wall", "wind_def": "wind deflector", "cooler": "reefer unit", "king": "King reefer unit",
         "ch_unit": "chrome reefer unit", "roll_up": "roll-up door", "roll_up_g": "roll-up door + lift gate",
         "simple": "swing doors", "simple_a": "aero doors", "aero": "aero skirt"}


@dataclass
class Part:
    acc: str            # acc_list name "<slot>.<unit>"
    slot: str
    unit: str
    model: str


@dataclass
class Layout:
    """Paintable geometry of one model: uv1 triangles (+ 3D for scale / orientation)."""
    model: str
    tris: list          # [((u,v)x3, (P)x3, normal)]

    def signature(self):
        return tuple(round(c, 3) for t in self.tris[:24] for uv in t[0] for c in uv)


@dataclass
class Group:
    slot: str
    parts: list = field(default_factory=list)
    layouts: list = field(default_factory=list)
    scale: tuple = (0.0, 0.0)     # metres per full u / v
    native: tuple = (0, 0)        # texture size at DENSITY

    @property
    def names(self):
        return [p.unit for p in self.parts]


# --- geometry ----------------------------------------------------------------
_layout_cache: dict = {}


def _disk_key(src, model_path):
    st = getattr(src, "stat", None)
    if st is None:
        return None
    a, b = st(model_path), st(model_path[:-4] + ".pmg")
    if not a or not b:
        return None
    import hashlib
    return hashlib.sha1(f"L{TEMPLATE_VERSION}|{a}|{b}".encode()).hexdigest()[:24]


def _disk_dir():
    import os
    from .platform_util import app_data_dir
    d = os.path.join(app_data_dir(), "cache", "models")
    os.makedirs(d, exist_ok=True)
    return d


def load_layout(src, model_path: str):
    """Paintable geometry of a model; parsed once, then cached on disk (the extract usually
    sits on a slow cloud drive)."""
    import os
    import pickle
    key = (id(src), model_path.lower())
    if key in _layout_cache:
        return _layout_cache[key]
    dk = None
    try:
        dk = _disk_key(src, model_path)
        if dk:
            fp = os.path.join(_disk_dir(), dk + ".pkl")
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    lay = pickle.load(f)
                _layout_cache[key] = lay
                return lay
    except Exception:
        dk = None
    lay = _parse_layout(src, model_path)
    if dk:
        try:
            with open(os.path.join(_disk_dir(), dk + ".pkl"), "wb") as f:
                pickle.dump(lay, f, protocol=4)
        except Exception:
            pass
    _layout_cache[key] = lay
    return lay


def _parse_layout(src, model_path: str):
    lay = None
    pmd = src.read(model_path)
    if not pmd or not model_path.lower().endswith(".pmd"):
        return None
    try:
        mats = read_pmd_materials(pmd)
    except (ModelError, struct.error, ValueError, IndexError):
        return None
    effects = {mp: mat_effect(src.read(mp)) for mp in set(mats)}
    if not any(is_paintable(e) for e in effects.values()):
        return None          # no paint on this model: skip the (big) geometry file
    pmg = src.read(model_path[:-4] + ".pmg")
    if not pmg:
        return None
    try:
        tris = []
        for pc in read_pmg(pmg):
            if pc.material >= len(mats) or not pc.uvs or not pc.pos:
                continue
            if not is_paintable(effects[mats[pc.material]]):
                continue
            # paint mask UV: the second set when the piece has one (trailer bodies / parts,
            # proven in game), else the only set (truck cabs, skirts, mirrors)
            uv = pc.uvs[1] if len(pc.uvs) >= 2 else pc.uvs[0]
            for a, b, c in pc.tris:
                n = (0.0, 0.0, 0.0)
                if pc.nrm:
                    n = tuple(pc.nrm[a][k] + pc.nrm[b][k] + pc.nrm[c][k] for k in range(3))
                tris.append(((uv[a], uv[b], uv[c]), (pc.pos[a], pc.pos[b], pc.pos[c]), n))
        if tris:
            lay = Layout(model_path, _dedupe(tris))
    except (ModelError, struct.error, ValueError, IndexError):
        lay = None
    return lay


def _dedupe(tris):
    """The same mesh is often shipped once per material (dry van / insulated / reefer)."""
    seen, out = set(), []
    for t in tris:
        k = tuple(round(c, 4) for uv in t[0] for c in uv)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _jacobian(t):
    """dP/du, dP/dv of one triangle (None when the UV triangle is degenerate)."""
    (uv0, uv1, uv2), (p0, p1, p2), _n = t
    du1, dv1 = uv1[0] - uv0[0], uv1[1] - uv0[1]
    du2, dv2 = uv2[0] - uv0[0], uv2[1] - uv0[1]
    det = du1 * dv2 - du2 * dv1
    if abs(det) < 1e-9:
        return None
    e1, e2 = _sub(p1, p0), _sub(p2, p0)
    dpdu = tuple((e1[k] * dv2 - e2[k] * dv1) / det for k in range(3))
    dpdv = tuple((e2[k] * du1 - e1[k] * du2) / det for k in range(3))
    return dpdu, dpdv


def layout_scale(layouts):
    """Metres per full u and per full v, as the median weighted by UV area: the big visible
    panels decide, not thin edge strips (this reproduces SCS's own atlas tile sizes exactly)."""
    rows = []
    for lay in layouts:
        for t in lay.tris:
            j = _jacobian(t)
            if not j:
                continue
            a, b, c = t[0]
            area = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
            rows.append((area, _len(j[0]), _len(j[1])))
    if not rows:
        return (1.0, 1.0)

    def wmed(k):
        rs = sorted(rows, key=lambda r: r[k])
        half, acc = sum(r[0] for r in rs) / 2, 0.0
        for r in rs:
            acc += r[0]
            if acc >= half:
                return r[k]
        return rs[-1][k]
    return (wmed(1), wmed(2))


def _pot(x):
    return int(2 ** max(round(math.log2(max(x, 1))), math.log2(MIN_SIDE)))


def native_size(scale):
    w, h = (min(4096, _pot(s * DENSITY)) for s in scale)
    return (w, h)


def output_size(native, res):
    """Texture size at the project resolution: SCS's texel density scaled to `res`, but never
    below half of `res` on the long side (front walls and doors get logos up close)."""
    f = res / 4096
    w, h = max(4, int(native[0] * f)), max(4, int(native[1] * f))
    nl = max(native)
    while max(w, h) < res // 2 and max(w, h) < nl * 4:   # small parts (mirrors) at most 4x SCS's size
        w, h = w * 2, h * 2
    while max(w, h) > res:
        w, h = max(4, w // 2), max(4, h // 2)
    return (w, h)


def _footprint(layouts, n=512):
    im = Image.new("L", (n, n))
    d = ImageDraw.Draw(im)
    for lay in layouts:
        for t in lay.tris:
            d.polygon([(uv[0] * n, uv[1] * n) for uv in t[0]], fill=255)
    return im


def _iou(a, b):
    inter = ImageChops.multiply(a, b).histogram()[255]
    union = ImageChops.lighter(a, b).histogram()[255]
    return inter / union if union else 0.0


# --- catalog -----------------------------------------------------------------
def _units(src, d, loader):
    out = []
    for fn in src.listdir(d):
        if fn.endswith("/") or not fn.endswith(".sii"):
            continue
        p = f"{d}/{fn}"
        data = src.read(p)
        if data:
            try:
                out.extend(sii.parse_file(data, p, loader))
            except Exception:
                pass
    return out


def _own(units, suffix):
    """Units named <name>.<vehicle>.<slot> like SCS's; mods that name them their own way
    (Adwin Stam: fl1.asfl.body on as.asfl) keep every unit of the folder."""
    strict = [u for u in units if u.name.endswith(suffix)]
    return strict if strict else units


def scan_parts(src, tid):
    """[(Part, Layout)] for every paintable body / accessory / chassis unit of an SCS trailer."""
    loader = src.read
    root = f"/def/vehicle/trailer_owned/{tid}"
    found = []
    dirs = [("body", f"{root}/body"), ("chassis", f"{root}/chassis")]
    for sub in src.listdir(f"{root}/accessory"):
        if sub.endswith("/"):
            dirs.append((sub[:-1], f"{root}/accessory/{sub[:-1]}"))
    for slot, d in dirs:
        for u in _own(_units(src, d, loader), f".{tid}.{slot}"):
            model = u.get_str("model") or u.get_str("exterior_model")
            if not model:
                continue
            unit = u.name.split(".")[0]
            lay = load_layout(src, model)
            if lay is None:
                continue
            found.append((Part(f"{slot}.{unit}", slot, unit, model), lay))
    return found


def group_parts(found):
    groups = []
    by_model = {}
    for part, lay in found:
        g = by_model.get((part.slot, lay.model.lower()))
        if g is None:
            g = Group(part.slot, [], [lay])
            by_model[(part.slot, lay.model.lower())] = g
            groups.append(g)
        g.parts.append(part)
    for g in groups:
        g.scale = layout_scale(g.layouts)
    # same slot + same UV layout + same texel scale -> one texture (SCS: paint_53 + paint_53h)
    merged = []
    for g in groups:
        fp = _footprint(g.layouts)
        for m in merged:
            if m.slot != g.slot:
                continue
            if any(abs(a / b - 1) > 0.05 for a, b in zip(m.scale, g.scale) if b):
                continue
            if _iou(m._fp, fp) >= MERGE_IOU:
                m.parts += g.parts
                m.layouts += g.layouts
                m._fp = ImageChops.lighter(m._fp, fp)
                break
        else:
            g._fp = fp
            merged.append(g)
    for g in merged:
        g.native = native_size(g.scale)
        del g._fp
    return merged


def _length(names):
    ls = sorted({int(x) for n in names for x in re.findall(r"(\d{2})", n)}, reverse=True)
    return ls


def _base(unit):
    return re.sub(r"_?\d+[a-z]*$", "", unit)


def group_label(g: Group) -> str:
    names = g.names
    ls = _length(names)
    ln = f" {ls[0]}'" if len(ls) == 1 else ""
    primary = min(names, key=lambda n: (len(n), n))
    base = _base(primary)
    word = WORDS.get(primary) or WORDS.get(base) or base.replace("_", " ")
    slot = SLOT_NAMES.get(g.slot, g.slot.replace("_", " ").capitalize())
    if g.slot == "body":
        return f"Body{ln}"
    if g.slot in ("chs_f_l", "chs_f_r"):
        return f"{slot}{' aero' if base == 'aero' else ln}"
    if g.slot == "f_body" and "paint" in names:
        return "Front wall"
    if g.slot == "chassis":
        if primary.startswith("dolly"):
            return "Dolly" + (" · " + word if base != "dolly" else "")
        return f"Chassis{ln}" if ln else f"Chassis · {primary.replace('_', ' ')}"
    return f"{slot} · {word}"


def part_name(p: Part) -> str:
    if p.slot == "chassis":
        return _chassis_name(p.unit)
    base = _base(p.unit)
    w = WORDS.get(p.unit) or WORDS.get(base) or base.replace("_", " ")
    if p.slot.startswith("chs_") and base == "paint":
        w = "painted skirt"
    ls = re.findall(r"(\d{2})", p.unit)
    extra = " (cut-out)" if re.search(r"\d+h$", p.unit) else ""
    return f"{w}{' ' + ls[0] + chr(39) if ls and p.slot != 'body' else ''}{extra}"


_CHS = {"d": "double", "double": "double", "bd": "B-double", "hook": "hook", "r": "rear", "f": "front",
        "sp": "spread", "conv": "converter", "4s": "4-axle steer"}


def _chassis_name(unit):
    """ch_53_4a -> 53' 4 axles, ch_28_d -> 28' double, dolly_conv_2a -> dolly converter 2 axles"""
    bits = unit.split("_")
    out = []
    for b in bits:
        if b == "ch":
            continue
        if re.fullmatch(r"\d{2}", b):
            out.append(f"{b}'")
        elif re.fullmatch(r"\d+a", b):
            out.append(f"{b[:-1]} axles")
        else:
            out.append(_CHS.get(b, b))
    return " ".join(out) or unit


def _sort_key(g: Group):
    ls = _length(g.names)
    return (SLOT_ORDER.index(g.slot) if g.slot in SLOT_ORDER else 99, -(ls[0] if ls else 0), group_label(g))


# --- templates -----------------------------------------------------------------
_FONT_CACHE = {}


def _font(size):
    size = max(8, int(size))
    if size not in _FONT_CACHE:
        f = None
        for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            try:
                f = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        if f is None:
            try:
                f = ImageFont.load_default(size=size)
            except TypeError:
                f = ImageFont.load_default()
        _FONT_CACHE[size] = f
    return _FONT_CACHE[size]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def face_labels(g: Group):
    """For bodies (model space = trailer space: +x right, +y up, -z front): name each face
    and say which way the front is and whether the texture reads mirrored / upside down."""
    if g.slot not in ("body", "chassis", "cabin"):   # accessories are modelled in locator space
        return []
    faces = {}
    for lay in g.layouts:
        for t in lay.tris:
            n = t[2]
            if _len(n) < 1e-6:
                continue
            ax = max(range(3), key=lambda k: abs(n[k]))
            faces.setdefault(("+" if n[ax] > 0 else "-") + "xyz"[ax], []).append(t)
    names = {"+x": "RIGHT SIDE", "-x": "LEFT SIDE", "+y": "ROOF", "-y": "UNDERSIDE", "-z": "FRONT",
             "+z": "BACK WALL" if g.slot == "cabin" else "REAR"}
    out = []
    for key, ts in faces.items():
        if g.slot == "cabin" and key not in ("+x", "-x", "+z", "+y"):
            continue   # curved cab fronts / pillars: labels there are noise
        js = [j for j in (_jacobian(t) for t in ts) if j]
        if not js:
            continue
        dpdu = tuple(statistics.median(j[0][k] for j in js) for k in range(3))
        dpdv = tuple(statistics.median(j[1][k] for j in js) for k in range(3))
        text, notes = names[key], []
        if key in ("+x", "-x"):
            # +u towards the front (-z) = front on the right of the sheet
            text = f"{text}  FRONT >" if dpdu[2] < 0 else f"< FRONT  {text}"
            right = (0.0, 0.0, -1.0 if key == "+x" else 1.0)   # viewer's right, looking at this side
            mirrored, upside = _dot(dpdu, right) < 0, dpdv[1] > 0
            if mirrored and upside:
                notes.append("ROTATED 180° - paint it upside down")
            elif mirrored:
                notes.append("MIRRORED")
            elif upside:
                notes.append("UPSIDE DOWN")
        elif key == "+y":
            text = f"{text}  FRONT >" if dpdu[2] < 0 else f"< FRONT  {text}"
            notes.append("left side at the bottom" if dpdv[0] > 0 else "left side at the top")
        elif key in ("-z", "+z") and dpdv[1] > 0:
            notes.append("UPSIDE DOWN")
        # where the face really is: its separate regions (a tank is one sheet wrapped around the
        # barrel, its underside split over the seam) = connected areas of the face in a small mask
        N = 256
        m = Image.new("L", (N, N))
        dm = ImageDraw.Draw(m)
        for t in ts:
            dm.polygon([(uv[0] * N, uv[1] * N) for uv in t[0]], fill=255)
        m = m.filter(ImageFilter.MaxFilter(5))      # join sliver triangles (fan-shaped tank ends)
        regions = []
        for _ in range(24):
            bb = m.getbbox()
            if not bb:
                break
            x0, y0 = bb[0], bb[1]
            row = m.crop((0, y0, N, y0 + 1)).tobytes()
            sx = next(i for i, c in enumerate(row) if c == 255)
            ImageDraw.floodfill(m, (sx, y0), 128)
            reg = m.point(lambda v: 255 if v == 128 else 0)
            area = reg.histogram()[255] / (N * N)
            regions.append((area, reg.getbbox()))
            m = m.point(lambda v: 0 if v == 128 else v)
        if not regions:
            continue
        top = max(r[0] for r in regions)
        if top < 0.005:
            continue
        for area, bb in regions:
            if area < top * 0.3 or (bb[3] - bb[1]) < N * 0.06 or (bb[2] - bb[0]) < N * 0.06 \
                    or (g.slot == "cabin" and area < 0.03):
                continue   # too small / thin to carry a readable label (tank bands, rims)
            out.append(((bb[0] / N, bb[1] / N, bb[2] / N, bb[3] / N), text, notes))
    return out


def render_template(g: Group, size, label_group=None) -> Image.Image:
    """Black background, parts white with black wireframe (the style SkinForge's guide /
    mirrored-piece tools read), plus face names on bodies."""
    W, H = size
    ss = 2 if max(W, H) <= 2048 else 1
    im = Image.new("L", (W * ss, H * ss), 40)   # dark grey (< 64 still reads as "unused" for the tools)
    d = ImageDraw.Draw(im)
    lw = max(1, round(max(W, H) / 2048)) * ss
    polys = [[(uv[0] * W * ss, uv[1] * H * ss) for uv in t[0]] for lay in g.layouts for t in lay.tris]
    for p in polys:
        d.polygon(p, fill=255)
    # outline only the panel borders: edges used by one triangle (per model), not the triangulation
    for lay in g.layouts:
        count = {}
        for t in lay.tris:
            pts = [(round(uv[0], 5), round(uv[1], 5)) for uv in t[0]]
            for i in range(3):
                a, b = pts[i], pts[(i + 1) % 3]
                k = (a, b) if a <= b else (b, a)
                count[k] = count.get(k, 0) + 1
        # the model's inner edges as thin light lines (seams, lights, grilles - like an author's
        # wireframe template); panel borders dark on top
        for (a, b), n in count.items():
            if n > 1:
                d.line([(a[0] * W * ss, a[1] * H * ss), (b[0] * W * ss, b[1] * H * ss)], fill=200, width=ss)
        for (a, b), n in count.items():
            if n == 1:
                d.line([(a[0] * W * ss, a[1] * H * ss), (b[0] * W * ss, b[1] * H * ss)], fill=0, width=lw)
    if ss != 1:
        im = im.resize((W, H), Image.Resampling.BOX)
    d = ImageDraw.Draw(im)
    for (u0, v0, u1, v1), text, notes in face_labels(label_group or g):
        bw, bh = (u1 - u0) * W, (v1 - v0) * H
        fs = max(10, min(bh * 0.25, bw * 0.85 / (0.72 * max(len(text), 6))))
        cx, cy = (u0 + u1) / 2 * W, (v0 + v1) / 2 * H
        d.text((cx, cy - (fs * 0.35 if notes else 0)), text, fill=90, font=_font(fs), anchor="mm")
        if notes:
            d.text((cx, cy + fs * 0.75), " · ".join(notes), fill=90, font=_font(fs * 0.55), anchor="mm")
    return im.convert("RGBA")


def png(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.save(b, format="PNG", compress_level=1)
    return b.getvalue()


def template_path(tid, token, size):
    return f"/@skinforge/scs_v{TEMPLATE_VERSION}/{tid}/{token}_{size[0]}x{size[1]}.png"


TRUCK_SLOT_NAMES = {"cabin": "Cab", "sideskirt": "Side skirts", "sunshld": "Sun visor", "mirror": "Mirrors",
                    "f_mirror": "Front mirror", "s_mirror": "Side mirror", "r_fender": "Rear fenders",
                    "r_fendr_top": "Rear fender tops", "r_bumper": "Rear bumper", "r_chs_cover": "Rear chassis cover",
                    "f_mudflap": "Front mudflaps", "f_equip": "Front equipment", "f_logo": "Front logo",
                    "exhaust_m": "Exhaust", "f_bumper": "Front bumper", "f_grill": "Grill", "tank": "Tanks"}


def truck_name(game, tid):
    brand, _, model = tid.partition(".")
    nice = model.replace("_", " ").strip()
    nice = " ".join(w.upper() if len(w) <= 3 and not w.isdigit() else w.capitalize() for w in nice.split())
    return f"{brand.replace('_', ' ').title()} {nice}".strip()


def list_trucks(game):
    """Truck ids with cabins (cheap: directory listing only)."""
    out = []
    for entry in game.listdir("/def/vehicle/truck"):
        if entry.endswith("/") and "cabin/" in game.listdir(f"/def/vehicle/truck/{entry[:-1]}"):
            out.append(entry[:-1])
    return out


def scs_override_parts(game, tid):
    """acc_list names SCS itself gives their own texture in this truck's paint jobs."""
    root = f"/def/vehicle/truck/{tid}/paint_job/accessory"
    names = set()
    for fn in game.listdir(root):
        if fn.endswith("/") or not fn.endswith(".sii"):
            continue
        data = game.read(f"{root}/{fn}")
        if not data:
            continue
        for m in re.finditer(rb'acc_list\[\]\s*:\s*"([^"]+)"', data):
            names.add(m.group(1).decode("ascii", "replace"))
    return names


def scan_truck(game, tid, res, progress=None):
    """Slots of one SCS truck: one cab sheet per cab model (the paint job's main texture, one
    paint job unit per cab like SCS's own _a/_b/_c liveries) + the parts SCS gives their own
    texture. Other paintable accessories use the cab texture and are drawn on the cab sheets."""
    from .catalog import Slot, Trailer
    loader = game.read
    root = f"/def/vehicle/truck/{tid}"
    cabs = {}      # model -> [unit names]
    for u in _own(_units(game, f"{root}/cabin", loader), f".{tid}.cabin"):
        model = u.get_str("model") or u.get_str("detail_model")   # Adwin Stam: detail_model only
        if model and u.name.endswith(".cabin"):
            cabs.setdefault(model, []).append(u.name)
    overridden = scs_override_parts(game, tid)
    parts, extras = [], []   # extras: (Layout, cabin units it fits or None = all)
    subs = [x[:-1] for x in game.listdir(f"{root}/accessory") if x.endswith("/")]
    for i, slot in enumerate(subs):
        if progress:
            progress(f"Reading {tid} parts ({i + 1}/{len(subs)}): {slot}")
        for u in _own(_units(game, f"{root}/accessory/{slot}", loader), f".{tid}.{slot}"):
            model = u.get_str("exterior_model")
            if not model:
                continue
            lay = load_layout(game, model)
            if lay is None:
                continue
            unit = u.name.split(".")[0]
            acc = f"{slot}.{unit}"
            if acc in overridden:
                parts.append((Part(acc, slot, unit, model), lay))
            else:
                fits = [x for x in u.get_list("suitable_for") if x.endswith(".cabin")]
                extras.append((lay, fits or None))
    slots = []
    for model, units in sorted(cabs.items(), key=lambda kv: kv[0]):
        lay = load_layout(game, model)
        if lay is None:
            continue
        layouts = [lay] + [el for el, fits in extras if fits is None or any(f in units for f in fits)]
        g = Group("cabin", [Part(f"cabin.{x.split('.')[0]}", "cabin", x.split(".")[0], model) for x in units], layouts)
        g.scale = layout_scale([lay])
        g.native = native_size(g.scale)
        names = [x.split(".")[0] for x in units]
        token = re.sub(r"[^a-z0-9_]", "_", f"cab_{min(names, key=len)}")
        size = output_size(g.native, res)
        size = (max(size[0], res), max(size[1], res)) if g.native[0] == g.native[1] else size   # cabs at full res
        path = template_path(tid, token, size)
        g_cab = Group("cabin", g.parts, [lay])   # face labels from the cab itself
        game.register_virtual(path, (lambda g=g, gc=g_cab, size=size: png(render_template(g, size, label_group=gc))))
        sl = Slot(key=f"{tid}/{token}", trailer=tid, token=token,
                  label="Cab · " + " / ".join(_cab_name(n) for n in names),
                  suitable_for=list(units), targets=[(x, _cab_name(x.split(".")[0])) for x in units],
                  template_dds=path, kind="scs")
        sl.vroot, sl.cab, sl.cabin_units = "truck", True, list(units)
        sl.tex_size, sl.native, sl.acc_list = size, g.native, []
        slots.append(sl)
    groups = group_parts(parts)
    groups.sort(key=lambda g: (g.slot, group_label(g)))
    used = {s.token for s in slots}
    base_labels = [_truck_part_label(g) for g in groups]
    labels = list(base_labels)
    for i, g in enumerate(groups):
        if base_labels.count(base_labels[i]) > 1:
            labels[i] += " · " + ", ".join(dict.fromkeys(p.unit.replace("_", " ") for p in g.parts))
    for g, label in zip(groups, labels):
        g.parts.sort(key=lambda p: (len(p.unit), p.unit))
        token = re.sub(r"[^a-z0-9_]", "_", f"{g.slot}_{g.parts[0].unit}")
        while token in used:
            token += "x"
        used.add(token)
        size = output_size(g.native, res)
        path = template_path(tid, token, size)
        game.register_virtual(path, (lambda g=g, size=size: png(render_template(g, size))))
        sl = Slot(key=f"{tid}/{token}", trailer=tid, token=token, label=label, suitable_for=[],
                  targets=[(p.acc, p.unit.replace("_", " ")) for p in g.parts], template_dds=path, kind="scs")
        sl.vroot, sl.acc_list, sl.tex_size, sl.native = "truck", [p.acc for p in g.parts], size, g.native
        slots.append(sl)
    return Trailer(tid, truck_name(game, tid), slots)


def mod_part_template(src, tdir, tid, accs, res=2048):
    """Template PNG for a mod truck part from its 3D models (None if the models can't be read,
    e.g. the main pack isn't loaded)."""
    loader = src.read
    layouts = []
    by_slot = {}
    for a in accs:
        slot, _, unit = a.partition(".")
        by_slot.setdefault(slot, set()).add(unit)
    for slot, units in by_slot.items():
        for u in _units(src, f"{tdir}/accessory/{slot}", loader):
            if u.name.split(".")[0] in units and u.get_str("exterior_model"):
                lay = _parse_layout(src, u.get_str("exterior_model"))
                if lay is not None:
                    layouts.append(lay)
    if not layouts:
        return None
    g = Group(next(iter(by_slot)), [], layouts)
    g.scale = layout_scale(layouts)
    g.native = native_size(g.scale)
    w, h = g.native
    f = res / max(w, h)
    return png(render_template(g, (max(4, int(w * f)), max(4, int(h * f)))))


def _cab_name(unit):
    return {"highline": "Highline", "normal": "Normal", "highline_8x4": "Highline 8x4", "lowline": "Lowline",
            "low": "Low", "high": "High"}.get(unit, unit.replace("_", " ").capitalize())


def _truck_part_label(g):
    base = TRUCK_SLOT_NAMES.get(g.slot, g.slot.replace("_", " ").capitalize())
    cfgs = sorted({re.match(r"(?:low_)?(\d+x\d+l?)", p.unit).group(1) for p in g.parts
                   if re.match(r"(?:low_)?(\d+x\d+l?)", p.unit)})
    return f"{base} {'/'.join(cfgs)}" if cfgs else base


NO_PAINT = ("No paintable parts: the author baked the colours into the textures (no truckpaint "
            "materials), so the game can't put a skin on it")


NO_MODELS = "Its 3D models aren't in the loaded packs - add the truck's main .scs too (not only the Open Definition)"


def empty_reason(game, tid):
    """Why a truck gave no slots: models missing (main pack not loaded) or nothing paintable."""
    root = f"/def/vehicle/truck/{tid}/cabin"
    for u in _units(game, root, game.read):
        m = u.get_str("model") or u.get_str("detail_model")
        if m and game.exists(m):
            return NO_PAINT
    return NO_MODELS


def is_mod(game, d):
    """True when this def folder doesn't come from the SCS game files."""
    base = getattr(game, "base", game if getattr(game, "kind", "") == "game" and not hasattr(game, "layers") else None)
    return base is None or not base.listdir(d)


def vehicle_name(game, d, tid):
    """Brand + name from data.sii info[] (mods), else the id."""
    data = game.read(f"{d}/data.sii")
    if data:
        info = re.findall(rb'info\[\]\s*:\s*"([^"]*)"', data)
        nice = [x.decode("utf-8", "replace").strip() for x in info[:2] if x.strip() and not x.startswith(b"@@")]
        if nice:
            return " ".join(dict.fromkeys(nice))
    return tid


def scan(src, res: int = 4096, progress=None, skip=()):
    """Trailer groups for the catalog: SCS's own trailers + mod trailers without an author
    template (templates drawn from their 3D models). Imported lazily to avoid a cycle."""
    from .catalog import Slot, Trailer
    game = getattr(src, "game", None)
    if game is None:
        return []
    out = []
    for entry in game.listdir("/def/vehicle/trailer_owned"):
        if not entry.endswith("/") or entry[:-1] in skip:
            continue
        tid = entry[:-1]
        mod = is_mod(game, f"/def/vehicle/trailer_owned/{tid}")
        if not tid.startswith("scs.") and not mod:
            continue
        tname = vehicle_name(game, f"/def/vehicle/trailer_owned/{tid}", tid) if mod else TRAILER_NAMES.get(tid, tid)
        if progress:
            progress(f"Reading {tname}…")
        try:
            groups = group_parts(scan_parts(game, tid))
        except Exception:
            groups = []
        if not groups:
            if mod:
                t = Trailer(tid, tname, [])
                t.vkind = "mod3d"
                t.error = NO_PAINT
                out.append(t)
            continue
        groups.sort(key=_sort_key)
        base_labels = [group_label(g) for g in groups]
        labels = list(base_labels)
        for i, g in enumerate(groups):
            if base_labels.count(base_labels[i]) > 1:
                labels[i] += " · " + ", ".join(dict.fromkeys(part_name(p) for p in g.parts))
        slots = []
        used = set()
        for g, label in zip(groups, labels):
            g.parts.sort(key=lambda p: (p.unit != "paint", len(p.unit), p.unit))
            token = re.sub(r"[^a-z0-9_]", "_", f"{g.slot}_{g.parts[0].unit}")
            while token in used:
                token += "x"
            used.add(token)
            size = output_size(g.native, res)
            path = template_path(tid, token, size)
            game.register_virtual(path, (lambda g=g, size=size: png(render_template(g, size))))
            s = Slot(key=f"{tid}/{token}", trailer=tid, token=token, label=label,
                     suitable_for=[], targets=[(p.acc, part_name(p)) for p in g.parts],
                     template_dds=path, kind="scs")
            s.acc_list = [p.acc for p in g.parts]
            s.tex_size = size
            s.native = g.native
            s.main = False   # SCS paints chassis through acc_list "chassis.<unit>" (fuel tanker bas_1)
            slots.append(s)
        t = Trailer(tid, tname, slots)
        if mod:
            t.vkind = "mod3d"
        out.append(t)
    out.sort(key=lambda t: t.name.lower())
    return out


def scan_trucks(src, res: int = 4096, loaded=(), progress=None, skip=()):
    """SCS trucks: listed cheaply; only the ones in `loaded` have their models read."""
    from .catalog import Trailer
    game = getattr(src, "game", None)
    if game is None:
        return []
    out = []
    for tid in list_trucks(game):
        if tid in skip:
            continue
        if tid in loaded:
            try:
                t = scan_truck(game, tid, res, progress)
            except Exception:
                import traceback
                traceback.print_exc()
                t = Trailer(tid, truck_name(game, tid), [])
                t.error = "Could not read this truck's models"
        else:
            t = Trailer(tid, truck_name(game, tid), [])
        if tid in loaded and not t.slots and not getattr(t, "error", None):
            t.error = empty_reason(game, tid)
        t.lazy = tid not in loaded
        t.vkind = "mod3d_truck" if is_mod(game, f"/def/vehicle/truck/{tid}") else "scs_truck"
        out.append(t)
    out.sort(key=lambda t: t.name.lower())
    return out
