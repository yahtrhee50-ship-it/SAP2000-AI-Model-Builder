/**
 * Manual Input mode controller.
 * Reads the form, builds a StructuralModel dict, posts to /api/preview,
 * and drives the build-from-json flow — no API key needed.
 */

const API = '';

// ── Parse helpers ──────────────────────────────────────────────────────────

function parseFloatList(str) {
  return str.split(',').map(s => parseFloat(s.trim())).filter(n => !isNaN(n));
}

function parseIntList(str) {
  return str.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
}

function parsePileCoords(text, restraint) {
  return text.trim().split('\n')
    .map((line, i) => {
      const parts = line.split(',').map(s => parseFloat(s.trim()));
      if (parts.length < 2 || parts.some(isNaN)) return null;
      return { x: parts[0], y: parts[1], z: parts[2] ?? 0.0, label: `P${i + 1}`, restraint };
    })
    .filter(Boolean);
}

// ── Form → model dict ──────────────────────────────────────────────────────

export function buildModelFromForm() {
  const f = document.getElementById('manual-form');
  const v = name => f.querySelector(`[name="${name}"]`)?.value ?? '';
  const n = name => parseFloat(v(name)) || 0;
  const checked = name => f.querySelector(`[name="${name}"]`)?.checked ?? false;

  const xSpacings = parseFloatList(v('x_spacings'));
  const ySpacings = parseFloatList(v('y_spacings'));

  const supportType = v('support_type');
  const restraint = supportType === 'pinned'
    ? [true, true, true, false, false, false]
    : [true, true, true, true, true, true];

  // Build grid-intersection pile list
  let piles = [];
  if (checked('piles_at_grid')) {
    let x = 0;
    const xs = [0, ...xSpacings.map((s, i) => { x += s; return x; })];
    let y = 0;
    const ys = [0, ...ySpacings.map((s, i) => { y += s; return y; })];
    let idx = 1;
    xs.forEach(px => ys.forEach(py => {
      piles.push({ x: px, y: py, z: 0.0, label: `P${idx++}`, restraint });
    }));
  } else {
    piles = parsePileCoords(v('pile_coords'), restraint);
  }

  const girderRows = parseIntList(v('girder_rows'));

  const model = {
    project: {
      name: v('project_name') || 'My Structure',
      description: '',
      unit_system: v('unit_system') || 'kN_m',
      structure_type: v('structure_type') || 'bridge_deck',
      designer: v('designer'),
    },
    grid: {
      x_spacings: xSpacings,
      y_spacings: ySpacings,
      origin_x: 0.0,
      origin_y: 0.0,
    },
    girders: {
      direction: v('girder_direction') || 'X',
      section: {
        name: v('girder_section') || 'W610x140',
        section_type: v('girder_section') || 'W610x140',
        material: v('girder_material') || 'A992',
      },
      row_indices: girderRows,
    },
    piles,
    slab: {
      thickness: n('slab_thickness') || 0.2,
      concrete_fc: n('slab_fc') || 28,
      unit_weight: n('slab_unit_weight') || 24,
      mesh_size: n('slab_mesh') || 0.5,
      material_name: 'Concrete_Slab',
    },
    loads: {
      dead_load: n('dead_load'),
      live_load: n('live_load'),
      moving_load_enabled: checked('moving_load'),
      lane_width: checked('moving_load') ? n('lane_width') : null,
    },
  };

  // Optional beams
  if (checked('beams_enabled')) {
    const cols = v('beam_cols').trim();
    model.beams = {
      section: {
        name: v('beam_section') || 'W460x60',
        section_type: v('beam_section') || 'W460x60',
        material: v('beam_material') || 'A992',
      },
      col_indices: cols ? parseIntList(cols) : null,
    };
  }

  return model;
}

// ── Preview ────────────────────────────────────────────────────────────────

export async function fetchPreview(model) {
  const res = await fetch(`${API}/api/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(model),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Preview failed (${res.status})`);
  }
  return res.json();
}

// ── Build ──────────────────────────────────────────────────────────────────

export async function buildFromForm(model, savePath) {
  // Connect SAP2000 first
  await fetch(`${API}/api/sap2000/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ visible: true }),
  });

  const res = await fetch(`${API}/api/sap2000/build-from-json`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(model),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || 'Build failed');
  return data;
}

// ── Wire up collapsible toggles ────────────────────────────────────────────

export function initManualForm() {
  const beamsToggle = document.getElementById('beams-toggle');
  const beamsFields = document.getElementById('beams-fields');
  beamsToggle?.addEventListener('change', () => {
    beamsFields.classList.toggle('open', beamsToggle.checked);
  });

  const pilesGridToggle = document.getElementById('piles-grid-toggle');
  const pileCustom = document.getElementById('pile-custom-fields');
  pilesGridToggle?.addEventListener('change', () => {
    pileCustom.classList.toggle('open', !pilesGridToggle.checked);
  });

  const movingToggle = document.getElementById('moving-load-toggle');
  const movingFields = document.getElementById('moving-load-fields');
  movingToggle?.addEventListener('change', () => {
    movingFields.classList.toggle('open', movingToggle.checked);
  });
}
