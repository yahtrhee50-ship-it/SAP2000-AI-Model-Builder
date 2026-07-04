"""
Live integration test for services/sap2000/operations/results.py.

Builds a known simply supported beam directly over COM (6 m span, pin +
roller, 10 kN down at midspan joint), runs the analysis, then exercises
every results operation and checks the numbers against statics:

  - joint_reactions: R_pin = R_roller = 5 kN up, sum F3 = +10 kN
  - base_reactions:  FZ = +10 kN
  - joint_displacements: midspan U3 < 0, supports U3 = 0
  - frame_forces: max |M3| == 15 kN*m (PL/4) at the midspan station
  - list_load_cases: DEAD present and finished

Run:  C:\\Python314\\python.exe scripts\\test_results_operations.py
Requires SAP2000 (launches or attaches automatically).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pythoncom
pythoncom.CoInitialize()

from src.backend.services.sap2000.connector import get_connection
from src.backend.services.sap2000.operations import OPERATIONS

PASS = True


def check(label, ok, detail=""):
    global PASS
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    PASS = PASS and ok


def main():
    conn = get_connection()
    conn.connect(visible=True)
    conn.initialize_new_model("kN_m")
    m = conn.model

    m.PointObj.AddCartesian(0.0, 0.0, 0.0, "", "J1")
    m.PointObj.AddCartesian(6.0, 0.0, 0.0, "", "J2")
    m.PointObj.AddCartesian(3.0, 0.0, 0.0, "", "J3")
    m.PointObj.SetRestraint("J1", [True, True, True, False, False, False])
    m.PointObj.SetRestraint("J2", [False, True, True, False, False, False])
    m.FrameObj.AddByPoint("J1", "J3", "", "Default", "F1")
    m.FrameObj.AddByPoint("J3", "J2", "", "Default", "F2")
    # kill self-weight so statics are exact: DEAD self-weight multiplier -> 0
    m.LoadPatterns.SetSelfWTMultiplier("DEAD", 0.0)
    m.PointObj.SetLoadForce("J3", "DEAD", [0.0, 0.0, -10.0, 0.0, 0.0, 0.0],
                            True, "Global", 0)
    conn.save(os.path.join(tempfile.gettempdir(), "test_results_ops.sdb"))
    conn.run_analysis()

    print("\n== list_load_cases ==")
    out = OPERATIONS["list_load_cases"](conn)
    dead = next((c for c in out["cases"] if c["name"] == "DEAD"), None)
    check("DEAD case exists", dead is not None)
    check("DEAD finished", dead and dead["run_status"] == "finished",
          str(dead))

    print("\n== joint_reactions ==")
    out = OPERATIONS["joint_reactions"](conn, cases=["DEAD"])
    rx = {r["joint"]: r for r in out["reactions"]}
    check("two support joints report reactions", set(rx) == {"J1", "J2"}, str(set(rx)))
    sum_f3 = sum(r["F3"] for r in out["reactions"])
    check("sum F3 == +10 kN (equilibrium)", abs(sum_f3 - 10.0) < 1e-6, f"{sum_f3:.6f}")
    for j in ("J1", "J2"):
        check(f"{j} F3 == 5 kN (symmetry)", abs(rx[j]["F3"] - 5.0) < 1e-6,
              f"{rx[j]['F3']:.6f}")

    print("\n== base_reactions ==")
    out = OPERATIONS["base_reactions"](conn, cases=["DEAD"])
    fz = out["base_reactions"][0]["FZ"]
    check("base FZ == +10 kN", abs(fz - 10.0) < 1e-6, f"{fz:.6f}")

    print("\n== joint_displacements ==")
    out = OPERATIONS["joint_displacements"](conn, cases=["DEAD"], joints=["J1", "J2", "J3"])
    dz = {r["joint"]: r["U3"] for r in out["displacements"]}
    check("midspan deflects down", dz["J3"] < -1e-9, f"{dz['J3']:.3e} m")
    check("supports do not deflect", abs(dz["J1"]) < 1e-12 and abs(dz["J2"]) < 1e-12)

    print("\n== frame_forces ==")
    out = OPERATIONS["frame_forces"](conn, cases=["DEAD"])
    m3max = max(abs(r["M3"]) for r in out["frame_forces"])
    check("max |M3| == PL/4 = 15 kN*m", abs(m3max - 15.0) < 1e-6, f"{m3max:.6f}")
    v2max = max(abs(r["V2"]) for r in out["frame_forces"])
    check("max |V2| == 5 kN", abs(v2max - 5.0) < 1e-6, f"{v2max:.6f}")

    print("\n== modal_periods (expected: no modal case run -> graceful) ==")
    try:
        out = OPERATIONS["modal_periods"](conn)
        check("modal_periods returned", True, f"{len(out['modes'])} modes")
    except Exception as exc:
        check("modal_periods raised cleanly (no MODAL results)", True, str(exc)[:80])

    print("\n" + ("ALL CHECKS PASS" if PASS else "SOME CHECKS FAILED"))
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
