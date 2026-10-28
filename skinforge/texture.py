"""Texture I/O for SCS: DXT5 DDS with full mip chain, uncompressed UI icons, .tobj, guides."""
from __future__ import annotations

import io
import struct

from PIL import Image, ImageChops, ImageDraw, ImageFilter

# --- .tobj -----------------------------------------------------------------
# 40-byte headers copied from known-good MS2 output (v1.50+ format), followed by
# u32 path length, u32 0, path. Mask = repeat/mipmapped; UI = clamp, no mips.
_TOBJ_MASK = bytes.fromhex("010ab170" + "00" * 16 + "01000200" "02000303" "03000000" "00000000" "00010000")
_TOBJ_UI = bytes.fromhex("010ab170" + "00" * 16 + "01000000" "02000303" "02000202" "00010000" "00010000")


def make_tobj(dds_path: str, ui: bool = False) -> bytes:
    p = dds_path.encode("ascii")
    return (_TOBJ_UI if ui else _TOBJ_MASK) + struct.pack("<II", len(p), 0) + p


def read_tobj_path(data: bytes):
    if not data or len(data) < 48:
        return None
    n = struct.unpack_from("<I", data, 40)[0]
    if 48 + n > len(data):
        return None
    try:
        return data[48:48 + n].decode("ascii")
    except UnicodeDecodeError:
        return None


# --- DDS -------------------------------------------------------------------
def _dxt5_blocks(im: Image.Image) -> bytes:
    w, h = im.size
    pw, ph = max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4)
    if (pw, ph) != (w, h):
        canvas = Image.new("RGBA", (pw, ph))
        canvas.paste(im, (0, 0))
        im = canvas
    buf = io.BytesIO()
    try:
        im.save(buf, format="DDS", pixel_format="DXT5")
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError("This Pillow version can't write DXT5 - update it (pip install -U pillow, needs 11.2+)") from e
    data = buf.getvalue()
    hdr = 148 if data[84:88] == b"DX10" else 128
    return data[hdr:]


def _mip_chain(im: Image.Image):
    """Premultiplied-alpha box-filtered mips so transparent areas don't bleed dark fringes."""
    levels = [im]
    cur = im.convert("RGBa")
    w, h = im.size
    while w > 1 or h > 1:
        w, h = max(1, w // 2), max(1, h // 2)
        cur = cur.resize((w, h), Image.Resampling.BOX)
        levels.append(cur.convert("RGBA"))
    return levels


def encode_dds_dxt5(im: Image.Image, mipmaps: bool = True) -> bytes:
    im = im.convert("RGBA")
    w, h = im.size
    levels = _mip_chain(im) if mipmaps else [im]
    body = b"".join(_dxt5_blocks(l) for l in levels)
    top = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mipmaps else 0)
    caps = 0x1000 | ((0x400000 | 0x8) if mipmaps else 0)
    hdr = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, h, w, top, 0, len(levels))
    hdr += b"\0" * 44
    hdr += struct.pack("<II4sIIIII", 32, 0x4, b"DXT5", 0, 0, 0, 0, 0)
    hdr += struct.pack("<IIIII", caps, 0, 0, 0, 0)
    return hdr + body


def encode_dds_bgra(im: Image.Image) -> bytes:
    """Uncompressed B8G8R8A8_UNORM_SRGB (DXGI 91) single level - what MS2 uses for UI icons."""
    im = im.convert("RGBA")
    w, h = im.size
    r, g, b, a = im.split()
    raw = Image.merge("RGBA", (b, g, r, a)).tobytes()
    hdr = struct.pack("<4sIIIIIII", b"DDS ", 124, 0x100F, h, w, w * 4, 0, 1)
    hdr += b"\0" * 44
    hdr += struct.pack("<II4sIIIII", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    hdr += struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)
    hdr += struct.pack("<IIIII", 91, 3, 0, 1, 0)
    return hdr + raw


def load_image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im.convert("RGBA")


