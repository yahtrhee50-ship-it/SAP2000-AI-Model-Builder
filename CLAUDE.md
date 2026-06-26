# SAP2000 AI Model Builder — Claude Instructions

## Agent Rules (from AGENTS.md)

- Keep all work inside this project folder unless explicitly approved otherwise.
- Do not create, modify, move, or delete files outside this project folder without approval.
- Do not say a task or project is complete until the relevant files and logic have been verified.
- Do not mark any test, validation, or verification item as passed unless it was actually run or directly confirmed by the user.
- Do not claim integration tests passed unless a real integration test was actually run.
- Do not claim unit tests passed unless a real unit test was actually run.
- If only a syntax check was run, describe it as a syntax check — not a full test suite.
- If the user manually verifies behavior in a real browser or app, record that as manual verification.
- Tell the user what files changed.
- If making assumptions, state them clearly.

## Git & GitHub

**Always use Git and GitHub for this project.**

After completing any task that changes files:
1. Stage the changed files by name (`git add <file> ...`)
2. Write a clean, descriptive commit message (what changed and why)
3. Commit locally
4. Push to GitHub (`git push origin master`)

The goal is to always have a saved version on GitHub so we can revert if needed.

**Tool paths (Git is on the D drive):**
- Run `where git` before any git operations to confirm the path
- If `git` is not in PATH, use `D:\AI_TEST\GIT\Git\cmd\git.exe`
- GitHub CLI (`gh`): `C:\Program Files\GitHub CLI\gh.exe` — check with `where gh`
- Remote branch is `master` (not `main`)

## Environment

- OS: Windows 11, PowerShell primary shell
- Python: `C:\Python314\python.exe`
- SAP2000: installed at `D:\CSI\SAP2000.exe`, ProgID `CSI.SAP2000.API.SapObject`
- Git: `D:\AI_TEST\GIT\Git\cmd\git.exe`
- GitHub CLI (`gh`): `C:\Program Files\GitHub CLI\gh.exe`

## Testing

After any code change, run the full test suites before pushing:

```powershell
# Metric (kN/m) — 122 checks
python scripts\full_test.py

# Imperial (kip/ft) — 77 checks
python scripts\imperial_test.py
```

Both must pass (0 failures, 0 warnings) before committing.

## SAP2000 COM API — Known Issues & Confirmed Patterns

### RESOLVED: Use `SAP2000v1.Helper` to launch and connect (confirmed working in Project_003)

The correct way to launch SAP2000 and get a working `SapModel` is:

```python
import comtypes.client
import comtypes.gen

# Load typelib first so comtypes.gen.SAP2000v1 is available
comtypes.client.GetModule(("{F896D55D-8BDF-4232-B9AB-4B210897A81D}", 1, 0))

helper = comtypes.client.CreateObject("SAP2000v1.Helper")
helper = helper.QueryInterface(comtypes.gen.SAP2000v1.cHelper)
sap_object = helper.CreateObject(r"D:\CSI\SAP2000.exe")
sap_object.ApplicationStart()
sap_model = sap_object.SapModel   # works reliably
```

Do NOT use `win32.Dispatch("CSI.SAP2000.API.SapObject")` — it launches with `-Embedding` and `SapModel` returns `E_NOINTERFACE`.

To attach to an already-running SAP2000 (avoid relaunching):
```python
clsid = comtypes.GUID("{B6B21850-FB75-41DE-85EC-BC9DBEC69BD3}")
lib = comtypes.client.GetModule(("{F896D55D-8BDF-4232-B9AB-4B210897A81D}", 1, 0))
sap_object = comtypes.client.GetActiveObject(clsid, interface=lib.cOAPI)
sap_model = sap_object.SapModel
```

### Blocking event loop
All SAP2000 COM calls must run in `asyncio.to_thread()` with `pythoncom.CoInitialize()` called at the start of the thread function. Never call COM directly in an async route.

### comtypes return value conventions
- Successful calls return `0` (integer) or raise `COMError` on failure.
- `AddByPoint`-style calls return `(actual_name, error_code)` — use `_ret()` / `_ret_name()` helpers.
- For SAFEARRAY arguments (boolean lists, string lists), pass plain Python lists — comtypes marshals them automatically via the typelib.
- Wrap COM calls in try/except as the primary failure path; fall back to return-code check.

## Project Structure

```
src/
├── backend/
│   ├── main.py                  FastAPI app + /demo route
│   ├── models/structural.py     Pydantic schemas
│   ├── routes/
│   │   ├── chat.py              AI interview session routes
│   │   ├── preview.py           POST /api/preview (no API key)
│   │   └── sap2000.py           SAP2000 connect/build routes (thread-pool)
│   └── services/
│       ├── interview_engine.py  7-phase AI interview + JSON extraction
│       ├── ai_providers/        Claude + OpenAI wrappers
│       └── sap2000/
│           ├── connector.py     COM connection (subprocess launch + GetActiveObject)
│           └── builder.py       StructuralModel → SAP2000 API calls
└── frontend/
    ├── index.html               Main two-panel UI (AI Interview + Manual Input)
    ├── demo.html                Auto-loading 3D demo page (served at /demo)
    ├── css/styles.css
    └── js/
        ├── app.js               Tab switching, AI chat, build modal
        ├── manual.js            Manual form → /api/preview → build
        └── viewer.js            Three.js 3D viewer (importmap CDN)

scripts/
├── full_test.py         122-check metric + full system test
├── imperial_test.py     77-check imperial (kip/ft) test
├── live_test.py         Quick 5-span bridge deck live test
└── sap2000_live_build.py  SAP2000 connect + build test
```

## Key Design Decisions

- `demo.html` uses `<script type="importmap">` so Three.js CDN's OrbitControls can import `three` as a bare specifier — do not remove the importmap.
- SAP2000 `_define_loads()` creates DEAD (SW×1.0), SDL, and LL patterns. `_assign_area_loads()` applies SDL and LL values to every slab shell element via `AreaObj.SetLoadUniform`.
- `BuildRequest` in sap2000.py does NOT have a `session_id` field — it comes from the path parameter only.
- The module-level `_connection` singleton in connector.py is reset on server restart.
