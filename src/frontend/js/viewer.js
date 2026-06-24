/**
 * 3D structural model viewer using Three.js.
 * Renders the grid, girders, beams, slab panels, and pile supports.
 */

import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js';
import { OrbitControls } from 'https://cdn.jsdelivr.net/npm/three@0.165.0/examples/jsm/controls/OrbitControls.js';

const COLORS = {
  grid:    0x2a3a4a,
  girder:  0x2f81f7,   // blue
  beam:    0x3fb950,   // green
  slab:    0x8b949e,   // gray
  pile:    0xf85149,   // red
  axis_x:  0xe06c75,
  axis_y:  0x98c379,
  axis_z:  0x61aeee,
};

export class StructuralViewer {
  constructor(canvasId) {
    this._canvas = document.getElementById(canvasId);
    this._scene = new THREE.Scene();
    this._scene.background = new THREE.Color(0x060a10);

    this._camera = new THREE.PerspectiveCamera(50, this._aspectRatio(), 0.1, 5000);
    this._camera.position.set(30, 25, 30);
    this._camera.lookAt(0, 0, 0);

    this._renderer = new THREE.WebGLRenderer({
      canvas: this._canvas,
      antialias: true,
    });
    this._renderer.setPixelRatio(window.devicePixelRatio);
    this._renderer.setSize(this._canvas.clientWidth, this._canvas.clientHeight);

    this._controls = new OrbitControls(this._camera, this._renderer.domElement);
    this._controls.enableDamping = true;
    this._controls.dampingFactor = 0.08;

    // Lighting
    const ambient = new THREE.AmbientLight(0xffffff, 0.6);
    const sun = new THREE.DirectionalLight(0xffffff, 0.8);
    sun.position.set(50, 80, 60);
    this._scene.add(ambient, sun);

    this._addAxesHelper();
    this._addGridFloor();

    // Track objects by group so we can replace them
    this._groups = {
      gridLines: new THREE.Group(),
      girders:   new THREE.Group(),
      beams:     new THREE.Group(),
      slab:      new THREE.Group(),
      piles:     new THREE.Group(),
    };
    Object.values(this._groups).forEach(g => this._scene.add(g));

    this._animate();
    window.addEventListener('resize', () => this._onResize());
  }

  /** Update the scene from preview data returned by the backend. */
  update(data) {
    this._clearGroups();

    if (data.grid) {
      this._drawGrid(data.grid);
    }
    if (data.piles) {
      this._drawPiles(data.piles);
    }
    if (data.girders) {
      this._drawFrames(data.girders.lines, COLORS.girder, this._groups.girders, 0.05);
    }
    if (data.beams) {
      this._drawFrames(data.beams.lines, COLORS.beam, this._groups.beams, 0.035);
    }
    if (data.slab) {
      this._drawSlab(data.slab);
    }

    this._fitCamera();
  }

  // ── Private ─────────────────────────────────────────────────────────────

  _clearGroups() {
    Object.values(this._groups).forEach(g => {
      while (g.children.length) {
        const c = g.children[0];
        g.remove(c);
        c.geometry?.dispose();
        c.material?.dispose();
      }
    });
  }

