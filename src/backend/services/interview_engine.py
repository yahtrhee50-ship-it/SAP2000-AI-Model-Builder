"""
AI interview engine: drives a multi-turn conversation that collects all
structural model parameters and extracts them into a StructuralModel.
"""
from __future__ import annotations
import json
import re
from typing import Optional
from ..models.structural import (
    StructuralModel, ProjectInfo, GridDefinition, GirderLayout, BeamLayout,
    FrameSection, PileSupport, SlabDefinition, LoadDefinition,
    UnitSystem, StructureType,
)
from .ai_providers.base import AIProvider

# ── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a structural engineering assistant helping users define a bridge deck or slab structure for analysis in SAP2000.

Your job is to conduct a friendly, efficient interview to collect all required parameters, then output a structured JSON model.

## Interview Phases (ask in order, one phase at a time):

### Phase 1 – Project Info
- Project name and description
- Unit system: kN/m (metric) or kip/ft (imperial)
- Structure type: bridge deck, building floor, or mat foundation
- Designer name (optional)

### Phase 2 – Grid Layout
- Number of bays in the X-direction and their individual spacings (e.g., "3 bays: 6m, 6m, 6m")
- Number of bays in the Y-direction and their individual spacings (e.g., "2 bays: 8m, 8m")
- These define the structural grid for beam/girder placement

### Phase 3 – Girders (Primary Beams)
- Primary direction: X or Y
- Steel section (W-shape preferred, e.g., W24×94 or W610×140) or custom dimensions
- Which grid lines have girders (all Y-lines for X-direction girders, or specify)

### Phase 4 – Secondary Beams
- Section size (e.g., W18×35)
- Placement: at every grid intersection, or specify spacing/locations
- (Optional — can skip if only girders)

### Phase 5 – Piles / Supports
- Pile locations: at all grid intersections, at selected grid points, or at custom X,Y coordinates
- Support type: fixed (default), pinned, or spring with stiffness values
- List coordinates if custom

### Phase 6 – Concrete Slab
- Slab thickness (e.g., 200 mm or 8 in)
- Concrete strength fc (e.g., 28 MPa or 4000 psi)
- Unit weight (default: 24 kN/m³ or 150 pcf)
- Target mesh element size (e.g., 0.5 m or 1.5 ft)

### Phase 7 – Loads
- Superimposed dead load (kN/m² or psf)
- Live load (kN/m² or psf)
- Moving load: yes or no
  - If yes: lane width and truck type. Supported truck types (SAP2000 standard
    vehicle library): P5, P7, P9, P11, P13 (Caltrans permit trucks — P13 is the
    default), HL-93 (AASHTO LRFD design envelope), HL-93K/M/S individually,
    HS20, HS15. Custom axle loads/spacings are not supported yet — if the user
    needs one, note it for the engineer and use the closest standard vehicle.

## Unit convention for the JSON (IMPORTANT):
All numeric values in the output JSON must be in the model's consistent unit system.
Convert the user's inputs before writing the JSON:
- Metric (unit_system "kN_m"): lengths in m (convert mm: /1000), fc in MPa,
  unit weight in kN/m³, area loads in kN/m².
- Imperial (unit_system "kip_ft"): lengths in ft (convert inches: /12), fc in ksi
  (convert psi: /1000), unit weight in kip/ft³ (convert pcf: /1000, e.g. 150 pcf → 0.150),
  area loads in ksf (convert psf: /1000, e.g. 80 psf → 0.080).
Echo the converted value back to the user when confirming (e.g. "80 psf = 0.080 ksf").

## Rules:
- Ask Phase 1 questions first, wait for answers, then proceed.
- Confirm each answer briefly before moving on.
- If a user gives ambiguous input, ask one clarifying question.
- After all phases are complete, output a JSON block (fenced with ```json ... ```) containing the full model. This JSON must follow this exact schema:

