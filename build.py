"""
AWE USA 2026 VR Map Build Script
---------------------------------
Fetches live source files from ExpoFP, falls back to local backups if unavailable,
then parses the data and regenerates index.html.

Usage:
  python3 build.py            # Fetch live data, regenerate map
  python3 build.py --backup   # Use backup files only (no network fetch)
"""

import json
import math
import re
import sys
import shutil
import urllib.request
from pathlib import Path
from xml.dom import minidom


def bounding_box(positions):
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    x, y = min(xs), min(ys)
    return x, y, max(xs) - x, max(ys) - y

BASE_DIR = Path(__file__).parent
BACKUP_DIR = BASE_DIR / 'backup'
BACKUP_DIR.mkdir(exist_ok=True)

SOURCES = {
    'fp.svg.js': 'https://aweusa2026.expofp.com/data/fp.svg.js',
    'data.js':   'https://aweusa2026.expofp.com/data/data.js',
}

LOGO_BASE = 'https://efp-data.s3.amazonaws.com/expos/aweusa2026/data/'

# Layer render order (lower index = rendered first / lower Y)
LAYER_ORDER = ['LightBackground', 'DarkBackground', 'IcongBackground',
               'Icons', 'Legend&Logo', 'Booths', 'Text']

# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_sources(use_backup=False):
    for filename, url in SOURCES.items():
        dest = BASE_DIR / filename
        backup = BACKUP_DIR / filename

        if use_backup:
            if backup.exists():
                print(f'  [backup] Using {backup}')
                shutil.copy(backup, dest)
            else:
                raise FileNotFoundError(f'No backup found for {filename}')
            continue

        try:
            print(f'  [fetch]  Downloading {url} ...', end=' ', flush=True)
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            if len(data) < 100:
                raise ValueError('Response too small — likely an error page')
            dest.write_bytes(data)
            shutil.copy(dest, backup)   # Update backup with fresh copy
            print(f'OK ({len(data):,} bytes)')
        except Exception as e:
            print(f'FAILED ({e})')
            if backup.exists():
                print(f'  [backup] Falling back to {backup}')
                shutil.copy(backup, dest)
            else:
                raise RuntimeError(f'Could not fetch {filename} and no backup exists.') from e

# ── SVG parsing (V5 format) ────────────────────────────────────────────────────

def parse_svg(svg_file):
    content = Path(svg_file).read_text(encoding='utf-8')

    # V5 uses backtick template literal
    svg_match = re.search(
        r"window\['__fp'\]\s*=\s*window\['__fp'\]\s*=\s*`(.*?)`;",
        content, re.DOTALL)
    if not svg_match:
        raise ValueError('SVG string not found in fp.svg.js (expected V5 backtick format)')
    svg_str = svg_match.group(1)

    # V5 stores path geometry in a separate __fpPaths array
    paths_match = re.search(
        r"window\['__fpPaths'\]\s*=\s*window\['__fpPaths'\]\s*=\s*(\[.*?\]);",
        content, re.DOTALL)
    fp_paths = json.loads(paths_match.group(1)) if paths_match else []

    doc = minidom.parseString(svg_str)
    elements = []

    for layer in doc.getElementsByTagName('g'):
        layer_id = layer.getAttribute('id')
        if not layer_id:
            continue

        def process_el(el, layer_id=layer_id):
            tag = el.tagName
            style = el.getAttribute('style') or ''
            fill_match = re.search(r'fill:\s*(#[0-9A-Fa-f]+)', style)
            fill = fill_match.group(1) if fill_match else '#888888'

            if tag == 'rect':
                x, y, w, h = (el.getAttribute(a) for a in ('x', 'y', 'width', 'height'))
                if not (x and y and w and h):
                    return
                elements.append({
                    'guid':          el.getAttribute('id'),
                    'tag':           'rect',
                    'x': x, 'y': y, 'width': w, 'height': h,
                    'exhibitor_ids': el.getAttribute('data-exhibitors'),
                    'layer':         layer_id,
                    'fill':          fill,
                })
            elif tag == 'path':
                idx_str = el.getAttribute('data-index')
                if not idx_str:
                    return
                idx = int(idx_str)
                if idx >= len(fp_paths):
                    return
                positions = fp_paths[idx].get('positions', [])
                cells     = fp_paths[idx].get('cells', [])
                if len(positions) < 2:
                    return
                bx, by, bw, bh = bounding_box(positions)
                if bw < 1 or bh < 1:
                    return
                elements.append({
                    'guid':          el.getAttribute('data-guid') or f'path-{idx}',
                    'tag':           'path',
                    'x': str(bx), 'y': str(by),
                    'width': str(bw), 'height': str(bh),
                    'exhibitor_ids': '',
                    'layer':         layer_id,
                    'fill':          fill,
                    'positions':     positions,
                    'cells':         cells,
                })

        for el in layer.childNodes:
            if el.nodeType != minidom.Node.ELEMENT_NODE:
                continue
            if el.tagName == 'g':
                for sub in el.childNodes:
                    if sub.nodeType == minidom.Node.ELEMENT_NODE:
                        process_el(sub)
            else:
                process_el(el)

    return elements

