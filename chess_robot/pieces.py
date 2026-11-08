"""スタントン型チェス駒のメッシュを作る

キング・クイーン・ビショップ・ルーク・ポーンは断面の輪郭を回転させた回転体（旋盤で削る本物の駒と同じ作り方）、
ナイトは馬の頭の横顔を押し出して作る。単位はメートル。
すべての駒の根元は半径6.5mm・高さ約8mmの台座で、アームはここをつかむ（衝突判定も同じ寸法）。
"""
from pathlib import Path

import numpy as np

BASE_R = 0.0065
BASE_H = 0.008
SEG = 64


def _arc(cz, rad, t0, t1, n=14, sx=1.0):
    """回転体の輪郭用の円弧（cz: 中心の高さ、t: 角度[度]、-90が下端、90が上端）。sx で横方向に潰す"""
    t = np.radians(np.linspace(t0, t1, n))
    return [(cz + rad * np.sin(a), sx * rad * np.cos(a)) for a in t]


def _base():
    # 台座：ふち・くびれ・ふくらみ。根元の半径は BASE_R
    return [(0.0, 0.0), (0.0, 0.0060), (0.0006, BASE_R), (0.0032, BASE_R), (0.0038, 0.0060),
            (0.0046, 0.0060), (0.0054, BASE_R), (0.0072, BASE_R), (0.0080, 0.0057)]


PROFILES = {
    "p": _base() + [(0.0090, 0.0046), (0.0118, 0.0034), (0.0134, 0.0031), (0.0138, 0.0045),
                    (0.0146, 0.0046), (0.0150, 0.0031)] + _arc(0.0182, 0.0041, -45, 90),
    "r": _base() + [(0.0090, 0.0051), (0.0120, 0.0046), (0.0190, 0.0044), (0.0198, 0.0050),
                    (0.0206, 0.0057), (0.0256, 0.0057), (0.0259, 0.0054), (0.0259, 0.0041),
                    (0.0240, 0.0039), (0.0238, 0.0)],
    "b": _base() + [(0.0090, 0.0047), (0.0130, 0.0035), (0.0158, 0.0030), (0.0162, 0.0045),
                    (0.0170, 0.0045), (0.0174, 0.0032)] + _arc(0.0220, 0.0048, -45, 75, sx=0.85)
                    + [(0.0272, 0.0010)] + _arc(0.0287, 0.0016, -60, 90),
    "q": _base() + [(0.0090, 0.0049), (0.0140, 0.0036), (0.0190, 0.0031), (0.0196, 0.0044),
                    (0.0204, 0.0044), (0.0208, 0.0032), (0.0245, 0.0040), (0.0270, 0.0055),
                    (0.0282, 0.0058), (0.0290, 0.0050), (0.0294, 0.0042)] + _arc(0.0294, 0.0042, 0, 70, 7)
                    + _arc(0.0345, 0.0016, -40, 90),
    "k": _base() + [(0.0090, 0.0050), (0.0150, 0.0037), (0.0205, 0.0032), (0.0211, 0.0046),
                    (0.0219, 0.0046), (0.0223, 0.0033), (0.0262, 0.0042), (0.0285, 0.0053),
                    (0.0294, 0.0054), (0.0298, 0.0046)] + _arc(0.0298, 0.0046, 0, 90, 8),
    "n": _base() + [(0.0088, 0.0052), (0.0092, 0.0040), (0.0094, 0.0)],
}
HEIGHT = {"p": 0.0223, "r": 0.0259, "b": 0.0303, "q": 0.0361, "k": 0.0405, "n": 0.0284}

# ナイトの頭の横顔（前向き = +u、上 = +v）[mm]
KNIGHT_HEAD = [(-4.6, 0.0), (4.2, 0.0), (4.6, 2.5), (6.2, 5.4), (8.8, 7.2), (9.8, 9.0), (9.2, 10.6),
               (7.4, 11.4), (5.0, 11.0), (4.0, 12.6), (3.0, 15.4), (1.2, 17.8), (0.2, 19.6),
               (-0.6, 18.0), (-1.8, 19.4), (-2.6, 17.4), (-4.4, 15.2), (-5.8, 11.6), (-6.2, 7.0),
               (-5.8, 3.0)]
