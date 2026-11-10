"""3D models (Milestone 5): bounds and polycount, and GLB conversion.

Thumbnails are made in the browser: the web shows the model with
<model-viewer> and uploads a poster (POST /api/items/{id}/poster). Until
then the item has a generic icon.

stats() reads what is cheap to read without a 3D library:
- glTF / GLB: meshes, primitives, triangles (index or POSITION counts) and
  bounds from the POSITION accessors' min/max (the spec requires them);
- OBJ: vertex and face counts, bounds from the `v` lines;
- STL: triangle count (binary header or ascii `facet` lines) and bounds;
- PLY: vertex and face counts from the header;
- FBX and USDZ: none (FBX conversion isn't supported).

to_glb() converts OBJ, STL and PLY to items/<id>/model.glb with trimesh,
only when trimesh is installed (the `media` extra); otherwise it's skipped.
"""

import json
import struct
import zipfile
from pathlib import Path

MAX_PARSE_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024 * 1024

FORMATS = {
    "model/gltf-binary": "glb", "model/gltf+json": "gltf", "model/obj": "obj",
    "model/stl": "stl", "model/x-ply": "ply", "model/x-fbx": "fbx",
    "model/vnd.usdz+zip": "usdz",
}
CONVERTIBLE = ("obj", "stl", "ply")


def _gltf_stats(doc: dict) -> dict:
    accessors = doc.get("accessors") or []
    tris = 0
    prims = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    vertices = 0
    for mesh in doc.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            prims += 1
            mode = prim.get("mode", 4)
            pos_i = (prim.get("attributes") or {}).get("POSITION")
            pos = accessors[pos_i] if isinstance(pos_i, int) and pos_i < len(accessors) else {}
            idx_i = prim.get("indices")
            idx = accessors[idx_i] if isinstance(idx_i, int) and idx_i < len(accessors) else None
            count = int((idx or pos).get("count") or 0)
            vertices += int(pos.get("count") or 0)
            if mode == 4:
                tris += count // 3
            elif mode in (5, 6):
                tris += max(0, count - 2)
            mn, mx = pos.get("min"), pos.get("max")
            if isinstance(mn, list) and isinstance(mx, list) and len(mn) == 3 and len(mx) == 3:
                lo = [min(a, float(b)) for a, b in zip(lo, mn, strict=True)]
                hi = [max(a, float(b)) for a, b in zip(hi, mx, strict=True)]
    out: dict = {
        "meshes": len(doc.get("meshes") or []),
        "primitives": prims,
        "triangles": tris,
        "vertices": vertices,
        "materials": len(doc.get("materials") or []),
        "textures": len(doc.get("textures") or []),
        "animations": len(doc.get("animations") or []),
    }
    if lo[0] != float("inf"):
        out["bounds"] = {"min": lo, "max": hi}
    gen = (doc.get("asset") or {}).get("generator")
    if isinstance(gen, str):
        out["generator"] = gen[:200]
    return out


def _glb_json(path: Path) -> dict:
    with path.open("rb") as f:
        head = f.read(20)
        magic, _version, _length, chunk_len, chunk_type = struct.unpack("<4sIIII", head)
        if magic != b"glTF" or chunk_type != 0x4E4F534A or chunk_len > MAX_JSON_BYTES:
            raise ValueError("not a GLB file")
        return json.loads(f.read(chunk_len))


def _bounds_update(lo, hi, x, y, z):
    if x < lo[0]:
        lo[0] = x
    if y < lo[1]:
        lo[1] = y
    if z < lo[2]:
        lo[2] = z
    if x > hi[0]:
        hi[0] = x
    if y > hi[1]:
        hi[1] = y
    if z > hi[2]:
        hi[2] = z


