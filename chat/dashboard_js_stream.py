"""Dashboard client — fetch/stream/entry-rendering half.

Paired with dashboard_js_graph.py; dashboard_js.py glues the two into
a single DASHBOARD_JS string inside an IIFE.
"""

JS_STREAM = r"""
function renderEntry(m, fresh) {
  const row = document.createElement('div');
  row.className = 'entry' + (fresh ? ' fresh' : '');
  row.dataset.from = m.from_name;
  row.dataset.to = m.to_name;
  row.style.setProperty('--from-color', hueFor(m.from_name));
  row.style.setProperty('--to-color', hueFor(m.to_name));
  row.style.setProperty('--entry-color', hueFor(m.from_name));
  const meta = document.createElement('div'); meta.className = 'meta';
  const parts = [
    ['ts', fmtTime(m.created_at)],
    ['from', m.from_name],
    ['arrow', '─►'],
    ['to', m.to_name],
    ['kind', m.type || ''],
  ];
  for (const [cls, txt] of parts) {
    const s = document.createElement('span');
    s.className = cls; s.textContent = txt; meta.appendChild(s);
  }
  const body = document.createElement('div');
  body.className = 'body'; body.textContent = m.body || '';
  row.append(meta, body);
  row.onclick = () => row.classList.toggle('open');
  applyFilter(row);
  return row;
}
function prependEntry(m) {
  const empty = FEED.querySelector('.empty');
  if (empty) empty.remove();
  FEED.insertBefore(renderEntry(m, true), FEED.firstChild);
  while (FEED.children.length > 240) FEED.lastChild.remove();
}
async function loadAgents() {
  try {
    const data = await (await fetch('api/agents')).json();
    positionAgents(data.agents);
    OPS.textContent = String(data.agents.length + 1).padStart(2, '0');
  } catch (e) { /* retry */ }
}
async function loadMessages() {
  try {
    const data = await (await fetch('api/messages?limit=140')).json();
    if (!data.messages.length) {
      FEED.innerHTML = '<div class="empty">no transmissions yet</div>';
      return;
    }
    FEED.innerHTML = '';
    for (const m of data.messages) FEED.appendChild(renderEntry(m, false));
    for (const m of data.messages.slice().reverse()) {
      const k = edgeKey(m.from_name, m.to_name);
      edgeCount.set(k, (edgeCount.get(k) || 0) + 1);
    }
    renderEdges();
  } catch (e) { /* retry */ }
}
const FLOW_EVENT_MAP = {
  wake_spawn_start:   { lane: '02', steps: ['03','04'] },
  wake_spawn_end:     { lane: '02', steps: ['04'] },
  hook_drain_stop:    { lane: '01', steps: ['03','04','05'] },
  hook_drain_session: { lane: '02', steps: ['05','06'] },
};
const FLOW_LIVE_MS = 2400;
let flowBusyTimer = null;
function fireStep(lane, step, delay) {
  const sel = '.card[data-lane="' + lane + '"][data-step="' + step + '"]';
  const card = document.querySelector(sel);
  if (!card) return;
  setTimeout(() => {
    card.classList.remove('fire');
    void card.getBoundingClientRect();
    card.classList.add('fire');
    setTimeout(() => card.classList.remove('fire'), 1200);
  }, delay);
}
function markFlowBusy(label) {
  document.body.classList.add('flow-busy');
  const t = document.getElementById('flowLiveText');
  if (t) t.textContent = label;
  if (flowBusyTimer) clearTimeout(flowBusyTimer);
  flowBusyTimer = setTimeout(() => {
    document.body.classList.remove('flow-busy');
    if (t) t.textContent = 'awaiting events';
  }, FLOW_LIVE_MS);
}
function fireLaneStart(lane) {
  fireStep(lane, '01', 0);
  fireStep(lane, '02', 180);
}
function onFlowEvent(ev) {
  const map = FLOW_EVENT_MAP[ev.event_type];
  if (!map) return;
  map.steps.forEach((s, i) => fireStep(map.lane, s, i * 220));
  markFlowBusy(ev.event_type.replace(/_/g, ' '));
}
function onMessageFlow() {
  fireLaneStart('01'); fireLaneStart('02');
  markFlowBusy('message in flight');
}
function connectStream() {
  const es = new EventSource('events');
  es.onopen = () => {
    STATUS.className = 'link-state live';
    STATUS_TEXT.textContent = 'link live';
  };
  es.onerror = () => {
    STATUS.className = 'link-state';
    STATUS_TEXT.textContent = 'link down · retry';
  };
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch (err) { return; }
    if (ev.kind === 'event') { onFlowEvent(ev); return; }
    if (ev.kind !== 'message') return;
    eventCount++;
    COUNT.textContent = String(eventCount).padStart(4, '0');
    emitPulse(ev.from_name, ev.to_name, hueFor(ev.from_name));
    bumpEdge(ev.from_name, ev.to_name);
    prependEntry(ev);
    onMessageFlow();
  };
}
function tick() {
  CLOCK.textContent = fmtClock(new Date());
  setTimeout(() => requestAnimationFrame(tick), 250);
}
function bindModeToggle() {
  const buttons = {
    obs:      document.getElementById('modeObs'),
    flow:     document.getElementById('modeFlow'),
    glossary: document.getElementById('modeGlossary'),
  };
  if (!buttons.obs || !buttons.flow || !buttons.glossary) return;
  const apply = (mode) => {
    document.body.classList.toggle('show-flow', mode === 'flow');
    document.body.classList.toggle('show-glossary', mode === 'glossary');
    for (const [k, el] of Object.entries(buttons)) {
      el.setAttribute('aria-pressed', String(k === mode));
    }
    try { localStorage.setItem('dashboard.mode', mode); } catch (e) { /*ignore*/ }
  };
  for (const [k, el] of Object.entries(buttons)) {
    el.addEventListener('click', () => apply(k));
  }
  let saved = null;
  try { saved = localStorage.getItem('dashboard.mode'); } catch (e) { /*ignore*/ }
  if (saved === 'flow' || saved === 'glossary') apply(saved);
}
function bindGlossarySearch() {
  const input = document.getElementById('glossSearch');
  const empty = document.getElementById('glossEmpty');
  const emptyTerm = document.getElementById('glossTerm');
  if (!input) return;
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll('.gloss-cat').forEach(cat => {
      let catShown = 0;
      cat.querySelectorAll('.gloss-entry').forEach(e => {
        const term = e.getAttribute('data-term') || '';
        const body = e.textContent.toLowerCase();
        const hit = !q || term.includes(q) || body.includes(q);
        e.hidden = !hit;
        if (hit) { catShown++; shown++; }
        if (q && hit) e.setAttribute('open', '');
        else if (!q) e.removeAttribute('open');
      });
      cat.hidden = catShown === 0;
    });
    if (emptyTerm) emptyTerm.textContent = q;
    if (empty) empty.hidden = shown !== 0;
  });
}
FILTER_BTN.addEventListener('click', () => setFilter(filter));
bindModeToggle();
bindGlossarySearch();
loadAgents().then(loadMessages).then(connectStream);
setInterval(loadAgents, 8000);
tick();
"""
