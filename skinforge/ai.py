"""AI traffic skins (SCS's own AI cars / vans / trucks / buses).

AI vehicles have no paint jobs: each livery is a *look* of the AI model (.pmd) whose materials
point at other textures (Ford Transit: default look = color.dds, "dhl" look = posted2.dds).
SkinForge does what MS2's AiTrafficSkin does, but from the user's own game files:

- the AI model set (.pmd + .pmg + .pmc, every LOD) is copied to /vehicle/ai/sf_<code>/<vehicle>/
  and each copied .pmd gets one extra look per skin;
- that look = the default look with the body paint material (effect "...paint...", e.g.
  eut2.dif.spec.mult.dif.spec.add.env.paint) swapped for a copy that reads the skin texture.
  Lights / glass / chrome keep SCS's texture, so the artwork only has to cover the body;
- baked far LODs (eut2.baked.*) keep the default look - far away the vehicle shows plain;
- an accessory_chassis_data + traffic_vehicle unit per skin (copied from SCS's own unit for
  that model: speed, sounds, mass, wheels, drivers), added through
  /def/vehicle/traffic_storage_<kind>.sf_<code>.sii so nothing of SCS's is overwritten.
"""
from __future__ import annotations

import re

from . import sii
from .pmg import ModelError, read_pmd, read_pmd_materials, read_pmg, add_looks

# traffic storages that hold ordinary road traffic (not police / trains / trailers / events)
STORAGES = ("car", "truck_light", "truck_medium", "truck_heavy", "truck_long", "bus", "truck")
# MS2's AI-truck recipe: flat light grey base x the skin as texture_mult on the paint UV set
AI_TRUCK_BASE = (219, 218, 219)
_SKIP_SUI = re.compile(r"(police|_hp$|crash|event|parked|abandoned|_p$|taxi|ambulance|fire|border|context|drivers)")
_TEX_BASE = re.compile(r'texture\s*:\s*"texture_base"\s*\{[^}]*?source\s*:\s*"([^"]+)"', re.S)


def _read_text(game, path):
    d = game.read(path)
    return sii.decode(d) if d and sii.is_text_sii(d) else None


DRIVERS_SUI = "/def/vehicle/ai/drivers_generic.sui"   # in base def.scs (SCS escorts + Jazzycat trucks use it)


def _includes(text):
    return list(dict.fromkeys(re.findall(r'^\s*@include\s+"([^"]+)"', text or "", re.M)))


def list_ai(game) -> dict:
    """AI vehicles by model: id -> {name, model, lods, collision, storage, unit (traffic_vehicle
    sii.Unit), accs [sii.Unit], sui, chassis}. Cheap (def files only)."""
    out = {}
    try:
        vdir = [n for n in game.listdir("/def/vehicle") if not n.endswith("/")]
    except Exception:
        vdir = []
    for kind in STORAGES:
        # SCS's own storage + the "<storage>.<dlc>.sii" files DLCs / mods add (not SkinForge's own)
        files = [f"/def/vehicle/traffic_storage_{kind}.sii"] + [
            f"/def/vehicle/{n}" for n in sorted(vdir)
            if n.startswith(f"traffic_storage_{kind}.") and n.endswith(".sii") and ".sf_" not in n]
        st = "\n".join(t for t in (_read_text(game, p) for p in files) if t)
        if not st:
            continue
        for inc in _includes(st):
            path = "/def/vehicle/" + inc.lstrip("/") if not inc.startswith("/") else inc
            stem = path.rsplit("/", 1)[-1][:-4]
            if _SKIP_SUI.search(stem):
                continue
            text = _read_text(game, path)
            if not text:
                continue
            units = sii.parse(text)
            by_name = {u.name: u for u in units}
            for u in units:
                if u.cls != "traffic_vehicle":
                    continue
                accs = [by_name.get(a) for a in u.get_list("accessories")]
                accs = [a for a in accs if a is not None]
                ch = next((a for a in accs if a.cls == "vehicle_accessory"
                           and "/def/vehicle/ai/" in (a.get_str("data_path") or "")), None)
                if ch is None:
                    continue
                cd = game.read(ch.get_str("data_path"))
                cu = next((x for x in (sii.parse_file(cd) if cd else []) if x.cls == "accessory_chassis_data"), None)
                if cu is None or not cu.get_str("model"):
                    continue
                model = cu.get_str("model")
                aid = _ai_id(model)
                look = (cu.get_str("look") or "default").lower()
                cur = out.get(aid)
                if cur is not None and not (cur["look"] != "default" and look == "default") \
                        and not (cur["storage"] == "truck_long" and kind == "truck"):
                    continue   # keep the first unit, one showing the default look, or a single-trailer semi
                out[aid] = {"id": aid, "name": _ai_name(model), "model": model,
                            "lods": cu.get_list("lods"), "collision": cu.get_str("collision"),
                            "variant": cu.get_str("variant") or "default", "look": look,
                            "chassis": cu, "unit": u, "accs": accs, "storage": kind,
                            "includes": _includes(text), "sui": path}
    return out


def _dir_stem(model):
    """('ford_transit_2016', 'ai_tv') - for /vehicle/car/<x>/ai/<stem>.pmd the car folder."""
    parts = model.strip("/").split("/")
    d = parts[-2] if len(parts) > 1 else "ai"
    if d == "ai" and len(parts) > 2:
        d = parts[-3]
    return d, parts[-1].rsplit(".", 1)[0]


def vkind_of(info):
    """AI semi trucks (they pull trailers) get their own list section."""
    if not info:
        return "scs_ai"
    semi = info.get("storage") == "truck" or bool(info["unit"].get_list("trailer_chains"))
    return "scs_ai_truck" if semi else "scs_ai"


def _ai_id(model):
    d, stem = _dir_stem(model)
    return "ai." + re.sub(r"[^a-z0-9_]", "_", d + ("" if stem == "ai" else "_" + stem))


def _ai_name(model):
    d, stem = _dir_stem(model)
    words = d.replace("_", " ").title().replace("Westernstar", "Western Star")
    raw = re.sub(r"^ai_|_lod0$|^ai$", "", stem)
    from .scsgame import _cab_name
    named = _cab_name(raw) if re.fullmatch(r"dc|day|sl?_?\d{2,3}\w*", raw or "-") else ""
    extra = named or raw.replace("_", " ").strip()
    return f"AI · {words}" + (f" ({extra})" if extra else "")


def paint_material(game, pmd_path):
    """(material index, material path, mat text) of the body paint material in look 0."""
    pmd = game.read(pmd_path)
    if not pmd:
        return None
    mats = read_pmd_materials(pmd, 0)
    for i, mp in enumerate(mats):
        txt = _read_text(game, mp)
        if not txt:
            continue
        m = re.search(r'effect\s*:\s*"([^"]+)"', txt)
        eff = m.group(1) if m else ""
        if ".paint" in eff and "baked" not in eff and _TEX_BASE.search(txt):
            return i, mp, txt
    # AI semi trucks: body = truckpaint (plain colour from color[], paint layout on uv set 1)
    for i, mp in enumerate(mats):
        txt = _read_text(game, mp)
        m = re.search(r'effect\s*:\s*"([^"]+)"', txt or "")
        if m and "truckpaint" in m.group(1):
            return i, mp, txt
    return None


def is_truckpaint(txt):
    m = re.search(r'effect\s*:\s*"([^"]+)"', txt or "")
    return bool(m and "truckpaint" in m.group(1))


def layout(game, info):
    """Paint-material triangles of the main model on its first UV set (= the atlas)."""
    from .scsgame import Layout, _dedupe
    pm = paint_material(game, info["model"])
    if pm is None:
        return None, None
    idx, mp, _txt = pm
    uvset = 1 if is_truckpaint(_txt) else 0    # truckpaint: the paint layout is the 2nd UV set
    pmg = game.read(info["model"][:-4] + ".pmg")
    if not pmg:
        return None, None
    tris = []
    pieces = [pc for pc in read_pmg(pmg) if pc.material == idx and pc.uvs and pc.pos]
    scales, used = {}, []
    for pc in pieces:
        if uvset:
            # same UV-set choice as the player cab sheets (proven in game per truck)
            from .scsgame import _paint_set
            k = _paint_set(pc, pieces, False, scales)
        else:
            k = 0
        if len(pc.uvs) <= k:
            continue
        uv = pc.uvs[k]
        used += [k] * len(pc.tris)
        for a, b, c in pc.tris:
            n = (0.0, 0.0, 0.0)
            if pc.nrm:
                n = tuple(pc.nrm[a][j] + pc.nrm[b][j] + pc.nrm[c][j] for j in range(3))
            # wrap to 0..1 like the sampler does (some AI bodies run past v = 0)
            uvs = tuple((x % 1.0 if not 0 <= x <= 1 else x, y % 1.0 if not 0 <= y <= 1 else y)
                        for x, y in (uv[a], uv[b], uv[c]))
            tris.append((uvs, (pc.pos[a], pc.pos[b], pc.pos[c]), n))
    if not tris:
        return None, mp
    lay = Layout(info["model"], _dedupe(tris))
    lay.uvset = max(set(used), key=used.count) if used else 0   # the set the skin texture is laid out on
    return lay, mp


MESSY = 2.6   # UV area / covered area: ~1 clean, ~2 mirrored sides, more = parts stacked on each other


