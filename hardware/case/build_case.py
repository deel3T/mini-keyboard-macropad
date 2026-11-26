"""Builds the Mini Keyboard case (lid + base) as clean, watertight STL files.

Units are millimetres. Change the numbers below and run:

    pip install manifold3d trimesh numpy
    python build_case.py

Base (68 x 68 x 12): 2 mm floor and walls, outer corners R3. A 3 mm lip on top
takes the lid's skirt; all inner corners are concentric with the outer R3
corners, so walls keep their thickness around the corners. 11 x 4 mm USB opening in the
front wall.

Lid (68 x 68 x 8, printed plate-side down): 1.5 mm plate at the 14 x 14 mm MX
switch cut-outs (the MX clip spec) on a 19 mm grid, 18 x 18 mm pockets above
them, a 2 mm frame and a skirt that slides over the base's lip.

FIT_GAP is the clearance between the lid's skirt and the base's lip on each
side, split evenly between the two parts.

Kasa ölçüleri milimetredir; sayıları değiştirip çalıştırarak kasayı yeniden
üretebilirsin. Lid = kapak, base = taban.
"""
import os

import manifold3d as mf
import numpy as np
import trimesh

SIZE = 68.0          # outer width / depth
CORNER_R = 3.0       # outer corner radius
SEG = 48             # segments per quarter circle
PITCH = 19.0         # key spacing
FIRST_KEY = 15.0     # centre of the first key from the edge
SWITCH_HOLE = 14.0   # MX plate cut-out
KEY_POCKET = 18.0
PLATE = 1.5          # plate thickness at the switch cut-outs (MX spec)
FIT_GAP = 0.2        # skirt <-> lip clearance per side

mf.set_circular_segments(SEG * 4)


def rounded_square(size, r):
    pts = []
    for cx, cy, a0 in ((size - r, r, -90), (size - r, size - r, 0), (r, size - r, 90), (r, r, 180)):
        for i in range(SEG + 1):
            a = np.radians(a0 + 90 * i / SEG)
            pts.append((cx + r * np.cos(a), cy + r * np.sin(a)))
    return mf.CrossSection([pts])


def square(x0, y0, x1, y1):
    return mf.CrossSection([[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]])


def inset(offset):
    """Outline `offset` mm inside the outer edge. Its corner arcs share the
    outer corner's centre, so every wall keeps the same thickness in the
    corners too (a chamfer there left the skirt only ~0.4 mm thick)."""
    return rounded_square(SIZE - 2 * offset, CORNER_R - offset).translate([offset, offset])


def extrude(cs, z0, z1):
    return mf.Manifold.extrude(cs, z1 - z0).translate([0, 0, z0])


OUTER = rounded_square(SIZE, CORNER_R)
LIP_OUTER = inset(1.0 + FIT_GAP / 2)
SKIRT_INNER = inset(1.0 - FIT_GAP / 2)
CAVITY = inset(2.0)
CENTERS = [FIRST_KEY + i * PITCH for i in range(3)]


def base():
    body = extrude(OUTER, 0, 9) + extrude(LIP_OUTER - CAVITY, 9, 12)
    body = body - extrude(CAVITY, 2, 12.5)
    usb = mf.Manifold.cube([11.0, 3.0, 4.0]).translate([6.0, -1.0, 3.0])
    return body - usb


def lid():
    holes = pockets = None
    for cx in CENTERS:
        for cy in CENTERS:
            h = square(cx - SWITCH_HOLE / 2, cy - SWITCH_HOLE / 2, cx + SWITCH_HOLE / 2, cy + SWITCH_HOLE / 2)
            p = square(cx - KEY_POCKET / 2, cy - KEY_POCKET / 2, cx + KEY_POCKET / 2, cy + KEY_POCKET / 2)
            holes = h if holes is None else holes + h
            pockets = p if pockets is None else pockets + p
    return (extrude(OUTER - holes, 0, PLATE) + extrude(OUTER - pockets, PLATE, 3)
            + extrude(OUTER - CAVITY, 3, 5) + extrude(OUTER - SKIRT_INNER, 5, 8))


def save(manifold, path):
    m = manifold.to_mesh()
    mesh = trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts), process=True)
    assert mesh.is_watertight and mesh.is_winding_consistent
    mesh.export(path)
    print(f"{os.path.basename(path)}: {mesh.volume:.0f} mm3, {len(mesh.faces)} triangles")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    save(base(), os.path.join(here, "base.stl"))
    save(lid(), os.path.join(here, "lid.stl"))
