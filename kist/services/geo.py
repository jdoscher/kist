"""Offline reverse geocoding (Milestone 5): GPS -> "City, Country".

`kist geo-download` fetches GeoNames' cities500 extract (every place with
500+ people, CC BY 4.0, https://www.geonames.org/) plus the country and
region names, once, into library/.kist/geo/. Nothing is shipped in the
image and no request leaves the server when a photo is looked up: the
nearest place within MAX_KM is found in memory with a 1-degree grid.
"""

import io
import logging
import math
import threading
import zipfile
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("kist.geo")

BASE_URL = "https://download.geonames.org/export/dump/"
FILES = ("cities500.zip", "countryInfo.txt", "admin1CodesASCII.txt")
PLACES = "places.tsv"
MAX_KM = 50.0

_lock = threading.Lock()
_index: dict | None = None
_index_mtime: float | None = None


def places_file() -> Path:
    return get_settings().geo_dir / PLACES


def available() -> bool:
    return places_file().is_file()


def _read_cities(raw: bytes) -> list[list[str]]:
    rows = []
    for line in raw.decode("utf-8", "replace").splitlines():
        cols = line.split("\t")
        if len(cols) < 15:
            continue
        rows.append(cols)
    return rows


def build(cities: bytes, country_info: bytes, admin1: bytes, out: Path) -> int:
    """Write the compact places.tsv: name, lat, lon, cc, region, country."""
    countries = {}
    for line in country_info.decode("utf-8", "replace").splitlines():
        if line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) > 4:
            countries[cols[0]] = cols[4]
    regions = {}
    for line in admin1.decode("utf-8", "replace").splitlines():
        cols = line.split("\t")
        if len(cols) > 1:
            regions[cols[0]] = cols[1]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8") as f:
        for cols in _read_cities(cities):
            try:
                lat, lon = float(cols[4]), float(cols[5])
            except ValueError:
                continue
            cc = cols[8]
            region = regions.get(f"{cc}.{cols[10]}", "")
            f.write(f"{cols[1]}\t{lat:.5f}\t{lon:.5f}\t{cc}\t{region}\t"
                    f"{countries.get(cc, cc)}\n")
            n += 1
    tmp.replace(out)
    return n


def download(source_dir: Path | None = None) -> int:
    """Fetch (or read from source_dir) the GeoNames files and build places.tsv."""
    data: dict[str, bytes] = {}
    if source_dir is not None:
        plain = source_dir / "cities500.txt"
        for name in FILES:
            p = source_dir / name
            if not p.is_file() and name == "cities500.zip" and plain.is_file():
                p = plain
            data[name] = p.read_bytes() if p.is_file() else b""
    else:
        import httpx

        with httpx.Client(timeout=120, follow_redirects=True,
                          headers={"User-Agent": "kist geo-download"}) as client:
            for name in FILES:
                resp = client.get(BASE_URL + name)
                resp.raise_for_status()
                data[name] = resp.content
    cities = data["cities500.zip"]
    if cities[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(cities)) as zf:
            cities = zf.read("cities500.txt")
    count = build(cities, data["countryInfo.txt"], data["admin1CodesASCII.txt"], places_file())
    reset()
    return count


def reset() -> None:
    global _index, _index_mtime
    with _lock:
        _index, _index_mtime = None, None


def _load() -> dict | None:
    global _index, _index_mtime
    path = places_file()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _lock:
        if _index is not None and _index_mtime == mtime:
            return _index
        grid: dict[tuple[int, int], list[tuple[float, float, int]]] = {}
        rows: list[tuple[str, str, str, str]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 6:
                    continue
                try:
                    lat, lon = float(cols[1]), float(cols[2])
                except ValueError:
                    continue
                rows.append((cols[0], cols[3], cols[4], cols[5]))
                grid.setdefault((math.floor(lat), math.floor(lon)), []).append(
                    (lat, lon, len(rows) - 1)
                )
        _index, _index_mtime = {"grid": grid, "rows": rows}, mtime
        return _index


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def lookup(lat: float, lon: float) -> dict | None:
    """The nearest place within MAX_KM, or None (or no GeoNames data)."""
    index = _load()
    if index is None:
        return None
    best, best_km = None, MAX_KM
    cy, cx = math.floor(lat), math.floor(lon)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            x = cx + dx
            if x >= 180:
                x -= 360
            elif x < -180:
                x += 360
            for plat, plon, idx in index["grid"].get((cy + dy, x), ()):
                d = _km(lat, lon, plat, plon)
                if d < best_km:
                    best, best_km = idx, d
    if best is None:
        return None
    city, cc, region, country = index["rows"][best]
    return {"city": city, "region": region or None, "country": country, "cc": cc,
            "km": round(best_km, 1)}


def label(place: dict | None) -> str | None:
    if not place:
        return None
    parts = [p for p in (place.get("city"), place.get("country")) if p]
    return ", ".join(parts) or None
