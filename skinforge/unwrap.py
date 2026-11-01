"""Livery UV layout for SCS escort / AI models whose own paint UVs are not paintable (one side
shared by both sides, parts stacked) - the way escort skin authors (Pauly) do it by hand:
copy the model and lay its body out again as LEFT SIDE / RIGHT SIDE / ROOF / FRONT / REAR.

No geometry is changed: only the UV values of one UV set of the paint pieces are rewritten in
the copied .pmg (same vertex count, same file size). That works because vertices are never
shared across UV islands (a vertex has one UV per set), so every island can be moved on its
own. Each island goes to the view its surface faces (left, right, top, front, rear) and is
placed where that view sees it: an affine fit from its own UVs onto the view projection, so
it keeps SCS's unfold but lines up with its neighbours like on a photo of the vehicle.
Islands nobody sees from outside (inner walls, undersides, dark interior) go into a small
"inside / hidden" box.

Axes: SCS models look down -z (x = right, y = up).
"""
from __future__ import annotations

import struct

import numpy as np

SHEET = (2, 1)            # template / texture shape (w : h)
MARGIN = 0.035            # between views, in sheet units (sheet = 2 x 1)
VIEWS = ("left", "right", "top", "front", "rear")
LABELS = {"left": "< FRONT    LEFT SIDE", "right": "RIGHT SIDE    FRONT >", "top": "< FRONT    ROOF / HOOD",
          "front": "FRONT", "rear": "REAR", "hidden": "inside"}


# ---------------------------------------------------------------- geometry helpers
def _islands(n_verts, tris):
    parent = list(range(n_verts))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, c in tris:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    roots = {}
    tri_island = []
    for a, _b, _c in tris:
        tri_island.append(roots.setdefault(find(a), len(roots)))
    return np.array(tri_island, int), len(roots)


def _face_normals(P, T, N):
    e1 = P[T[:, 1]] - P[T[:, 0]]
    e2 = P[T[:, 2]] - P[T[:, 0]]
    fn = np.cross(e1, e2)
    area = np.linalg.norm(fn, axis=1) / 2
    if N is not None and len(N):
        vn = N[T].sum(axis=1)
        flip = (fn * vn).sum(axis=1) < 0      # wind against the smooth normal: trust the normal
        fn[flip] *= -1
    ln = np.linalg.norm(fn, axis=1)
    ln[ln == 0] = 1
    return fn / ln[:, None], area


def _view_of(n):
    """Per triangle: which view looks at it."""
    x, y, z = n[:, 0], n[:, 1], n[:, 2]
    cand = np.stack([-x, x, y, -z, z, -y], axis=1)      # left right top front rear bottom
    return cand.argmax(axis=1)                          # 5 = bottom


def project(view, P):
    """View coordinates (metres, u right, v down) as the template shows that view."""
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    if view == "left":
        return np.stack([z, -y], 1)          # front (-z) at the left
    if view == "right":
        return np.stack([-z, -y], 1)         # front at the right
    if view == "top":
        return np.stack([z, x], 1)           # front at the left, left side at the top
    if view == "front":
        return np.stack([-x, -y], 1)         # seen from in front: the vehicle's left is on the right
    return np.stack([x, -y], 1)              # rear: seen from behind