def _quality(lay):
    """(messy?, layout without the giant stretched faces). SCS's AI semis and some traffic
    trailers were never laid out for liveries (they only get a flat colour), so their UVs pile
    parts on top of each other - the same art then lands on several parts."""
    from .scsgame import Layout, _uv_area, stack_ratio
    r = stack_ratio([lay])
    messy = r > MESSY
    if messy:   # stretched faces across half the sheet (hidden / degenerate parts) only add noise
        tris = [t for t in lay.tris if _uv_area(t) < 0.05] or lay.tris
        lay = Layout(lay.model, tris)
    return messy, lay


def _mark_messy(t, s):
    s.label += " · overlapping layout"
    s.messy = True
    t.name += " (overlapping)"


AIQ_VERSION = 1   # bump when the usable/unusable rule changes (cached in settings.json)


def usable(game, aid, info):
    """True = clean template, False = overlapping / nothing paintable. Reads the model once
    (no template drawn) - used by the background check that keeps unusable AI out of the list."""
    from .scsgame import load_layout, stack_ratio
    try:
        if aid.startswith("ait."):
            lay = load_layout(game, info["model"])
        else:
            lay, _mp = layout(game, info)
        return lay is not None and stack_ratio([lay]) <= MESSY
    except Exception:
        return None          # read error: don't remember a verdict, try again next start


def all_infos(game):
    out = dict(list_ai(game))
    out.update(list_ai_trailers(game))
    return out


def scan_ai(game, aid, res, info=None):
    """One AI vehicle as a group with one 'Body' slot (template drawn from the model)."""
    from .catalog import Slot, Trailer
    from .scsgame import Group, _register, render_template, template_path
    info = info or list_ai(game).get(aid)
    t = Trailer(aid, info["name"] if info else aid, [])
    t.vkind = vkind_of(info)
    if not info:
        t.error = "Not found in the game's traffic files"
        return t
    lay, mp = layout(game, info)
    if lay is None:
        t.error = "No paintable body material on this AI model"
        return t
    messy, lay = _quality(lay)
    g = Group("ai", [], [lay])   # "ai": no face labels - on AI atlases they cover the shapes
    g._stacked = False   # AI sheets: symmetric parts share UVs on purpose, draw them normally
    size = (res, res)                    # AI atlases are square
    path = template_path(aid, "body", size)
    _register(game, path, (lambda sz, g=g: render_template(g, sz)), size)
    s = Slot(key=f"{aid}/body", trailer=aid, token="body", label="Body (whole vehicle sheet)",
             suitable_for=[], targets=[(info["model"], info["name"])], template_dds=path, kind="ai")
    s.tex_size, s.native, s.vroot = size, size, "ai"
    s.ai = info
    s.ai_read = game.read
    if messy:
        _mark_messy(t, s)
    t.slots = [s]
    return t


def scan(src, res, loaded=(), progress=None):
    game = getattr(src, "game", None)
    if game is None:
        return []
    from .catalog import Trailer
    try:
        found = list_ai(game)
    except Exception:
        import traceback
        traceback.print_exc()
        return []
    out = []
    for aid in sorted(found, key=lambda a: found[a]["name"].lower()):
        info = found[aid]
        if aid in loaded:
            if progress:
                progress(f"Reading {info['name']}…")
            try:
                t = scan_ai(game, aid, res, info)
            except Exception:
                import traceback
                traceback.print_exc()
                t = Trailer(aid, info["name"], [])
                t.vkind, t.error = vkind_of(info), "Could not read this AI model"
        else:
            t = Trailer(aid, info["name"], [])
            t.vkind = vkind_of(info)
        t.lazy = aid not in loaded
        out.append(t)
    return out


# ------------------------------------------------------------------ build
def _fmt(v):
    return v if isinstance(v, str) else str(v)


def _unit_text(cls, name, attrs):
    lines = [f"{cls} : {name}", "{"]
    for k, v in attrs:
        lines.append(f"\t{k}: {_fmt(v)}")
    lines.append("}")
    return "\n".join(lines)


_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(n):
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = _B36[r] + out
        if not n:
            return out


def build_files(slot, skins, code, files, say=print, spawn=1.0, counter=None, replace_scs=True, chains=None):
    """skins = [(skin id, skin name, dds game path)] for this AI vehicle. Adds the model copies,
    materials, defs and the storage include to `files`. Returns (storage kind, sui text)."""
    info, read = slot.ai, slot.ai_read
    vid = re.sub(r"[^a-z0-9_]", "_", slot.trailer.split(".", 1)[1])[:40]
    base = f"/vehicle/ai/sf_{code}/{vid}"
    esc = getattr(slot, "escort", None)
    pm = (None, esc["mat"], esc["text"]) if esc else paint_material(_Reader(read), info["model"])
    if pm is None:
        raise ModelError(f"{info['name']}: no paint material")
    _idx, paint_mp, paint_txt = pm
    atlas = _tex_image(_Reader(read), esc["base"]) if esc and esc.get("base") else None
    looks, mats_for_skin = [], {}
    for n, (sid, _name, dds) in enumerate(skins):
        look = f"sf{code}{sid or 'a'}"[:12]
        tobj = dds[:-4] + ".tobj"
        mat_path = f"{base}/{look}_paint.mat"
        if esc and esc["mode"] == "mult":
            # SCS's own body texture (brightened, so the livery keeps its colours) x the livery
            txt = _TEX_MULT.sub(lambda m: m.group(0).replace(m.group(1), tobj), paint_txt, count=1)
            if atlas is not None:
                bt = _escort_base(files, base, atlas)
                txt = _TEX_BASE.sub(lambda m: m.group(0).replace(m.group(1), bt), txt, count=1)
        elif esc:
            txt = _TEX_BASE.sub(lambda m: m.group(0).replace(m.group(1), tobj), paint_txt, count=1)
        elif is_truckpaint(paint_txt):
            lay, _m = layout(_Reader(read), info)
            if lay is not None and getattr(lay, "uvset", 1) == 0:
                txt = _truck_mat_base(tobj)        # skin laid out on uv0: plain base texture
            else:
                txt = _truck_mat(paint_txt, tobj, _truck_base(files, base))
        else:
            txt = _TEX_BASE.sub(lambda m: m.group(0).replace(m.group(1), tobj), paint_txt, count=1)
        files[mat_path] = txt.encode("utf-8")
        mats_for_skin[look] = mat_path
        looks.append(look)
    say(f"Copying {info['name']} model ({len(info['lods']) + 1} LODs)…")
    new_paths = {}
    for p in [info["model"]] + list(info["lods"]):
        pmd = read(p)
        if not pmd:
            continue
        stem = p.rsplit("/", 1)[-1][:-4]
        look0 = read_pmd(pmd)["mats"][0]
        nl = [(lk, [mats_for_skin[lk] if m == paint_mp else m for m in look0]) for lk in looks]
        files[f"{base}/{stem}.pmd"] = add_looks(pmd, nl)
        for ext in (".pmg", ".pma", ".ppd"):
            d = read(p[:-4] + ext)
            if d and ext == ".pmg" and esc:    # escorts: the body's livery layout (unwrap.py)
                plan, _f, _g = escort_plan(_Reader(read), p, paint_mp, esc["mode"], esc["uv"],
                                           frame=esc["frame"], atlas=atlas)
                if plan is not None:
                    from .unwrap import write_uvs
                    d = write_uvs(d, plan.uv, esc["uv"])
            if d:
                files[f"{base}/{stem}{ext}"] = d
        new_paths[p] = f"{base}/{stem}.pmd"
    col = info.get("collision")
    if col and read(col):
        files[f"{base}/{col.rsplit('/', 1)[-1]}"] = read(col)
        col = f"{base}/{col.rsplit('/', 1)[-1]}"
    # defs: one chassis + one traffic_vehicle per skin, SCS's own unit as the pattern
    u = info["unit"]
    copy_keys = ("max_speed", "sound_move", "sound_horn", "allow_parked", "vehicle_mass", "tags",
                 "trailer_chains", "engine_power", "lod_dist",
                 "lights", "horn_sound", "bus_stop", "vehicle_type")
    out = []
    counter = counter if counter is not None else [0]
    for n, look in enumerate(looks):
        tok = f"sf{code}{_b36(counter[0])}"     # unique over every AI vehicle in the mod
        counter[0] += 1
        if len(tok) > 12:
            raise ModelError("too many AI skins for this unit code")
        ch_path = f"/def/vehicle/ai/sf_{code}/{vid}_{n}.sii"
        ch = info["chassis"]
        attrs = [("model", f'"{new_paths.get(info["model"], info["model"])}"')]
        attrs += [("lods[]", f'"{new_paths[l]}"') for l in info["lods"] if l in new_paths]   # a LOD without our look is left out
        if col:
            attrs.append(("collision", f'"{col}"'))
        for k in ("shadow_texture", "shadow_intensity"):
            if ch.get(k) is not None:
                attrs.append((k, ch.get(k)))
        attrs += [("variant", info["variant"]), ("look", look)]
        files[ch_path] = ("SiiNunit\n{\n" + _unit_text("accessory_chassis_data", f".chassis.{tok}", attrs)
                          + "\n}\n").encode("utf-8")
        if esc and n == 0 and replace_scs:
            # SCS spawns its escorts by their own unit names too (front / back escort, the ones
            # parked along special transport routes): the first skin also replaces SCS's own
            # escort chassis def, so every SCS escort of this model wears it
            for a in info["accs"]:
                dp = a.get_str("data_path")
                if a.cls == "vehicle_accessory" and dp and "/def/vehicle/ai/" in dp and dp not in files:
                    txt = _scs_chassis_override(read, dp, info, new_paths, col, look)
                    if txt:
                        files[dp] = txt.encode("utf-8")
        acc_units, acc_names = [], []
        for i, a in enumerate(info["accs"]):
            an = f".{tok}.a{i}"
            acc_names.append(an)
            dp = a.get_str("data_path")
            is_chassis = a.cls == "vehicle_accessory" and dp and "/def/vehicle/ai/" in dp
            aa = [(k, v) for k, v in a.attrs.items() if not isinstance(v, list)]
            aa = [(k, (f'"{ch_path}"' if (is_chassis and k == "data_path") else v)) for k, v in aa]
            acc_units.append(_unit_text(a.cls, an, aa))
        attrs = [("accessories[]", x) for x in acc_names]
        own = (chains or {}).get(skins[n][0]) if not esc else None
        for k in copy_keys:
            v = u.attrs.get(k)
            if v is None or (own and k == "trailer_chains"):
                continue
            attrs += [(k + "[]", x) for x in v] if isinstance(v, list) else [(k, v)]
        if own:   # fleet: this skin's AI trailers only (one is picked per truck)
            attrs += [("trailer_chains[]", f'"{t}"') for t in own]
        # escorts: 1 = as likely as any other escort (SCS keeps pickups out of the front role with
        # 0.001 - yours should show up, like MS2's escorts)
        # fleet trucks: weight 1 x factor, like Jazzycat's painted trucks (no spawn_ratio = 1) - SCS's own
        # 0.2 / 0.5 drowned them among the hundreds of ratio-1 trucks traffic packs add
        ratio = "1" if info.get("escorts") else (_ratio(None, spawn) if own else _ratio(u, spawn))
        attrs += [("spawn_ratio", ratio), ("color[]", "(1, 1, 1)")]
        body = _unit_text("traffic_vehicle", f"traffic.{tok}", attrs)
        inc = [i for i in info.get("includes", []) if "driver" in i]
        if not inc and not info.get("escorts"):
            inc = [DRIVERS_SUI]   # SCS's generic drivers, included inside the unit like Jazzycat does
        if inc:   # drivers: SCS includes them inside the unit
            body = body[:-1] + "".join(f'@include "{i}"\n' for i in inc) + "}"
        out.append(body + "\n\n" + "\n\n".join(acc_units))
    return info["storage"], "\n\n".join(out)


