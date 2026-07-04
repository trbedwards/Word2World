"""Build a self-contained HTML web player from a Word2World game JSON.

Takes the JSON produced by the Word2World loop (data_gen_<experiment>.json)
or one of the bundled examples, matches every tile to a sprite the same way
play_game.py does, and writes a single HTML file with the sprites embedded
as base64 data URIs. The result runs in any browser - desktop or phone -
with no server and no dependencies.

Usage:
    python word2world/web_player.py path/to/game_data.json -o demo.html
    python word2world/web_player.py            # uses examples/example_1.json
"""

import argparse
import base64
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import map_to_list, find_most_similar_images, extract_list
from solvers import find_characters
from fixers import pad_rows_to_max_length
from configs import Config


def image_to_data_uri(pil_image, size=16):
    """Encode a PIL image as a base64 PNG data URI at the given tile size."""
    image = pil_image.convert("RGBA")
    if image.size != (size, size):
        image = image.resize((size, size))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def tile_char_list(raw_value):
    """Parse a tile list that may be stored as a list or as raw LLM text."""
    if isinstance(raw_value, list):
        return [str(tile) for tile in raw_value]
    text = str(raw_value)
    parsed = extract_list(text)
    if parsed:
        return [str(tile) for tile in parsed]
    # The raw text is usually a slightly malformed list like ['a', 'b' 'c'];
    # pull out the quoted single characters directly.
    quoted = re.findall(r"['\"](.)['\"]", text)
    if quoted:
        return quoted
    return [char for char in text if char.isalnum()]


def parse_objectives(objectives):
    """Normalise the objectives dict to [{'name': str, 'tile': str, 'row': int, 'col': int}]."""
    parsed = []
    if not isinstance(objectives, dict):
        return parsed
    for name, value in objectives.items():
        entry = {"name": str(name), "tile": None, "row": None, "col": None}
        target = value
        if isinstance(target, str):
            target = extract_list(target)
        if isinstance(target, (list, tuple)) and len(target) >= 3:
            entry["tile"] = str(target[0])
            try:
                entry["row"] = int(target[1])
                entry["col"] = int(target[2])
            except (TypeError, ValueError):
                pass
        parsed.append(entry)
    return parsed


