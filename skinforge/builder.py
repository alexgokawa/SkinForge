"""Turn a project (settings + artwork per slot) into a ready-to-load .scs mod."""
from __future__ import annotations

import hashlib
import io
import struct
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from . import texture as T
from .logos import GROUP_ID as LOGO_GROUP, LOGO_SIZE, is_tex, texture_paths as logo_texture_paths

PROJECT_VERSION = 2
ART_FIELDS = ("art", "overrides", "upright", "color")   # per skin; everything else in slots[key] is shared

DEFAULT_PROJECT = {
    "version": PROJECT_VERSION,
    "paint_name": "My Trailer Skin",
    "mod_name": "My Trailer Skins",
    "author": "",
    "mod_version": "1.0",
    "code": "skin",
    "price": 1000,
    "finish": "both",            # gloss | metallic | matte joined by "+" ("both" = gloss+metallic)
    "base_color": "#9a9a9a",
    "color_locked": False,
    "metallic": {
        "flake_color": "#ffffff",
        "flip_color": "#6b6b6b",
        "flip_strength": 0.94,
        "flake_density": 1.5,
        "flake_shininess": 30.0,
        "flake_clearcoat_rolloff": 5.0,
    },
    "resolution": 4096,
    "res_by_vehicle": {},
    "finish_by_vehicle": {},      # id -> "gloss+matte" etc. (overrides "finish")
    "lock_by_vehicle": {},
    "traffic_by_vehicle": {},     # SCS truck id -> "own" (default) / "ai" / "both": paint job and/or AI traffic        # id -> true / false (overrides "color_locked")         # truck / trailer / car id -> 2048 / 4096 / 8192 (overrides "resolution")
    "template_size": "",         # template export size: "" = texture size, or 2048 / 4096 / 8192
    "truck_uvset": "normal",     # normal | both (also ship alternate-UV-set units for trucks)
    "sources": [],
    "output_dir": "",
    "description": "",
    "icon": "",                  # art/icon.png (256x64) - optional
    "mod_icon": "",              # art/mod_icon.png (276x162) - optional
    "slots": {},                 # key -> {"art": rel path, "overrides": {"0": rel path}}
    "ids": {},                   # key -> int  (never reused, never changed)
    "next_id": 1,
    "built": {},                 # unit token -> slot key (everything ever shipped)
}

TOKEN_RE = re.compile(r"^[a-z0-9_]{1,6}$")


class BuildError(Exception):
    pass


def new_project(**kw):
    p = json.loads(json.dumps(DEFAULT_PROJECT))
    p.update(kw)
    return migrate(p)


# --- skins: several paint jobs in one mod ---------------------------------------
# p["skins"] = [{"id": "", "name": ..., "icon": rel}, {"id": "b", ...}]; the first skin has id ""
# so projects from before 0.5.1 keep every ID and unit token. Artwork per skin lives in
# p["skin_art"][id][slot key] = {"art", "overrides", "upright"}; marks, cabs and pivots stay
# shared in p["slots"]. Slot keys of other skins are namespaced "<id>|<key>" in "ids"/"built".
# Logos (texture replacements) are shared: always stored under skin "".
def migrate(p: dict) -> dict:
    if "skins" not in p:
        art = {}
        for k, cfg in p.get("slots", {}).items():
            a = {f: cfg.pop(f) for f in ART_FIELDS if f in cfg}
            if a:
                art[k] = a
        p["skins"] = [{"id": "", "name": p.get("paint_name") or "Skin 1", "icon": p.get("icon", "")}]
        p["skin_art"] = {"": art}
        p["active_skin"] = ""
    p.setdefault("skin_art", {}).setdefault("", {})
    if p.get("game") not in ("ats", "ets2"):
        p["game"] = "ets2" if "euro truck" in (p.get("output_dir") or "").lower() else "ats"
    if p.get("active_skin") not in [s["id"] for s in p["skins"] if not s.get("deleted")]:
        p["active_skin"] = next(s["id"] for s in p["skins"] if not s.get("deleted"))
    p["version"] = PROJECT_VERSION
    return p


def skin(p, sid=None):
    sid = p.get("active_skin", "") if sid is None else sid
    return next((s for s in p["skins"] if s["id"] == sid), p["skins"][0])


def art_ns(key, sid):
    return "" if is_tex(key) else sid


def art_cfg(p, key, sid=None, create=False) -> dict:
    """Per-skin artwork of a slot (art / overrides / upright)."""
    sid = art_ns(key, p.get("active_skin", "") if sid is None else sid)
    store = p["skin_art"].setdefault(sid, {}) if create else p["skin_art"].get(sid, {})
    if create:
        return store.setdefault(key, {})
    return store.get(key) or {}


def slot_view(p, key, sid=None) -> dict:
    """Shared slot settings + this skin's artwork."""
    return {**(p["slots"].get(key) or {}), **art_cfg(p, key, sid)}


def color_key(slot) -> str:
    """Where a slot's own base colour is stored: SCS parts share their trailer's paint job."""
    if slot.kind == "scs" and (getattr(slot, "cab", False) or getattr(slot, "body", False)):
        return slot.key          # a cab / a trailer body can be its own paint job: its own colour
    return f"@scs/{slot.trailer}" if slot.kind == "scs" else slot.key


def new_skin_id(p) -> str:
    used = {s["id"] for s in p["skins"]}
    import itertools
    import string
    for n in itertools.count(1):
        for combo in itertools.product(string.ascii_lowercase, repeat=n):
            c = "".join(combo)
            if c not in used and c not in ("m", "u"):
                return c


def load_project(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    p = json.loads(json.dumps(DEFAULT_PROJECT))
    p.update(data)
    p["metallic"] = {**DEFAULT_PROJECT["metallic"], **data.get("metallic", {})}
    return migrate(p)


def save_project(p: dict, path: str):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2)
    os.replace(tmp, path)


# --- helpers -----------------------------------------------------------------
def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def sii_color(h: str) -> str:
    r, g, b = (srgb_to_linear(v / 255) for v in hex_to_rgb(h))
    return f"({r:.4f}, {g:.4f}, {b:.4f})"


def sii_str(s: str) -> str:
    return '"' + s.replace("\\", "/").replace('"', "'") + '"'


def safe_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .")
    return s or "mod"


def assign_id(p: dict, key: str) -> int:
    if key not in p["ids"]:
        p["ids"][key] = p["next_id"]
        p["next_id"] += 1
    return p["ids"][key]


@dataclass
class BuildResult:
    path: str
    units: list
    warnings: list
    seconds: float
    size: int


