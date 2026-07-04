"""
Load-combination operations (SAP2000 RespCombo OAPI).

`define_load_combos` creates ASCE 7-22 LRFD strength combinations (§2.3.1
basic 1-5, §2.3.6 seismic 6-7) as linear-additive response combos in the
current model, given a mapping from ASCE load types to the model's load
case names.

"Or" alternatives in the code equations (0.5(Lr or S or R); (L or 0.5W) in
combo 3) are handled by enumerating one combo VARIANT per alternative that
is actually mapped, suffixed a/b/c... — enveloping across variants then
recovers the code intent exactly. Combos whose distinguishing load is not
mapped are skipped (no W -> no 4/5; no E -> no 6/7; no Lr/S/R -> no 3).

Scope notes (consistent with Project_006 src/calcs/asce7.py):
  - Live-load reduction (§4.7) NOT applied — map an already-reduced L case
    if reduction is intended.
  - Companion L in combos 4 and 6 uses factor 1.0 (conservative; the §2.3.1
    exception permitting 0.5L is not taken).
  - W and E direction reversal is the engineer's responsibility: map
    directional wind/seismic cases (e.g. WX, WXN) and call once per
    direction with a distinct `prefix`, or map cases already defined as
    enveloping.
"""
from __future__ import annotations

import logging

from ..connector import SAP2000Connection

log = logging.getLogger(__name__)

_LOAD_TYPES = ("D", "L", "Lr", "S", "R", "W", "E")
_ROOF_TYPES = ("Lr", "S", "R")

# SAP2000 eCNameType: 0 = LoadCase, 1 = LoadCombo
_CTYPE_CASE, _CTYPE_COMBO = 0, 1
# SAP2000 combo types for RespCombo.Add
_COMBO_LINEAR_ADD, _COMBO_ENVELOPE = 0, 1


def _check(ret_code, what: str) -> None:
    code = int(ret_code[-1]) if isinstance(ret_code, (tuple, list)) else int(ret_code)
    if code != 0:
        raise RuntimeError(f"SAP2000 {what} failed (code {code})")


def _norm_case_map(case_map: dict) -> dict[str, list[str]]:
    """Validate keys and normalize values to lists of case names."""
    if not case_map:
        raise ValueError("case_map is required, e.g. {'D': 'DEAD', 'L': 'LIVE'}")
    out: dict[str, list[str]] = {}
    for k, v in case_map.items():
        if k not in _LOAD_TYPES:
            raise ValueError(
                f"Unknown load type {k!r}. Valid: {', '.join(_LOAD_TYPES)}.")
        names = [v] if isinstance(v, str) else list(v or ())
        if names:
            out[k] = [str(n) for n in names]
    if "D" not in out:
        raise ValueError("case_map must map 'D' (dead load) to a load case.")
    return out


def generate_lrfd_combos(case_map: dict) -> tuple[list[dict], list[dict]]:
    """Enumerate ASCE 7-22 LRFD combos for the mapped load types (pure logic,
    no SAP2000). Returns (combos, skipped); each combo dict has
    id/variant/equation/code_ref/factors where factors = {load_type: factor}
    over mapped types only."""
    cm = _norm_case_map(case_map)
    roofs = [t for t in _ROOF_TYPES if t in cm]
    has = cm.__contains__

    # Each entry: (id, code_ref, list of (variant_suffix_terms, factors))
    combos: list[dict] = []
    skipped: list[dict] = []

    def add(cid: str, code_ref: str, variants: list[tuple[str, dict]]):
        multi = len(variants) > 1
        for i, (desc, factors) in enumerate(variants):
            factors = {t: f for t, f in factors.items() if has(t) and f != 0.0}
            eq = " + ".join(f"{f:g}{t}" for t, f in factors.items())
            combos.append({
                "id": cid, "variant": chr(ord("a") + i) if multi else "",
                "equation": eq, "code_ref": code_ref, "factors": factors,
                "note": desc,
            })

    add("LC1", "ASCE 7-22 2.3.1(1)", [("", {"D": 1.4})])

    add("LC2", "ASCE 7-22 2.3.1(2)",
        [(f"roof={r}", {"D": 1.2, "L": 1.6, r: 0.5}) for r in roofs]
        or [("", {"D": 1.2, "L": 1.6})])

    if roofs:
        variants = []
        for r in roofs:
            if has("L"):
                variants.append((f"roof={r}, companion=L",
                                 {"D": 1.2, r: 1.6, "L": 1.0}))
            if has("W"):
                variants.append((f"roof={r}, companion=0.5W",
                                 {"D": 1.2, r: 1.6, "W": 0.5}))
            if not has("L") and not has("W"):
                variants.append((f"roof={r}", {"D": 1.2, r: 1.6}))
        add("LC3", "ASCE 7-22 2.3.1(3)", variants)
    else:
        skipped.append({"id": "LC3", "reason": "no Lr/S/R mapped"})

    if has("W"):
        add("LC4", "ASCE 7-22 2.3.1(4)",
            [(f"roof={r}", {"D": 1.2, "W": 1.0, "L": 1.0, r: 0.5})
             for r in roofs]
            or [("", {"D": 1.2, "W": 1.0, "L": 1.0})])
        add("LC5", "ASCE 7-22 2.3.1(5)", [("", {"D": 0.9, "W": 1.0})])
    else:
        skipped += [{"id": c, "reason": "no W mapped"} for c in ("LC4", "LC5")]

    if has("E"):
        add("LC6", "ASCE 7-22 2.3.6(6)",
            [("", {"D": 1.2, "E": 1.0, "L": 1.0, "S": 0.2})])
        add("LC7", "ASCE 7-22 2.3.6(7)", [("", {"D": 0.9, "E": 1.0})])
    else:
        skipped += [{"id": c, "reason": "no E mapped"} for c in ("LC6", "LC7")]

    return combos, skipped