KNIGHT_THICK = 0.0082
KNIGHT_Z0 = 0.0088


def lathe(profile, seg=SEG):
    pts = np.array(profile)
    verts, faces = [], []
    ring_index = []
    for z, r in pts:
        if r < 1e-9:
            ring_index.append([len(verts)] * seg)
            verts.append((0.0, 0.0, z))
        else:
            idx = []
            for k in range(seg):
                a = 2 * np.pi * k / seg
                idx.append(len(verts))
                verts.append((r * np.cos(a), r * np.sin(a), z))
            ring_index.append(idx)
    for i in range(len(ring_index) - 1):
        a, b = ring_index[i], ring_index[i + 1]
        for k in range(seg):
            k2 = (k + 1) % seg
            tri1 = (a[k], a[k2], b[k2])
            tri2 = (a[k], b[k2], b[k])
            for t in (tri1, tri2):
                if len(set(t)) == 3:
                    faces.append(t)
    return np.array(verts), np.array(faces)


def _ear_clip(poly):
    """単純多角形（反時計回り）を三角形に分ける"""
    idx = list(range(len(poly)))
    P = np.array(poly)
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for j in range(len(idx)):
            i0, i1, i2 = idx[j - 1], idx[j], idx[(j + 1) % len(idx)]
            a, b, c = P[i0], P[i1], P[i2]
            if cross(a, b, c) <= 0:
                continue
            if any(inside(P[m], a, b, c) for m in idx if m not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(j)
            break
    tris.append(tuple(idx))
    return tris


def knight_head(layers=6, bevel=0.0019):
    """馬の頭：横顔を、ふちを丸めながら押し出す（枕のような断面）。鼻先ほど細くする"""
    poly = [(u / 1000, v / 1000) for u, v in KNIGHT_HEAD]
    area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly)))
    if area < 0:
        poly = poly[::-1]
    n = len(poly)
    cx = np.mean([p[0] for p in poly]); cy = np.mean([p[1] for p in poly]) + 0.001
    h = KNIGHT_THICK / 2

    def taper(u):                                   # 後頭部は太く、鼻先は細く
        t = np.clip((u + 0.004) / 0.013, 0, 1)
        return 1.0 - 0.38 * t

    def ring(inset, y):
        out = []
        for u, v in poly:
            d = np.hypot(u - cx, v - cy)
            s = max(d - inset, 1e-4) / d
            uu, vv = cx + (u - cx) * s, cy + (v - cy) * s
            out.append((uu, y * taper(uu), vv + KNIGHT_Z0))
        return out

    rings = []
    for k in range(layers, -1, -1):                 # -y 側の面 → 側面 → +y 側の面
        phi = k / layers * np.pi / 2
        rings.append(ring(bevel * (1 - np.cos(phi)), -(h - bevel + bevel * np.sin(phi))))
    for k in range(0, layers + 1):
        phi = k / layers * np.pi / 2
        rings.append(ring(bevel * (1 - np.cos(phi)), +(h - bevel + bevel * np.sin(phi))))
    verts = [v for r in rings for v in r]
    faces = []
    for L in range(len(rings) - 1):
        for i in range(n):
            j = (i + 1) % n
            a, b = L * n + i, L * n + j
            c, d = (L + 1) * n + j, (L + 1) * n + i
            faces.append((a, b, c)); faces.append((a, c, d))
    cap = _ear_clip([(p[0], p[2]) for p in rings[0]])
    last = (len(rings) - 1) * n
    for a, b, c in cap:
        faces.append((a, c, b))
        faces.append((last + a, last + b, last + c))
    return np.array(verts), np.array(faces)


def write_obj(path, verts, faces):
    with open(path, "w") as f:
        for v in verts:
            f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
        for t in faces:
            f.write(f"f {t[0] + 1} {t[1] + 1} {t[2] + 1}\n")


def build_meshes(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for kind, prof in PROFILES.items():
        v, f = lathe(prof)
        p = out_dir / f"chess_{kind}.obj"
        write_obj(p, v, f)
        files[kind] = p.name
    v, f = knight_head()
    p = out_dir / "chess_n_head.obj"
    write_obj(p, v, f)
    files["n_head"] = p.name
    return files
