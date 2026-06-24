"""
Imperial-unit (kip/ft) live test.
Building floor: 4 spans x 3 bays
  Spans (X): 4 x 20 ft = 80 ft total
  Bays  (Y): 3 x 25 ft = 75 ft total
  Girders: W24x94  (X-direction, at every Y-line)
  Beams:   W18x35  (Y-direction, at every X-line)
  Slab:    0.667 ft thick (8 in), fc=4 ksi, 150 pcf
  SDL:     0.020 ksf (20 psf),  LL: 0.080 ksf (80 psf)
  Supports: pinned at all 20 grid intersections
"""
import sys, os, json, urllib.request, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE = "http://127.0.0.1:8000"
PASS = []; FAIL = []; WARN = []

def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json"} if data else {}
    req  = urllib.request.Request(f"{BASE}{path}", data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            try:    return r.status, json.loads(raw)
            except: return r.status, raw.decode(errors="replace")
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read())
        except: return e.code, {}

def ok(label, cond, note=""):
    if cond:
        PASS.append(label); print(f"  PASS  {label}")
    else:
        FAIL.append(label); print(f"  FAIL  {label}" + (f"  ({note})" if note else ""))

def warn(label, note=""):
    WARN.append(label); print(f"  WARN  {label}" + (f"  -- {note}" if note else ""))

def section(title):
    print(f"\n{'='*64}")
    print(f"  {title}")
    print(f"{'='*64}")

# ── Imperial model ────────────────────────────────────────────────────────────
X_SPACINGS = [20.0, 20.0, 20.0, 20.0]    # 4 spans @ 20 ft
Y_SPACINGS = [25.0, 25.0, 25.0]           # 3 bays  @ 25 ft
X_COORDS   = [0.0, 20.0, 40.0, 60.0, 80.0]   # 5 lines
Y_COORDS   = [0.0, 25.0, 50.0, 75.0]          # 4 lines

PILES = [
    {"x": x, "y": y, "z": 0.0,
     "label": "P%d" % (i + 1),
     "restraint": [True, True, True, False, False, False]}   # pinned
    for i, (x, y) in enumerate((x, y) for x in X_COORDS for y in Y_COORDS)
]

MODEL = {
    "project": {
        "name":           "Level-3 Floor  --  4 Spans x 3 Bays",
        "description":    "Building floor, kip/ft units",
        "unit_system":    "kip_ft",
        "structure_type": "building_floor",
        "designer":       "Imperial Test",
    },
    "grid": {
        "x_spacings": X_SPACINGS,
        "y_spacings": Y_SPACINGS,
        "origin_x": 0.0,
        "origin_y": 0.0,
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
        "thickness":     0.667,        # 8 in = 0.667 ft
        "concrete_fc":   0.576,        # 4 ksi = 576 ksf → stored in kip/ft2
        "unit_weight":   0.150,        # 150 pcf = 0.150 kcf
        "mesh_size":     2.0,          # 2 ft mesh
        "material_name": "Concrete_Slab",
    },
    "loads": {
        "dead_load":           0.020,  # 20 psf = 0.020 ksf
        "live_load":           0.080,  # 80 psf = 0.080 ksf
        "moving_load_enabled": False,
    },
}

# =============================================================================
section("A / SERVER HEALTH")
# =============================================================================
sc, d = _req("GET", "/health")
ok("GET /health -> 200",           sc == 200)
ok("version = 1.0.0",              d.get("version") == "1.0.0")

# =============================================================================
section("B / PREVIEW  --  imperial model")
# =============================================================================
sc, pv = _req("POST", "/api/preview", MODEL)
ok("POST /api/preview (kip_ft) -> 200",  sc == 200)
ok("model marked complete",              pv.get("complete") is True)

xc = pv.get("grid", {}).get("x_coords", [])
yc = pv.get("grid", {}).get("y_coords", [])