def _obj_stats(path: Path) -> dict:
    v = f = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    read = 0
    with path.open("rb") as fh:
        for line in fh:
            read += len(line)
            if read > MAX_PARSE_BYTES:
                break
            if line.startswith(b"v "):
                v += 1
                parts = line.split()
                try:
                    _bounds_update(lo, hi, float(parts[1]), float(parts[2]), float(parts[3]))
                except (IndexError, ValueError):
                    pass
            elif line.startswith(b"f "):
                f += max(1, len(line.split()) - 3)  # polygons as triangles
    out: dict = {"vertices": v, "triangles": f}
    if v and lo[0] != float("inf"):
        out["bounds"] = {"min": lo, "max": hi}
    return out


def _stl_stats(path: Path) -> dict:
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(80)
        count = int.from_bytes(fh.read(4), "little")
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        if size == 84 + 50 * count:
            if size <= MAX_PARSE_BYTES:
                for _ in range(count):
                    rec = fh.read(50)
                    vals = struct.unpack("<12f", rec[:48])
                    for i in (3, 6, 9):
                        _bounds_update(lo, hi, vals[i], vals[i + 1], vals[i + 2])
            out: dict = {"triangles": count}
        else:
            fh.seek(0)
            facets = 0
            read = 0
            for line in fh:
                read += len(line)
                if read > MAX_PARSE_BYTES:
                    break
                s = line.strip()
                if s.startswith(b"facet"):
                    facets += 1
                elif s.startswith(b"vertex"):
                    p = s.split()
                    try:
                        _bounds_update(lo, hi, float(p[1]), float(p[2]), float(p[3]))
                    except (IndexError, ValueError):
                        pass
            out = {"triangles": facets}
    if lo[0] != float("inf"):
        out["bounds"] = {"min": lo, "max": hi}
    return out


def _ply_stats(path: Path) -> dict:
    out: dict = {}
    with path.open("rb") as fh:
        for _ in range(200):
            line = fh.readline().strip()
            if line == b"end_header" or not line:
                break
            parts = line.split()
            if len(parts) == 3 and parts[0] == b"element":
                try:
                    n = int(parts[2])
                except ValueError:
                    continue
                if parts[1] == b"vertex":
                    out["vertices"] = n
                elif parts[1] == b"face":
                    out["triangles"] = n
    return out


def stats(path: Path, mime: str) -> dict:
    fmt = FORMATS.get(mime)
    out: dict = {"format": fmt}
    try:
        if fmt == "glb":
            out.update(_gltf_stats(_glb_json(path)))
        elif fmt == "gltf":
            if path.stat().st_size <= MAX_JSON_BYTES:
                out.update(_gltf_stats(json.loads(path.read_bytes())))
                doc = json.loads(path.read_bytes())
                out["external_buffers"] = any(
                    not str(b.get("uri", "data:")).startswith("data:")
                    for b in doc.get("buffers") or []
                )
        elif fmt == "obj":
            out.update(_obj_stats(path))
        elif fmt == "stl":
            out.update(_stl_stats(path))
        elif fmt == "ply":
            out.update(_ply_stats(path))
        elif fmt == "usdz":
            with zipfile.ZipFile(path) as zf:
                out["files"] = len(zf.infolist())
    except (OSError, ValueError, KeyError, struct.error, zipfile.BadZipFile) as exc:
        out["error"] = str(exc)[:200]
    if "bounds" in out:
        b = out["bounds"]
        out["size"] = [round(b["max"][i] - b["min"][i], 6) for i in range(3)]
    return out


def trimesh_available() -> bool:
    try:
        import trimesh  # type: ignore[import-not-found] # noqa: F401
    except ImportError:
        return False
    return True


def to_glb(path: Path, fmt: str, out: Path) -> bool:
    """OBJ/STL/PLY -> GLB with trimesh. False when it can't be done here."""
    if fmt not in CONVERTIBLE or not trimesh_available():
        return False
    import trimesh  # type: ignore[import-not-found]

    scene = trimesh.load(str(path), file_type=fmt, force="scene", process=False)
    data = scene.export(file_type="glb")
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(out)
    return True
