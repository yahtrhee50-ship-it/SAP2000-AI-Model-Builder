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

# Standard vehicles from SAP2000's built-in library, selected by truck_type.
# Each entry: list of (VehName, Type, ScaleFactor) rows for the
# "Vehicles 1 - Standard Vehicles" database table. For HSn-44 the scale factor
# is the "n" (HS20-44 = HSn-44 with SF 20). Axle weights/spacings come from
# SAP2000's vehicle library — verify against the current Caltrans BDA / AASHTO
# source before using results in final calculations.
STANDARD_TRUCKS = {
    "P5":     [("P5", "P5", "1")],
    "P7":     [("P7", "P7", "1")],
    "P9":     [("P9", "P9", "1")],
    "P11":    [("P11", "P11", "1")],
    "P13":    [("P13", "P13", "1")],
    "HL-93":  [("HL-93K", "HL-93K", "1"), ("HL-93M", "HL-93M", "1"),
               ("HL-93S", "HL-93S", "1")],
    "HL-93K": [("HL-93K", "HL-93K", "1")],
    "HL-93M": [("HL-93M", "HL-93M", "1")],
    "HL-93S": [("HL-93S", "HL-93S", "1")],
    "HS20":   [("HS20-44", "HSn-44", "20")],
    "HS20-44": [("HS20-44", "HSn-44", "20")],
    "HS15":   [("HS15-44", "HSn-44", "15")],
}
DEFAULT_TRUCK = "P13"  # typical Caltrans permit truck

