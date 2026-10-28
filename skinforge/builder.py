"""Turn a project (settings + artwork per slot) into a ready-to-load .scs mod."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from . import texture as T
from .logos import GROUP_ID as LOGO_GROUP, LOGO_SIZE, texture_paths as logo_texture_paths

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
    "finish": "both",            # gloss | metallic | both
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
    return "" if key.startswith(LOGO_GROUP + "/") else sid


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


# --- build -------------------------------------------------------------------
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
    units = []

    finishes = {"gloss": ["g"], "metallic": ["m"], "both": ["g", "m"]}[p.get("finish", "both")]

    def say(msg):
        if progress:
            progress(msg)

    def art_path(rel):
        return os.path.join(project_dir, rel) if rel and not os.path.isabs(rel) else rel

    def make_texture(rel_art, game_dir, stem, key=None, size=None, sid="") -> str:
        fp = art_path(rel_art)
        with open(fp, "rb") as f:
            raw = f.read()
        sig = compose[0](key, sid) if (compose and key) else ""
        h = hashlib.sha1(raw + str(res).encode() + sig.encode() + str(size).encode()).hexdigest()
        if h in tex_cache:
            return tex_cache[h]
        im = T.load_image(raw)
        out_size = tuple(size) if size else pot_size(im.size, res)
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

    metallic = p["metallic"]
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

    def unit_text(slot, token, name, fin, alt_uv, mask_tobj, scs=False, icon=None, color=None):
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
            f"\tbase_color_locked: {'true' if p.get('color_locked') else 'false'}",
        ]
        if fin == "m":
            body += [
                "\tflipflake: true",
                f"\tflake_color: {sii_color(metallic['flake_color'])}",
                "\tflake_color_locked: false",
                f"\tflip_color: {sii_color(metallic['flip_color'])}",
                "\tflip_color_locked: false",
                f"\tflip_strength: {float(metallic['flip_strength']):.3f}",
                f"\tflake_density: {float(metallic['flake_density']):.3f}",
                f"\tflake_shininess: {float(metallic['flake_shininess']):.3f}",
                f"\tflake_clearcoat_rolloff: {float(metallic['flake_clearcoat_rolloff']):.3f}",
                '\tflake_noise: "/material/custom/flake_noise.tobj"',
            ]
        if scs:
            body.append('\tpart_type: "aftermarket"')
        body.append(f'\tpaint_job_mask: "{mask_tobj}"')
        if not scs:
            body += [f'\tsuitable_for[]: "{x}"' for x in suitable(slot)]
        elif getattr(slot, "cab", False):
            body += [f'\tsuitable_for[]: "{x}"' for x in slot.cabin_units]
        body.append("}")
        return ("SiiNunit\n{\n" + "\n".join(body) + "\n}\n").encode("utf-8")

    def split(v):
        """built value -> (skin id, slot key)"""
        return tuple(v.split("|", 1)) if "|" in v else ("", v)

    live = [sk for sk in p["skins"] if not sk.get("deleted")]
    if not any(cfg.get("art") or any((cfg.get("overrides") or {}).values())
               for sk in live for cfg in p["skin_art"].get(sk["id"], {}).values()):
        raise BuildError("No artwork assigned - drop an image on at least one slot")

    # logos: texture replacements, shared by every skin
    for key, cfg in sorted(p["skin_art"].get("", {}).items()):
        if not key.startswith(LOGO_GROUP + "/") or not cfg.get("art"):
            continue
        role = key.split("/", 1)[1]
        with open(art_path(cfg["art"]), "rb") as f:
            im = T.load_image(f.read())
        from .logos import size_for
        lw, lh = size_for(role)
        im = _fit_contain(im, lw) if lw == lh else T.fit_art(im, (lw, lh))
        say(f"Compressing logo {role}…")
        dds = T.encode_dds_dxt5(im)
        for tid, _name, path in logo_texture_paths(role):
            files[path] = dds
            units.append({"token": role, "trailer": tid, "slot": "logo texture", "name": path})

    per_key = {}   # namespaced key -> (slot, mask_tobj, ovr_text, skin)
    shipped_skins = {split(v)[0] for v in built.values()}
    for sk in p["skins"]:
        sid = sk["id"]
        if sk.get("deleted") and sid not in shipped_skins:
            continue
        pre = f"{sid}|" if sid else ""
        arts = {} if sk.get("deleted") else {k: c for k, c in p["skin_art"].get(sid, {}).items()
                                              if not k.startswith(LOGO_GROUP + "/")}
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
            tid = k[5:].split("/")[0] if k.startswith("@scs/") else (slots_by_key[k].trailer if k in slots_by_key and slots_by_key[k].kind == "scs" else None)
            if tid and tid not in scs_trailers:
                scs_trailers.append(tid)
        emit = [k for k in emit if not k.startswith("@scs/") and not (k in slots_by_key and slots_by_key[k].kind == "scs")]

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
                mask = make_texture(cfg["art"], game_dir, f"{n:02d}", key, sid=sid)
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
                omask = make_texture(rel, game_dir, f"{sid + '_' if sid else ''}part_{stem}") if rel else blank_texture(game_dir)
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
                for fin in finishes:
                    token = f"{code}{n:02d}{'m' if fin == 'm' else ''}{'u' if alt_uv else ''}"
                    if len(token) > 12:
                        raise BuildError(f"Unit token {token} longer than 12 characters")
                    name = sname
                    if len(finishes) > 1 and fin == "m":
                        name += " Metallic"
                    if alt_uv:
                        name += " (Alt UV)"
                    files[pj_path(slot, token)] = unit_text(slot, token, name, fin, alt_uv, mask_tobj, icon=icon,
                                                            color=cfg_of(key).get("color"))
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
            main_trailer = None
            for sl in parts:
                rel = cfg_of(sl.key).get("art")
                if not rel:
                    continue
                size = sl.tex_size or (res, res)
                dds = make_texture(rel, root_dir, sl.token, sl.key, size, sid=sid)
                if sl.main:
                    main_trailer = dds
                    continue
                nw, nh = sl.native or size
                # SCS's own numbers: trailer parts 10 x texture width (box rew_xm22), truck parts
                # width / 64 (Scania S log_0: mirror 256 -> 4, skirt 512 -> 8, fender 1024 -> 16)
                uvs = nw / 64.0 if sl.vroot == "truck" else 10.0 * nw
                lines = ["simple_paint_job_data : .ovr%d" % len(blocks), "{",
                         f'\tpaint_job_mask: "{dds[:-4]}.tobj"',
                         f"\tflake_uvscale: {uvs:.6f}"]
                if nh != nw:
                    lines.append(f"\tflake_vratio: {nh / nw:.6f}")
                lines += [f'\tacc_list[]: "{a}"' for a in sl.acc_list]
                lines.append("}")
                blocks.append("\n".join(lines))
            ovr_text = ("SiiNunit\n{\n" + "\n\n".join(blocks) + "\n}\n").encode("utf-8") if blocks else None
            for cab, ukey in targets:
                n = nums[ukey]
                anchor = cab or all_parts[0]
                if cab is not None:
                    rel = cfg_of(cab.key).get("art")
                    main = make_texture(rel, root_dir, cab.token, cab.key, cab.tex_size or (res, res), sid=sid) \
                        if rel else blank_texture(root_dir)
                else:
                    main = main_trailer or blank_texture(root_dir)
                if not blocks and main.endswith("/blank.dds"):
                    warnings.append(f"{sname}: {tid}{' ' + cab.label if cab else ''}: no artwork, but it shipped "
                                    f"before - kept as plain colour so existing saves don't crash")
                per_key[pre + ukey] = (anchor, main[:-4] + ".tobj", ovr_text, sk, color)
                for fin in finishes:
                    token = f"{code}{n:02d}{'m' if fin == 'm' else ''}"
                    name = sname + (" Metallic" if len(finishes) > 1 and fin == "m" else "")
                    files[pj_path(anchor, token)] = unit_text(anchor, token, name, fin, False, main[:-4] + ".tobj",
                                                              scs=True, icon=icon, color=color)
                    if ovr_text:
                        files[pj_path(anchor, token, acc=True)] = ovr_text
                    units.append({"token": token, "trailer": tid,
                                  "slot": (cab.label + " + " if cab else "") + f"{len(blocks)} parts", "name": name})
                    built[token] = pre + ukey

    # variants (finish / alt UV) that shipped before but are switched off now: keep them alive
    emitted = {u["token"] for u in units}
    for token, key in list(built.items()):
        if token in emitted or key not in per_key:
            continue
        slot, mask_tobj, ovr_text, sk, kcolor = per_key[key]
        fin = "m" if "m" in re.sub(r"^[a-z0-9_]*?\d+", "", token[len(code):]) else "g"
        alt_uv = token.endswith("u")
        sid = sk["id"]
        files[pj_path(slot, token)] = unit_text(slot, token, (sk.get("name") or "Skin") + (" Metallic" if fin == "m" else "")
                                                + (" (Alt UV)" if alt_uv else ""), fin, alt_uv, mask_tobj,
                                                scs=split(key)[1].startswith("@scs/"), icon=code if not sid else f"{code}_{sid}",
                                                color=kcolor)
        if ovr_text:
            files[pj_path(slot, token, acc=True)] = ovr_text
        warnings.append(f"{token}: variant turned off but it shipped before - kept for save compatibility")

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