```json
{
  "project": {
    "name": "...",
    "description": "...",
    "unit_system": "kN_m",
    "structure_type": "bridge_deck",
    "designer": "..."
  },
  "grid": {
    "x_spacings": [6.0, 6.0, 6.0],
    "y_spacings": [8.0, 8.0],
    "origin_x": 0.0,
    "origin_y": 0.0
  },
  "girders": {
    "direction": "X",
    "section": {
      "name": "W610x140",
      "section_type": "W610x140",
      "material": "A992"
    },
    "row_indices": [0, 1, 2]
  },
  "beams": {
    "section": {
      "name": "W460x60",
      "section_type": "W460x60",
      "material": "A992"
    },
    "col_indices": [0, 1, 2, 3]
  },
  "piles": [
    {"x": 0.0, "y": 0.0, "z": 0.0, "label": "P1", "restraint": [true,true,true,true,true,true]},
    {"x": 6.0, "y": 0.0, "z": 0.0, "label": "P2", "restraint": [true,true,true,true,true,true]}
  ],
  "slab": {
    "thickness": 0.2,
    "concrete_fc": 28.0,
    "unit_weight": 24.0,
    "mesh_size": 0.5,
    "material_name": "Concrete_Slab"
  },
  "loads": {
    "dead_load": 2.0,
    "live_load": 5.0,
    "moving_load_enabled": false,
    "lane_width": null,
    "truck_type": null,
    "truck_axle_loads": null,
    "truck_axle_spacings": null
  }
}
```

- Output this JSON only when ALL phases are complete.
- After outputting JSON, ask the user to review the 3D preview and confirm before building the SAP2000 model.

