# Maps

Kist's map (`GET /api/map`, the web's map view) needs two things, both
self-hosted by default so no request about your photos leaves your network.

## Place names (reverse geocoding)

```bash
docker compose exec worker kist geo-download
```

downloads GeoNames' `cities500.zip` (every place with at least 500 people,
about 10 MB), `countryInfo.txt` and `admin1CodesASCII.txt` from
https://download.geonames.org/export/dump/ once, and writes
`library/.kist/geo/places.tsv`. Photos and videos with GPS are then named
"City, Country" (searchable, and the `place=` filter), offline. It names the
places of existing items at the end; new items get theirs on import. Run it
again now and then for updated names. GeoNames data is CC BY 4.0: credit
"GeoNames" where you show place names publicly.

Without network access on the server, download the three files elsewhere and
run `kist geo-download --from /library/import/geonames`.

## Map tiles

The default is a Protomaps basemap in one PMTiles file that you host:

1. Download a build from https://maps.protomaps.com/builds/ (the whole
   planet is ~120 GB; extract just your region with the `pmtiles` CLI:
   `pmtiles extract https://build.protomaps.com/YYYYMMDD.pmtiles region.pmtiles
   --bbox=WEST,SOUTH,EAST,NORTH --maxzoom=14`).
2. Put it at `library/maps/basemap.pmtiles`.

Or use a hosted provider: set `KIST_MAP_TILES_URL` (an XYZ template such as
`https://tiles.example.com/{z}/{x}/{y}.png`, or a `.pmtiles` URL) and
`KIST_MAP_ATTRIBUTION` (the credit line the provider requires). A hosted
provider sees which areas you look at. `GET /api/map/config` tells the web
which one to use.
