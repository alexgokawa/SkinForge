"""Livery transfer: player cab sheet -> SCS AI truck.

SCS's AI semis were only ever meant to get a flat colour, so their texture layouts are packed
or overlapping - nobody wants to paint them. Their models are simplified copies of the player
trucks in the same 3D space, so the user paints the normal (vanilla-style) cab template and
SkinForge bakes that art onto the AI model's texture layout:

  for every texel of the AI sheet: its 3D point on the AI model -> the same spot on the player
  cab (seen along the face's main axis, depth-checked) -> that spot's UV on the cab sheet ->
  the art's colour there.

The player cab is rasterised once per axis view (PIL draws triangle ids, nearest last), so the
lookup is plain array indexing (numpy). AI and player models can be offset from each other (AI =
whole truck, player cab = cab space): the offset is found by a coarse-to-fine search that
maximises how many AI surface points land on the cab.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

AXES = [(0, 1), (0, -1), (1, 1), (1, -1), (2, 1), (2, -1)]   # (axis, sign): view looking along -sign*axis


class Surface:
    """Triangles with 3D positions, face normals and UVs as arrays."""

    def __init__(self, tris, sheet=None, prio=None):
        """sheet / prio: per triangle source-sheet index and priority (higher wins where two
        sources sit on the same spot, e.g. a 53' and a 48' body)."""
        uv = np.array([t[0] for t in tris], dtype=np.float64)          # (n, 3, 2)
        pos = np.array([t[1] for t in tris], dtype=np.float64)         # (n, 3, 3)
        hint = np.array([t[2] for t in tris], dtype=np.float64)        # (n, 3) summed vertex normals
        fn = np.cross(pos[:, 1] - pos[:, 0], pos[:, 2] - pos[:, 0])
        ln = np.linalg.norm(fn, axis=1)
        ok = ln > 1e-12
        fn[ok] /= ln[ok, None]
        flip = (fn * hint).sum(1) < 0                                  # match the model's normals
        fn[flip] *= -1
        self.uv, self.pos, self.n, self.ok = uv[ok], pos[ok], fn[ok], ok[ok]
        n = len(tris)
        self.sheet = (np.zeros(n, np.int64) if sheet is None else np.asarray(sheet, np.int64))[ok]
        self.prio = (np.zeros(n) if prio is None else np.asarray(prio, np.float64))[ok]


def _plane_axes(axis):
    return [a for a in range(3) if a != axis]


class Views:
    """The player cab rasterised along the six axes: per pixel, the visible triangle."""

    def __init__(self, surf: Surface, px=0.005, margin=0.6):
        self.s = surf
        lo = surf.pos.reshape(-1, 3).min(0) - margin
        hi = surf.pos.reshape(-1, 3).max(0) + margin
        self.lo, self.px = lo, px
        self.buf = {}
        for axis, sign in AXES:
            u, v = _plane_axes(axis)
            W = int((hi[u] - lo[u]) / px) + 1
            H = int((hi[v] - lo[v]) / px) + 1
            im = Image.new("I", (W, H), 0)
            d = ImageDraw.Draw(im)
            facing = surf.n[:, axis] * sign > 0.05
            idx = np.nonzero(facing)[0]
            depth = surf.pos[idx, :, axis].mean(1) * sign + surf.prio[idx] * 0.02   # nearer / preferred = drawn last
            for i in idx[np.argsort(depth)]:                         # far first, near last
                p = surf.pos[i]
                d.polygon([((p[k, u] - lo[u]) / px, (p[k, v] - lo[v]) / px) for k in range(3)], fill=int(i) + 1)
            self.buf[(axis, sign)] = np.asarray(im, dtype=np.int64)

    def lookup(self, pts, nrm, tol=0.08):
        """UV on the player sheet for each point (NaN where nothing matches)."""
        s = self.s
        out = np.full((len(pts), 2), np.nan)
        self.last_sheet = np.full(len(pts), -1, np.int64)
        done = np.zeros(len(pts), bool)
        order = np.argsort(-np.abs(nrm), axis=1)                    # main axis first
        for rank in range(2):
            for axis, sign in AXES:
                sel = ~done & (order[:, rank] == axis) & (np.sign(nrm[:, axis]) == sign)
                if not sel.any():
                    continue
                q = pts[sel]
                u, v = _plane_axes(axis)
                buf = self.buf[(axis, sign)]
                x = ((q[:, u] - self.lo[u]) / self.px).astype(np.int64)
                y = ((q[:, v] - self.lo[v]) / self.px).astype(np.int64)
                inside = (x >= 0) & (y >= 0) & (x < buf.shape[1]) & (y < buf.shape[0])
                tid = np.zeros(len(q), np.int64)
                tid[inside] = buf[y[inside], x[inside]]
                hit = tid > 0
                if not hit.any():
                    continue
                t = tid[hit] - 1
                P = s.pos[t]                                        # (m, 3, 3)
                a2 = P[:, :, [u, v]]
                q2 = q[hit][:, [u, v]]
                w = _bary(a2, q2)
                depth = (w[:, :, None] * P[:, :, [axis]]).sum(1)[:, 0]
                good = np.abs(depth - q[hit][:, axis]) < tol
                uvs = (w[:, :, None] * s.uv[t]).sum(1)
                ids = np.nonzero(sel)[0][np.nonzero(hit)[0]]
                ok_ids = ids[good]
                out[ok_ids] = uvs[good]
                self.last_sheet[ok_ids] = s.sheet[t[good]]
                done[ok_ids] = True
        return out


def _bary(tri2, p2):
    a, b, c = tri2[:, 0], tri2[:, 1], tri2[:, 2]
    v0, v1, v2 = b - a, c - a, p2 - a
    d00 = (v0 * v0).sum(1); d01 = (v0 * v1).sum(1); d11 = (v1 * v1).sum(1)
    d20 = (v2 * v0).sum(1); d21 = (v2 * v1).sum(1)
    den = d00 * d11 - d01 * d01
    den[np.abs(den) < 1e-18] = 1e-18
    vb = (d11 * d20 - d01 * d21) / den
    wb = (d00 * d21 - d01 * d20) / den
    return np.stack([1 - vb - wb, vb, wb], axis=1)


def texels(surf: Surface, size, first=None):
    """Every texel of a W x H sheet covered by the surface: (x, y, 3D point, normal).
    `first`: triangle indices drawn last (= they win where the layout stacks several parts)."""
    W, H = size
    im = Image.new("I", (W, H), 0)
    d = ImageDraw.Draw(im)
    order = np.arange(len(surf.uv))
    if first is not None:
        win = np.zeros(len(order), bool)
        win[first] = True
        order = np.concatenate([order[~win], order[win]])
    for i in order:
        d.polygon([(surf.uv[i, k, 0] * W, surf.uv[i, k, 1] * H) for k in range(3)], fill=int(i) + 1)
    buf = np.asarray(im, dtype=np.int64)
    ys, xs = np.nonzero(buf)
    t = buf[ys, xs] - 1
    p2 = np.stack([(xs + 0.5) / W, (ys + 0.5) / H], 1)
    w = np.clip(_bary(surf.uv[t], p2), -0.5, 1.5)
    pts = (w[:, :, None] * surf.pos[t]).sum(1)
    return xs, ys, pts, surf.n[t]


def align(ai: Surface, views: Views, samples=2500, seed=1, small_part=False):
    """Offset to add to AI points so they land on the player cab: (offset, share of AI points hit)."""
    rng = np.random.default_rng(seed)
    area = np.linalg.norm(np.cross(ai.pos[:, 1] - ai.pos[:, 0], ai.pos[:, 2] - ai.pos[:, 0]), axis=1)
    if area.sum() <= 0:
        return np.zeros(3), 0.0
    t = rng.choice(len(area), size=samples, p=area / area.sum())
    r = rng.random((samples, 2))
    flip = r.sum(1) > 1
    r[flip] = 1 - r[flip]
    w = np.stack([1 - r[:, 0] - r[:, 1], r[:, 0], r[:, 1]], 1)
    pts = (w[:, :, None] * ai.pos[t]).sum(1)
    nrm = ai.n[t]

    def score(off, tol=0.1):
        return np.isfinite(views.lookup(pts + off, nrm, tol)[:, 0]).mean()

    # SCS builds AI trucks / traffic trailers in the same space as the player models: try no
    # offset first; only search when that doesn't land (roof tops / fronts lined up)
    vl, vh = views.s.pos.reshape(-1, 3).min(0), views.s.pos.reshape(-1, 3).max(0)
    al, ah = ai.pos.reshape(-1, 3).min(0), ai.pos.reshape(-1, 3).max(0)
    cands = [np.zeros(3)]
    # parts modelled in their own space (trailer front / rear doors): line up low / high / middle
    for dy in (vh[1] - ah[1], vl[1] - al[1], (vl[1] + vh[1] - al[1] - ah[1]) / 2):
        for dz in (vl[2] - al[2], vh[2] - ah[2], (vl[2] + vh[2] - al[2] - ah[2]) / 2):
            cands.append(np.array([0.0, dy, dz]))
    best, best_s = max(((c, score(c, 0.15)) for c in cands), key=lambda x: x[1])
    small = small_part or best_s > 0.3
    passes = ((0.02, 0.1, 0.1, 0.04), (0.005, 0.02, 0.02, 0.01)) if small else \
        ((0.1, 1.0, 3.0, 0.0), (0.02, 0.12, 0.12, 0.06), (0.005, 0.025, 0.025, 0.02))
    for step, rng_y, rng_z, rng_x in passes:
        c = best.copy()
        for dx in np.arange(-rng_x, rng_x + 1e-9, max(step, 0.02) if rng_x else 1):
            for dy in np.arange(-rng_y, rng_y + 1e-9, step):
                for dz in np.arange(-rng_z, rng_z + 1e-9, step):
                    off = c + np.array([dx, dy, dz])
                    s_ = score(off, 0.15 if step >= 0.1 else 0.06)
                    if s_ > best_s:
                        best, best_s = off, s_
    return best, float(score(best, 0.06))


def bake(art, player: Surface, ai: Surface, size, offset, views=None, fill=(154, 154, 154, 255)):
    """The AI sheet (RGBA, size) with the player-sheet art carried over. Texels with no match
    take `fill` (the paint colour)."""
    views = views or Views(player)
    # AI faces that sit on the player cab win where the AI layout stacks several parts
    cen = ai.pos.mean(1)
    on_cab = np.nonzero(np.isfinite(views.lookup(cen + offset, ai.n, tol=0.1)[:, 0]))[0]
    xs, ys, pts, nrm = texels(ai, size, first=on_cab)
    uv = views.lookup(pts + offset, nrm, tol=0.08)
    sheet = views.last_sheet.copy()
    miss = ~np.isfinite(uv[:, 0])
    if miss.any():   # second, looser pass: walls the two models place a few cm apart (trailer fronts)
        uv2 = views.lookup(pts[miss] + offset, nrm[miss], tol=0.35)
        uv[miss] = uv2
        sheet[miss] = views.last_sheet
    arts = art if isinstance(art, (list, tuple)) else [art]
    ok = np.isfinite(uv[:, 0])
    out = np.zeros((size[1], size[0], 4), np.uint8)
    out[...] = fill
    src = np.zeros((int(ok.sum()), 4), np.float32)              # transparent = paint colour
    ui, sh = uv[ok], sheet[ok]
    for k, im in enumerate(arts):
        sel = sh == k
        if im is None or not sel.any():
            continue
        a = np.asarray(im.convert("RGBA"), dtype=np.uint8)
        H, W = a.shape[:2]
        sx = np.clip((ui[sel, 0] % 1.0) * W, 0, W - 1).astype(np.int64)
        sy = np.clip((ui[sel, 1] % 1.0) * H, 0, H - 1).astype(np.int64)
        src[sel] = a[sy, sx]
    al = src[:, 3:4] / 255.0
    col = src[:, :3] * al + np.array(fill[:3], np.float32) * (1 - al)   # transparent = paint colour
    out[ys[ok], xs[ok], :3] = col.astype(np.uint8)
    out[ys[ok], xs[ok], 3] = 255
    return Image.fromarray(out, "RGBA"), float(ok.mean()) if len(ok) else 0.0


# ================================================================== projection template (escorts)
VIEWS = {   # name: (forward = looking direction, up). image right = forward x up
    "LEFT SIDE": ((1, 0, 0), (0, 1, 0)),
    "RIGHT SIDE": ((-1, 0, 0), (0, 1, 0)),
    "ROOF": ((0, -1, 0), (1, 0, 0)),
    "FRONT": ((0, 0, 1), (0, 1, 0)),
    "REAR": ((0, 0, -1), (0, 1, 0)),
}


class Projection:
    """An MS2-style template made from the model itself: the vehicle seen from the left, the
    right, above, the front and the back, laid out on one sheet (each view as seen from outside,
    so lettering reads the right way on the vehicle). locate() maps 3D points to that sheet."""

    def __init__(self, surf: Surface, size, gap=0.15):
        self.s, self.size = surf, size
        W, H = size
        P = surf.pos.reshape(-1, 3)
        lo, hi = P.min(0), P.max(0)
        L, Hh, Wd = hi[2] - lo[2], hi[1] - lo[1], hi[0] - lo[0]
        k = min(W / (L + Wd + 3 * gap), H / (2 * Hh + Wd + 4 * gap))
        place = {"LEFT SIDE": (gap, gap), "RIGHT SIDE": (gap, 2 * gap + Hh), "ROOF": (gap, 3 * gap + 2 * Hh),
                 "FRONT": (2 * gap + L, gap), "REAR": (2 * gap + L, 2 * gap + Hh)}
        self.k, self.view = k, {}
        for vi, (name, (f, u)) in enumerate(VIEWS.items()):
            f, u = np.array(f, float), np.array(u, float)
            r = np.cross(f, u)
            X, Y = P @ r, -(P @ u)
            ox, oy = place[name]
            self.view[name] = (vi, f, u, r, X.min(), Y.min(), ox * k, oy * k,
                               (X.max() - X.min()) * k, (Y.max() - Y.min()) * k)
        self.buf = self._raster()

    def _xy(self, name, pts):
        vi, f, u, r, xmin, ymin, ox, oy, _w, _h = self.view[name]
        return (pts @ r - xmin) * self.k + ox, (-(pts @ u) - ymin) * self.k + oy

    def _raster(self, outline=None):
        W, H = self.size
        im = Image.new("I", (W, H), 0)
        d = ImageDraw.Draw(im)
        s = self.s
        for name, (vi, f, *_rest) in self.view.items():
            facing = (s.n @ f) < -0.05
            idx = np.nonzero(facing)[0]
            depth = (s.pos[idx] @ f).mean(1)
            for i in idx[np.argsort(-depth)]:              # far first
                x, y = self._xy(name, s.pos[i])
                d.polygon(list(zip(x.tolist(), y.tolist())), fill=int(i) * 5 + vi + 1)
        return np.asarray(im, dtype=np.int64)

    def template(self):
        """White views with the model's panel lines, outlines and view names (RGBA)."""
        from .scsgame import _font
        W, H = self.size
        im = Image.new("L", (W, H), 40)
        d = ImageDraw.Draw(im)
        s = self.s
        lw = max(1, round(W / 2048))
        for name, (vi, f, *_rest) in self.view.items():
            idx = np.nonzero((s.n @ f) < -0.05)[0]
            depth = (s.pos[idx] @ f).mean(1)
            for i in idx[np.argsort(-depth)]:
                x, y = self._xy(name, s.pos[i])
                d.polygon(list(zip(x.tolist(), y.tolist())), fill=255, outline=215)
        mask = Image.fromarray(((self.buf > 0) * 255).astype(np.uint8), "L")
        from PIL import ImageFilter, ImageChops
        edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(2 * lw + 1)))
        im.paste(0, mask=edge)
        for name, (vi, f, u, r, xmin, ymin, ox, oy, w, h) in self.view.items():
            fs = max(12, min(h * 0.12, w * 0.8 / (0.7 * (len(name) + 8))))
            text = name
            if name in ("LEFT SIDE", "ROOF"):
                text = "< FRONT  " + name
            elif name == "RIGHT SIDE":
                text = name + "  FRONT >"
            d.text((ox + w / 2, oy + h / 2), text, fill=90, font=_font(fs), anchor="mm")
        return im.convert("RGBA")

    def locate(self, pts, nrm, own_tri=None, tol=0.05):
        """Sheet UV (0..1) for each point: the view it faces most where it is visible."""
        W, H = self.size
        out = np.full((len(pts), 2), np.nan)
        names = list(self.view)
        F = np.array([self.view[n][1] for n in names])              # (5, 3)
        facing = -(nrm @ F.T)                                       # (m, 5)
        order = np.argsort(-facing, axis=1)
        for rank in range(3):
            todo = np.nonzero(~np.isfinite(out[:, 0]))[0]
            if not len(todo):
                break
            for j, name in enumerate(names):
                sel = todo[(order[todo, rank] == j) & (facing[todo, j] > 0.05)]
                if not len(sel):
                    continue
                x, y = self._xy(name, pts[sel])
                xi, yi = x.astype(np.int64), y.astype(np.int64)
                inside = (xi >= 0) & (yi >= 0) & (xi < W) & (yi < H)
                v = np.zeros(len(sel), np.int64)
                v[inside] = self.buf[yi[inside], xi[inside]]
                tri, view = (v - 1) // 5, (v - 1) % 5
                ok = (v > 0) & (view == self.view[name][0])
                if own_tri is not None:
                    same = ok & (tri == own_tri[sel])
                else:
                    same = np.zeros(len(sel), bool)
                # visible = the drawn face is this point's own face or lies at its depth
                f = self.view[name][1]
                dep = np.full(len(sel), np.inf)
                okk = ok & ~same
                if okk.any():
                    dep[okk] = np.abs((self.s.pos[tri[okk]].mean(1) @ f) - (pts[sel][okk] @ f))
                good = same | (ok & (dep < tol))
                out[sel[good], 0] = x[good] / W
                out[sel[good], 1] = y[good] / H
        # nothing visible (inside a wheel arch...): the view it faces most, unchecked
        rest = np.nonzero(~np.isfinite(out[:, 0]))[0]
        for j, name in enumerate(names):
            sel = rest[order[rest, 0] == j]
            if len(sel):
                x, y = self._xy(name, pts[sel])
                out[sel, 0], out[sel, 1] = x / W, y / H
        return out