Start by greeting the user and asking Phase 1 questions."""


# ── JSON extraction ───────────────────────────────────────────────────────────

def extract_model_json(text: str) -> Optional[StructuralModel]:
    """Pull the first ```json ... ``` block from assistant text and parse it."""
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return _parse_model(data)
    except Exception:
        return None


def _parse_model(data: dict) -> StructuralModel:
    model = StructuralModel()

    if "project" in data:
        p = data["project"]
        model.project = ProjectInfo(
            name=p.get("name", "SAP2000 Model"),
            description=p.get("description", ""),
            unit_system=UnitSystem(p.get("unit_system", "kN_m")),
            structure_type=StructureType(p.get("structure_type", "bridge_deck")),
            designer=p.get("designer", ""),
        )

    if "grid" in data:
        g = data["grid"]
        model.grid = GridDefinition(
            x_spacings=g["x_spacings"],
            y_spacings=g["y_spacings"],
            origin_x=g.get("origin_x", 0.0),
            origin_y=g.get("origin_y", 0.0),
        )

    if "girders" in data:
        gd = data["girders"]
        sec = gd["section"]
        model.girders = GirderLayout(
            direction=gd.get("direction", "X"),
            section=FrameSection(
                name=sec["name"],
                section_type=sec["section_type"],
                material=sec.get("material", "A992"),
            ),
            row_indices=gd.get("row_indices", []),
        )

    if "beams" in data:
        bd = data["beams"]
        if bd:
            sec = bd["section"]
            model.beams = BeamLayout(
                section=FrameSection(
                    name=sec["name"],
                    section_type=sec["section_type"],
                    material=sec.get("material", "A992"),
                ),
                col_indices=bd.get("col_indices"),
                spacing=bd.get("spacing"),
            )

    if "piles" in data:
        model.piles = [
            PileSupport(
                x=p["x"], y=p["y"], z=p.get("z", 0.0),
                label=p.get("label", ""),
                restraint=p.get("restraint", [True]*6),
            )
            for p in data["piles"]
        ]

    if "slab" in data:
        s = data["slab"]
        model.slab = SlabDefinition(
            thickness=s["thickness"],
            concrete_fc=s.get("concrete_fc", 28.0),
            unit_weight=s.get("unit_weight", 24.0),
            mesh_size=s.get("mesh_size", 0.5),
            material_name=s.get("material_name", "Concrete_Slab"),
        )

    if "loads" in data:
        ld = data["loads"]
        model.loads = LoadDefinition(
            dead_load=ld.get("dead_load", 0.0),
            live_load=ld.get("live_load", 0.0),
            moving_load_enabled=ld.get("moving_load_enabled", False),
            lane_width=ld.get("lane_width"),
            truck_type=ld.get("truck_type"),
            truck_axle_loads=ld.get("truck_axle_loads"),
            truck_axle_spacings=ld.get("truck_axle_spacings"),
        )

    return model


# ── Session state ─────────────────────────────────────────────────────────────

class InterviewSession:
    """Holds conversation history and the current partial structural model."""

    def __init__(self, session_id: str, provider: AIProvider):
        self.session_id = session_id
        self.provider = provider
        self.messages: list[dict] = []
        self.model: StructuralModel = StructuralModel()
        self.model_finalized: bool = False

    async def send(self, user_text: str) -> str:
        """Add user message, get AI response, extract model if ready."""
        self.messages.append({"role": "user", "content": user_text})
        response = await self.provider.chat(self.messages, SYSTEM_PROMPT)
        self.messages.append({"role": "assistant", "content": response})

        extracted = extract_model_json(response)
        if extracted:
            self.model = extracted
            self.model_finalized = True

        return response

    async def send_stream(self, user_text: str):
        """Streaming version — yields chunks, updates model at end."""
        self.messages.append({"role": "user", "content": user_text})
        full_response = ""
        async for chunk in self.provider.chat_stream(self.messages, SYSTEM_PROMPT):
            full_response += chunk
            yield chunk
        self.messages.append({"role": "assistant", "content": full_response})

        extracted = extract_model_json(full_response)
        if extracted:
            self.model = extracted
            self.model_finalized = True

    def get_preview_data(self) -> dict:
        """Return serializable preview data for the 3D viewer."""
        m = self.model
        data: dict = {"complete": m.is_complete(), "completion": m.completion_summary()}

        if m.grid:
            data["grid"] = {
                "x_coords": m.grid.x_coords,
                "y_coords": m.grid.y_coords,
            }

        if m.piles:
            data["piles"] = [{"x": p.x, "y": p.y, "z": p.z, "label": p.label} for p in m.piles]

        if m.girders and m.grid:
            girders = []
            xc = m.grid.x_coords
            yc = m.grid.y_coords
            if m.girders.direction == "X":
                for ri in m.girders.row_indices:
                    if ri < len(yc):
                        y = yc[ri]
                        girders.append({"x1": xc[0], "y1": y, "x2": xc[-1], "y2": y})
            else:
                for ci in m.girders.row_indices:
                    if ci < len(xc):
                        x = xc[ci]
                        girders.append({"x1": x, "y1": yc[0], "x2": x, "y2": yc[-1]})
            data["girders"] = {"direction": m.girders.direction, "lines": girders, "section": m.girders.section.section_type}

        if m.beams and m.grid:
            beams = []
            xc = m.grid.x_coords
            yc = m.grid.y_coords
            col_indices = m.beams.col_indices or list(range(len(xc)))
            if m.girders and m.girders.direction == "X":
                for ci in col_indices:
                    if ci < len(xc):
                        x = xc[ci]
                        beams.append({"x1": x, "y1": yc[0], "x2": x, "y2": yc[-1]})
            else:
                for ri in col_indices:
                    if ri < len(yc):
                        y = yc[ri]
                        beams.append({"x1": xc[0], "y1": y, "x2": xc[-1], "y2": y})
            data["beams"] = {"lines": beams, "section": m.beams.section.section_type}

        if m.slab and m.grid:
            xc = m.grid.x_coords
            yc = m.grid.y_coords
            data["slab"] = {
                "panels": [
                    {
                        "x1": xc[i], "y1": yc[j],
                        "x2": xc[i+1], "y2": yc[j+1],
                    }
                    for i in range(len(xc)-1)
                    for j in range(len(yc)-1)
                ],
                "thickness": m.slab.thickness,
            }

        if m.loads:
            data["loads"] = {
                "dead_load": m.loads.dead_load,
                "live_load": m.loads.live_load,
                "moving_load": m.loads.moving_load_enabled,
            }

        return data