# Grid coords
ok("5 X-coord lines (4 spans + 1)",      len(xc) == 5)
ok("4 Y-coord lines (3 bays  + 1)",      len(yc) == 4)
ok("total length = 80.0 ft",             xc and abs(xc[-1] - xc[0] - 80.0) < 0.001)
ok("total width  = 75.0 ft",             yc and abs(yc[-1] - yc[0] - 75.0) < 0.001)
ok("X-coords: [0, 20, 40, 60, 80]",     xc == [0.0, 20.0, 40.0, 60.0, 80.0])
ok("Y-coords: [0, 25, 50, 75]",         yc == [0.0, 25.0, 50.0, 75.0])

# Girder lines  (W24x94, X-direction)
glines = pv.get("girders", {}).get("lines", [])
ok("4 girder lines (one per Y-line)",    len(glines) == 4)
ok("girder section = W24x94",            pv.get("girders", {}).get("section") == "W24x94")
ok("girders span full 80 ft (x1=0, x2=80)",
   all(g["x1"] == 0.0 and g["x2"] == 80.0 for g in glines))
ok("girder Y-positions match Y-coords",
   sorted(g["y1"] for g in glines) == sorted(yc))

# Beam lines  (W18x35, Y-direction)
blines = pv.get("beams", {}).get("lines", [])
ok("5 beam lines (one per X-line)",      len(blines) == 5)
ok("beam section = W18x35",              pv.get("beams", {}).get("section") == "W18x35")
ok("beams span full 75 ft (y1=0, y2=75)",
   all(b["y1"] == 0.0 and b["y2"] == 75.0 for b in blines))
ok("beam X-positions match X-coords",
   sorted(b["x1"] for b in blines) == sorted(xc))

# Slab panels  (4 spans x 3 bays = 12 panels)
panels = pv.get("slab", {}).get("panels", [])
ok("12 slab panels (4 spans x 3 bays)",  len(panels) == 12)
ok("slab thickness = 0.667 ft",          abs(pv.get("slab", {}).get("thickness", 0) - 0.667) < 0.001)
ok("first panel: x[0-20] y[0-25]",       panels and panels[0] == {"x1":0.0,"y1":0.0,"x2":20.0,"y2":25.0})
ok("last panel: x[60-80] y[50-75]",      panels and panels[-1] == {"x1":60.0,"y1":50.0,"x2":80.0,"y2":75.0})
panel_keys = [(p["x1"],p["y1"],p["x2"],p["y2"]) for p in panels]
ok("no duplicate panels",                len(panel_keys) == len(set(panel_keys)))

# Piles  (5 x-lines x 4 y-lines = 20 pinned supports)
piles_pv = pv.get("piles", [])
ok("20 pile supports (5 x 4 intersections)",  len(piles_pv) == 20)
pile_coords = sorted((p["x"], p["y"]) for p in piles_pv)
expected_coords = sorted((x, y) for x in X_COORDS for y in Y_COORDS)
ok("pile coordinates match all grid intersections",  pile_coords == expected_coords)

# Loads
ld = pv.get("loads", {})
ok("SDL = 0.020 ksf",                   ld.get("dead_load") == 0.020)
ok("LL  = 0.080 ksf",                   ld.get("live_load") == 0.080)
ok("moving load disabled",              ld.get("moving_load") is False)

# Completion
comp = pv.get("completion", {})
ok("all 7 phases in completion dict",   len(comp) == 7)
ok("all phases complete",               all(comp.values()))

# =============================================================================
section("C / BUILDER LOGIC  (unit tests, no SAP2000 needed)")
# =============================================================================
from src.backend.models.structural import (
    StructuralModel, GridDefinition, GirderLayout, BeamLayout,
    FrameSection, PileSupport, SlabDefinition, LoadDefinition,
)
from src.backend.services.sap2000.builder import ModelBuilder

# Grid coordinate computation
grid = GridDefinition(x_spacings=X_SPACINGS, y_spacings=Y_SPACINGS)
ok("grid x_coords correct",  grid.x_coords == X_COORDS)
ok("grid y_coords correct",  grid.y_coords == Y_COORDS)

