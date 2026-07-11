"""
Modular SAP2000 operations — each capability is a standalone function with a
JSON-serializable parameter/return contract, so it can be exposed as a REST
endpoint today and converted 1:1 into an agent tool (e.g. an MCP tool in
Project_006's junior-se server) later.

Contract for every operation:
    func(conn: SAP2000Connection, **params) -> dict   (JSON-serializable)

- `conn` is the live COM connection (dependency-injected; the function never
  creates or owns the connection).
- All other parameters are plain JSON types (str, float, bool, lists, dicts).
- The return dict always carries "status": "ok" plus the payload, or raises —
  callers (routes / tools) translate exceptions into their own error shape.

Register every new operation in OPERATIONS below; the generic
`POST /api/sap2000/op/{name}` route dispatches by registry lookup, so adding
an operation here is all that's needed to expose it over REST.
"""
from . import combos, modeling, moving, results

OPERATIONS = {
    "define_load_combos": combos.define_load_combos,
    "add_multistep_moving_load": moving.add_multistep_moving_load,
    "add_columns": modeling.add_columns,
    "find_joints": modeling.find_joints,
    "run_analysis": modeling.run_analysis,
    "list_load_cases": results.list_load_cases,
    "joint_reactions": results.joint_reactions,
    "joint_displacements": results.joint_displacements,
    "frame_forces": results.frame_forces,
    "base_reactions": results.base_reactions,
    "modal_periods": results.modal_periods,
}