def _ratio(unit, factor):
    """spawn_ratio of our copy: SCS's own unit's ratio (1 when it has none) x the project's
    "how often" factor - so "like SCS's own" really is as often as that SCS truck / trailer."""
    try:
        base = 1.0 if unit is None else float(str(unit.attrs.get("spawn_ratio", "1")).strip('"'))
    except (TypeError, ValueError):
        base = 1.0
    if base <= 0.01:      # SCS parks some units at ~0 (on-demand only): use a normal share
        base = 0.5
    return f"{max(0.001, min(50.0, base * factor)):.4g}"


def _scs_chassis_override(read, dp, info, new_paths, col, look):
    """SCS's own escort chassis def (same file, same unit name), pointed at our copied model with
    our look. None when it isn't the chassis of this model."""
    d = read(dp)
    if not d or not sii.is_text_sii(d):
        return None
    txt = sii.decode(d)
    m = re.search(r'model\s*:\s*"([^"]+)"', txt)
    if not m or m.group(1) != info["model"]:
        return None

    def path(mm):
        p = mm.group(2)
        return mm.group(1) + '"' + new_paths.get(p, p) + '"'
    # LODs we didn't copy have no look of ours: drop them (the next LOD up stays visible)
    txt = re.sub(r'^[ \t]*lods\[\]\s*:\s*"([^"]+)"[^\n]*\n', lambda mm: mm.group(0) if mm.group(1) in new_paths else "", txt, flags=re.M)
    txt = re.sub(r'((?:model|lods\[\])\s*:\s*)"([^"]+)"', path, txt)
    if col:
        txt = re.sub(r'(collision\s*:\s*)"[^"]+"', lambda mm: mm.group(1) + '"' + col + '"', txt)
    if re.search(r"^\s*look\s*:", txt, re.M):
        txt = re.sub(r"^(\s*look\s*:\s*)\S+", lambda mm: mm.group(1) + look, txt, count=1, flags=re.M)
    else:
        txt = re.sub(r"(accessory_chassis_data\s*:\s*\S+\s*\{)", lambda mm: mm.group(1) + f"\n\tlook: {look}", txt, count=1)
    return txt


def _truck_base(files, base):
    """MS2's flat base texture for AI truck bodies, once per vehicle folder."""
    dds = f"{base}/sf_base.dds"
    if dds not in files:
        from PIL import Image
        from . import texture as T
        files[dds] = T.encode_dds_dxt5(Image.new("RGBA", (16, 16), AI_TRUCK_BASE + (255,)))
        files[dds[:-4] + ".tobj"] = T.make_tobj(dds)
    return dds[:-4] + ".tobj"


def _truck_mat_base(skin_tobj):
    return "\n".join([
        'effect : "eut2.dif.spec.add.env.rfx" {',
        "\tadditional_ambient : 0.000000",
        "\tdiffuse : { 1.000000 , 1.000000 , 1.000000 }",
        "\tenv_factor : { 0.073239 , 0.073239 , 0.073239 }",
        "\treflection : 0.000000",
        "\tshininess : 250.000000",
        "\tspecular : { 1.194180 , 1.194180 , 1.194180 }",
        '\ttexture : "texture_base" {', f'\t\tsource : "{skin_tobj}"', "\t}",
        '\ttexture : "texture_reflection" {', '\t\tsource : "/material/environment/vehicle_reflection.tobj"',
        "\t\tu_address : clamp", "\t\tv_address : clamp", "\t\tw_address : clamp", "\t\taniso : disabled", "\t}",
        "}"]) + "\n"


def _truck_mat(orig, skin_tobj, base_tobj):
    """AI truck body: truckpaint -> dif.spec.mult (base x skin on the paint UV set), as MS2's
    TrafficTruck does. The normal map of SCS's own material is kept."""
    nmap = re.search(r'texture\s*:\s*"texture_nmap"\s*\{[^}]*?source\s*:\s*"([^"]+)"', orig, re.S)
    eff = "eut2.dif.spec.mult.dif.spec.add.env" + (".tsnmapuv" if nmap else "") + ".rfx"
    out = [f'effect : "{eff}" {{',
           "\tadditional_ambient : 0.000000",
           "\tdiffuse : { 1.000000 , 1.000000 , 1.000000 }",
           "\tenv_factor : { 0.073239 , 0.073239 , 0.073239 }",
           "\tfresnel : { 0.200000 , 0.900000 }",
           "\treflection : 0.000000",
           "\tshininess : 250.000000",
           "\tspecular : { 1.194180 , 1.194180 , 1.194180 }",
           '\ttexture : "texture_base" {', f'\t\tsource : "{base_tobj}"', "\t}",
           '\ttexture : "texture_mult" {', f'\t\tsource : "{skin_tobj}"', "\t}",
           '\ttexture : "texture_reflection" {', '\t\tsource : "/material/environment/vehicle_reflection.tobj"',
           "\t\tu_address : clamp", "\t\tv_address : clamp", "\t\tw_address : clamp", "\t\taniso : disabled", "\t}"]
    if nmap:
        out += ['\ttexture : "texture_nmap" {', f'\t\tsource : "{nmap.group(1)}"', "\t}"]
    out.append("}")
    return "\n".join(out) + "\n"


def _escort_base(files, base, atlas):
    """SCS's escort body texture, brightened so its light body reads ~white: the livery (texture_mult)
    then keeps its own colours, while dark trim / interior stay dark."""
    dds = f"{base}/sf_body.dds"
    if dds not in files:
        import numpy as np
        from PIL import Image
        from . import texture as T
        a = np.asarray(atlas.convert("RGBA"), float)
        lum = a[..., :3].mean(-1)
        light = lum[lum > 60]
        ref = np.percentile(light, 95) if light.size else 255.0
        f = min(255.0 / max(ref, 1.0), 1.6)
        a[..., :3] = np.clip(a[..., :3] * f, 0, 255)
        files[dds] = T.encode_dds_dxt5(Image.fromarray(a.astype(np.uint8), "RGBA"))
        files[dds[:-4] + ".tobj"] = T.make_tobj(dds)
    return dds[:-4] + ".tobj"


class _Reader:
    def __init__(self, read):
        self.read = read


# ================================================================== AI trailers
# Traffic trailers (the ones AI semis pull) use ordinary paint jobs: SCS gives each a
# vehicle_paint_job_accessory with a company livery (airbrush + paint_job_mask on one texture,
# e.g. /vehicle/trailer/company/apex.tobj). An AI trailer skin = one more traffic_trailer unit
# with SCS's own chassis + our paint job - nothing of SCS's is copied or changed (MS2's
# TrafficTrailer does the same).
TRAILER_STORAGES = ("trailer_semi",)
_SKIP_TRAILER = re.compile(r"(parked|event|academy|depot|_slave)")