  _drawGrid(grid) {
    const mat = new THREE.LineBasicMaterial({ color: COLORS.grid });
    const { x_coords, y_coords } = grid;

    x_coords.forEach(x => {
      const geo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(x, 0, y_coords[0]),
        new THREE.Vector3(x, 0, y_coords[y_coords.length - 1]),
      ]);
      this._groups.gridLines.add(new THREE.Line(geo, mat));
    });

    y_coords.forEach(y => {
      const geo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(x_coords[0], 0, y),
        new THREE.Vector3(x_coords[x_coords.length - 1], 0, y),
      ]);
      this._groups.gridLines.add(new THREE.Line(geo, mat));
    });
  }

  _drawFrames(lines, color, group, radius) {
    const mat = new THREE.MeshStandardMaterial({ color });
    lines.forEach(({ x1, y1, x2, y2 }) {
      const start = new THREE.Vector3(x1, 0, y1);
      const end   = new THREE.Vector3(x2, 0, y2);
      const dir   = new THREE.Vector3().subVectors(end, start);
      const len   = dir.length();
      if (len < 0.001) return;

      const geo = new THREE.CylinderGeometry(radius, radius, len, 8);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.copy(start).lerp(end, 0.5);
      mesh.quaternion.setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        dir.normalize(),
      );
      group.add(mesh);
    });
  }

  _drawPiles(piles) {
    const mat = new THREE.MeshStandardMaterial({ color: COLORS.pile });
    piles.forEach(({ x, y, z, label }) => {
      // Cone pointing up
      const geo = new THREE.ConeGeometry(0.3, 1.2, 8);
      const cone = new THREE.Mesh(geo, mat);
      cone.position.set(x, -0.6, y);
      this._groups.piles.add(cone);

      // Sphere cap
      const sgeo = new THREE.SphereGeometry(0.25, 10, 10);
      const ball = new THREE.Mesh(sgeo, new THREE.MeshStandardMaterial({ color: 0xff6b6b }));
      ball.position.set(x, 0, y);
      this._groups.piles.add(ball);
    });
  }

  _drawSlab(slabData) {
    const mat = new THREE.MeshStandardMaterial({
      color: COLORS.slab,
      transparent: true,
      opacity: 0.35,
      side: THREE.DoubleSide,
    });
    const thick = slabData.thickness || 0.2;

    slabData.panels.forEach(({ x1, y1, x2, y2 }) => {
      const w = x2 - x1;
      const h = y2 - y1;
      const geo = new THREE.BoxGeometry(w, thick, h);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set((x1 + x2) / 2, thick / 2, (y1 + y2) / 2);
      this._groups.slab.add(mesh);

      // Wireframe outline
      const wfGeo = new THREE.EdgesGeometry(geo);
      const wfMat = new THREE.LineBasicMaterial({ color: 0x555e6b, linewidth: 1 });
      const wf = new THREE.LineSegments(wfGeo, wfMat);
      wf.position.copy(mesh.position);
      this._groups.slab.add(wf);
    });
  }

  _fitCamera() {
    const box = new THREE.Box3();
    this._scene.traverse(obj => {
      if (obj.isMesh || obj.isLine) box.expandByObject(obj);
    });
    if (box.isEmpty()) return;

    const center = new THREE.Vector3();
    const size   = new THREE.Vector3();
    box.getCenter(center);
    box.getSize(size);
    const maxDim = Math.max(size.x, size.y, size.z);
    const dist   = maxDim * 1.8;

    this._camera.position.set(
      center.x + dist * 0.7,
      center.y + dist * 0.5,
      center.z + dist * 0.7,
    );
    this._camera.lookAt(center);
    this._controls.target.copy(center);
    this._controls.update();
  }

  _addAxesHelper() {
    const len = 5;
    const mkAxis = (from, to, color) => {
      const geo = new THREE.BufferGeometry().setFromPoints([from, to]);
      return new THREE.Line(geo, new THREE.LineBasicMaterial({ color }));
    };
    this._scene.add(
      mkAxis(new THREE.Vector3(0,0,0), new THREE.Vector3(len,0,0), COLORS.axis_x),
      mkAxis(new THREE.Vector3(0,0,0), new THREE.Vector3(0,len,0), COLORS.axis_z),
      mkAxis(new THREE.Vector3(0,0,0), new THREE.Vector3(0,0,len), COLORS.axis_y),
    );
  }

  _addGridFloor() {
    const helper = new THREE.GridHelper(200, 40, 0x1a2030, 0x1a2030);
    helper.position.y = -0.01;
    this._scene.add(helper);
  }

  _animate() {
    requestAnimationFrame(() => this._animate());
    this._controls.update();
    this._renderer.render(this._scene, this._camera);
  }

  _onResize() {
    const w = this._canvas.clientWidth;
    const h = this._canvas.clientHeight;
    this._camera.aspect = w / h;
    this._camera.updateProjectionMatrix();
    this._renderer.setSize(w, h);
  }

  _aspectRatio() {
    return this._canvas.clientWidth / (this._canvas.clientHeight || 600);
  }
}