class Frame:
    """Vehicle extents (from the main model) and where each view sits on the sheet."""

    def __init__(self, P):
        lo, hi = P.min(0), P.max(0)
        self.lo, self.hi = lo, hi
        self.c = (lo + hi) / 2
        self.half = np.maximum((hi - lo) / 2, 1e-3)
        Wd, H, L = hi - lo
        m = MARGIN
        # left column: LEFT over RIGHT; right column: ROOF over FRONT | REAR | hidden
        hid_w = max(L - 2 * Wd - 2 * m, 0.45 * Wd)
        col2 = max(L, 2 * Wd + hid_w + 2 * m)
        tw = m + L + m + col2 + m
        th = m + max(2 * H + m, Wd + m + H) + m
        s = min(SHEET[0] / tw, SHEET[1] / th)       # sheet units per metre (same for every view)
        self.s = s
        ox = (SHEET[0] - tw * s) / 2
        oy = (SHEET[1] - th * s) / 2
        X1 = ox + m * s
        X2 = X1 + (L + m) * s
        Y1 = oy + m * s
        self.rect = {
            "left": (X1, Y1, L * s, H * s),
            "right": (X1, Y1 + (H + m) * s, L * s, H * s),
            "top": (X2, Y1, L * s, Wd * s),
            "front": (X2, Y1 + (Wd + m) * s, Wd * s, H * s),
            "rear": (X2 + (Wd + m) * s, Y1 + (Wd + m) * s, Wd * s, H * s),
            "hidden": (X2 + 2 * (Wd + m) * s, Y1 + (Wd + m) * s, hid_w * s, H * s),
        }
        # view-space box of the whole vehicle per view (for placing projections in their rect)
        corners = np.array([[a, b, c] for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])])
        self.vbox = {}
        for v in VIEWS:
            q = project(v, corners)
            self.vbox[v] = (q.min(0), q.max(0))

    def to_sheet(self, view, q):
        x, y, w, h = self.rect[view]
        lo, hi = self.vbox[view]
        return np.stack([x + (q[:, 0] - lo[0]) * self.s, y + (q[:, 1] - lo[1]) * self.s], 1)

    def visible(self, view, P):
        """Is an island (its vertices) on the outside for that view? Inner walls are not."""
        m = P.mean(0)
        if view == "top":
            return True                    # open beds / roofs: nothing above them
        ax = {"left": 0, "right": 0, "front": 2, "rear": 2}[view]
        sign = {"left": -1, "right": 1, "front": -1, "rear": 1}[view]
        return sign * (m[ax] - self.c[ax]) > 0.25 * self.half[ax]


def _affine(src, dst):
    """Least-squares affine src -> dst; None when src is degenerate."""
    A = np.hstack([src, np.ones((len(src), 1))])
    if len(src) < 3 or np.linalg.matrix_rank(A, tol=1e-7) < 3:
        return None
    M, *_ = np.linalg.lstsq(A, dst, rcond=None)
    return M


# ---------------------------------------------------------------- the layout
class Plan:
    """New UVs (sheet units, 2 x 1) for the paint pieces of one model."""

    def __init__(self):
        self.uv = {}          # piece index -> (n_verts, 2) array in 0..1 texture space
        self.tris = []        # (uv tri in 0..1, view) for drawing the template
        self.used = set()


def layout(pieces, piece_ids, k, frame, dark=None, hk=0):
    """pieces: read_pmg pieces; piece_ids: the paint pieces; k: UV set whose layout is rewritten.
    dark(piece, tri_idx) -> bool array: islands SCS keeps dark (interior) go to the hidden box.
    hk: UV set whose shape the hidden islands keep (SCS's own unfold, usually set 0)."""
    plan = Plan()
    hidden = []            # (piece, vert idx array, original uv)
    for pi in piece_ids:
        pc = pieces[pi]
        P = np.array(pc.pos, float)
        T = np.array(pc.tris, int)
        N = np.array(pc.nrm, float) if pc.nrm else None
        UV = np.array(pc.uvs[k], float)
        out = UV.copy()
        isl, n_isl = _islands(len(P), pc.tris)
        fn, area = _face_normals(P, T, N)
        tv = _view_of(fn)
        dk = dark(pc, T) if dark else np.zeros(len(T), bool)
        for i in range(n_isl):
            sel = isl == i
            ti = T[sel]
            vids = np.unique(ti)
            a = area[sel]
            votes = np.bincount(tv[sel], weights=a, minlength=6)
            view_i = int(votes.argmax())
            view = VIEWS[view_i] if view_i < 5 else None
            is_dark = a[dk[sel]].sum() > 0.5 * a.sum() if a.sum() else False
            if view is None or is_dark or not frame.visible(view, P[vids]):
                hidden.append((pi, vids, np.array(pc.uvs[hk if hk < len(pc.uvs) else k], float)[vids], ti))
                continue
            q = project(view, P[vids])
            dst = frame.to_sheet(view, q)
            # fit on the faces that really look this way (a side island that wraps over a rail
            # into the bed must not drag the side off its place)
            core = np.unique(ti[tv[sel] == view_i])
            ci = np.searchsorted(vids, core)
            M = _affine(UV[core], dst[ci]) if len(core) >= 3 else None
            if M is None:
                M = _affine(UV[vids], dst)
            if M is not None:
                fit = np.hstack([UV[vids], np.ones((len(vids), 1))]) @ M
                ref = ci if len(core) >= 3 else np.arange(len(vids))
                span = max(np.ptp(dst[ref], axis=0).max(), 1e-6)
                if np.abs(fit[ref] - dst[ref]).max() > 0.35 * span:   # SCS's unfold is not a view of it: project
                    fit = dst
            else:
                fit = dst
            out[vids] = fit
            plan.used.add(view)
            for t in ti:
                plan.tris.append((fit[np.searchsorted(vids, t)], view))
        plan.uv[pi] = out
    _pack_hidden(plan, hidden, frame)
    for pi, arr in plan.uv.items():               # sheet units -> texture 0..1
        plan.uv[pi] = arr / np.array(SHEET, float)
    plan.tris = [(t / np.array(SHEET, float), v) for t, v in plan.tris]
    return plan