def build_payload(data, round_number, tile_data_dir):
    round_data = data[round_number]

    grid_str = pad_rows_to_max_length(round_data["world"])
    grid_world = map_to_list(grid_str)

    world_1st_layer = pad_rows_to_max_length(round_data["world_1st_layer"]["world"])
    grid_1st_layer = map_to_list(world_1st_layer)

    char_tile_mapping = round_data["tile_mapping"]

    walkables = tile_char_list(round_data["walkable_tiles"])
    interactive_objects = tile_char_list(round_data["interactive_object_tiles"])
    # Characters are never valid floor or pickups
    walkables = [t for t in walkables if t not in ("@", "#")]
    interactive_objects = [t for t in interactive_objects if t not in ("@", "#")]

    print("Matching tiles to sprites...")
    tileset, _scores = find_most_similar_images(char_tile_mapping, tile_data_dir)

    tiles = {char: image_to_data_uri(img) for char, img in tileset.items()}

    # Most common walkable tile in the base layer becomes the default floor
    tile_counts = {}
    for row in grid_1st_layer:
        for tile in row:
            if tile in walkables:
                tile_counts[tile] = tile_counts.get(tile, 0) + 1
    if tile_counts:
        default_walkable = max(tile_counts, key=tile_counts.get)
    elif walkables:
        default_walkable = walkables[0]
    else:
        raise ValueError("No walkable tiles found in the map.")

    characters = find_characters(grid_str)
    if "@" not in characters:
        raise ValueError("No protagonist '@' found in the world map.")

    legend = {char: desc for desc, char in char_tile_mapping.items()}

    story = round_data.get("story", "")
    first_line = next((line for line in story.strip().splitlines() if line.strip()), "")
    title = re.sub(r"^#+\s*", "", first_line).strip(" *#") or "Word2World"
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0] + "…"

    return {
        "title": title,
        "story": story,
        "goals": str(round_data.get("goals", "")),
        "objectives": parse_objectives(round_data.get("objectives", {})),
        "grid": grid_world,
        "gridBase": grid_1st_layer,
        "tiles": tiles,
        "legend": legend,
        "walkables": walkables,
        "objects": interactive_objects,
        "defaultWalkable": default_walkable,
        "playerStart": list(characters["@"]),
        "enemyStart": list(characters.get("#", (-1, -1))),
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>__TITLE__</title>
<style>
  :root { --panel: #16161f; --ink: #e8e6df; --dim: #8a8878; --gold: #e3b341; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; background: #0b0b10; color: var(--ink);
    font-family: Georgia, 'Times New Roman', serif; overflow: hidden; }
  #app { display: flex; flex-direction: column; height: 100%; max-width: 900px; margin: 0 auto; }
  header { padding: 10px 14px 6px; text-align: center; }
  header h1 { font-size: 1.05rem; letter-spacing: .04em; color: var(--gold); font-weight: normal; }
  header p { font-size: .72rem; color: var(--dim); margin-top: 2px;
    font-family: ui-monospace, Menlo, Consolas, monospace; }
  #stage { position: relative; flex: 1; min-height: 0; display: flex;
    align-items: center; justify-content: center; padding: 6px; }
  canvas { image-rendering: pixelated; image-rendering: crisp-edges;
    background: #000; border: 1px solid #2a2a35; border-radius: 4px;
    max-width: 100%; max-height: 100%; touch-action: none; }
  #hud { display: flex; gap: 8px; align-items: center; padding: 4px 14px;
    font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .74rem; color: var(--dim);
    flex-wrap: wrap; justify-content: center; }
  #hud b { color: var(--ink); font-weight: normal; }
  #inventory { display: flex; gap: 4px; }
  #inventory img { width: 20px; height: 20px; image-rendering: pixelated;
    border: 1px solid #2a2a35; border-radius: 3px; background: #000; }
  #msg { position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%);
    background: rgba(10,10,16,.92); border: 1px solid #3a3a48; border-radius: 6px;
    padding: 8px 14px; font-size: .8rem; max-width: 86%; text-align: center;
    opacity: 0; transition: opacity .25s; pointer-events: none; }
  #msg.show { opacity: 1; }
  /* story overlay */
  #story { position: absolute; inset: 0; background: rgba(8,8,12,.96); z-index: 20;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    padding: 20px; text-align: center; }
  #story .scroll { max-width: 620px; max-height: 60vh; overflow-y: auto;
    font-size: .88rem; line-height: 1.55; color: var(--ink); text-align: left;
    white-space: pre-wrap; padding: 0 6px; }
  #story h2 { color: var(--gold); font-weight: normal; margin-bottom: 12px; font-size: 1.15rem; }
  #story button, #end button { margin-top: 18px; background: var(--gold); color: #14110a;
    border: 0; border-radius: 6px; padding: 10px 26px; font-size: .9rem;
    font-family: inherit; cursor: pointer; }
  #end { position: absolute; inset: 0; background: rgba(8,8,12,.94); z-index: 20;
    display: none; flex-direction: column; align-items: center; justify-content: center;
    padding: 20px; text-align: center; }
  #end h2 { color: var(--gold); font-weight: normal; font-size: 1.4rem; }
  #end p { color: var(--dim); margin-top: 8px; font-size: .85rem; max-width: 480px; }
  /* objectives drawer */
  #quests { position: absolute; top: 8px; right: 8px; z-index: 10;
    font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .68rem; }
  #quests details { background: rgba(10,10,16,.9); border: 1px solid #2a2a35;
    border-radius: 6px; padding: 6px 8px; max-width: 240px; }
  #quests summary { cursor: pointer; color: var(--gold); }
  #quests li { margin: 5px 0 0 14px; color: var(--dim); }
  #quests li.done { color: #6fbf73; text-decoration: line-through; }
  /* touch controls */
  #touch { display: none; position: absolute; inset: 0; pointer-events: none; z-index: 15; }
  #touch .pad { position: absolute; left: 12px; bottom: 12px; width: 132px; height: 132px; }
  #touch .pad button { position: absolute; width: 44px; height: 44px; pointer-events: auto;
    background: rgba(40,40,55,.75); color: var(--ink); border: 1px solid #3a3a48;
    border-radius: 8px; font-size: 18px; }
  #touch .actions { position: absolute; right: 12px; bottom: 24px; display: flex; gap: 10px; }
  #touch .actions button { pointer-events: auto; width: 54px; height: 54px; border-radius: 50%;
    background: rgba(60,50,25,.8); color: var(--gold); border: 1px solid #5a4a20; font-size: 11px;
    font-family: ui-monospace, monospace; }
  @media (pointer: coarse) { #touch { display: block; } }
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>__TITLE__</h1>
    <p>arrows / WASD move &middot; Z shoot &middot; SPACE strike &middot; walk into items to collect</p>
  </header>
  <div id="stage">
    <canvas id="game"></canvas>
    <div id="quests"></div>
    <div id="msg"></div>
    <div id="touch">
      <div class="pad">
        <button data-dir="up"    style="left:44px; top:0">&#9650;</button>
        <button data-dir="left"  style="left:0;   top:44px">&#9664;</button>
        <button data-dir="right" style="left:88px;top:44px">&#9654;</button>
        <button data-dir="down"  style="left:44px;top:88px">&#9660;</button>
      </div>
      <div class="actions">
        <button data-act="shoot">SHOOT</button>
        <button data-act="strike">STRIKE</button>
      </div>
    </div>
    <div id="story">
      <h2>__TITLE__</h2>
      <div class="scroll" id="storyText"></div>
      <button id="startBtn">Begin the Quest</button>
    </div>
    <div id="end">
      <h2 id="endTitle"></h2>
      <p id="endText"></p>
      <button onclick="location.reload()">Play Again</button>
    </div>
  </div>
  <div id="hud">
    <span>Score <b id="score">0</b></span>
    <span>Items <b id="itemCount">0</b></span>
    <span id="inventory"></span>
  </div>
</div>
<script>
const GAME = __GAME_DATA__;

const TILE = 16, VIEW_W = 20, VIEW_H = 16;
const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
let scale = 2;

function fitCanvas() {
  const stage = document.getElementById('stage');
  const maxW = stage.clientWidth - 12, maxH = stage.clientHeight - 12;
  scale = Math.max(1, Math.floor(Math.min(maxW / (VIEW_W * TILE), maxH / (VIEW_H * TILE))));
  canvas.width = VIEW_W * TILE * scale;
  canvas.height = VIEW_H * TILE * scale;
  ctx.imageSmoothingEnabled = false;
}
window.addEventListener('resize', () => { fitCanvas(); draw(); });

// --- world state ---
const grid = GAME.grid.map(r => r.split(''));
const base = GAME.gridBase.map(r => r.split(''));
const H = grid.length, W = grid[0].length;
const walkables = new Set(GAME.walkables);
const objects = new Set(GAME.objects);

const sprites = {};
let pending = 0;
for (const [ch, uri] of Object.entries(GAME.tiles)) {
  pending++;
  const img = new Image();
  img.onload = () => { if (--pending === 0) draw(); };
  img.src = uri;
  sprites[ch] = img;
}

// playerStart/enemyStart are (x, y) as returned by solvers.find_characters
const player = { x: GAME.playerStart[0], y: GAME.playerStart[1], face: [0, 1], alive: true };
const enemy = { x: GAME.enemyStart[0], y: GAME.enemyStart[1], dir: 1, alive: GAME.enemyStart[1] >= 0 };
const enemyHome = enemy.x;
grid[player.y][player.x] = GAME.defaultWalkable;
if (enemy.alive) grid[enemy.y][enemy.x] = GAME.defaultWalkable;

let bullets = [], enemyBullets = [];
let score = 0, picked = {}, totalObjects = 0, gameOver = false, started = false;
for (const row of grid) for (const t of row) if (objects.has(t)) totalObjects++;

const objectives = GAME.objectives.map(o => ({...o, done: false}));

// --- helpers ---
const cam = { x: 0, y: 0 };
function updateCamera() {
  cam.x = Math.max(0, Math.min(player.x - (VIEW_W >> 1), W - VIEW_W));
  cam.y = Math.max(0, Math.min(player.y - (VIEW_H >> 1), H - VIEW_H));
}
function walkable(x, y) {
  return x >= 0 && y >= 0 && x < W && y < H &&
    (walkables.has(grid[y][x]) || objects.has(grid[y][x]));
}
function showMsg(text, ms) {
  const el = document.getElementById('msg');
  el.textContent = text; el.classList.add('show');
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('show'), ms || 2200);
}
// Collapsed by default on touch screens, where the drawer would cover the map
let questsOpen = !window.matchMedia('(pointer: coarse)').matches;
function renderQuests() {
  const box = document.getElementById('quests');
  if (!objectives.length) { box.innerHTML = ''; return; }
  if (!box.querySelector('details')) {
    box.innerHTML = '<details' + (questsOpen ? ' open' : '') + '><summary></summary><ul></ul></details>';
    box.querySelector('details').addEventListener('toggle', e => { questsOpen = e.target.open; });
  }
  const doneCount = objectives.filter(o => o.done).length;
  box.querySelector('summary').textContent = 'Objectives ' + doneCount + '/' + objectives.length;
  box.querySelector('ul').innerHTML =
    objectives.map(o => '<li class="' + (o.done ? 'done' : '') + '">' + o.name + '</li>').join('');
}
function checkObjectives() {
  for (const o of objectives) {
    if (o.done) continue;
    if (o.row !== null && Math.abs(o.row - player.y) <= 1 && Math.abs(o.col - player.x) <= 1) {
      o.done = true; score += 100; showMsg('Objective complete: ' + o.name);
    } else if (o.tile && picked[o.tile]) {
      o.done = true; score += 100; showMsg('Objective complete: ' + o.name);
    }
  }
  renderQuests();
  if (!gameOver && objectives.length && objectives.every(o => o.done)) {
    endGame(true, 'Every objective is fulfilled. The story is complete.');
  }
}
function endGame(won, text) {
  gameOver = true;
  document.getElementById('endTitle').textContent = won ? 'Quest Complete' : 'You Have Fallen';
  document.getElementById('endText').textContent = text + ' Final score: ' + score;
  document.getElementById('end').style.display = 'flex';
}