# Pydantic round-trip
import json as _json
dumped  = _json.dumps(MODEL)
model_a = StructuralModel(**_json.loads(dumped))
dumped2 = _json.dumps(model_a.model_dump())
model_b = StructuralModel(**_json.loads(dumped2))
ok("round-trip: unit_system = kip_ft",    model_b.project.unit_system.value == "kip_ft")
ok("round-trip: x_spacings preserved",    model_b.grid.x_spacings == X_SPACINGS)
ok("round-trip: girder section = W24x94", model_b.girders.section.name == "W24x94")
ok("round-trip: beam section = W18x35",   model_b.beams.section.name == "W18x35")
ok("round-trip: pile count = 20",         len(model_b.piles) == 20)
ok("round-trip: pinned restraints",       model_b.piles[0].restraint == [True,True,True,False,False,False])
ok("round-trip: SDL = 0.020 ksf",         model_b.loads.dead_load == 0.020)
ok("round-trip: LL  = 0.080 ksf",         model_b.loads.live_load == 0.080)
ok("round-trip: moving load disabled",    model_b.loads.moving_load_enabled is False)
ok("round-trip: slab thickness = 0.667",  abs(model_b.slab.thickness - 0.667) < 0.001)

# =============================================================================
section("D / BUILDER  (mock SAP2000, imperial model)")
# =============================================================================
class _MockArea:
    load_calls = []
    def AddByPoint(self, n, pts, name):           return (name, 0)
    def SetProperty(self, name, prop):            return 0
    def SetLoadUniform(self, name, pat, val, *a): self.load_calls.append((name, pat, val))

class _MockPoint:
    def AddCartesian(self, x, y, z, name):  return (name, 0)
    def SetRestraint(self, name, r):         return 0
    def SetSpring(self, *a):                 return 0

class _MockFrame:
    def AddByPoint(self, p1, p2, name):      return (name, 0)
    def SetSection(self, name, sec):         return 0

class _MockPropMaterial:
    def AddMaterial(self, *a):               return 0
    def SetMPIsotropic(self, *a):            return 0
    def SetWeightAndMass(self, *a):          return 0

class _MockPropFrame:
    def ImportProp(self, *a):                return 0
    def SetSD(self, *a):                     return 0
    def SetRectangle(self, *a):              return 0

class _MockPropArea:
    def SetShell_1(self, *a):                return 0

class _MockLoadPatterns:
    calls = []
    def Add(self, name, ptype, mult, *a):    self.calls.append((name, mult))

class _MockView:
    def RefreshView(self, *a):               return 0

_mock_area     = _MockArea()
_mock_patterns = _MockLoadPatterns()

class _MockSapModel:
    PointObj     = _MockPoint()
    FrameObj     = _MockFrame()
    AreaObj      = _mock_area
    PropMaterial = _MockPropMaterial()
    PropFrame    = _MockPropFrame()
    PropArea     = _MockPropArea()
    LoadPatterns = _mock_patterns
    View         = _MockView()

class _MockConn:
    model = _MockSapModel()

full_model = StructuralModel(**_json.loads(dumped))
builder    = ModelBuilder(_MockConn())
report     = builder.build(full_model)

ok("builder: no errors",                    len(report["errors"]) == 0)

# Frame counts:
# Girders: 4 Y-lines x 4 spans = 16
# Beams:   5 X-lines x 3 bays  = 15
girder_frames = [f for f in report["frames"] if f.startswith("G_")]
beam_frames   = [f for f in report["frames"] if f.startswith("B_")]
ok("builder: 16 girder frames (4 Y-lines x 4 spans)",   len(girder_frames) == 16)
ok("builder: 15 beam frames  (5 X-lines x 3 bays)",     len(beam_frames)   == 15)
ok("builder: 31 total frame elements",                   len(report["frames"]) == 31)

