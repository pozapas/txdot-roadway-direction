"""
Small synthetic test. It builds a tiny shapefile with DFO (M) values, runs the tool and checks the result.
Run:  python -m pytest tests     or     python tests/test_direction.py
"""
import os, sys, tempfile
import geopandas as gpd, shapefile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import txdot_direction as td

WGS84 = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
         'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')

# id, RDBD_ID, REC, HWY_DES, HWY, FRM_DFO, TO_DFO, points as (lon, lat, M)
LINES = [
    # divided highway IH0001 running north-east; all drawn from low DFO to high DFO
    (1, 'RG', 1, 4, 'IH0001', 0.0, 1.0, [(-97.7400, 30.0000, 0.0), (-97.7300, 30.0100, 1.0)]),
    (2, 'LG', 1, 4, 'IH0001', 0.0, 1.0, [(-97.7405, 30.0004, 0.0), (-97.7305, 30.0104, 1.0)]),
    (3, 'KG', 1, 4, 'IH0001', 0.0, 1.0, [(-97.7402, 30.0002, 0.0), (-97.7302, 30.0102, 1.0)]),
    # RG drawn backwards (high DFO first): the tool must reverse it before taking the bearing
    (4, 'RG', 1, 4, 'IH0001', 1.0, 2.0, [(-97.7200, 30.0200, 2.0), (-97.7300, 30.0100, 1.0)]),
    # two-way undivided state highway going east
    (5, 'KG', 1, 2, 'SH0071', 0.0, 1.0, [(-97.8000, 30.1000, 0.0), (-97.7900, 30.1000, 1.0)]),
    # left frontage road going east (traffic goes west)
    (6, 'XG', 2, 1, 'IH0001', 0.0, 1.0, [(-97.7410, 30.0010, 0.0), (-97.7310, 30.0010, 1.0)]),
    # grade-separated connector (ramp) going south
    (7, 'GS', 0, 1, 'IH0001', 0.0, 0.3, [(-97.7500, 30.0300, 0.0), (-97.7500, 30.0270, 0.3)]),
    # one-way single roadbed
    (8, 'KG', 1, 1, 'US0290', 0.0, 1.0, [(-97.7600, 30.0500, 0.0), (-97.7500, 30.0500, 1.0)]),
]


def make_input(folder):
    path = os.path.join(folder, 'tiny.shp')
    w = shapefile.Writer(path, shapeType=shapefile.POLYLINEM)
    for f, t, s, d in [('ID', 'N', 4, 0), ('RDBD_ID', 'C', 2, 0), ('REC', 'N', 2, 0), ('HWY_DES', 'N', 2, 0),
                       ('HWY', 'C', 10, 0), ('FRM_DFO', 'N', 12, 3), ('TO_DFO', 'N', 12, 3), ('F_SYSTEM', 'N', 2, 0)]:
        w.field(f, t, s, d)
    for i, rb, rec, des, hwy, fr, to, pts in LINES:
        w.linem([[list(p) for p in pts]])
        w.record(i, rb, rec, des, hwy, fr, to, 1)
    w.close()
    with open(path[:-4] + '.prj', 'w') as f:
        f.write(WGS84)
    return path


def run_tool(**kw):
    tmp = tempfile.mkdtemp()
    shp = make_input(tmp)
    td.run(shp, os.path.join(tmp, 'out'), fmt='gpkg', **kw)
    return gpd.read_file(os.path.join(tmp, 'out', 'tiny_direction.gpkg')).set_index('ID')


def test_rules():
    g = run_tool(assume_connectors=True)
    # RG: drawn bearing about 41 deg, traffic goes the drawn way (NE)
    assert g.loc[1, 'DFO_DIR'] == 'INC' and abs(g.loc[1, 'TRAV_BEAR'] - 40.9) < 0.5
    assert g.loc[1, 'TRAV_CMP8'] == 'NE' and g.loc[1, 'TRAV_CMP4'] == 'N'
    # LG: same drawn bearing, traffic goes the other way (SW)
    assert g.loc[2, 'DFO_DIR'] == 'DEC' and abs(g.loc[2, 'TRAV_BEAR'] - 220.9) < 0.5
    assert g.loc[2, 'TRAV_CMP8'] == 'SW' and g.loc[2, 'TRAV_CMP4'] == 'S'
    # KG between LG and RG is dropped
    assert 3 not in g.index
    # backwards-drawn RG is reversed first, so it still points NE
    assert g.loc[4, 'M_ORDER_OK'] == 0 and g.loc[4, 'TRAV_CMP8'] == 'NE'
    # two-way road: BOTH, no travel bearing
    assert g.loc[5, 'DFO_DIR'] == 'BOTH' and g.loc[5, 'TRAV_CMP8'] in ('', None)
    # left frontage drawn east carries westbound traffic
    assert g.loc[6, 'DFO_DIR'] == 'DEC' and g.loc[6, 'TRAV_CMP4'] == 'W'
    # ramp gets the drawn direction (south), flagged as assumed
    assert g.loc[7, 'DIR_RULE'] == 'CONNECTOR_ASSUMED' and g.loc[7, 'TRAV_CMP8'] == 'S'
    # one-way single roadbed is left unknown
    assert g.loc[8, 'DFO_DIR'] == 'UNK' and g.loc[8, 'DIR_RULE'] == 'ONE_WAY_UNVERIFIED'


def test_defaults_leave_ramps_unknown_and_can_keep_kg():
    g = run_tool(keep_kg=True)
    assert g.loc[7, 'DFO_DIR'] == 'UNK' and g.loc[7, 'DIR_RULE'] == 'CONNECTOR_UNVERIFIED'
    assert 3 in g.index and g.loc[3, 'DFO_DIR'] == 'BOTH'


if __name__ == '__main__':
    test_rules(); test_defaults_leave_ramps_unknown_and_can_keep_kg()
    print("all tests passed")
