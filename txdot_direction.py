"""
txdot_direction.py  -  add travel-direction columns to the TxDOT Roadway Inventory.

Input : the TxDOT Roadway Inventory download (the yearly .zip from
        https://www.txdot.gov/data-maps/roadway-inventory.html) or the
        TxDOT_Roadway_Linework_wAssets.shp file inside it.
Output: the same lines with these new columns
  BEAR_DRAWN  bearing of the line as drawn (increasing DFO), degrees, 0 = N, 90 = E
  DFO_DIR     INC (traffic moves toward increasing DFO), DEC (decreasing), BOTH (two-way), UNK (not known)
  TRAV_BEAR   travel bearing in degrees (blank when DFO_DIR is BOTH or UNK)
  TRAV_CMP8   travel direction, 8-point compass (N, NE, E, SE, S, SW, W, NW)
  TRAV_CMP4   travel direction, 4-point compass (N, E, S, W)
  DIR_RULE    which rule set the direction (see README)
  M_ORDER_OK  1 if the line was already drawn from low DFO to high DFO, 0 if it was reversed first

Rule: TxDOT draws every line from low DFO to high DFO. A right roadbed (RG, AG, ...) lies to the right of
that direction and a left roadbed (LG, XG, ...) to the left. With right-hand traffic, right roadbeds carry
traffic toward increasing DFO and left roadbeds toward decreasing DFO. See README.md for the full method.

Examples
  python txdot_direction.py --input 2025.zip --output out
  python txdot_direction.py --input TxDOT_Roadway_Linework_wAssets.shp --output out --fsystem 1 2 --rec 1
  python txdot_direction.py --input 2025.zip --output out --format gpkg --key-fields --travel-geometry
Requires: geopandas, pyogrio, pyshp, pyproj, numpy (pyarrow for --format parquet).
"""
import argparse, os, sys, time, warnings, zipfile
import numpy as np, geopandas as gpd, pyogrio, shapefile, shapely
from pyproj import Geod

warnings.filterwarnings('ignore', message='Measured')   # GDAL drops M values; we read them with pyshp instead

__version__ = "1.0.0"

RIGHT = {'RG', 'SG', 'TG', 'UG', 'VG', 'AG', 'BG'}   # right roadbeds incl. right frontage (AG, BG)
LEFT = {'LG', 'MG', 'PG', 'NG', 'OG', 'XG', 'YG'}    # left roadbeds incl. left frontage (XG, YG)
FRONTAGE = {'AG', 'BG', 'XG', 'YG'}
NEED = ['RDBD_ID', 'REC', 'HWY_DES', 'FRM_DFO', 'TO_DFO', 'HWY']
KEY_FIELDS = ['REC', 'RIA_RTE_ID', 'GID', 'FRM_DFO', 'TO_DFO', 'HWY', 'RDBD_ID', 'STE_NAM', 'F_SYSTEM',
              'HWY_DES', 'NUM_LANES', 'SPD_MAX', 'ADT_CUR']
C8 = np.array(['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']); C4 = np.array(['N', 'E', 'S', 'W'])


def main():
    ap = argparse.ArgumentParser(description="Add travel-direction columns to the TxDOT Roadway Inventory.")
    ap.add_argument('--input', required=True, help='TxDOT yearly .zip or TxDOT_Roadway_Linework_wAssets.shp')
    ap.add_argument('--output', required=True, help='output folder')
    ap.add_argument('--name', default=None, help='output file name (default: input name + _direction)')
    ap.add_argument('--fsystem', type=int, nargs='*', help='keep only these F_SYSTEM codes, e.g. 1 2')
    ap.add_argument('--rec', type=int, nargs='*', help='keep only these REC codes, e.g. 1 (main lanes)')
    ap.add_argument('--format', choices=['shp', 'gpkg', 'parquet'], default='shp',
                    help='shp (zipped, for ArcGIS), gpkg (ArcGIS Pro / QGIS) or parquet (Python / R)')
    ap.add_argument('--fields', nargs='*', help='attribute fields to keep (default: all)')
    ap.add_argument('--key-fields', action='store_true', help='keep only a short list of key fields')
    ap.add_argument('--travel-geometry', action='store_true',
                    help="also write a copy with DEC lines reversed, so 'arrow at end' points the way traffic moves")
    ap.add_argument('--assume-drawn-for-connectors', action='store_true',
                    help='give ramps/connectors (REC 0, GS) the drawn direction, labelled CONNECTOR_ASSUMED')
    ap.add_argument('--assume-drawn-for-unverified', action='store_true',
                    help='also give one-way single roadbeds (KG, HWY_DES 0/1) the drawn direction (not reliable)')
    ap.add_argument('--keep-centerline-kg', action='store_true', help='keep KG lines that sit between LG and RG')
    ap.add_argument('--version', action='version', version=__version__)
    a = ap.parse_args()
    run(a.input, a.output, name=a.name, fsystem=a.fsystem, rec=a.rec, fmt=a.format,
        fields=KEY_FIELDS if a.key_fields else a.fields, travel_geometry=a.travel_geometry,
        assume_connectors=a.assume_drawn_for_connectors, assume_unverified=a.assume_drawn_for_unverified,
        keep_kg=a.keep_centerline_kg)