// --- actions ---
function movePlayer(dx, dy) {
  if (!started || gameOver) return;
  player.face = [dx, dy];
  const nx = player.x + dx, ny = player.y + dy;
  if (!walkable(nx, ny)) return;
  player.x = nx; player.y = ny;
  const t = grid[ny][nx];
  if (objects.has(t)) {
    grid[ny][nx] = GAME.defaultWalkable;
    picked[t] = (picked[t] || 0) + 1;
    score += 25;
    const desc = GAME.legend[t] || 'an item';
    showMsg('Picked up: ' + desc);
    renderInventory();
  }
  updateCamera();
  checkObjectives();
}
function renderInventory() {
  let count = 0;
  const inv = document.getElementById('inventory');
  inv.innerHTML = '';
  for (const [t, n] of Object.entries(picked)) {
    count += n;
    if (GAME.tiles[t]) {
      const img = document.createElement('img');
      img.src = GAME.tiles[t]; img.title = GAME.legend[t] || t;
      inv.appendChild(img);
    }
  }
  document.getElementById('itemCount').textContent = count;
  document.getElementById('score').textContent = score;
}
function shoot() {
  if (!started || gameOver) return;
  bullets.push({ x: player.x, y: player.y, dx: player.face[0], dy: player.face[1] });
}
function strike() {
  if (!started || gameOver || !enemy.alive) return;
  if (Math.abs(player.x - enemy.x) + Math.abs(player.y - enemy.y) === 1) defeatEnemy('struck down');
}
function defeatEnemy(how) {
  enemy.alive = false; score += 250;
  showMsg('The antagonist is ' + how + '!');
  for (const o of objectives) {
    if (!o.done && /defeat|antagonist|villain|destroy|confront|stop/i.test(o.name)) {
      o.done = true; score += 100;
    }
  }
  renderQuests(); renderInventory();
  if (!objectives.length) endGame(true, 'The antagonist is defeated.');
  else checkObjectives();
}