def _default_icon(name: str, color: str, w=256, h=64) -> Image.Image:
    im = Image.new("RGBA", (w, h), (24, 27, 33, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([0, h - 8, w, h], fill=hex_to_rgb(color) + (255,))
    try:
        font = ImageFont.load_default(size=22 if h <= 64 else 26)
    except TypeError:
        font = ImageFont.load_default()
    text = name if len(name) <= 22 else name[:21] + "…"
    d.text((10, (h - 8) // 2), text, fill=(235, 235, 235, 255), font=font, anchor="lm")
    return im


def pot_size(size, long_side: int):
    """Output size for a mask: long side = long_side, aspect snapped to 1:1 / 2:1 / 4:1 / 8:1."""
    import math
    w, h = size
    r = 2 ** round(math.log2(w / h))
    r = max(1 / 8, min(8, r))
    if r >= 1:
        return long_side, max(4, int(long_side / r))
    return max(4, int(long_side * r)), long_side


def _fit_contain(im: Image.Image, size: int) -> Image.Image:
    """Square images are scaled; others are centred on a transparent square canvas."""
    w, h = im.size
    if w == h:
        return im.resize((size, size), Image.Resampling.LANCZOS) if w != size else im
    s = size / max(w, h)
    im = im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.size[0]) // 2, (size - im.size[1]) // 2))
    return canvas


def _fit(im: Image.Image, w: int, h: int) -> Image.Image:
    """cover-fit + centre crop"""
    sw, sh = im.size
    s = max(w / sw, h / sh)
    im = im.resize((max(1, round(sw * s)), max(1, round(sh * s))), Image.Resampling.LANCZOS)
    x, y = (im.size[0] - w) // 2, (im.size[1] - h) // 2
    return im.crop((x, y, x + w, y + h))


FIN_SUFFIX = {"g": "", "m": "m", "t": "t"}          # unit token suffix per finish
FIN_NAME = {"g": "", "m": " Metallic", "t": " Matte"}
MATTE_TOBJ = "/vehicle/truck/upgrade/paintjob/skinforge_matte.tobj"   # shared by every SkinForge mod (same content)


SKIN_OPTS = ("finish", "metallic", "color_locked", "finish_by_vehicle", "lock_by_vehicle", "traffic_by_vehicle",
             "finish_by_slot", "lock_by_slot", "traffic_by_slot", "ai_lengths")


def skin_opts(p, sid=None) -> dict:
    """A skin's paint job options: its own (skin["opts"]) where set, else the project's."""
    o = skin(p, sid).get("opts") or {}
    return {k: (o[k] if k in o else p.get(k)) for k in SKIN_OPTS}


def finish_codes(p) -> list:
    """Project finish ("gloss", "metallic", "matte", "both" or e.g. "gloss+matte") -> ["g", "m", "t"] subset."""
    f = str(p.get("finish") or "both").lower()
    names = {"gloss", "metallic"} if f == "both" else set(f.split("+"))
    out = [c for n, c in (("gloss", "g"), ("metallic", "m"), ("matte", "t")) if n in names]
    return out or ["g", "m"]


def matte_dds() -> bytes:
    """4x4 uncompressed A8R8G8B8, every pixel white with alpha 0 (byte-identical to MS2's matte.dds)."""
    hdr = struct.pack("<4s7I", b"DDS ", 124, 0x100F, 4, 4, 16, 0, 0) + b"\0" * 44
    hdr += struct.pack("<2I4s5I", 32, 0x41, b"\0\0\0\0", 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    hdr += struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return hdr + b"\xff\xff\xff\x00" * 16


# --- build -------------------------------------------------------------------
def _hex_rgba(h):
    h = (h or "#9a9a9a").lstrip("#")
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    except (ValueError, IndexError):
        return (154, 154, 154, 255)


def build(project: dict, project_dir: str, trailers: list, out_path: str, progress=None,
          compose=None) -> BuildResult:
    """compose = (signature(key, skin) -> str, apply(key, image, skin) -> image): upright-sheet pieces."""
    t0 = time.time()
    p = project
    code = p["code"].strip().lower()
    if not TOKEN_RE.match(code):
        raise BuildError("Unit code must be 1-6 characters: a-z, 0-9 or _")
    res = int(p.get("resolution", 4096))
    if not T.is_pot(res):
        raise BuildError("Resolution must be a power of two")
    slots_by_key = {s.key: s for t in trailers for s in t.slots}
    warnings = []
    files: dict[str, bytes] = {}
    tex_cache: dict[str, str] = {}   # art hash -> dds game path
    tex_images: dict = {}            # dds game path -> final PIL image
    ai_cab_jobs = []                 # (truck id, cab slot, skin id, skin name, cab dds, colour)
    ai_trailer_jobs = []             # (trailer id, part slots, {slot key: dds}, skin id, skin name, colour)
    units = []

    # finish / colour lock / AI traffic / metallic are per skin (skin "opts", else the project's)
    cur_sid = [""]
    _opts_cache = {}

    def O():
        sid = cur_sid[0]
        if sid not in _opts_cache:
            o = skin_opts(p, sid)
            _opts_cache[sid] = {
                "finishes": finish_codes(o),
                "fin_v": {k: v for k, v in (o.get("finish_by_vehicle") or {}).items() if v},
                "lock_v": {k: bool(v) for k, v in (o.get("lock_by_vehicle") or {}).items() if v is not None},
                "traffic_v": {k: v for k, v in (o.get("traffic_by_vehicle") or {}).items() if v in ("ai", "both")},
                "fin_s": {k: v for k, v in (o.get("finish_by_slot") or {}).items() if v},
                "lock_s": {k: bool(v) for k, v in (o.get("lock_by_slot") or {}).items() if v is not None},
                "traffic_s": {k: v for k, v in (o.get("traffic_by_slot") or {}).items() if v in ("own", "ai", "both")},
                "locked": bool(o.get("color_locked")),
                "metallic": {**DEFAULT_PROJECT["metallic"], **(o.get("metallic") or {})},
                "ai_lengths": {k: set(v) for k, v in (o.get("ai_lengths") or {}).items() if isinstance(v, list)},
            }
        return _opts_cache[sid]

    def fins_of(tid, skey=None):
        """Finishes of one template (its own paint job): its own choice, else its truck / trailer's,
        else the skin's."""
        o = O()
        v = o["fin_s"].get(skey) if skey else None
        v = v or o["fin_v"].get(tid)
        return finish_codes({"finish": v}) if v else o["finishes"]

    def locked_of(tid, skey=None):
        o = O()
        if skey and skey in o["lock_s"]:
            return o["lock_s"][skey]
        return o["lock_v"].get(tid, o["locked"])

    def traffic_of(tid, skey=None):
        o = O()
        return (o["traffic_s"].get(skey) if skey else None) or o["traffic_v"].get(tid, "own")
    matte_used = []
    by_vehicle = {k: int(v) for k, v in (p.get("res_by_vehicle") or {}).items() if v and T.is_pot(int(v))}

    def vres(tid):
        """Texture size of one truck / trailer / car: its own choice, else the project's."""
        return by_vehicle.get(tid, res)

    sheet_sizes = {k: tuple(v) for k, v in (p.get("sheet_size") or {}).items() if v}

    def tsize(sl):
        """The part's sheet: its template-folder sheet's shape when the user paints on one."""
        return sheet_sizes.get(sl.key) or sl.tex_size

    def vsize(tid, size):
        """An SCS part's texture size (scanned at the project size) at this vehicle's size."""
        w, h = size or (res, res)
        f = vres(tid) / res
        return (max(4, int(w * f)), max(4, int(h * f)))

    def say(msg):
        if progress:
            progress(msg)

    def art_path(rel):
        return os.path.join(project_dir, rel) if rel and not os.path.isabs(rel) else rel

    def make_texture(rel_art, game_dir, stem, key=None, size=None, sid="", lres=None) -> str:
        lres = lres or res
        fp = art_path(rel_art)
        with open(fp, "rb") as f:
            raw = f.read()
        sig = compose[0](key, sid) if (compose and key) else ""
        h = hashlib.sha1(raw + str(lres).encode() + sig.encode() + str(size).encode()).hexdigest()
        if h in tex_cache:
            return tex_cache[h]
        im = T.load_image(raw)
        out_size = tuple(size) if size else pot_size(im.size, lres)
        w, hh = im.size
        if abs((w / hh) - (out_size[0] / out_size[1])) > 0.02 * (out_size[0] / out_size[1]):
            warnings.append(f"{os.path.basename(fp)} is {w}x{hh} - stretched to {out_size[0]}x{out_size[1]} "
                            f"(paint job masks need a 1:1, 2:1 or 4:1 shape)")
        elif not (T.is_pot(w) and T.is_pot(hh)):
            warnings.append(f"{os.path.basename(fp)}: {w}x{hh} is not power-of-two - resized to "
                            f"{out_size[0]}x{out_size[1]}")
        im = T.fit_art(im, out_size)
        if sig:
            say(f"Placing upright pieces for {os.path.basename(fp)}…")
            im = compose[1](key, im, sid)
        say(f"Compressing {os.path.basename(fp)} ({out_size[0]}x{out_size[1]} DXT5)…")
        dds_path = f"{game_dir}/{stem}.dds"
        tex_images[dds_path] = im          # kept for the AI-traffic transfer
        files[dds_path] = T.encode_dds_dxt5(im)
        files[dds_path[:-4] + ".tobj"] = T.make_tobj(dds_path)
        tex_cache[h] = dds_path
        return dds_path

    blank_cache = {}

    def blank_texture(game_dir) -> str:
        if game_dir in blank_cache:
            return blank_cache[game_dir]
        dds_path = f"{game_dir}/blank.dds"
        files[dds_path] = T.encode_dds_dxt5(Image.new("RGBA", (4, 4), (0, 0, 0, 0)))
        files[dds_path[:-4] + ".tobj"] = T.make_tobj(dds_path)
        blank_cache[game_dir] = dds_path
        return dds_path

    uv_both = p.get("truck_uvset", "normal") == "both"
    built = p.setdefault("built", {})

    def pj_path(slot, token, acc=False):
        return f"/def/vehicle/{slot.root}/{slot.trailer}/paint_job/{'accessory/' if acc else ''}{token}.sii"

    def suitable(slot):
        """Template's suitable_for, or the chosen/fixed cab list (trucks) + its non-cab entries."""
        cfg = p["slots"].get(slot.key) or {}
        cabs = cfg.get("cabins") if isinstance(cfg.get("cabins"), list) else None
        if cabs is None and slot.kind == "truck":
            cabs = slot.fix_cabins
        if not cabs:
            return slot.suitable_for
        return list(cabs) + [x for x in slot.suitable_for if not x.endswith(".cabin")]

    def unit_text(slot, token, name, fin, alt_uv, mask_tobj, scs=False, icon=None, color=None, skey=None, suit=None):
        metallic = O()["metallic"]
        body = [
            f"accessory_paint_job_data : {token}.{slot.trailer}.paint_job",
            "{",
            f"\tname: {sii_str(name)}",
            f"\tprice: {int(p.get('price', 1000))}",
            "\tunlock: 0",
            f'\ticon: "{icon or code}"',
            "\tairbrush: true",
            "\tstock: false",
            f"\talternate_uvset: {'true' if alt_uv else 'false'}",
            f"\tbase_color: {sii_color(color or p['base_color'])}",
            f"\tbase_color_locked: {'true' if locked_of(slot.trailer, skey) else 'false'}",
        ]
        if fin == "t":
            matte_used.append(token)
            # matte: a white base texture with alpha 0 kills the gloss. No flake/flip layer: with
            # flipflake on, the engine's default flip colour bled in (white -> salmon red, light
            # colours tinted - dev9 test). MS2's template ends up the same way: its metallic.sui
            # @include comes after the matte lines and sets flipflake to the metallic switch (off)
            body += [
                f'\tbase_texture_override: "{MATTE_TOBJ}"',
                "\tflipflake: false",
            ]
        if fin == "m":
            body += [
                "\tflipflake: true",
                f"\tflake_color: {sii_color(metallic['flake_color'])}",
                f"\tflake_color_locked: {'true' if locked_of(slot.trailer, skey) else 'false'}",
                f"\tflip_color: {sii_color(metallic['flip_color'])}",
                f"\tflip_color_locked: {'true' if locked_of(slot.trailer, skey) else 'false'}",   # a locked paint job locks every colour
                f"\tflip_strength: {float(metallic['flip_strength']):.3f}",
                f"\tflake_density: {float(metallic['flake_density']):.3f}",
                f"\tflake_shininess: {float(metallic['flake_shininess']):.3f}",
                f"\tflake_clearcoat_rolloff: {float(metallic['flake_clearcoat_rolloff']):.3f}",
                '\tflake_noise: "/material/custom/flake_noise.tobj"',
            ]
            if getattr(slot, "vroot", "") == "car":
                # SCS's own Road Trip metallics: flakes on the car's other UV set, scale 20
                body += ["\talternate_flipflake_uvset: true", "\tflake_uvscale: 20.0"]
        if scs:
            body.append('\tpart_type: "aftermarket"')
        body.append(f'\tpaint_job_mask: "{mask_tobj}"')
        if not scs:
            body += [f'\tsuitable_for[]: "{x}"' for x in suitable(slot)]
        elif getattr(slot, "cab", False):
            body += [f'\tsuitable_for[]: "{x}"' for x in slot.cabin_units]
        elif suit:   # SCS / mod trailer body with its own paint job (Jon Ruda style: "<body>.<trailer>.body")
            body += [f'\tsuitable_for[]: "{x}"' for x in suit]
        body.append("}")
        return ("SiiNunit\n{\n" + "\n".join(body) + "\n}\n").encode("utf-8")

    def body_units(sl, tid):
        """suitable_for names of a body slot: acc "body.dryvan_53" -> "dryvan_53.scs.box.body"."""
        return [f"{a.split('.', 1)[1]}.{tid}.body" for a in sl.acc_list if a.startswith("body.")]

    def scs_trailer_jobs(tid, parts, all_parts, gkey, color, block_of, part_dds, part_art, main_trailer,
                         root_dir, sid, sk, sname, icon, pre):
        """SCS / mod trailer: one paint job for the whole trailer, plus one paint job per body that has
        its own finish / colour lock / AI traffic / colour (suitable_for = that body, like Jon Ruda's
        per-length paint jobs). The shared one then only offers the other bodies."""
        o = O()
        bodies = [b for b in parts if getattr(b, "body", False) and body_units(b, tid)]
        own = [b for b in bodies if b.key in o["fin_s"] or b.key in o["lock_s"] or cfg_of(b.key).get("color")]
        own_keys = {b.key for b in own}
        rest_bodies = [b for b in bodies if b.key not in own_keys]
        others = [sl for sl in parts if not getattr(sl, "body", False) or sl.key not in own_keys]
        part_blocks = [block_of[sl.key] for sl in others if sl.key in block_of]
        jobs = [(None, gkey, part_blocks, [sl for sl in others], [u for b in rest_bodies for u in body_units(b, tid)]
                 if own else None)]
        non_body = [block_of[sl.key] for sl in parts if not getattr(sl, "body", False) and sl.key in block_of]
        for b in own:
            blk = ([block_of[b.key]] if b.key in block_of else []) + non_body
            jobs.append((b, f"{gkey}/{b.token}", blk, [b] + [sl for sl in parts if not getattr(sl, "body", False)],
                         body_units(b, tid)))
        main = main_trailer or blank_texture(root_dir)
        # AI traffic: the AI traffic sheet (company livery layout) goes on SCS's traffic trailers of this
        # type whenever it has artwork - no tick needed; the sheet's length boxes pick the lengths
        sheet_rel = cfg_of(f"{tid}/ai_sheet").get("art")
        if sheet_rel:
            # the sheet's own colour (set while the sheet is selected), else the trailer's, else the
            # first body's: AI trailers have no shop colour of their own
            scolor = cfg_of(f"{tid}/ai_sheet").get("color") or color or \
                next((cfg_of(b.key).get("color") for b in bodies if cfg_of(b.key).get("color")), None)
            ai_trailer_jobs.append((tid, o["ai_lengths"].get(tid), sheet_rel, sid, sname, scolor))
        for body, ukey, blk, job_parts, suit in jobs:
            anchor = body if body is not None else all_parts[0]
            has_art = any(sl.key in part_dds for sl in job_parts)
            shipped = any(v == pre + ukey for v in built.values())
            skey = body.key if body is not None else None
            jcolor = (cfg_of(body.key).get("color") if body is not None else None) or color
            if not has_art and not shipped:
                continue           # nothing painted for this paint job (and it never shipped)
            n = assign_id(p, pre + ukey)
            text = ("SiiNunit\n{\n" + "\n\n".join(
                f"simple_paint_job_data : .ovr{i}\n" + b for i, b in enumerate(blk)) + "\n}\n").encode("utf-8") if blk else None
            if not has_art and main.endswith("/blank.dds"):
                warnings.append(f"{sname}: {tid}{' ' + body.label if body else ''}: no artwork, but it shipped "
                                f"before - kept as plain colour so existing saves don't crash")
            per_key[pre + ukey] = (anchor, main[:-4] + ".tobj", text, sk, jcolor)
            vfins = fins_of(tid, skey)
            for fin in vfins:
                token = f"{code}{n:02d}{FIN_SUFFIX[fin]}"
                name = sname + (f" {body.label}" if body is not None else "") + \
                    (FIN_NAME[fin] if len(vfins) > 1 and fin != "g" else "")
                files[pj_path(anchor, token)] = unit_text(anchor, token, name, fin, False, main[:-4] + ".tobj",
                                                          scs=True, icon=icon, color=jcolor, skey=skey, suit=suit)
                if text:
                    files[pj_path(anchor, token, acc=True)] = text
                units.append({"token": token, "trailer": tid,
                              "slot": (body.label + " + " if body is not None else "") + f"{len(blk)} parts", "name": name})
                built[token] = pre + ukey

    def split(v):
        """built value -> (skin id, slot key)"""
        return tuple(v.split("|", 1)) if "|" in v else ("", v)

    live = [sk for sk in p["skins"] if not sk.get("deleted")]
    if not any(cfg.get("art") or any((cfg.get("overrides") or {}).values())
               for sk in live for cfg in p["skin_art"].get(sk["id"], {}).values()):
        raise BuildError("No artwork assigned - drop an image on at least one slot")

    # logos: texture replacements, shared by every skin
    for key, cfg in sorted(p["skin_art"].get("", {}).items()):
        if not is_tex(key) or not cfg.get("art"):
            continue
        role = key.split("/", 1)[1]
        with open(art_path(cfg["art"]), "rb") as f:
            im = T.load_image(f.read())
        from .logos import size_for
        from . import signs, player_logos
        if player_logos.is_role(role):
            # replaces the game's own company logo: mat + tobj + uncompressed UI dds (MS2's recipe)
            sz = size_for(role)
            if not sz:   # game files not readable: keep the art's shape, power of two, max 512
                p2 = lambda v: 1 << max(5, min(9, int(v).bit_length() - 1))
                sz = (p2(im.size[0]), p2(im.size[1]))
            lim = _fit_contain(im, sz[0]) if sz[0] == sz[1] else T.fit_art(im, sz)
            say(f"Company logo {player_logos.index(role) + 1}…")
            files.update(player_logos.files_for(role, T.encode_dds_bgra(lim), T.make_tobj))
            units.append({"token": role, "trailer": "player_company", "slot": "company logo",
                          "name": player_logos.dds_path(player_logos.index(role))})
            continue
        lw, lh = size_for(role)
        sign = signs.is_role(role)
        # logos sit centred on a transparent square; a sign fills its whole texture
        im = _fit_contain(im, lw) if (lw == lh and not sign) else T.fit_art(im, (lw, lh))
        say(f"Compressing {'sign' if sign else 'logo'} {role}…")
        # signs keep the pack's own colour space (SneaK / MattVT ship sRGB DX10 textures)
        dds = T.encode_dds_dxt5(im, srgb=bool(sign and signs.DETECTED[role].get("srgb")))
        for tid, _name, path in logo_texture_paths(role):
            files[path] = dds
            units.append({"token": role, "trailer": tid, "slot": "sign texture" if sign else "logo texture", "name": path})

    per_key = {}   # namespaced key -> (slot, mask_tobj, ovr_text, skin)
    ai_jobs = {}   # AI slot key -> [(skin id, skin name, art, colour)]
    shipped_skins = {split(v)[0] for v in built.values()}
    for sk in p["skins"]:
        sid = sk["id"]
        cur_sid[0] = sid
        if sk.get("deleted") and sid not in shipped_skins:
            continue
        pre = f"{sid}|" if sid else ""
        arts = {} if sk.get("deleted") else {k: c for k, c in p["skin_art"].get(sid, {}).items()
                                              if not is_tex(k)}
        icon = code if not sid else f"{code}_{sid}"
        sname = sk.get("name") or "Skin"
        if len([x for x in p["skins"] if not x.get("deleted")]) > 1 or sid:
            say(f"Skin: {sname}")

        def cfg_of(key):
            return arts.get(key) or {}

        # which slots to emit: everything with art + everything ever shipped (save safety)
        # slots with art, or with only separate-part art (e.g. only the Torpedo box painted)
        emit = [k for k, c in arts.items() if c.get("art") or any((c.get("overrides") or {}).values())]
        for v in sorted(built.values()):
            ns, k = split(v)
            if ns == sid and k not in emit:
                emit.append(k)
        # SCS base-game trailers: one paint job per trailer, one texture per part (key "@scs/<trailer>")
        scs_trailers = []
        for k in emit:
            tid = k[5:].split("/")[0] if k.startswith("@scs/") else (slots_by_key[k].trailer if k in slots_by_key and slots_by_key[k].kind in ("scs", "ai_sheet") else None)
            if tid and tid not in scs_trailers:
                scs_trailers.append(tid)
        emit = [k for k in emit if not k.startswith("@scs/") and not (k in slots_by_key and slots_by_key[k].kind in ("scs", "ai_sheet"))]
        # AI traffic: one extra look per skin on a copy of the AI model (built after all skins)
        for k in emit:
            sl = slots_by_key.get(k)
            if sl is not None and sl.kind in ("ai", "ai_trailer", "ai_escort") and cfg_of(k).get("art"):
                ai_jobs.setdefault(k, []).append((sid, sname, cfg_of(k)["art"], cfg_of(k).get("color")))
        emit = [k for k in emit if not (k in slots_by_key and slots_by_key[k].kind in ("ai", "ai_trailer", "ai_escort"))]

        # parts textures (overrides) are usually shared by every template of one vehicle
        # (Pete 389: headache rack / rear bumper / rear fenders), so art given once is reused
        shared_ovr = {}
        for key, cfg in arts.items():
            sl = slots_by_key.get(key)
            if not sl:
                continue
            for o in sl.overrides:
                rel = (cfg.get("overrides") or {}).get(str(o.index))
                if rel and o.template_mask:
                    shared_ovr.setdefault((sl.trailer, o.template_mask.lower()), rel)

        for key in emit:
            slot = slots_by_key.get(key)
            cfg = cfg_of(key)
            if slot is None:
                warnings.append(f"{key}: not found in the loaded packs - kept out of this build")
                continue
            n = assign_id(p, pre + key)
            game_dir = f"/vehicle/{slot.root}/upgrade/paintjob/{slot.trailer}/{code}"
            if cfg.get("art"):
                mask = make_texture(cfg["art"], game_dir, f"{n:02d}", key, sid=sid, lres=vres(slot.trailer))
            else:
                if not any((cfg.get("overrides") or {}).values()):
                    warnings.append(f"{sname}: {slot.trailer} {slot.label}: no artwork, but it shipped before - "
                                    f"kept as plain colour so existing saves don't crash")
                mask = blank_texture(game_dir)   # only separate parts painted: the cab takes the paint colour
            mask_tobj = mask[:-4] + ".tobj"

            ovr_blocks = []
            for o in slot.overrides:
                rel = (cfg.get("overrides") or {}).get(str(o.index))
                if not rel and o.template_mask:
                    rel = shared_ovr.get((slot.trailer, o.template_mask.lower()))
                stem = re.sub(r"[^a-z0-9_]", "_", (o.template_mask or f"o{o.index}").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower())
                omask = make_texture(rel, game_dir, f"{sid + '_' if sid else ''}part_{stem}", lres=vres(slot.trailer)) \
                    if rel else blank_texture(game_dir)
                lines = ["simple_paint_job_data : .ovr%d" % len(ovr_blocks), "{",
                         f'\tpaint_job_mask: "{omask[:-4]}.tobj"']
                lines += [f'\tacc_list[]: "{a}"' for a in o.acc_list]
                for k, v in o.extra.items():
                    if isinstance(v, list):
                        lines += [f"\t{k}[]: {x}" for x in v]
                    else:
                        lines.append(f"\t{k}: {v}")
                lines.append("}")
                ovr_blocks.append("\n".join(lines))
            ovr_text = ("SiiNunit\n{\n" + "\n\n".join(ovr_blocks) + "\n}\n").encode("utf-8") if ovr_blocks else None
            per_key[pre + key] = (slot, mask_tobj, ovr_text, sk, cfg.get("color"))

            uv_variants = [False] + ([True] if (uv_both and slot.kind == "truck") else [])
            for alt_uv in uv_variants:
                vfins = fins_of(slot.trailer, slot.key)
                for fin in vfins:
                    token = f"{code}{n:02d}{FIN_SUFFIX[fin]}{'u' if alt_uv else ''}"
                    if len(token) > 12:
                        raise BuildError(f"Unit token {token} longer than 12 characters")
                    name = sname
                    if len(vfins) > 1 and fin != "g":
                        name += FIN_NAME[fin]
                    if alt_uv:
                        name += " (Alt UV)"
                    files[pj_path(slot, token)] = unit_text(slot, token, name, fin, alt_uv, mask_tobj, icon=icon,
                                                            color=cfg_of(key).get("color"), skey=slot.key)
                    if ovr_text:
                        files[pj_path(slot, token, acc=True)] = ovr_text
                    units.append({"token": token, "trailer": slot.trailer, "slot": slot.label, "name": name})
                    built[token] = pre + key

        # SCS base-game trailers (one paint job) and trucks (one paint job per cab sheet, like SCS's
        # own _a/_b/_c liveries; every cab unit shares the same parts file)
        for tid in scs_trailers:
            all_parts = [sl for sl in slots_by_key.values() if sl.kind == "scs" and sl.trailer == tid]
            if not all_parts:
                warnings.append(f"{tid}: not found in the loaded game files - kept out of this build")
                continue
            cabs = [sl for sl in all_parts if sl.cab]
            parts = [sl for sl in all_parts if not sl.cab]
            gkey = f"@scs/{tid}"
            color = cfg_of(gkey).get("color")
            if cabs:
                shipped_cabs = {split(v)[1] for v in built.values() if split(v)[0] == sid}
                units_for = [c for c in cabs if cfg_of(c.key).get("art") or f"{gkey}/{c.token}" in shipped_cabs]
                if not units_for:
                    units_for = cabs          # parts only: the paint job goes on every cab
                targets = [(c, f"{gkey}/{c.token}") for c in units_for]
            else:
                targets = [(None, gkey)]
            nums = {ukey: assign_id(p, pre + ukey) for _c, ukey in targets}
            first = min(nums.values())
            root_dir = f"/vehicle/{all_parts[0].root}/upgrade/paintjob/{tid.replace('.', '_')}/{code}{first:02d}"
            blocks = []
            block_of = {}
            main_trailer = None
            part_art = False
            part_dds = {}
            for sl in parts:
                rel = cfg_of(sl.key).get("art")
                size = vsize(tid, tsize(sl))
                if rel:
                    dds = make_texture(rel, root_dir, sl.token, sl.key, size, sid=sid)
                    part_dds[sl.key] = dds
                    part_art = part_art or not sl.main
                elif sl.main:
                    continue
                else:
                    # a part left empty still gets its own (blank) texture: without an override it
                    # samples the cab sheet at its own UVs and shows bits of the cab art (Mack
                    # Anthem roof deflector). Blank = the paint job's base colour, like SCS's plain parts
                    dds = blank_texture(root_dir)
                if sl.main:
                    main_trailer = dds
                    continue
                nw, nh = sl.native or size
                # SCS's own numbers: trailer parts 10 x texture width (box rew_xm22), truck parts
                # width / 64 (Scania S log_0: mirror 256 -> 4, skirt 512 -> 8, fender 1024 -> 16)
                uvs = nw / 64.0 if sl.vroot in ("truck", "car") else 10.0 * nw
                lines = ["simple_paint_job_data : .ovr%d" % len(blocks), "{",
                         f'\tpaint_job_mask: "{dds[:-4]}.tobj"',
                         f"\tflake_uvscale: {uvs:.6f}"]
                if nh != nw:
                    lines.append(f"\tflake_vratio: {nh / nw:.6f}")
                lines += [f'\tacc_list[]: "{a}"' for a in sl.acc_list]
                lines.append("}")
                blocks.append("\n".join(lines))
                block_of[sl.key] = "\n".join(lines[1:])          # without the ".ovrN" name line
            ovr_text = ("SiiNunit\n{\n" + "\n\n".join(blocks) + "\n}\n").encode("utf-8") if blocks else None
            if not cabs:
                scs_trailer_jobs(tid, parts, all_parts, gkey, color, block_of, part_dds, part_art, main_trailer,
                                 root_dir, sid, sk, sname, icon, pre)
                continue
            for cab, ukey in targets:
                n = nums[ukey]
                anchor = cab or all_parts[0]
                if cab is not None:
                    rel = cfg_of(cab.key).get("art")
                    main = make_texture(rel, root_dir, cab.token, cab.key, vsize(tid, tsize(cab)), sid=sid) \
                        if rel else blank_texture(root_dir)
                else:
                    main = main_trailer or blank_texture(root_dir)
                if not part_art and main.endswith("/blank.dds"):
                    warnings.append(f"{sname}: {tid}{' ' + cab.label if cab else ''}: no artwork, but it shipped "
                                    f"before - kept as plain colour so existing saves don't crash")
                ckey = cab.key if cab is not None else None      # a cab is its own paint job
                ccolor = (cfg_of(cab.key).get("color") if cab is not None else None) or color
                per_key[pre + ukey] = (anchor, main[:-4] + ".tobj", ovr_text, sk, ccolor)
                tmode = traffic_of(tid, ckey)
                if cab is not None and tmode in ("ai", "both") and cfg_of(cab.key).get("art"):
                    ai_cab_jobs.append((tid, cab, sid, sname, main, ccolor))
                if cab is None and tmode in ("ai", "both") and part_dds:
                    ai_trailer_jobs.append((tid, parts, dict(part_dds), sid, sname, color))
                if tmode == "ai" and not any(v == pre + ukey for v in built.values()):
                    continue          # AI traffic only: no paint job in the truck shop
                vfins = fins_of(tid, ckey)
                for fin in vfins:
                    token = f"{code}{n:02d}{FIN_SUFFIX[fin]}"
                    name = sname + (FIN_NAME[fin] if len(vfins) > 1 and fin != "g" else "")
                    files[pj_path(anchor, token)] = unit_text(anchor, token, name, fin,
                                                              bool(cab is not None and getattr(cab, "alt_uv", False)),
                                                              main[:-4] + ".tobj",
                                                              scs=True, icon=icon, color=ccolor, skey=ckey)
                    if ovr_text:
                        files[pj_path(anchor, token, acc=True)] = ovr_text
                    units.append({"token": token, "trailer": tid,
                                  "slot": (cab.label + " + " if cab else "") + f"{len(blocks)} parts", "name": name})
                    built[token] = pre + ukey

    # variants (finish / alt UV) that shipped before but are switched off now: keep them alive
    emitted = {u["token"] for u in units}
    kept_alive = []
    for token, key in list(built.items()):
        if token in emitted or key not in per_key:
            continue
        slot, mask_tobj, ovr_text, sk, kcolor = per_key[key]
        cur_sid[0] = sk["id"]
        suffix = re.sub(r"^[a-z0-9_]*?\d+", "", token[len(code):])
        fin = "m" if "m" in suffix else "t" if "t" in suffix else "g"
        alt_uv = token.endswith("u")
        sid = sk["id"]
        files[pj_path(slot, token)] = unit_text(slot, token, (sk.get("name") or "Skin") + (FIN_NAME[fin] if fin != "g" else "")
                                                + (" (Alt UV)" if alt_uv else ""), fin, alt_uv, mask_tobj,
                                                scs=split(key)[1].startswith("@scs/"), icon=code if not sid else f"{code}_{sid}",
                                                color=kcolor,
                                                skey=slot.key if (slot.kind != "scs" or getattr(slot, "cab", False)) else None)
        if ovr_text:
            files[pj_path(slot, token, acc=True)] = ovr_text
        kept_alive.append((fin, alt_uv, token))

    if kept_alive:   # one note per finish, not one per paint job
        for what in ("Gloss", "Metallic", "Matte", "Alt UV"):
            toks = [t for f, alt, t in kept_alive if (what == "Alt UV" and alt) or
                    (not alt and {"g": "Gloss", "m": "Metallic", "t": "Matte"}[f] == what)]
            if toks:
                warnings.append(f"{what} is switched off, but {len(toks)} {what.lower()} paint job"
                                f"{'s' if len(toks) > 1 else ''} shipped in an earlier build - kept in the mod "
                                f"so saved games that use them don't break ({', '.join(toks[:4])}"
                                f"{', …' if len(toks) > 4 else ''})")
    storages, ai_count = {}, [0]
    try:   # how often our AI trucks / trailers spawn next to each of SCS's own (spawn_ratio)
        spawn = max(0.01, min(50.0, float(p.get("ai_spawn") or 1)))
    except (TypeError, ValueError):
        spawn = 1.0
    # AI trucks of a skin pull that skin's AI trailers (a matched fleet, like Jazzycat's packs)
    fleet_sids = {j[2] for j in ai_cab_jobs} if ai_trailer_jobs else set()
    fleet_chains, fleet_texts, fleet_used = {}, {}, set()
    if ai_trailer_jobs:   # SCS trailers ticked "AI traffic": the AI traffic sheet on SCS's traffic trailers
        from . import ai as AI
        for tid in dict.fromkeys(j[0] for j in ai_trailer_jobs):
            sheet = slots_by_key.get(f"{tid}/{AI.SHEET_TOKEN}")
            if sheet is None:
                warnings.append(f"{tid}: SCS has no traffic version of this trailer in your game files - AI traffic left out")
                continue
            per_info = {}       # traffic trailer id -> (info, [(skin id, name, dds)])
            done = set()
            for _t, lengths, rel, sid, sname, color in ai_trailer_jobs:
                if _t != tid:
                    continue
                infos = [i for i in sheet.traffic
                         if lengths is None or AI.length_of(i["chassis_path"].rsplit("/", 1)[-1]) in lengths]
                if not infos:
                    warnings.append(f"{sname}: {tid}: no traffic length ticked on the AI traffic sheet - AI traffic left out")
                    continue
                dds = f"/vehicle/trailer/sf_{code}/{tid.replace('.', '_')}_{sid or 'a'}_ai.dds"
                if dds not in files:
                    say(f"AI traffic sheet: {tid} · {sname}…")
                    with open(art_path(rel), "rb") as f:
                        im = T.load_image(f.read())
                    w = min(4096, vres(tid))
                    im = T.fit_art(im, (w, w // 2))
                    if im.mode == "RGBA":   # transparent = the paint colour, like a template
                        bg = Image.new("RGBA", im.size, _hex_rgba(color or p.get("base_color") or "#9a9a9a"))
                        bg.alpha_composite(im)
                        im = bg
                    files[dds] = T.encode_dds_dxt5(im)
                    files[dds[:-4] + ".tobj"] = T.make_tobj(dds)
                for info in infos:
                    if (info["id"], sid) in done:
                        continue
                    done.add((info["id"], sid))
                    per_info.setdefault(info["id"], (info, []))[1].append((sid, sname, dds))
            model_cache = {}
            liveries = {}
            for _id, (info, skins_out) in per_info.items():
                # SCS's traffic trailer unit carries EVERY company livery of its type (company_paint_job/),
                # our copy carries one: share its spawn_ratio, so 1x = as often as one SCS company
                folder = info["chassis_path"].rsplit("/", 2)[-2]
                if folder not in liveries:
                    try:
                        names = sheet.game.listdir(f"/def/vehicle/trailer/{folder}/company_paint_job") or []
                    except Exception:
                        names = []
                    liveries[folder] = max(1, sum(1 for x in names if x.endswith(".sii") and x != "default.sii"))
                aslot = AI._ai_slot(sheet.game, info)
                aslot.kind = "ai_trailer"
                say(f"AI traffic: {info['name']} - re-mapping the model so the whole sheet shows…")
                try:
                    ch = AI.remapped_chassis(sheet.game, info, code, files, model_cache)
                except Exception as e:
                    ch = None
                    warnings.append(f"{info['name']}: model not re-mapped ({e}) - SCS's layout used, the ends may smear")
                for one in skins_out:
                    fleet = one[0] in fleet_sids
                    kind, text = AI.build_trailer_files(aslot, [one], code, files, ai_count,
                                                         spawn=spawn / liveries[folder], chassis=ch)
                    if fleet:
                        # Jazzycat-style fleet: the trailer only rides behind this skin's own AI
                        # trucks (trailer_chains), never in SCS's random semi-trailer pool
                        fleet_chains.setdefault(one[0], []).extend(re.findall(r"traffic_trailer : (\S+)", text))
                        fleet_texts.setdefault(one[0], []).append((kind, text))
                    else:
                        storages.setdefault(kind, []).append(text)
                for sid, sname, _d in skins_out:
                    units.append({"token": f"sf{code}", "trailer": info["id"], "slot": "AI traffic", "name": sname})
    if ai_cab_jobs:   # SCS trucks ticked "AI traffic": cab art carried over onto SCS's AI truck
        from . import ai as AI
        for tid in dict.fromkeys(j[0] for j in ai_cab_jobs):
            jobs = [j for j in ai_cab_jobs if j[0] == tid]
            say(f"AI traffic: matching {tid} cabs to its AI models…")
            try:
                every_cab = [sl for sl in slots_by_key.values() if sl.kind == "scs" and sl.trailer == tid and sl.cab]
                painted = list({id(j[1]): j[1] for j in jobs}.values())    # Slot isn't hashable: dedupe by identity
                pairs = AI.pair_cabs(jobs[0][1].game, tid, painted, every_cab)
            except Exception as e:
                warnings.append(f"{tid}: AI traffic left out - {e}")
                continue
            if not pairs:
                warnings.append(f"{tid}: no SCS AI version of this truck in your game files - AI traffic left out")
                continue
            for aslot, cab in pairs:
                skins_out = []
                for _t, jcab, sid, sname, main, color in jobs:
                    if jcab is not cab or main not in tex_images:
                        continue
                    say(f"AI traffic: {aslot.ai['name']} · {sname} (transfer from {cab.label})…")
                    fill = _hex_rgba(color or p.get("base_color") or "#9a9a9a")
                    im = AI.bake_from_cab(aslot, cab, tex_images[main], 2048, fill)
                    vid = re.sub(r"[^a-z0-9_]", "_", aslot.trailer.split(".", 1)[1])[:40]
                    dds = f"/vehicle/ai/sf_{code}/{vid}/{sid or 'a'}.dds"
                    files[dds] = T.encode_dds_dxt5(im)
                    files[dds[:-4] + ".tobj"] = T.make_tobj(dds)
                    skins_out.append((sid, sname, dds))
                if not skins_out:
                    continue
                try:
                    kind, text = AI.build_files(aslot, skins_out, code, files, say, counter=ai_count, spawn=spawn,
                                                 chains=fleet_chains)
                except Exception as e:
                    warnings.append(f"{aslot.ai['name']}: AI traffic left out - {e}")
                    continue
                fleet_used.update(x[0] for x in skins_out)
                storages.setdefault(kind, []).append(text)
                for sid, sname, _d in skins_out:
                    units.append({"token": f"sf{code}{sid or 'a'}", "trailer": aslot.trailer, "slot": "AI traffic",
                                  "name": sname})
    for fsid, texts in fleet_texts.items():
        # a skin whose AI trucks didn't get built: its trailers go back into SCS's random pool
        for kind, text in texts:
            if fsid in fleet_used:
                # rides only behind this skin's own AI trucks: no tags / spawn_ratio, like Jazzycat's
                # painted trailers (the truck's trailer_chains pick it by name)
                text = re.sub(r"^\t(tags\[\]|spawn_ratio): .*\n", "", text, flags=re.M)
                storages.setdefault("trailer_truck", []).append(text)
            else:
                storages.setdefault(kind, []).append(text)
        if fsid not in fleet_used:
            warnings.append("AI traffic: no AI truck of this skin was built - its AI trailers ride "
                            "behind SCS's own AI trucks instead")
    if ai_jobs:
        from . import ai as AI
        for key, jobs in sorted(ai_jobs.items()):
            slot = slots_by_key[key]
            vid = re.sub(r"[^a-z0-9_]", "_", slot.trailer.split(".", 1)[1])[:40]
            skins_out = []
            for sid, sname, rel, color in jobs:
                with open(art_path(rel), "rb") as f:
                    im = T.load_image(f.read())
                size = (min(2048, vres(slot.trailer)),) * 2 if slot.kind == "ai" else \
                    (min(4096, vres(slot.trailer)), min(4096, vres(slot.trailer)) // 2) if slot.kind == "ai_escort" else \
                    vsize(slot.trailer, tsize(slot))
                fill = _hex_rgba(color or p.get("base_color") or "#9a9a9a")
                if slot.kind == "ai_escort":
                    # the escort's own livery layout (left / right / roof / front / rear): 1:1
                    bg = Image.new("RGBA", size, fill)
                    bg.alpha_composite(T.fit_art(im, size))
                else:
                    im = T.fit_art(im, size)
                    # AI bodies have no paint colour of their own: transparent = the skin's base colour
                    bg = Image.new("RGBA", size, fill)
                    bg.alpha_composite(im)
                say(f"Compressing AI {slot.trailer} {sname} ({size[0]}x{size[1]} DXT5)…")
                dds = (f"/vehicle/ai/sf_{code}/{vid}/{sid or 'a'}.dds" if slot.kind in ("ai", "ai_escort")
                       else f"/vehicle/trailer/sf_{code}/{vid}_{sid or 'a'}.dds")
                files[dds] = T.encode_dds_dxt5(bg)
                files[dds[:-4] + ".tobj"] = T.make_tobj(dds)
                skins_out.append((sid, sname, dds))
            try:
                if slot.kind == "ai_escort":   # one unit per escort role SCS has for it (front / back)
                    for ekind, eunit, eaccs in slot.ai["escorts"]:
                        import copy
                        es = copy.copy(slot)
                        es.ai = dict(slot.ai, unit=eunit, accs=eaccs, storage=ekind)
                        k2, t2 = AI.build_files(es, skins_out, code, files, say, counter=ai_count, spawn=spawn)
                        storages.setdefault(k2, []).append(t2)
                    kind, text = None, None
                elif slot.kind == "ai_trailer":
                    kind, text = AI.build_trailer_files(slot, skins_out, code, files, ai_count, spawn=spawn)
                else:
                    kind, text = AI.build_files(slot, skins_out, code, files, say, counter=ai_count, spawn=spawn)
            except Exception as e:
                warnings.append(f"{slot.trailer}: AI skin left out - {e}")
                continue
            if kind:
                storages.setdefault(kind, []).append(text)
            for sid, sname, _d in skins_out:
                units.append({"token": f"sf{code}{sid or 'a'}", "trailer": slot.trailer, "slot": "AI traffic",
                              "name": sname})
    if storages:
        for kind, texts in storages.items():
            sui = f"/def/vehicle/ai/sf_{code}_{kind}.sui"
            files[sui] = ("\n\n".join(texts) + "\n").encode("utf-8")
            files[f"/def/vehicle/traffic_storage_{kind}.sf_{code}.sii"] = (
                "SiiNunit\n{\n" + f'@include "ai/sf_{code}_{kind}.sui"' + "\n}\n").encode("utf-8")

    if matte_used:   # one flat base texture for every matte paint job in the mod
        files[MATTE_TOBJ[:-5] + ".dds"] = matte_dds()
        files[MATTE_TOBJ] = T.make_tobj(MATTE_TOBJ[:-5] + ".dds")

    # icon per skin (paint job list) + mod manager icon
    first = live[0] if live else p["skins"][0]
    used_skins = {split(v)[0] for t, v in built.items() if t in {u["token"] for u in units}}
    for sk in p["skins"]:
        if sk["id"] not in used_skins:
            continue
        icon = code if not sk["id"] else f"{code}_{sk['id']}"
        rel = sk.get("icon") or ""
        if rel and os.path.exists(art_path(rel)):
            icon_im = _fit(Image.open(art_path(rel)).convert("RGBA"), 256, 64)
        else:
            icon_im = _default_icon(sk.get("name") or "Skin", p["base_color"])
        files[f"/material/ui/accessory/{icon}.dds"] = T.encode_dds_bgra(icon_im)
        files[f"/material/ui/accessory/{icon}.tobj"] = T.make_tobj(f"/material/ui/accessory/{icon}.dds", ui=True)
        files[f"/material/ui/accessory/{icon}.mat"] = (
            'effect : "ui.rfx" {\n\ttexture : "texture" {\n\t\tsource : "%s.tobj"\n'
            "\t\tu_address : clamp\n\t\tv_address : clamp\n\t\tmip_filter : none\n\t}\n}\n" % icon).encode()

    if p.get("mod_icon") and os.path.exists(art_path(p["mod_icon"])):
        mi = _fit(Image.open(art_path(p["mod_icon"])).convert("RGB"), 276, 162)
    else:
        mi = _default_icon(p["mod_name"], p["base_color"], 276, 162).convert("RGB")
    b = io.BytesIO()
    mi.save(b, format="JPEG", quality=92)
    files["/mod_icon.jpg"] = b.getvalue()

    desc = p.get("description") or f"{first.get('name') or p['mod_name']} - skins built with SkinForge."
    files["/mod_description.txt"] = desc.encode("utf-8")
    manifest = [
        "SiiNunit", "{", "mod_package : .package_name", "{",
        f"\tpackage_version: {sii_str(str(p.get('mod_version', '1.0')))}",
        f"\tdisplay_name: {sii_str(p['mod_name'])}",
        f"\tauthor: {sii_str(p.get('author') or 'unknown')}",
        "\tmp_mod_optional: true",
        '\ticon: "mod_icon.jpg"',
        '\tdescription_file: "mod_description.txt"',
        '\tcategory[]: "paint_job"',
        "}", "}", ""]
    files["/manifest.sii"] = "\n".join(manifest).encode("utf-8")

    say("Packing .scs…")
    size = write_scs(files, out_path)
    return BuildResult(out_path, units, warnings, time.time() - t0, size)


def write_scs(files: dict, out_path: str) -> int:
    """ZIP the way the game accepts it: directory entries + regular-file mode bits."""
    dirs = set()
    for path in files:
        parts = path.strip("/").split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]) + "/")
    tmp = out_path + ".partial"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for d in sorted(dirs):
            zi = zipfile.ZipInfo(d, date_time=time.localtime()[:6])
            zi.external_attr = (0o40755 << 16) | 0x10
            zi.compress_type = zipfile.ZIP_STORED
            z.writestr(zi, b"")
        for path in sorted(files):
            zi = zipfile.ZipInfo(path.lstrip("/"), date_time=time.localtime()[:6])
            zi.external_attr = 0o100644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[path])
    try:
        os.replace(tmp, out_path)
    except PermissionError:
        os.remove(tmp)
        raise BuildError(f"Can't overwrite {os.path.basename(out_path)} - close ATS / ETS2 (the game locks loaded mods) and build again")
    return os.path.getsize(out_path)
