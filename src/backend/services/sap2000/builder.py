"""
Translates a StructuralModel into SAP2000 API calls.
Uses the thick-shell area object for concrete slabs.
"""
from __future__ import annotations
import logging
from ...models.structural import StructuralModel, UnitSystem
from .connector import SAP2000Connection

log = logging.getLogger(__name__)

# SAP2000 material type codes
MAT_STEEL = 1
MAT_CONCRETE = 2

# SAP2000 shell type codes
SHELL_THIN = 1
SHELL_THICK = 2     # thick plate (includes shear deformation)
SHELL_MEMBRANE = 3

# Load pattern type codes (eLoadPatternType)
LOAD_DEAD = 1
LOAD_LIVE = 3

# ── Vehicle library ──────────────────────────────────────────────────────────
#
# EVERY truck is built as a GENERAL vehicle ("Vehicles 2/3 - General Vehicles"),
# never a SAP2000 LIBRARY standard vehicle ("Vehicles 1"). On a non-Bridge
# SAP2000 license a library vehicle carries width / length / floating-axle /
# response-component features the program cannot represent: it opens with a
# CSiBridge conversion warning and is stripped to a flat load. General vehicles
# carry none of those features and reproduce the discrete axle train (the
# encoding verified live to 0.01% against influence-line statics).
#
# AASHTO code vehicles are defined here from published AASHTO LRFD data — axle
# loads in kip, spacings in ft, design-lane load in kip/ft (converted to model
# units at build time). Each entry is a list of sub-vehicles
# (VehName, axle_loads, axle_spacings, lane_load, source); the vehicle CLASS
# (MLCLASS) envelopes them, so HL-93 = max of design truck / design tandem, each
# superimposed with the design-lane load. VERIFY against the current AASHTO LRFD
# before final use.
AASHTO_VEHICLES = {
    "HS20": [
        ("HS20-44", [8.0, 32.0, 32.0], [14.0, 14.0], 0.0,
         "AASHTO LRFD 3.6.1.2.2 design truck (HS20); rear axle spacing "
         "14-30 ft variable modeled at 14 ft (governs typical spans)"),
    ],
    "HS15": [
        ("HS15-44", [6.0, 24.0, 24.0], [14.0, 14.0], 0.0,
         "AASHTO LRFD design truck scaled 0.75 (HS15); rear axle spacing "
         "14-30 ft variable modeled at 14 ft"),
    ],
    "HL-93": [
        ("HL93TRUCK", [8.0, 32.0, 32.0], [14.0, 14.0], 0.64,
         "AASHTO LRFD 3.6.1.2.2/3.6.1.2.4 design truck + 0.64 klf design-lane "
         "load; rear spacing 14-30 ft variable modeled at 14 ft"),
        ("HL93TANDEM", [25.0, 25.0], [4.0], 0.64,
         "AASHTO LRFD 3.6.1.2.3/3.6.1.2.4 design tandem + 0.64 klf design-lane "
         "load"),
    ],
}
# Case/space-insensitive aliases (truck_type is upper()/despaced before lookup).
AASHTO_VEHICLES["HS20-44"] = AASHTO_VEHICLES["HS20"]
AASHTO_VEHICLES["HS15-44"] = AASHTO_VEHICLES["HS15"]
AASHTO_VEHICLES["HL93"] = AASHTO_VEHICLES["HL-93"]

# Caltrans permit trucks: axle data must be source-confirmed by the engineer
# (Reference caltrans-vehicles.md — do not rely on memorized permit axle
# weights/spacings). Requesting one raises with guidance to supply
# truck_axle_loads / truck_axle_spacings.
CALTRANS_PERMIT_TRUCKS = {"P5", "P7", "P9", "P11", "P13"}

DEFAULT_TRUCK = "P13"  # Caltrans permit truck — requires engineer-supplied axles


def _general_vehicle_rows(veh_name: str, axle_loads, spacings, unif_load=0.0):
    """Build the SAP2000 general-vehicle table rows for one vehicle, in MODEL
    units. Returns (general_row, load_rows):
      general_row -> "Vehicles 2 - General Vehicles 1 - General"
                     [VehName, NumInter, StayInLane]
      load_rows   -> "Vehicles 3 - General Vehicles 2 - Loads"
                     [VehName, LoadType, InterUnif, InterAxle, InterMinD,
                      InterMaxD] — a Leading Load first axle then Fixed Length
                     axles at the given spacings.
    unif_load is a uniform (design-lane) load per unit length carried on every
    segment (0 for a pure axle train). Raises ValueError on malformed input
    (need N axle loads > 0 and N-1 spacings > 0)."""
    axles = [float(a) for a in axle_loads]
    gaps = [float(s) for s in (spacings or [])]
    if (not axles or len(gaps) != len(axles) - 1
            or any(a <= 0 for a in axles) or any(g <= 0 for g in gaps)):
        raise ValueError(
            f"vehicle {veh_name}: need N axle loads > 0 (model force units) and "
            f"N-1 spacings > 0 (model length units); got {len(axles)} loads, "
            f"{len(gaps)} spacings")
    u = f"{unif_load:g}"
    load_rows = [[veh_name, "Leading Load", u, f"{axles[0]:g}", "", ""]]
    for axle, gap in zip(axles[1:], gaps):
        load_rows.append(
            [veh_name, "Fixed Length", u, f"{axle:g}", f"{gap:g}", ""])
    if unif_load:
        # The leading/inter-axle uniform loads only cover the vehicle and the
        # lane AHEAD of it; without a trailing row the lane behind the last
        # axle is never loaded and lane-load envelopes under-report (verified
        # live: HL-93 midspan M3 came out 1047 vs 1088 kip-ft on a 60 ft span
        # — exactly the missing w*integral behind the rear axle).
        load_rows.append([veh_name, "Trailing Load", u, "", "", ""])
    general_row = [veh_name, str(len(load_rows)), "No"]
    return general_row, load_rows