// --- enemy AI (mirrors play_game.py) ---
function tickEnemy() {
  if (!enemy.alive || gameOver || !started) return;
  const sees = (Math.abs(player.x - enemy.x) <= 3 && player.y === enemy.y) ||
               (Math.abs(player.y - enemy.y) <= 3 && player.x === enemy.x);
  if (sees) {
    const dx = Math.sign(player.x - enemy.x), dy = dx === 0 ? Math.sign(player.y - enemy.y) : 0;
    enemyBullets.push({ x: enemy.x, y: enemy.y, dx, dy });
  } else {
    const nx = enemy.x + enemy.dir;
    if (Math.abs(nx - enemyHome) > 5 || !walkable(nx, enemy.y)) enemy.dir *= -1;
    else enemy.x = nx;
  }
}
function tickBullets() {
  for (const b of bullets) { b.x += b.dx; b.y += b.dy; }
  for (const b of enemyBullets) { b.x += b.dx; b.y += b.dy; }
  bullets = bullets.filter(b => {
    if (enemy.alive && b.x === enemy.x && b.y === enemy.y) { defeatEnemy('shot down'); return false; }
    return walkable(b.x, b.y);
  });
  enemyBullets = enemyBullets.filter(b => {
    if (b.x === player.x && b.y === player.y) {
      endGame(false, 'The antagonist’s attack found its mark.');
      return false;
    }
    return walkable(b.x, b.y);
  });
}

