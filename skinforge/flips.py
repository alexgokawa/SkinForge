"""Mirrored UV pieces: paint them upright on a separate sheet, flip them back at build time.

Some templates lay pieces out mirrored or upside down (Jon's W900: the whole right side).
A piece ("island") is found from one click on the template: the white area enclosed by
the black background, including its own wireframe lines and labels. All pieces with the
same flip turn around one shared box (the union of their boxes), so the upright sheet is
the group flipped as one sheet, and the build flips the user's upright art back.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OPS = {"v": "Flip vertical", "h": "Flip horizontal", "r": "Rotate 180°"}
WORK = 1024   # island detection resolution (Pillow flood fill is pure Python)


def _apply(im: Image.Image, op: str) -> Image.Image:
    if op == "v":
        return im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if op == "h":
        return im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return im.transpose(Image.Transpose.ROTATE_180)


def _border_fill(mask: Image.Image, value: int = 128) -> Image.Image:
    """Flood-fill every 255 region touching the border with `value`."""
    m = mask.copy()
    w, h = m.size
    px = m.load()
    for x in range(w):
        for y in (0, h - 1):
            if px[x, y] == 255:
                ImageDraw.floodfill(m, (x, y), value)
    for y in range(h):
        for x in (0, w - 1):
            if px[x, y] == 255:
                ImageDraw.floodfill(m, (x, y), value)
    return m


def used_mask(template: Image.Image, work: int = WORK) -> Image.Image:
    """255 = texture area that belongs to some piece, at `work` px on the long side."""
    tw, th = template.size
    s = min(1.0, work / max(tw, th))
    size = (max(1, round(tw * s)), max(1, round(th * s)))
    g = template.convert("L")
    if g.size != size:
        g = g.resize(size, Image.Resampling.BOX)
    black = g.point(lambda v: 255 if v < 64 else 0)
    bg = _border_fill(black).point(lambda v: 255 if v == 128 else 0)   # background + lines touching it
    used = ImageChops.invert(bg)
    # close the wireframe lines that the background fill ate (1-3 px)
    return used.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))


def _fill_holes(mask: Image.Image) -> Image.Image:
    """Close holes (labels, windows) inside one island - done on its bbox only, for speed."""
    bbox = mask.getbbox()
    if not bbox:
        return mask
    x0, y0, x1, y1 = max(0, bbox[0] - 1), max(0, bbox[1] - 1), min(mask.size[0], bbox[2] + 1), min(mask.size[1], bbox[3] + 1)
    crop = mask.crop((x0, y0, x1, y1))
    inv = ImageChops.invert(crop)
    outside = _border_fill(inv).point(lambda v: 255 if v == 128 else 0)
    holes = ImageChops.subtract(inv, outside)
    out = mask.copy()
    out.paste(ImageChops.lighter(crop, holes), (x0, y0))
    return out


def island(used: Image.Image, x: float, y: float):
    """Island at normalized point (x, y). Returns (mask L at used-size, bbox in pixels) or None."""
    w, h = used.size
    px, py = min(w - 1, max(0, int(x * w))), min(h - 1, max(0, int(y * h)))
    if used.getpixel((px, py)) != 255:
        # clicked a line or a gap: look a few px around for the piece
        found = None
        for r in range(1, 7):
            for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, -r), (r, -r), (-r, r)):
                qx, qy = px + dx, py + dy
                if 0 <= qx < w and 0 <= qy < h and used.getpixel((qx, qy)) == 255:
                    found = (qx, qy)
                    break
            if found:
                break
        if not found:
            return None
        px, py = found
    m = used.copy()
    ImageDraw.floodfill(m, (px, py), 128)
    mask = m.point(lambda v: 255 if v == 128 else 0)
    mask = _fill_holes(mask)
    bbox = mask.getbbox()
    if not bbox:
        return None
    return mask, bbox


def island_in_box(used: Image.Image, box):
    """Biggest piece inside a dragged box (normalized x0, y0, x1, y1), cut off at the box edge -
    for pieces that touch their neighbours on the template."""
    w, h = used.size
    x0, y0 = max(0, int(min(box[0], box[2]) * w)), max(0, int(min(box[1], box[3]) * h))
    x1, y1 = min(w, int(max(box[0], box[2]) * w) + 1), min(h, int(max(box[1], box[3]) * h) + 1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    crop = used.crop((x0, y0, x1, y1))
    cw, ch = crop.size
    work = crop.copy()
    best, best_n = None, 0
    step = max(2, min(cw, ch) // 16)
    px = work.load()
    for yy in range(0, ch, step):
        for xx in range(0, cw, step):
            if px[xx, yy] != 255:
                continue
            ImageDraw.floodfill(work, (xx, yy), 128)
            comp = work.point(lambda v: 255 if v == 128 else 0)
            n = sum(comp.histogram()[255:])
            if n > best_n:
                best, best_n = comp, n
            work.paste(0, (0, 0), comp)   # done with this component
            px = work.load()
    if best is None:
        return None
    mask = Image.new("L", used.size, 0)
    mask.paste(best, (x0, y0))
    mask = _fill_holes(mask)
    bbox = mask.getbbox()
    return (mask, bbox) if bbox else None


def island_for_mark(used: Image.Image, mk: dict):
    if mk.get("box"):
        return island_in_box(used, mk["box"])
    return island(used, mk["x"], mk["y"])


def islands_for(used: Image.Image, marks: list):
    """[(mark, mask, bbox)] for the marks that still hit a piece."""
    out = []
    for mk in marks:
        r = island_for_mark(used, mk)
        if r:
            out.append((mk, r[0], r[1]))
    return out


def _scaled(mask: Image.Image, bbox, size):
    """Island mask + bbox scaled to `size` (w, h), slightly grown to cover anti-aliased edges."""
    sw, sh = size[0] / mask.size[0], size[1] / mask.size[1]
    m = mask.resize(size, Image.Resampling.BILINEAR).point(lambda v: 255 if v >= 96 else 0)
    if max(size) >= 1024:
        m = m.filter(ImageFilter.MaxFilter(3))
    b = (int(bbox[0] * sw), int(bbox[1] * sh), min(size[0], round(bbox[2] * sw)), min(size[1], round(bbox[3] * sh)))
    return m, b


def pivots_for(isl: list, used_size) -> dict:
    """Flip box per op (union of that op's piece boxes) in used-mask pixels - stored with the
    project when an upright sheet is exported, so later mark changes can't move painted art."""
    out = {}
    for mk, mask, bbox in isl:
        u = out.get(mk["op"])
        out[mk["op"]] = list(bbox) if u is None else [min(u[0], bbox[0]), min(u[1], bbox[1]), max(u[2], bbox[2]), max(u[3], bbox[3])]
    return {op: v + list(used_size) for op, v in out.items()}


def _scale_box(box, uw, uh, size):
    sw, sh = size[0] / uw, size[1] / uh
    return (int(box[0] * sw), int(box[1] * sh), min(size[0], round(box[2] * sw)), min(size[1], round(box[3] * sh)))


def placements(isl: list, size, pivots: dict | None = None):
    """[(op, mask at `size`, piece box, box on the upright sheet)]. All pieces with the same flip
    turn around one shared box (the union of their boxes, or the stored pivot), so the group
    flips as one rigid sheet: pieces can't land on each other, and they keep their real-world
    arrangement (W900: tankers/stairs end up under the cab)."""
    piv = dict(pivots or {})
    if isl:
        uw, uh = isl[0][1].size
        for op, v in pivots_for(isl, (uw, uh)).items():
            piv.setdefault(op, v)
    out = []
    for mk, mask, bbox in isl:
        op = mk["op"]
        m, b = _scaled(mask, bbox, size)
        p = piv[op]
        u = _scale_box(p[:4], p[4], p[5], size)
        cx, cy = u[0] + u[2], u[1] + u[3]
        if op == "v":
            src = (b[0], cy - b[3], b[2], cy - b[1])
        elif op == "h":
            src = (cx - b[2], b[1], cx - b[0], b[3])
        else:
            src = (cx - b[2], cy - b[3], cx - b[0], cy - b[1])
        out.append((op, m, b, src))
    return out


def footprint(isl: list, size, pivots=None) -> Image.Image:
    """Where the marked pieces sit on the upright sheet (L mask)."""
    out = Image.new("L", size, 0)
    for op, m, b, src in placements(isl, size, pivots):
        fm = _apply(m.crop(b), op)
        out.paste(fm, src[:2], fm)
    return out


def to_upright(image: Image.Image, isl: list, background=(0, 0, 0, 0), pivots=None) -> Image.Image:
    """Sheet with only the marked pieces, turned upright."""
    size = image.size
    out = Image.new("RGBA", size, background)
    src_im = image.convert("RGBA")
    for op, m, b, src in placements(isl, size, pivots):
        out.paste(_apply(src_im.crop(b), op), src[:2], _apply(m.crop(b), op))
    return out


def from_upright(main: Image.Image, upright: Image.Image, isl: list, pivots=None) -> Image.Image:
    """Put upright-painted pieces back into the game layout on top of `main`."""
    size = main.size
    out = main.convert("RGBA").copy()
    up = upright.convert("RGBA")
    if up.size != size:
        up = up.resize(size, Image.Resampling.LANCZOS)
    for op, m, b, src in placements(isl, size, pivots):
        piece = _apply(up.crop(src), op)                 # same flip again = back to the game layout
        region = Image.composite(piece, out.crop(b), m.crop(b))   # replace (incl. transparency) inside the piece
        out.paste(region, b[:2])
    return out


def grey_out(template: Image.Image, isl: list) -> Image.Image:
    """Main-sheet template with the marked pieces dimmed and hatched ('paint these upright')."""
    size = template.size
    out = template.convert("RGBA").copy()
    for mk, mask, bbox in isl:
        m, b = _scaled(mask, bbox, size)
        shade = Image.new("RGBA", size, (40, 120, 160, 0))
        d = ImageDraw.Draw(shade)
        step = max(8, size[0] // 128)
        for i in range(-size[1], size[0], step * 2):
            d.line([(i, 0), (i + size[1], size[1])], fill=(40, 120, 160, 255), width=max(2, step // 3))
        a = m.point(lambda v: 150 if v else 0)
        shade.putalpha(ImageChops.multiply(shade.getchannel("A"), a))
        out.alpha_composite(shade)
    return out


def outline(size, isl: list, color=(0, 200, 255, 230)) -> Image.Image:
    """Overlay with the marked pieces' outlines (for the preview)."""
    ov = Image.new("RGBA", size, (0, 0, 0, 0))
    for mk, mask, bbox in isl:
        m, b = _scaled(mask, bbox, size)
        edge = ImageChops.subtract(m.filter(ImageFilter.MaxFilter(5)), m)
        layer = Image.new("RGBA", size, color)
        layer.putalpha(edge)
        ov.alpha_composite(layer)
        tint = Image.new("RGBA", size, color[:3] + (0,))
        tint.putalpha(m.point(lambda v: 40 if v else 0))
        ov.alpha_composite(tint)
    return ov


def norm_bbox(bbox, size):
    w, h = size
    return [bbox[0] / w, bbox[1] / h, bbox[2] / w, bbox[3] / h]