def is_pot(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


# --- sizes -----------------------------------------------------------------
def scaled_size(src_size, long_side: int):
    """Keep the aspect ratio (4096x2048 templates exist), long side = long_side."""
    w, h = src_size
    if w >= h:
        return long_side, max(1, round(long_side * h / w))
    return max(1, round(long_side * w / h)), long_side


def _as_size(size, template):
    if size is None:
        return template.size
    if isinstance(size, int):
        return scaled_size(template.size, size)
    return tuple(size)


# --- template guides -------------------------------------------------------
def _unused_mask(template: Image.Image, work: int = 1024) -> Image.Image:
    """L mask (255 = unused UV space), long side = `work`.

    Jon-style templates: white parts + black wireframe on black background. Remove thin
    lines with a morphological opening, then flood-fill the black that touches the border.
    """
    ww, wh = scaled_size(template.size, work)
    small = template.convert("L").resize((ww, wh), Image.Resampling.BOX)
    black = small.point(lambda v: 255 if v < 48 else 0)
    opened = black.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    canvas = opened.copy()
    px = canvas.load()
    seeds = [(x, 0) for x in range(ww)] + [(x, wh - 1) for x in range(ww)] + \
            [(0, y) for y in range(wh)] + [(ww - 1, y) for y in range(wh)]
    for s in seeds:
        if px[s] == 255:
            ImageDraw.floodfill(canvas, s, 128)
    return canvas.point(lambda v: 255 if v == 128 else 0)


def make_guide(template: Image.Image, size=None, unused_alpha: int = 150,
               downscaled: bool = False) -> Image.Image:
    """Transparent overlay: wireframe lines + shaded unused areas. Put it as the top layer.
    `size` = long side (int) or (w, h); default = template size."""
    W, H = _as_size(size, template)
    t = template.convert("L")
    if t.size != (W, H):
        t = t.resize((W, H), Image.Resampling.LANCZOS)
    unused = _unused_mask(template).resize((W, H), Image.Resampling.BILINEAR)
    thr = 185 if (downscaled or W < template.size[0]) else 110   # downscaled lines turn grey
    lines = t.point(lambda v: 255 if v < thr else 0)
    lines = ImageChops.subtract(lines, unused)
    a_lines = lines.point(lambda v: 210 if v > 0 else 0)
    a_unused = unused.point(lambda v: int(v * unused_alpha / 255))
    alpha = ImageChops.lighter(a_lines, a_unused)
    # colour: lines near-black, unused dark slate
    rgb = Image.new("RGB", (W, H), (34, 38, 46))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    rgb = Image.composite(black, rgb, lines)
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def fit_art(art: Image.Image, size) -> Image.Image:
    """size: (w, h) or long side."""
    size = scaled_size(art.size, size) if isinstance(size, int) else tuple(size)
    if art.size != size:
        art = art.resize(size, Image.Resampling.LANCZOS)
    return art


def preview(template: Image.Image, art: Image.Image | None, color=(200, 200, 200), size=1024,
            guide_strength: float = 0.55, guide: Image.Image | None = None) -> Image.Image:
    W, H = scaled_size(template.size, size)
    base = checker((W, H)) if color is None else Image.new("RGBA", (W, H), tuple(color) + (255,))
    if art is not None:
        base.alpha_composite(art.resize((W, H), Image.Resampling.LANCZOS) if art.size != (W, H) else art)
    g = guide if guide is not None else make_guide(template, (W, H), unused_alpha=215)
    if g.size != (W, H):
        g = g.resize((W, H), Image.Resampling.LANCZOS)
    if guide_strength < 1:
        # keep unused-space shading solid, fade only the line art so the artwork stays readable
        r = g.getchannel("R")
        a = g.getchannel("A")
        unused = r.point(lambda v: 255 if v > 10 else 0)
        faded = a.point(lambda v: int(v * guide_strength))
        a = Image.composite(a, faded, unused)
        g = g.copy()
        g.putalpha(a)
    base.alpha_composite(g)
    return base.convert("RGB")


def checker(size, cell=32):
    """Grey checkerboard = 'no paint colour' (shows exactly what is transparent)."""
    W, H = size
    im = Image.new("RGBA", (W, H), (150, 150, 150, 255))
    d = ImageDraw.Draw(im)
    for y in range(0, H, cell):
        for x in range((y // cell) % 2 * cell, W, cell * 2):
            d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(205, 205, 205, 255))
    return im


def png_bytes(im: Image.Image, optimize=False) -> bytes:
    b = io.BytesIO()
    im.save(b, format="PNG", optimize=optimize, compress_level=1 if not optimize else 6)
    return b.getvalue()