# ── Metadata parsing ───────────────────────────────────────────────────────────

def parse_metadata(data_file):
    content = Path(data_file).read_text(encoding='utf-8').lstrip('\ufeff')
    start = content.find('{')
    end   = content.rfind('}') + 1
    data  = json.loads(content[start:end])
    exhibitors = {ex['id']: ex for ex in data.get('exhibitors', [])}
    categories = {cat['id']: cat['name'] for cat in data.get('categories', [])}
    return exhibitors, categories

# ── A-Frame generation ─────────────────────────────────────────────────────────

def generate_aframe(elements, exhibitors, categories, output_file):
    valid = [e for e in elements if e['x'] and e['y'] and e['width'] and e['height']]

    min_x = min(float(e['x'])                    for e in valid)
    max_x = max(float(e['x']) + float(e['width']) for e in valid)
    min_y = min(float(e['y'])                    for e in valid)
    max_y = max(float(e['y']) + float(e['height']) for e in valid)

    cx = (min_x + max_x) / 2
    cz = (min_y + max_y) / 2
    M  = 0.3048  # feet → metres

    booth_html = []

    # ── Background layers: assign strictly increasing Y (1mm steps) ──────────
    # Sorting by LAYER_ORDER ensures lower layers get lower Y values.
    BG_LAYERS = {'LightBackground', 'DarkBackground', 'IcongBackground', 'Icons', 'Legend&Logo', 'Text'}
    bg_elements = [e for e in valid if e.get('layer') in BG_LAYERS and not e.get('exhibitor_ids')]

    def layer_sort_key(e):
        try:
            return LAYER_ORDER.index(e.get('layer', ''))
        except ValueError:
            return 99

    bg_elements.sort(key=layer_sort_key)
    # Background uses 1mm increments starting at 0.001m
    BG_Y_BASE = 0.001
    BG_Y_STEP = 0.001
    bg_y_map = {id(e): round(BG_Y_BASE + i * BG_Y_STEP, 4) for i, e in enumerate(bg_elements)}
    bg_layer_map = {id(e): e.get('layer', '') for e in bg_elements}
    bg_ids   = {id(e) for e in bg_elements}
    # Booth floors render just above ground level (5mm) to prevent z-fighting with camera/background
    BOOTH_Y = 0.005

    for e in valid:
        x = (float(e['x']) - cx) * M
        z = (float(e['y']) - cz) * M
        w = float(e['width'])  * M
        h = float(e['height']) * M

        fill = e.get('fill') or '#888888'
        if fill == 'none': fill = '#888888'

        ex_ids_str = e.get('exhibitor_ids', '')
        if ex_ids_str:
            # ── Booth element ──────────────────────────────────────────────
            ex_ids = [int(i) for i in ex_ids_str.split(',') if i.strip()]
            exs    = [exhibitors[i] for i in ex_ids if i in exhibitors]
            if not exs:
                continue
            ex = exs[0]
            name      = ex.get('name', 'Unknown')
            safe_name = name.replace('"', '&quot;')
            logo_url  = (LOGO_BASE + ex['logo']) if ex.get('logo') else ''
            area      = float(e['width']) * float(e['height'])

            # Resolve category names for this exhibitor
            cat_ids   = ex.get('categories', [])
            cat_names = ', '.join(categories.get(cid, '') for cid in cat_ids if categories.get(cid))
            description = (ex.get('description') or '').strip()
            # Strip HTML tags from description
            description = re.sub(r'<[^>]+>', ' ', description)  # replace tags with space to preserve word boundaries
            description = re.sub(r'\s+', ' ', description).strip()  # collapse multiple spaces
            # Escape for HTML attribute
            safe_cats = cat_names.replace('"', '&quot;').replace("'", '&#39;')
            safe_desc = description.replace('"', '&quot;').replace("'", '&#39;')
            # AABB in world metres (used by proximity detector)
            aabb_min_x = round(x, 3)
            aabb_min_z = round(z, 3)
            aabb_max_x = round(x + w, 3)
            aabb_max_z = round(z + h, 3)

            if area < 400:
                # Small booth: floor space + table block + info wall
                booth_html.append(
                    f'        <a-entity class="booth-trigger" '
                    f'data-name="{safe_name}" data-cats="{safe_cats}" data-desc="{safe_desc}" '
                    f'data-minx="{aabb_min_x}" data-minz="{aabb_min_z}" '
                    f'data-maxx="{aabb_max_x}" data-maxz="{aabb_max_z}">'
                )
                booth_html.append(
                    f'          <a-plane position="{x+w/2:.3f} {BOOTH_Y} {z+h/2:.3f}" '
                    f'rotation="-90 0 0" width="{w:.3f}" height="{h:.3f}" color="{fill}"></a-plane>'
                )
                # Black border frame (4 thin boxes around edges)
                border_thickness = 0.01
                booth_html.append(
                    f'          <a-box position="{x+w/2:.3f} {BOOTH_Y} {z+h/2+h/2+border_thickness/2:.3f}" '
                    f'width="{w+border_thickness:.3f}" height="0.001" depth="{border_thickness:.3f}" color="#000000"></a-box>'
                )
                booth_html.append(
                    f'          <a-box position="{x+w/2:.3f} {BOOTH_Y} {z+h/2-h/2-border_thickness/2:.3f}" '
                    f'width="{w+border_thickness:.3f}" height="0.001" depth="{border_thickness:.3f}" color="#000000"></a-box>'
                )
                booth_html.append(
                    f'          <a-box position="{x+w/2-w/2-border_thickness/2:.3f} {BOOTH_Y} {z+h/2:.3f}" '
                    f'width="{border_thickness:.3f}" height="0.001" depth="{h:.3f}" color="#000000"></a-box>'
                )
                booth_html.append(
                    f'          <a-box position="{x+w/2+w/2+border_thickness/2:.3f} {BOOTH_Y} {z+h/2:.3f}" '
                    f'width="{border_thickness:.3f}" height="0.001" depth="{h:.3f}" color="#000000"></a-box>'
                )
                booth_html.append(
                    f'          <a-box class="booth-furniture" position="{x+w/2:.3f} 0.41 {z+h/2:.3f}" '
                    f'width="{w*0.6:.3f}" height="0.8" depth="{h*0.4:.3f}" color="#4CC3D9"></a-box>'
                )
                booth_html.append(
                    f'          <a-plane class="booth-furniture" position="{x+w/2:.3f} 1.25 {z-0.01:.3f}" '
                    f'width="{w:.3f}" height="2.5" color="#FFF" rotation="0 0 0">'
                )
                booth_html.append(
                    f'            <a-text value="{safe_name}" align="center" color="#000" '
                    f'width="2.5" position="0 0.5 0.05"></a-text>'
                )
                if logo_url:
                    booth_html.append(
                        f'            <a-image src="{logo_url}" width="0.8" '
                        f'position="0 -0.2 0.05" aspect-ratio></a-image>'
                    )
                booth_html.append('          </a-plane>')
                booth_html.append('        </a-entity>')
            else:
                # Large booth: colored floor
                booth_html.append(
                    f'        <a-entity class="booth-trigger" '
                    f'data-name="{safe_name}" data-cats="{safe_cats}" data-desc="{safe_desc}" '
                    f'data-minx="{aabb_min_x}" data-minz="{aabb_min_z}" '
                    f'data-maxx="{aabb_max_x}" data-maxz="{aabb_max_z}">'
                )
                booth_html.append(
                    f'          <a-plane position="{x+w/2:.3f} {BOOTH_Y} {z+h/2:.3f}" '
                    f'rotation="-90 0 0" width="{w:.3f}" height="{h:.3f}" color="{fill}"></a-plane>'
                )
                # Black border frame (4 thin boxes around edges)
                border_thickness = 0.01
                booth_html.append(
                    f'          <a-box position="{x+w/2:.3f} {BOOTH_Y} {z+h/2+h/2+border_thickness/2:.3f}" '
                    f'width="{w+border_thickness:.3f}" height="0.001" depth="{border_thickness:.3f}" color="#000000"></a-box>'
                )
                booth_html.append(
                    f'          <a-box position="{x+w/2:.3f} {BOOTH_Y} {z+h/2-h/2-border_thickness/2:.3f}" '
                    f'width="{w+border_thickness:.3f}" height="0.001" depth="{border_thickness:.3f}" color="#000000"></a-box>'
                )
                booth_html.append(
                    f'          <a-box position="{x+w/2-w/2-border_thickness/2:.3f} {BOOTH_Y} {z+h/2:.3f}" '
                    f'width="{border_thickness:.3f}" height="0.001" depth="{h:.3f}" color="#000000"></a-box>'
                )
                booth_html.append(
                    f'          <a-box position="{x+w/2+w/2+border_thickness/2:.3f} {BOOTH_Y} {z+h/2:.3f}" '
                    f'width="{border_thickness:.3f}" height="0.001" depth="{h:.3f}" color="#000000"></a-box>'
                )
                booth_html.append(
                    f'          <a-entity class="booth-furniture" position="{x+w/2:.3f} 2.5 {z+h/2:.3f}">'
                )
                booth_html.append(
                    f'            <a-text value="{safe_name}" align="center" color="#000" '
                    f'width="6" position="0 0.5 0.05"></a-text>'
                )
                if logo_url:
                    booth_html.append(
                        f'            <a-image src="{logo_url}" width="1.5" '
                        f'position="0 -0.5 0.05" aspect-ratio></a-image>'
                    )
                booth_html.append('          </a-entity>')
                booth_html.append('        </a-entity>')

        elif id(e) in bg_ids:
            # ── Background / floor element ─────────────────────────────────
            layer_y = bg_y_map[id(e)]

            if e.get('tag') == 'path' and e.get('positions'):
                # Render as a true polygon mesh via the floor-polygon component.
                # This avoids the bounding-box rectangle approximation that causes
                # two overlapping rectangles to z-fight at grazing angles.
                # ShapeGeometry is built in XY then rotated -PI/2 around X,
                # which maps shape-Y to world -Z. Negate Z here so the
                # double-negation restores the correct orientation.
                pts_str = json.dumps(
                    [[round((p[0]-cx)*M, 3), round(-(p[1]-cz)*M, 3)]
                     for p in e['positions']]
                )
                cells_str = json.dumps(e.get('cells', []))
                guid = e.get('guid', f'bg-{id(e)}')
                # Escape the JSON for use inside an HTML attribute
                pts_attr = pts_str.replace('"', '&quot;')
                cells_attr = cells_str.replace('"', '&quot;')
                booth_html.append(
                    f'        <a-entity id="{guid}" data-layer="{bg_layer_map[id(e)]}" '
                    f'floor-polygon="points: {pts_attr}; cells: {cells_attr}; color: {fill}; y: {layer_y}">'
                    f'</a-entity>'
                )
            else:
                # Rect-based background element
                booth_html.append(
                    f'        <a-plane data-layer="{bg_layer_map[id(e)]}" position="{x+w/2:.3f} {layer_y} {z+h/2:.3f}" '
                    f'rotation="-90 0 0" width="{w:.3f}" height="{h:.3f}" '
                    f'color="{fill}"></a-plane>'
                )

    # ── Outer walls: extruded from the show floor polygon (path-0) ─────────────
    # Find the large grey show floor polygon (guid='path-0') and build a 20 m
    # tall wall segment for each edge of its boundary.
    WALL_HEIGHT = 20.0   # metres
    WALL_COLOR  = '#9A9A9A'
    WALL_THICKNESS = 0.3  # metres — thin but solid

    floor_el = next((e for e in elements if e.get('guid') == 'path-0'), None)
    wall_html = []
    if floor_el and floor_el.get('positions'):
        wall_html.append('        <a-entity id="outer-walls">')
        pts = floor_el['positions']
        n   = len(pts)
        for i in range(n):
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            # World-space X/Z (same transform as booth geometry)
            x1 = (p1[0] - cx) * M
            z1 = (p1[1] - cz) * M
            x2 = (p2[0] - cx) * M
            z2 = (p2[1] - cz) * M
            dx = x2 - x1
            dz = z2 - z1
            seg_len = (dx*dx + dz*dz) ** 0.5
            if seg_len < 0.01:
                continue  # skip degenerate zero-length edges
            # Midpoint
            mx = (x1 + x2) / 2
            mz = (z1 + z2) / 2
            my = WALL_HEIGHT / 2  # centre of wall vertically
            # Rotation: A-Frame Y-axis rotation to align box WIDTH (local +X) with edge.
            # After Y rotation θ, local +X in world = (cos θ, 0, -sin θ).
            # We want that to equal (dx, 0, dz)/len, so θ = atan2(-dz, dx).
            angle_deg = math.degrees(math.atan2(-dz, dx))
            wall_html.append(
                f'          <a-box '
                f'position="{mx:.3f} {my:.3f} {mz:.3f}" '
                f'rotation="0 {angle_deg:.3f} 0" '
                f'width="{seg_len:.3f}" '
                f'height="{WALL_HEIGHT:.3f}" '
                f'depth="{WALL_THICKNESS:.3f}" '
                f'color="{WALL_COLOR}"></a-box>'
            )
        wall_html.append('        </a-entity>')

    inner = '\n'.join(booth_html) + ('\n' + '\n'.join(wall_html) if wall_html else '')
    hud_inner = inner.replace('class="booth-furniture"', 'class="hud-furniture" visible="false"')
    hud_inner = hud_inner.replace('class="booth-trigger"', 'class="hud-booth-trigger"')
    hud_inner = hud_inner.replace('class="structural-pillar"', 'class="hud-pillar" visible="false"')
    hud_inner = hud_inner.replace('id="outer-walls"', 'id="outer-walls" visible="false"')
    # Remove Text, Icons, and Legend&Logo layers from HUD to keep it clean
    for layer in ['Text', 'Icons', 'Legend&Logo']:
        hud_inner = re.sub(rf'<a-(entity|plane)[^>]*data-layer="{layer}"[^>]*>.*?</a-\1>', '', hud_inner, flags=re.IGNORECASE | re.DOTALL)

    hud_inner = re.sub(r'<a-text.*?</a-text>', '', hud_inner, flags=re.IGNORECASE | re.DOTALL)
    hud_inner = re.sub(r'<a-image.*?</a-image>', '', hud_inner, flags=re.IGNORECASE | re.DOTALL)
    hud_inner = re.sub(r'id="([^"]+)"', r'id="hud-\1"', hud_inner)

    html = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width">
    <title>AWE USA 2026 VR Map</title>
    <script src="https://aframe.io/releases/1.4.0/aframe.min.js"></script>
    <script>
      // floor-polygon: renders an arbitrary flat polygon as a Three.js mesh
      // at a precise world-space Y, completely avoiding z-fighting.
      AFRAME.registerComponent('floor-polygon', {
        schema: {
          points: { type: 'string' },
          cells:  { type: 'string' },
          color:  { type: 'color',  default: '#888888' },
          y:      { type: 'number', default: 0 }
        },
        init: function () {
          var data  = this.data;
          var pts   = JSON.parse(data.points);  // [[x,z], ...] metres
          var cells = data.cells ? JSON.parse(data.cells) : null;
          var geo;

          if (cells && cells.length > 0) {
            // Use pre-triangulated cells from source data
            geo = new THREE.BufferGeometry();
            var vertices = [];
            for (var i = 0; i < cells.length; i++) {
              var cell = cells[i];
              // Each cell is [idx1, idx2, idx3]
              for (var j = 0; j < 3; j++) {
                var p = pts[cell[j]];
                vertices.push(p[0], 0, -p[1]); // x, y, z (where z is -shapeY)
              }
            }
            geo.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
            geo.computeVertexNormals();
          } else {
            // Fallback to re-triangulation if cells not available
            var shape = new THREE.Shape();
            shape.moveTo(pts[0][0], pts[0][1]);
            for (var i = 1; i < pts.length; i++) {
              shape.lineTo(pts[i][0], pts[i][1]);
            }
            shape.closePath();
            geo = new THREE.ShapeGeometry(shape);
            geo.applyMatrix4(new THREE.Matrix4().makeRotationX(-Math.PI / 2));
          }

          var mat  = new THREE.MeshStandardMaterial({
            color: new THREE.Color(data.color),
            side: THREE.DoubleSide,
            roughness: 0.8,
            metalness: 0.0
          });
          var mesh = new THREE.Mesh(geo, mat);
          mesh.position.y = data.y;
          this.el.setObject3D('mesh', mesh);
        }
      });

      var dollhouseMode = false;
      var dollhouseScale = 0.05;
      var SCALE_MIN = 0.01;
      var SCALE_MAX = 0.30;
      var SCALE_STEP = 0.005;

      AFRAME.registerComponent('scale-switcher', {
        init: function () {
          var scene  = document.querySelector('#expo-scene');
          var camera = document.querySelector('#camera-rig');

          window.addEventListener('keydown', function(e) {
            var walls = document.querySelector('#outer-walls');
            var furniture = document.querySelectorAll('.booth-furniture');
            if (e.key === '1') {
              dollhouseMode = false;
              scene.setAttribute('scale',    '1 1 1');
              scene.setAttribute('position', '0 0 0');
              camera.setAttribute('position','0 1.753 0');
              if (walls) walls.setAttribute('visible', 'true');
              furniture.forEach(function(f) { f.setAttribute('visible', 'true'); });
            } else if (e.key === '2') {
              dollhouseMode = true;
              scene.setAttribute('scale',    dollhouseScale + ' ' + dollhouseScale + ' ' + dollhouseScale);
              scene.setAttribute('position', '0 1 -2');
              camera.setAttribute('position','0 1.753 0');
              if (walls) walls.setAttribute('visible', 'false');
              furniture.forEach(function(f) { f.setAttribute('visible', 'false'); });
            }
          });

          window.addEventListener('wheel', function(e) {
            if (!dollhouseMode) return;
            e.preventDefault();
            var delta = e.deltaY > 0 ? -SCALE_STEP : SCALE_STEP;
            dollhouseScale = Math.min(SCALE_MAX, Math.max(SCALE_MIN, dollhouseScale + delta));
            dollhouseScale = Math.round(dollhouseScale * 1000) / 1000;
            scene.setAttribute('scale', dollhouseScale + ' ' + dollhouseScale + ' ' + dollhouseScale);
          }, { passive: false });

          // Shift-sprint: hold Shift to move 4x faster in 1:1 mode
          var NORMAL_ACCEL = 65;   // A-Frame default wasd-controls acceleration
          var SPRINT_ACCEL = 260;  // 4x sprint
          window.addEventListener('keydown', function(e) {
            if (e.key === 'Shift' && !dollhouseMode) {
              var cam = document.querySelector('a-camera');
              if (cam) cam.setAttribute('wasd-controls', 'acceleration', SPRINT_ACCEL);
            }
          });
          window.addEventListener('keyup', function(e) {
            if (e.key === 'Shift') {
              var cam = document.querySelector('a-camera');
              if (cam) cam.setAttribute('wasd-controls', 'acceleration', NORMAL_ACCEL);
            }
          });
        }
      });

      AFRAME.registerComponent('hud-manager', {
        init: function () {
          this.camera = document.querySelector('a-camera');
          this.rotator = document.querySelector('#hud-rotator');
          this.content = document.querySelector('#hud-content');
          this.marker = document.querySelector('#hud-marker');
          this.visible = true;

          window.addEventListener('keydown', function(e) {
            if (e.key.toLowerCase() === 'm') {
              this.visible = !this.visible;
            }
          }.bind(this));
        },
        tick: function () {
          var shouldBeVisible = this.visible && !dollhouseMode;
          if (this.el.getAttribute('visible') !== shouldBeVisible) {
            this.el.setAttribute('visible', shouldBeVisible);
          }
          if (!shouldBeVisible) return;

          var worldPos = new THREE.Vector3();
          this.camera.object3D.getWorldPosition(worldPos);
          var worldQuat = new THREE.Quaternion();
          this.camera.object3D.getWorldQuaternion(worldQuat);
          var worldEuler = new THREE.Euler().setFromQuaternion(worldQuat, 'YXZ');

          // Head-locked HUD: stays in the same place on the screen.
          // To keep the map "North-up", we counter-rotate the rotator by the camera's yaw.
          this.rotator.object3D.rotation.y = -worldEuler.y;

          // The player is at the center of the HUD.
          // We shift the map content by the negative of the camera position.
          this.content.object3D.position.set(-worldPos.x, 0, -worldPos.z);

          // Marker stays at the center of the HUD
          this.marker.object3D.position.set(0, 5, 0);
        }
      });

      AFRAME.registerComponent('aspect-ratio', {
        init: function () {
          this.el.addEventListener('materialtextureloaded', function(e) {
            var img   = e.detail.texture.image;
            var ratio = img.height / img.width;
            var width = this.getAttribute('width');
            this.setAttribute('height', width * ratio);
          }.bind(this));
        }
      });

      // ── Proximity info window ────────────────────────────────────────────────
      // Each booth entity has class="booth-trigger" and data-minx/minz/maxx/maxz
      // (world-space AABB in metres) plus data-name/cats/desc.
      // Every tick the camera position is tested against all booth AABBs.
      // The nearest booth within TRIGGER_DIST (0.305 m ≈ 1 ft from edge) starts
      // a 0.5 s dwell timer; if still within range after the dwell the info
      // panel is shown. Moving away hides it immediately.
      window.TRIGGER_DIST = 0.305;   // metres — 1 ft from booth edge
      window.DWELL_MS     = 500;     // ms dwell before panel appears
      window.infoPanel    = null;    // the <a-entity> info panel
      window.currentBooth = null;    // DOM element of currently shown booth
      window.dwellTimer   = null;    // setTimeout handle
      window.dwellTarget  = null;    // booth DOM element being dwelled on

      // Canvas-based word-wrap helper
      window.canvasWrapText = function canvasWrapText(ctx, text, maxWidth) {
        var words = text.split(' ');
        var lines = [];
        var line = '';
        for (var i = 0; i < words.length; i++) {
          var testLine = line ? line + ' ' + words[i] : words[i];
          if (ctx.measureText(testLine).width > maxWidth && line) {
            lines.push(line);
            line = words[i];
          } else {
            line = testLine;
          }
        }
        if (line) lines.push(line);
        return lines;
      };

      // Build a canvas texture for the info panel
      window.buildPanelCanvas = function buildPanelCanvas(name, cats, desc) {
        var W = 512, H = 800;
        var canvas = document.createElement('canvas');
        canvas.width = W; canvas.height = H;
        var ctx = canvas.getContext('2d');

        // Outer background
        ctx.fillStyle = '#0d0d1a';
        ctx.fillRect(0, 0, W, H);

        // Name section background
        ctx.fillStyle = '#1a1a3e';
        ctx.fillRect(0, 0, W, 72);

        // Name text
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 26px Arial, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        var nameLines = window.canvasWrapText(ctx, name, W - 32);
        var nameY = nameLines.length > 1 ? 20 : 36;
        nameLines.slice(0, 2).forEach(function(ln, i) { ctx.fillText(ln, W/2, nameY + i * 28); });

        // Cyan divider
        ctx.fillStyle = '#4CC3D9';
        ctx.fillRect(0, 72, W, 3);

        // Categories section background
        ctx.fillStyle = '#162040';
        ctx.fillRect(0, 75, W, 90);

        // Categories text
        ctx.fillStyle = '#4CC3D9';
        ctx.font = '18px Arial, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        var catLines = window.canvasWrapText(ctx, cats || '(none)', W - 32);
        catLines.slice(0, 3).forEach(function(ln, i) { ctx.fillText(ln, W/2, 82 + i * 24); });

        // Divider
        ctx.fillStyle = '#334466';
        ctx.fillRect(0, 165, W, 2);

        // Description header
        ctx.fillStyle = '#aaaacc';
        ctx.font = 'bold 16px Arial, sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText('Description:', 16, 174);

        // Description body
        ctx.fillStyle = '#cccccc';
        ctx.font = '15px Arial, sans-serif';
        var descLines = window.canvasWrapText(ctx, desc || '(not provided)', W - 32);
        descLines.forEach(function(ln, i) { ctx.fillText(ln, 16, 198 + i * 22); });

        return canvas;
      };

      window.showInfoPanel = function showInfoPanel(boothEl) {
        if (window.currentBooth === boothEl) return;
        window.hideInfoPanel();
        window.currentBooth = boothEl;

        var name  = boothEl.dataset.name  || '';
        var cats  = boothEl.dataset.cats  || '';
        var desc  = boothEl.dataset.desc  || '';

        // Place panel 2.5 m in front of camera in world space.
        // Use the actual Three.js rendering camera (aScene.camera) which
        // follows standard Three.js convention: getWorldDirection returns -Z (look direction).
        var aScene = document.querySelector('a-scene');
        var rendCam = aScene.camera;
        var camWp2 = new THREE.Vector3();
        rendCam.getWorldPosition(camWp2);
        var camFwd = new THREE.Vector3();
        rendCam.getWorldDirection(camFwd); // Three.js: points toward where user looks (-Z)
        var px2 = camWp2.x + camFwd.x * 2.5;
        var py2 = camWp2.y;
        var pz2 = camWp2.z + camFwd.z * 2.5;

        // Build canvas texture
        var panelCanvas = window.buildPanelCanvas(name, cats, desc);
        var texture = new THREE.CanvasTexture(panelCanvas);

        // Create a single plane with the canvas texture
        var geometry = new THREE.PlaneGeometry(2.4, 3.75);
        // DoubleSide so it's visible regardless of rotation
        var material = new THREE.MeshBasicMaterial({ map: texture, side: THREE.DoubleSide, transparent: false });
        var mesh = new THREE.Mesh(geometry, material);

        // Wrap in an a-entity so hideInfoPanel can remove it
        // MUST append to scene FIRST so A-Frame initialises object3D
        window.infoPanel = document.createElement('a-entity');
        window.infoPanel.setAttribute('id', 'info-panel');
        document.querySelector('a-scene').appendChild(window.infoPanel);

        // Orient panel to face the camera: panel's default normal is +Z.
        // camFwd points FROM camera TOWARD panel.
        // We want the panel normal to point back toward the camera = -camFwd.
        // atan2(x, z) gives the angle in XZ plane.
        var yawRad = Math.atan2(-camFwd.x, -camFwd.z); // panel faces back toward camera
        mesh.position.set(px2, py2, pz2);
        mesh.rotation.set(0, yawRad, 0);

        // Add mesh after entity is in scene
        window.infoPanel.object3D.add(mesh);
        window._infoPanelMesh = mesh;
        window._infoPanelTexture = texture;
      };

      window.hideInfoPanel = function hideInfoPanel() {
        if (window.infoPanel) {
          window.infoPanel.parentNode && window.infoPanel.parentNode.removeChild(window.infoPanel);
          window.infoPanel = null;
        }
        window.currentBooth = null;
      };

      window.pointToAABBDist = function pointToAABBDist(px, pz, minx, minz, maxx, maxz) {
        // Signed distance from point (px,pz) to axis-aligned rectangle.
        // Returns 0 if inside, positive distance if outside.
        var dx = Math.max(minx - px, 0, px - maxx);
        var dz = Math.max(minz - pz, 0, pz - maxz);
        return Math.sqrt(dx*dx + dz*dz);
      };

      // Start proximity polling once the scene is ready
      document.addEventListener('DOMContentLoaded', function() {
        var scene = document.querySelector('a-scene');
        function startProximityLoop() {
          var camEl = document.querySelector('a-camera');
          if (!camEl || !camEl.object3D) {
            setTimeout(startProximityLoop, 200);
            return;
          }
          window._proximityInterval = setInterval(function() {
            if (dollhouseMode) {
              if (window.currentBooth) { clearTimeout(window.dwellTimer); window.dwellTimer = null; window.dwellTarget = null; window.hideInfoPanel(); }
              return;
            }
            var pos = camEl.object3D.getWorldPosition(new THREE.Vector3());
            var px = pos.x;
            var pz = pos.z;
            var closest     = null;
            var closestDist = Infinity;
            var booths = document.querySelectorAll('.booth-trigger');
            booths.forEach(function(b) {
              var d = window.pointToAABBDist(px, pz,
                parseFloat(b.dataset.minx), parseFloat(b.dataset.minz),
                parseFloat(b.dataset.maxx), parseFloat(b.dataset.maxz));
              if (d < closestDist) { closestDist = d; closest = b; }
            });
            // Show at TRIGGER_DIST; hide only when 2x away (hysteresis)
            var HIDE_DIST = window.TRIGGER_DIST * 2.0;
            if (closest && closestDist <= window.TRIGGER_DIST) {
              if (closest !== window.dwellTarget) {
                clearTimeout(window.dwellTimer);
                window.dwellTarget = closest;
                window.dwellTimer  = setTimeout(function() {
                  window.showInfoPanel(window.dwellTarget);
                }, window.DWELL_MS);
              }
            } else if (!closest || closestDist > HIDE_DIST) {
              if (window.dwellTimer) { clearTimeout(window.dwellTimer); window.dwellTimer = null; }
              window.dwellTarget = null;
              if (window.currentBooth) window.hideInfoPanel();
            }
          }, 100);  // poll every 100 ms
        }
        if (scene && scene.hasLoaded) { startProximityLoop(); }
        else { document.querySelector('a-scene').addEventListener('loaded', startProximityLoop); }
      });
    </script>
  </head>
  <body>
    <a-scene scale-switcher>
      <a-sky color="#ECECEC"></a-sky>

      <a-entity id="camera-rig" position="0 0 0">
        <a-camera user-height="0" position="0 1.753 0">
          <!-- HUD Map -->
          <a-entity id="hud-map" position="-0.18 0.1 -0.35" rotation="90 0 0" scale="0.0008 0.0008 0.0008" hud-manager visible="true">
            <a-plane width="450" height="450" color="#111" opacity="0.9" rotation="-90 0 0" position="0 -1.5 0"></a-plane>
            <a-entity id="hud-rotator">
              <a-entity id="hud-content">
                """ + hud_inner + """
              </a-entity>
            </a-entity>
            <a-sphere id="hud-marker" radius="12" color="#FF3333" position="0 20 0"></a-sphere>
          </a-entity>
        </a-camera>
      </a-entity>

      <a-entity id="expo-scene">
