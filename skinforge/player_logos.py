"""Player company logos (the logo you pick for your own company in the game).

Like MS2's "Player Company Logo" template: the mod REPLACES the game's own logos
/material/ui/player_logo/logo_<n>.(mat|tobj|dds) - nothing is added, and only one company
logo mod can be active (the one highest in the load order wins). Each slot's template is
the game's own logo at that index, at its own size, read from the game files.
"""
from __future__ import annotations

import struct

GROUP_ID = "player_logos"
DIR = "/material/ui/player_logo"
MAX = 8          # MS2 offers 8 (logo_0 .. logo_7)
_SIZE = {}       # index -> (w, h) of the game's own logo, filled by detect()
_SEEN = [False]  # game logos could be read


def role(i):
    return f"plogo_{i}"


def is_role(r):
    return r.startswith("plogo_") and r[6:].isdigit()


def index(r):
    return int(r[6:])


def dds_path(i):
    return f"{DIR}/logo_{i}.dds"


def _dds_size(data):
    if data and data[:4] == b"DDS " and len(data) >= 20:
        h, w = struct.unpack_from("<II", data, 12)
        return (w, h)
    return None


def detect(src):
    """Read the size of the game's own logos (a few bytes each)."""
    _SIZE.clear()
    _SEEN[0] = False
    if src is None:
        return
    for i in range(MAX):
        try:
            sz = _dds_size(src.read(dds_path(i)))
        except Exception:
            sz = None
        if sz:
            _SIZE[i] = sz
            _SEEN[0] = True


def size_for(r):
    """The game's size for this logo, or None when the game files couldn't be read."""
    return _SIZE.get(index(r))


def texture_paths(r):
    return [("player_company", "Player company logo", dds_path(index(r)))]


def files_for(r, dds_bytes, make_tobj):
    """mat + tobj + dds for one logo, exactly like MS2's template writes them."""
    i = index(r)
    mat = ('effect : "ui.rfx" {\n\ttexture : "texture" {\n\t\tsource : "logo_%d.tobj"\n'
           "\t\tu_address : clamp\n\t\tv_address : clamp\n\t\tmip_filter : none\n\t}\n}\n" % i).encode()
    return {f"{DIR}/logo_{i}.mat": mat,
            f"{DIR}/logo_{i}.tobj": make_tobj(dds_path(i), ui=True),
            dds_path(i): dds_bytes}


def group(game="ats"):
    from .catalog import Slot, Trailer
    slots = []
    for i in range(MAX):
        s = Slot(key=f"{GROUP_ID}/{role(i)}", trailer=GROUP_ID, token=role(i),
                 label=f"Company logo {i + 1} (replaces the game's logo_{i})",
                 suitable_for=[], targets=[("player_company", f"logo_{i}")],
                 template_dds=dds_path(i) if i in _SIZE else None, kind="logo")
        if i in _SIZE:
            s.tex_size = _SIZE[i]
        slots.append(s)
    return Trailer(GROUP_ID, "Player company logos", slots)
