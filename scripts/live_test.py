"""
Live simulation test: 5 spans x 4 bays bridge deck.
  Bays  = transverse spaces between girders (Y-direction), 4 bays @ 2.5 m
  Spans = longitudinal support intervals   (X-direction), 5 spans @ 6.0 m
  W610x140 girders (longitudinal), W460x60 beams (transverse)
  200 mm thick-shell slab, fc=28 MPa
  SDL 2.0 kN/m2, LL 5.0 kN/m2, HL-93 moving load (1 lane)
  Piles at all 30 grid intersections, fixed
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
W    = 62

def hr(label=""):
    dash = "-" * (W - len(label) - 4)
    print(f"\n-- {label} {dash}" if label else "\n" + "-" * W)

# ---- Build model --------------------------------------------------------
x_vals = [0.0, 6.0, 12.0, 18.0, 24.0, 30.0]   # 6 lines, 5 spans
y_vals = [0.0, 2.5, 5.0,  7.5,  10.0]          # 5 lines, 4 bays

piles = [
    {
        "x": x, "y": y, "z": 0.0,
        "label": "P%d" % (i + 1),
        "restraint": [True, True, True, True, True, True],
    }
    for i, (x, y) in enumerate(
        (x, y) for x in x_vals for y in y_vals
    )
]

model = {
    "project": {
        "name":           "Bridge Deck - 5 Span x 4 Bay",
        "description":    "5 spans @ 6 m, 4 bays @ 2.5 m",
        "unit_system":    "kN_m",
        "structure_type": "bridge_deck",
        "designer":       "AI Live Test",
    },
    "grid": {
        "x_spacings": [6.0, 6.0, 6.0, 6.0, 6.0],
        "y_spacings": [2.5, 2.5, 2.5, 2.5],
        "origin_x": 0.0,
        "origin_y": 0.0,
    },
    "girders": {
        "direction":   "X",
        "section":     {"name": "W610x140", "section_type": "W610x140", "material": "A992"},
        "row_indices": [0, 1, 2, 3, 4],
    },
    "beams": {
        "section":     {"name": "W460x60", "section_type": "W460x60", "material": "A992"},
        "col_indices": [0, 1, 2, 3, 4, 5],
    },
    "piles": piles,
    "slab": {
        "thickness":     0.2,
        "concrete_fc":   28.0,
        "unit_weight":   24.0,
        "mesh_size":     0.5,
        "material_name": "Concrete_Slab",
    },
    "loads": {
        "dead_load":           2.0,
        "live_load":           5.0,
        "moving_load_enabled": True,
        "lane_width":          3.0,
        "truck_axle_loads":    [35.0, 145.0, 145.0],
        "truck_axle_spacings": [4.3, 4.3],
    },
}

# ---- POST to /api/preview -----------------------------------------------
body = json.dumps(model).encode()
req  = urllib.request.Request(
    "%s/api/preview" % BASE,
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req) as resp:
        pv = json.loads(resp.read())
except urllib.error.HTTPError as e:
    print("HTTP %d: %s" % (e.code, e.read().decode()))
    sys.exit(1)

xc      = pv["grid"]["x_coords"]
yc      = pv["grid"]["y_coords"]
g_lines = pv["girders"]["lines"]
b_lines = pv["beams"]["lines"]
panels  = pv["slab"]["panels"]
pls     = pv["piles"]
ld      = pv["loads"]

# ---- Print report -------------------------------------------------------
print("=" * W)
print("  LIVE TEST  --  Bridge Deck: 5 Spans x 4 Bays")
print("=" * W)

hr("Grid")
print("  X-coords (%d lines) : %s  m" % (len(xc), xc))
print("  Y-coords (%d lines) : %s  m" % (len(yc), yc))
print("  Length = %.1f m   Width = %.1f m" % (xc[-1] - xc[0], yc[-1] - yc[0]))

hr("Girders  W610x140  (longitudinal / X-direction)")
print("  Count   : %d  [expected 5 -- one continuous line per Y girder line]" % len(g_lines))
print("  Section : %s" % pv["girders"]["section"])
for i, g in enumerate(g_lines):
    print("  Girder %d : x [%.1f to %.1f]  at  y = %.1f m" % (i+1, g["x1"], g["x2"], g["y1"]))

hr("Beams  W460x60  (transverse / Y-direction)")
print("  Count   : %d  [expected 6 -- one line per X support line]" % len(b_lines))
print("  Section : %s" % pv["beams"]["section"])
for i, b in enumerate(b_lines):
    print("  Beam  %d : at x = %.1f m   y [%.1f to %.1f]" % (i+1, b["x1"], b["y1"], b["y2"]))

hr("Slab  200 mm Thick Shell  (fc = 28 MPa)")
print("  Panels  : %d  [expected 20 = 5 spans x 4 bays]" % len(panels))
print("  Thickness : %.3f m  (%.0f mm)" % (pv["slab"]["thickness"], pv["slab"]["thickness"]*1000))
print("  First   : x[%.1f-%.1f]  y[%.1f-%.1f]" % (
    panels[0]["x1"], panels[0]["x2"], panels[0]["y1"], panels[0]["y2"]))
print("  Last    : x[%.1f-%.1f]  y[%.1f-%.1f]" % (
    panels[-1]["x1"], panels[-1]["x2"], panels[-1]["y1"], panels[-1]["y2"]))

span_count = len(xc) - 1   # 5
bay_count  = len(yc) - 1   # 4
print()
print("  Panel map  (columns = spans, rows = bays):")
print("           " + "".join("  Span%-2d   " % (s+1) for s in range(span_count)))
for j in range(bay_count):
    row = []
    for i in range(span_count):
        p = panels[i * bay_count + j]
        row.append("[%2.0f-%2.0f, %.1f-%.1f]" % (p["x1"], p["x2"], p["y1"], p["y2"]))
    print("  Bay %d :  %s" % (j+1, "  ".join(row)))

hr("Piles / Supports  (fixed, all intersections)")
print("  Count   : %d  [expected 30 = 6 support lines x 5 girder lines]" % len(pls))
print()
print("  Pile layout (x = support line, y = girder line):")
print("  %s" % "".join("  y=%-4.1f" % y for y in y_vals))
for row_x in x_vals:
    row_piles = [p for p in pls if p["x"] == row_x]
    labels = "".join("  %-6s" % p["label"] for p in row_piles)
    print("  x=%4.1fm : %s" % (row_x, labels))

hr("Loads")
print("  Superimposed dead load (SDL) : %.1f kN/m2" % ld["dead_load"])
print("  Live load (LL)               : %.1f kN/m2" % ld["live_load"])
print("  Moving load                  : %s" % ld["moving_load"])
print("  Lane width                   : %.1f m" % model["loads"]["lane_width"])
print("  HL-93 axle loads             : %s kN" % model["loads"]["truck_axle_loads"])
print("  HL-93 axle spacings          : %s m" % model["loads"]["truck_axle_spacings"])

hr("Model Completion")
for k, v in pv["completion"].items():
    status = "COMPLETE" if v else "MISSING "
    print("  %s  %s" % (status, k))

hr("Pass / Fail Checks")
checks = [
    ("6 X-coord lines  (5 spans + 1)",          len(xc)           == 6),
    ("5 Y-coord lines  (4 bays  + 1)",          len(yc)           == 5),
    ("Total length = 30.0 m",                    xc[-1]-xc[0]     == 30.0),
    ("Total width  = 10.0 m",                    yc[-1]-yc[0]     == 10.0),
    ("5 girder lines (1 per Y-line)",            len(g_lines)      == 5),
    ("6 transverse beam lines (1 per X-line)",   len(b_lines)      == 6),
    ("20 slab panels  (5 spans x 4 bays)",       len(panels)       == 20),
    ("30 pile supports (6 x-lines x 5 y-lines)", len(pls)          == 30),
    ("SDL = 2.0 kN/m2",                          ld["dead_load"]   == 2.0),
    ("LL  = 5.0 kN/m2",                          ld["live_load"]   == 5.0),
    ("Moving load enabled",                      ld["moving_load"] is True),
    ("All model phases complete",                all(pv["completion"].values())),
]
passed = 0
for label, ok in checks:
    mark = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    print("  %s  %s" % (mark, label))

print()
print("  Result : %d / %d checks passed" % (passed, len(checks)))
print("=" * W)