""" + inner + """
      </a-entity>
    </a-scene>

    <div style="position:fixed;top:10px;left:10px;background:rgba(0,0,0,.55);
                color:#fff;padding:10px 14px;font-family:sans-serif;border-radius:6px;
                font-size:14px;line-height:1.6;">
      <b>AWE USA 2026 VR Map</b><br>
      Press <kbd>1</kbd> &mdash; 1:1 scale &nbsp;|&nbsp; Press <kbd>2</kbd> &mdash; Dollhouse<br>
      Press <kbd>M</kbd> &mdash; Toggle HUD Map
    </div>
  </body>
</html>
"""
    Path(output_file).write_text(html, encoding='utf-8')
    print(f'  [build]  Written {output_file} ({Path(output_file).stat().st_size:,} bytes)')

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    use_backup = '--backup' in sys.argv

    print('=== AWE USA 2026 VR Map Builder ===')
    print(f'Mode: {"backup" if use_backup else "live (with backup fallback)"}')

    print('\n[1/3] Fetching source files...')
    fetch_sources(use_backup=use_backup)

    print('\n[2/3] Parsing SVG and exhibitor data...')
    elements              = parse_svg(BASE_DIR / 'fp.svg.js')
    exhibitors, categories = parse_metadata(BASE_DIR / 'data.js')
    print(f'  SVG elements: {len(elements)}, Exhibitors: {len(exhibitors)}, Categories: {len(categories)}')

    # Save parsed JSON alongside the HTML for debugging / inspection
    parsed_out = BASE_DIR / 'parsed_data.json'
    with open(parsed_out, 'w') as f:
        json.dump(elements, f, indent=2)

    print('\n[3/3] Generating A-Frame scene...')
    generate_aframe(elements, exhibitors, categories, BASE_DIR / 'index.html')

    print('\nDone.')

if __name__ == '__main__':
    main()
