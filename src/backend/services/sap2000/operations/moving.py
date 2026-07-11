"""
Multi-step static moving-load operation.

Adds a load pattern that steps a defined general vehicle along a defined lane
at a given speed/start time, analyzed by a Static Linear Multistep load case —
a stepped time history of static solutions, distinct from the influence-line
envelope (MOVE1) the builder creates.

── Why this uses an s2k text round trip ─────────────────────────────────────
SAP2000 27.1 (non-Bridge license) has NO working programmatic write path for
the vehicle-live pattern data (verified live, 2026-07-11):
  * classic OAPI: no methods exist for it (typelib swept);
  * DatabaseTables interactive import of "Multi-Step Moving Load 1 - General"
    / "2 - Vehicle Data" reports "1 of 1 records successfully read" with zero
    errors/warnings and silently stores NOTHING (both with TableVersion=1 and
    the tables' native version 2);
  * the display AND editing table reads hide vehicle-live data entirely, so
    read-back gates cannot even see the problem.
The one path that works: SAP2000's own text backup (.$2k, written next to the
.sdb on every save) accepts these tables when re-opened as a .s2k — after
repairing its PROGRAM CONTROL row, which the backup writer leaves without
ProgramName/Version/CurrUnits (imported verbatim it aborts with "Version 0 is
not a recognized program version", leaving a blank model).

── Calibrated stepping convention (exact to machine precision, live) ────────
  step k of the case is time  t = (k-1) * disc          (k = 1..dur/disc+1)
  lead axle station           a = station_written + speed*max(0, t-start_time) - 1 ft
  trailing axles follow at their spacings; axles off the lane carry nothing.
The -1 ft is the vehicle's variable "Leading Load" segment collapsing to a
1 ft default in multi-step mode (front of vehicle = station, lead axle 1 ft
behind). With station_refers_to="lead_axle" (default) this operation writes
station+1 so the LEAD AXLE starts exactly at the requested station.
Calibration was done in kip_ft; other unit systems raise unless
station_refers_to="vehicle_front" (no offset assumption).
"""
from __future__ import annotations

import logging
import os
import re
import time

from ..connector import SAP2000Connection

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_LEAD_OFFSET_FT = 1.0  # leading-segment default length, kip_ft (calibrated)

# eUnits code -> PROGRAM CONTROL CurrUnits string
_CURR_UNITS = {
    1: "lb, in, F", 2: "lb, ft, F", 3: "Kip, in, F", 4: "Kip, ft, F",
    5: "KN, mm, C", 6: "KN, m, C", 7: "Kgf, mm, C", 8: "Kgf, m, C",
    9: "N, mm, C", 10: "N, m, C", 11: "Ton, mm, C", 12: "Ton, m, C",
    13: "KN, cm, C", 14: "Kgf, cm, C", 15: "N, cm, C", 16: "Ton, cm, C",
}


def _validate_name(kind: str, name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            f"{kind} name {name!r} must be alphanumeric/_/-/. (no spaces) "
            "for the s2k table write")


def _insert_rows(text: str, table: str, rows: list[str]) -> str:
    """Insert rows at the top of a TABLE block, creating the block before
    END TABLE DATA if the backup does not have it yet."""
    header = f'TABLE:  "{table}"'
    body = "\n".join(rows)
    if header in text:
        return text.replace(header, header + "\n" + body, 1)
    return text.replace("END TABLE DATA",
                        f"{header}\n{body}\n \nEND TABLE DATA")


def _has_token(text: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}(\s|$)", text, re.M) is not None


