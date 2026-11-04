"""Custom yard signs - texture replacements for map mods that ship an editable sign texture.

MattVT's yards ship "EDIT SIGN HERE.scs" (one 4096 sign texture, e.g. taina95/sign_surette/
sign_template.dds); SneaK's yards read their brand / barn art from "SneaKs Master Def.scs"
(material/own/*, material/snk/*). The user's SkinForge mod writes a new .dds at the same path,
so it must load ABOVE those packs in the mod manager. Nothing else (no defs) is needed.

The packs are found in the game's mod folder (and in the loaded sources): never added as
skin sources themselves.
"""
from __future__ import annotations

import os
import re
import struct
import zipfile

GROUP_ID = "yard_signs"

# SneaK's Master Def: file stem -> label (from his read.txt)
SNEAKS = {
    "skinnable1": "Audubon barn",
    "snkbrand1": "Mill Creek barn 1",
    "snkbrand2": "Mill Creek barn 2",
    "snkbrand3": "Rio Pecos barn",
    "snkbrand4": "Diablo barn",
    "cjart": "Carls Junction barn",
    "snkefbig": "Effingham barn - big",
    "snkefsmall": "Effingham barn - small",
    "snksignbase": "Sign base",
    "sign": "Workshop sign",
    "laptopscreen": "Laptop screen",
    "cabinetdecals": "Cabinet decals",
    "snkmailpouch": "Mail Pouch barn",
    "swlogo2": "Trailer logo",
    "msdsign": "Manchester SD sign",
    "rockfordart": "Rockford IL barn",
    "vlsign": "Van Leer Coal Mine sign",
    "rushville_art": "Rushville monument",
    "voltison": "Company trailer (Voltison)",
}
_SNEAKS_DIRS = ("material/own/", "material/snk/", "material/snkmonu/", "vehicle/trailer/company/voltison",
                "vehicle/trailer_owned/snkts/")

# role -> {"label", "pack", "path", "archive", "size", "srgb"}
DETECTED: dict = {}


def is_role(role: str) -> bool:
    return role in DETECTED


def _dds_info(head: bytes):
    """(w, h), sRGB flag from a DDS header."""
    if head[:4] != b"DDS " or len(head) < 128:
        return None, False
    h, w = struct.unpack_from("<II", head, 12)
    srgb = False
    if head[84:88] == b"DX10" and len(head) >= 132:
        srgb = struct.unpack_from("<I", head, 128)[0] in (29, 72, 75, 78, 91, 93, 99)
    return (w, h), srgb


def _pack_of(names):
    """'sneaks' / 'mattvt' / None and the sign textures in one archive's file list."""
    low = [n for n in names if n.lower().endswith(".dds")]
    if any(n.lower().startswith("def/") for n in names) and not any(
            n.lower().startswith(_SNEAKS_DIRS[:2]) for n in low):
        return None, []          # a real vehicle / map mod, not a sign pack
    sneaks = [n for n in low if n.lower().startswith(_SNEAKS_DIRS)
              and n.rsplit("/", 1)[-1][:-4].lower() in SNEAKS]
    if len(sneaks) >= 3:
        return "sneaks", sneaks
    # MattVT style: an "edit me" sign pack - dds files named / foldered "sign"
    signs = [n for n in low if re.search(r"(^|/)sign[^/]*(/|$)|sign[^/]*\.dds$", n.lower())]
    if signs and len(low) <= 8:
        return "mattvt", signs
    return None, []


def _role(pack, path):
    parts = path.lower()[:-4].split("/")
    stem = parts[-1] if pack == "sneaks" else "_".join(parts[-2:])
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return f"{'snk' if pack == 'sneaks' else 'mvt'}_{stem}"


def _label(pack, path):
    stem = path.rsplit("/", 1)[-1][:-4].lower()
    if pack == "sneaks":
        return SNEAKS.get(stem, stem)
    folder = path.strip("/").split("/")
    place = folder[-2] if len(folder) > 1 else stem
    place = re.sub(r"^sign_", "", place).replace("_", " ").title()
    return f"Yard sign · {place}"


def _is_original(fp):
    """The author's pack (has def/ files) rather than a texture-only repaint of it (e.g. a paint mod
    that overrides the same sign textures): the original's textures and sizes are the real ones."""
    if not fp.lower().endswith((".scs", ".zip")):
        return False
    try:
        with open(fp, "rb") as f:
            if f.read(2) != b"PK":
                return False
        with zipfile.ZipFile(fp) as z:
            low = [n.lower() for n in z.namelist()]
            if "manifest.sii" in low:
                man = z.read(z.namelist()[low.index("manifest.sii")]).lower()
                if b'"paint_job"' in man:
                    return False      # a repaint built with MS2 / SkinForge (category paint_job)
            # SneaK's master def: read.txt + Template/; other authors: their own defs
            return any(n.startswith(("def/", "template/")) or n == "read.txt" for n in low)
    except Exception:      # unreadable, broken or password-protected zip (RuntimeError): not a sign pack
        return False