def run(input, output, name=None, fsystem=None, rec=None, fmt='shp', fields=None, travel_geometry=False,
        assume_connectors=False, assume_unverified=False, keep_kg=False):
    """Run the whole method and write the result. Returns the output GeoDataFrame."""
    t0 = time.time()
    os.makedirs(output, exist_ok=True)
    shp = resolve_input(input)
    available = list(pyogrio.read_info(shp)['fields'])
    missing = [f for f in NEED if f not in available]
    if missing:
        sys.exit(f"{os.path.basename(shp)} is missing {missing}. Use TxDOT_Roadway_Linework_wAssets.shp "
                 "(the plain TxDOT_Roadway_Linework.shp has no roadbed fields).")
    if fields:
        skip = [f for f in fields if f not in available]
        if skip: print(f"note: these fields are not in this year's file and were skipped: {skip}")
        fields = [f for f in fields if f in available]
        fields += [f for f in NEED if f not in fields]

    where = []
    if fsystem: where.append(f"F_SYSTEM IN ({','.join(map(str, fsystem))})")
    if rec: where.append(f"REC IN ({','.join(map(str, rec))})")
    print(f"reading {os.path.basename(shp)} ...")
    g = gpd.read_file(shp, where=" AND ".join(where) or None, fid_as_index=True, engine='pyogrio',
                      columns=fields or None)
    print(f"read {len(g):,} lines in {time.time() - t0:.0f}s")

    g = fix_drawing_order(g, shp)
    g = add_direction(g, assume_connectors, assume_unverified)
    if not keep_kg:
        drop = kg_centerline_mask(g)
        kg_left = int(((g.RDBD_ID.astype(str).str.strip() == 'KG') & ~drop).sum())
        print(f"dropped {int(drop.sum()):,} KG centerlines that sit between LG and RG; "
              f"kept {kg_left:,} KG lines with no LG/RG beside them")
        g = g[~drop]

    base = name or (os.path.splitext(os.path.basename(shp))[0] + "_direction")
    write(g, output, base, fmt)
    if travel_geometry:
        tg = g.copy()
        rev = tg.DFO_DIR.values == 'DEC'
        tg.loc[rev, 'geometry'] = [shapely.reverse(x) for x in tg.geometry[rev]]
        write(tg, output, base + "_travel_geometry", fmt)
    print("\nDIR_RULE counts:"); print(g.DIR_RULE.value_counts().to_string())
    print("\nDFO_DIR counts:"); print(g.DFO_DIR.value_counts().to_string())
    print(f"\ndone in {time.time() - t0:.0f}s -> {os.path.abspath(output)}")
    return g


def resolve_input(path, output=None):
    """Return a .shp path. A TxDOT .zip is unpacked once (only the wAssets shapefile) into a folder next to it,
    for example 2025.zip -> 2025_unzipped/, so the large input does not end up in the output folder."""
    if not path.lower().endswith('.zip'):
        return path
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shps = [n for n in names if n.lower().endswith('.shp')]
        pick = [n for n in shps if 'wassets' in n.lower()] or shps
        if not pick:
            sys.exit(f"No shapefile found in {path}")
        stem = pick[0][:-4]
        members = [n for n in names if n.startswith(stem + '.') and not n.lower().endswith(('.sbn', '.sbx', '.xml'))]
        dest = os.path.splitext(os.path.abspath(path))[0] + '_unzipped'; os.makedirs(dest, exist_ok=True)
        for n in members:
            target = os.path.join(dest, os.path.basename(n))
            if os.path.exists(target) and os.path.getsize(target) == z.getinfo(n).file_size:
                continue
            print(f"unzipping {os.path.basename(n)} ({z.getinfo(n).file_size / 1e6:,.0f} MB) ...")
            with z.open(n) as src, open(target, 'wb') as dst:
                while True:
                    buf = src.read(1 << 24)
                    if not buf: break
                    dst.write(buf)
    print(f"input shapefile: {dest} (you can delete this folder after the run)")
    return os.path.join(dest, os.path.basename(stem) + '.shp')


def fix_drawing_order(g, shp):
    """Step 1: make every line run from low DFO to high DFO, using the M (DFO) value stored at each vertex.
    GDAL drops M values, so they are read with pyshp."""
    sf = shapefile.Reader(shp)
    if not sf.shapeTypeName.endswith('M') and 'Z' not in sf.shapeTypeName:
        print("WARNING: no M values in this file; assuming lines are drawn in increasing DFO.")
        m_ok = np.ones(len(g), dtype=bool)
    else:
        m_ok = np.empty(len(g), dtype=bool)
        for i, fid in enumerate(g.index):
            m = sf.shape(int(fid)).m
            m_ok[i] = True if (not m or m[0] is None or m[-1] is None) else (m[0] <= m[-1])
    sf.close()
    g['M_ORDER_OK'] = m_ok.astype(int)
    if (~m_ok).any():
        g.loc[~m_ok, 'geometry'] = [shapely.reverse(x) for x in g.geometry[~m_ok]]
    print(f"drawn from low DFO to high DFO: {m_ok.mean():.4%} of lines ({(~m_ok).sum():,} reversed)")
    return g


