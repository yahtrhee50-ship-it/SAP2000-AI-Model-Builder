"""
Analysis-results extraction operations (SAP2000 Results.* OAPI).

Every function follows the operations contract (see package __init__):
`func(conn, **json_params) -> json_dict`, making each directly convertible
into an agent tool later.

comtypes vtable convention: COM methods are called with placeholder values
for the ByRef out-params and return a tuple of all in/out + out params in
signature order, with the API return code LAST (r[-1]; 0 == success).
Tuple layouts below were verified live against SAP2000 (see Project_006
session log 2026-07-03).

Case selection: each results call selects the requested cases/combos for
output first (Results.Setup). If `cases` is omitted, all load CASES
currently defined in the model are selected (combos must be requested
explicitly by name).
"""
from __future__ import annotations

import logging

from ..connector import SAP2000Connection

log = logging.getLogger(__name__)


def _check(ret_code, what: str) -> None:
    code = int(ret_code[0]) if isinstance(ret_code, (tuple, list)) else int(ret_code)
    if code != 0:
        raise RuntimeError(f"SAP2000 {what} failed (code {code})")


def _all_case_names(model) -> list[str]:
    r = model.LoadCases.GetNameList(0, [])
    _check(r[-1], "LoadCases.GetNameList")
    return [str(n) for n in (r[1] or ())]


_MULTISTEP_OPTIONS = {"envelope": 1, "steps": 2, "last_step": 3}


def _select_cases(model, cases: list[str] | None,
                  multistep: str | None = None) -> list[str]:
    """Select cases/combos for output; returns the selected names.

    multistep: how multi-step static cases report — "steps" (one row per
    step), "envelope" (Max/Min rows), "last_step", or None to leave the
    program's current setting untouched."""
    setup = model.Results.Setup
    _check(setup.DeselectAllCasesAndCombosForOutput(),
           "Results.Setup.DeselectAllCasesAndCombosForOutput")
    if multistep is not None:
        code = _MULTISTEP_OPTIONS.get(multistep)
        if code is None:
            raise ValueError(f"multistep must be one of "
                             f"{sorted(_MULTISTEP_OPTIONS)}, not {multistep!r}")
        _check(setup.SetOptionMultiStepStatic(code),
               "Results.Setup.SetOptionMultiStepStatic")
    names = cases if cases else _all_case_names(model)
    selected = []
    for name in names:
        if setup.SetCaseSelectedForOutput(name) == 0:
            selected.append(name)
        elif setup.SetComboSelectedForOutput(name) == 0:
            selected.append(name)
        elif cases:  # explicitly requested but unknown -> hard error
            raise RuntimeError(f"'{name}' is neither a load case nor a combo")
    if not selected:
        raise RuntimeError("No load cases/combos selected for output")
    return selected


def _rows(r, str_fields: list[tuple[str, int]],
          num_fields: list[tuple[str, int]]) -> list[dict]:
    """Turn a Results.* returned tuple (NumberResults at r[0]) into a list
    of row dicts. str_fields/num_fields: (name, tuple_index) pairs."""
    n = int(r[0])
    rows = []
    for i in range(n):
        row = {}
        for name, idx in str_fields:
            arr = r[idx]
            v = arr[i] if arr is not None else None
            row[name] = str(v) if v is not None else None
        for name, idx in num_fields:
            arr = r[idx]
            v = arr[i] if arr is not None else None
            row[name] = float(v) if v is not None else None
        rows.append(row)
    return rows


def list_load_cases(conn: SAP2000Connection) -> dict:
    """List the load cases defined in the current SAP2000 model, with each
    case's run status from the last analysis."""
    model = conn.model
    names = _all_case_names(model)
    # Analyze.GetCaseStatus: (NumberItems, CaseName[], Status[], ret);
    # Status: 1=not run, 2=could not start, 3=not finished, 4=finished
    status_map = {}
    try:
        r = model.Analyze.GetCaseStatus(0, [], [])
        _check(r[-1], "Analyze.GetCaseStatus")
        labels = {1: "not_run", 2: "could_not_start", 3: "not_finished", 4: "finished"}
        for cname, st in zip(r[1] or (), r[2] or ()):
            status_map[str(cname)] = labels.get(int(st), str(st))
    except Exception as exc:  # status is best-effort decoration
        log.warning("GetCaseStatus unavailable: %s", exc)
    return {"status": "ok",
            "cases": [{"name": n, "run_status": status_map.get(n, "unknown")}
                      for n in names]}


def _is_restrained(model, joint: str) -> bool:
    r = model.PointObj.GetRestraint(joint, [])
    return r[-1] == 0 and any(bool(b) for b in (r[0] or ()))


