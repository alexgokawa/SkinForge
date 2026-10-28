"""Jon Ruda truck "Your Logo" textures - plain texture overrides, no defs.

Each logo texture is a full 2048x2048 DXT5 image (logo on transparent) that replaces
Jon's own file at the same path; his .tobj/.mat files stay in use, so the mod must load
ABOVE the Jon Ruda truck mods. Paths traced from his .pmd -> .mat -> .tobj chain
(see project doc jon_ruda_your_logo_texture_map.md).
"""
from __future__ import annotations

import re
import struct

from .catalog import Slot, Trailer

LOGO_SIZE = 2048
GROUP_ID = "ruda_logos"

# role -> (label, file suffix appended to the truck's prefix)
ROLES = [
    ("black", "Logo - black option", "_black"),
    ("white", "Logo - white option", "_white"),
    ("rear_black", "Rear logo - black option", "_rear_black"),
    ("rear_white", "Rear logo - white option", "_rear_white"),
    ("front_steps_black", "Front steps - black option", "_black_front_steps"),
    ("front_steps_white", "Front steps - white option", "_white_front_steps"),
    ("rear_steps_black", "Rear steps - black option", "_black_rear_steps"),
    ("rear_steps_white", "Rear steps - white option", "_white_rear_steps"),
]
_ALL = [r[0] for r in ROLES]
_BASIC = ["black", "white", "rear_black", "rear_white"]

# truck id, name, folder, file prefix, roles
TRUCKS = [
    ("petejc", "Peterbilt 389 Glider", "vehicle/truck/upgrade/petejc/companylogo", "yourlogo", _ALL),
    ("w900jl", "Kenworth W900 Highway Killer", "vehicle/truck/upgrade/companylogo/w900jl", "yourlogo", _ALL),
    ("pete362", "Peterbilt 362 Cabover", "vehicle/truck/upgrade/pete362/companylogo", "yourlogo", _BASIC),
    ("fred122", "Freightshaker Coronado SD", "vehicle/truck/fred122/textures", "379_yourlogo", _BASIC),
    ("lord350", "Lord G350", "vehicle/truck/lord350/textures", "379_yourlogo", _BASIC),
    ("ram3500", "RAM 3500", "vehicle/truck/ram3500/textures", "379_yourlogo", ["black", "white"]),
]


# "Your ..." textures found in the loaded packs (any game): role (file stem) ->
# {"paths": [(vehicle id, vehicle name, path)], "size": (w, h)}. Filled by detect().
DETECTED: dict = {}
_KNOWN_PATHS = None


def _known_paths():
    global _KNOWN_PATHS
    if _KNOWN_PATHS is None:
        _KNOWN_PATHS = {f"/{folder}/{prefix}{s}.dds".lower()
                        for _t, _n, folder, prefix, roles in TRUCKS for r, _l, s in ROLES if r in roles}
    return _KNOWN_PATHS


def texture_paths(role: str):
    """[(truck id, truck name, '/vehicle/.../file.dds')] for one role."""
    if role in DETECTED:
        return list(DETECTED[role]["paths"])
    suffix = dict((r, s) for r, _l, s in ROLES)[role]
    return [(tid, name, f"/{folder}/{prefix}{suffix}.dds")
            for tid, name, folder, prefix, roles in TRUCKS if role in roles]


def size_for(role: str):
    return tuple(DETECTED[role]["size"]) if role in DETECTED else (LOGO_SIZE, LOGO_SIZE)


_YOUR = re.compile(r"^(?:\d+_)?your[a-z0-9_]*\.dds$")


def _dds_size(data):
    if data and data[:4] == b"DDS " and len(data) >= 20:
        h, w = struct.unpack_from("<II", data, 12)
        return (w, h)
    return None


def _vehicle_of(path):
    parts = path.strip("/").split("/")
    if len(parts) > 3 and parts[0] == "vehicle" and parts[1] in ("truck", "trailer", "trailer_owned"):
        vid = parts[3] if parts[2] == "upgrade" and len(parts) > 4 else parts[2]
        return vid, vid.replace("_", " ").title()
    return parts[-2] if len(parts) > 1 else "", (parts[-2] if len(parts) > 1 else "").replace("_", " ").title()


def detect(src):
    """Find author "Your logo / text / number ..." textures in the loaded packs: every
    vehicle texture named your*.dds (e.g. Jon's Torpedo: yourlogo, yourtext, yournumber,
    yourcontainer, yourmudflaps). Paths the built-in ATS list already covers are skipped."""
    DETECTED.clear()
    files = getattr(src, "files", None)
    if files is None:
        return DETECTED
    for p in files():
        name = p.rsplit("/", 1)[-1].lower()
        if not p.startswith("/vehicle/") or not _YOUR.match(name) or p.lower() in _known_paths():
            continue
        role = re.sub(r"^\d+_", "", name[:-4])
        vid, vname = _vehicle_of(p)
        d = DETECTED.setdefault(role, {"paths": [], "size": None})
        d["paths"].append((vid, vname, p))
        if d["size"] is None:
            d["size"] = _dds_size(src.read(p)) or (LOGO_SIZE, LOGO_SIZE)
    return DETECTED


def _label(role):
    words = {"yourlogo": "Logo", "yourtext": "Text", "yournumber": "Number", "yourcontainer": "Container / box",
             "yourmudflaps": "Mudflaps", "yourname": "Name"}
    if role in words:
        return words[role]
    t = re.sub(r"^your", "", role).replace("_", " ").strip()
    return (t[:1].upper() + t[1:]) if t else role


def group(game: str = "ats") -> Trailer:
    slots = []
    if game == "ats":
        for role, label, _s in ROLES:
            targets = [(tid, name) for tid, name, _p in texture_paths(role)]
            s = Slot(key=f"{GROUP_ID}/{role}", trailer=GROUP_ID, token=role, label=label,
                     suitable_for=[], targets=targets, template_dds=None, kind="logo")
            slots.append(s)
    for role in sorted(DETECTED):
        d = DETECTED[role]
        vehicles = sorted({n for _v, n, _p in d["paths"]})
        s = Slot(key=f"{GROUP_ID}/{role}", trailer=GROUP_ID, token=role,
                 label=f"{_label(role)} · {', '.join(vehicles)}" if game != "ats" else _label(role) + " · " + ", ".join(vehicles),
                 suitable_for=[], targets=[(v, n) for v, n, _p in d["paths"]], template_dds=d["paths"][0][2], kind="logo")
        slots.append(s)
    name = "Jon Ruda trucks - Your Logo" if game == "ats" else "Your logo textures (from your packs)"
    return Trailer(GROUP_ID, name, slots)


def role_for_filename(path: str):
    """Map a texture file name from an existing logo mod back to a role."""
    stem = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if stem.endswith(".dds"):
        stem = stem[:-4]
    for prefix in ("379_yourlogo", "yourlogo"):
        if stem.startswith(prefix):
            suffix = stem[len(prefix):]
            for role, _l, s in ROLES:
                if s == suffix:
                    return role
    return None
