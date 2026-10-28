"""Read-only access to SCS model geometry (.pmg 0x15) and model materials (.pmd).

Layout from ConverterPIX (mwl4). Note: the pmg_piece_t offsets in its comments are wrong;
the packed declaration order is the real layout (bone index/weight before the index offset).
Only what SkinForge needs: positions, normals, UV sets and triangles per piece.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass


class ModelError(Exception):
    pass


@dataclass
class Piece:
    material: int
    pos: list
    nrm: list
    uvs: list      # [uv set][vertex] -> (u, v)
    tris: list     # [(a, b, c)]


def read_pmg(data: bytes) -> list[Piece]:
    if len(data) < 100 or data[1:4] != b"gmP":
        raise ModelError("not a .pmg model")
    if data[0] != 0x15:
        raise ModelError(f"unsupported .pmg version {data[0]:#x} (SkinForge reads 0x15, game 1.40+)")
    piece_count = struct.unpack_from("<i", data, 4)[0]
    pieces_off = struct.unpack_from("<i", data, 84)[0]
    out = []
    for i in range(piece_count):
        b = pieces_off + 100 * i
        edges, verts, _tc_mask, tc_width, material = struct.unpack_from("<iiIii", data, b)
        (_skel, pos_o, nrm_o, tc_o, col_o, fac_o, tan_o, bidx_o, _bw_o, idx_o) = struct.unpack_from("<10i", data, b + 60)
        stride = sum(s for o, s in ((pos_o, 12), (nrm_o, 12), (tan_o, 16), (tc_o, 8 * tc_width),
                                    (col_o, 4), (fac_o, 4), (bidx_o, 8)) if o != -1)
        pos = [struct.unpack_from("<3f", data, pos_o + stride * j) for j in range(verts)] if pos_o != -1 else []
        nrm = [struct.unpack_from("<3f", data, nrm_o + stride * j) for j in range(verts)] if nrm_o != -1 else []
        uvs = [[struct.unpack_from("<2f", data, tc_o + stride * j + 8 * k) for j in range(verts)]
               for k in range(tc_width)] if tc_o != -1 else []
        tris = [struct.unpack_from("<3H", data, idx_o + 6 * t) for t in range(edges // 3)]
        out.append(Piece(material, pos, nrm, uvs, tris))
    return out


def read_pmd_materials(data: bytes, look: int = 0) -> list[str]:
    """Material paths of one look (index = piece material id)."""
    if len(data) < 60:
        raise ModelError("not a .pmd file")
    _ver, mat_count, look_count = struct.unpack_from("<3I", data, 0)
    look = min(look, max(0, look_count - 1))
    mat_off = struct.unpack_from("<I", data, 56)[0]
    out = []
    for i in range(mat_count):
        o = struct.unpack_from("<I", data, mat_off + 4 * (look * mat_count + i))[0]
        end = data.index(b"\0", o)
        out.append(data[o:end].decode("utf-8", "replace"))
    return out


_EFFECT = re.compile(rb'(?:effect|material)\s*:\s*"([^"]+)"')   # new format / older "material : "eut2..." {"


def mat_effect(data: bytes | None) -> str:
    if not data:
        return ""
    m = _EFFECT.search(data[:400])
    return m.group(1).decode("ascii", "replace") if m else ""


def is_paintable(effect: str) -> bool:
    """Pieces that take the paint job texture (sampled with the second UV set)."""
    return "truckpaint" in effect