def bake_projection(art, proj: Projection, target: Surface, size, fill=(154, 154, 154, 255)):
    """Target sheet (the model's own texture layout) filled from art painted on the projection
    template. target and proj.s are the same model (target carries the texture-layout UVs)."""
    xs, ys, pts, nrm = texels(target, size)
    # own triangle of each texel: same index in both surfaces (built from the same tris)
    W, H = size
    tb = Image.new("I", (W, H), 0)
    d = ImageDraw.Draw(tb)
    for i in range(len(target.uv)):
        d.polygon([(target.uv[i, k, 0] * W, target.uv[i, k, 1] * H) for k in range(3)], fill=int(i) + 1)
    own = np.asarray(tb, dtype=np.int64)[ys, xs] - 1
    uv = proj.locate(pts, nrm, own_tri=own)
    a = np.asarray(art.convert("RGBA"), dtype=np.uint8)
    AH, AW = a.shape[:2]
    sx = np.clip(uv[:, 0] * AW, 0, AW - 1).astype(np.int64)
    sy = np.clip(uv[:, 1] * AH, 0, AH - 1).astype(np.int64)
    src = a[sy, sx].astype(np.float32)
    al = src[:, 3:4] / 255.0
    col = src[:, :3] * al + np.array(fill[:3], np.float32) * (1 - al)
    out = np.zeros((H, W, 4), np.uint8)
    out[...] = fill
    out[ys, xs, :3] = col.astype(np.uint8)
    out[ys, xs, 3] = 255
    return Image.fromarray(out, "RGBA")