def list_ai_trailers(game) -> dict:
    try:
        vdir = [n for n in game.listdir("/def/vehicle") if not n.endswith("/")]
    except Exception:
        vdir = []
    out = {}
    for kind in TRAILER_STORAGES:
        files = [f"/def/vehicle/traffic_storage_{kind}.sii"] + [
            f"/def/vehicle/{n}" for n in sorted(vdir)
            if n.startswith(f"traffic_storage_{kind}.") and n.endswith(".sii") and ".sf_" not in n]
        st = "\n".join(t for t in (_read_text(game, p) for p in files) if t)
        for inc in _includes(st):
            path = "/def/vehicle/" + inc.lstrip("/") if not inc.startswith("/") else inc
            if _SKIP_TRAILER.search(path.rsplit("/", 1)[-1]):
                continue
            text = _read_text(game, path)
            if not text:
                continue
            units = sii.parse(text)
            by_name = {u.name: u for u in units}
            for u in units:
                if u.cls != "traffic_trailer":
                    continue
                accs = [by_name.get(a) for a in u.get_list("accessories")]
                accs = [a for a in accs if a is not None]
                if not any(a.cls == "vehicle_paint_job_accessory" for a in accs):
                    continue                       # no paint job on this one: can't be skinned
                ch = next((a for a in accs if a.cls == "vehicle_accessory"
                           and "/def/vehicle/trailer/" in (a.get_str("data_path") or "")), None)
                if ch is None:
                    continue
                cpath = ch.get_str("data_path")
                d = game.read(cpath)
                cu = next((x for x in (sii.parse_file(d) if d else []) if x.cls == "accessory_chassis_data"), None)
                if cu is None:
                    continue
                model = cu.get_str("detail_model") or cu.get_str("model")
                if not model:
                    continue
                parts = cpath.strip("/").split("/")
                tid = "ait." + re.sub(r"[^a-z0-9_]", "_", f"{parts[-2]}_{parts[-1][:-4]}")
                if tid in out:
                    continue
                out[tid] = {"id": tid, "name": _trailer_name(parts[-2], parts[-1][:-4]), "model": model,
                            "chassis_path": cpath, "unit": u, "accs": accs, "storage": kind}
    return out


def _trailer_name(folder, stem):
    f = re.sub(r"^scs_", "", folder).replace("_", " ").title()
    s = stem.replace("_", " ")
    return f"AI trailer · {f} ({s})"


def scan_ai_trailer(game, tid, res, info=None):
    from .catalog import Slot, Trailer
    from .scsgame import (Group, _register, load_layout, layout_scale, native_size, output_size,
                          render_template, template_path)
    info = info or list_ai_trailers(game).get(tid)
    t = Trailer(tid, info["name"] if info else tid, [])
    t.vkind = "scs_ai_trailer"
    if not info:
        t.error = "Not found in the game's traffic files"
        return t
    lay = load_layout(game, info["model"])
    if lay is None:
        t.error = "No paintable parts on this trailer model"
        return t
    messy, lay = _quality(lay)
    g = Group("ai" if messy else "body", [], [lay])   # trailers: clean company layout, labels help
    g._stacked = False
    g.scale = layout_scale([lay])
    g.native = native_size(g.scale)
    size = output_size(g.native, res)
    path = template_path(tid, "body", size)
    _register(game, path, (lambda sz, g=g: render_template(g, sz)), size)
    s = Slot(key=f"{tid}/body", trailer=tid, token="body", label="Whole trailer (one sheet)",
             suitable_for=[], targets=[(info["chassis_path"], info["name"])], template_dds=path, kind="ai_trailer")
    s.tex_size, s.native, s.vroot = size, g.native, "ai"
    s.ai = info
    s.ai_read = game.read
    if messy:
        _mark_messy(t, s)
    t.slots = [s]
    return t


def scan_trailers(src, res, loaded=(), progress=None):
    game = getattr(src, "game", None)
    if game is None:
        return []
    from .catalog import Trailer
    try:
        found = list_ai_trailers(game)
    except Exception:
        import traceback
        traceback.print_exc()
        return []
    out = []
    for tid in sorted(found, key=lambda a: found[a]["name"].lower()):
        info = found[tid]
        if tid in loaded:
            try:
                t = scan_ai_trailer(game, tid, res, info)
            except Exception:
                import traceback
                traceback.print_exc()
                t = Trailer(tid, info["name"], [])
                t.vkind, t.error = "scs_ai_trailer", "Could not read this trailer model"
        else:
            t = Trailer(tid, info["name"], [])
            t.vkind = "scs_ai_trailer"
        t.lazy = tid not in loaded
        out.append(t)
    return out


def build_trailer_files(slot, skins, code, files, counter, spawn=1.0, chassis=None):
    """skins = [(skin id, skin name, dds game path)]. Paint job + traffic_trailer per skin.
    Returns (storage kind, sui text)."""
    info = slot.ai
    folder = info["chassis_path"].rsplit("/", 1)[0]
    out = []
    for sid, _name, dds in skins:
        tok = f"sf{code}{_b36(counter[0])}"
        counter[0] += 1
        if len(tok) > 12:
            raise ModelError("too many AI skins for this unit code")
        pj = f"{folder}/sf_{code}/{tok}.sii"
        files[pj] = ("SiiNunit\n{\naccessory_paint_job_data : .paint_job\n{\n\tairbrush: true\n"
                     f'\tpaint_job_mask: "{dds[:-4]}.tobj"\n}}\n}}\n').encode("utf-8")
        acc_units, acc_names = [], []
        for i, a in enumerate(info["accs"]):
            an = f".{tok}.a{i}"
            acc_names.append(an)
            aa = [(k, v) for k, v in a.attrs.items() if not isinstance(v, list)]
            if a.cls == "vehicle_paint_job_accessory":
                aa = [(k, f'"{pj}"' if k == "data_path" else v) for k, v in aa]
            elif chassis and a.cls == "vehicle_accessory" and (a.get_str("data_path") or "") == info["chassis_path"]:
                aa = [(k, f'"{chassis}"' if k == "data_path" else v) for k, v in aa]   # re-mapped models
            acc_units.append(_unit_text(a.cls, an, aa))
        u = info["unit"]
        attrs = [("accessories[]", x) for x in acc_names]
        for k in ("sound_move", "cargo_mass", "tags", "trailer_type", "mass"):
            v = u.attrs.get(k)
            if v is None:
                continue
            attrs += [(k + "[]", x) for x in v] if isinstance(v, list) else [(k, v)]
        attrs.append(("spawn_ratio", _ratio(u, spawn)))
        out.append(_unit_text("traffic_trailer", f"traffic.trailer.{tok}", attrs) + "\n\n" + "\n\n".join(acc_units))
    return info["storage"], "\n\n".join(out)



# ================================================================== player cab -> AI truck
def _ai_slot(game, info):
    """A minimal slot object for build_files (no template needed)."""
    from .catalog import Slot
    s = Slot(key=f"{info['id']}/body", trailer=info["id"], token="body", label="AI", suitable_for=[],
             targets=[], template_dds=None, kind="ai")
    s.ai, s.ai_read = info, game.read
    return s


def ai_for_truck(game, tid):
    """SCS's AI versions of a player truck (chassis defs under /def/vehicle/ai/truck/<tid>/)."""
    out = []
    for info in list_ai(game).values():
        dp = next((a.get_str("data_path") for a in info["accs"] if a.cls == "vehicle_accessory"), "") or ""
        if f"/def/vehicle/ai/truck/{tid}/" in dp:
            out.append(info)
    return out


_PAIR_CACHE = {}


def pair_cabs(game, tid, cabs, all_cabs=None):
    """[(AI slot, player cab slot)]: each AI model of the truck with the cab it matches in 3D.
    all_cabs = every cab of the truck: each AI model is matched against all of them and kept only
    when its best match is one of `cabs` (painted, AI ticked) - otherwise SCS's AI day cab would
    match a painted sleeper well enough (shared front) and drive around in that livery."""
    from . import transfer as TR
    infos = ai_for_truck(game, tid)
    mine = {id(c) for c in cabs}
    pool = list(cabs) + [c for c in (all_cabs or []) if id(c) not in mine and getattr(c, "layouts", None)]
    out = []
    for info in infos:
        lay, _mp = layout(game, info)
        if lay is None:
            continue
        A = TR.Surface(lay.tris)
        best = None
        for cab in pool:
            ck = (id(game), info["id"], cab.key)
            if ck not in _PAIR_CACHE:
                P = TR.Surface([t for L in getattr(cab, "layouts", []) for t in L.tris])
                V = TR.Views(P)
                off, sc = TR.align(A, V)
                _PAIR_CACHE[ck] = (off, sc, P, V)
            off, sc, P, V = _PAIR_CACHE[ck]
            if best is None or sc > best[1]:
                best = (cab, sc)
        if best and best[1] >= 0.3 and id(best[0]) in mine:
            s = _ai_slot(game, info)
            s.pair = (best[0], _PAIR_CACHE[(id(game), info["id"], best[0].key)], A, lay)
            out.append((s, best[0]))
    return out