# Grid joints: 5 x-coords x 4 y-coords = 20
grid_joints = [j for j in report["joints"] if j.startswith("J_")]
ok("builder: 20 grid joints",               len(grid_joints) == 20)

# Slab mesh: each 20x25 ft panel subdivided by 2 ft mesh
# nx = round(20/2) = 10,  ny = round(25/2) = 13  (round(12.5) -> 12 or 13)
nx = round(20.0 / 2.0)   # 10
ny = round(25.0 / 2.0)   # 12  (Python banker's rounding: round(12.5) = 12)
elems_per_panel = nx * ny
expected_areas  = 12 * elems_per_panel
ok("builder: %d mesh elements per 20x25 ft panel (nx=%d x ny=%d)" % (elems_per_panel, nx, ny),
   len(report["areas"]) == expected_areas,
   "got %d" % len(report["areas"]))

# Load patterns: DEAD, SDL, LL (no ML)
pat_names = [c[0] for c in _mock_patterns.calls]
ok("builder: DEAD self-weight pattern defined",    "DEAD" in pat_names)
ok("builder: SDL pattern defined",                 "SDL"  in pat_names)
ok("builder: LL  pattern defined",                 "LL"   in pat_names)
ok("builder: no ML pattern (moving load off)",     "ML"   not in pat_names)
dead_mult = next((c[1] for c in _mock_patterns.calls if c[0] == "DEAD"), None)
ok("builder: DEAD selfWtMultiplier = 1.0",         dead_mult == 1.0)

# Load assignments via SetLoadUniform
sdl_calls = [c for c in _mock_area.load_calls if c[1] == "SDL"]
ll_calls  = [c for c in _mock_area.load_calls if c[1] == "LL"]
ok("builder: SDL applied to all area elements",    len(sdl_calls) == expected_areas)
ok("builder: LL  applied to all area elements",    len(ll_calls)  == expected_areas)
ok("builder: SDL value = -0.020 ksf (downward)",   sdl_calls and abs(sdl_calls[0][2] + 0.020) < 1e-9)
ok("builder: LL  value = -0.080 ksf (downward)",   ll_calls  and abs(ll_calls[0][2]  + 0.080) < 1e-9)

# Materials
ok("builder: materials defined",                   len(report["materials"]) >= 1)

# Load report messages
load_log = " ".join(report.get("loads", []))
ok("builder: load report mentions SDL value",       "0.02" in load_log or "SDL" in load_log)
ok("builder: load report mentions LL value",        "0.08" in load_log or "LL"  in load_log)

# =============================================================================
section("E / EDGE CASES  (imperial variations)")
# =============================================================================

# E1. W-section names: imperial sections use different naming (W24x94 vs W610x140)
e1 = dict(MODEL)
e1["girders"] = dict(MODEL["girders"])
e1["girders"] = {"direction":"X","section":{"name":"W30x116","section_type":"W30x116","material":"A992"},"row_indices":[0,1,2,3]}
sc, pv = _req("POST", "/api/preview", e1)
ok("W30x116 section (large imperial beam) -> 200",  sc == 200)

# E2. Metric section names should still work in imperial unit model
e2 = dict(MODEL)
e2["girders"] = {"direction":"X","section":{"name":"W610x140","section_type":"W610x140","material":"A992"},"row_indices":[0,1,2,3]}
sc, pv = _req("POST", "/api/preview", e2)
ok("metric section name in kip_ft model (cross-check) -> 200", sc == 200)