def _pack_hidden(plan, hidden, frame):
    """Shelf-pack the hidden islands (their own UV shape) into the hidden box."""
    if not hidden:
        return
    x0, y0, w, h = frame.rect["hidden"]
    boxes = []
    for pi, vids, uv, ti in hidden:
        lo, hi = uv.min(0), uv.max(0)
        boxes.append((pi, vids, uv, lo, np.maximum(hi - lo, 1e-4), ti))
    total = sum(float(b[4][0] * b[4][1]) for b in boxes)
    f = np.sqrt(w * h / max(total, 1e-9)) * 0.85
    order = sorted(range(len(boxes)), key=lambda i: -boxes[i][4][1])
    for _ in range(30):
        x, y, row, ok, place = 0.0, 0.0, 0.0, True, {}
        for i in order:
            bw, bh = boxes[i][4] * f
            if x + bw > w:
                x, y, row = 0.0, y + row, 0.0
            if y + bh > h or bw > w:
                ok = False
                break
            place[i] = (x, y)
            x += bw
            row = max(row, bh)
        if ok:
            break
        f *= 0.85
    for i, (pi, vids, uv, lo, _sz, _ti) in enumerate(boxes):
        px, py = place.get(i, (0.0, 0.0))
        new = (uv - lo) * f + np.array([x0 + px, y0 + py])
        plan.uv[pi][vids] = new
    plan.used.add("hidden")
    for pi, _vids, _uv, _lo, _sz, ti in boxes:     # template lines for the hidden box
        plan.tris += [(plan.uv[pi][t], "hidden") for t in ti]


# ---------------------------------------------------------------- pmg writing
def atlas_dark(img, uvset=0, level=60):
    """dark() for layout(): triangles whose centre sits on a dark texel of SCS's own texture
    (interior, tyres, trim) - they keep SCS's look (mult materials) and stay off the sheet."""
    a = np.asarray(img.convert("L"), float)
    h, w = a.shape

    def dark(pc, T):
        if uvset >= len(pc.uvs):
            return np.zeros(len(T), bool)
        uv = np.array(pc.uvs[uvset], float)[T].mean(axis=1)
        x = np.clip((uv[:, 0] % 1.0) * w, 0, w - 1).astype(int)
        y = np.clip((uv[:, 1] % 1.0) * h, 0, h - 1).astype(int)
        return a[y, x] < level
    return dark


def write_uvs(data: bytes, new_uvs: dict, k: int) -> bytes:
    """Copy of a .pmg 0x15 with UV set k of some pieces replaced ({piece index: (n, 2) array})."""
    buf = bytearray(data)
    pieces_off = struct.unpack_from("<i", data, 84)[0]
    for pi, uv in new_uvs.items():
        b = pieces_off + 100 * pi
        _edges, verts, _m, tc_width, _mat = struct.unpack_from("<iiIii", data, b)
        (_skel, pos_o, nrm_o, tc_o, col_o, fac_o, tan_o, bidx_o, _bw_o, _idx_o) = struct.unpack_from("<10i", data, b + 60)
        if tc_o == -1 or k >= tc_width or len(uv) != verts:
            raise ValueError(f"piece {pi}: no UV set {k}")
        stride = sum(s for o, s in ((pos_o, 12), (nrm_o, 12), (tan_o, 16), (tc_o, 8 * tc_width),
                                    (col_o, 4), (fac_o, 4), (bidx_o, 8)) if o != -1)
        for j in range(verts):
            struct.pack_into("<2f", buf, tc_o + stride * j + 8 * k, float(uv[j][0]), float(uv[j][1]))
    return bytes(buf)