# Model input convention per unit system (values in StructuralModel must already
# be in these units — the interview prompt instructs the AI to convert):
#   kN_m:   length m,  fc MPa, unit weight kN/m3,  area loads kN/m2
#   kip_ft: length ft, fc ksi, unit weight kip/ft3, area loads ksf
#   kip_in: length in, fc ksi, unit weight kip/in3, area loads kip/in2
# kip_to_force / ft_to_len convert the AASHTO_VEHICLES data (canonical kip, ft,
# kip/ft) into each model unit system.
UNIT_INFO = {
    "kN_m":   {"len": "m",  "force": "kN",  "press": "kN/m2",   "mm_to_len": 1 / 1000.0,  "thermal": 1.17e-5, "lane_width": 3.6,   "kip_to_force": 4.4482216, "ft_to_len": 0.3048},
    "kip_ft": {"len": "ft", "force": "kip", "press": "ksf",     "mm_to_len": 1 / 304.8,   "thermal": 6.5e-6,  "lane_width": 12.0,  "kip_to_force": 1.0,       "ft_to_len": 1.0},
    "kip_in": {"len": "in", "force": "kip", "press": "kip/in2", "mm_to_len": 1 / 25.4,    "thermal": 6.5e-6,  "lane_width": 144.0, "kip_to_force": 1.0,       "ft_to_len": 12.0},
}


def _concrete_E(fc: float, unit_system: str) -> float:
    """Concrete modulus of elasticity in the model's stress units.

    kN_m:   ACI 318 metric  Ec = 4700*sqrt(fc MPa) MPa  -> kPa
    kip_ft: ACI 318         Ec = 57000*sqrt(fc psi) psi = 1802.5*sqrt(fc ksi) ksi -> ksf
    kip_in: same formula, kept in ksi
    """
    if unit_system == "kip_ft":
        return 1802.5 * (fc ** 0.5) * 144.0
    if unit_system == "kip_in":
        return 1802.5 * (fc ** 0.5)
    return 4700.0 * (fc ** 0.5) * 1000.0


def _ret(result) -> int:
    """Extract the integer error code from a COM return value.

    comtypes vtable returns vary by call:
      2-element: [name_or_echo, retcode]          e.g. AddCartesian, SetRestraint
      3-element: [input_echo, actual_name, retcode] e.g. AreaObj.AddByPoint
    The error code is always the LAST element.
    """
    if isinstance(result, (list, tuple)):
        return int(result[-1])
    if result is None:
        return 0
    return int(result)


def _ret_name(result, fallback: str) -> str:
    """Extract the actual object name SAP2000 assigned (may differ from requested).

    The assigned name is the second-to-last element in the return list,
    which works for both 2-element [name, retcode] and 3-element [echo, name, retcode].
    """
    if isinstance(result, (list, tuple)) and len(result) >= 2:
        return str(result[-2])
    return fallback