def bake_from_cab(aslot, cab, art, size, fill):
    from . import transfer as TR
    _cab, (off, _sc, P, V), A, lay = aslot.pair
    im, _cov = TR.bake(art, P, A, (size, size), off, V, fill=fill)
    return im



# ================================================================== owned trailer -> traffic trailer
def traffic_for_trailer(game, tid):
    """SCS traffic trailers of an ownable SCS trailer type: scs.box -> /def/vehicle/trailer/scs_box/."""
    folder = tid.replace(".", "_")
    cache = getattr(game, "_sf_ai_trailers", None)
    if cache is None:
        cache = list_ai_trailers(game)
        try:
            game._sf_ai_trailers = cache      # read the traffic storages once per load
        except Exception:
            pass
    return [i for i in cache.values() if i["chassis_path"].split("/")[-2] == folder]


def pair_trailer(game, tid, slots):
    """[(AI trailer slot, info)] for every traffic trailer of this type that lines up with the
    ownable trailer's parts in 3D. Each part keeps its own sheet (index = position in slots)."""
    import numpy as np
    from . import transfer as TR
    from .scsgame import load_layout
    out = []
    parts = [(i, L) for i, sl in enumerate(slots) for L in getattr(sl, "layouts", [])]
    if not parts:
        return out
    for info in traffic_for_trailer(game, tid):
        lay = load_layout(game, info["model"])
        if lay is None:
            continue
        A = TR.Surface(lay.tris)
        # each part on its own: which parts belong to this traffic trailer (53' vs 48' body) and
        # where (front walls / rear doors are modelled in their own accessory space)
        found = {}
        for i, sl in enumerate(slots):
            tris = [t for L in getattr(sl, "layouts", []) for t in L.tris]
            if not tris:
                continue
            P1 = TR.Surface(tris)
            if not len(P1.pos):
                continue
            ext = P1.pos.reshape(-1, 3).max(0) - P1.pos.reshape(-1, 3).min(0)
            off_i, sc_i = TR.align(A, TR.Views(P1, px=0.01), samples=1500, small_part=max(ext) < 4)
            if sc_i >= 0.015:
                found[i] = (off_i, sc_i)
        if not found:
            continue
        tris, sheet, prio = [], [], []
        for i, (off_i, sc_i) in found.items():
            for L in slots[i].layouts:
                for (uv3, p3, n3) in L.tris:   # into the traffic trailer's space
                    tris.append((uv3, tuple(tuple(c - o for c, o in zip(pp, off_i)) for pp in p3), n3))
                    sheet.append(i)
                    prio.append(sc_i)
        P = TR.Surface(tris, sheet, prio)
        V = TR.Views(P)
        off = np.zeros(3)
        sc = float(np.isfinite(V.lookup(*_sample(A), tol=0.1)[:, 0]).mean())
        if sc < 0.3:
            continue
        from .scsgame import layout_scale, native_size
        s = _ai_slot(game, info)
        s.kind = "ai_trailer"
        s.tex_size = native_size(layout_scale([lay]))
        s.pair = (None, (off, sc, P, V), A, lay)
        out.append(s)
    return out


def _sample(A, n=1500):
    import numpy as np
    rng = np.random.default_rng(3)
    t = rng.integers(0, len(A.pos), n)
    return A.pos[t].mean(1), A.n[t]


def bake_trailer(aslot, arts, size, fill):
    from . import transfer as TR
    _c, (off, _sc, P, V), A, _lay = aslot.pair
    im, _cov = TR.bake(arts, P, A, size, off, V, fill=fill)
    return im


# ================================================================== escort vehicles (Special Transport)
# Like MS2's AiEscortSkin: an escort vehicle with your livery joins the escort pool (front / back
# of oversize loads). SCS's own escort models (ATS: Dodge Ram 2500, Ford F-150, Featan pickup;
# ETS2: per the game files) are read from the game; the template is made from the model itself
# (MS2-style side / roof / front / rear views) and baked onto its texture layout at build time.
ESCORT_STORAGES = ("escort_front", "escort_back")


def list_escorts(game) -> dict:
    """id -> info (+ "escorts": [(storage, unit, accs)]) for every escort model in the game files."""
    try:
        vdir = [n for n in game.listdir("/def/vehicle") if not n.endswith("/")]
        aidir = [n for n in game.listdir("/def/vehicle/ai") if not n.endswith("/")]
    except Exception:
        return {}
    files = []
    for kind in ESCORT_STORAGES:
        for p in [f"/def/vehicle/traffic_storage_{kind}.sii"] + [
                f"/def/vehicle/{n}" for n in sorted(vdir)
                if n.startswith(f"traffic_storage_{kind}.") and n.endswith(".sii") and ".sf_" not in n]:
            for inc in _includes(_read_text(game, p) or ""):
                files.append((kind, "/def/vehicle/" + inc.lstrip("/")))
    # SCS ships more escort .sui files than its storages include (Ram, F-150): take those too
    for n in sorted(aidir):
        if "escort" in n and n.endswith(".sui"):
            files.append(("escort_back" if "back" in n else "escort_front", "/def/vehicle/ai/" + n))
    out = {}
    for kind, path in dict.fromkeys(files):
        text = _read_text(game, path)
        if not text:
            continue
        units = sii.parse(text)
        by_name = {u.name: u for u in units}
        for u in units:
            if u.cls != "traffic_vehicle" or "escort" not in [x.strip('"') for x in u.get_list("tags")]:
                continue
            accs = [a for a in (by_name.get(x) for x in u.get_list("accessories")) if a is not None]
            ch = next((a for a in accs if a.cls == "vehicle_accessory"
                       and "/def/vehicle/ai/" in (a.get_str("data_path") or "")), None)
            if ch is None:
                continue
            cd = game.read(ch.get_str("data_path"))
            cu = next((x for x in (sii.parse_file(cd) if cd else []) if x.cls == "accessory_chassis_data"), None)
            if cu is None or not cu.get_str("model"):
                continue
            model = cu.get_str("model")
            eid = "esc." + _ai_id(model)[3:] + "_" + re.sub(r"[^a-z0-9_]", "_", (cu.get_str("variant") or "default").lower())
            info = out.get(eid)
            if info is None:
                info = out[eid] = {"id": eid, "name": "Escort · " + _ai_name(model)[5:], "model": model,
                                   "lods": cu.get_list("lods"), "collision": cu.get_str("collision"),
                                   "variant": cu.get_str("variant") or "default", "look": "default",
                                   "chassis": cu, "unit": u, "accs": accs, "storage": kind,
                                   "includes": _includes(text), "sui": path, "escorts": []}
            if kind not in [k for k, *_r in info["escorts"]]:
                info["escorts"].append((kind, u, accs))
    return out


_TEX_MULT = re.compile(r'texture\s*:\s*"texture_mult"\s*\{[^}]*?source\s*:\s*"([^"]+)"', re.S)


def escort_material(game, model):
    """The escort body material and how its livery is mapped:
    (index, mat path, mat text, mode, uv set) - mode "paint": one texture (texture_base) on uv 0
    (Dodge Ram); mode "mult": SCS's own texture on uv 0 times the escort livery (texture_mult,
    escort_paint.dds) on uv 1 (F-150, Featan follow car)."""
    pm = paint_material(game, model)
    if pm is not None and not is_truckpaint(pm[2]):
        return pm[0], pm[1], pm[2], "paint", 0
    pmd = game.read(model)
    if not pmd:
        return None
    for i, mp in enumerate(read_pmd_materials(pmd, 0)):
        txt = _read_text(game, mp) or ""
        m = _TEX_MULT.search(txt)
        if m and re.search(r"escort|paint", m.group(1)):
            return i, mp, txt, "mult", 1
    return None


def _tex_image(game, tobj):
    """A texture (by its .tobj) as a PIL image, or None."""
    from .texture import load_image
    d = game.read(tobj) if tobj else None
    m = re.search(rb"(/[\w/.\-]+\.dds)", d or b"")
    dds = game.read(m.group(1).decode()) if m else game.read(tobj[:-5] + ".dds") if tobj else None
    try:
        return load_image(dds) if dds else None
    except Exception:
        return None


def escort_plan(game, model, mat_path, mode, k, frame=None, atlas=None):
    """New livery UVs for one LOD of an escort model (None: that LOD has no such material)."""
    from . import unwrap as UW
    import numpy as np
    pmd, pmg = game.read(model), game.read(model[:-4] + ".pmg")
    if not pmd or not pmg:
        return None, None, None
    mats = read_pmd_materials(pmd, 0)
    if mat_path not in mats:
        return None, None, None
    mi = mats.index(mat_path)
    pieces = read_pmg(pmg)
    ids = [i for i, pc in enumerate(pieces) if pc.material == mi and len(pc.uvs) > k and pc.pos]
    if not ids:
        return None, None, None
    if frame is None:
        frame = UW.Frame(np.array([p for i in ids for p in pieces[i].pos], float))
    dark = UW.atlas_dark(atlas) if atlas is not None else None
    return UW.layout(pieces, ids, k, frame, dark=dark), frame, pmg