def _model_case_and_combo_names(model) -> tuple[set[str], set[str]]:
    r = model.LoadCases.GetNameList(0, [])
    _check(r[-1], "LoadCases.GetNameList")
    cases = {str(n) for n in (r[1] or ())}
    r = model.RespCombo.GetNameList(0, [])
    _check(r[-1], "RespCombo.GetNameList")
    return cases, {str(n) for n in (r[1] or ())}


def define_load_combos(conn: SAP2000Connection, case_map: dict,
                       prefix: str = "", envelope: bool = True,
                       replace: bool = True) -> dict:
    """Create ASCE 7-22 LRFD strength combos in the current SAP2000 model.

    Args:
        case_map: ASCE load type -> model load case name(s), e.g.
            {"D": "DEAD", "L": "LIVE", "S": "SNOW", "W": ["WX"]}. 'D' required.
        prefix:   prepended to combo names (e.g. "WX-" when calling per wind
            direction). Names are like "LC2", "LC3a", "LC3b".
        envelope: also create an envelope combo "<prefix>LRFD-ENV" over all
            generated combos.
        replace:  delete any same-named existing combos first.

    Returns dict with the created combos (name/equation/code_ref/case factors),
    skipped combo ids with reasons, and the envelope name.
    """
    model = conn.model
    combos, skipped = generate_lrfd_combos(case_map)
    cm = _norm_case_map(case_map)
    case_names, combo_names = _model_case_and_combo_names(model)

    missing = [n for names in cm.values() for n in names
               if n not in case_names and n not in combo_names]
    if missing:
        raise RuntimeError(
            f"case_map names not found in model: {missing}. "
            f"Defined cases: {sorted(case_names)}")

    created = []
    for c in combos:
        name = f"{prefix}{c['id']}{c['variant']}"
        if replace and name in combo_names:
            _check(model.RespCombo.Delete(name), f"RespCombo.Delete({name})")
        _check(model.RespCombo.Add(name, _COMBO_LINEAR_ADD),
               f"RespCombo.Add({name})")
        applied = []
        for ltype, factor in c["factors"].items():
            for cname in cm[ltype]:
                ctype = _CTYPE_CASE if cname in case_names else _CTYPE_COMBO
                r = model.RespCombo.SetCaseList(name, ctype, cname, factor)
                _check(r, f"RespCombo.SetCaseList({name}, {cname})")
                applied.append({"case": cname, "factor": factor})
        created.append({"name": name, "equation": c["equation"],
                        "code_ref": c["code_ref"], "note": c["note"],
                        "cases": applied})

    env_name = None
    if envelope and created:
        env_name = f"{prefix}LRFD-ENV"
        if replace and env_name in combo_names:
            _check(model.RespCombo.Delete(env_name),
                   f"RespCombo.Delete({env_name})")
        _check(model.RespCombo.Add(env_name, _COMBO_ENVELOPE),
               f"RespCombo.Add({env_name})")
        for c in created:
            r = model.RespCombo.SetCaseList(env_name, _CTYPE_COMBO,
                                            c["name"], 1.0)
            _check(r, f"RespCombo.SetCaseList({env_name}, {c['name']})")

    return {"status": "ok", "combos": created, "skipped": skipped,
            "envelope": env_name,
            "assumptions": [
                "ASCE 7-22 LRFD; live-load reduction (4.7) not applied",
                "companion L taken at 1.0 in combos 4/6 (0.5L exception not used)",
                "W/E direction reversal is the engineer's responsibility "
                "(map directional cases or call per direction with a prefix)",
            ]}
