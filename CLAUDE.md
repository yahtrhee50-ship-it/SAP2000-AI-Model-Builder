# SAP2000 AI Model Builder — Claude Instructions

## Git & GitHub

**Always use Git and GitHub for this project.**

After completing any task that changes files:
1. Stage the changed files by name (`git add <file> ...`)
2. Write a clean, descriptive commit message (what changed and why)
3. Commit locally
4. Push to GitHub (`git push origin main`)

The goal is to always have a saved version on GitHub so we can revert if needed.

**Tool paths (Git is on the D drive):**
- Run `where git` before any git operations to confirm the path
- If `git` is not in PATH, use `D:\Git\bin\git.exe` (or wherever `where git` resolves)
- GitHub CLI (`gh`) may also be on the D drive — check with `where gh`

## Environment

- OS: Windows 11, PowerShell primary shell
- Python: `C:\Python314\python.exe`
- SAP2000: installed at `D:\CSI\SAP2000.exe`, ProgID `CSI.SAP2000.API.SapObject`
- Git: installed on the D drive (not the default C:\Program Files location)
- GitHub CLI (`gh`): may be on the D drive — verify path before use

## Testing

After any code change, run the full test suites before pushing:

```powershell
# Metric (kN/m) — 122 checks
python scripts\full_test.py

# Imperial (kip/ft) — 77 checks
python scripts\imperial_test.py
```

Both must pass (0 failures, 0 warnings) before committing.

## SAP2000 COM API — Known Issues

- **`SapModel` returns `E_NOINTERFACE`**: Persistent issue with pywin32 late binding. Root cause not yet resolved.
  - `GetOAPIVersionNumber()` and `Visible` work fine — the COM object IS connected.
  - `SapModel` property specifically fails regardless of apartment type (STA/MTA) or gencache state.
  - Next steps to investigate: check SAP2000 license activation, try running server as Administrator, check if SAP2000 API requires a specific version of pywin32.

- **`-Embedding` dialog on COM launch**: When `win32.Dispatch("CSI.SAP2000.API.SapObject")` is called, SAP2000 shows "File -Embedding not found! Command line data ignored." — dismiss with OK. Connector now launches SAP2000 directly via subprocess to avoid this.

- **Blocking event loop**: All SAP2000 COM calls must run in `asyncio.to_thread()` with `pythoncom.CoInitialize()` called at the start of the thread function. Never call COM directly in an async route.

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
