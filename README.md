# SAP2000 AI Model Builder

An AI-guided structural modeling tool that collects bridge deck / slab parameters through a conversational interview or a manual input form, previews the structure in a live 3D viewer, and builds the full model in SAP2000 via COM API.

---

## Quick Start

```powershell
cd d:\AI_TEST\Agent_Developer\Project_005_SAP2000api_v3
python run.py
```

Opens `http://127.0.0.1:8000` automatically.
API docs at `http://127.0.0.1:8000/docs`.

### Requirements
- Python 3.11+
- SAP2000 v21+ installed and licensed on the same machine
- An Anthropic or OpenAI API key (AI Interview mode only)

Install dependencies:
```powershell
pip install -r requirements.txt
```

---

## Two Input Modes

### AI Interview (left panel → "AI Interview" tab)
1. Select **Claude** or **GPT-4o** and enter your API key
2. Click **Start Interview** — the AI guides you through 7 phases
3. When the model is complete the 3D preview updates automatically
4. Click **Build in SAP2000**

### Manual Input (left panel → "Manual Input" tab)
1. No API key required — fill in the form directly
2. Click **Update 3D Preview** to see the structure
3. Click **Build in SAP2000**

---

## Standard AI Interview Prompt

Copy and paste the block below as your **first message** after clicking
**Start Interview**. Fill in the bracketed values for your project before sending.
The AI will confirm each section and ask follow-up questions if anything is unclear.

```
I want to model a [bridge deck / building floor / mat foundation].

Project name: [My Bridge]
Designer: [Your Name]
Units: [kN/m (metric) | kip/ft (imperial)]

Grid layout:
  X-direction: [3] bays at [6, 6, 6] m spacing
  Y-direction: [2] bays at [8, 8] m spacing

Primary girders:
  Direction: [X]
  Section: [W610x140]  (or e.g. W24x94 for imperial)
  Material: [A992]
  Location: all Y grid lines (indices [0, 1, 2])

Secondary beams:
  Section: [W460x60]
  Location: all X grid lines (indices [0, 1, 2, 3])
  (Skip this line entirely if no secondary beams are needed)

Pile / support locations:
  [All grid intersections — fixed supports]
  (Or list custom coordinates: (0,0), (6,0), (12,0), …)

Concrete slab:
  Thickness: [200 mm / 0.2 m]
  Concrete strength fc: [28 MPa]
  Unit weight: [24 kN/m³]
  Target mesh size: [0.5 m]

Loads:
  Superimposed dead load: [2.0 kN/m²]
  Live load: [5.0 kN/m²]
  Moving load: [Yes / No]
    If Yes — lane width: [3.6 m], truck type: [HL-93 / HS20 / custom]
```

**Example filled-in prompt (metric bridge deck):**

```
I want to model a bridge deck.

Project name: Overpass A1
Designer: J. Smith
Units: kN/m (metric)

Grid layout:
  X-direction: 3 bays at 6, 6, 6 m spacing
  Y-direction: 2 bays at 8, 8 m spacing

Primary girders:
  Direction: X
  Section: W610x140
  Material: A992
  Location: all Y grid lines (indices 0, 1, 2)

Secondary beams:
  Section: W460x60
  Location: all X grid lines (indices 0, 1, 2, 3)

Pile / support locations:
  All grid intersections — fixed supports

Concrete slab:
  Thickness: 0.2 m
  Concrete strength fc: 28 MPa
  Unit weight: 24 kN/m³
  Target mesh size: 0.5 m

Loads:
  Superimposed dead load: 2.0 kN/m²
  Live load: 5.0 kN/m²
  Moving load: Yes — lane width 3.6 m, truck type HL-93
```

**Example filled-in prompt (imperial building floor):**

```
I want to model a building floor.

Project name: Level 3 Framing
Designer: R. Johnson
Units: kip/ft (imperial)

Grid layout:
  X-direction: 4 bays at 20, 20, 20, 20 ft spacing
  Y-direction: 3 bays at 25, 25, 25 ft spacing

Primary girders:
  Direction: X
  Section: W24x94
  Material: A992
  Location: all Y grid lines (indices 0, 1, 2, 3)

Secondary beams:
  Section: W18x35
  Location: all X grid lines (indices 0, 1, 2, 3, 4)

Pile / support locations:
  All grid intersections — pinned supports

Concrete slab:
  Thickness: 0.67 ft (8 in)
  Concrete strength fc: 4 ksi
  Unit weight: 150 pcf
  Target mesh size: 1.5 ft

Loads:
  Superimposed dead load: 20 psf
  Live load: 80 psf
  Moving load: No
```

---

## Project Structure

```
src/
├── backend/
│   ├── main.py                        FastAPI application
│   ├── models/structural.py           Pydantic schemas
│   ├── routes/
│   │   ├── chat.py                    POST /api/chat/* (AI interview)
│   │   ├── preview.py                 POST /api/preview (no API key needed)
│   │   └── sap2000.py                 POST /api/sap2000/build*
│   └── services/
│       ├── interview_engine.py        7-phase AI conversation + JSON extraction
│       ├── ai_providers/              Claude and OpenAI wrappers
│       └── sap2000/                   COM connector + model builder
└── frontend/
    ├── index.html                     Two-panel UI (chat | 3D viewer)
    ├── css/styles.css                 Dark theme
    └── js/
        ├── app.js                     Tab switching, AI chat, build modal
        ├── manual.js                  Manual form logic, /api/preview calls
        └── viewer.js                  Three.js 3D structural viewer
```

---

## Structural Elements Modeled

| Element | SAP2000 API | Notes |
|---|---|---|
| Grid joints | `PointObj.AddCartesian` | At every grid intersection |
| Girders | `FrameObj.AddByPoint` + `SetSection` | Primary W-sections |
| Beams | `FrameObj.AddByPoint` + `SetSection` | Secondary W-sections |
| Slab | `AreaObj.AddByPoint` + `SetProperty` | **Thick shell** (ShellType = 2) |
| Piles / supports | `PointObj.SetRestraint` | Fixed or pinned |
| Loads | `LoadPatterns.Add` | Dead, live, moving vehicle |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/chat/start` | Start AI interview session |
| `POST` | `/api/chat/message` | Send message, receive AI response + preview |
| `GET` | `/api/chat/preview/{id}` | Get current 3D preview for a session |
| `POST` | `/api/preview` | Compute preview from model JSON (no API key) |
| `GET` | `/api/sap2000/status` | Check SAP2000 connection |
| `POST` | `/api/sap2000/connect` | Connect to / launch SAP2000 |
| `POST` | `/api/sap2000/build/{session_id}` | Build from AI session |
| `POST` | `/api/sap2000/build-from-json` | Build from raw model JSON |
