"""
Live SAP2000 build test -- imperial units (kip/ft)
Building floor: 4 spans x 3 bays
  Spans (X): 4 x 20 ft = 80 ft
  Bays  (Y): 3 x 25 ft = 75 ft
  Girders: W24x94  (X-direction)
  Beams:   W18x35  (Y-direction)
  Slab:    0.667 ft thick (8 in), fc=4 ksi
  SDL:     0.020 ksf (20 psf),  LL: 0.080 ksf (80 psf)
  Supports: pinned at all 20 grid intersections

Connects to SAP2000 (launches it if not already open),
builds the full model, and reports every element created.
"""
import sys, os, json, urllib.request, urllib.error, time

BASE = "http://127.0.0.1:8000"
W    = 64

def hr(label=""):
    if label:
        dash = "-" * (W - len(label) - 4)
        print(f"\n-- {label} {dash}")
    else:
        print("\n" + "-" * W)

def post(path, body):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {"detail": str(e)}

def get(path):
    req = urllib.request.Request(f"{BASE}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}

# ---------- model definition -------------------------------------------------
X_COORDS = [0.0, 20.0, 40.0, 60.0, 80.0]
Y_COORDS = [0.0, 25.0, 50.0, 75.0]

PILES = [
    {"x": x, "y": y, "z": 0.0,
     "label": "P%d" % (i + 1),
     "restraint": [True, True, True, False, False, False]}
    for i, (x, y) in enumerate((x, y) for x in X_COORDS for y in Y_COORDS)
]

MODEL = {
    "project": {
        "name":           "Level-3 Floor  4Span x 3Bay",
        "description":    "Live SAP2000 build test -- kip/ft",
        "unit_system":    "kip_ft",
        "structure_type": "building_floor",
        "designer":       "SAP2000 Live Test",
    },
    "grid": {
        "x_spacings": [20.0, 20.0, 20.0, 20.0],
        "y_spacings": [25.0, 25.0, 25.0],
        "origin_x": 0.0, "origin_y": 0.0,
    },
    "girders": {
        "direction":   "X",
        "section":     {"name": "W24x94", "section_type": "W24x94", "material": "A992"},
        "row_indices": [0, 1, 2, 3],
    },
    "beams": {
        "section":     {"name": "W18x35", "section_type": "W18x35", "material": "A992"},
        "col_indices": [0, 1, 2, 3, 4],
    },
    "piles": PILES,
    "slab": {
        "thickness":     0.667,
        "concrete_fc":   0.576,
        "unit_weight":   0.150,
        "mesh_size":     2.0,
        "material_name": "Concrete_Slab",
    },
    "loads": {
        "dead_load":           0.020,
        "live_load":           0.080,
        "moving_load_enabled": False,
    },
}

# ---------- run ---------------------------------------------------------------
print("=" * W)
print("  SAP2000 Live Build Test -- kip/ft  --  4 Spans x 3 Bays")
print("=" * W)

# 1. Health check
sc, _ = get("/health")
if sc != 200:
    print("ERROR: Server not running at %s" % BASE)
    sys.exit(1)
print("\n  Server OK at %s" % BASE)

# 2. SAP2000 status
hr("Step 1 -- Check SAP2000 status")
sc, d = get("/api/sap2000/status")
print("  Connected: %s" % d.get("connected"))

# 3. Connect / launch SAP2000
hr("Step 2 -- Connect to SAP2000 (launches if not running)")
print("  Sending POST /api/sap2000/connect ...")
t0 = time.time()
sc, d = post("/api/sap2000/connect", {"visible": True})
elapsed = time.time() - t0

if sc == 200:
    print("  Connected in %.1f s" % elapsed)
else:
    print("  FAILED (HTTP %d): %s" % (sc, d.get("detail", d)))
    print()
    print("  SAP2000 does not appear to be installed on this machine.")
    print("  To use this feature, install SAP2000 v21+ with a valid license.")
    sys.exit(1)

# 4. Re-check status
sc, d = get("/api/sap2000/status")
print("  Status after connect: connected = %s" % d.get("connected"))

# 5. Build model
hr("Step 3 -- Build model in SAP2000")
print("  Model: %s" % MODEL["project"]["name"])
print("  Units: %s" % MODEL["project"]["unit_system"])
print("  Grid:  %d spans x %d bays" % (len(MODEL["grid"]["x_spacings"]), len(MODEL["grid"]["y_spacings"])))
print("  Sending POST /api/sap2000/build-from-json ...")
t0 = time.time()
sc, d = post("/api/sap2000/build-from-json", MODEL)
elapsed = time.time() - t0

if sc != 200:
    print("  BUILD FAILED (HTTP %d): %s" % (sc, d.get("detail", d)))
    sys.exit(1)

print("  Built in %.1f s" % elapsed)
report = d.get("report", {})

# 6. Report
hr("Step 4 -- Build report")

mats = report.get("materials", [])
print("  Materials (%d):" % len(mats))
for m in mats:
    print("    %s" % m)

secs = report.get("sections", [])
print("  Sections  (%d):" % len(secs))
for s in secs:
    print("    %s" % s)

joints = report.get("joints", [])
grid_j   = [j for j in joints if j.startswith("J_")]
supp_j   = [j for j in joints if j.startswith("Support")]
print("  Joints    (%d grid, %d supports):" % (len(grid_j), len(supp_j)))
print("    Grid joints   : %d  (expected %d x %d = %d)" % (
    len(grid_j), len(X_COORDS), len(Y_COORDS), len(X_COORDS)*len(Y_COORDS)))
print("    Support nodes : %d  (expected %d)" % (len(supp_j), len(PILES)))

frames = report.get("frames", [])
g_fr = [f for f in frames if f.startswith("G_")]
b_fr = [f for f in frames if f.startswith("B_")]
print("  Frames    (%d total):" % len(frames))
print("    Girder segments: %d  (expected %d x %d = %d)" % (
    len(g_fr), len(MODEL["girders"]["row_indices"]), len(MODEL["grid"]["x_spacings"]),
    len(MODEL["girders"]["row_indices"]) * len(MODEL["grid"]["x_spacings"])))
print("    Beam   segments: %d  (expected %d x %d = %d)" % (
    len(b_fr), len(MODEL["beams"]["col_indices"]), len(MODEL["grid"]["y_spacings"]),
    len(MODEL["beams"]["col_indices"]) * len(MODEL["grid"]["y_spacings"])))

areas = report.get("areas", [])
nx_per = round(20.0 / MODEL["slab"]["mesh_size"])
ny_per = round(25.0 / MODEL["slab"]["mesh_size"])
n_panels = len(MODEL["grid"]["x_spacings"]) * len(MODEL["grid"]["y_spacings"])
expected_areas = n_panels * nx_per * ny_per
print("  Shell areas (%d total):" % len(areas))
print("    Panels        : %d  (4 spans x 3 bays)" % n_panels)
print("    Mesh per panel: %d x %d = %d elements" % (nx_per, ny_per, nx_per*ny_per))
print("    Expected total: %d" % expected_areas)

ld_log = report.get("loads", [])
print("  Load log  (%d entries):" % len(ld_log))
for l in ld_log:
    print("    %s" % l)

errors = report.get("errors", [])

# 7. Pass/fail checks
hr("Step 5 -- Checks")
checks = [
    ("No build errors",                       len(errors) == 0),
    ("Materials defined",                     len(mats)   >= 1),
    ("Sections defined (W24x94 + W18x35)",    len(secs)   >= 2),
    ("20 grid joints (5x4)",                  len(grid_j) == 20),
    ("20 pile supports (pinned)",             len(supp_j) == 20),
    ("16 girder segments (4 lines x 4 spans)",len(g_fr)   == 16),
    ("15 beam segments  (5 lines x 3 bays)",  len(b_fr)   == 15),
    ("%d shell elements (%d panels x %d mesh)" % (expected_areas, n_panels, nx_per*ny_per),
                                              len(areas)  == expected_areas),
    ("SDL load applied to shells",            any("SDL" in str(l) for l in ld_log)),
    ("LL  load applied to shells",            any("LL"  in str(l) for l in ld_log)),
    ("DEAD self-weight pattern defined",      any("DEAD" in str(l) for l in ld_log)),
]

passed = 0
for label, result in checks:
    mark = "PASS" if result else "FAIL"
    if result: passed += 1
    print("  %s  %s" % (mark, label))

if errors:
    print()
    print("  Build errors:")
    for e in errors:
        print("    ERROR: %s" % e)

hr()
print("  Result: %d / %d checks passed" % (passed, len(checks)))
if passed == len(checks):
    print("  SAP2000 model is now open -- review it in the application.")
else:
    print("  Some checks failed -- see details above.")
print("=" * W)
