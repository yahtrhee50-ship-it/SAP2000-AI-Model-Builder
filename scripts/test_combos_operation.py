"""
Live integration test for services/sap2000/operations/combos.py.

Part 1 (pure logic, no SAP2000): generate_lrfd_combos enumeration —
variant expansion of the "or" alternatives, skips, factor values.

Part 2 (live COM): simply supported beam (6 m, pin + roller), DEAD
(self-weight killed) 10 kN down + LIVE 20 kN down at midspan joint.
define_load_combos({"D": "DEAD", "L": "LIVE"}) then run the analysis and
check factored reactions against statics:

  - LC1 = 1.4D          -> each support F3 = 1.4*10/2   =  7 kN
  - LC2 = 1.2D + 1.6L   -> each support F3 = (12+32)/2  = 22 kN
  - LRFD-ENV (envelope) -> Max step F3 = 22 kN
  - LC3..LC7 skipped (no Lr/S/R, W, E mapped)
  - calling twice (replace=True) succeeds

Run:  C:\\Python314\\python.exe scripts\\test_combos_operation.py
Requires SAP2000 (launches or attaches automatically).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = True


def check(label, ok, detail=""):
    global PASS
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    PASS = PASS and ok


def logic_tests():
    from src.backend.services.sap2000.operations.combos import generate_lrfd_combos

    print("== pure logic: full map D/L/Lr/S/W/E ==")
    combos, skipped = generate_lrfd_combos(
        {"D": "DEAD", "L": "LIVE", "Lr": "ROOFLIVE", "S": "SNOW", "W": "WX",
         "E": "EQX"})
    by_name = {c["id"] + c["variant"]: c for c in combos}
    check("no combos skipped", not skipped, str(skipped))
    check("LC1 = 1.4D", by_name["LC1"]["factors"] == {"D": 1.4})
    check("LC2 has 2 roof variants (Lr, S)",
          {"LC2a", "LC2b"} <= set(by_name), str(sorted(by_name)))
    check("LC2a = 1.2D+1.6L+0.5Lr",
          by_name["LC2a"]["factors"] == {"D": 1.2, "L": 1.6, "Lr": 0.5})
    lc3 = [c for c in combos if c["id"] == "LC3"]
    check("LC3 has 4 variants (2 roofs x L/0.5W)", len(lc3) == 4,
          str([c["note"] for c in lc3]))
    check("LC3 W-companion factor is 0.5",
          any(c["factors"].get("W") == 0.5 for c in lc3))
    lc4 = [c for c in combos if c["id"] == "LC4"]
    check("LC4 has 2 roof variants, W=1.0, L=1.0",
          len(lc4) == 2 and all(
              c["factors"]["W"] == 1.0 and c["factors"]["L"] == 1.0
              for c in lc4))
    check("LC5 = 0.9D+1.0W", by_name["LC5"]["factors"] == {"D": 0.9, "W": 1.0})
    check("LC6 = 1.2D+1.0E+1.0L+0.2S",
          by_name["LC6"]["factors"] == {"D": 1.2, "E": 1.0, "L": 1.0, "S": 0.2})
    check("LC7 = 0.9D+1.0E", by_name["LC7"]["factors"] == {"D": 0.9, "E": 1.0})

    print("\n== pure logic: D+L only ==")
    combos, skipped = generate_lrfd_combos({"D": "DEAD", "L": "LIVE"})
    ids = [c["id"] + c["variant"] for c in combos]
    check("only LC1, LC2 generated", ids == ["LC1", "LC2"], str(ids))
    check("LC2 = 1.2D+1.6L (no roof term)",
          combos[1]["factors"] == {"D": 1.2, "L": 1.6})
    skip_ids = {s["id"] for s in skipped}
    check("LC3-7 skipped", skip_ids == {"LC3", "LC4", "LC5", "LC6", "LC7"},
          str(sorted(skip_ids)))

    print("\n== pure logic: missing D rejected ==")
    try:
        generate_lrfd_combos({"L": "LIVE"})
        check("raises without D", False)
    except ValueError as exc:
        check("raises without D", True, str(exc)[:60])


def live_tests():
    import pythoncom
    pythoncom.CoInitialize()
    from src.backend.services.sap2000.connector import get_connection
    from src.backend.services.sap2000.operations import OPERATIONS

    conn = get_connection()
    conn.connect(visible=True)
    conn.initialize_new_model("kN_m")
    m = conn.model

    m.PointObj.AddCartesian(0.0, 0.0, 0.0, "", "J1")
    m.PointObj.AddCartesian(6.0, 0.0, 0.0, "", "J2")
    m.PointObj.AddCartesian(3.0, 0.0, 0.0, "", "J3")
    m.PointObj.SetRestraint("J1", [True, True, True, True, False, False])
    m.PointObj.SetRestraint("J2", [False, True, True, False, False, False])
    m.FrameObj.AddByPoint("J1", "J3", "", "Default", "F1")
    m.FrameObj.AddByPoint("J3", "J2", "", "Default", "F2")
    m.LoadPatterns.SetSelfWTMultiplier("DEAD", 0.0)
    m.LoadPatterns.Add("LIVE", 3, 0.0, True)  # 3 = eLoadPatternType Live
    m.PointObj.SetLoadForce("J3", "DEAD", [0.0, 0.0, -10.0, 0.0, 0.0, 0.0],
                            True, "Global", 0)
    m.PointObj.SetLoadForce("J3", "LIVE", [0.0, 0.0, -20.0, 0.0, 0.0, 0.0],
                            True, "Global", 0)

    print("\n== define_load_combos (live) ==")
    out = OPERATIONS["define_load_combos"](conn, case_map={"D": "DEAD", "L": "LIVE"})
    names = [c["name"] for c in out["combos"]]
    check("created LC1, LC2", names == ["LC1", "LC2"], str(names))
    check("envelope LRFD-ENV created", out["envelope"] == "LRFD-ENV")
    check("LC3-7 reported skipped", len(out["skipped"]) == 5,
          str(out["skipped"]))
    lc2 = out["combos"][1]
    check("LC2 case factors D:1.2 L:1.6",
          {(c["case"], c["factor"]) for c in lc2["cases"]}
          == {("DEAD", 1.2), ("LIVE", 1.6)}, str(lc2["cases"]))
    check("LC2 cites 2.3.1(2)", lc2["code_ref"].endswith("2.3.1(2)"))

    print("\n== replace=True (idempotent second call) ==")
    out2 = OPERATIONS["define_load_combos"](conn, case_map={"D": "DEAD", "L": "LIVE"})
    check("second call succeeds", out2["status"] == "ok"
          and [c["name"] for c in out2["combos"]] == ["LC1", "LC2"])

    print("\n== unknown case rejected ==")
    try:
        OPERATIONS["define_load_combos"](conn, case_map={"D": "NOPE"})
        check("raises for unknown case name", False)
    except RuntimeError as exc:
        check("raises for unknown case name", True, str(exc)[:70])

    print("\n== factored reactions vs statics ==")
    conn.save(os.path.join(tempfile.gettempdir(), "test_combos_op.sdb"))
    conn.run_analysis()

    out = OPERATIONS["joint_reactions"](conn, cases=["LC1"])
    f3 = {r["joint"]: r["F3"] for r in out["reactions"]}
    check("LC1: each support F3 == 1.4*10/2 = 7 kN",
          abs(f3["J1"] - 7.0) < 1e-6 and abs(f3["J2"] - 7.0) < 1e-6,
          f"J1={f3['J1']:.6f} J2={f3['J2']:.6f}")

    out = OPERATIONS["joint_reactions"](conn, cases=["LC2"])
    f3 = {r["joint"]: r["F3"] for r in out["reactions"]}
    check("LC2: each support F3 == (1.2*10+1.6*20)/2 = 22 kN",
          abs(f3["J1"] - 22.0) < 1e-6 and abs(f3["J2"] - 22.0) < 1e-6,
          f"J1={f3['J1']:.6f} J2={f3['J2']:.6f}")

    out = OPERATIONS["joint_reactions"](conn, cases=["LRFD-ENV"])
    mx = max(r["F3"] for r in out["reactions"] if r["joint"] == "J1")
    check("LRFD-ENV: J1 max F3 == 22 kN (envelope of 7, 22)",
          abs(mx - 22.0) < 1e-6, f"{mx:.6f}")


def main():
    logic_tests()
    live_tests()
    print("\n" + ("ALL CHECKS PASS" if PASS else "SOME CHECKS FAILED"))
    return 0 if PASS else 1


if __name__ == "__main__":
    sys.exit(main())