# Model input convention per unit system (values in StructuralModel must already
# be in these units — the interview prompt instructs the AI to convert):
#   kN_m:   length m,  fc MPa, unit weight kN/m3,  area loads kN/m2
#   kip_ft: length ft, fc ksi, unit weight kip/ft3, area loads ksf
#   kip_in: length in, fc ksi, unit weight kip/in3, area loads kip/in2
UNIT_INFO = {
    "kN_m":   {"len": "m",  "force": "kN",  "press": "kN/m2",   "mm_to_len": 1 / 1000.0,  "thermal": 1.17e-5, "lane_width": 3.6},
    "kip_ft": {"len": "ft", "force": "kip", "press": "ksf",     "mm_to_len": 1 / 304.8,   "thermal": 6.5e-6,  "lane_width": 12.0},
    "kip_in": {"len": "in", "force": "kip", "press": "kip/in2", "mm_to_len": 1 / 25.4,    "thermal": 6.5e-6,  "lane_width": 144.0},
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
        self._units = "kN_m"             # updated by build()
        # {girder row index: [(start_coord, actual_frame_name), ...]} — filled by
        # _add_frames, consumed by _define_moving_load for lane definition
        self._girder_rows: dict[int, list[tuple[float, str]]] = {}

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
            info = UNIT_INFO[self._units]
            E = _concrete_E(model.slab.concrete_fc, self._units)
            if _ret(m.PropMaterial.SetMPIsotropic(name, E, 0.2, info["thermal"])) != 0:
                report["errors"].append(f"Failed to set concrete stiffness on '{name}'")
            # Option 1 = weight per unit volume (0 is not a valid option code)
            if _ret(m.PropMaterial.SetWeightAndMass(name, 1, model.slab.unit_weight)) != 0:
                report["errors"].append(f"Failed to set concrete unit weight on '{name}'")
            report["materials"].append(f"Concrete {name} (E={E:.0f} {info['press']})")

    # ── Section properties ─────────────────────────────────────────────────────

    def _define_sections(self, model: StructuralModel, report: dict) -> None:
        m = self._m

        if model.girders:
            self._add_frame_section(model.girders.section, self._steel_mat, report)

        if model.beams:
            self._add_frame_section(model.beams.section, self._steel_mat, report)

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

    def _apply_tables(self, report: dict, what: str) -> bool:
        r = self._m.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
        # r = [NumFatalErrors, NumErrorMsgs, NumWarnMsgs, NumInfoMsgs, ImportLog, ret]
        fatal, errs = int(r[0]), int(r[1])
        if _ret(r) != 0 or fatal or errs:
            report["errors"].append(
                f"Table import for {what} failed ({fatal} fatal, {errs} errors): {r[4]}"
            )
            return False
        return True

    def _define_moving_load(self, model: StructuralModel, report: dict) -> None:
        ld = model.loads
        if not ld or not ld.moving_load_enabled:
            return
        if not self._girder_rows:
            report["errors"].append("Moving load requested but no girders were created")
            return

        m = self._m
        info = UNIT_INFO[self._units]

        # 1. Vehicle definition — either a custom stepped axle train (general
        #    vehicle) or standard vehicle(s) from SAP2000's library.
        if ld.truck_axle_loads:
            axles = [float(a) for a in ld.truck_axle_loads]
            spacings = [float(s) for s in (ld.truck_axle_spacings or [])]
            if (not axles or len(spacings) != len(axles) - 1
                    or any(a <= 0 for a in axles) or any(s <= 0 for s in spacings)):
                report["errors"].append(
                    "Invalid custom axle train: need N axle loads > 0 "
                    "(model force units) and N-1 spacings > 0 (model length units); "
                    f"got {len(axles)} loads, {len(spacings)} spacings"
                )
                return
            veh_names = ["CUSTOM1"]
            load_fields = ["VehName", "LoadType", "InterUnif", "InterAxle",
                           "InterMinD", "InterMaxD"]
            load_rows = [["CUSTOM1", "Leading Load", "0", str(axles[0]), "", ""]]
            for axle, spacing in zip(axles[1:], spacings):
                load_rows.append(
                    ["CUSTOM1", "Fixed Length", "0", str(axle), str(spacing), ""])
            if (self._edit_table(
                    "Vehicles 2 - General Vehicles 1 - General",
                    ["VehName", "NumInter", "StayInLane"],
                    [["CUSTOM1", str(len(load_rows)), "No"]]) != 0
                    or self._edit_table(
                        "Vehicles 3 - General Vehicles 2 - Loads",
                        load_fields, load_rows) != 0
                    or not self._apply_tables(report, "custom vehicle")):
                return
            truck_desc = (
                f"custom axle train, {len(axles)} axles, "
                f"total {sum(axles):g} {info['force']}"
            )
        else:
            truck = (ld.truck_type or DEFAULT_TRUCK).upper().replace(" ", "")
            vehicles = STANDARD_TRUCKS.get(truck)
            if vehicles is None:
                report["errors"].append(
                    f"Unknown truck_type '{ld.truck_type}'. Supported: {sorted(STANDARD_TRUCKS)}"
                )
                return
            if self._edit_table(
                "Vehicles 1 - Standard Vehicles",
                ["VehName", "Type", "ScaleFactor"],
                [list(v) for v in vehicles],
            ) != 0 or not self._apply_tables(report, "standard vehicles"):
                return
            veh_names = [v[0] for v in vehicles]
            truck_desc = f"{truck}: {', '.join(veh_names)}"

        # 2. Traffic lane + vehicle class, queued together into a SINGLE apply
        #    (importing vehicles in the same apply as the class regenerates the
        #    auto per-vehicle classes and clobbers custom ones). Lane runs along
        #    the girder line closest to the middle of the deck.
        row_indices = sorted(self._girder_rows)
        mid_row = row_indices[len(row_indices) // 2]
        frames = [name for _, name in sorted(self._girder_rows[mid_row])]
        lane_width = ld.lane_width or info["lane_width"]

        # Lane discretization: SAP2000 evaluates influence values at discrete
        # lane points and interpolates linearly between them. Align those
        # points with the default frame output stations (9 segments per frame)
        # so envelope values AT the output stations are exact, not interpolated
        # (verified live: coarse default discretization under-reported interior
        # shear envelope by up to 10%).
        coords = (model.grid.x_coords if model.girders.direction == "X"
                  else model.grid.y_coords)
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
            f"(width {lane_width} {info['len']}, along girder row {mid_row}, "
            f"{len(frames)} frames)"
        )
        if not ld.truck_axle_loads:
            report["loads"].append(
                "VERIFY: vehicle axle configuration comes from the SAP2000 standard "
                "vehicle library - confirm against the current Caltrans BDA / AASHTO "
                "source before using results in final calculations"
            )

    def _refresh_view(self) -> None:
        try:
            self._m.View.RefreshView(0, False)
        except Exception:
            pass
