"""
Pure-Python unit check (NO SAP2000) for the moving-load general-vehicle rebuild.

Verifies the AASHTO general-vehicle registry, the shared row builder
`_general_vehicle_rows`, and the kip/ft -> model-unit conversion — the logic
that replaces the (broken on a non-Bridge license) SAP2000 library
standard-vehicle path. See docs plan smooth-watching-leaf.md.

Live COM verification of the analysis (stepped-axle influence-line match,
Vehicles-1-empty warning proxy) lives in P006 scripts/verify_moving_load_live.py.

Run:  C:\\Python314\\python.exe scripts\\test_general_vehicle_rows.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.backend.services.sap2000.builder import (  # noqa: E402
    AASHTO_VEHICLES, CALTRANS_PERMIT_TRUCKS, UNIT_INFO, _general_vehicle_rows)

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


LOAD_FIELDS = ["VehName", "LoadType", "InterUnif", "InterAxle", "InterMinD", "InterMaxD"]


# ── row builder ───────────────────────────────────────────────────────────────
print("Row builder (_general_vehicle_rows):")

gen, loads = _general_vehicle_rows("HS20-44", [8.0, 32.0, 32.0], [14.0, 14.0], 0.0)
check("HS20 general row = [name, 3 segments, No]", gen == ["HS20-44", "3", "No"], gen)
check("HS20 leading axle row", loads[0] == ["HS20-44", "Leading Load", "0", "8", "", ""], loads[0])
check("HS20 2nd axle Fixed Length @14", loads[1] == ["HS20-44", "Fixed Length", "0", "32", "14", ""], loads[1])
check("HS20 3rd axle Fixed Length @14", loads[2] == ["HS20-44", "Fixed Length", "0", "32", "14", ""], loads[2])
check("HS20 has exactly 3 load rows", len(loads) == 3, len(loads))

# uniform (design-lane) load carried on every segment
hl_gen, hl = _general_vehicle_rows("HL93TRUCK", [8.0, 32.0, 32.0], [14.0, 14.0], 0.64)
check("HL-93 truck InterUnif=0.64 on all rows",
      all(r[2] == "0.64" for r in hl), [r[2] for r in hl])
check("HL-93 truck has trailing uniform row (axles + 1)",
      len(hl) == 4 and hl[-1] == ["HL93TRUCK", "Trailing Load", "0.64", "", "", ""],
      hl[-1] if hl else hl)
check("HL-93 truck general row counts trailing segment",
      hl_gen == ["HL93TRUCK", "4", "No"], hl_gen)
check("no trailing row without a lane load (HS20)",
      all(r[1] != "Trailing Load" for r in loads))

# malformed input rejected
for bad in ([[], []], [[10.0, 20.0], []], [[10.0, -5.0], [14.0]], [[0.0], []]):
    try:
        _general_vehicle_rows("BAD", bad[0], bad[1])
        check(f"reject malformed {bad}", False, "no error raised")
    except ValueError:
        check(f"reject malformed {bad}", True)


# ── AASHTO registry ───────────────────────────────────────────────────────────
print("\nAASHTO registry:")

check("HS20 present", "HS20" in AASHTO_VEHICLES)
check("HS15 present", "HS15" in AASHTO_VEHICLES)
check("HL-93 present", "HL-93" in AASHTO_VEHICLES)
check("HS20-44 alias == HS20", AASHTO_VEHICLES["HS20-44"] is AASHTO_VEHICLES["HS20"])
check("HL93 alias == HL-93", AASHTO_VEHICLES["HL93"] is AASHTO_VEHICLES["HL-93"])

hs20 = AASHTO_VEHICLES["HS20"][0]
check("HS20 axles = 8/32/32 kip", hs20[1] == [8.0, 32.0, 32.0], hs20[1])
check("HS20 spacings = 14/14 ft", hs20[2] == [14.0, 14.0], hs20[2])
check("HS20 no lane load", hs20[3] == 0.0, hs20[3])

hs15 = AASHTO_VEHICLES["HS15"][0]
check("HS15 axles = 6/24/24 kip (0.75 x HS20)", hs15[1] == [6.0, 24.0, 24.0], hs15[1])

hl93 = AASHTO_VEHICLES["HL-93"]
check("HL-93 envelopes 2 sub-vehicles (truck + tandem)", len(hl93) == 2, len(hl93))
truck = next(v for v in hl93 if v[1] == [8.0, 32.0, 32.0])
tandem = next(v for v in hl93 if v[1] == [25.0, 25.0])
check("HL-93 tandem = 25/25 kip @ 4 ft", tandem[2] == [4.0], tandem[2])
check("HL-93 truck lane load 0.64 klf", truck[3] == 0.64, truck[3])
check("HL-93 tandem lane load 0.64 klf", tandem[3] == 0.64, tandem[3])
check("every AASHTO sub-vehicle carries a source string",
      all(isinstance(v[4], str) and v[4] for e in
          {id(x): x for x in AASHTO_VEHICLES.values()}.values() for v in e))


# ── Caltrans guard ────────────────────────────────────────────────────────────
print("\nCaltrans permit guard:")

check("P5/P7/P9/P11/P13 all flagged permit",
      CALTRANS_PERMIT_TRUCKS == {"P5", "P7", "P9", "P11", "P13"}, CALTRANS_PERMIT_TRUCKS)
check("no Caltrans permit name leaked into the AASHTO registry",
      CALTRANS_PERMIT_TRUCKS.isdisjoint(AASHTO_VEHICLES))


# ── unit conversion (kip/ft -> model units) ───────────────────────────────────
print("\nUnit conversion into model systems:")

# emulate the builder's conversion of the AASHTO entry into model units
def to_model(units):
    info = UNIT_INFO[units]
    kf, lf = info["kip_to_force"], info["ft_to_len"]
    v = AASHTO_VEHICLES["HL-93"][0]  # truck: 8/32/32 @14/14, 0.64 klf
    axles = [a * kf for a in v[1]]
    spac = [s * lf for s in v[2]]
    lane = v[3] * kf / lf
    return axles, spac, lane

# kip_ft: identity
a, s, lane = to_model("kip_ft")
check("kip_ft axles unchanged (8/32/32)", a == [8.0, 32.0, 32.0], a)
check("kip_ft spacing unchanged (14 ft)", s == [14.0, 14.0], s)
check("kip_ft lane load 0.64 kip/ft", abs(lane - 0.64) < 1e-9, lane)

# kip_in: force same, lengths x12
a, s, lane = to_model("kip_in")
check("kip_in spacing = 168 in", s == [168.0, 168.0], s)
check("kip_in lane load = 0.64/12 kip/in", abs(lane - 0.64 / 12.0) < 1e-9, lane)

# kN_m: force x4.4482216, length x0.3048
a, s, lane = to_model("kN_m")
check("kN_m 32 kip -> 142.34 kN", abs(a[1] - 32.0 * 4.4482216) < 1e-6, a[1])
check("kN_m 14 ft -> 4.2672 m", abs(s[0] - 14.0 * 0.3048) < 1e-9, s[0])
# 0.64 kip/ft -> (0.64*4.4482216)/0.3048 kN/m ~= 9.339 kN/m
check("kN_m lane load 0.64 klf -> ~9.339 kN/m",
      abs(lane - 0.64 * 4.4482216 / 0.3048) < 1e-6, lane)


print(f"\n{'='*52}")
print(f"RESULT: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