def joint_reactions(conn: SAP2000Connection, cases: list[str] | None = None,
                    joints: list[str] | None = None,
                    multistep: str | None = None) -> dict:
    """Support reactions (F1,F2,F3,M1,M2,M3 per joint per case/combo step),
    in current model units. Without an explicit `joints` list, only
    restrained (support) joints are returned — SAP2000 reports all-zero
    reaction rows for every free joint in the model, which is noise.
    multistep: "steps" for one row per step of multi-step static cases,
    "envelope" for Max/Min rows, None to leave the program setting."""
    model = conn.model
    selected = _select_cases(model, cases, multistep)
    targets = [(j, 0) for j in joints] if joints else [("ALL", 2)]
    rows = []
    for name, item_type in targets:
        r = model.Results.JointReact(name, item_type,
                                     0, [], [], [], [], [], [], [], [], [], [], [])
        _check(r[-1], f"Results.JointReact({name})")
        rows += _rows(r, [("joint", 1), ("case", 3), ("step_type", 4)],
                      [("step_num", 5), ("F1", 6), ("F2", 7), ("F3", 8),
                       ("M1", 9), ("M2", 10), ("M3", 11)])
    if not joints:
        restrained = {j: _is_restrained(model, j) for j in {row["joint"] for row in rows}}
        rows = [row for row in rows if restrained[row["joint"]]]
    return {"status": "ok", "cases_selected": selected, "reactions": rows}


def joint_displacements(conn: SAP2000Connection, cases: list[str] | None = None,
                        joints: list[str] | None = None,
                        multistep: str | None = None) -> dict:
    """Joint displacements/rotations (U1,U2,U3,R1,R2,R3 per joint per
    case/combo step), in current model units."""
    model = conn.model
    selected = _select_cases(model, cases, multistep)
    targets = [(j, 0) for j in joints] if joints else [("ALL", 2)]
    rows = []
    for name, item_type in targets:
        r = model.Results.JointDispl(name, item_type,
                                     0, [], [], [], [], [], [], [], [], [], [], [])
        _check(r[-1], f"Results.JointDispl({name})")
        rows += _rows(r, [("joint", 1), ("case", 3), ("step_type", 4)],
                      [("step_num", 5), ("U1", 6), ("U2", 7), ("U3", 8),
                       ("R1", 9), ("R2", 10), ("R3", 11)])
    return {"status": "ok", "cases_selected": selected, "displacements": rows}


def frame_forces(conn: SAP2000Connection, cases: list[str] | None = None,
                 frames: list[str] | None = None,
                 multistep: str | None = None) -> dict:
    """Frame internal forces (P,V2,V3,T,M2,M3 at each output station along
    each frame, per case/combo step), in current model units. NOTE: frame
    names are SAP2000's ACTUAL object names (see build report
    "requested -> actual" mapping)."""
    model = conn.model
    selected = _select_cases(model, cases, multistep)
    targets = [(f, 0) for f in frames] if frames else [("ALL", 2)]
    rows = []
    for name, item_type in targets:
        r = model.Results.FrameForce(name, item_type,
                                     0, [], [], [], [], [], [], [],
                                     [], [], [], [], [], [])
        _check(r[-1], f"Results.FrameForce({name})")
        rows += _rows(r, [("frame", 1), ("case", 5), ("step_type", 6)],
                      [("station", 2), ("step_num", 7), ("P", 8), ("V2", 9),
                       ("V3", 10), ("T", 11), ("M2", 12), ("M3", 13)])
    return {"status": "ok", "cases_selected": selected, "frame_forces": rows}


def base_reactions(conn: SAP2000Connection, cases: list[str] | None = None,
                   multistep: str | None = None) -> dict:
    """Global base reactions (total FX,FY,FZ,MX,MY,MZ about the reporting
    point gx,gy,gz) per case/combo step, in current model units."""
    model = conn.model
    selected = _select_cases(model, cases, multistep)
    r = model.Results.BaseReact(0, [], [], [], [], [], [], [], [], [],
                                0.0, 0.0, 0.0)
    _check(r[-1], "Results.BaseReact")
    rows = _rows(r, [("case", 1), ("step_type", 2)],
                 [("step_num", 3), ("FX", 4), ("FY", 5), ("FZ", 6),
                  ("MX", 7), ("MY", 8), ("MZ", 9)])
    gx, gy, gz = float(r[10]), float(r[11]), float(r[12])
    return {"status": "ok", "cases_selected": selected,
            "reporting_point": {"gx": gx, "gy": gy, "gz": gz},
            "base_reactions": rows}


def modal_periods(conn: SAP2000Connection) -> dict:
    """Modal periods/frequencies from the last modal analysis (requires a
    run MODAL case)."""
    model = conn.model
    _select_cases(model, None)
    r = model.Results.ModalPeriod(0, [], [], [], [], [], [], [])
    _check(r[-1], "Results.ModalPeriod")
    rows = _rows(r, [("case", 1), ("step_type", 2)],
                 [("mode", 3), ("period_s", 4), ("frequency_hz", 5),
                  ("circ_freq_rad_s", 6), ("eigenvalue", 7)])
    for row in rows:
        row["mode"] = int(row["mode"])
    return {"status": "ok", "modes": rows}