def add_multistep_moving_load(conn: SAP2000Connection,
                              vehicle: str,
                              speed: float,
                              duration: float,
                              lane: str = "LANE1",
                              pattern_name: str = "MSTEP",
                              case_name: str = "MSTEP1",
                              station: float = 0.0,
                              start_time: float = 0.0,
                              direction: str = "Forward",
                              disc: float = 1.0,
                              station_refers_to: str = "lead_axle",
                              run_analysis: bool = False) -> dict:
    """Create a multi-step static moving-load case: `vehicle` (an existing
    general vehicle, e.g. from the builder's moving-load setup) enters `lane`
    at `station` (lead-axle station in model length units by default) at
    `start_time` seconds and travels in `direction` at `speed` (model length
    units per second); the case solves a static step every `disc` seconds for
    `duration` seconds (steps = duration/disc + 1).

    The model must have been saved (needs a .sdb path); the operation
    round-trips the model through its text form, so unsaved analysis results
    are dropped (set run_analysis=True to re-run after the case is added).
    """
    m = conn.model

    # ── validate ──
    for kind, name in (("vehicle", vehicle), ("lane", lane),
                       ("pattern", pattern_name), ("case", case_name)):
        _validate_name(kind, name)
    if direction not in ("Forward", "Backward"):
        raise ValueError("direction must be 'Forward' or 'Backward'")
    if speed <= 0 or duration <= 0 or disc <= 0:
        raise ValueError("speed, duration and disc must be positive")
    if station_refers_to not in ("lead_axle", "vehicle_front"):
        raise ValueError("station_refers_to must be 'lead_axle' or "
                         "'vehicle_front'")

    filename = str(m.GetModelFilename(True) or "")
    if not filename or "untitled" in filename.lower():
        raise RuntimeError(
            "Model has no saved .sdb file — save it first (e.g. build with "
            "save_path, or the run_analysis operation with save_path)")

    r = m.GetDatabaseUnits()
    units_code = int(r[0]) if isinstance(r, (tuple, list)) else int(r)
    curr_units = _CURR_UNITS.get(units_code)
    if curr_units is None:
        raise RuntimeError(f"Unmapped SAP2000 units code {units_code}")

    if station_refers_to == "lead_axle":
        if units_code not in (3, 4):  # kip_in / kip_ft (imperial, ft-based cal)
            raise RuntimeError(
                "lead_axle station calibration (1 ft leading segment) is "
                "verified for imperial models only; use "
                "station_refers_to='vehicle_front' for this unit system")
        offset = _LEAD_OFFSET_FT * (12.0 if units_code == 3 else 1.0)
    else:
        offset = 0.0
    station_written = station + offset

    # ── save; SAP writes/refreshes the .$2k text backup next to the .sdb ──
    if m.File.Save(filename) != 0:
        raise RuntimeError(f"SAP2000 Save({filename}) failed")
    backup = os.path.splitext(filename)[0] + ".$2k"
    if not os.path.exists(backup):
        raise RuntimeError(
            f"Text backup {backup} not found — enable 'Automatically add "
            "text backup file' in SAP2000 options (it is required by this "
            "operation)")
    text = open(backup, encoding="ascii", errors="strict").read()

    # ── sanity: vehicle + lane really exist in the model text ──
    if not _has_token(text, f"VehName={vehicle}"):
        raise RuntimeError(
            f"General vehicle {vehicle!r} not found in the model (define it "
            "first, e.g. via the builder's moving-load setup)")
    if not _has_token(text, f"Lane={lane}"):
        raise RuntimeError(f"Lane {lane!r} not found in the model")

    # ── repair PROGRAM CONTROL (backup writer omits ProgramName/Version) ──
    rv = m.GetVersion("", 0.0)
    version = str(rv[0]) if isinstance(rv, (tuple, list)) else "27.1.0"
    pc_re = re.compile(r'(TABLE:  "PROGRAM CONTROL"\n   )(?!ProgramName=)')
    text, n_fix = pc_re.subn(
        rf'\1ProgramName=SAP2000   Version={version}   '
        rf'CurrUnits="{curr_units}"   ', text)
    if n_fix == 0 and "ProgramName=" not in text:
        raise RuntimeError("PROGRAM CONTROL table not found in text backup")

    # ── idempotency: drop any previous rows for this pattern/case ──
    pat_tok = re.compile(rf"\bLoadPat={re.escape(pattern_name)}(\s|$)")
    case_tok = re.compile(rf"\bCase={re.escape(case_name)}(\s|$)")
    text = "\n".join(ln for ln in text.split("\n")
                     if not pat_tok.search(ln) and not case_tok.search(ln))

    # ── splice the five row sets ──
    text = _insert_rows(text, "LOAD PATTERN DEFINITIONS", [
        f'   LoadPat={pattern_name}   DesignType="Vehicle Live"   '
        f'SelfWtMult=0'])
    text = _insert_rows(text, "MULTI-STEP MOVING LOAD 1 - GENERAL", [
        f"   LoadPat={pattern_name}   LoadDur={duration:g}   "
        f"LoadDisc={disc:g}   SpeedFrom=Vehicle"])
    text = _insert_rows(text, "MULTI-STEP MOVING LOAD 2 - VEHICLE DATA", [
        f"   LoadPat={pattern_name}   Vehicle={vehicle}   Lane={lane}   "
        f"Station={station_written:g}   StartTime={start_time:g}   "
        f"Direction={direction}   Speed={speed:g}   VertSF=1"])
    text = _insert_rows(text, "LOAD CASE DEFINITIONS", [
        f'   Case={case_name}   Type=LinMSStat   InitialCond=Zero   '
        f'DesTypeOpt="Prog Det"   DesignType="Vehicle Live"   '
        f'DesActOpt="Prog Det"   DesignAct="Short-Term Composite"   '
        f'AutoType=None   RunCase=Yes'])
    text = _insert_rows(text, "CASE - MULTISTEP STATIC 1 - LOAD ASSIGNMENTS", [
        f'   Case={case_name}   LoadType="Load pattern"   '
        f'LoadName={pattern_name}   LoadSF=1'])

    # ── round trip: write s2k, reopen, save back to the .sdb ──
    s2k_path = os.path.splitext(filename)[0] + "_mstep.s2k"
    with open(s2k_path, "w", encoding="ascii") as f:
        f.write(text)
    if m.File.OpenFile(s2k_path) != 0:
        raise RuntimeError(f"SAP2000 OpenFile({s2k_path}) failed — the "
                           "spliced text import was rejected")

    # verify the pattern and case really exist now
    pats = [str(n) for n in (m.LoadPatterns.GetNameList(0, [])[1] or ())]
    cases = [str(n) for n in (m.LoadCases.GetNameList(0, [])[1] or ())]
    if pattern_name not in pats or case_name not in cases:
        raise RuntimeError(
            f"s2k import dropped the new definitions (patterns={pats}, "
            f"cases={cases}) — model left open from {s2k_path}; original "
            f".sdb on disk is untouched")
    if m.File.Save(filename) != 0:
        raise RuntimeError(f"SAP2000 Save back to {filename} failed")

    n_steps = int(round(duration / disc)) + 1
    station_key = ("lead_axle_station" if station_refers_to == "lead_axle"
                   else "vehicle_front_station")
    steps = []
    for k in range(1, n_steps + 1):
        t = (k - 1) * disc
        if t < start_time:
            # probed live: the vehicle carries ZERO load before start_time;
            # at t == start_time it is applied at its start station
            steps.append({"step": k, "time_s": t, station_key: None,
                          "applied": False})
        else:
            steps.append({"step": k, "time_s": t,
                          station_key: station + speed * (t - start_time),
                          "applied": True})

    result = {
        "status": "ok",
        "pattern": pattern_name,
        "case": case_name,
        "vehicle": vehicle,
        "lane": lane,
        "saved_to": filename,
        "n_steps": n_steps,
        "steps": steps,
        "notes": [
            "Static Linear Multistep case: one static solution per step, "
            f"step k at time t=(k-1)*{disc:g} s",
            f"station_refers_to={station_refers_to}: table Station written "
            f"as {station_written:g} (lead axle starts at requested "
            f"station {station:g})" if offset else
            f"station_refers_to=vehicle_front: lead axle sits 1 ft (kip_ft "
            f"calibration) behind the written station {station_written:g}",
            "Model was round-tripped through its text form; prior analysis "
            "results were discarded" +
            ("" if run_analysis else " — re-run the analysis to get results"),
        ],
    }
    if direction == "Backward":
        result["notes"].append(
            "direction=Backward is passed through but has NOT been "
            "live-verified — check the first steps against statics")

    if run_analysis:
        conn.run_analysis()
        rs = m.Analyze.GetCaseStatus(0, [], [])
        labels = {1: "not_run", 2: "could_not_start",
                  3: "not_finished", 4: "finished"}
        result["run"] = {str(n): labels.get(int(s), str(s))
                        for n, s in zip(rs[1] or (), rs[2] or ())}
        if result["run"].get(case_name) != "finished":
            raise RuntimeError(
                f"case {case_name} did not finish: {result['run']}")
    return result
