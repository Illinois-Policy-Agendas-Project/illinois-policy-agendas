#!/usr/bin/env python3
"""Make content/legislators/district-map.json: outlines of the Illinois House and Senate districts for the map on the
website's "Find your legislators" page, as SVG path data on a flat map of Illinois, plus the projection the page uses
to place an address on the same map.

Run it again only when district lines change (after redistricting) or for a newer Census release:

    pip install shapely pyshp numpy
    mkdir -p build/zip && cd build/zip
    curl -O https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_17_sldl_500k.zip
    curl -O https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_17_sldu_500k.zip
    for f in *.zip; do unzip -oq $f -d ${f%.zip}; done; cd ../..
    python pipeline/district_map.py build/zip content/legislators/district-map.json

Source: U.S. Census Bureau cartographic boundary files (1:500,000), which follow the shoreline, so districts on
Lake Michigan stop at the water. Projection: longitude scaled by the cosine of Illinois' middle latitude, which keeps
shapes true enough at the scale of one state; the page uses the same formula (proj in the file) for the address pin.
"""
import json, math, sys
import numpy as np
import shapefile
from shapely.geometry import shape, Polygon, MultiPolygon
from shapely import transform as shp_transform

DL, OUT = sys.argv[1], sys.argv[2]
H = 1000.0          # map height in map units
TOL = 0.12          # simplification, in map units (about 80 m)


def read(path, key):
    r = shapefile.Reader(path)
    names = [f[0] for f in r.fields[1:]]
    for sr in r.iterShapeRecords():
        rec = dict(zip(names, sr.record))
        k = str(rec[key]).strip()
        if k.isdigit():
            yield int(k), shape(sr.shape.__geo_interface__)


house = dict(read(f'{DL}/cb_2024_17_sldl_500k/cb_2024_17_sldl_500k', 'SLDLST'))
senate = dict(read(f'{DL}/cb_2024_17_sldu_500k/cb_2024_17_sldu_500k', 'SLDUST'))
minx = min(g.bounds[0] for g in senate.values()); maxx = max(g.bounds[2] for g in senate.values())
miny = min(g.bounds[1] for g in senate.values()); maxy = max(g.bounds[3] for g in senate.values())
cos0 = math.cos(math.radians((miny + maxy) / 2)); k = H / (maxy - miny)
proj = {'lon0': round(minx, 6), 'lat0': round(maxy, 6), 'cos': round(cos0, 6), 'k': round(k, 6)}
W = (maxx - minx) * cos0 * k


def to_map(x, y, z=None):
    return ((x - minx) * cos0 * k, (maxy - y) * k)


def path(g):
    g = shp_transform(g, lambda a: np.column_stack(to_map(a[:, 0], a[:, 1]))).simplify(TOL, preserve_topology=True)
    polys = g.geoms if isinstance(g, MultiPolygon) else [g] if isinstance(g, Polygon) else []
    out = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            pts = [(round(x, 1), round(y, 1)) for x, y in ring.coords[:-1]]
            if len(pts) < 3:
                continue
            d = f'M{pts[0][0]:g} {pts[0][1]:g}l'
            px, py = pts[0]; segs = []
            for x, y in pts[1:]:
                dx, dy = round(x - px, 1), round(y - py, 1)
                if dx or dy:
                    segs.append(f'{dx:g} {dy:g}'.replace(' -', '-')); px, py = px + dx, py + dy
            out.append(d + ' '.join(segs) + 'z')
    return ''.join(out)


m = {'proj': proj, 'w': round(W, 1), 'h': H,
     'house': {d: path(g) for d, g in sorted(house.items())},
     'senate': {d: path(g) for d, g in sorted(senate.items())}}
json.dump(m, open(OUT, 'w'), separators=(',', ':'))
print(len(m['house']), 'House and', len(m['senate']), 'Senate districts; map', m['w'], 'x', H)