def detect(mod_dirs=(), src=None):
    """Find sign packs in the mod folder(s). Only zip-type .scs files are read (the packs are
    small zips); big archives just get their file list read, which is quick."""
    DETECTED.clear()
    for d in mod_dirs:
        try:   # newest pack first: an updated master def wins over an old copy left in the folder
            names = sorted(os.listdir(d), key=lambda n: -os.path.getmtime(os.path.join(d, n)))
        except OSError:
            continue
        names = sorted(names, key=lambda n: not _is_original(os.path.join(d, n)))   # stable: keeps date order
        for fn in names:
            if not fn.lower().endswith((".scs", ".zip")):
                continue
            fp = os.path.join(d, fn)
            try:
                with open(fp, "rb") as f:
                    if f.read(2) != b"PK":
                        continue
                with zipfile.ZipFile(fp) as z:
                    all_names = z.namelist()
                    pack, found = _pack_of(all_names)
                    lower = {n.lower(): n for n in all_names}
                    for n in found:
                        with z.open(n) as f:
                            size, srgb = _dds_info(f.read(160))
                        if not size:
                            continue
                        role = _role(pack, n)
                        if role in DETECTED:
                            continue
                        stem = n.rsplit("/", 1)[-1][:-4].lower()
                        # the author's own painting template (SneaK: Template/<name>_template.png)
                        tpl = lower.get(f"template/{stem}_template.png")
                        DETECTED[role] = {"label": _label(pack, n), "pack": pack, "path": "/" + n.lstrip("/"),
                                          "archive": fp, "archive_name": fn, "size": size, "srgb": srgb,
                                          "template": tpl}
            except Exception:      # incl. encrypted zips (RuntimeError: 'manifest.sii is encrypted'): skip the file
                continue
    return DETECTED


PACK_NAMES = {"mattvt": "MattVT yards", "sneaks": "SneaK's yards"}


# the user's template folder (Sources > Template folder…): a PNG named after the sign wins over
# what the pack ships. Set by the app; _PNGS caches the folder's file list (cleared on reload).
TEMPLATE_ROOT = [None]
_PNGS: dict = {}


def _compact(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


# other names people give a sign's template (file stem -> words)
ALIASES = {"cabinetdecals": ("cabinet",), "snksignbase": ("signbase",), "snkmailpouch": ("mailpouch",),
           "laptopscreen": ("laptop",)}
_GENERIC = {"signs", "template", "templates", "yardsign", "logos", "texture"}


def _keys(d):
    """Names a template for this sign may carry: the texture's file stem, its label, the yard name."""
    stem = d["path"].rsplit("/", 1)[-1][:-4].lower()
    keys = {_compact(d["label"].split("·")[-1])}
    if d["pack"] == "sneaks" or stem not in ("sign", "sign_template", "template"):
        keys.add(_compact(stem))
    folder = d["path"].strip("/").split("/")
    if d["pack"] == "mattvt" and len(folder) > 1:
        keys.add(_compact(re.sub(r"^sign_", "", folder[-2])))
    return {k for k in keys if len(k) >= 5 and k not in _GENERIC}


def _pngs(root):
    if root not in _PNGS:
        out = []
        for dp, _dn, fns in os.walk(root):
            out += [os.path.join(dp, f) for f in fns if f.lower().endswith(".png") and not f.startswith("._")]
        _PNGS[root] = out
    return _PNGS[root]


def forget_folder():
    _PNGS.clear()


def folder_template(role):
    """PNG in the user's template folder named after this sign, or None. Prefers files with
    'template' in the name, then the shortest name (closest match)."""
    root, d = TEMPLATE_ROOT[0], DETECTED.get(role)
    if not root or not d or not os.path.isdir(root):
        return None
    keys = _keys(d)
    stem = _compact(d["path"].rsplit("/", 1)[-1][:-4])
    keys |= {_compact(a) for a in ALIASES.get(stem, ())}
    mvt = [r for r, x in DETECTED.items() if x["pack"] == "mattvt"]
    hits = []
    for fp in _pngs(root):
        c = _compact(os.path.splitext(os.path.basename(fp))[0])
        rel = _compact(os.path.relpath(fp, root))
        if c == stem or c == stem + "template":            # named exactly like the texture (sign.png)
            hits.append((0, 0, len(c), fp))
        elif any(k in c for k in keys):
            hits.append((1, "template" not in c, len(c), fp))
        elif d["pack"] == "mattvt" and len(mvt) == 1 and "mattvt" in rel:   # the one MattVT sign: its folder
            hits.append((2, "template" not in c, len(c), fp))
    return min(hits)[3] if hits else None


def read(role):
    """Your template folder's PNG for this sign, else the author's template if the pack ships one,
    else the pack's texture (what shows in game today)."""
    d = DETECTED.get(role)
    if not d:
        return None
    fp = folder_template(role)
    if fp:
        try:
            with open(fp, "rb") as f:
                return f.read()
        except OSError:
            pass
    try:
        with zipfile.ZipFile(d["archive"]) as z:
            return z.read(d.get("template") or d["path"].lstrip("/"))
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def texture_paths(role):
    d = DETECTED[role]
    return [(d["pack"], PACK_NAMES.get(d["pack"], d["pack"]), d["path"])]


def size_for(role):
    return tuple(DETECTED[role]["size"])


def group():
    from .catalog import Slot, Trailer
    slots = []
    for role in sorted(DETECTED, key=lambda r: (DETECTED[r]["pack"], DETECTED[r]["label"])):
        d = DETECTED[role]
        w, h = d["size"]
        s = Slot(key=f"{GROUP_ID}/{role}", trailer=GROUP_ID, token=role,
                 label=f"{PACK_NAMES.get(d['pack'], d['pack'])} · {d['label']}",
                 suitable_for=[], targets=[(d["pack"], d["archive_name"])], template_dds=d["path"], kind="logo")
        s.tex_archive = d["archive"]
        s.tex_size = (w, h)
        slots.append(s)
    return Trailer(GROUP_ID, "Yard signs (MattVT / SneaK)", slots) if slots else None
