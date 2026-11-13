#!/usr/bin/env python3
"""Make content/legislators/zip-districts.json: each Illinois ZIP code (Census ZCTA) with the Illinois House and Senate
districts it falls in and the share of the ZIP's area in each. The website's "Find your legislators" page reads it, in
the visitor's browser, so a ZIP code typed there never leaves their computer.

Run it again only when district lines change (after redistricting) or for a newer Census release:

    pip install shapely pyshp
    mkdir -p build/zip && cd build/zip
    curl -O https://www2.census.gov/geo/tiger/TIGER2025/SLDL/tl_2025_17_sldl.zip
    curl -O https://www2.census.gov/geo/tiger/TIGER2025/SLDU/tl_2025_17_sldu.zip
    curl -O https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip
    for f in *.zip; do unzip -oq $f -d ${f%.zip}; done; cd ../..
    python pipeline/zip_districts.py build/zip content/legislators/zip-districts.json

Sources: U.S. Census Bureau TIGER/Line state legislative districts (Illinois) and the 2020 ZCTA cartographic
boundaries. Shares of under 2% are dropped (slivers along district lines). "il" is given when part of the ZIP area is
outside Illinois.
"""
import json, sys
import shapefile
from shapely.geometry import shape
from shapely.strtree import STRtree
from shapely.ops import unary_union

DL, OUT = sys.argv[1], sys.argv[2]

def read(path, key):
    r = shapefile.Reader(path)
    names = [f[0] for f in r.fields[1:]]
    out = []
    for sr in r.iterShapeRecords():
        rec = dict(zip(names, sr.record))
        out.append((rec[key], shape(sr.shape.__geo_interface__).buffer(0)))
    return out

house = [(int(k), g) for k, g in read(f'{DL}/tl_2025_17_sldl/tl_2025_17_sldl', 'SLDLST') if k.strip('Z').isdigit()]
senate = [(int(k), g) for k, g in read(f'{DL}/tl_2025_17_sldu/tl_2025_17_sldu', 'SLDUST') if k.strip('Z').isdigit()]
print(len(house), 'House districts,', len(senate), 'Senate districts')
il = unary_union([g for _, g in senate])
minx, miny, maxx, maxy = il.bounds

zr = shapefile.Reader(f'{DL}/cb_2020_us_zcta520_500k/cb_2020_us_zcta520_500k')
znames = [f[0] for f in zr.fields[1:]]
zips = []
for sr in zr.iterShapeRecords():
    bx = sr.shape.bbox
    if bx[2] < minx or bx[0] > maxx or bx[3] < miny or bx[1] > maxy:
        continue
    rec = dict(zip(znames, sr.record))
    zips.append((rec['ZCTA5CE20'], shape(sr.shape.__geo_interface__).buffer(0)))
print(len(zips), 'ZIP areas near Illinois')

def shares(zg, dists, tree):
    a = zg.area; got = []
    for i in tree.query(zg):
        d, g = dists[i]; x = zg.intersection(g).area / a
        if x >= 0.02: got.append([d, round(x, 2)])
    return sorted(got, key=lambda t: -t[1])

ht, st = STRtree([g for _, g in house]), STRtree([g for _, g in senate])
table = {}
for z, zg in zips:
    inside = zg.intersection(il).area / zg.area
    if inside < 0.05:
        continue
    row = {'h': shares(zg, house, ht), 's': shares(zg, senate, st)}
    if inside < 0.95: row['il'] = round(inside, 2)
    if row['h'] or row['s']: table[z] = row
print(len(table), 'ZIP codes with an Illinois district;', sum(1 for r in table.values() if len(r['h']) > 1), 'split between House districts,', sum(1 for r in table.values() if len(r['s']) > 1), 'between Senate districts')
json.dump(table, open(OUT, 'w'), separators=(',', ':'), sort_keys=True)