def add_direction(g, assume_connectors=False, assume_unverified=False):
    """Steps 2 and 3: bearing of each line, then the roadbed rules."""
    # 2) bearing of the drawn line, first vertex -> last vertex (geodesic azimuth, 0 = N, 90 = E)
    ll = g.geometry.to_crs(4326)
    first = np.array([x.coords[0] if x.geom_type == 'LineString' else x.geoms[0].coords[0] for x in ll])
    last = np.array([x.coords[-1] if x.geom_type == 'LineString' else x.geoms[-1].coords[-1] for x in ll])
    bear = np.mod(Geod(ellps='WGS84').inv(first[:, 0], first[:, 1], last[:, 0], last[:, 1])[0], 360)

    # 3) travel direction rules
    rb = g.RDBD_ID.astype(str).str.strip().values
    rec = g.REC.astype(float).values
    des = g.HWY_DES.astype(float).values
    rule = np.full(len(g), 'OTHER', dtype=object)
    rule[np.isin(rb, list(RIGHT))] = 'RIGHT_ROADBED'
    rule[np.isin(rb, list(LEFT))] = 'LEFT_ROADBED'
    rule[np.isin(rb, list(FRONTAGE)) & (des == 2)] = 'TWO_WAY'      # frontage road coded two-way
    kg = rb == 'KG'
    rule[kg & np.isin(des, [2, 3, 4, 5])] = 'TWO_WAY'
    rule[kg & np.isin(des, [0, 1])] = 'ONE_WAY_UNVERIFIED'
    rule[(rec == 0) | (rb == 'GS')] = 'CONNECTOR_UNVERIFIED'

    dfo_dir = np.full(len(g), 'UNK', dtype=object)
    dfo_dir[rule == 'RIGHT_ROADBED'] = 'INC'
    dfo_dir[rule == 'LEFT_ROADBED'] = 'DEC'
    dfo_dir[rule == 'TWO_WAY'] = 'BOTH'
    if assume_connectors:
        c = rule == 'CONNECTOR_UNVERIFIED'
        dfo_dir[c] = 'INC'; rule[c] = 'CONNECTOR_ASSUMED'
    if assume_unverified:
        dfo_dir[np.isin(rule, ['ONE_WAY_UNVERIFIED', 'CONNECTOR_UNVERIFIED'])] = 'INC'

    trav = np.where(dfo_dir == 'INC', bear, np.where(dfo_dir == 'DEC', np.mod(bear + 180, 360), np.nan))
    has = ~np.isnan(trav)
    t = np.nan_to_num(trav)
    g['BEAR_DRAWN'] = bear.round(1)
    g['DFO_DIR'] = dfo_dir
    g['TRAV_BEAR'] = np.round(trav, 1)
    g['TRAV_CMP8'] = np.where(has, C8[(np.floor(np.mod(t + 22.5, 360) / 45)).astype(int)], '')
    g['TRAV_CMP4'] = np.where(has, C4[(np.floor(np.mod(t + 45, 360) / 90)).astype(int)], '')
    g['DIR_RULE'] = rule
    return g


def kg_centerline_mask(g):
    """Step 4: True for KG lines whose DFO range overlaps an LG or RG line of the same highway (HWY)."""
    rb = g.RDBD_ID.astype(str).str.strip()
    hwy = g.HWY.astype(str).str.strip()
    out = np.zeros(len(g), dtype=bool)
    side = g[rb.isin(['LG', 'RG']).values & (hwy != '').values]
    ivs = {}
    for h, d in side.groupby(side.HWY.astype(str).str.strip()):
        a = d[['FRM_DFO', 'TO_DFO']].to_numpy(float); a = a[np.argsort(a[:, 0])]
        merged = [list(a[0])]
        for s, e in a[1:]:
            if s <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], e)
            else: merged.append([s, e])
        ivs[h] = np.array(merged)
    pos = np.flatnonzero((rb == 'KG').values & (hwy != '').values)
    for i in pos:
        m = ivs.get(hwy.iat[i])
        if m is None: continue
        f, t = float(g.FRM_DFO.iat[i]), float(g.TO_DFO.iat[i])
        k = np.searchsorted(m[:, 0], t, side='left') - 1   # last LG/RG interval that starts before t
        if k >= 0 and m[k, 1] > f: out[i] = True
    return out


def write(gdf, folder, base, fmt):
    if fmt == 'parquet':
        path = os.path.join(folder, base + ".parquet"); gdf.to_parquet(path)
    elif fmt == 'gpkg':
        path = os.path.join(folder, base + ".gpkg"); gdf.to_file(path, layer=base, driver='GPKG')
    else:
        d = os.path.join(folder, base); os.makedirs(d, exist_ok=True)
        gdf.to_file(os.path.join(d, base + ".shp"), driver='ESRI Shapefile')
        path = os.path.join(folder, base + ".zip")
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in os.listdir(d): z.write(os.path.join(d, f), f)
    print("wrote", path)


if __name__ == '__main__':
    main()