# E3. Single span, single bay imperial
e3 = {
    "project": {"name":"Test","unit_system":"kip_ft","structure_type":"building_floor","designer":""},
    "grid": {"x_spacings":[30.0],"y_spacings":[20.0],"origin_x":0.0,"origin_y":0.0},
    "girders": {"direction":"X","section":{"name":"W24x94","section_type":"W24x94","material":"A992"},"row_indices":[0,1]},
    "piles": [{"x":x,"y":y,"z":0.0,"label":"P%d"%(i+1),"restraint":[True]*6}
              for i,(x,y) in enumerate((x,y) for x in [0.0,30.0] for y in [0.0,20.0])],
    "slab": {"thickness":0.667,"concrete_fc":0.576,"unit_weight":0.150,"mesh_size":2.5},
    "loads": {"dead_load":0.020,"live_load":0.080},
}
sc, pv = _req("POST", "/api/preview", e3)
ok("1-span 1-bay imperial model -> 200",     sc == 200)
ok("1-span 1-bay: 1 slab panel",             len(pv.get("slab",{}).get("panels",[])) == 1)
ok("1-span 1-bay: 4 piles",                  len(pv.get("piles",[])) == 4)
ok("1-span 1-bay: total length = 30 ft",     pv.get("grid",{}).get("x_coords",[])[-1] == 30.0 if pv.get("grid",{}).get("x_coords") else False)
ok("1-span 1-bay: total width  = 20 ft",     pv.get("grid",{}).get("y_coords",[])[-1] == 20.0 if pv.get("grid",{}).get("y_coords") else False)

# E4. Non-uniform imperial spacings
e4 = dict(MODEL)
e4["grid"] = {"x_spacings":[18.0,24.0,20.0,22.0],"y_spacings":[20.0,30.0,25.0],"origin_x":0.0,"origin_y":0.0}
sc, pv = _req("POST", "/api/preview", e4)
ok("non-uniform imperial spacings -> 200",   sc == 200)
xc2 = pv.get("grid",{}).get("x_coords",[])
ok("total non-uniform length = 84 ft",
   xc2 and abs((xc2[-1]-xc2[0]) - sum([18.0,24.0,20.0,22.0])) < 0.001)

# E5. Imperial + Y-direction girders
e5 = dict(MODEL)
e5["girders"] = {"direction":"Y","section":{"name":"W24x94","section_type":"W24x94","material":"A992"},"row_indices":[0,1,2,3,4]}
e5["beams"]   = {"section":{"name":"W18x35","section_type":"W18x35","material":"A992"},"col_indices":[0,1,2,3]}
sc, pv = _req("POST", "/api/preview", e5)
ok("Y-dir girders, imperial -> 200",          sc == 200)
ok("Y-dir: 5 girder lines (one per X-coord)", len(pv.get("girders",{}).get("lines",[])) == 5)
ok("Y-dir: 4 beam lines   (one per Y-coord)", len(pv.get("beams",{}).get("lines",[])) == 4)
if pv.get("girders",{}).get("lines"):
    ok("Y-dir girders span 75 ft width",
       all(abs(g["y2"]-75.0) < 0.001 for g in pv["girders"]["lines"]))

# E6. Imperial, no beams
e6 = {k: v for k, v in MODEL.items() if k != "beams"}
sc, pv = _req("POST", "/api/preview", e6)
ok("imperial model without beams -> 200",        sc == 200)
ok("no beams in response",                       "beams" not in pv)
ok("beams phase = False in completion",          pv.get("completion",{}).get("beams") is False)
ok("other phases still complete without beams",
   all(v for k, v in pv.get("completion",{}).items() if k != "beams"))

# =============================================================================
section("F / SUMMARY")
# =============================================================================
total = len(PASS) + len(FAIL)
print()
print("  Model : kip/ft  |  4-span x 3-bay building floor")
print("  W24x94 girders, W18x35 beams, 8-in slab, 20/80 psf loads")
print()
print("  Passed : %d / %d" % (len(PASS), total))
print("  Failed : %d / %d" % (len(FAIL), total))
print("  Warnings: %d" % len(WARN))

if FAIL:
    print()
    print("  FAILURES:")
    for f in FAIL: print("    - " + f)

if WARN:
    print()
    print("  WARNINGS:")
    for w in WARN: print("    - " + w)

print()
sys.exit(0 if not FAIL else 1)
