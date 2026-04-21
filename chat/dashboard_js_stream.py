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
    if (ev.kind !== 'message') return;
    eventCount++;
    COUNT.textContent = String(eventCount).padStart(4, '0');
    emitPulse(ev.from_name, ev.to_name, hueFor(ev.from_name));
    bumpEdge(ev.from_name, ev.to_name);
    prependEntry(ev);
  };
}
function tick() {
  CLOCK.textContent = fmtClock(new Date());
  setTimeout(() => requestAnimationFrame(tick), 250);
}
FILTER_BTN.addEventListener('click', () => setFilter(filter));
loadAgents().then(loadMessages).then(connectStream);
setInterval(loadAgents, 8000);
tick();
"""
