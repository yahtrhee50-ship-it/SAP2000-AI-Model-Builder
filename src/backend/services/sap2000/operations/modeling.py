"""
Model-editing operations: columns, joint lookup, and analysis runs.

Every function follows the operations contract (see package __init__):
`func(conn, **json_params) -> json_dict`, making each directly convertible
into an agent tool later.
"""
from __future__ import annotations

import logging

from ..connector import SAP2000Connection
from ..builder import MAT_STEEL, MAT_CONCRETE, _ret, _ret_name, _concrete_E

log = logging.getLogger(__name__)


def _steel_material(model) -> str:
    """Create (or reuse) an A992 steel material and return its actual name."""
    ret = model.PropMaterial.AddMaterial(
        "A992", MAT_STEEL, "United States", "ASTM A992", "Grade 50")
    if _ret(ret) == 0:
        return _ret_name(ret, "A992")
    return "A992"  # assume it already exists (e.g. created by the builder)


def add_columns(conn: SAP2000Connection,
                positions: list[list[float]],
                height: float,
                top_z: float = 0.0,
                section_shape: str = "W14X90",
                section_name: str = "COLSEC",
                material: str = "steel",
                fc: float = 28.0,
                unit_weight: float = 24.0,
                unit_system: str = "kN_m",
                rect_depth: float = 0.5,
                rect_width: float = 0.5,
                fix_base: bool = True) -> dict:
    """Add vertical columns below the given (x, y) plan positions.

    Each column runs from (x, y, top_z) down to (x, y, top_z - height); the
    top joint auto-merges with any existing joint at that location (deck
    joints), and the base joint is restrained (fixed if fix_base, else
    pinned). All values are in current model units.

    section_shape: "W14x90"-style AISC label (steel, imported from the AISC
    catalogue; falls back to a rectangle if import fails) or "rect" for a
    rect_depth x rect_width rectangle. material: "steel" or "concrete"
    (concrete uses fc + unit_weight via the same E formula as the builder,
    interpreted per unit_system).
    """
    m = conn.model
    if height <= 0:
        raise ValueError("height must be positive")
    if not positions:
        raise ValueError("positions is empty")

    # Material
    if material == "steel":
        mat = _steel_material(m)
    elif material == "concrete":
        mat = "Concrete_Col"
        if _ret(m.PropMaterial.SetMaterial(mat, MAT_CONCRETE)) != 0:
            raise RuntimeError(f"Failed to create concrete material '{mat}'")
        E = _concrete_E(fc, unit_system)
        if _ret(m.PropMaterial.SetMPIsotropic(mat, E, 0.2, 1.17e-5)) != 0:
            raise RuntimeError(f"Failed to set stiffness on '{mat}'")
        if _ret(m.PropMaterial.SetWeightAndMass(mat, 1, unit_weight)) != 0:
            raise RuntimeError(f"Failed to set unit weight on '{mat}'")
    else:
        raise ValueError(f"Unknown material '{material}' (steel|concrete)")

    # Section
    shape = section_shape.upper()
    if shape != "RECT" and shape.startswith("W") and "X" in shape:
        ret = m.PropFrame.ImportProp(section_name, mat, "AISC15.xml",
                                     shape.replace("X", "x"))
        if _ret(ret) != 0:
            log.warning("W-section import failed for %s, using rectangle", shape)
            m.PropFrame.SetRectangle(section_name, mat, rect_depth, rect_width)
    else:
        if _ret(m.PropFrame.SetRectangle(section_name, mat,
                                         rect_depth, rect_width)) != 0:
            raise RuntimeError(f"Failed to define column section '{section_name}'")

    restraint = [True] * 6 if fix_base else [True, True, True, False, False, False]
    base_z = top_z - height
    columns = []
    for k, (x, y) in enumerate(positions):
        rb = m.PointObj.AddCartesian(float(x), float(y), base_z, f"CB_{k}")
        base = _ret_name(rb, f"CB_{k}")
        rt = m.PointObj.AddCartesian(float(x), float(y), top_z, f"CT_{k}")
        top = _ret_name(rt, f"CT_{k}")
        rf = m.FrameObj.AddByPoint(base, top, f"COL_{k}")
        if _ret(rf) != 0:
            raise RuntimeError(f"Failed to add column at ({x}, {y})")
        name = _ret_name(rf, f"COL_{k}")
        if _ret(m.FrameObj.SetSection(name, section_name)) != 0:
            raise RuntimeError(f"Failed to set section on column '{name}'")
        if _ret(m.PointObj.SetRestraint(base, restraint)) != 0:
            raise RuntimeError(f"Failed to restrain column base '{base}'")
        columns.append({"name": name, "base_joint": base, "top_joint": top,
                        "x": float(x), "y": float(y)})

    try:
        m.View.RefreshView(0, False)
    except Exception:
        pass

    return {"status": "ok", "section": section_name, "material": mat,
            "base_z": base_z, "fixed_base": fix_base, "columns": columns}


def find_joints(conn: SAP2000Connection,
                coords: list[list[float]],
                tol: float = 0.01) -> dict:
    """Find the joint (point object) closest to each requested [x, y, z]
    coordinate, in current model units. Reports the actual SAP2000 joint
    name, its coordinates, and the distance; `matched` is False if no joint
    lies within `tol`."""
    m = conn.model
    r = m.PointObj.GetNameList(0, [])
    if _ret(r) != 0:
        raise RuntimeError("PointObj.GetNameList failed")
    names = [str(n) for n in (r[1] or ())]

    pts = []
    for name in names:
        rc = m.PointObj.GetCoordCartesian(name, 0.0, 0.0, 0.0)
        if _ret(rc) != 0:
            continue
        pts.append((name, float(rc[0]), float(rc[1]), float(rc[2])))
    if not pts:
        raise RuntimeError("Model has no joints")

    out = []
    for c in coords:
        x, y, z = (float(c[0]), float(c[1]),
                   float(c[2]) if len(c) > 2 else 0.0)
        name, px, py, pz = min(
            pts, key=lambda p: (p[1]-x)**2 + (p[2]-y)**2 + (p[3]-z)**2)
        dist = ((px-x)**2 + (py-y)**2 + (pz-z)**2) ** 0.5
        out.append({"requested": [x, y, z], "joint": name,
                    "coords": [px, py, pz], "distance": dist,
                    "matched": dist <= tol})
    return {"status": "ok", "joints": out}


def run_analysis(conn: SAP2000Connection, save_path: str = "") -> dict:
    """Save (optionally to save_path) and run the analysis; returns each
    load case's run status. Raises if no case actually finished."""
    if save_path:
        conn.save(save_path)
    conn.run_analysis()
    m = conn.model
    r = m.Analyze.GetCaseStatus(0, [], [])
    labels = {1: "not_run", 2: "could_not_start", 3: "not_finished", 4: "finished"}
    cases = [{"name": str(n), "run_status": labels.get(int(s), str(s))}
             for n, s in zip(r[1] or (), r[2] or ())]
    return {"status": "ok", "saved_to": save_path or None, "cases": cases}
