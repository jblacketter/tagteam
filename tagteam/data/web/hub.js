/* Tagteam Hub (Phase 35) — plain JS. Ranks registered projects by what
 * needs the human (Needs you → Waiting → Quiet), shows burn and the shared
 * subscription window, and links each row to that project's cockpit
 * mounted at /p/<id>/. Live via EventSource(/api/hub/events), polling
 * fallback, slow 30 s snapshot refresh in live mode. Read-only page. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function fmtAge(s) {
    if (s == null || isNaN(s)) return '?';
    s = Math.max(0, Math.floor(s));
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d) return d + 'd ' + h + 'h';
    if (h) return h + 'h ' + String(m).padStart(2, '0') + 'm';
    if (m) return m + 'm';
    return s + 's';
  }
  function fmtInt(v) { return (typeof v === 'number') ? v.toLocaleString() : '-'; }
  function getJSON(url) { return fetch(url, { cache: 'no-store' }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; }); }); }
  function toast(kind, msg) { var t = el('div', 'toast ' + kind, msg); $('toasts').appendChild(t); setTimeout(function () { t.remove(); }, 6000); }

  var showAll = false;
  try { showAll = localStorage.getItem('tagteam.hub.showAll') === '1'; } catch (e) { /* ignore */ }
  $('show-all').checked = showAll;
  $('show-all').addEventListener('change', function () { showAll = this.checked; try { localStorage.setItem('tagteam.hub.showAll', showAll ? '1' : '0'); } catch (e) { /* ignore */ } refresh('toggle'); });

  var quietOpen = false;
  $('quiet-toggle').addEventListener('click', function () { quietOpen = !quietOpen; $('quiet-rows').classList.toggle('hidden', !quietOpen); this.firstChild.textContent = (quietOpen ? '▾ ' : '▸ ') + 'Quiet '; });

  function projCell(r) {
    var c = el('div', 'proj');
    c.appendChild(el('div', 'name', r.name));
    c.appendChild(el('div', 'parent', r.parent ? r.parent + '/' : ''));
    c.title = r.path;
    return c;
  }
  function cycleCell(r) {
    var c = el('div', 'cycle');
    if (r.phase) { c.innerHTML = '<b></b> · ' + (r.type || '') + ' · r' + (r.round == null ? '-' : r.round); c.querySelector('b').textContent = r.phase; }
    else c.textContent = r.state ? 'no cycle' : 'no state';
    return c;
  }
  function badges(r) {
    var w = el('div', 'why');
    if (r.group === 'needs_you') w.appendChild(el('span', 'badge hot', r.why));
    else if (r.group === 'waiting') {
      w.appendChild(el('span', 'badge' + (r.abandoned ? ' hot' : r.stale ? ' warn' : ''), r.abandoned ? 'abandoned? · ' + r.why : r.stale ? 'stale · ' + r.why : r.why));
      if (r.inflight && r.inflight.alive) w.appendChild(el('span', 'badge live', 'in flight'));
      if (r.watcher && r.watcher.running) w.appendChild(el('span', 'badge ok', 'watcher ' + (r.watcher.mode || '')));
      if (r.paused) w.appendChild(el('span', 'badge warn', 'paused'));
      if (r.hint && (r.stale || r.paused)) w.appendChild(el('span', 'hint-inline', r.hint));
    } else {
      w.appendChild(el('span', 'badge', r.why));
      if (r.kind === 'legacy') w.appendChild(el('span', 'badge', 'legacy (no tagteam.yaml)'));
      if (r.kind === 'no-yaml' || r.kind === 'scratch') w.appendChild(el('span', 'badge', r.kind));
    }
    if (r.error) w.appendChild(el('span', 'err', '! ' + r.error));
    return w;
  }
  function row(r, kind) {
    var d = el('div', 'row ' + kind);
    d.appendChild(projCell(r));
    d.appendChild(cycleCell(r));
    d.appendChild(badges(r));
    var age = el('div', 'age', kind === 'waiting' ? fmtAge(r.owed_age_s) : (r.last_activity_age_s != null ? fmtAge(r.last_activity_age_s) + ' ago' : ''));
    d.appendChild(age);
    // Phase 37: a Start link only when the project's launch intent has a command
    if (r.intent && r.intent.command) {
      var st = el('a', 'btn btn-small btn-start', 'Start ' + (r.intent.type === 'impl' ? 'implementation' : 'plan') + ' \u2192');
      st.href = '/p/' + encodeURIComponent(r.id) + '/#start';
      st.title = r.intent.command + ' — opens this project\u2019s cockpit Start card';
      d.appendChild(st);
    }
    var open = el('a', 'btn btn-small' + (kind === 'needs' ? ' btn-primary' : ''), 'Open');
    open.href = '/p/' + encodeURIComponent(r.id) + '/';
    open.title = 'open this project’s cockpit';
    d.appendChild(open);
    return d;
  }

  function render(p) {
    var g = p.groups, t = p.totals;
    $('hub-registry').textContent = (p.registry.total || 0) + ' registered';
    $('chip-count').textContent = t.projects + ' project' + (t.projects === 1 ? '' : 's');
    $('chip-live').textContent = t.live + ' live';
    $('chip-live').className = 'chip' + (t.live ? ' ok' : '');
    var u24 = p.usage && p.usage['24h'], u7 = p.usage && p.usage['7d'];
    $('chip-burn').textContent = u24 ? ('24h: ' + fmtInt(u24.input_tokens + u24.output_tokens) + ' tok · 7d: ' + fmtInt(u7.input_tokens + u7.output_tokens) + ' tok') : 'burn: —';
    var rl = p.rate_limits || [];
    $('chip-window').textContent = rl.length ? rl.map(function (r) { var when = r.resets_at ? new Date(r.resets_at) : null; return r.provider + ' ' + String(r.kind).replace('_', ' ') + ': ' + (r.status || '?') + (when && !isNaN(when.getTime()) ? ' → ' + when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : ''); }).join(' · ') : 'window: n/a';
    $('chip-window').className = 'chip' + (rl.some(function (r) { return r.status && r.status !== 'allowed'; }) ? ' warn' : '');

    var nr = $('needs-rows'); nr.innerHTML = '';
    g.needs_you.forEach(function (r) { nr.appendChild(row(r, 'needs')); });
    $('needs-count').textContent = g.needs_you.length ? String(g.needs_you.length) : '';
    $('needs-count').classList.toggle('hot', g.needs_you.length > 0);
    $('needs-you').classList.toggle('hot', g.needs_you.length > 0);
    $('needs-empty').classList.toggle('hidden', g.needs_you.length > 0);
    $('needs-empty-body').textContent = 'Nothing needs you across ' + t.projects + ' project' + (t.projects === 1 ? '' : 's') + '.' + (t.waiting ? ' ' + t.waiting + ' turn' + (t.waiting === 1 ? '' : 's') + ' owed to agents' + (t.stale ? ' (' + t.stale + ' stale)' : '') + '.' : '');

    var wr = $('waiting-rows'); wr.innerHTML = '';
    g.waiting.forEach(function (r) { wr.appendChild(row(r, 'waiting')); });
    $('waiting-count').textContent = g.waiting.length ? String(g.waiting.length) : '';
    $('waiting-meta').textContent = t.stale ? (t.stale + ' stale — nothing is dispatching; start a watcher') : '';
    $('waiting-empty').classList.toggle('hidden', g.waiting.length > 0);

    var qr = $('quiet-rows'); qr.innerHTML = '';
    g.quiet.forEach(function (r) { qr.appendChild(row(r, 'quiet')); });
    $('quiet-count').textContent = String(g.quiet.length);

    var hz = $('hidden'); var hr = $('hidden-rows'); hr.innerHTML = '';
    if (showAll && g.hidden.length) {
      hz.classList.remove('hidden');
      g.hidden.forEach(function (h) { var d = el('div', 'row'); var pc = el('div', 'proj'); pc.appendChild(el('div', 'name', h.path)); d.appendChild(pc); d.appendChild(el('span', 'badge', h.kind)); hr.appendChild(d); });
      $('hidden-count').textContent = String(g.hidden.length);
    } else hz.classList.add('hidden');
    $('registry-empty').style.display = p.registry.total ? 'none' : '';
  }

  var refreshing = false, queued = false;
  function refresh(reason) {
    if (refreshing) { queued = true; return; }
    refreshing = true;
    getJSON('/api/hub' + (showAll ? '?all=1' : '')).then(function (r) {
      if (!r.ok) throw new Error('hub ' + r.status);
      render(r.body);
    }).catch(function (e) { console.error('[hub] refresh failed', e); if (connMode === 'live') setConn('disconnected'); })
      .then(function () { refreshing = false; if (queued) { queued = false; refresh('queued'); } });
  }

  // ---------- live: SSE with polling fallback (same contract as the cockpit) ----------
  var connMode = 'connecting', es = null, pollTimer = null, pollDelay = 4000, sseRetry = null, sseAttempts = 0;
  function setConn(mode, text) { connMode = mode; $('conn').dataset.mode = mode; $('conn-text').textContent = text || ({ live: 'Live', polling: 'Polling', disconnected: 'Disconnected — retrying', connecting: 'Connecting…' })[mode]; }
  function startPolling() { if (pollTimer) return; setConn('polling'); var tick = function () { refresh('tick'); pollDelay = Math.min(20000, Math.round(pollDelay * 1.25)); pollTimer = setTimeout(tick, pollDelay); }; pollTimer = setTimeout(tick, pollDelay); }
  function stopPolling() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } pollDelay = 4000; }
  function connectSSE() {
    if (!window.EventSource || /[?&]nosse=1/.test(location.search)) { startPolling(); return; }
    try { es = new EventSource('/api/hub/events'); } catch (e) { startPolling(); return; }
    es.addEventListener('open', function () { sseAttempts = 0; stopPolling(); setConn('live'); });
    es.addEventListener('change', function () { refresh('sse'); });
    es.addEventListener('error', function () {
      setConn('disconnected'); startPolling();
      if (es && es.readyState === EventSource.CLOSED) {
        sseAttempts++;
        if (sseRetry) clearTimeout(sseRetry);
        sseRetry = setTimeout(function () { try { es.close(); } catch (e2) { /* ignore */ } connectSSE(); }, Math.min(30000, 2000 * Math.pow(2, Math.min(sseAttempts, 4))));
      }
    });
  }
  refresh('boot');
  connectSSE();
  setInterval(function () { if (connMode === 'live') refresh('tick'); }, 30000);
})();
