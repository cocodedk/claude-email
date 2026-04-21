"""Dashboard client — graph/SVG half (nodes, edges, pulses, filter).

Paired with dashboard_js_stream.py; dashboard_js.py glues the two into
a single DASHBOARD_JS string inside an IIFE.
"""

JS_GRAPH = r"""
const NS = 'http://www.w3.org/2000/svg';
const EDGES = document.getElementById('edges');
const NODES = document.getElementById('nodes');
const PULSES = document.getElementById('pulses');
const FEED = document.getElementById('feed');
const FILTER_LABEL = document.getElementById('filterLabel');
const FILTER_BTN = document.getElementById('filterClear');
const STATUS = document.getElementById('status');
const STATUS_TEXT = document.getElementById('statusText');
const CLOCK = document.getElementById('clock');
const COUNT = document.getElementById('count');
const OPS = document.getElementById('ops');

const PALETTE = {};
const agents = new Map();
const edgeCount = new Map();
let filter = null;
let eventCount = 0;

function hueFor(name) {
  if (PALETTE[name]) return PALETTE[name];
  let h = 0;
  for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PALETTE[name] = `hsl(${h % 360} 72% 68%)`;
}
function svgEl(tag, attrs) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}
function polar(angle, r) {
  return { x: Math.cos(angle) * r, y: Math.sin(angle) * r };
}
function edgeKey(a, b) { return [a, b].sort().join('|'); }
function fmtTime(iso) {
  try { return new Date(iso).toISOString().substr(11, 12); }
  catch (e) { return String(iso); }
}
function fmtClock(d) { return d.toISOString().substr(11, 8) + 'Z'; }

function positionAgents(list) {
  NODES.querySelectorAll('.agent').forEach(n => n.remove());
  agents.clear();
  agents.set('user', { x: 0, y: 0, el: null, status: 'running' });
  const ringR = 165;
  const n = Math.max(list.length, 1);
  list.forEach((a, i) => {
    const angle = -Math.PI / 2 + (i / n) * Math.PI * 2;
    const { x, y } = polar(angle, ringR);
    const g = svgEl('g', {
      class: `node agent ${a.status || ''}`,
      transform: `translate(${x.toFixed(2)}, ${y.toFixed(2)})`,
      'data-name': a.name,
    });
    g.style.setProperty('--agent-color', hueFor(a.name));
    g.appendChild(svgEl('circle', { class: 'halo', r: 14 }));
    g.appendChild(svgEl('circle', { class: 'core', r: 9 }));
    const label = svgEl('text', { y: 28 });
    label.textContent = a.name.replace(/^agent-/, '');
    g.appendChild(label);
    const title = svgEl('title');
    title.textContent = `${a.name}\n${a.project_path || ''}\n${a.status || ''}`;
    g.appendChild(title);
    g.addEventListener('click', () => setFilter(a.name));
    NODES.appendChild(g);
    agents.set(a.name, { x, y, el: g, status: a.status });
  });
  renderEdges();
  applyMutedNodes();
}
function nodeXY(name) {
  const a = agents.get(name);
  return a ? { x: a.x, y: a.y } : null;
}
function renderEdges() {
  EDGES.innerHTML = '';
  for (const [k, n] of edgeCount) {
    const [a, b] = k.split('|');
    const p1 = nodeXY(a), p2 = nodeXY(b);
    if (!p1 || !p2 || (p1.x === p2.x && p1.y === p2.y)) continue;
    const mx = (p1.x + p2.x) / 2, my = (p1.y + p2.y) / 2;
    const nx = -(p2.y - p1.y), ny = p2.x - p1.x;
    const len = Math.hypot(nx, ny) || 1;
    const bow = 26;
    const cx = mx + (nx / len) * bow;
    const cy = my + (ny / len) * bow;
    const d = `M ${p1.x.toFixed(1)},${p1.y.toFixed(1)} Q ${cx.toFixed(1)},${cy.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
    const w = Math.min(1 + Math.log2(n + 1) * 0.9, 4.5);
    EDGES.appendChild(svgEl('path', {
      d, class: 'edge', 'stroke-width': w.toFixed(2),
      opacity: Math.min(0.22 + n * 0.04, 0.78).toFixed(2),
    }));
  }
}
function bumpEdge(from, to) {
  const k = edgeKey(from, to);
  edgeCount.set(k, (edgeCount.get(k) || 0) + 1);
  renderEdges();
}
function emitPulse(from, to, color) {
  const a = nodeXY(from), b = nodeXY(to);
  if (!a || !b) return;
  const dot = svgEl('circle', {
    r: 4.5, class: 'pulse', fill: color, cx: a.x, cy: a.y,
  });
  PULSES.appendChild(dot);
  const start = performance.now(), dur = 950;
  function step(t) {
    const k = Math.min((t - start) / dur, 1);
    const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    dot.setAttribute('cx', (a.x + (b.x - a.x) * e).toFixed(2));
    dot.setAttribute('cy', (a.y + (b.y - a.y) * e).toFixed(2));
    dot.setAttribute('opacity', (1 - k * 0.25).toFixed(2));
    if (k < 1) requestAnimationFrame(step);
    else {
      dot.remove();
      const tgt = agents.get(to);
      if (tgt && tgt.el) {
        tgt.el.classList.add('active');
        setTimeout(() => tgt.el.classList.remove('active'), 520);
      }
    }
  }
  requestAnimationFrame(step);
}
function setFilter(name) {
  filter = (filter === name) ? null : name;
  FILTER_LABEL.textContent = filter || 'ALL CHANNELS';
  FILTER_BTN.style.display = filter ? '' : 'none';
  for (const el of FEED.children) applyFilter(el);
  applyMutedNodes();
}
function applyMutedNodes() {
  for (const [name, a] of agents) {
    if (!a.el) continue;
    a.el.classList.toggle('muted', Boolean(filter) && filter !== name);
  }
}
function applyFilter(el) {
  if (!el.dataset || !el.dataset.from) return;
  if (!filter) { el.style.display = ''; return; }
  el.style.display = (el.dataset.from === filter || el.dataset.to === filter)
    ? '' : 'none';
}
"""