// --- rendering ---
function drawTile(ch, x, y) {
  const img = sprites[ch];
  if (img && img.complete) {
    ctx.drawImage(img, (x - cam.x) * TILE * scale, (y - cam.y) * TILE * scale, TILE * scale, TILE * scale);
  } else {
    ctx.fillStyle = '#f0f';
    ctx.fillRect((x - cam.x) * TILE * scale, (y - cam.y) * TILE * scale, TILE * scale, TILE * scale);
  }
}
function draw() {
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let y = cam.y; y < Math.min(cam.y + VIEW_H, H); y++) {
    for (let x = cam.x; x < Math.min(cam.x + VIEW_W, W); x++) {
      drawTile(GAME.defaultWalkable, x, y);          // floor fill
      const b = base[y] && base[y][x];
      if (b && b !== '@' && b !== '#') drawTile(b, x, y);   // terrain layer
      const t = grid[y][x];
      if (t !== b && t !== '@' && t !== '#') drawTile(t, x, y); // object layer
    }
  }
  if (enemy.alive) drawTile('#', enemy.x, enemy.y);
  drawTile('@', player.x, player.y);
  ctx.fillStyle = '#ffdd66';
  for (const b of bullets) {
    ctx.beginPath();
    ctx.arc((b.x - cam.x + .5) * TILE * scale, (b.y - cam.y + .5) * TILE * scale, 3 * scale, 0, 7);
    ctx.fill();
  }
  ctx.fillStyle = '#ff5555';
  for (const b of enemyBullets) {
    ctx.beginPath();
    ctx.arc((b.x - cam.x + .5) * TILE * scale, (b.y - cam.y + .5) * TILE * scale, 3 * scale, 0, 7);
    ctx.fill();
  }
}