class ModelBuilder:

    def __init__(self, conn: SAP2000Connection):
        self._m = conn.model
        self._steel_mat   = "A992"      # updated by _define_materials
        self._concrete_mat = "Concrete"  # updated by _define_materials
        self._concrete_created = False   # set by _define_materials
        self._units = "kN_m"             # updated by build()
        # {girder row index: [(start_coord, actual_frame_name), ...]} — filled by
        # _add_frames, consumed by _define_moving_load for lane definition
        self._girder_rows: dict[int, list[tuple[float, str]]] = {}
        # {("X"|"Y", grid line index): [(start_coord, actual_frame_name), ...]}
        # — every frame line (girders + frame groups), for lane targeting
        self._frame_lines: dict[tuple[str, int], list[tuple[float, str]]] = {}

    def build(self, model: StructuralModel) -> dict:
        """Build the full SAP2000 model. Returns a report dict."""
        report: dict[str, list] = {
            "materials": [],
            "sections": [],
            "joints": [],
            "frames": [],
            "areas": [],
            "loads": [],
            "errors": [],
        }
        self._units = model.project.unit_system.value

        # Flush any interactive-table edits left queued by a previous COM
        # client (e.g. a crashed script). A stale queue makes the FIRST
        # ApplyEditedTables of this build fail with the empty-queue
        # signature (ret!=0, 0 fatal, 0 errors, empty log) — observed live
        # 2026-07-11 on an attached instance.
        try:
            self._m.DatabaseTables.CancelTableEditing()
        except Exception:
            pass

        try:
            self._define_materials(model, report)
            self._define_sections(model, report)
            joints = self._add_joints(model, report)
            self._add_supports(model, joints, report)
            self._add_frames(model, joints, report)
            self._add_slab(model, report)
            self._define_loads(model, report)
            self._assign_area_loads(model, report)
            self._define_moving_load(model, report)
            self._refresh_view()
        except Exception as exc:
            report["errors"].append(str(exc))
            log.exception("Error building SAP2000 model")

        return report

    # ── Materials ─────────────────────────────────────────────────────────────

    def _define_materials(self, model: StructuralModel, report: dict) -> None:
        m = self._m

        # Steel for beams/girders
        # SAP2000 may auto-rename 'A992' to 'A992Fy50-1' when ASTM spec is supplied;
        # capture the actual name so frame sections can reference it correctly.
        ret = m.PropMaterial.AddMaterial("A992", MAT_STEEL, "United States", "ASTM A992", "Grade 50")
        if _ret(ret) == 0:
            self._steel_mat = _ret_name(ret, "A992")
            report["materials"].append("A992 Steel")

        # Concrete for slab.
        # SetMaterial (not AddMaterial) — AddMaterial requires a valid region/standard/
        # grade and fails with empty strings, leaving the slab with no concrete material.
        if model.slab:
            name = model.slab.material_name
            ret = m.PropMaterial.SetMaterial(name, MAT_CONCRETE)
            if _ret(ret) != 0:
                report["errors"].append(f"Failed to create concrete material '{name}'")
                return
            self._concrete_mat = name
            self._concrete_created = True
            info = UNIT_INFO[self._units]
            E = _concrete_E(model.slab.concrete_fc, self._units)
            if _ret(m.PropMaterial.SetMPIsotropic(name, E, 0.2, info["thermal"])) != 0:
                report["errors"].append(f"Failed to set concrete stiffness on '{name}'")
            # Option 1 = weight per unit volume (0 is not a valid option code)
            if _ret(m.PropMaterial.SetWeightAndMass(name, 1, model.slab.unit_weight)) != 0:
                report["errors"].append(f"Failed to set concrete unit weight on '{name}'")
            report["materials"].append(f"Concrete {name} (E={E:.0f} {info['press']})")

        # Concrete for frame groups when there is no slab to define it from.
        # Uses a default fc (4 ksi imperial / 28 MPa metric) and normal weight;
        # with a slab, frame concrete shares the slab material (same fc).
        needs_conc = any(
            g.section.material.lower().startswith("conc") for g in model.frame_groups)
        if needs_conc and not self._concrete_created:
            name = "Concrete_Frame"
            if _ret(m.PropMaterial.SetMaterial(name, MAT_CONCRETE)) != 0:
                report["errors"].append(f"Failed to create concrete material '{name}'")
                return
            info = UNIT_INFO[self._units]
            fc = 4.0 if self._units.startswith("kip") else 28.0
            uw = {"kip_ft": 0.15, "kip_in": 0.15 / 1728.0}.get(self._units, 24.0)
            E = _concrete_E(fc, self._units)
            m.PropMaterial.SetMPIsotropic(name, E, 0.2, info["thermal"])
            m.PropMaterial.SetWeightAndMass(name, 1, uw)
            self._concrete_mat = name
            self._concrete_created = True
            report["materials"].append(
                f"Concrete {name} (default fc={fc:g}, E={E:.0f} {info['press']})")

    # ── Section properties ─────────────────────────────────────────────────────

    def _define_sections(self, model: StructuralModel, report: dict) -> None:
        m = self._m

        if model.girders:
            self._add_frame_section(model.girders.section, self._steel_mat, report)

        if model.beams:
            self._add_frame_section(model.beams.section, self._steel_mat, report)

        for grp in model.frame_groups:
            if grp.section.material.lower().startswith("conc"):
                mat = self._concrete_mat
                if not self._concrete_created:
                    report["errors"].append(
                        f"Frame group '{grp.name}' wants concrete but no "
                        f"concrete material was created")
                    continue
            else:
                mat = self._steel_mat
            self._add_frame_section(grp.section, mat, report)

        if model.slab:
            slab = model.slab
            # SetShell_1: name, ShellType, IncludeDrillingDOF, MatProp, MatAng, Thickness, Bending
            ret = m.PropArea.SetShell_1(
                "SlabSection",
                SHELL_THICK,
                True,
                self._concrete_mat,
                0.0,
                slab.thickness,
                slab.thickness,
            )
            if _ret(ret) == 0:
                report["sections"].append(
                    f"Thick shell slab t={slab.thickness}{UNIT_INFO[self._units]['len']}"
                )
            else:
                report["errors"].append("Failed to define slab shell section")

    def _add_frame_section(self, sec, mat_name: str, report: dict) -> None:
        m = self._m
        stype = sec.section_type.upper()

        mm = UNIT_INFO[self._units]["mm_to_len"]  # mm -> model length units

        if stype.startswith("W") and "X" in stype:
            # Standard W-section — import from AISC catalogue
            ret = m.PropFrame.ImportProp(
                sec.name, mat_name,
                "AISC15.xml",
                stype.replace("X", "x"),
            )
            if _ret(ret) != 0:
                # Fallback: rectangular placeholder sized to rough W-section depth/width
                log.warning("W-section import failed for %s (mat=%s), using rectangle fallback", stype, mat_name)
                d = sec.depth_mm * mm if sec.depth_mm else 610 * mm
                b = sec.flange_width_mm * mm if sec.flange_width_mm else 230 * mm
                m.PropFrame.SetRectangle(sec.name, mat_name, d, b)
        else:
            # Custom rectangular placeholder
            d = sec.depth_mm * mm if sec.depth_mm else 500 * mm
            b = sec.flange_width_mm * mm if sec.flange_width_mm else 200 * mm
            m.PropFrame.SetRectangle(sec.name, mat_name, d, b)

        report["sections"].append(sec.name)

    # ── Joints ─────────────────────────────────────────────────────────────────

    def _add_joints(self, model: StructuralModel, report: dict) -> dict[tuple, str]:
        """Add all grid joints and return {(x,y): joint_name} map."""
        m = self._m
        joints: dict[tuple, str] = {}

        if not model.grid:
            return joints

        xc = model.grid.x_coords
        yc = model.grid.y_coords

        for i, x in enumerate(xc):
            for j, y in enumerate(yc):
                name = f"J_{i}_{j}"
                ret = m.PointObj.AddCartesian(x, y, 0.0, name)
                if _ret(ret) == 0:
                    actual = _ret_name(ret, name)
                    joints[(x, y)] = actual  # actual SAP2000 name for API calls
                    report["joints"].append(name)  # requested name for report/checks

        return joints

    # ── Supports ──────────────────────────────────────────────────────────────

    def _add_supports(self, model: StructuralModel, joints: dict, report: dict) -> None:
        m = self._m
        for pile in model.piles:
            # Find closest joint
            closest = min(joints.keys(), key=lambda k: (k[0]-pile.x)**2 + (k[1]-pile.y)**2)
            jname = joints[closest]
            # comtypes: SetRestraint may raise COMError on failure rather than return non-zero.
            # Coerce to plain Python list of bools so comtypes can marshal SAFEARRAY(VARIANT_BOOL).
            dof = [bool(v) for v in pile.restraint]
            try:
                ret = m.PointObj.SetRestraint(jname, dof)
                if _ret(ret) == 0:
                    report["joints"].append(f"Support at {jname}")
            except Exception as exc:
                log.warning("SetRestraint(%s) failed: %s", jname, exc)

            if pile.spring_stiffness:
                try:
                    m.PointObj.SetSpring(jname, pile.spring_stiffness)
                except Exception:
                    pass

    # ── Frames ─────────────────────────────────────────────────────────────────

    def _add_frames(self, model: StructuralModel, joints: dict, report: dict) -> None:
        m = self._m
        if not model.grid:
            return

        xc = model.grid.x_coords
        yc = model.grid.y_coords

        # Girders
        if model.girders:
            sec_name = model.girders.section.name
            direction = model.girders.direction
            row_indices = model.girders.row_indices

            if direction == "X":
                for ri in row_indices:
                    if ri >= len(yc):
                        continue
                    y = yc[ri]
                    for i in range(len(xc) - 1):
                        p1 = joints.get((xc[i], y))
                        p2 = joints.get((xc[i+1], y))
                        if p1 and p2:
                            name = f"G_X_{ri}_{i}"
                            ret = m.FrameObj.AddByPoint(p1, p2, name)
                            if _ret(ret) == 0:
                                actual = _ret_name(ret, name)
                                m.FrameObj.SetSection(actual, sec_name)
                                # SAP2000 may assign its own object name; report the
                                # actual one so results can be queried by callers
                                report["frames"].append(
                                    name if actual == name else f"{name} -> {actual}"
                                )
                                self._girder_rows.setdefault(ri, []).append((xc[i], actual))
                                self._frame_lines.setdefault(("X", ri), []).append((xc[i], actual))
            else:
                for ci in row_indices:
                    if ci >= len(xc):
                        continue
                    x = xc[ci]
                    for j in range(len(yc) - 1):
                        p1 = joints.get((x, yc[j]))
                        p2 = joints.get((x, yc[j+1]))
                        if p1 and p2:
                            name = f"G_Y_{ci}_{j}"
                            ret = m.FrameObj.AddByPoint(p1, p2, name)
                            if _ret(ret) == 0:
                                actual = _ret_name(ret, name)
                                m.FrameObj.SetSection(actual, sec_name)
                                report["frames"].append(
                                    name if actual == name else f"{name} -> {actual}"
                                )
                                self._girder_rows.setdefault(ci, []).append((yc[j], actual))
                                self._frame_lines.setdefault(("Y", ci), []).append((yc[j], actual))

        # Secondary beams
        if model.beams:
            sec_name = model.beams.section.name
            col_indices = model.beams.col_indices or list(range(len(xc) if model.girders and model.girders.direction == "Y" else len(yc)))
            girder_dir = model.girders.direction if model.girders else "X"

            if girder_dir == "X":
                # Beams run Y-direction
                for ci in col_indices:
                    if ci >= len(xc):
                        continue
                    x = xc[ci]
                    for j in range(len(yc) - 1):
                        p1 = joints.get((x, yc[j]))
                        p2 = joints.get((x, yc[j+1]))
                        if p1 and p2:
                            name = f"B_{ci}_{j}"
                            ret = m.FrameObj.AddByPoint(p1, p2, name)
                            if _ret(ret) == 0:
                                actual = _ret_name(ret, name)
                                m.FrameObj.SetSection(actual, sec_name)
                                report["frames"].append(
                                    name if actual == name else f"{name} -> {actual}"
                                )
            else:
                # Beams run X-direction
                for ri in col_indices:
                    if ri >= len(yc):
                        continue
                    y = yc[ri]
                    for i in range(len(xc) - 1):
                        p1 = joints.get((xc[i], y))
                        p2 = joints.get((xc[i+1], y))
                        if p1 and p2:
                            name = f"B_{ri}_{i}"
                            ret = m.FrameObj.AddByPoint(p1, p2, name)
                            if _ret(ret) == 0:
                                actual = _ret_name(ret, name)
                                m.FrameObj.SetSection(actual, sec_name)
                                report["frames"].append(
                                    name if actual == name else f"{name} -> {actual}"
                                )

        # Frame groups (any number of member families, each with its own section)
        for grp in model.frame_groups:
            sec_name = grp.section.name
            if grp.direction == "X":
                for ri in grp.line_indices:
                    if ri >= len(yc):
                        continue
                    y = yc[ri]
                    for i in range(len(xc) - 1):
                        p1 = joints.get((xc[i], y))
                        p2 = joints.get((xc[i+1], y))
                        if p1 and p2:
                            name = f"{grp.name}_X_{ri}_{i}"
                            ret = m.FrameObj.AddByPoint(p1, p2, name)
                            if _ret(ret) == 0:
                                actual = _ret_name(ret, name)
                                m.FrameObj.SetSection(actual, sec_name)
                                report["frames"].append(
                                    name if actual == name else f"{name} -> {actual}"
                                )
                                self._frame_lines.setdefault(("X", ri), []).append((xc[i], actual))
            else:
                for ci in grp.line_indices:
                    if ci >= len(xc):
                        continue
                    x = xc[ci]
                    for j in range(len(yc) - 1):
                        p1 = joints.get((x, yc[j]))
                        p2 = joints.get((x, yc[j+1]))
                        if p1 and p2:
                            name = f"{grp.name}_Y_{ci}_{j}"
                            ret = m.FrameObj.AddByPoint(p1, p2, name)
                            if _ret(ret) == 0:
                                actual = _ret_name(ret, name)
                                m.FrameObj.SetSection(actual, sec_name)
                                report["frames"].append(
                                    name if actual == name else f"{name} -> {actual}"
                                )
                                self._frame_lines.setdefault(("Y", ci), []).append((yc[j], actual))

    # ── Slab ──────────────────────────────────────────────────────────────────

    def _add_slab(self, model: StructuralModel, report: dict) -> None:
        if not model.slab or not model.grid:
            return

        m = self._m
        xc = model.grid.x_coords
        yc = model.grid.y_coords
        mesh = model.slab.mesh_size

        for i in range(len(xc) - 1):
            for j in range(len(yc) - 1):
                x0, x1 = xc[i], xc[i+1]
                y0, y1 = yc[j], yc[j+1]

                # Subdivide panel into mesh elements
                nx = max(1, round((x1 - x0) / mesh))
                ny = max(1, round((y1 - y0) / mesh))
                dx = (x1 - x0) / nx
                dy = (y1 - y0) / ny

                for ix in range(nx):
                    for iy in range(ny):
                        cx0 = x0 + ix * dx
                        cx1 = cx0 + dx
                        cy0 = y0 + iy * dy
                        cy1 = cy0 + dy

                        # 4-point area (counterclockwise)
                        pts = [
                            [cx0, cy0, 0.0],
                            [cx1, cy0, 0.0],
                            [cx1, cy1, 0.0],
                            [cx0, cy1, 0.0],
                        ]
                        # Add joints for each corner; use actual names SAP2000 assigns
                        jnames = []
                        for px, py, pz in pts:
                            jname = f"SJ_{round(px*1000)}_{round(py*1000)}"
                            r = m.PointObj.AddCartesian(px, py, pz, jname)
                            jnames.append(_ret_name(r, jname))

                        area_name = f"S_{i}_{j}_{ix}_{iy}"
                        # comtypes: pass a plain list of str so SAFEARRAY(BSTR) marshals correctly.
                        # On success returns (actual_name, 0); on failure raises COMError.
                        try:
                            ret = m.AreaObj.AddByPoint(4, [str(j) for j in jnames], area_name)
                            if _ret(ret) == 0:
                                actual_area = _ret_name(ret, area_name)
                                m.AreaObj.SetProperty(actual_area, "SlabSection")
                                report["areas"].append(actual_area)
                        except Exception as exc:
                            log.warning("AreaObj.AddByPoint(%s) failed: %s", area_name, exc)

    # ── Loads ─────────────────────────────────────────────────────────────────

    def _define_loads(self, model: StructuralModel, report: dict) -> None:
        if not model.loads:
            return

        m = self._m
        ld = model.loads

        # Self-weight pattern (selfWtMultiplier=1.0 captures beams, girders, slab weight)
        m.LoadPatterns.Add("DEAD", LOAD_DEAD, 1.0, True)

        # Superimposed dead load pattern (no self-weight, added separately as area load)
        m.LoadPatterns.Add("SDL", LOAD_DEAD, 0.0, True)

        # Live load pattern (AddLoadCase=True so an analyzable linear static
        # case "LL" exists — with False the pattern has no results case at all)
        m.LoadPatterns.Add("LL", LOAD_LIVE, 0.0, True)

        press = UNIT_INFO[self._units]["press"]
        report["loads"].append(
            "Patterns: DEAD (SW x1.0), SDL=%.4g %s, LL=%.4g %s"
            % (ld.dead_load, press, ld.live_load, press)
        )

    def _assign_area_loads(self, model: StructuralModel, report: dict) -> None:
        """Apply uniform SDL and LL pressure loads to all slab area elements."""
        if not model.loads or not model.slab:
            return

        m  = self._m
        ld = model.loads

        # LoadDir=6 = Gravity direction (Global -Z). Dir 6 is only valid with the
        # Global CSys — with CSys="Local" SAP2000 rejects the call (local dirs are 1-3),
        # so loads were silently dropped before this was fixed.
        # Replace=True so loads don't stack on re-runs.
        failed = 0
        for area_name in report["areas"]:
            if ld.dead_load > 0:
                if _ret(m.AreaObj.SetLoadUniform(area_name, "SDL", -ld.dead_load, 6, True, "Global")) != 0:
                    failed += 1
            if ld.live_load > 0:
                if _ret(m.AreaObj.SetLoadUniform(area_name, "LL", -ld.live_load, 6, True, "Global")) != 0:
                    failed += 1

        if failed:
            report["errors"].append(f"{failed} area load assignments failed")
        if report["areas"]:
            press = UNIT_INFO[self._units]["press"]
            report["loads"].append(
                "SDL %.4g %s and LL %.4g %s applied to %d area elements"
                % (ld.dead_load, press, ld.live_load, press, len(report["areas"]))
            )

    # ── Moving load (lane + standard vehicle + moving load case) ──────────────
    #
    # The BridgeModeler_1 COM interfaces (Lane/Vehicle/VehicleClass) return -100
    # on this installation, so lanes and vehicles are created through the
    # interactive database tables instead (DatabaseTables.SetTableForEditingArray
    # + ApplyEditedTables), which works on a plain frame/shell model. The moving
    # load CASE itself uses the documented classic API (LoadCases.Moving.*),
    # which works once lanes and vehicle classes exist.

    def _edit_table(self, key: str, fields: list[str], rows: list[list[str]]) -> int:
        flat = [str(c) for row in rows for c in row]
        ret = self._m.DatabaseTables.SetTableForEditingArray(key, 1, fields, len(rows), flat)
        return _ret(ret)

    def _table_rows(self, key: str) -> tuple[list[str], list[list[str]]]:
        """Read a database table. Returns (fields, rows); ([], []) if empty/unreadable."""
        try:
            r = self._m.DatabaseTables.GetTableForDisplayArray(key, [], "", 0, [], 0, [])
            if _ret(r) != 0:
                return [], []
            fields = [str(f) for f in (r[2] or ())]
            n = int(r[3] or 0)
            data = list(r[4] or ())
            ncol = len(fields)
            if not ncol:
                return [], []
            rows = [["" if c is None else str(c) for c in data[i * ncol:(i + 1) * ncol]]
                    for i in range(n)]
            return fields, rows
        except Exception:
            return [], []

    def _class_entries(self) -> list[tuple[str, str, str]]:
        """(VehClass, VehName, ScaleFactor) rows from the vehicle-class table."""
        fields, rows = self._table_rows("Vehicles 4 - Vehicle Classes")
        if not fields:
            return []
        try:
            ic = fields.index("VehClass")
            iv = fields.index("VehName")
            isf = fields.index("ScaleFactor")
        except ValueError:
            return []
        return [(row[ic], row[iv], row[isf]) for row in rows]

    def _apply_raw(self) -> tuple[bool, int, int, str]:
        r = self._m.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
        # r = [NumFatalErrors, NumErrorMsgs, NumWarnMsgs, NumInfoMsgs, ImportLog, ret]
        fatal, errs = int(r[0]), int(r[1])
        ok = _ret(r) == 0 and not fatal and not errs
        return ok, fatal, errs, str(r[4] or "")

    @staticmethod
    def _is_first_apply_flake(ok: bool, fatal: int, errs: int, log: str) -> bool:
        """The FIRST ApplyEditedTables on a freshly launched SAP2000 instance
        can fail with ret!=0, 0 fatal, 0 errors and an EMPTY import log (the
        queued edits are eaten; observed live 2026-07-11, twice, both on the
        first build after launch — identical re-queue+apply succeeds)."""
        return (not ok) and fatal == 0 and errs == 0 and not log.strip()

    def _apply_tables(self, report: dict, what: str) -> bool:
        ok, fatal, errs, log = self._apply_raw()
        if not ok:
            report["errors"].append(
                f"Table import for {what} failed ({fatal} fatal, {errs} errors): {log}"
            )
        return ok

    def _write_general_vehicles(self, report: dict, what: str, vehicles) -> list | None:
        """Write one or more general vehicles into the Vehicles 2/3 tables in a
        SINGLE apply. `vehicles` = list of (veh_name, axle_loads, spacings,
        unif_load) in MODEL units. All general rows go into one edit of
        "Vehicles 2" and all load rows into one edit of "Vehicles 3" (a second
        SetTableForEditingArray on the same table would overwrite the first).
        Returns the vehicle names on success, or None (error already appended)."""
        gen_rows, load_rows, names = [], [], []
        for veh_name, axle_loads, spacings, unif_load in vehicles:
            try:
                g, lr = _general_vehicle_rows(veh_name, axle_loads, spacings, unif_load)
            except ValueError as exc:
                report["errors"].append(str(exc))
                return None
            gen_rows.append(g)
            load_rows.extend(lr)
            names.append(veh_name)
        for attempt in (1, 2):
            if (self._edit_table("Vehicles 2 - General Vehicles 1 - General",
                                 ["VehName", "NumInter", "StayInLane"],
                                 gen_rows) != 0
                    or self._edit_table(
                        "Vehicles 3 - General Vehicles 2 - Loads",
                        ["VehName", "LoadType", "InterUnif", "InterAxle",
                         "InterMinD", "InterMaxD"], load_rows) != 0):
                report["errors"].append(f"Failed to queue vehicle tables for {what}")
                return None
            ok, fatal, errs, log_txt = self._apply_raw()
            if ok:
                return names
            if attempt == 1 and self._is_first_apply_flake(ok, fatal, errs, log_txt):
                log.warning("First table apply came back empty (fresh-instance "
                            "flake) — re-queueing vehicle tables for %s", what)
                continue
            report["errors"].append(
                f"Table import for {what} failed ({fatal} fatal, {errs} errors): {log_txt}")
            return None
        return None

    def _define_moving_load(self, model: StructuralModel, report: dict) -> None:
        ld = model.loads
        if not ld or not ld.moving_load_enabled:
            return
        if not self._frame_lines:
            report["errors"].append(
                "Moving load requested but no girders or frame groups were created")
            return

        m = self._m
        info = UNIT_INFO[self._units]

        # 1. Vehicle definition — ALWAYS a general vehicle (Vehicles 2/3), never a
        #    SAP2000 library standard vehicle (Vehicles 1), which a non-Bridge
        #    license strips to a flat load (see the Vehicle library note above).
        veh_sources: list[str] = []
        if ld.vehicles:
            # Multi-vehicle class: registry trucks and/or engineer-supplied
            # axle trains, all enveloped together in MLCLASS.
            kf, lf = info["kip_to_force"], info["ft_to_len"]
            vehicles = []
            for vd in ld.vehicles:
                if vd.truck_type:
                    truck = vd.truck_type.upper().replace(" ", "")
                    if truck in CALTRANS_PERMIT_TRUCKS:
                        report["errors"].append(
                            f"vehicle '{vd.name}': '{vd.truck_type}' is a Caltrans "
                            f"permit vehicle — supply axle_loads/axle_spacings in "
                            f"model units from a current Caltrans BDA source.")
                        return
                    entry = AASHTO_VEHICLES.get(truck)
                    if entry is None:
                        report["errors"].append(
                            f"vehicle '{vd.name}': unknown truck_type "
                            f"'{vd.truck_type}'. AASHTO vehicles: "
                            f"{sorted(AASHTO_VEHICLES)}")
                        return
                    for vname, kip_axles, ft_spacings, klf_lane, source in entry:
                        vehicles.append((
                            vname,
                            [a * kf for a in kip_axles],
                            [s * lf for s in ft_spacings],
                            klf_lane * kf / lf,
                        ))
                        veh_sources.append(f"{vname}: {source}")
                else:
                    if not vd.axle_loads:
                        report["errors"].append(
                            f"vehicle '{vd.name}': needs truck_type or "
                            f"axle_loads + axle_spacings")
                        return
                    vehicles.append((
                        vd.name,
                        [float(a) for a in vd.axle_loads],
                        [float(s) for s in (vd.axle_spacings or [])],
                        float(vd.lane_load or 0.0),
                    ))
                    veh_sources.append(
                        f"{vd.name}: engineer-supplied axle train "
                        f"({info['force']}, {info['len']})")
            veh_names = self._write_general_vehicles(report, "vehicles", vehicles)
            if veh_names is None:
                return
            truck_desc = f"{len(veh_names)} vehicle(s): {', '.join(veh_names)}"
        elif ld.truck_axle_loads:
            # Engineer-supplied axle train (values already in model units).
            axles = [float(a) for a in ld.truck_axle_loads]
            spacings = [float(s) for s in (ld.truck_axle_spacings or [])]
            veh_names = self._write_general_vehicles(
                report, "custom vehicle", [("CUSTOM1", axles, spacings, 0.0)])
            if veh_names is None:
                return
            truck_desc = (
                f"custom axle train, {len(axles)} axles, "
                f"total {sum(axles):g} {info['force']}"
            )
        else:
            truck = (ld.truck_type or DEFAULT_TRUCK).upper().replace(" ", "")
            if truck in CALTRANS_PERMIT_TRUCKS:
                report["errors"].append(
                    f"truck_type '{ld.truck_type or DEFAULT_TRUCK}' is a Caltrans "
                    f"permit vehicle. SAP2000 library standard vehicles are "
                    f"unsupported on this non-Bridge license (they open with a "
                    f"CSiBridge conversion warning and degrade to a flat load). "
                    f"Supply truck_axle_loads + truck_axle_spacings in model units "
                    f"({info['force']}, {info['len']}) from a current Caltrans BDA "
                    f"source."
                )
                return
            entry = AASHTO_VEHICLES.get(truck)
            if entry is None:
                report["errors"].append(
                    f"Unknown truck_type '{ld.truck_type}'. AASHTO vehicles: "
                    f"{sorted(AASHTO_VEHICLES)}; Caltrans permit trucks "
                    f"{sorted(CALTRANS_PERMIT_TRUCKS)} require truck_axle_loads / "
                    f"truck_axle_spacings."
                )
                return
            # Convert canonical AASHTO kip / ft / (kip/ft) into model units.
            kf, lf = info["kip_to_force"], info["ft_to_len"]
            vehicles = []
            for vname, kip_axles, ft_spacings, klf_lane, source in entry:
                vehicles.append((
                    vname,
                    [a * kf for a in kip_axles],
                    [s * lf for s in ft_spacings],
                    klf_lane * kf / lf,
                ))
                veh_sources.append(f"{vname}: {source}")
            veh_names = self._write_general_vehicles(
                report, "AASHTO general vehicles", vehicles)
            if veh_names is None:
                return
            truck_desc = f"{truck} (AASHTO general vehicle): {', '.join(veh_names)}"

        # 2. Traffic lane + vehicle class, queued together into a SINGLE apply
        #    (importing vehicles in the same apply as the class regenerates the
        #    auto per-vehicle classes and clobbers custom ones). Lane runs along
        #    the member line given by lane_direction/lane_line_index, defaulting
        #    to the girder line closest to the middle of the deck.
        if ld.lane_direction is not None or ld.lane_line_index is not None:
            ldir = (ld.lane_direction or "X").upper()
            if ldir not in ("X", "Y") or ld.lane_line_index is None:
                report["errors"].append(
                    "lane_direction must be X or Y and lane_line_index must be "
                    "given to target a lane member line")
                return
            line = self._frame_lines.get((ldir, ld.lane_line_index))
            if not line:
                report["errors"].append(
                    f"No frames on {ldir}-direction grid line index "
                    f"{ld.lane_line_index}; available lines: "
                    f"{sorted(self._frame_lines)}")
                return
            frames = [name for _, name in sorted(line)]
            lane_desc = f"{ldir}-direction line index {ld.lane_line_index}"
        else:
            if not self._girder_rows:
                report["errors"].append(
                    "Moving load default lane needs girders; give "
                    "lane_direction + lane_line_index to use a frame group line")
                return
            row_indices = sorted(self._girder_rows)
            mid_row = row_indices[len(row_indices) // 2]
            frames = [name for _, name in sorted(self._girder_rows[mid_row])]
            ldir = model.girders.direction if model.girders else "X"
            lane_desc = f"girder row {mid_row}"
        lane_width = ld.lane_width or info["lane_width"]

        # Lane discretization: SAP2000 evaluates influence values at discrete
        # lane points and interpolates linearly between them. Align those
        # points with the default frame output stations (9 segments per frame)
        # so envelope values AT the output stations are exact, not interpolated
        # (verified live: coarse default discretization under-reported interior
        # shear envelope by up to 10%).
        coords = (model.grid.x_coords if ldir == "X" else model.grid.y_coords)
        seg_lengths = [b - a for a, b in zip(coords, coords[1:]) if b > a]
        disc_along = min(seg_lengths) / 9.0 if seg_lengths else lane_width / 6.0

        lane_fields = ["Lane", "LaneFrom", "LaneType", "Frame", "Width",
                       "Offset", "DiscAlong"]
        lane_rows = []
        for i, fname in enumerate(frames):
            lane_rows.append([
                "LANE1",
                "Frame" if i == 0 else "",
                "Vehicle" if i == 0 else "",
                fname,
                str(lane_width),
                "0",
                f"{disc_along:.6g}" if i == 0 else "",
            ])
        class_fields = ["VehClass", "VehName", "ScaleFactor"]
        mlclass_rows = [["MLCLASS", vn, "1"] for vn in veh_names]

        if self._edit_table("Lane Definition Data", lane_fields, lane_rows) != 0:
            report["errors"].append("Failed to queue lane definition table")
            return
        if self._edit_table("Vehicles 4 - Vehicle Classes", class_fields, mlclass_rows) != 0:
            report["errors"].append("Failed to queue vehicle class table")
            return
        if not self._apply_tables(report, "lane + vehicle class"):
            return

        # 2b. Verify-and-repair loop for the vehicle class. Observed on SAP2000
        #     27.1: the first class-table import after a vehicle import can go
        #     LATENT — it does not show in read-back but replays on the NEXT
        #     class import, duplicating rows (this corrupted earlier models and
        #     made SAP2000 raise an error when the saved file was reopened).
        #     Repair: rewrite the FULL corrected class table (custom class rows
        #     exactly once + the auto per-vehicle classes) and re-verify.
        #     Empirically converges by the second rewrite.
        expected = sorted(("MLCLASS", vn) for vn in veh_names)

        def _mlclass_ok() -> bool:
            got = sorted((c, v) for c, v, _ in self._class_entries() if c == "MLCLASS")
            return got == expected

        for _ in range(3):
            if _mlclass_ok():
                break
            others = []
            for entry in self._class_entries():
                if entry[0] != "MLCLASS" and list(entry) not in others:
                    others.append(list(entry))
            if self._edit_table("Vehicles 4 - Vehicle Classes", class_fields,
                                mlclass_rows + others) != 0 \
                    or not self._apply_tables(report, "vehicle class repair"):
                return
        if not _mlclass_ok():
            report["errors"].append(
                "Vehicle class MLCLASS wrong after repeated table imports: "
                f"{self._class_entries()}"
            )
            return

        # 2c. Verify the lane read-back: exactly one row per girder frame.
        _, lrows = self._table_rows("Lane Definition Data")
        lane_frames = [row[2] for row in lrows if row and row[0] == "LANE1"]
        if sorted(lane_frames) != sorted(frames):
            report["errors"].append(
                f"Lane LANE1 read-back mismatch: expected frames {frames}, got {lane_frames}"
            )
            return

        # 3. Moving load case (classic documented API)
        mov = m.LoadCases.Moving
        if _ret(mov.SetCase("MOVE1")) != 0:
            report["errors"].append("Failed to create moving load case MOVE1")
            return
        if _ret(mov.SetLoads("MOVE1", 1, ["MLCLASS"], [1.0], [1.0], [1.0])) != 0:
            report["errors"].append("Failed to assign vehicle class to MOVE1")
            return
        if _ret(mov.SetLanesLoaded("MOVE1", 1, 1, ["LANE1"])) != 0:
            report["errors"].append("Failed to assign lanes to MOVE1")
            return

        # 3b. Verify the case read-back through the database tables.
        _, arows = self._table_rows("Case - Moving Load 1 - Lane Assignments")
        assign = [row for row in arows if row and row[0] == "MOVE1"]
        _, lnrows = self._table_rows("Case - Moving Load 2 - Lanes Loaded")
        lanes_loaded = [row for row in lnrows if row and row[0] == "MOVE1"]
        if len(assign) != 1 or assign[0][2] != "MLCLASS" \
                or len(lanes_loaded) != 1 or lanes_loaded[0][2] != "LANE1":
            report["errors"].append(
                "MOVE1 case read-back mismatch: "
                f"assignments={assign}, lanes={lanes_loaded}"
            )
            return

        report["loads"].append(
            f"Moving load case MOVE1: vehicle class MLCLASS ({truck_desc}) on LANE1 "
            f"(width {lane_width} {info['len']}, along {lane_desc}, "
            f"{len(frames)} frames)"
        )
        for src in veh_sources:
            report["loads"].append(
                f"VERIFY axle data — {src}. Confirm against the current AASHTO LRFD "
                f"before using results in final calculations."
            )

    def _refresh_view(self) -> None:
        try:
            self._m.View.RefreshView(0, False)
        except Exception:
            pass
