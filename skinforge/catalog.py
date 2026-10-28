"""Discover skinnable trailers in a mod source by reading its template paint jobs.

Nothing is hard-coded per trailer: every `accessory_paint_job_data` in
def/vehicle/trailer_owned/<trailer>/paint_job/ that is a template (file name starts with
"template" or its name is "Template") becomes a *slot*: one image to paint, applied to the
same bodies/chassis the template is `suitable_for`. Accessory overrides that the template
ships in paint_job/accessory/<token>.sii become extra image slots.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from . import sii
from .scs import MultiSource, Source
from .texture import read_tobj_path

# Author templates whose suitable_for names cabins that don't exist; mapped from the cab names.
# Confirmed in game 2026-10-27: Jon's W900 "90s" templates never showed (MS2 skins targeting
# 42.flat.gl / 42.m.* did).
KNOWN_CAB_FIXES = {
    "kw.w900jl/template_42_90s": ["42.flat.gl", "42.m.1win", "42.m.2win"],
    "kw.w900jl/template_60_90s": ["60.flat.gl", "60.f.m.1win", "60.f.m.2win"],
    "kw.w900jl/template_60a_90s": ["60a.flat.gl", "60.a.m.1win", "60.a.m.2win"],
    "kw.w900jl/template_daycab90s": ["dc.flat.gl", "dc.f.m.1win", "dc.f.m.2win"],
    # Jon's "dc.*" also catches the Old daycabs, which have their own (90s) template above
    "kw.w900jl/template_daycab": ["dc.1win", "dc.2win"],
}

KNOWN_NAMES = {
    "gd53": "Great Dane Champion / Everest (box & reefer)",
    "wilson": "Wilson hopper",
    "livestockjr": "Livestock trailer",
    "polart": "Polar tanker",
    "lowboyjr": "Lowboy",
    "manac.flatbed": "Manac flatbed",
    "pete.jc": "Pete 389 Glider",
    "kw.w900jl": "KW W900 Highway Killer",
}


@dataclass
class Override:
    index: int
    acc_list: list
    template_mask: str | None      # dds path of the template's override texture
    extra: dict = field(default_factory=dict)  # other attrs to carry (flake_uvscale ...)


@dataclass
class Slot:
    key: str                # "<trailer>/<template token>" - stable id inside a project
    trailer: str
    token: str
    label: str
    suitable_for: list
    targets: list           # [(unit name, display name)]
    template_dds: str | None
    overrides: list = field(default_factory=list)
    template_attrs: dict = field(default_factory=dict)
    kind: str = "trailer"   # trailer | truck | logo
    uvsets: list = field(default_factory=list)   # trucks: [False] / [False, True] alt-UV variants shipped
    cabin_units: list = field(default_factory=list)   # trucks: cabins the template really matches
    fix_cabins: list = None                            # trucks: built-in correction (author typo)
    acc_list: list = field(default_factory=list)       # scs: parts this texture goes on
    tex_size: tuple = None                             # scs: output texture size (w, h)
    native: tuple = None                               # scs: size at SCS's own texel density
    main: bool = False                                 # scs: the paint job's main texture (chassis)
    vroot: str = "trailer_owned"                       # scs: def/vehicle/<vroot>/ (trailer_owned | truck)
    cab: bool = False                                  # scs trucks: a cab sheet (= paint job main texture)

    @property
    def root(self):
        if self.vroot == "car":
            return "car"
        return "truck" if (self.kind == "truck" or self.vroot == "truck") else "trailer_owned"

    def to_json(self):
        return {
            "key": self.key, "kind": self.kind, "trailer": self.trailer, "uvsets": self.uvsets, "token": self.token, "label": self.label,
            "suitable_for": self.suitable_for,
            "targets": [{"unit": u, "name": n} for u, n in self.targets],
            "has_template": self.template_dds is not None,
            "cabin_units": self.cabin_units,
            "fix_cabins": self.fix_cabins,
            "unmatched": [u for u, n in self.targets if u == n and "*" not in u and u.endswith((".cabin", ".body", ".chassis"))
                          ] if self.kind == "truck" else [],
            "tex_size": list(self.tex_size) if self.tex_size else None,
            "main": self.main,
            "cab": self.cab,
            "overrides": [{"index": o.index, "parts": o.acc_list, "has_template": o.template_mask is not None,
                           "tex": (o.template_mask or "").lower()}
                          for o in self.overrides],
        }


@dataclass
class Trailer:
    id: str
    name: str
    slots: list
    cabins: list = field(default_factory=list)   # trucks: [(unit, display name)]

    def to_json(self):
        kind = getattr(self, "vkind", None) or (self.slots[0].kind if self.slots else "trailer")
        return {"id": self.id, "name": self.name, "kind": kind, "slots": [s.to_json() for s in self.slots],
                "cabins": [{"unit": u, "name": n} for u, n in self.cabins],
                "lazy": getattr(self, "lazy", False), "error": getattr(self, "error", "")}


def _label_from_token(token: str, targets=None) -> str:
    t = re.sub(r"^template[_\-]?", "", token, flags=re.I)
    t = t.replace("_", " ").strip()
    if len(t) <= 1 and targets:
        names = sorted({n for _, n in targets})
        return " / ".join(names)
    t = re.sub(r"\b(\d{2})\b", r"\1'", t)
    return (t[:1].upper() + t[1:]) if t else "Main"


def _resolve_mask(src, mask_path: str | None) -> str | None:
    if not mask_path:
        return None
    if mask_path.lower().endswith(".tobj"):
        data = src.read(mask_path)
        if data:
            p = read_tobj_path(data)
            if p:
                if not p.startswith("/"):
                    p = mask_path.rsplit("/", 1)[0] + "/" + p
                if src.exists(p):
                    return p
        dds = mask_path[:-5] + ".dds"
        return dds if src.exists(dds) else None
    return mask_path if src.exists(mask_path) else None


def _units_in_dir(src, d: str, loader):
    out = []
    for name in src.listdir(d):
        if name.endswith("/") or not name.endswith(".sii"):
            continue
        p = d.rstrip("/") + "/" + name
        data = src.read(p)
        if data:
            out.extend(sii.parse_file(data, p, loader))
    return out


def _clean_name(n: str) -> str:
    n = n.replace("@@unit_in@@", '"').replace("@@chassis@@", "")
    n = re.sub(r"@@([a-z0-9_]+)@@", lambda m: m.group(1).replace("_", " "), n)
    return re.sub(r"\s+", " ", n).strip()


def _targets(parts: dict, suitable: list):
    targets = []
    for pat in suitable:
        hits = [k for k in parts if fnmatch.fnmatchcase(k, pat)]
        for h in sorted(hits):
            targets.append((h, parts[h]))
        if not hits:
            targets.append((pat, pat))
    return targets


def _overrides(src, pj_dir, names, loader):
    for n in names:
        p = f"{pj_dir}/accessory/{n}.sii"
        odata = src.read(p)
        if not odata:
            continue
        ovr = []
        for i, ou in enumerate(u for u in sii.parse_file(odata, p, loader) if u.cls == "simple_paint_job_data"):
            extra = {k: v for k, v in ou.attrs.items() if k not in ("paint_job_mask", "acc_list")}
            ovr.append(Override(i, ou.get_list("acc_list"), _resolve_mask(src, ou.get_str("paint_job_mask")), extra))
        return ovr
    return []


def _scan_trailers(src, loader):
    root = "/def/vehicle/trailer_owned"
    out = []
    for entry in src.listdir(root):
        if not entry.endswith("/"):
            continue
        tid = entry[:-1]
        tdir = f"{root}/{tid}"
        pj_dir = f"{tdir}/paint_job"
        parts = {}
        for sub in ("body", "chassis"):
            for u in _units_in_dir(src, f"{tdir}/{sub}", loader):
                parts[u.name] = _clean_name(u.get_str("name", u.name))
        slots = []
        for fn in src.listdir(pj_dir):
            if fn.endswith("/") or not fn.endswith(".sii"):
                continue
            p = f"{pj_dir}/{fn}"
            data = src.read(p)
            if not data:
                continue
            for u in sii.parse_file(data, p, loader):
                if u.cls != "accessory_paint_job_data":
                    continue
                unit_token = u.name.split(".")[0]
                token = fn[:-4]   # file stem: unique even when unit names collide (gd53 reuses "template.*")
                is_template = fn.lower().startswith("template") or (u.get_str("name", "").lower() == "template")
                if not is_template or not u.get_str("paint_job_mask"):
                    continue
                suitable = u.get_list("suitable_for")
                targets = _targets(parts, suitable)
                slots.append(Slot(
                    key=f"{tid}/{token}", trailer=tid, token=token, label=_label_from_token(token, targets),
                    suitable_for=suitable, targets=targets,
                    template_dds=_resolve_mask(src, u.get_str("paint_job_mask")),
                    overrides=_overrides(src, pj_dir, [unit_token, token], loader), template_attrs=dict(u.attrs)))
        if slots:
            slots.sort(key=lambda s: _natural(s.label))
            out.append(Trailer(tid, KNOWN_NAMES.get(tid, tid), slots))
    return out


def _scan_trucks(src, loader):
    """Truck template paint jobs. Authors usually ship each template twice (normal and
    alternate UV set) on the same image, so slots are grouped by template image."""
    root = "/def/vehicle/truck"
    out = []
    for entry in src.listdir(root):
        if not entry.endswith("/"):
            continue
        tid = entry[:-1]
        tdir = f"{root}/{tid}"
        pj_dir = f"{tdir}/paint_job"
        if not src.listdir(pj_dir):
            continue
        parts = {}
        cabins = {}
        for sub in ("cabin", "chassis"):
            for u in _units_in_dir(src, f"{tdir}/{sub}", loader):
                parts[u.name] = _clean_name(u.get_str("name", u.name))
                if sub == "cabin":
                    cabins[u.name] = parts[u.name]
        groups = {}   # mask path -> dict
        for fn in src.listdir(pj_dir):
            if fn.endswith("/") or not fn.endswith(".sii"):
                continue
            p = f"{pj_dir}/{fn}"
            data = src.read(p)
            if not data:
                continue
            for u in sii.parse_file(data, p, loader):
                if u.cls != "accessory_paint_job_data":
                    continue
                name = (u.get_str("name") or "").lower()
                mask = u.get_str("paint_job_mask")
                if "template" not in name or not mask:
                    continue
                alt = (u.get_str("alternate_uvset") or "false").lower() == "true"
                g = groups.setdefault(mask.lower(), {"mask": mask, "suitable": [], "uvsets": set(),
                                                     "units": [], "attrs": None})
                for sfor in u.get_list("suitable_for"):
                    if sfor not in g["suitable"]:
                        g["suitable"].append(sfor)
                g["uvsets"].add(alt)
                g["units"].append((alt, u.name.split(".")[0], fn[:-4]))
                if g["attrs"] is None or not alt:
                    g["attrs"] = dict(u.attrs)
        slots = []
        used_stems = {g["mask"].rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() for g in groups.values()}
        mod_files = {}
        for g in groups.values():
            stem = g["mask"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            token = re.sub(r"[^a-z0-9_]", "_", stem.lower())
            label = re.sub(r"^template[_\-]?", "", stem, flags=re.I).replace("_", " · ") or "Main"
            label = label[:1].upper() + label[1:]
            names = [n for alt, a, b in sorted(g["units"]) for n in (a, b)]   # normal-UV unit first
            s = Slot(key=f"{tid}/{token}", trailer=tid, token=token, label=label,
                     suitable_for=g["suitable"], targets=_targets(parts, g["suitable"]),
                     template_dds=_resolve_mask(src, g["mask"]),
                     overrides=_overrides(src, pj_dir, names, loader),
                     template_attrs=g["attrs"] or {}, kind="truck")
            s.uvsets = sorted(g["uvsets"])
            s.cabin_units = sorted({u for u, n in s.targets if u in cabins})
            # one template image shared by several cabs (Coronado SD: the day cab uses the 70mid
            # sheet): name the other cabs too, or they look missing from the list
            # (only when the mod ships that cab's own sheet unused, e.g. template_day_122sf.dds)
            head, sep, rest = label.partition(" · ")
            mdir, mstem = g["mask"].rsplit("/", 1)[0], stem.lower()
            extra = []
            for u in s.cabin_units:
                c = u.split(".")[0]
                own = mstem.replace(head.lower(), c.lower(), 1)
                if c.lower() != head.lower() and own != mstem and own not in used_stems \
                        and own + ".dds" in mod_files.setdefault(mdir, {f.lower() for f in src.listdir(mdir)}):
                    extra.append(c)
            if extra:
                s.label = " + ".join([head] + extra) + (sep + rest if sep else "")
            fix = KNOWN_CAB_FIXES.get(s.key)
            if fix:
                full = [f"{c}.{tid}.cabin" for c in fix]
                if all(f in cabins for f in full):
                    s.fix_cabins = full
            slots.append(s)
        if slots:
            _add_author_parts(src, tid, tdir, pj_dir, slots, loader)
            slots.sort(key=lambda s: _natural(s.label))
            out.append(Trailer(tid, _truck_name(src, tdir, tid, loader), slots,
                               cabins=sorted(cabins.items(), key=lambda kv: _natural(kv[1]))))
    return out


def _author_part_groups(src, pj_dir, loader):
    """Parts the author's own skins give their own texture: every acc_list block in
    paint_job/accessory/*.sii; blocks that share a part are merged (one texture each)."""
    groups = []   # list of sets
    adir = f"{pj_dir}/accessory"
    for fn in src.listdir(adir):
        if fn.endswith("/") or not fn.endswith(".sii"):
            continue
        data = src.read(f"{adir}/{fn}")
        if not data:
            continue
        try:
            units = sii.parse_file(data, f"{adir}/{fn}", loader)
        except Exception:
            continue
        for u in units:
            if u.cls != "simple_paint_job_data":
                continue
            acc = set(u.get_list("acc_list"))
            if not acc:
                continue
            hit = [g for g in groups if g & acc]
            for g in hit:
                acc |= g
                groups.remove(g)
            groups.append(acc)
    return groups


def _add_author_parts(src, tid, tdir, pj_dir, slots, loader):
    """Torpedo NextGen & co: the author's template only covers the cab, but his skins paint
    the box ("buss"), side skirts, side guards... with their own textures. Offer those parts as
    separate parts of every cab template; their templates come from the 3D models when the main
    pack is loaded, else from the author's matching part template (paintjob/<part>/<part>.dds)."""
    from . import scsgame
    from .scs import register_virtual
    covered = {a for sl in slots for o in sl.overrides for a in o.acc_list}
    groups = [g for g in _author_part_groups(src, pj_dir, loader) if not (g & covered)]
    if not groups:
        return
    up = f"/vehicle/truck/upgrade/{tid}/paintjob"
    author_tpl = {}
    for d in src.listdir(up):
        if d.endswith("/"):
            name = d[:-1]
            for cand in (f"{up}/{name}/{name}.dds", f"{up}/{name}/{name}.tobj"):
                if src.exists(cand):
                    author_tpl[re.sub(r"[^a-z0-9]", "", name.lower())] = _resolve_mask(src, cand)
                    break
    base_idx = 100
    for i, g in enumerate(sorted(groups, key=lambda g: sorted(g)[0])):
        accs = sorted(g)
        slot_dir = accs[0].split(".")[0]
        token = re.sub(r"[^a-z0-9_]", "_", accs[0].replace(".", "_"))
        vpath = f"/@skinforge/modpart_v{scsgame.TEMPLATE_VERSION}/{tid}/{token}.png"
        key = re.sub(r"[^a-z0-9]", "", slot_dir.lower())
        fallback = author_tpl.get(key) or next((v for k, v in author_tpl.items() if k.startswith(key) or key.startswith(k)), None)

        def make(accs=accs, fallback=fallback):
            data = scsgame.mod_part_template(src, tdir, tid, accs)
            if data is None and fallback:
                data = src.read(fallback)
            return data
        register_virtual(vpath, make)
        o = Override(base_idx + i, accs, vpath, {})
        for sl in slots:
            sl.overrides.append(o)


def _truck_name(src, tdir, tid, loader):
    if tid in KNOWN_NAMES:
        return KNOWN_NAMES[tid]
    data = src.read(f"{tdir}/data.sii")
    if data:
        for u in sii.parse_file(data, f"{tdir}/data.sii", loader):
            info = u.get_list("info")
            if info:
                return " ".join(info)
    return tid


def scan(src, res: int = 4096, trucks_loaded=(), progress=None, trailers_loaded=None, cars_loaded=()) -> list[Trailer]:
    from . import scsgame
    loader = src.read
    out = _scan_trailers(src, loader)
    out = [t for t in out if not t.id.startswith("scs.")]   # SCS's own trailers come from the game files
    out.sort(key=lambda t: t.id)
    trucks = _scan_trucks(src, loader)
    trucks.sort(key=lambda t: t.name.lower())
    if getattr(src, "game", None) is None:     # mod packs only: still read their 3D models
        from .scs import LayeredGame
        src = _ModsOnly(src, LayeredGame(getattr(src, "sources", [src]), None))
    own_t = {t.id for t in out}      # vehicles with an author template stay in their own group
    own = {t.id for t in trucks}
    st = scsgame.scan(src, res, progress, skip=own_t, loaded=trailers_loaded)
    sk = scsgame.scan_trucks(src, res, trucks_loaded, progress, skip=own)
    mod = lambda t: getattr(t, "vkind", "") in ("mod3d", "mod3d_truck")
    cars = scsgame.scan_cars(src, res, cars_loaded, progress)
    return (out + trucks + [t for t in st if mod(t)] + [t for t in sk if mod(t)]
            + [t for t in st if not mod(t)] + [t for t in sk if not mod(t)] + cars)


class _ModsOnly:
    def __init__(self, src, game):
        self._src, self.game = src, game

    def __getattr__(self, k):
        return getattr(self._src, k)


def _natural(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


def open_sources(paths: list[str]):
    from .scs import open_source
    srcs = [open_source(p) for p in paths]
    return srcs[0] if len(srcs) == 1 else MultiSource(srcs)