// --- input ---
const keyDirs = {
  ArrowUp: [0,-1], ArrowDown: [0,1], ArrowLeft: [-1,0], ArrowRight: [1,0],
  w: [0,-1], s: [0,1], a: [-1,0], d: [1,0],
  W: [0,-1], S: [0,1], A: [-1,0], D: [1,0],
};
let held = null;
function press(dir) {
  // Move immediately so quick taps register; the main loop repeats while held.
  if (held !== dir) { held = dir; movePlayer(dir[0], dir[1]); }
}
document.addEventListener('keydown', e => {
  if (keyDirs[e.key]) { press(keyDirs[e.key]); e.preventDefault(); }
  else if (e.key === 'z' || e.key === 'Z') shoot();
  else if (e.key === ' ') { strike(); e.preventDefault(); }
});
document.addEventListener('keyup', e => {
  if (keyDirs[e.key] && held === keyDirs[e.key]) held = null;
});
const touchDirs = { up: [0,-1], down: [0,1], left: [-1,0], right: [1,0] };
for (const btn of document.querySelectorAll('#touch .pad button')) {
  const dir = touchDirs[btn.dataset.dir];
  btn.addEventListener('pointerdown', e => { e.preventDefault(); press(dir); });
  btn.addEventListener('pointerup', () => { if (held === dir) held = null; });
  btn.addEventListener('pointercancel', () => { if (held === dir) held = null; });
}
for (const btn of document.querySelectorAll('#touch .actions button')) {
  btn.addEventListener('pointerdown', e => {
    e.preventDefault();
    if (btn.dataset.act === 'shoot') shoot(); else strike();
  });
}

// --- main loop: 10 ticks/sec like play_game.py ---
setInterval(() => {
  if (held) movePlayer(held[0], held[1]);
  tickEnemy();
  tickBullets();
  renderInventory();
  draw();
}, 100);

// --- boot ---
document.getElementById('storyText').textContent = GAME.story.trim();
document.getElementById('startBtn').addEventListener('click', () => {
  document.getElementById('story').style.display = 'none';
  started = true;
  showMsg('Find the objectives. Beware the antagonist.', 3000);
});
renderQuests();
updateCamera();
fitCanvas();
draw();
</script>
</body>
</html>
"""


def build_html(payload):
    html = HTML_TEMPLATE.replace("__TITLE__", payload["title"])
    return html.replace("__GAME_DATA__", json.dumps(payload))


def main():
    cfg = Config()
    parser = argparse.ArgumentParser(description="Build a browser-playable Word2World demo.")
    parser.add_argument("game_path", nargs="?", default=None,
                        help="Path to a game JSON (defaults to word2world/examples/example_1.json)")
    parser.add_argument("-o", "--output", default=None, help="Output HTML path")
    parser.add_argument("--round", dest="round_number", default="round_0",
                        help="Which round of the generation to build (default round_0)")
    parser.add_argument("--title", default=None, help="Override the game title")
    args = parser.parse_args()

    game_path = args.game_path
    if game_path is None:
        game_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples", "example_1.json")
    if not os.path.exists(game_path):
        raise ValueError(f"{game_path} does not exist. Please provide an existing path.")

    with open(game_path, "r") as file:
        data = json.load(file)

    tile_data_dir = cfg.tile_data_dir
    if not os.path.exists(tile_data_dir):
        tile_data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    payload = build_payload(data, args.round_number, tile_data_dir)
    if args.title:
        payload["title"] = args.title

    output = args.output
    if output is None:
        stem = os.path.splitext(os.path.basename(game_path))[0]
        output = os.path.join(os.path.dirname(game_path) or ".", f"{stem}_web_player.html")

    html = build_html(payload)
    with open(output, "w", encoding="utf-8") as file:
        file.write(html)
    size_kb = os.path.getsize(output) / 1024
    print(f"Wrote {output} ({size_kb:.0f} KB) - open it in any browser to play.")


if __name__ == "__main__":
    main()
