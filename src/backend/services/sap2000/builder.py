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

# Load pattern type codes
LOAD_DEAD = 1
LOAD_LIVE = 3
LOAD_VEHICLE = 7


def _ret(result) -> int:
    """Extract the integer error code from a COM return value.
    comtypes returns [actual_name, retcode] for object-creating calls
    and a plain int for property-setting calls.
    """
    if isinstance(result, (list, tuple)):
        return int(result[1])
    return int(result)


def _ret_name(result, fallback: str) -> str:
    """Extract the actual object name SAP2000 assigned (may differ from requested)."""
    if isinstance(result, (list, tuple)):
        return str(result[0])
    return fallback


class ModelBuilder:

    def __init__(self, conn: SAP2000Connection):
        self._m = conn.model
        self._steel_mat   = "A992"      # updated by _define_materials
        self._concrete_mat = "Concrete"  # updated by _define_materials

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

        try:
            self._define_materials(model, report)
            self._define_sections(model, report)
            joints = self._add_joints(model, report)
            self._add_supports(model, joints, report)
            self._add_frames(model, joints, report)
            self._add_slab(model, report)
            self._define_loads(model, report)
            self._assign_area_loads(model, report)
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

        # Concrete for slab
        if model.slab:
            name = model.slab.material_name
            ret = m.PropMaterial.AddMaterial(name, MAT_CONCRETE, "", "", "")
            if _ret(ret) == 0:
                actual = _ret_name(ret, name)
                self._concrete_mat = actual
                fc = model.slab.concrete_fc * 1000  # MPa -> kPa for kN-m model
                E = 4700 * (model.slab.concrete_fc ** 0.5) * 1000  # MPa -> kPa
                m.PropMaterial.SetMPIsotropic(actual, E, 0.2, 1.17e-5)
                m.PropMaterial.SetWeightAndMass(actual, 0, model.slab.unit_weight)
                report["materials"].append(f"Concrete {actual}")

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
                report["sections"].append(f"Thick shell slab t={slab.thickness}m")

    def _add_frame_section(self, sec, mat_name: str, report: dict) -> None:
        m = self._m
        stype = sec.section_type.upper()

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
                d = sec.depth_mm / 1000 if sec.depth_mm else 0.61
                b = sec.flange_width_mm / 1000 if sec.flange_width_mm else 0.23
                m.PropFrame.SetRectangle(sec.name, mat_name, d, b)
        else:
            # Custom rectangular placeholder
            d = sec.depth_mm / 1000 if sec.depth_mm else 0.5
            b = sec.flange_width_mm / 1000 if sec.flange_width_mm else 0.2
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
                if ret is None or int(ret) == 0:
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
                                report["frames"].append(name)
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
                                report["frames"].append(name)

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
                                report["frames"].append(name)
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
                                report["frames"].append(name)

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

        # Live load pattern
        m.LoadPatterns.Add("LL", LOAD_LIVE, 0.0, False)

        if ld.moving_load_enabled:
            m.LoadPatterns.Add("ML", LOAD_VEHICLE, 0.0, False)

        report["loads"].append(
            "Patterns: DEAD (SW x1.0), SDL=%.2f kN/m2, LL=%.2f kN/m2%s"
            % (ld.dead_load, ld.live_load, ", ML" if ld.moving_load_enabled else "")
        )

    def _assign_area_loads(self, model: StructuralModel, report: dict) -> None:
        """Apply uniform SDL and LL pressure loads to all slab area elements."""
        if not model.loads or not model.slab:
            return

        m  = self._m
        ld = model.loads

        # LoadDir=6 = Local Z (gravity direction for horizontal slabs)
        # Replace=True so loads don't stack on re-runs
        for area_name in report["areas"]:
            if ld.dead_load > 0:
                m.AreaObj.SetLoadUniform(area_name, "SDL", -ld.dead_load, 6, True, "Local")
            if ld.live_load > 0:
                m.AreaObj.SetLoadUniform(area_name, "LL",  -ld.live_load,  6, True, "Local")

        if report["areas"]:
            report["loads"].append(
                "SDL %.2f kN/m2 and LL %.2f kN/m2 applied to %d area elements"
                % (ld.dead_load, ld.live_load, len(report["areas"]))
            )

    def _refresh_view(self) -> None:
        try:
            self._m.View.RefreshView(0, False)
        except Exception:
            pass