def render_escort_template(plan, frame, size):
    """Pauly-style sheet: the body's faces in white, SCS's mesh lines, one label per view."""
    from PIL import Image, ImageDraw
    from . import unwrap as UW
    from .scsgame import _font
    W, H = size
    ss = 2 if W <= 4096 else 1                  # draw at 2x, then shrink: smooth lines
    im = Image.new("RGB", (W * ss, H * ss), (43, 43, 43))
    d = ImageDraw.Draw(im)
    for t, v in plan.tris:
        d.polygon([(x * W * ss, y * H * ss) for x, y in t], fill=(236, 236, 236) if v != "hidden" else (150, 150, 150))
    lw = max(1, ss * W // 2048)
    for t, _v in plan.tris:
        pts = [(x * W * ss, y * H * ss) for x, y in t]
        d.line(pts + [pts[0]], fill=(120, 120, 120), width=lw)
    f = _font(max(10, int(H * ss * 0.022)))
    for v in list(UW.VIEWS) + ["hidden"]:
        if v not in plan.used:
            continue
        x, y, w, h = frame.rect[v]
        tx, ty = x / UW.SHEET[0] * W * ss, y / UW.SHEET[1] * H * ss
        d.text((tx + 6 * ss, ty + 4 * ss), UW.LABELS[v], fill=(150, 150, 150) if v != "hidden" else (110, 110, 110), font=f)
    return im.resize((W, H), Image.Resampling.LANCZOS) if ss != 1 else im


def scan_escort(game, eid, res, info=None):
    """Escort vehicles: SCS maps one side onto both sides (or stretches a stripe texture over the
    body), so the body is laid out again like escort skin authors do (unwrap.py) - the template
    is the new layout: LEFT SIDE / RIGHT SIDE / ROOF / FRONT / REAR, painted 1:1."""
    from .catalog import Slot, Trailer
    from .scsgame import _register, template_path
    info = info or list_escorts(game).get(eid)
    t = Trailer(eid, info["name"] if info else eid, [])
    t.vkind = "scs_escort"
    if not info:
        t.error = "Not found in the game's escort files"
        return t
    em = escort_material(game, info["model"])
    if em is None:
        t.error = "No paintable body material on this escort model"
        return t
    mi, mp, txt, mode, k = em
    tb = _TEX_BASE.search(txt)
    atlas = _tex_image(game, tb.group(1)) if tb else None
    plan, frame, _pmg = escort_plan(game, info["model"], mp, mode, k, atlas=atlas)
    if plan is None or not plan.tris:
        t.error = "No paintable body material on this escort model"
        return t
    size = (res, res // 2)                                   # 2:1 like MS2's / Pauly's escort sheets

    def render(sz, plan=plan, frame=frame):
        return render_escort_template(plan, frame, sz)
    path = template_path(eid, "body2", size)
    _register(game, path, render, size)
    s = Slot(key=f"{eid}/body", trailer=eid, token="body", label="Whole vehicle (left / right / roof / front / rear)",
             suitable_for=[], targets=[(info["model"], info["name"])], template_dds=path, kind="ai_escort")
    s.tex_size, s.native, s.vroot = size, size, "ai"
    s.ai, s.ai_read = info, game.read
    s.escort = {"mat": mp, "text": txt, "mode": mode, "uv": k, "frame": frame,
                "base": tb.group(1) if tb else None}
    t.slots = [s]
    return t


def scan_escorts(src, res, loaded=(), progress=None):
    game = getattr(src, "game", None)
    if game is None:
        return []
    from .catalog import Trailer
    try:
        found = list_escorts(game)
    except Exception:
        import traceback
        traceback.print_exc()
        return []
    out = []
    for eid in sorted(found, key=lambda a: found[a]["name"].lower()):
        info = found[eid]
        if eid in loaded:
            try:
                t = scan_escort(game, eid, res, info)
            except Exception:
                import traceback
                traceback.print_exc()
                t = Trailer(eid, info["name"], [])
                t.vkind, t.error = "scs_escort", "Could not read this escort model"
        else:
            t = Trailer(eid, info["name"], [])
            t.vkind = "scs_escort"
        t.lazy = eid not in loaded
        out.append(t)
    return out


# ================================================================== AI traffic sheet (company livery layout)
# SCS's traffic trailers of one type (e.g. /def/vehicle/trailer/scs_box/: 28', 45', 53') all take ONE
# company texture (/vehicle/trailer/company/<company>.dds, 2:1) on the paint UV set (uv1): the top half
# is the side - both sides share it, the band is centred and longer trailers run past the sheet edges -
# and the bottom row holds the front wall / rear doors. SneaK's Voltison trick (his master def replaces
# voltison.dds) works the same way. So an AI livery is painted on that sheet directly and every traffic
# length of the type uses it - no 3D transfer from the ownable trailer.
SHEET_TOKEN = "ai_sheet"


REMAP_TYPES = {"scs_box"}   # traffic trailer types whose models SkinForge re-maps (checked face by face)


def _remaps(info):
    return info["chassis_path"].rstrip("/").split("/")[-2] in REMAP_TYPES


def _folder(info):
    return info["chassis_path"].rstrip("/").split("/")[-2]


def type_ends(game, info):
    """FRONT / REAR panel rects of a non-box traffic trailer type (cached on the game object)."""
    from .pmg import mat_effect, is_paintable
    memo = getattr(game, "_sf_ends", None)
    if memo is None:
        memo = {}
        try:
            game._sf_ends = memo
        except Exception:
            pass
    key = _folder(info)
    if key not in memo:
        mp = []
        for i in getattr(game, "_sf_ai_trailers", {}).values() or [info]:
            if _folder(i) != key:
                continue
            m = i["model"]
            pmd, pg = game.read(m), game.read(m[:-4] + ".pmg")
            if not pmd or not pg:
                continue
            mats = read_pmd_materials(pmd)
            paint = {k for k, x in enumerate(mats) if is_paintable(mat_effect(game.read(x)))}
            pieces = read_pmg(pg)
            mp.append((pieces, {pi for pi, pc in enumerate(pieces) if pc.material in paint}))
        try:
            memo[key] = plan_ends(mp) if mp else {}
        except Exception:
            import traceback
            traceback.print_exc()
            memo[key] = {}
    return memo[key]


def _traffic_models(game, tid):
    """[(info, [(uv tri, normal)])] paint triangles (uv1) of every SCS traffic trailer of this type."""
    from .pmg import mat_effect, is_paintable
    out = []
    for info in traffic_for_trailer(game, tid):
        model = info["model"]
        try:
            pmd = game.read(model)
            pmg = game.read(model[:-4] + ".pmg")
            if not pmd or not pmg:
                continue
            mats = read_pmd_materials(pmd)
            paint = {i for i, m in enumerate(mats) if is_paintable(mat_effect(game.read(m)))}
            tris = []
            pieces = read_pmg(pmg)
            pids = {pi for pi, pc in enumerate(pieces) if pc.material in paint}
            other = {} if _remaps(info) else remap_model_uv1(pieces, pids, type_ends(game, info))
            for pi, pc in enumerate(pieces):
                if pc.material not in paint or len(pc.uvs) < 2:
                    continue
                # the layout the built models use (box: sides + roof re-mapped; others: ends placed)
                uv = (remap_piece_uv1(pc) if _remaps(info) else other.get(pi)) or pc.uvs[1]
                for a, b, c in pc.tris:
                    tris.append(((tuple(uv[a]), tuple(uv[b]), tuple(uv[c])), _raw_normal(pc, a, b, c)))
            if tris:
                out.append((info, tris, game))
        except Exception:
            import traceback
            traceback.print_exc()
    return out


def length_of(name):
    m = re.search(r"(?:^|_)(\d{2})(?:_|$|')", name)
    return int(m.group(1)) if m else None


def render_sheet_template(models, size):
    """The company layout: faces white with their edges, side extent of each traffic length marked,
    labels SIDE / FRONT / REAR. Long trailers run past the sheet edges (texture repeats there)."""
    from PIL import Image, ImageDraw
    from .scsgame import _font
    W, H = size
    ss = 2 if max(W, H) <= 2048 else 1
    im = Image.new("L", (W * ss, H * ss), 40)
    d = ImageDraw.Draw(im)
    groups = {"SIDE · BOTH SIDES": [], "FRONT WALL": [], "REAR DOORS": [], "ROOF · FRONT ON THE LEFT": []}
    for _info, tris, *_g in models:
        for (uvs, n) in tris:
            d.polygon([(u * W * ss, v * H * ss) for u, v in uvs], fill=255)
    for _info, tris, *_g in models:
        for (uvs, n) in tris:
            d.polygon([(u * W * ss, v * H * ss) for u, v in uvs], outline=170)
            key = ("SIDE · BOTH SIDES" if abs(n[0]) > 0.7 else "ROOF · FRONT ON THE LEFT" if n[1] > 0.7 else
                   "FRONT WALL" if n[2] < -0.7 else "REAR DOORS" if n[2] > 0.7 else None)   # SCS: forward = -z
            if key:
                groups[key] += [uv for uv in uvs]
    if ss != 1:
        im = im.resize((W, H), Image.Resampling.BOX)
    im = im.convert("RGB")
    d = ImageDraw.Draw(im)
    lens = sorted({length_of(info["chassis_path"].rsplit("/", 1)[-1]) or 0 for info, _t, *_g in models} - {0})
    ltxt = " (" + ", ".join(f"{L}'" for L in lens) + ")" if lens else ""
    if any(_remaps(info) for info, _t, *_g in models):
        note = (f"Every traffic length{ltxt} shows this WHOLE band, edge to edge - shorter trailers squeeze it. "
                "Fill the band right down to its bottom edge.")
        ny = int(H * 0.49) - max(12, H // 36) - 4
    else:
        note = f"One sheet for every traffic trailer of this type{ltxt}. Orange boxes: end panels added by SkinForge."
        ny = H - max(11, H // 44) - 10
    nf = _font(max(11, H // 44))
    nb = d.textbbox((8, ny), note, font=nf)
    d.rectangle((nb[0] - 4, nb[1] - 3, nb[2] + 4, nb[3] + 3), fill=(30, 30, 30))   # readable over white faces
    d.text((8, ny), note, fill=(230, 120, 30), font=nf)
    taken = [nb]
    if not any(_remaps(info) for info, _t, *_g in models):
        # tanks, dumpers...: only the panels SkinForge made get labels (their other faces don't split cleanly)
        ends = type_ends(models[0][2], models[0][0]) if models and len(models[0]) > 2 else {}
        for role, (R0, S0, R1, S1) in ends.items():
            name = "FRONT END" if role == "front" else "REAR END"
            fs = max(12, min((S1 - S0) * H * 0.12, (R1 - R0) * W * 0.8 / max(6, len(name) * 0.62)))
            d.rectangle((R0 * W, S0 * H, R1 * W, S1 * H), outline=(230, 120, 30), width=max(1, W // 1000))
            d.text(((R0 + R1) / 2 * W, S0 * H + fs * 0.8), name, fill=(90, 90, 90), font=_font(fs), anchor="mm")
        return im.convert("RGBA")
    # labels only where they help: a compact area (faces fill most of its box) and no collision
    areas = {k: 0.0 for k in groups}
    for _info, tris, *_g in models:
        for (uvs, n) in tris:
            key = ("SIDE · BOTH SIDES" if abs(n[0]) > 0.7 else "ROOF · FRONT ON THE LEFT" if n[1] > 0.7 else
                   "FRONT WALL" if n[2] < -0.7 else "REAR DOORS" if n[2] > 0.7 else None)
            if key:
                (a, b, c) = uvs
                areas[key] += abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
    for name, pts in sorted(groups.items(), key=lambda kv: -areas[kv[0]]):
        pts = [(min(1, max(0, u)), min(1, max(0, v))) for u, v in pts]
        if not pts:
            continue
        u0, u1 = min(u for u, v in pts), max(u for u, v in pts)
        v0, v1 = min(v for u, v in pts), max(v for u, v in pts)
        box = max(1e-6, (u1 - u0) * (v1 - v0))
        if areas[name] / len(models) < 0.3 * box:
            continue                     # scattered over the sheet (tanks, dumpers): a label would mislead
        fs = max(12, min((v1 - v0) * H * 0.12, (u1 - u0) * W * 0.8 / max(6, len(name) * 0.62)))
        f = _font(fs)
        cx, cy = (u0 + u1) / 2 * W, (v0 + v1) / 2 * H
        bb = d.textbbox((cx, cy), name, font=f, anchor="mm")
        if any(bb[0] < t[2] and t[0] < bb[2] and bb[1] < t[3] and t[1] < bb[3] for t in taken):
            continue
        taken.append(bb)
        d.text((cx, cy), name, fill=(90, 90, 90), font=f, anchor="mm")
    return im.convert("RGBA")


def sheet_slot(game, tid, res):
    """The 'AI traffic sheet' slot of an SCS trailer type, or None when the game has no traffic
    version of it (or none with a company paint job)."""
    from .catalog import Slot
    from .scsgame import _register, template_path
    models = _traffic_models(game, tid)
    if not models:
        return None
    w = min(4096, res)
    size = (w, w // 2)
    path = template_path(tid, SHEET_TOKEN, size)
    _register(game, path, (lambda sz, m=models: render_sheet_template(m, sz)), size)
    lens = sorted({length_of(i["chassis_path"].rsplit("/", 1)[-1]) or 0 for i, _t, *_g in models} - {0})
    s = Slot(key=f"{tid}/{SHEET_TOKEN}", trailer=tid, token=SHEET_TOKEN,
             label="AI traffic sheet · all traffic lengths" + (f" ({', '.join(str(x) + chr(39) for x in lens)})" if lens else ""),
             suitable_for=[], targets=[(i["chassis_path"], i["name"]) for i, _t, *_g in models],
             template_dds=path, kind="ai_sheet")
    s.tex_size, s.native, s.vroot = size, size, "trailer_owned"
    s.game = game
    s.traffic = [i for i, _t, *_g in models]
    return s


# ---- traffic trailer models re-mapped so EVERY length shows the whole AI sheet ----
# SCS's company layout runs a 53' side from u -0.24 to 1.24 (texture clamps: the edge pixels smear)
# and a 28' from 0.09 to 0.91. In a copy of each traffic model the side faces of the paint pieces
# (uv1) are stretched linearly to 0..1 - side vertices are never shared with roof / end faces, so
# nothing else moves. LOD3 (baked, far away) stays SCS's.
_MODEL_KEYS = ("detail_model", "model")


ROOF_RECT = (0.505, 0.515, 0.995, 0.985)   # u0, v0, u1, v1: the empty bottom-right of the sheet


def _raw_normal(p, a, b, c):
    pa, pb, pc = p.pos[a], p.pos[b], p.pos[c]
    e1 = [pb[i] - pa[i] for i in range(3)]
    e2 = [pc[i] - pa[i] for i in range(3)]
    n = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
    ln = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
    return (-n[0] / ln, -n[1] / ln, -n[2] / ln)   # SCS winding: flip to point outwards


def remap_piece_uv1(p):
    """New uv1 for one paint piece of a traffic trailer: side faces stretched to the full sheet width
    (every length shows the whole band), the roof laid out flat from above into ROOF_RECT (front on
    the left) instead of SCS's tiny square. Other faces unchanged. None when nothing changes."""
    if len(p.uvs) < 2:
        return None
    side, roof = set(), set()
    for a, b, c in p.tris:
        n = _raw_normal(p, a, b, c)
        if abs(n[0]) > 0.7:
            side.update((a, b, c))
        elif n[1] > 0.7:
            roof.update((a, b, c))
    roof -= side
    if not side and not roof:
        return None
    uv = [list(t) for t in p.uvs[1]]
    if side:
        us = [p.uvs[1][j][0] for j in side]
        u0, u1 = min(us), max(us)
        if u1 - u0 > 1e-4:
            for j in side:
                uv[j][0] = 0.002 + 0.996 * (p.uvs[1][j][0] - u0) / (u1 - u0)
    if roof:
        xs = [p.pos[j][0] for j in roof]
        zs = [p.pos[j][2] for j in roof]
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
        r0, s0, r1, s1 = ROOF_RECT
        if x1 - x0 > 1e-4 and z1 - z0 > 1e-4:
            for j in roof:
                uv[j][0] = r0 + (r1 - r0) * (p.pos[j][2] - z0) / (z1 - z0)   # length: front (-z) left
                uv[j][1] = s0 + (s1 - s0) * (p.pos[j][0] - x0) / (x1 - x0)   # width
    return uv


def _remap_pmg(data):
    """Copy of a traffic trailer .pmg with remap_piece_uv1 applied to its paint pieces (or None)."""
    from .unwrap import write_uvs
    new = {}
    for pi, p in enumerate(read_pmg(data)):
        if pi in _remap_pmg.paint:
            uv = remap_piece_uv1(p)
            if uv is not None:
                new[pi] = uv
    return write_uvs(data, new, 1) if new else None


_remap_pmg.paint = set()


def remapped_chassis(game, info, code, files, cache):
    """Copy of the traffic trailer's chassis def whose models are the re-mapped copies; returns its
    path (or None -> use SCS's own). cache: model path -> new path (shared by all skins / units)."""
    from .pmg import mat_effect, is_paintable
    box = _remaps(info)
    ends = {} if box else type_ends(game, info)
    text = _read_text(game, info["chassis_path"])
    if not text:
        return None
    paths = re.findall(r'(?:detail_model|model|lods\[\])\s*:\s*"([^"]+\.pmd)"', text)
    changed = {}
    for mp in paths:
        if "_lod3" in mp:
            continue                      # baked far-away LOD: keep SCS's
        if mp not in cache:
            cache[mp] = None
            pmd, pmg = game.read(mp), game.read(mp[:-4] + ".pmg")
            if pmd and pmg:
                try:
                    mats = read_pmd_materials(pmd)
                    paint = {i for i, m in enumerate(mats) if is_paintable(mat_effect(game.read(m)))}
                    pieces = read_pmg(pmg)
                    _remap_pmg.paint = {pi for pi, pc in enumerate(pieces) if pc.material in paint}
                    if box:
                        new_pmg = _remap_pmg(pmg)
                    else:                  # tanks, dumpers...: only whole islands move (ends, wide shells)
                        from .unwrap import write_uvs
                        nu = remap_model_uv1(pieces, _remap_pmg.paint, ends)
                        new_pmg = write_uvs(pmg, nu, 1) if nu else None
                    if new_pmg is not None:
                        folder = mp.rsplit("/", 2)[-2]
                        np_ = f"/vehicle/trailer/sf_{code}/traffic/{folder}/{mp.rsplit('/', 1)[-1]}"
                        files[np_] = pmd
                        files[np_[:-4] + ".pmg"] = new_pmg
                        cache[mp] = np_
                except Exception:
                    import traceback
                    traceback.print_exc()
        if cache.get(mp):
            changed[mp] = cache[mp]
    if not changed:
        return None
    for old, new in changed.items():
        text = text.replace(f'"{old}"', f'"{new}"')
    stem = info["chassis_path"].rsplit("/", 1)[-1]
    folder = info["chassis_path"].rsplit("/", 1)[0]
    dp = f"{folder}/sf_{code}/ch_{stem}"
    files[dp] = text.encode("utf-8")
    return dp


# ---- other traffic trailer types (tanks, dumpers...): end caps get a real place on the sheet ----
# SCS collapses big islands - e.g. both gas tank ends (7 m2 each) - onto ONE texel (u 0.01, v 1.0): a
# plain colour. In our model copies those end islands (dominant normal +-z, big in 3D, ~zero in UV) are
# projected flat into the largest free part of the sheet (FRONT / REAR panels, same place for every
# model of the type), and islands wider than the sheet (food tank shell u -0.37..1.34) are compressed
# to 0..1 like the box. Only whole islands move: nothing tears.
def _tri3(p, a, b, c):
    pa, pb, pc = p.pos[a], p.pos[b], p.pos[c]
    e1 = [pb[i] - pa[i] for i in range(3)]
    e2 = [pc[i] - pa[i] for i in range(3)]
    n = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
    ln = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
    return ln / 2, ((-n[0] / ln, -n[1] / ln, -n[2] / ln) if ln else (0, 0, 0))


def _tri2(uv, a, b, c):
    return abs((uv[b][0] - uv[a][0]) * (uv[c][1] - uv[a][1]) - (uv[c][0] - uv[a][0]) * (uv[b][1] - uv[a][1])) / 2


def _analyse(pieces, paint):
    """Islands of the paint pieces: [(piece index, [tri idx], area3, areaUV, normal sum, verts)]."""
    from .unwrap import _islands
    out = []
    for pi, p in enumerate(pieces):
        if pi not in paint or len(p.uvs) < 2 or not p.tris:
            continue
        ti, n = _islands(len(p.pos), p.tris)
        groups = [[] for _ in range(n)]
        for t, i in enumerate(ti):
            groups[i].append(t)
        for tl in groups:
            A3 = A2 = 0.0
            ns = [0.0, 0.0, 0.0]
            for t in tl:
                a3, nn = _tri3(p, *p.tris[t])
                A3 += a3
                A2 += _tri2(p.uvs[1], *p.tris[t])
                for k in range(3):
                    ns[k] += nn[k] * a3
            verts = sorted({j for t in tl for j in p.tris[t]})
            out.append((pi, tl, A3, A2, ns, verts))
    return out


def _end_role(isl, dens):
    """'front' / 'rear' for a big island squeezed to (almost) nothing in UV, else None."""
    _pi, _tl, A3, A2, ns, _v = isl
    if A3 < 0.5 or A2 > 0.02 * dens * A3:
        return None
    ln = (ns[0] ** 2 + ns[1] ** 2 + ns[2] ** 2) ** 0.5 or 1.0
    nz = ns[2] / ln
    return "front" if nz < -0.7 else "rear" if nz > 0.7 else None   # SCS: forward = -z


def _density(isls):
    real = [(A2 / A3, A3) for _pi, _tl, A3, A2, _n, _v in isls if A3 > 0.05 and A2 > 1e-7]
    if not real:
        return 0.0
    real.sort()
    half, acc = sum(w for _d, w in real) / 2, 0.0
    for d, w in real:
        acc += w
        if acc >= half:
            return d
    return real[-1][0]


def _free_rect(occ):
    """Largest empty rectangle (x0, y0, x1, y1) in a boolean grid (rows = v)."""
    import numpy as np
    H, W = occ.shape
    heights = np.zeros(W, int)
    best, area = None, 0
    for y in range(H):
        heights = np.where(occ[y], 0, heights + 1)
        stack = []
        for x in range(W + 1):
            h = heights[x] if x < W else 0
            start = x
            while stack and stack[-1][1] >= h:
                sx, sh = stack.pop()
                if sh * (x - sx) > area:
                    area, best = sh * (x - sx), (sx, y - sh + 1, x, y + 1)
                start = sx
            stack.append((start, h))
    return best


def plan_ends(models_pieces):
    """{"front": rect, "rear": rect} (u0, v0, u1, v1) in the free part of the sheet, shared by every
    model of the type - or {} when no model has squeezed ends. models_pieces: [(pieces, paint)]."""
    from PIL import Image, ImageDraw
    import numpy as np
    GW, GH = 128, 64
    im = Image.new("L", (GW, GH), 0)
    d = ImageDraw.Draw(im)
    roles = set()
    for pieces, paint in models_pieces:
        isls = _analyse(pieces, paint)
        dens = _density(isls)
        for isl in isls:
            r = _end_role(isl, dens)
            if r:
                roles.add(r)
                continue
            pi, tl, _A3, A2, _n, verts = isl
            uv = pieces[pi].uvs[1]
            us = [uv[j][0] for j in verts]
            stretch = (max(us) - min(us)) > 1.02
            for t in tl:
                pts = []
                for j in pieces[pi].tris[t]:
                    u, v = uv[j]
                    if stretch:
                        u = (u - min(us)) / (max(us) - min(us))
                    pts.append((u * GW, v * GH))
                d.polygon(pts, fill=255, outline=255)
    if not roles:
        return {}
    occ = np.array(im) > 0
    occ[:, :1] = occ[:, -1:] = True        # keep off the sheet border
    occ[:1, :] = occ[-1:, :] = True
    occ[int(0.97 * GH):, :int(0.04 * GW) + 1] = True   # SCS's plain-colour texel corner (u 0.01, v 1.0)
    r = _free_rect(occ)
    if r is None:
        return {}
    x0, y0, x1, y1 = r
    m = 1                                           # one cell margin
    x0, y0, x1, y1 = (x0 + m) / GW, (y0 + m) / GH, (x1 - m) / GW, (y1 - m) / GH
    if x1 - x0 < 0.05 or y1 - y0 < 0.05:
        return {}
    order = [x for x in ("front", "rear") if x in roles]
    if len(order) == 1:
        return {order[0]: (x0, y0, x1, y1)}
    if (x1 - x0) * 2 >= (y1 - y0):                  # sheet is 2:1: compare in pixels
        mid = (x0 + x1) / 2
        return {"front": (x0, y0, mid - 0.004, y1), "rear": (mid + 0.004, y0, x1, y1)}
    mid = (y0 + y1) / 2
    return {"front": (x0, y0, x1, mid - 0.008), "rear": (x0, mid + 0.008, x1, y1)}


def remap_model_uv1(pieces, paint, ends):
    """{piece index: new uv1} for a non-box traffic model: wide islands compressed to 0..1, squeezed
    end caps projected into ends["front"/"rear"] (seen from outside, top up, aspect kept)."""
    isls = _analyse(pieces, paint)
    dens = _density(isls)
    new = {}

    def uvof(pi):
        if pi not in new:
            new[pi] = [list(t) for t in pieces[pi].uvs[1]]
        return new[pi]
    by_role = {}
    for isl in isls:
        r = _end_role(isl, dens) if ends else None
        if r and r in ends:
            by_role.setdefault(r, []).append(isl)
            continue
        pi, _tl, _A3, A2, _n, verts = isl
        src = pieces[pi].uvs[1]
        us = [src[j][0] for j in verts]
        u0, u1 = min(us), max(us)
        if A2 > 1e-7 and u1 - u0 > 1.02:
            uv = uvof(pi)
            for j in verts:
                uv[j][0] = 0.002 + 0.996 * (src[j][0] - u0) / (u1 - u0)
    for role, isl_list in by_role.items():
        R0, S0, R1, S1 = ends[role]
        pts = [(pi, j, pieces[pi].pos[j]) for pi, _tl, _a, _b, _n, verts in isl_list for j in verts]
        xs = [p[0] for _pi, _j, p in pts]
        ys = [p[1] for _pi, _j, p in pts]
        xa, xb, ya, yb = min(xs), max(xs), min(ys), max(ys)
        if xb - xa < 1e-4 or yb - ya < 1e-4:
            continue
        # keep the end's shape: fit its width x height into the panel (sheet is 2:1 -> u counts double)
        pw, ph = (R1 - R0) * 2, (S1 - S0)
        s = min(pw / (xb - xa), ph / (yb - ya))
        w, h = (xb - xa) * s / 2, (yb - ya) * s
        cu, cv = (R0 + R1) / 2, (S0 + S1) / 2
        flip = role == "rear"           # seen from behind, the trailer's left is on the right
        for pi, j, p in pts:
            fx = (p[0] - xa) / (xb - xa)
            if flip:
                fx = 1 - fx
            uv = uvof(pi)
            uv[j][0] = cu - w / 2 + w * fx
            uv[j][1] = cv - h / 2 + h * (yb - p[1]) / (yb - ya)       # top of the trailer = top of the panel
    return new
