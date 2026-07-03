"""Pydantic schemas for the structural bridge/slab model."""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class UnitSystem(str, Enum):
    KN_M = "kN_m"
    KIP_FT = "kip_ft"
    KIP_IN = "kip_in"


class StructureType(str, Enum):
    BRIDGE_DECK = "bridge_deck"
    BUILDING_FLOOR = "building_floor"
    MAT_FOUNDATION = "mat_foundation"


class GridDefinition(BaseModel):
    x_spacings: list[float] = Field(..., description="Bay spacings along X axis (m or ft)")
    y_spacings: list[float] = Field(..., description="Bay spacings along Y axis (m or ft)")
    origin_x: float = 0.0
    origin_y: float = 0.0

    @property
    def x_coords(self) -> list[float]:
        coords = [self.origin_x]
        for s in self.x_spacings:
            coords.append(coords[-1] + s)
        return coords

    @property
    def y_coords(self) -> list[float]:
        coords = [self.origin_y]
        for s in self.y_spacings:
            coords.append(coords[-1] + s)
        return coords


class FrameSection(BaseModel):
    name: str
    section_type: str = Field(..., description="e.g. W18x35, W24x94, custom")
    depth_mm: Optional[float] = None
    flange_width_mm: Optional[float] = None
    web_thickness_mm: Optional[float] = None
    flange_thickness_mm: Optional[float] = None
    material: str = "A992"


class GirderLayout(BaseModel):
    direction: str = Field("X", description="Primary girder direction: X or Y")
    section: FrameSection
    row_indices: list[int] = Field(..., description="Grid line indices where girders run")


class BeamLayout(BaseModel):
    section: FrameSection
    spacing: Optional[float] = None
    col_indices: Optional[list[int]] = None


class PileSupport(BaseModel):
    x: float
    y: float
    z: float = 0.0
    label: str = ""
    restraint: list[bool] = Field(
        default=[True, True, True, True, True, True],
        description="Ux,Uy,Uz,Rx,Ry,Rz restraints"
    )
    spring_stiffness: Optional[list[float]] = None


class SlabDefinition(BaseModel):
    thickness: float = Field(..., description="Slab thickness in model length units (m or ft)")
    concrete_fc: float = Field(28.0, description="Concrete compressive strength (MPa metric / ksi imperial)")
    unit_weight: float = Field(24.0, description="Concrete unit weight in model units (kN/m³ metric / kip/ft³ imperial)")
    mesh_size: float = Field(0.5, description="Target mesh element size (m or ft)")
    material_name: str = "Concrete_Slab"


class LoadDefinition(BaseModel):
    dead_load: float = Field(0.0, description="Superimposed dead load in model units (kN/m² metric / ksf imperial)")
    live_load: float = Field(0.0, description="Live load in model units (kN/m² metric / ksf imperial)")
    moving_load_enabled: bool = False
    lane_width: Optional[float] = None
    truck_axle_loads: Optional[list[float]] = None
    truck_axle_spacings: Optional[list[float]] = None


class ProjectInfo(BaseModel):
    name: str = "SAP2000 Model"
    description: str = ""
    unit_system: UnitSystem = UnitSystem.KN_M
    structure_type: StructureType = StructureType.BRIDGE_DECK
    designer: str = ""


class StructuralModel(BaseModel):
    """Complete structural model definition collected through AI interview."""
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    grid: Optional[GridDefinition] = None
    girders: Optional[GirderLayout] = None
    beams: Optional[BeamLayout] = None
    piles: list[PileSupport] = Field(default_factory=list)
    slab: Optional[SlabDefinition] = None
    loads: Optional[LoadDefinition] = None

    def is_complete(self) -> bool:
        return all([
            self.grid is not None,
            self.girders is not None,
            self.slab is not None,
            self.loads is not None,
            len(self.piles) > 0,
        ])

    def completion_summary(self) -> dict[str, bool]:
        return {
            "project": True,
            "grid": self.grid is not None,
            "girders": self.girders is not None,
            "beams": self.beams is not None,
            "piles": len(self.piles) > 0,
            "slab": self.slab is not None,
            "loads": self.loads is not None,
        }
