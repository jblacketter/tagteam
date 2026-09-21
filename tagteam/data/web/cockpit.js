/* Tagteam Cockpit (Phase 34) — plain JS, no framework, no build step.
 *
 * Zones, top to bottom (UX pass 2026-08-17: attention order = page order):
 * the Now strip (project · phase · who is working / who we wait on ·
 * watcher on/off · connection), the Needs-you banner (loud only when a
 * human is required; the Start card is an invitation), the Cycle region
 * (Phase 43: Lead | Reviewer lanes + the Activity log of every turn), the
 * tabs (<lead name> | Rounds | Diff | Usage | Notes). The page speaks the
 * arbiter's words — the CLI's names live in tooltips and the confirm modal.
 * Live via EventSource(/api/events); polling fallback.
 * Every control: pending → server {ok, message} as a toast; final actions
 * confirm with the exact CLI line the server will run (dry_run).
 */
(function () {
  'use strict';

  // ---------- helpers ----------
  var $ = function (id) { return document.getElementById(id); };
  var tokenMeta = document.querySelector('meta[name="tagteam-token"]');
  var TOKEN = tokenMeta ? tokenMeta.getAttribute('content') : null;
  // Phase 35: when the hub mounts this cockpit at /p/<id>/, the server
  // injects <meta name="tagteam-base"> and every API / EventSource /
  // navigation URL is prefixed with it. Standalone: absent → '' (identical).
  var baseMeta = document.querySelector('meta[name="tagteam-base"]');
  var BASE = baseMeta ? (baseMeta.getAttribute('content') || '').replace(/\/$/, '') : '';
  function url(path) { return BASE + path; }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function fmtAge(s) {
    if (s == null || isNaN(s)) return '?';
    s = Math.max(0, Math.floor(s));
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (d) return d + 'd' + String(h).padStart(2, '0') + 'h';
    if (h) return h + 'h' + String(m).padStart(2, '0') + 'm';
    if (m) return m + 'm' + String(sec).padStart(2, '0') + 's';
    return sec + 's';
  }
  function fmtTs(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts).slice(0, 19);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  function fmtTime(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts).slice(11, 19);
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  function fmtInt(v) { return (typeof v === 'number') ? v.toLocaleString() : '-'; }
  function firstLine(s, n) {
    s = String(s || '').trim();
    var line = s.split('\n')[0];
    if (line.length > (n || 160)) line = line.slice(0, (n || 160) - 1) + '…';
    return line;
  }
  function getJSON(path) {
    return fetch(url(path), { cache: 'no-store' }).then(function (r) {
      return r.json().then(function (b) { return { ok: r.ok, status: r.status, body: b }; });
    });
  }
  function postJSON(path, data) {
    var headers = { 'Content-Type': 'application/json' };
    if (TOKEN) headers['X-Tagteam-Token'] = TOKEN;
    return fetch(url(path), { method: 'POST', headers: headers, body: JSON.stringify(data || {}) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          return { ok: r.ok && b.ok !== false, status: r.status, body: b };
        });
      });
  }
  function toast(kind, msg, cli) {
    var t = el('div', 'toast ' + kind);
    t.textContent = msg || (kind === 'ok' ? 'Done.' : 'Failed.');
    if (cli) { var c = el('span', 't-cli', cli); t.appendChild(c); }
    $('toasts').appendChild(t);
    setTimeout(function () { t.style.opacity = '0'; t.style.transition = 'opacity .4s'; }, kind === 'err' ? 9000 : 5000);
    setTimeout(function () { t.remove(); }, kind === 'err' ? 9600 : 5600);
  }

  // Run an action with the feedback contract: pending → toast(message).
  // `confirmText` → confirm modal showing the exact CLI first (dry_run).
  function act(btn, url, data, opts) {
    opts = opts || {};
    var run = function () {
      if (btn) { btn.disabled = true; btn.classList.add('pending'); }
      return postJSON(url, data).then(function (r) {
        var msg = (r.body && (r.body.message || r.body.error)) || (r.ok ? 'Done.' : 'Failed (' + r.status + ')');
        toast(r.ok ? 'ok' : 'err', msg, r.body && r.body.cli);
        if (opts.onDone) opts.onDone(r);
        if (r.ok) refreshAll('action'); else if (opts.onError) opts.onError(msg);
        return r;
      }).catch(function (e) {
        toast('err', 'Request failed: ' + e);
      }).then(function (r) {
        if (btn) { btn.disabled = false; btn.classList.remove('pending'); }
        return r;
      });
    };
    if (!opts.confirm) return run();
    // dry-run to get the exact CLI line the server would run
    return postJSON(url, Object.assign({}, data, { dry_run: true })).then(function (r) {
      if (!r.ok) { toast('err', (r.body && (r.body.message || r.body.error)) || 'Cannot prepare action'); return; }
      confirmModal(opts.confirm.title, opts.confirm.body, r.body.cli, run, opts.confirm.labels);
    });
  }

  var confirmCb = null;
  function confirmModal(title, body, cli, onOk, labels) {
    // Error prevention: a destructive confirm names the action on its
    // button ("Stop the turn" / "Keep going"), never a generic Run/Cancel
    // pair that reads two ways for a cancel action.
    labels = labels || {};
    $('confirm-title').textContent = title;
    $('confirm-body').textContent = body || '';
    $('confirm-cli').textContent = cli || '';
    $('confirm-ok').textContent = labels.ok || 'Run';
    $('confirm-ok').className = 'btn ' + (labels.danger ? 'btn-danger-solid' : 'btn-primary');
    $('confirm-cancel').textContent = labels.cancel || 'Cancel';
    confirmCb = onOk;
    $('confirm').classList.remove('hidden');
    $('confirm-ok').focus();
  }
  $('confirm-cancel').addEventListener('click', function () { $('confirm').classList.add('hidden'); confirmCb = null; });
  $('confirm-ok').addEventListener('click', function () { var cb = confirmCb; $('confirm').classList.add('hidden'); confirmCb = null; if (cb) cb(); });
  $('confirm').addEventListener('click', function (e) { if (e.target === $('confirm')) { $('confirm').classList.add('hidden'); confirmCb = null; } });

  // ---------- state ----------
  var NOW = null;              // /api/now payload
  var START = null;            // /api/start payload (Phase 37 launch intent)
  var CYCLE_ID = null;         // "<phase>_<type>"
  var briefCurrent = null;     // /api/brief/current payload
  var lastRoundsKey = null;
  var diffLoaded = false, usageLoaded = false, notesLoaded = false;
  var expandedFeed = {};       // id -> bool
  var expandedFiles = {};      // path -> bool

  // ---------- Now strip ----------
  function renderNow(n) {
    NOW = n;
    var st = n.state || {};
    var phase = st.phase, type = st.type;
    CYCLE_ID = (phase && type) ? phase + '_' + type : null;
    setVerdictCycle(CYCLE_ID); setLaneCycle(CYCLE_ID);   // Phase 58: a new cycle clears chips and the reviewer lane
    var projName = (n.project_dir || '').split('/').filter(Boolean).slice(-1)[0] || 'project';
    $('now-project').textContent = projName; $('now-project').title = n.project_dir || '';
    document.title = projName + ' — Tagteam';

    // phase chip: "lead-gen-rebuild — implementation, round 2 — approved"
    var cyc = $('chip-cycle');
    if (phase) {
      cyc.innerHTML = '<b>' + esc(phase) + '</b> — ' + esc(typeWord(type)) + ', round ' + esc(st.round);
      var cs = n.cycle && n.cycle.state;
      if (cs && cs !== 'in-progress') cyc.innerHTML += ' — <span class="' + (cs === 'approved' ? '' : 'muted') + '">' + esc(cycleWord(cs)) + '</span>';
    } else {
      cyc.textContent = 'no phase in progress';
    }

    // Phase 68: who has the ball is ONE sentence, derived server-side (n.headline);
    // the watcher's facts and history live in the drawer behind it.
    renderTurnBar(n);
    renderDrawerFacts(n);
    if (n.paused) {
      $('btn-pause').textContent = 'Resume';
      $('btn-pause').title = 'resume turns (tagteam resume) — the watcher picks up the waiting turn';
    } else {
      $('btn-pause').textContent = 'Pause';
      $('btn-pause').title = 'pause turns (tagteam pause) — nothing new is started until you resume';
    }

    // Phase 38: gate chip — only when the gate is on; last decision for this cycle.
    var gc = $('chip-gate');
    var gk = n.gatekeeper || {};
    if (gc) {
      if (gk.enabled) {
        gc.classList.remove('hidden');
        gc.className = 'chip' + (gk.last && gk.last.status === 'bounce' ? ' warn' : (gk.last ? ' ok' : ''));
        if (gk.last) {
          gc.innerHTML = 'pre-check ' + (gk.last.status === 'pass' ? '&#10003; passed' : '&#8617; bounced') + ' r' + esc(gk.last.round);
          gc.title = (gk.last.headline || ('gate ' + gk.last.status)) + (gk.last.ts ? ' · ' + fmtTs(gk.last.ts) : '');
        } else {
          gc.textContent = 'pre-checks on';
          gc.title = 'the gatekeeper runs the tests and a scope check before each review (' + (gk.on || []).join(', ') + ')';
        }
      } else gc.classList.add('hidden');
    }

    var notes = $('chip-notes');
    if (n.pending_notes) { notes.classList.remove('hidden'); notes.textContent = n.pending_notes + ' note' + (n.pending_notes > 1 ? 's' : '') + ' waiting'; }
    else notes.classList.add('hidden');
    var nc = $('notes-count'); nc.textContent = n.pending_notes ? String(n.pending_notes) : '';

    renderLanes(n);
    renderNeeds();
    fitLanes();
  }

  $('btn-watcher').addEventListener('click', function (e) {
    e.stopPropagation();
    var wr = (NOW && NOW.watcher) || {};
    if (wr.running) {
      act($('btn-watcher'), '/api/watch/stop', {}, { confirm: { title: 'Stop the watcher?', body: 'Sends SIGTERM to the pidfile\'d watcher only if its identity verifies.' } });
    } else {
      var mode = watcherStartMode();
      act($('btn-watcher'), '/api/watch/start', { mode: mode }, { confirm: { title: 'Start the watcher?', body: watcherStartBody(mode) } });
    }
  });

  $('btn-pause').addEventListener('click', function () {
    var btn = this;
    if (NOW && NOW.paused) act(btn, '/api/resume', {});
    else act(btn, '/api/pause', { reason: 'paused from the cockpit' });
  });
  // (Phase 43: the tail drawer is gone — the running Activity row streams
  // the same log, and Cancel lives on that row.)

  // ---------- Phase 68: Turn bar + watcher drawer (text nodes only — the page holds the POST token) ----------
  // The sentence, its tone and its age come from the server (`n.headline`,
  // cockpit_api.headline). This block only presents them: it appends the ONE
  // ticking age, colours the bar, and fills the drawer. It must not work out
  // who has the ball for itself.
  var HEAD = null;                 // the headline being shown (age ticks locally between polls)
  var DRAWER = { open: false, all: false, events: [], follow: true, timer: null };
  var DRAWER_KEY = 'tagteam.cockpit.drawer';
  var KIND_FAMILY = { turn: 'dispatch', sent: 'dispatch', resumed: 'dispatch', advance: 'dispatch', start: 'dispatch', stop: 'dispatch',
    gate: 'check', panel: 'check', paused: 'hold', watchdog: 'hold', done: 'done',
    'send-failed': 'problem', refused: 'problem', stuck: 'problem', error: 'problem', aborted: 'problem', escalated: 'problem', info: 'info' };
  var TERMINAL_MODES = { iterm2: 1, terminal: 1, tmux: 1 };

  function renderTurnBar(n) {
    var h = (n && n.headline) || { state: 'idle', tone: 'idle', text: '…', age_s: null };
    HEAD = { state: h.state, tone: h.tone, text: h.text, age_s: h.age_s, role: h.role };
    var bar = $('turn-bar');
    bar.className = 'turn-bar tone-' + (h.tone || 'idle') + (h.role === 'lead' || h.role === 'reviewer' ? ' role-' + h.role : '');
    bar.dataset.state = h.state || '';
    $('turn-text').textContent = h.text || '';
    paintTurnAge();
  }
  function paintTurnAge() {
    $('turn-age').textContent = (HEAD && HEAD.age_s != null) ? '· ' + fmtAge(Math.round(HEAD.age_s)) : '';
  }
  function tickTurnBar() {        // called once a second by renderNowAges
    if (HEAD && HEAD.age_s != null) { HEAD.age_s += 1; paintTurnAge(); }
  }

  function watcherStartMode() { return (START && START.headless && START.headless.ok) ? 'headless' : 'notify'; }
  function headlessReasons() { return (START && START.headless && !START.headless.ok && START.headless.errors) || []; }
  function watcherStartBody(mode) {
    if (mode === 'headless') return 'From then on each waiting turn runs by itself as a fresh agent process, detached from this server.';
    var why = headlessReasons();
    return 'Starts a watcher that only NOTIFIES you on each turn flip — it runs no turns. This page cannot run the agents itself yet' +
      (why.length ? ': ' + why.join('; ') + '.' : '.');
  }
  function watcherNote(n) {       // why the lanes may be empty / why this page cannot run turns
    var wr = (n && n.watcher) || {};
    if (wr.running && TERMINAL_MODES[wr.mode]) return 'Agents are running in their terminals (' + wr.mode + '). The lanes show only turns the cockpit runs itself.';
    if (wr.running && wr.mode === 'notify') return 'This watcher only notifies you; it runs no turns.';
    var why = headlessReasons();
    if (!wr.running && why.length) return 'This page cannot run the agents itself yet: ' + why.join('; ') + '.';
    return '';
  }

  function fact(dl, label, value, cls) {
    dl.appendChild(el('dt', null, label));
    dl.appendChild(el('dd', cls || null, value));
  }
  function renderDrawerFacts(n) {
    var wr = (n && n.watcher) || {}; var beat = wr.beat || {};
    var dl = $('wd-facts');
    while (dl.firstChild) dl.removeChild(dl.firstChild);
    if (wr.running) fact(dl, 'watcher', 'running — ' + (wr.mode || 'mode unknown') + ', pid ' + wr.pid + (wr.started_at ? ', since ' + fmtTime(wr.started_at) : ''));
    else fact(dl, 'watcher', wr.stale_pidfile ? 'not running — the recorded process died' : 'not running', (n && n.owed) ? 'warn' : null);
    fact(dl, 'last look', beat.text || 'never', beat.state === 'stale' ? 'danger' : null);
    fact(dl, 'turns', n && n.paused ? 'paused' + (n.paused.by ? ' by ' + n.paused.by : '') + (n.paused.reason ? ' — ' + n.paused.reason : '') : 'not paused', n && n.paused ? 'warn' : null);
    if (n && n.last_turn) fact(dl, 'last turn', lastTurnText(n.last_turn).replace(/^last: /, ''));
    var wbtn = $('btn-watcher');
    if (wr.running) {
      wbtn.textContent = 'Stop the watcher'; wbtn.title = 'stop this watcher (identity-checked)';
      wbtn.classList.toggle('hidden', wr.source !== 'pidfile');
    } else {
      wbtn.textContent = 'Start the watcher'; wbtn.title = 'tagteam watch --mode ' + watcherStartMode() + ' --pidfile';
      wbtn.classList.remove('hidden');
    }
    var note = watcherNote(n);
    $('wd-note').textContent = note;
    $('wd-note').classList.toggle('hidden', !note);
  }

  function drawerRow(ev) {
    var fam = KIND_FAMILY[ev.kind] || 'info';
    var row = el('div', 'wd-row f-' + fam);
    row.dataset.kind = ev.kind || 'info';
    row.appendChild(el('span', 't', fmtTime(ev.ts)));
    row.appendChild(el('span', 'k', ev.kind || 'info'));
    row.appendChild(el('span', 'm', String(ev.msg == null ? '' : ev.msg).replace(/^\s+/, '')));
    return row;
  }
  function atBottom(box) { return box.scrollHeight - box.scrollTop - box.clientHeight < 8; }
  function renderDrawerRows(events) {
    var box = $('wd-rows');
    var had = DRAWER.events.length;
    var follow = had === 0 || atBottom(box);       // follow new rows only while already at the bottom
    var top = box.scrollTop;
    DRAWER.events = events || [];
    while (box.firstChild) box.removeChild(box.firstChild);
    var shown = 0;
    DRAWER.events.forEach(function (ev) {
      if (!DRAWER.all && (ev.kind || 'info') === 'info') return;
      box.appendChild(drawerRow(ev)); shown += 1;
    });
    if (!shown) {
      box.appendChild(el('div', 'wd-empty', DRAWER.events.length
        ? 'Only routine lines so far — tick "show everything" to see them.'
        : 'No watcher history yet — it is recorded once a watcher runs here (tagteam 3.14.5+).'));
    }
    if (follow) { box.scrollTop = box.scrollHeight; $('wd-new').classList.add('hidden'); }
    else { box.scrollTop = top; $('wd-new').classList.toggle('hidden', DRAWER.events.length <= had); }
    DRAWER.follow = follow;
  }
  function loadWatcherEvents() {
    if (!DRAWER.open) return Promise.resolve();   // nothing is fetched while the drawer is closed
    return getJSON('/api/watcher/events?n=200').then(function (r) {
      if (r.ok && r.body && DRAWER.open) renderDrawerRows(r.body.events || []);
    });
  }
  function refreshDrawer() {
    // `last look` is a sentence with an age in it; while the drawer is open it
    // is re-read every few seconds rather than left to go stale for 30 s.
    if (!DRAWER.open) return;
    getJSON('/api/now').then(function (r) { if (r.ok && r.body && DRAWER.open) { renderTurnBar(r.body); NOW.watcher = r.body.watcher; NOW.paused = r.body.paused; renderDrawerFacts(r.body); } });
    loadWatcherEvents();
  }
  function setDrawer(open) {
    DRAWER.open = !!open;
    $('watcher-drawer').classList.toggle('hidden', !DRAWER.open);
    $('turn-bar').setAttribute('aria-expanded', DRAWER.open ? 'true' : 'false');
    $('turn-caret').textContent = DRAWER.open ? '▴' : '▾';
    try { localStorage.setItem(DRAWER_KEY, DRAWER.open ? '1' : '0'); } catch (e) {}
    if (DRAWER.timer) { clearInterval(DRAWER.timer); DRAWER.timer = null; }
    if (DRAWER.open) { loadWatcherEvents(); DRAWER.timer = setInterval(refreshDrawer, 5000); }
    if (typeof fitLanes === 'function') fitLanes();
  }
  $('turn-bar').addEventListener('click', function () { setDrawer(!DRAWER.open); });
  $('wd-all').addEventListener('change', function () { DRAWER.all = !!$('wd-all').checked; renderDrawerRows(DRAWER.events); });
  $('wd-new').addEventListener('click', function () { var box = $('wd-rows'); box.scrollTop = box.scrollHeight; $('wd-new').classList.add('hidden'); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && DRAWER.open && !document.querySelector('.modal-overlay:not(.hidden)')) setDrawer(false); });
  // ---------- end Phase 68 ----------

  // ---------- Needs you ----------
  function briefSections(md) {
    // Split the brief markdown by "## " headings → [{title, text}]
    var out = [], cur = null;
    String(md || '').split('\n').forEach(function (line) {
      var m = /^##\s+(.*)$/.exec(line);
      if (m) { cur = { title: m[1].trim(), text: '' }; out.push(cur); }
      else if (cur) cur.text += line + '\n';
      else if (line.trim() && !/^#\s/.test(line) && !/^<!--/.test(line)) { cur = { title: '', text: line + '\n' }; out.push(cur); }
    });
    return out;
  }

  function cardShell(kind, title, meta) {
    var c = el('div', 'card ' + kind);
    var h = el('div', 'card-head');
    h.appendChild(el('span', 'card-kind', kind === 'hold' ? 'hold' : kind === 'stale' ? 'attention' : kind));
    h.appendChild(el('span', 'card-title', title));
    if (meta) { var m = el('span', 'card-meta', meta); m.style.marginLeft = 'auto'; h.appendChild(m); }
    c.appendChild(h);
    return c;
  }
  function textareaRow(placeholder, rows) {
    var ta = el('textarea'); ta.placeholder = placeholder; ta.rows = rows || 3; return ta;
  }
  function inlineError(card, msg) {
    var e = card.querySelector('.inline-error') || card.appendChild(el('div', 'inline-error'));
    e.textContent = msg || '';
  }

  function renderNeeds() {
    var wrap = $('needs-cards'); wrap.innerHTML = '';
    var cards = 0;
    var n = NOW || {}; var st = n.state || {}; var cs = n.cycle && n.cycle.state;

    // Escalation / question — from the current event (Phase 33 rule)
    if (cs === 'escalated' || cs === 'needs-human') {
      var ev = briefCurrent && briefCurrent.event;
      var brief = briefCurrent && briefCurrent.brief;
      var kind = cs === 'escalated' ? 'escalation' : 'question';
      var card = cardShell(kind, cs === 'escalated' ? 'Escalation — rule on ' + st.phase + '/' + st.type + ' r' + st.round
                                                    : 'Question from ' + (ev ? ev.role : 'the reviewer') + ' — ' + st.phase + '/' + st.type + ' r' + st.round,
                           ev ? fmtTs(ev.ts) : '');
      if (ev) {
        var long = (ev.content || '').length > 420 || (ev.content || '').split('\n').length > 7;
        var body = el('div', 'card-body' + (long ? ' clamp' : ''), ev.content || '');
        card.appendChild(body);
        if (long) {
          var more = el('button', 'link-btn', 'show all'); more.addEventListener('click', function () { body.classList.toggle('clamp'); more.textContent = body.classList.contains('clamp') ? 'show all' : 'show less'; });
          card.appendChild(more);
        }
      }
      // brief
      if (brief) {
        var b = el('div', 'brief');
        var bm = el('div', 'brief-meta', 'Brief #' + brief.id + ' (' + brief.kind + ' a' + brief.attempt + ', ' + brief.status + ') · ' + (brief.path || '').split('/').slice(-1)[0]);
        b.appendChild(bm);
        briefSections(brief.content).forEach(function (s) {
          if (s.title) b.appendChild(el('h4', null, s.title));
          var sec = el('div', 'sec' + (/recommendation/i.test(s.title) ? ' rec' : ''), s.text.trim());
          b.appendChild(sec);
        });
        card.appendChild(b);
      } else if (cs === 'escalated' || cs === 'needs-human') {
        var attempts = (briefCurrent && briefCurrent.attempts) || [];
        var txt = attempts.length ? ('Brief attempts: ' + attempts.map(function (a) { return 'a' + a.attempt + ' ' + a.status; }).join(', ')) : 'No brief yet for this event.';
        var hb = el('div', 'hint'); hb.textContent = txt + ' ';
        if (n.briefer_enabled) {
          var running = attempts.some(function (a) { return a.status === 'running'; });
          var gb = el('button', 'btn btn-small', running ? 'Brief running…' : 'Generate brief');
          gb.disabled = running; gb.title = 'tagteam brief --generate';
          gb.addEventListener('click', function () { act(gb, '/api/brief/generate', {}); });
          hb.appendChild(gb);
        } else {
          var code = el('code', null, 'briefer.enabled: true'); hb.appendChild(document.createTextNode('Enable the briefer in tagteam.yaml (')); hb.appendChild(code); hb.appendChild(document.createTextNode(') to get a decision brief.'));
        }
        card.appendChild(hb);
      }
      // ruling controls
      var ta = textareaRow(cs === 'escalated' ? 'Comment for the lead (required for Request changes; optional for Approve)…' : 'Your answer…', 3);
      card.appendChild(ta);
      var row = el('div', 'row');
      var safe = el('div', 'actions-safe'); var fin = el('div', 'actions-final');
      if (cs === 'escalated') {
        var rc = el('button', 'btn', 'Request changes'); rc.title = 'tagteam rule request-changes --content …';
        rc.addEventListener('click', function () {
          if (!ta.value.trim()) { inlineError(card, 'Request changes needs a comment.'); ta.focus(); return; }
          inlineError(card, '');
          act(rc, '/api/rule', { ruling: 'request-changes', content: ta.value.trim() }, { confirm: { title: 'Request changes?', body: 'The lead gets the turn back with your comment (recorded as an arbiter ruling).' }, onError: function (m) { inlineError(card, m); } });
        });
        safe.appendChild(rc);
        var ap = el('button', 'btn btn-approve', 'Approve'); ap.title = 'tagteam rule approve';
        ap.addEventListener('click', function () {
          inlineError(card, '');
          act(ap, '/api/rule', { ruling: 'approve', content: ta.value.trim() }, { confirm: { title: 'Approve and close the cycle?', body: 'This is final: the cycle is marked approved.' }, onError: function (m) { inlineError(card, m); } });
        });
        fin.appendChild(ap);
      } else {
        var sel = el('select'); ['reviewer', 'lead'].forEach(function (r) { var o = el('option', null, 'to ' + r); o.value = r; sel.appendChild(o); });
        safe.appendChild(sel);
        var an = el('button', 'btn btn-primary', 'Answer'); an.title = 'tagteam rule answer --to … --content …';
        an.addEventListener('click', function () {
          if (!ta.value.trim()) { inlineError(card, 'An answer needs content.'); ta.focus(); return; }
          inlineError(card, '');
          act(an, '/api/rule', { ruling: 'answer', content: ta.value.trim(), to: sel.value }, { confirm: { title: 'Send the answer?', body: 'Delivered as an interjection; the ' + sel.value + ' gets the turn.' }, onError: function (m) { inlineError(card, m); } });
        });
        fin.appendChild(an);
      }
      row.appendChild(safe); row.appendChild(fin); card.appendChild(row);
      wrap.appendChild(card); cards++;
    }

    // Phase 37 / UX pass: the Start card — ONE button. Running turns from
    // this page (the "headless" engine) is how the cockpit works, not a
    // choice to make here; terminal users start from the terminal
    // (`tagteam session start`). The card says exactly what Start will do.
    // It is an invitation, not an alarm: it does not count toward "needs you".
    var launchPending = n.launch && n.launch.status === 'pending';
    var startCard = false;
    var lead = (n.agents && n.agents.lead) || 'the lead';
    if (START && START.intent && !launchPending) {
      var it = START.intent;
      if (it.command) {
        var canStart = !!(START.headless && START.headless.ok);
        // compact: an invitation, one row — title · the command · Start; the
        // explanation is one small line (the card is not the page's billboard)
        var scard = cardShell('start', 'Next: ' + it.phase + ' — ' + typeWord(it.type), String(it.reason || '').replace(/no cycle in progress/, 'nothing in progress'));
        scard.classList.add('compact');
        var sbody = el('div', 'card-body');
        var cmd = el('code', 'cmd', it.command); sbody.appendChild(cmd);
        var what = el('div', 'muted small');
        if (canStart) {
          what.textContent = 'Start turns the watcher on (if it is off) and tells ' + lead + ' this. Prefer to talk first? Chat with ' + lead + ' below, then say it yourself.';
        } else {
          what.textContent = 'This page cannot run turns for both agents yet (' + ((START.headless && START.headless.errors) || []).join('; ') + '). Tell ' + lead + ' yourself, in its terminal.';
        }
        sbody.appendChild(what);
        // Phase 43: a launch that failed for THIS intent says so under the card
        if (n.launch && n.launch.status === 'failed') {
          var lf = el('div', 'inline-error', 'Last Start failed' + (n.launch.finished_at ? ' ' + fmtTs(n.launch.finished_at) : '') + ': ' + plainError(n.launch.error) + (n.launch.log_path ? '\nlog: ' + n.launch.log_path : ''));
          sbody.appendChild(lf);
        }
        scard.appendChild(sbody);
        var srow = el('div', 'row');
        var copyBtn = el('button', 'btn btn-small', 'Copy command'); copyBtn.title = 'copy the /handoff command';
        copyBtn.addEventListener('click', function () { try { navigator.clipboard.writeText(it.command); toast('ok', 'Copied.'); } catch (e) { toast('err', 'Clipboard unavailable — select the command and copy it.'); } });
        srow.appendChild(copyBtn);
        var sfin = el('div', 'actions-final');
        if (canStart) {
          var hb = el('button', 'btn btn-primary', 'Start'); hb.title = 'tagteam watch --mode headless --pidfile (if needed) + tagteam lead "' + it.command + '"';
          hb.addEventListener('click', function () {
            act(hb, '/api/start/launch', { intent: it, ensure_watcher: true }, { confirm: { title: 'Start ' + it.phase + ' — ' + typeWord(it.type) + '?', body: 'Turns the watcher on for this project (if it is off) and sends ' + lead + ' the command below as the first message of a chat. Clicking twice does not start it twice.' }, onDone: function (r) {
              if (r && r.body && (r.body.conversation_id || (r.body.existing && r.body.existing.conversation_id))) {
                LEAD.current = r.body.conversation_id || r.body.existing.conversation_id;
                try { localStorage.setItem('tagteam.cockpit.lead', LEAD.current); } catch (e) { /* ignore */ }
                loadLead(true).then(function () { try { $('lead-text').focus(); } catch (e) { /* ignore */ } });
              }
            } });
          });
          sfin.appendChild(hb);
        }
        srow.appendChild(sfin); scard.appendChild(srow);
        wrap.appendChild(scard); startCard = true;
      } else if (it.reason && /not set up/.test(it.reason)) {
        var nc = cardShell('stale', 'Not set up yet', '');
        nc.appendChild(el('div', 'hint', it.reason)); wrap.appendChild(nc); cards++;
      }
    }

    // Hold
    if (n.paused) {
      var pc = cardShell('hold', 'Turns are paused', fmtTs(n.paused.ts));
      var pb = el('div', 'card-body', (n.paused.reason || 'paused') + (n.paused.by ? '\nby ' + n.paused.by : '') + (n.paused.outcome ? '\nthe turn ' + outcomeLabel(rawOutcome(n.paused.outcome)) + ' — ' + (n.paused.phase || '') + ' ' + typeWord(n.paused.type) + ' round ' + (n.paused.round || '') : '') + (n.paused.log_path ? '\nlog: ' + n.paused.log_path : ''));
      pc.appendChild(pb);
      var pr = el('div', 'row'); var rs = el('button', 'btn btn-primary', 'Resume'); rs.title = 'tagteam resume';
      rs.addEventListener('click', function () { act(rs, '/api/resume', {}); });
      var fill = el('div', 'actions-final'); fill.appendChild(rs); pr.appendChild(fill); pc.appendChild(pr);
      wrap.appendChild(pc); cards++;
    }

    // A process that disappeared / a turn waiting with the watcher off
    if (n.inflight && n.inflight.pid_alive === false) {
      var sc = cardShell('stale', (n.inflight.agent || n.inflight.provider || 'The agent') + '\'s process disappeared mid-turn', fmtTs(n.inflight.started_at));
      sc.appendChild(el('div', 'card-body', inflightKind(n) + ' · ' + n.inflight.stem + '\nThe engine normally records this itself within a moment; if it stays, Cancel turn clears the record (nothing is killed).'));
      var sr = el('div', 'row'); var cb = el('button', 'btn btn-danger', 'Cancel turn'); cb.title = 'tagteam cancel-turn';
      cb.addEventListener('click', function () { act(cb, '/api/cancel-turn', {}, { confirm: { title: 'Clear the lost turn?', body: 'Binds the recorded pid first; a stale record is removed without signalling.', labels: { ok: 'Clear it', cancel: 'Leave it', danger: true } } }); });
      var sf = el('div', 'actions-final'); sf.appendChild(cb); sr.appendChild(sf); sc.appendChild(sr);
      wrap.appendChild(sc); cards++;
    } else if (n.owed && !n.inflight && !n.paused && !(n.watcher && n.watcher.running)) {
      // immediately, not after two minutes: with the watcher off nobody will run this turn
      var who = n.owed.agent || n.owed.role;
      var wc = cardShell('stale', 'Waiting on ' + who + ', but the watcher is off', 'waiting ' + fmtAge(n.owed.age_s));
      wc.appendChild(el('div', 'card-body', 'Nothing runs ' + who + '\'s turn until the watcher is on. Start it here, or run /handoff in ' + who + '\'s own terminal.'));
      var wr2 = el('div', 'row'); var wfin = el('div', 'actions-final');
      var wstart = el('button', 'btn btn-primary', 'Start the watcher');
      var wmode = (START && START.headless && START.headless.ok) ? 'headless' : 'notify';
      wstart.title = 'tagteam watch --mode ' + wmode + ' --pidfile';
      wstart.addEventListener('click', function () { act(wstart, '/api/watch/start', { mode: wmode }, { confirm: { title: 'Start the watcher?', body: watcherStartBody(wmode) } }); });
      wfin.appendChild(wstart); wr2.appendChild(wfin); wc.appendChild(wr2);
      wrap.appendChild(wc); cards++;
    }

    // First-run states
    if (!n.agents || !n.agents.lead) {
      var fc = cardShell('stale', 'No tagteam.yaml yet', '');
      var fb = el('div', 'hint'); fb.innerHTML = BASE ? 'Run <code>tagteam init</code> in the project (or <code>tagteam serve</code> there and use the Saloon setup).'
        : 'Set up the two agents in the <a href="/?theme=saloon">Saloon</a> (the Mayor walks you through it) or run <code>tagteam init</code>.';
      fc.appendChild(fb); wrap.appendChild(fc); cards++;
    }

    // the badge counts what NEEDS you; the Start card is an invitation
    $('needs-count').textContent = cards ? String(cards) : '';
    $('needs-count').classList.toggle('hot', cards > 0);
    $('needs-you').classList.toggle('hot', cards > 0 && (cs === 'escalated' || cs === 'needs-human'));
    $('needs-you').classList.toggle('quiet', !cards && !startCard);
    $('needs-empty').classList.toggle('hidden', cards > 0 || startCard);
    if (!cards && !startCard) {
      var eb = $('needs-empty-body');
      var who = n.owed && (n.owed.agent || n.owed.role);
      if (launchPending) eb.textContent = 'Starting ' + (n.launch.phase || '') + ' — ' + typeWord(n.launch.type) + ': ' + lead + ' is on it (' + fmtAge(n.launch.age_s) + ').';
      else if (n.inflight) eb.textContent = (n.inflight.agent || n.inflight.role) + ' is working — ' + inflightKind(n) + ' (' + fmtAge(n.inflight.age_s) + ').';
      else if (who) eb.textContent = 'Waiting on ' + who + ' — ' + st.phase + ', ' + typeWord(st.type) + ', round ' + st.round + ' (' + fmtAge(n.owed.age_s) + ').';
      else if (!st.phase) eb.textContent = (START && START.intent && START.intent.reason) ? START.intent.reason : 'No phase in progress.';
      else if (st.status === 'done') eb.textContent = st.phase + ' (' + typeWord(st.type) + ') is ' + cycleWord(st.result || 'done') + '.';
      else eb.textContent = 'Nothing is waiting on anyone right now.';
    }
  }

  // ---------- Tabs ----------
  function showTab(name) {
    document.querySelectorAll('.tab').forEach(function (t) { t.classList.toggle('active', t.dataset.tab === name); });
    document.querySelectorAll('.panel').forEach(function (p) { p.classList.toggle('active', p.id === 'panel-' + name); });
    if (name === 'diff' && !diffLoaded) loadDiff();
    if (name === 'usage' && !usageLoaded) loadUsage();
    if (name === 'notes' && !notesLoaded) loadNotes();
    try { localStorage.setItem('tagteam.cockpit.tab', name); } catch (e) { /* ignore */ }
  }
  document.querySelectorAll('.tab').forEach(function (t) { t.addEventListener('click', function () { showTab(t.dataset.tab); }); });
  function activeTab() { var t = document.querySelector('.tab.active'); return t ? t.dataset.tab : 'feed'; }

  // ---------- Feed ----------
  function loadFeed() {
    if (!CYCLE_ID) { $('feed-list').innerHTML = ''; $('feed-empty').classList.remove('hidden'); $('feed-empty').textContent = 'No phase in progress.'; return Promise.resolve(); }
    var st = NOW.state;
    var requested = CYCLE_ID;
    return Promise.all([getJSON('/api/rounds/' + encodeURIComponent(CYCLE_ID)), getJSON('/api/briefs?phase=' + encodeURIComponent(st.phase) + '&type=' + encodeURIComponent(st.type))]).then(function (rs) {
      var rounds = (rs[0].body && rs[0].body.rounds) || [];
      var briefs = (rs[1].body && rs[1].body.briefs) || [];
      // Phase 45/58: the verdict of each round for the reviewer lane's cards — only for the cycle this response was for
      applyRounds(requested, rounds);
      var items = [];
      rounds.forEach(function (r) {
        (r.entries || []).forEach(function (e, i) {
          var isRuling = /^\[ARBITER RULING by /.test(e.content || '');
          // Phase 38: gate entries — kind 'gate' (pass = neutral, bounce = warm).
          var isGate = e.role === 'gatekeeper' || e.action === 'GATE_PASS' || e.action === 'GATE_BOUNCE';
          var kind = isRuling ? 'ruling' : (isGate ? 'gate' + (e.action === 'GATE_BOUNCE' ? ' bounce' : ' pass') : e.role);
          items.push({ id: 'r' + r.round + '-' + i + '-' + e.ts, kind: kind, role: isRuling ? 'arbiter' : (isGate ? 'gatekeeper' : e.role), action: e.action, ts: e.ts, round: r.round, text: e.content || '', by: e.updated_by });
        });
        (r.rulings || []).forEach(function (e, i) {
          if (!(r.entries || []).some(function (x) { return x.ts === e.ts; })) items.push({ id: 'ru' + r.round + '-' + i, kind: 'ruling', role: 'arbiter', action: e.action, ts: e.ts, round: r.round, text: e.content || '' });
        });
        (r.interjections || []).forEach(function (n) {
          items.push({ id: 'i' + n.id, kind: 'interjection', role: 'note #' + n.id, action: n.retired_ts ? 'retired' : (n.delivered_role ? 'delivered → ' + n.delivered_role + ' r' + n.delivered_round : 'queued'), ts: n.ts, round: r.round, text: n.note, by: n.by });
        });
        if (!r.entries || !r.entries.length) {
          if (r.lead_text) items.push({ id: 'l' + r.round, kind: 'lead', role: 'lead', action: r.lead_action || 'SUBMIT_FOR_REVIEW', ts: '', round: r.round, text: r.lead_text });
          if (r.reviewer_text) items.push({ id: 'v' + r.round, kind: 'reviewer', role: 'reviewer', action: r.action || '', ts: '', round: r.round, text: r.reviewer_text });
        }
      });
      briefs.forEach(function (b) {
        items.push({ id: 'b' + b.id, kind: 'brief', role: 'brief #' + b.id, action: b.kind + ' a' + b.attempt + ' ' + b.status, ts: b.finished_at || b.ts, round: b.round, text: (b.reason || '') + (b.path ? '\n' + b.path : ''), by: b.provider });
      });
      items.sort(function (a, b) { return (b.ts || '').localeCompare(a.ts || '') || (b.round - a.round); });
      var key = items.map(function (i) { return i.id + '|' + i.action; }).join(';');
      $('feed-meta').textContent = rounds.length + ' round' + (rounds.length === 1 ? '' : 's') + ' · ' + items.length + ' entries';
      $('feed-title').textContent = 'Rounds — ' + st.phase + ', ' + typeWord(st.type) + ' — what each side said, newest first';
      if (key === lastRoundsKey) return;
      lastRoundsKey = key;
      var list = $('feed-list'); list.innerHTML = '';
      $('feed-empty').classList.toggle('hidden', items.length > 0);
      items.forEach(function (it) {
        var d = el('div', 'feed-item ' + it.kind);
        d.appendChild(el('span', 'dot'));
        var body = el('div');
        var line = el('div', 'feed-line');
        line.appendChild(el('span', 'tag role', it.role));
        if (it.action) line.appendChild(el('span', 'tag action', it.action));
        line.appendChild(el('span', 'tag', 'r' + it.round));
        if (it.by && it.by !== it.role) line.appendChild(el('span', 'muted', it.by));
        line.appendChild(el('span', 'ts', fmtTs(it.ts)));
        body.appendChild(line);
        var summary = el('div', 'feed-summary', firstLine(it.text, 200));
        body.appendChild(summary);
        var full = String(it.text || '').trim();
        if (full.length > firstLine(full, 200).length) {
          var more = el('button', 'link-btn', expandedFeed[it.id] ? 'less' : 'more');
          var fullEl = el('div', 'feed-full', full);
          if (!expandedFeed[it.id]) fullEl.classList.add('hidden');
          more.addEventListener('click', function () { expandedFeed[it.id] = !expandedFeed[it.id]; fullEl.classList.toggle('hidden', !expandedFeed[it.id]); more.textContent = expandedFeed[it.id] ? 'less' : 'more'; });
          body.appendChild(more); body.appendChild(fullEl);
        }
        d.appendChild(body); list.appendChild(d);
      });
    });
  }

  // ---------- Diff ----------
  function colorize(patch) {
    return String(patch || '').split('\n').map(function (l) {
      var cls = l.startsWith('+++') || l.startsWith('---') || l.startsWith('diff ') || l.startsWith('index ') ? 'l-meta'
        : l.startsWith('@@') ? 'l-hunk' : l.startsWith('+') ? 'l-add' : l.startsWith('-') ? 'l-del' : '';
      return cls ? '<span class="' + cls + '">' + esc(l) + '</span>' : esc(l);
    }).join('\n');
  }
  function loadDiff() {
    if (!CYCLE_ID) { $('diff-list').innerHTML = ''; $('diff-empty').classList.remove('hidden'); return Promise.resolve(); }
    $('diff-title').textContent = 'Loading scope diff…';
    return getJSON('/api/scope-diff/' + encodeURIComponent(CYCLE_ID)).then(function (r) {
      var b = r.body || {}; diffLoaded = true;
      var list = $('diff-list'); list.innerHTML = '';
      var banner = $('diff-banner');
      if (b.error) { banner.textContent = b.error; banner.classList.remove('hidden'); $('diff-title').textContent = 'Scope diff'; return; }
      $('diff-title').textContent = 'Scope diff — ' + (b.paths || []).length + ' path' + ((b.paths || []).length === 1 ? '' : 's') + ' since baseline ' + (b.diff_base || '').slice(0, 10);
      if (b.truncated) { banner.textContent = 'Diff capped: ' + (b.omitted_files ? b.omitted_files + ' file(s) omitted; ' : '') + 'patch text limited to ' + Math.round((b.max_bytes || 0) / 1024) + ' KB — see per-file markers, or run git diff locally.'; banner.classList.remove('hidden'); }
      else banner.classList.add('hidden');
      $('diff-empty').classList.toggle('hidden', (b.files || []).length > 0);
      (b.files || []).forEach(function (f) {
        var box = el('div', 'file');
        var head = el('div', 'file-head');
        head.appendChild(el('span', 'status', f.status + (f.binary ? ' · binary' : '')));
        head.appendChild(el('span', 'path', f.path));
        if (typeof f.additions === 'number') head.appendChild(el('span', 'adds', '+' + f.additions));
        if (typeof f.deletions === 'number') head.appendChild(el('span', 'dels', '−' + f.deletions));
        box.appendChild(head);
        var pre = el('pre'); pre.innerHTML = f.binary ? '<span class="l-meta">(binary file — not diffed)</span>' : (f.patch ? colorize(f.patch) : '<span class="l-meta">(no textual diff)</span>');
        if (!expandedFiles[f.path]) pre.classList.add('hidden');
        head.addEventListener('click', function () { expandedFiles[f.path] = !expandedFiles[f.path]; pre.classList.toggle('hidden', !expandedFiles[f.path]); });
        box.appendChild(pre);
        if (f.truncated) box.appendChild(el('div', 'trunc', 'patch truncated at the size cap'));
        list.appendChild(box);
      });
    });
  }
  $('btn-diff-refresh').addEventListener('click', function () { loadDiff(); });
  $('btn-diff-expand').addEventListener('click', function () {
    var anyHidden = Array.prototype.some.call(document.querySelectorAll('#diff-list .file pre'), function (p) { return p.classList.contains('hidden'); });
    document.querySelectorAll('#diff-list .file').forEach(function (box) {
      var path = box.querySelector('.path').textContent; expandedFiles[path] = anyHidden; box.querySelector('pre').classList.toggle('hidden', !anyHidden);
    });
    this.textContent = anyHidden ? 'Collapse all' : 'Expand all';
  });

  // ---------- Usage ----------
  function bucketTable(buckets) {
    var keys = Object.keys(buckets || {});
    if (!keys.length) return '<div class="hint" style="padding:8px 10px">no rows</div>';
    var h = '<table class="u"><tr><th>bucket</th><th>turns</th><th>ok</th><th>failed</th><th>in</th><th>out</th><th>cache r</th><th>cache w</th><th>mean</th></tr>';
    keys.forEach(function (k) {
      var b = buckets[k];
      h += '<tr><td>' + esc(k) + '</td><td>' + b.turns + '</td><td>' + b.ok + '</td><td>' + b.failed + '</td><td>' + fmtInt(b.input_tokens) + '</td><td>' + fmtInt(b.output_tokens) + '</td><td>' + fmtInt(b.cache_read_tokens) + '</td><td>' + fmtInt(b.cache_write_tokens) + '</td><td>' + (b.mean_duration_ms != null ? Math.round(b.mean_duration_ms / 1000) + 's' : '-') + '</td></tr>';
    });
    return h + '</table>';
  }
  function drawChurn(series) {
    var svg = $('churn'); svg.innerHTML = '';
    var W = 640, H = 220, padL = 46, padR = 12, padT = 10, padB = 24;
    var pts = (series || []).filter(function (s) { return typeof s.round === 'number'; });
    var byRole = {};
    pts.forEach(function (s) {
      var tot = (s.input || 0) + (s.output || 0);
      (byRole[s.role] = byRole[s.role] || []).push({ x: s.round, y: tot, s: s });
    });
    var maxR = Math.max(5, Math.max.apply(null, pts.map(function (p) { return p.round; }).concat([1])));
    var maxY = Math.max(1, Math.max.apply(null, pts.map(function (p) { return (p.input || 0) + (p.output || 0); }).concat([1])));
    var X = function (r) { return padL + (r - 1) / Math.max(1, maxR - 1) * (W - padL - padR); };
    var Y = function (v) { return H - padB - v / maxY * (H - padT - padB); };
    var ns = 'http://www.w3.org/2000/svg';
    function line(x1, y1, x2, y2, stroke, dash, w) { var l = document.createElementNS(ns, 'line'); l.setAttribute('x1', x1); l.setAttribute('y1', y1); l.setAttribute('x2', x2); l.setAttribute('y2', y2); l.setAttribute('stroke', stroke); l.setAttribute('stroke-width', w || 1); if (dash) l.setAttribute('stroke-dasharray', dash); svg.appendChild(l); }
    function text(x, y, t, anchor, fill) { var e = document.createElementNS(ns, 'text'); e.setAttribute('x', x); e.setAttribute('y', y); e.setAttribute('font-size', '10'); e.setAttribute('fill', fill || '#9aa4b1'); e.setAttribute('text-anchor', anchor || 'start'); e.setAttribute('font-family', 'ui-monospace, Menlo, monospace'); e.textContent = t; svg.appendChild(e); }
    line(padL, H - padB, W - padR, H - padB, '#2b333d'); line(padL, padT, padL, H - padB, '#2b333d');
    for (var r = 1; r <= maxR; r++) { if (maxR <= 12 || r % 2 === 1) text(X(r), H - 8, 'r' + r, 'middle'); }
    text(padL - 4, padT + 8, fmtInt(maxY), 'end'); text(padL - 4, H - padB, '0', 'end');
    // No threshold marker: auto-escalation is not a round number (it fires
    // after 10 consecutive stale rounds — unchanged re-submissions — whatever
    // the round), so nothing is drawn at a fixed x; the rule is stated in the
    // chart caption instead.
    var colors = { lead: '#5aa9ff', reviewer: '#c58af9', briefer: '#d29922' };
    Object.keys(byRole).forEach(function (role) {
      var arr = byRole[role].sort(function (a, b) { return a.x - b.x || 0; });
      var d = ''; arr.forEach(function (p, i) { d += (i ? 'L' : 'M') + X(p.x).toFixed(1) + ',' + Y(p.y).toFixed(1) + ' '; });
      var path = document.createElementNS(ns, 'path'); path.setAttribute('d', d); path.setAttribute('fill', 'none'); path.setAttribute('stroke', colors[role] || '#9aa4b1'); path.setAttribute('stroke-width', '2'); svg.appendChild(path);
      arr.forEach(function (p) { var c = document.createElementNS(ns, 'circle'); c.setAttribute('cx', X(p.x)); c.setAttribute('cy', Y(p.y)); c.setAttribute('r', p.s.status === 'ok' ? 3 : 4); c.setAttribute('fill', p.s.status === 'ok' ? (colors[role] || '#9aa4b1') : '#f85149'); var t = document.createElementNS(ns, 'title'); t.textContent = role + ' r' + p.x + ': ' + fmtInt(p.y) + ' tokens (' + p.s.status + ')'; c.appendChild(t); svg.appendChild(c); });
    });
    if (!pts.length) text(W / 2, H / 2, 'no turns yet', 'middle');
  }
  function loadUsage() {
    var st = (NOW && NOW.state) || {};
    var q = st.phase ? '?phase=' + encodeURIComponent(st.phase) + '&type=' + encodeURIComponent(st.type) : '';
    return Promise.all([getJSON('/api/usage' + q), getJSON('/api/usage')]).then(function (rs) {
      usageLoaded = true;
      var cur = rs[0].body || {}, all = rs[1].body || {};
      var lims = all.rate_limits || [];
      var rl = $('rate-line');
      if (!lims.length) rl.innerHTML = '<span class="k">Subscription window:</span> n/a — no rate-limit signal recorded yet (Claude headless turns report it; Codex has no equivalent).';
      else rl.innerHTML = lims.map(function (l) {
        var when = l.resets_at ? new Date(l.resets_at) : null;
        var resets = when && !isNaN(when.getTime()) ? when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) + (l.resets_in_s != null ? ' (in ' + fmtAge(l.resets_in_s) + ')' : '') : '?';
        return '<span class="k">' + esc(l.provider) + ' ' + esc(String(l.kind).replace('_', ' ')) + ' window:</span> <b>' + esc(l.status || '?') + '</b>, resets ' + esc(resets) + ' <span class="muted">· seen ' + esc(fmtTs(l.ts)) + '</span>';
      }).join('<br>');
      drawChurn(cur.series || []);
      $('usage-role').innerHTML = bucketTable(all.by_role);
      $('usage-cycle').innerHTML = bucketTable(all.by_cycle);
      $('usage-agent').innerHTML = bucketTable(all.by_agent);
      $('usage-empty').classList.toggle('hidden', !!(all.totals && all.totals.turns));
    });
  }
  $('btn-usage-refresh').addEventListener('click', function () { loadUsage(); });

  // ---------- Notes ----------
  function loadNotes() {
    var st = (NOW && NOW.state) || {};
    var q = st.phase ? '?phase=' + encodeURIComponent(st.phase) + '&type=' + encodeURIComponent(st.type) : '';
    return getJSON('/api/interjections' + q).then(function (r) {
      notesLoaded = true;
      var rows = (r.body && r.body.interjections) || [];
      var list = $('notes-list'); list.innerHTML = '';
      $('notes-empty').classList.toggle('hidden', rows.length > 0);
      rows.slice().reverse().forEach(function (n) {
        var d = el('div', 'note ' + n.status);
        var left = el('div');
        var meta = el('div', 'meta');
        meta.appendChild(el('span', 'st', n.status === 'delivered' ? 'delivered → ' + n.delivered_role + ' r' + n.delivered_round : n.status));
        meta.appendChild(document.createTextNode(' #' + n.id + ' · ' + (n.by || '') + ' → ' + (n.target_role || 'next turn') + ' · ' + (n.phase ? n.phase + '/' + n.type + ' r' + n.round : 'next cycle') + ' · ' + fmtTs(n.ts)));
        left.appendChild(meta); left.appendChild(el('div', 'text', n.note));
        d.appendChild(left);
        var right = el('div');
        if (n.status === 'pending') {
          var rb = el('button', 'btn btn-small', 'Retire'); rb.title = 'tagteam interject --retire ' + n.id;
          rb.addEventListener('click', function () { act(rb, '/api/interject/retire', { id: n.id }); });
          right.appendChild(rb);
        }
        d.appendChild(right); list.appendChild(d);
      });
    });
  }
  $('note-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var ta = $('note-text'); var note = ta.value.trim();
    if (!note) { ta.focus(); return; }
    act($('btn-interject'), '/api/interject', { note: note, to: $('note-to').value }, { onDone: function (r) { if (r && r.ok) ta.value = ''; } });
  });

  // ---------- Refresh orchestration ----------
  var refreshing = false, refreshQueued = false;
  function refreshAll(reason) {
    if (refreshing) { refreshQueued = true; return; }
    refreshing = true;
    getJSON('/api/now').then(function (r) {
      if (!r.ok) throw new Error('now ' + r.status);
      var n = r.body;
      var st = n.state || {}; var cs = n.cycle && n.cycle.state;
      var p = Promise.resolve();
      if ((cs === 'escalated' || cs === 'needs-human') && st.phase) {
        p = getJSON('/api/brief/current?phase=' + encodeURIComponent(st.phase) + '&type=' + encodeURIComponent(st.type)).then(function (b) { briefCurrent = b.body; });
      } else briefCurrent = null;
      return p.then(function () { return getJSON('/api/start').then(function (sr) { START = sr.ok ? sr.body : null; }).catch(function () { START = null; }); }).then(function () {
        renderNow(n);
        var t = activeTab();
        // Phase 45: the lead lane is always visible — its chat refreshes on every pass
        var ps = [loadFeed(), loadActivity(), loadLead(false)];
        if (t === 'notes' || notesLoaded) ps.push(loadNotes());
        if (t === 'usage' && usageLoaded) ps.push(loadUsage());
        if (t === 'diff' && diffLoaded && reason !== 'tick') ps.push(loadDiff());
        return Promise.all(ps);
      });
    }).catch(function (e) {
      console.error('[cockpit] refresh failed', e);
      if (connMode === 'live') setConn('disconnected');
    }).then(function () {
      refreshing = false;
      if (refreshQueued) { refreshQueued = false; refreshAll('queued'); }
    });
  }

  // ---------- Phase 43: Cycle region + Activity log (text nodes only — the page holds the POST token) ----------
  // One outcome vocabulary for the strip, the lanes, the rows and the cards —
  // in the arbiter's words (the keys are the API's; the values are on screen).
  var OUTCOME_LABEL = { running: 'working', finished: 'done', cancelled: 'cancelled', failed: 'failed', timed_out: 'timed out', process_gone: 'process disappeared', orphaned: 'no result recorded' };
  var OUTCOME_BAD = { cancelled: true, failed: true, timed_out: true, process_gone: true, orphaned: true };
  function outcomeLabel(s) { return OUTCOME_LABEL[s] || String(s || '?'); }
  function rawOutcome(raw) {   // engine outcome strings (pause marker, errors) → vocabulary key
    return ({ ok: 'finished', timeout: 'timed_out', nonzero_exit: 'failed', no_round: 'failed', spawn_failed: 'failed', cancelled: 'cancelled', running: 'running' })[raw] || 'failed';
  }
  function isRunning(it) { return it && (it.status === 'running' || it.status === 'process_gone'); }
  // the words for the things that run: turn · chat · pre-check · review lens · brief
  function typeWord(t) { return t === 'impl' ? 'implementation' : t === 'plan' ? 'plan' : String(t || ''); }
  function cycleWord(s) { return ({ 'in-progress': 'in progress', 'needs-human': 'question for you', escalated: 'escalated to you', approved: 'approved', aborted: 'aborted' })[s] || String(s || ''); }
  function roleWord(role) { return role === 'lead' ? 'lead' : role === 'reviewer' ? 'reviewer' : role === 'gatekeeper' ? 'pre-check' : String(role || ''); }
  function agentName(it) {   // the agent's name for a row/turn, falling back to the configured name for its role
    if (it.agent && it.agent !== 'gate' && it.agent !== 'panel' && it.agent !== 'briefer') return it.agent;
    var a = (NOW && NOW.agents) || {};
    return it.role === 'lead' ? (a.lead || '') : it.role === 'reviewer' ? (a.reviewer || '') : '';
  }
  function kindLabel(it) {
    if (!it) return '';
    var r = (it.round != null) ? ' · round ' + it.round : '';
    switch (it.kind) {
      case 'cycle': return (it.type ? typeWord(it.type) + ' ' : '') + (it.role === 'reviewer' ? 'review' : 'turn') + r;
      case 'conversation': return 'chat' + (it.ref && it.ref.turn != null ? ' #' + it.ref.turn : '');
      case 'gate': return 'pre-check' + r;
      case 'panel': return 'review panel' + r;
      case 'panel_lens': return 'review lens' + (it.detail ? ' (' + it.detail + ')' : '') + r;
      case 'briefer': return 'decision brief' + r;
      case 'launch': return 'start';
      default: return (it.kind || 'turn') + r;
    }
  }
  function inflightKind(n) {
    var k = (n && n.turn_kind) || 'cycle'; var inf = (n && n.inflight) || {};
    if (k === 'cycle') return (inf.role === 'reviewer' ? 'review' : 'turn') + (inf.round != null ? ', round ' + inf.round : '');
    if (k === 'conversation') return 'chat';
    if (k === 'panel') return 'review lens';
    if (k === 'briefer') return 'decision brief';
    if (k === 'gate') return 'pre-check';
    return k;
  }
  function agoText(ts) {
    if (!ts) return '';
    var d = new Date(ts); if (isNaN(d.getTime())) return '';
    return fmtAge(Math.round((Date.now() - d.getTime()) / 1000)) + ' ago';
  }
  function lastTurnText(lt) {
    var machine = lt.kind === 'gate' || lt.kind === 'panel' || lt.kind === 'briefer' || lt.kind === 'launch';
    var who = machine ? '' : (agentName(lt) || roleWord(lt.role) || '');
    return 'last: ' + (who ? who + '\'s ' : '') + kindLabel(lt) + ' — ' + outcomeLabel(lt.status) + (lt.ended_at ? ', ' + agoText(lt.ended_at) : '');
  }
  // engine error strings that reach the page → what happened, in plain words
  function plainError(e) {
    var s = String(e || 'unknown');
    s = s.replace(/\bby web:([\w.@-]+)/, 'by you (web:$1)');
    if (/slot busy/i.test(s)) return 'the lead was already working on something else at that moment — try again now';
    if (/orphaned/i.test(s)) return 'the process that started it went away before it finished';
    if (/spawn|not found|No such file/i.test(s)) return 'the agent\'s command could not be started (' + s + ')';
    return s;
  }

  // Shared EventSource registry: one connection per resource, any number
  // of named consumers (the Lead panel and an Activity row both read a
  // conversation stream; the running Activity row reads a turn-log stream).
  var STREAMS = {};   // key -> {es, path, handlers: {type: {name: fn}}, buffer: [{type, data}]}
  var STREAM_BUFFER_MAX = 5000;
  function stream(key, path) {
    var s = STREAMS[key];
    if (s) return s;
    if (!window.EventSource) return null;
    var es; try { es = new EventSource(url(path)); } catch (e) { return null; }
    s = STREAMS[key] = { es: es, path: path, handlers: {}, buffer: [] };
    ['line', 'end'].forEach(function (t) {
      es.addEventListener(t, function (ev) {
        var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
        // buffered so a consumer that joins after the replay still gets it
        s.buffer.push({ type: t, data: d }); if (s.buffer.length > STREAM_BUFFER_MAX) s.buffer.shift();
        var hs = s.handlers[t] || {};
        Object.keys(hs).forEach(function (name) { try { hs[name](d); } catch (e2) { console.error('[cockpit] stream handler', name, e2); } });
      });
    });
    es.addEventListener('error', function () { /* EventSource reconnects with Last-Event-ID */ });
    return s;
  }
  function onStream(key, path, name, handlers) {
    var s = stream(key, path); if (!s) return null;
    var fresh = Object.keys(handlers).filter(function (t) { return !(s.handlers[t] && s.handlers[t][name]); });
    Object.keys(handlers).forEach(function (t) { (s.handlers[t] = s.handlers[t] || {})[name] = handlers[t]; });
    // late joiner: replay what this stream already delivered
    s.buffer.forEach(function (b) { if (fresh.indexOf(b.type) >= 0) { try { handlers[b.type](b.data); } catch (e) { /* ignore */ } } });
    return s;
  }
  function offStream(key, name) {
    var s = STREAMS[key]; if (!s) return;
    var left = 0;
    Object.keys(s.handlers).forEach(function (t) { delete s.handlers[t][name]; left += Object.keys(s.handlers[t]).length; });
    if (!left) { try { s.es.close(); } catch (e) { /* ignore */ } delete STREAMS[key]; }
  }
  function closeStream(key) { var s = STREAMS[key]; if (!s) return; try { s.es.close(); } catch (e) { /* ignore */ } delete STREAMS[key]; }

  // ---- Phase 45: the lanes — headers, state, token (the arbiter's words) ----
  function renderLanes(n) {
    var st = n.state || {};
    var launch = n.launch || null; var pending = !!(launch && launch.status === 'pending');
    var lead = (n.agents && n.agents.lead) || 'lead', rev = (n.agents && n.agents.reviewer) || 'reviewer';
    $('lane-lead-name').textContent = lead + ' — lead';
    $('lane-reviewer-name').textContent = rev + ' — reviewer';
    var owedRole = n.owed && n.owed.role; var inf = n.inflight || null; var infRole = inf && inf.role;
    var cs = n.cycle && n.cycle.state;
    function laneText(role) {
      var running = inf && infRole === role;
      var onTurn = owedRole === role;
      if (running) return (inf.pid_alive === false ? OUTCOME_LABEL.process_gone : 'working · ' + inflightKind(n)) + ' · ' + fmtAge(inf.age_s);
      if (onTurn) return 'its turn · ' + ((n.watcher && n.watcher.running) ? 'the watcher will start it' : 'the watcher is off — nothing will start it') + ' · waiting ' + fmtAge(n.owed.age_s);
      if (pending && role === 'lead') return 'starting · ' + fmtAge(launch.age_s);
      if (cs === 'escalated' || cs === 'needs-human') return 'waiting on you';
      if (st.status === 'done' && st.phase) return (st.phase + ' — ' + typeWord(st.type) + ' ' + cycleWord(st.result || 'done'));
      return 'waiting';
    }
    ['lead', 'reviewer'].forEach(function (role) {
      var lane = $('lane-' + role);
      var running = inf && infRole === role;
      lane.classList.toggle('on-turn', owedRole === role || (pending && role === 'lead'));
      lane.classList.toggle('running', !!running && inf.pid_alive !== false);
      lane.classList.toggle('gone', !!running && inf.pid_alive === false);
      $('lane-' + role + '-state').textContent = laneText(role);
    });
    var tok = $('lane-token');
    var side = owedRole || (pending ? 'lead' : ((cs === 'escalated' || cs === 'needs-human') ? 'you' : 'none'));
    tok.className = 'token ' + side;
    tok.title = side === 'you' ? 'waiting on you (arbiter)' : side === 'none' ? 'nobody\'s turn right now' : 'waiting on the ' + side;
  }
  // Fitts / hierarchy: the chat's composer and both streams share the first
  // viewport — the lanes block takes the space below the Needs-you banner
  // (min 360px) and the timelines scroll inside their lane; the tabs sit
  // below the fold. Recomputed on resize and after every render (the banner
  // changes height).
  function fitLanes() {
    var lanes = $('lanes'); if (!lanes) return;
    if (window.innerWidth <= 1000) { lanes.style.height = ''; return; }   // stacked: natural height
    var top = lanes.getBoundingClientRect().top + (window.scrollY || 0);
    var h = (window.innerHeight || 800) - top - 14;
    lanes.style.height = Math.max(360, Math.round(h)) + 'px';
  }
  function renderLaneAges() {
    if (!NOW) return;
    renderLanes(NOW);
    [ACT, RLANE, LLANE].forEach(function (store) {
      Object.keys(store.rows).forEach(function (id) {
        var rec = store.rows[id];
        if (isRunning(rec.item) && rec.item.age_s != null) { rec.item.age_s += 1; setActStatus(rec); }
      });
    });
    if (LEAD.sending) renderLeadGate();
  }

  // ---- keyed row stores: rows keyed by item id, patched in place, never wiped ----
  // ACT   — the flat "all activity" list on the Rounds tab (Phase 43): running first, then newest first
  // RLANE — the reviewer lane: the reviewer's turns / pre-checks / lenses, ascending in time (newest at the foot)
  // LLANE — the lead lane's cycle-turn cards, ascending, merged in time with the chat messages
  function makeStore(name, containerId, ascending) { return { name: name, containerId: containerId, ascending: ascending, rows: {}, count: 0 }; }
  var ACT = makeStore('act', 'activity', false);
  var RLANE = makeStore('rlane', 'reviewer-timeline', true);
  var LLANE = makeStore('llane', 'lead-timeline', true);
  var VERDICT_WORD = { APPROVE: 'approved', REQUEST_CHANGES: 'changes requested', ESCALATE: 'escalated', NEED_HUMAN: 'question for you', GATE_PASS: 'passed', GATE_BOUNCE: 'bounced' };
  // Phase 58: the verdict cache belongs to one cycle: {cycle, byN: round -> {reviewer, gate}} from /api/rounds.
  var ROUNDS = { cycle: null, byN: {} };
  var LANE_CYCLE = null;          // the cycle the reviewer lane is scoped to
  var SHOW_LAST_SESSION = false;  // reveal rows from other cycles ("Show last session")
  function cycleKey(it) { return (it && it.phase && it.type) ? it.phase + '_' + it.type : null; }
  function cycleLabel(it) { return (it && it.phase) ? it.phase + ' · ' + (it.type || '') : 'another cycle'; }
  function setVerdictCycle(cycle) {
    if (cycle === ROUNDS.cycle) return;
    ROUNDS = { cycle: cycle, byN: {} };
    refreshVerdicts();
  }
  function applyRounds(requested, rounds) {
    // a response for a cycle that is no longer current (or not yet adopted) is dropped
    if (!requested || requested !== CYCLE_ID || requested !== ROUNDS.cycle) return false;
    var byN = {};
    (rounds || []).forEach(function (r) {
      var rec = byN[r.round] = {};
      (r.entries || []).forEach(function (e) {
        if (e.role === 'reviewer' && e.action && e.action !== 'AMEND') rec.reviewer = { action: e.action, text: e.content || '' };
        if (e.role === 'gatekeeper' || e.action === 'GATE_PASS' || e.action === 'GATE_BOUNCE') rec.gate = { action: e.action, text: e.content || '' };
      });
      if (!rec.reviewer && r.action && r.reviewer_text) rec.reviewer = { action: r.action, text: r.reviewer_text };
    });
    ROUNDS = { cycle: requested, byN: byN };
    refreshVerdicts();
    return true;
  }
  function setLaneCycle(cycle) {
    if (cycle === LANE_CYCLE) return;
    LANE_CYCLE = cycle; SHOW_LAST_SESSION = false;
    renderReviewerLaneScope();
  }
  function applyLaneScope(rec) {
    var other = cycleKey(rec.item) !== LANE_CYCLE;
    rec.row.classList.toggle('other-cycle', other);
    rec.row.classList.toggle('hidden', other && !SHOW_LAST_SESSION);
    if (rec.cycleEl) { rec.cycleEl.textContent = other ? cycleLabel(rec.item) : ''; rec.cycleEl.classList.toggle('hidden', !other); }
    return other;
  }
  function renderReviewerLaneScope() {
    var other = 0, current = 0;
    Object.keys(RLANE.rows).forEach(function (id) { if (applyLaneScope(RLANE.rows[id])) other++; else current++; });
    var btn = $('btn-reviewer-last');
    btn.classList.toggle('hidden', !other);
    btn.textContent = SHOW_LAST_SESSION ? 'Hide last session' : 'Show last session';
    btn.setAttribute && btn.setAttribute('aria-pressed', SHOW_LAST_SESSION ? 'true' : 'false');
    var empty = $('reviewer-empty');
    var parts = LANE_CYCLE ? LANE_CYCLE.match(/^(.*)_(plan|impl)$/) : null;
    empty.textContent = parts ? 'No reviews yet for ' + parts[1] + ' · ' + parts[2] + '.' : 'No reviews yet.';
    empty.classList.toggle('hidden', current > 0 || (SHOW_LAST_SESSION && other > 0));
    return { current: current, other: other };
  }
  function toggleLastSession() { SHOW_LAST_SESSION = !SHOW_LAST_SESSION; return renderReviewerLaneScope(); }
  function inReviewerLane(it) { return (it.role === 'reviewer' || it.role === 'gatekeeper') && (it.kind === 'cycle' || it.kind === 'gate' || it.kind === 'panel' || it.kind === 'panel_lens'); }
  function inLeadLane(it) { return it.role === 'lead' && it.kind === 'cycle'; }
  function actSortKey(it) { return (isRunning(it) ? '1' : '0') + '|' + String(it.started_at || '') + '|' + String(it.id || ''); }
  function ascSortKey(ts, id) { return String(ts || '') + '|' + String(id || ''); }
  function storeSortKey(store, it) { return store.ascending ? ascSortKey(it.started_at, it.id) : actSortKey(it); }
  function loadActivity() {
    return getJSON('/api/activity').then(function (r) {
      if (!r.ok) return;
      var b = r.body || {}; var items = b.items || [];
      var seen = {};
      ACT.count = items.length;
      items.forEach(function (it) {
        seen[it.id] = true;
        upsertRow(ACT, it);
        if (inReviewerLane(it)) upsertRow(RLANE, it);
        if (inLeadLane(it)) upsertRow(LLANE, it);
      });
      // A running row whose record vanished (marker gone, nothing recorded)
      // is not "running" any more and not "finished" either — say so.
      [ACT, RLANE, LLANE].forEach(function (store) {
        Object.keys(store.rows).forEach(function (id) {
          var rec = store.rows[id];
          if (!seen[id] && isRunning(rec.item) && !rec.lost) {
            rec.lost = true; rec.item.status = 'orphaned'; rec.item.detail = (rec.item.detail ? rec.item.detail + ' · ' : '') + 'the process went away without recording a result';
            patchActRow(rec);          // its stream ends by itself once the log is drained
          }
        });
      });
      var list = $('activity');
      $('activity-empty').classList.toggle('hidden', !!list.firstChild);
      $('activity-more').classList.toggle('hidden', !b.truncated);
      $('activity-meta').textContent = items.length ? (items.length + ' turn' + (items.length === 1 ? '' : 's') + (b.truncated ? ' (newest ' + b.limit + ')' : '')) : '';
      renderReviewerLaneScope();
      renderLeadEmpty();
    }).catch(function (e) { console.error('[cockpit] activity failed', e); });
  }
  // the test harness / Phase 43 name: the flat list
  function upsertActivity(it, list) { return upsertRow(ACT, it); }
  function upsertRow(store, it) {
    var rec = store.rows[it.id];
    var list = $(store.containerId);
    if (!rec) {
      rec = store.rows[it.id] = { store: store, item: it, lines: [], cursor: null, streamKey: null, opened: false };
      buildActRow(rec);
      insertRow(store, rec);
    } else {
      rec.item = it;
      patchActRow(rec);
      // Whenever the ordering-relevant part of the item changed (running →
      // terminal, terminal → running, a corrected timestamp) the row moves to
      // its place — moved, not rebuilt: its lines and its stream stay with it.
      // A finished row keeps its stream OPEN (the record can land before the
      // log's last lines are drained); the stream closes on its own `end`.
      if (rec.row.dataset.key !== storeSortKey(store, it)) insertRow(store, rec);
    }
    if (isRunning(it)) { openActLines(rec, true); attachActStream(rec); }
    return rec;
  }
  function insertKeyed(list, row, key, ascending) {
    // keep the reader at the foot of a timeline that was at the foot
    var stick = ascending && (list.scrollTop + list.clientHeight >= list.scrollHeight - 40);
    row.dataset.key = key;
    var kids = list.children, before = null;
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (k === row) continue;
      var kk = k.dataset.key || '';
      if (ascending ? (kk > key) : (kk < key)) { before = k; break; }
    }
    if (before) list.insertBefore(row, before); else list.appendChild(row);
    if (stick) list.scrollTop = list.scrollHeight;
  }
  function insertRow(store, rec) { insertKeyed($(store.containerId), rec.row, storeSortKey(store, rec.item), store.ascending); }
  // Phase 43 name kept for the flat list
  function insertActRow(list, rec) { insertKeyed(list, rec.row, actSortKey(rec.item), false); }
  function actCancel(rec) {
    var it = rec.item;
    if (it.kind === 'conversation' && it.ref && it.ref.conversation) {
      act(rec.cancelBtn, '/api/lead/' + encodeURIComponent(it.ref.conversation) + '/cancel', {}, { confirm: { title: 'Stop ' + (agentName(it) || 'the lead') + '\'s turn?', body: 'Stops the running agent process (identity-checked); the turn is recorded as cancelled — whatever it already did stays done.', labels: { ok: 'Stop the turn', cancel: 'Keep going', danger: true } } });
    } else {
      act(rec.cancelBtn, '/api/cancel-turn', {}, { confirm: { title: 'Stop ' + (agentName(it) || it.role || 'this') + '\'s turn?', body: 'Stops ' + (agentName(it) || it.role || 'the agent') + ' (' + kindLabel(it) + '); the turn is recorded as cancelled and turns are paused until you resume.', labels: { ok: 'Stop the turn', cancel: 'Keep going', danger: true } } });
    }
  }
  function buildActRow(rec) {
    var it = rec.item;
    var row = el('div', 'act-row'); row.dataset.id = it.id;
    var head = el('div', 'act-head');
    rec.dot = el('span', 'dot'); head.appendChild(rec.dot);
    rec.tsEl = el('span', 'ts'); head.appendChild(rec.tsEl);
    rec.roleEl = el('span', 'tag role'); head.appendChild(rec.roleEl);
    rec.agentEl = el('span', 'agent'); head.appendChild(rec.agentEl);
    rec.kindEl = el('span', 'kind'); head.appendChild(rec.kindEl);
    rec.statusEl = el('span', 'status'); head.appendChild(rec.statusEl);
    rec.verdictEl = el('span', 'verdict hidden'); head.appendChild(rec.verdictEl);
    rec.cycleEl = el('span', 'cycle-tag hidden'); head.appendChild(rec.cycleEl);
    head.appendChild(el('span', 'spacer'));
    rec.textBtn = el('button', 'link-btn hidden', 'what it said'); rec.textBtn.type = 'button';
    rec.textBtn.addEventListener('click', function () { toggleActText(rec); });
    head.appendChild(rec.textBtn);
    rec.openBtn = el('button', 'link-btn', 'log'); rec.openBtn.type = 'button';
    rec.openBtn.addEventListener('click', function () { toggleActLines(rec); });
    head.appendChild(rec.openBtn);
    // destructive, small, and apart from the safe links (Fitts + error prevention)
    rec.cancelBtn = el('button', 'btn btn-danger btn-small act-stop hidden', 'stop turn'); rec.cancelBtn.type = 'button'; rec.cancelBtn.title = 'stop this turn (tagteam cancel-turn)';
    rec.cancelBtn.addEventListener('click', function () { actCancel(rec); });
    head.appendChild(rec.cancelBtn);
    row.appendChild(head);
    rec.detailEl = el('div', 'act-detail muted hidden'); row.appendChild(rec.detailEl);
    rec.textEl = el('div', 'act-text hidden'); row.appendChild(rec.textEl);
    rec.box = el('div', 'act-lines hidden'); row.appendChild(rec.box);
    rec.row = row;
    patchActRow(rec);
  }
  function setActStatus(rec) {
    var it = rec.item;
    var txt = outcomeLabel(it.status);
    if (isRunning(it)) txt += ' · ' + fmtAge(it.age_s);
    else if (it.duration_ms != null) txt += ' · ' + fmtAge(Math.round(it.duration_ms / 1000));
    rec.statusEl.textContent = txt;
  }
  // the verdict of a finished reviewer turn / pre-check, from the round it belongs to
  function verdictFor(it) {
    if (isRunning(it) || it.round == null) return null;
    if (it.kind === 'gate') return it.raw_status === 'pass' ? { word: VERDICT_WORD.GATE_PASS, cls: 'ok' } : it.raw_status === 'bounce' ? { word: VERDICT_WORD.GATE_BOUNCE, cls: 'warn' } : null;
    if (it.kind === 'cycle' && it.role === 'reviewer') {
      if (cycleKey(it) !== CYCLE_ID || ROUNDS.cycle !== CYCLE_ID) return null;   // Phase 58: only this cycle's verdicts
      var r = ROUNDS.byN[it.round]; var a = r && r.reviewer && r.reviewer.action;
      if (!a || !VERDICT_WORD[a]) return null;
      return { word: VERDICT_WORD[a], cls: a === 'APPROVE' ? 'ok' : (a === 'REQUEST_CHANGES' ? 'changes' : 'warn'), text: r.reviewer.text };
    }
    return null;
  }
  function patchActRow(rec) {
    var it = rec.item;
    rec.row.className = 'act-row k-' + (it.kind || 'turn') + ' s-' + (it.status || 'unknown') + ' r-' + (it.role || 'none');
    // inside a lane the header already says who: time only, no role/agent columns
    rec.tsEl.textContent = rec.store.ascending ? fmtTime(it.started_at || it.ended_at) : fmtTs(it.started_at || it.ended_at);
    rec.tsEl.title = fmtTs(it.started_at || it.ended_at);
    rec.roleEl.textContent = roleWord(it.role);
    rec.agentEl.textContent = agentName(it);
    rec.kindEl.textContent = kindLabel(it);
    setActStatus(rec);
    var v = verdictFor(it);
    rec.verdictEl.textContent = v ? v.word : '';
    rec.verdictEl.className = 'verdict' + (v ? ' ' + v.cls : ' hidden');
    rec.textBtn.classList.toggle('hidden', !(v && v.text));
    rec.detailEl.textContent = (it.kind === 'panel_lens' ? '' : (it.detail || ''));
    rec.detailEl.classList.toggle('hidden', !rec.detailEl.textContent);
    rec.cancelBtn.classList.toggle('hidden', !(it.status === 'running'));
    var isConv = it.kind === 'conversation' || (it.kind === 'launch' && it.ref && it.ref.conversation);
    rec.openBtn.textContent = rec.box.classList.contains('hidden') ? (isConv ? 'open chat' : 'log') : 'hide';
    rec.openBtn.classList.toggle('hidden', !(it.ref || rec.lines.length));
    if (!isRunning(it) && rec.lines.length && !rec.box.classList.contains('hidden')) rec.openBtn.textContent = 'hide';
    if (rec.store === RLANE) applyLaneScope(rec);   // className was just reset
  }
  function refreshVerdicts() { Object.keys(RLANE.rows).forEach(function (id) { patchActRow(RLANE.rows[id]); }); Object.keys(ACT.rows).forEach(function (id) { patchActRow(ACT.rows[id]); }); }
  function toggleActText(rec) {
    var v = verdictFor(rec.item);
    if (!v || !v.text) return;
    if (rec.textEl.classList.contains('hidden')) { rec.textEl.textContent = v.text; rec.textEl.classList.remove('hidden'); rec.textBtn.textContent = 'hide'; }
    else { rec.textEl.classList.add('hidden'); rec.textBtn.textContent = 'what it said'; }
  }
  function appendActLine(rec, text) {
    rec.lines.push(text);
    if (rec.lines.length > 2000) { rec.lines.shift(); if (rec.box.firstChild) rec.box.removeChild(rec.box.firstChild); }
    var line = el('div', /\[tool|tool_use|Bash|Read|Edit|Write|\[tagteam\]/.test(text) ? 'tool' : null, text);
    var stick = rec.box.scrollTop + rec.box.clientHeight >= rec.box.scrollHeight - 24;
    rec.box.appendChild(line);
    if (stick && !rec.hover) rec.box.scrollTop = rec.box.scrollHeight;
  }
  function openActLines(rec, auto) {
    if (auto && rec.userClosed) return;
    rec.box.classList.remove('hidden'); rec.opened = true;
    rec.openBtn.textContent = 'hide';
    if (!rec.hoverBound) {
      rec.hoverBound = true;
      rec.box.addEventListener('mouseenter', function () { rec.hover = true; });
      rec.box.addEventListener('mouseleave', function () { rec.hover = false; });
    }
    if (!rec.lines.length && !isRunning(rec.item)) fillActLinesFromRecord(rec);
  }
  function toggleActLines(rec) {
    var it = rec.item;
    var isConv = it.kind === 'conversation' || (it.kind === 'launch' && it.ref && it.ref.conversation);
    if (isConv && !rec.lines.length && !isRunning(it)) {
      // a finished chat turn: it lives in the lead lane
      if (it.ref && it.ref.conversation) { LEAD.current = it.ref.conversation; try { localStorage.setItem('tagteam.cockpit.lead', LEAD.current); } catch (e) { /* ignore */ } }
      loadLead(true).then(function () { focusLeadTurn(it.ref && it.ref.turn); });
      return;
    }
    if (rec.box.classList.contains('hidden')) { rec.userClosed = false; openActLines(rec, false); }
    else { rec.box.classList.add('hidden'); rec.userClosed = true; rec.openBtn.textContent = isConv ? 'open chat' : 'log'; }
  }
  function fillActLinesFromRecord(rec) {
    var it = rec.item;
    if (!it.stem) { appendActLine(rec, '(no log recorded for this turn)'); return; }
    getJSON('/api/tail?stem=' + encodeURIComponent(it.stem) + '&lines=200').then(function (r) {
      var b = r.body || {};
      if (rec.lines.length) return;
      if (b.lines && b.lines.length) b.lines.forEach(function (l) { appendActLine(rec, l); });
      else appendActLine(rec, b.message || '(empty log)');
      rec.box.scrollTop = rec.box.scrollHeight;
    });
  }
  function attachActStream(rec) {
    var it = rec.item;
    if (rec.streamKey) return;
    var name = rec.store.name + ':' + it.id;
    if (it.kind === 'conversation' && it.ref && it.ref.conversation) {
      var cid = it.ref.conversation, n = it.ref.turn;
      rec.streamKey = 'lead:' + cid;
      onStream(rec.streamKey, leadStreamPath(cid), name, {
        line: function (d) { if (d.turn === n) appendActLine(rec, d.text); },
        end: function (d) { if (d.turn === n) { detachActStream(rec); refreshAll('activity-end'); } }
      });
      return;
    }
    if (!it.stem) return;
    rec.streamKey = 'log:' + it.stem;
    var after = rec.cursor != null ? '?after=' + encodeURIComponent(rec.cursor) : '';
    onStream(rec.streamKey, '/api/activity/log/' + encodeURIComponent(it.stem) + '/events' + after, name, {
      line: function (d) { rec.cursor = d.id; appendActLine(rec, d.text); },
      end: function () { detachActStream(rec); refreshAll('activity-end'); }
    });
  }
  function detachActStream(rec) {
    if (!rec.streamKey) return;
    offStream(rec.streamKey, rec.store.name + ':' + rec.item.id);
    rec.streamKey = null;
    patchActRow(rec);
  }
  function focusWorkingLane() {
    var rec = null;
    [RLANE, LLANE].forEach(function (store) { Object.keys(store.rows).forEach(function (id) { if (!rec && isRunning(store.rows[id].item)) rec = store.rows[id]; }); });
    var target = rec ? rec.row : ($('lead-timeline').querySelector('.lead-msg.working') || $('lanes'));
    try { target.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) { target.scrollIntoView(); }
    if (rec) { rec.row.classList.add('flash'); setTimeout(function () { rec.row.classList.remove('flash'); }, 1600); }
  }
  function focusLeadTurn(n) {
    if (n == null) return;
    var m = $('lead-timeline').querySelector('.lead-msg[data-turn="' + String(n) + '"]');
    if (m) { try { m.scrollIntoView({ block: 'center' }); } catch (e) { /* ignore */ } m.classList.add('flash'); setTimeout(function () { m.classList.remove('flash'); }, 1600); }
  }

  // ---------- Phase 37: Lead panel (text nodes only — the page holds the POST token) ----------
  // Phase 45: the panel is the lead LANE — the chat's messages are keyed rows
  // in #lead-timeline, merged in time with the lead's cycle-turn cards
  // (LLANE); nothing here wipes the container.
  var LEAD = { list: [], current: null, conv: null, streamKey: null, cursor: {}, lines: {}, sending: false, sentAt: null, timer: null, msgRows: {} };
  function leadStreamPath(cid) { return '/api/lead/' + encodeURIComponent(cid) + '/events' + (LEAD.cursor[cid] ? '?after=' + encodeURIComponent(LEAD.cursor[cid]) : ''); }
  function leadLines(cid, n) { var c = LEAD.lines[cid] = LEAD.lines[cid] || {}; return (c[n] = c[n] || []); }
  try { LEAD.current = localStorage.getItem('tagteam.cockpit.lead') || null; } catch (e) { /* ignore */ }

  function leadStatus(text, cls) { var st = $('lead-status'); st.textContent = text || ''; st.className = 'lead-status muted small' + (cls ? ' ' + cls : ''); }

  function loadLead(force) {
    return getJSON('/api/lead').then(function (r) {
      if (!r.ok) throw new Error('lead ' + r.status);
      LEAD.list = r.body.conversations || [];
      LEAD.slot = r.body.slot || {}; LEAD.cfg = r.body.lead || {};
      var sel = $('lead-select'); sel.innerHTML = '';
      LEAD.list.forEach(function (c) {
        var o = document.createElement('option'); o.value = c.id;
        o.textContent = (c.title || c.id) + ' · ' + (c.turns || 0) + ' message' + (c.turns === 1 ? '' : 's');
        sel.appendChild(o);
      });
      if (!LEAD.current || !LEAD.list.some(function (c) { return c.id === LEAD.current; })) LEAD.current = LEAD.list.length ? LEAD.list[0].id : null;
      if (LEAD.current) sel.value = LEAD.current;
      sel.classList.toggle('hidden', !LEAD.list.length);
      renderLeadGate();
      if (LEAD.current) return loadConversation(LEAD.current, force);
      LEAD.conv = null;
      renderLeadTimeline();
      var busyElsewhere = LEAD.slot && LEAD.slot.held && LEAD.slot.kind !== 'conversation';   // keep the gate line
      if (LEAD.cfg.ok && !busyElsewhere) leadStatus('New chat with ' + (LEAD.cfg.agent || 'the lead') + ' — your first message starts it.');
      else if (!LEAD.cfg.ok) leadStatus('');
    }).catch(function (e) { leadStatus('The chat is unavailable: ' + e, 'error'); });
  }

  function renderLeadGate() {
    var send = $('btn-lead-send'), ta = $('lead-text');
    var cfg = LEAD.cfg || {}, slot = LEAD.slot || {};
    if (!cfg.ok) {
      send.disabled = true; ta.disabled = true;
      leadStatus('This page cannot run the lead yet: ' + (cfg.errors || []).join('; ') + ' — set agents.lead.headless in tagteam.yaml.', 'error');
      return;
    }
    if (slot.held && slot.kind !== 'conversation') {
      // a cycle turn is a different thing from this chat — name it, and point
      // at where its activity streams (this lane, or the reviewer's).
      send.disabled = true; ta.disabled = false;
      var st = $('lead-status'); st.textContent = ''; st.className = 'lead-status muted small busy';
      var agents = (NOW && NOW.agents) || {};
      var who = slot.role === 'reviewer' ? (agents.reviewer || 'the reviewer') : (cfg.agent || agents.lead || 'the lead');
      var sentence = (slot.kind === 'gate') ? 'The pre-check is running' : (slot.kind === 'panel') ? 'A review lens is running' : (slot.kind === 'briefer') ? 'The decision brief is being written' : who + ' is working on its ' + (slot.role === 'reviewer' ? 'review' : 'turn');
      var where = slot.role === 'reviewer' || slot.kind === 'gate' || slot.kind === 'panel' ? 'in the reviewer lane' : 'above';
      st.appendChild(document.createTextNode(sentence + (slot.round ? ' (round ' + slot.round + ')' : '') + ' — streaming ' + where + ' · '));
      var see = el('button', 'link-btn', 'watch it'); see.type = 'button'; see.addEventListener('click', focusWorkingLane);
      st.appendChild(see); st.appendChild(document.createTextNode(', wait, or '));
      var lnk = el('button', 'link-btn', 'leave a note for the next turn'); lnk.type = 'button'; lnk.addEventListener('click', function () { showTab('notes'); });
      st.appendChild(lnk); st.appendChild(document.createTextNode('. You can chat while turns are paused.'));
      return;
    }
    if (LEAD.sending || (slot.held && slot.kind === 'conversation')) {
      // Visibility of status: an unmistakable "working" state while your
      // message runs — spinner, who, elapsed — not a faint line.
      send.disabled = true; ta.disabled = false;
      $('btn-lead-cancel').classList.remove('hidden');
      var st2 = $('lead-status'); st2.textContent = ''; st2.className = 'lead-status busy working';
      st2.appendChild(el('span', 'spin'));
      st2.appendChild(document.createTextNode((cfg.agent || 'The lead') + ' is working on your message' + (LEAD.sentAt ? ' · ' + fmtAge(Math.round((Date.now() - LEAD.sentAt) / 1000)) : '') + ' — its steps stream above.'));
      return;
    }
    send.disabled = false; ta.disabled = false; $('btn-lead-cancel').classList.add('hidden');
    if (LEAD.conv) leadStatus((LEAD.conv.continuity ? 'memory: ' + LEAD.conv.continuity : '') + (LEAD.conv.turns && LEAD.conv.turns.length ? ' · ' + LEAD.conv.turns.length + ' message' + (LEAD.conv.turns.length === 1 ? '' : 's') : ''));
  }

  function msgNode(who, cls, ts, text) {
    var m = el('div', 'lead-msg ' + cls);
    var head = el('div', 'who'); head.appendChild(el('span', null, who)); head.appendChild(el('span', 'spacer')); head.appendChild(el('span', null, fmtTs(ts)));
    m.appendChild(head);
    var body = el('div', 'body'); body.textContent = text || ''; m.appendChild(body);
    return m;
  }
  function linesBox(lines) {
    var live = el('div', 'live'); live.dataset.live = '1';
    lines.forEach(function (text) { live.appendChild(el('div', /\[tool|tool_use|Bash|Read|Edit|Write|\[tagteam\]/.test(text) ? 'tool' : null, text)); });
    return live;
  }
  // one keyed row per chat turn: the "you" bubble + the lead's bubble
  function msgSig(cid, t) { return [t.status, (t.reply || '').length, t.finished_at || '', t.error || '', t.continuity || '', leadLines(cid, t.n).length].join('|'); }
  function fillMsgRow(rec) {
    var t = rec.turn, cid = rec.cid;
    while (rec.row.firstChild) rec.row.removeChild(rec.row.firstChild);
    var agent = (LEAD.cfg && LEAD.cfg.agent) || 'lead';
    rec.row.appendChild(msgNode('you', 'you', t.ts, t.user_text));
    var m = el('div', 'lead-msg lead'); m.dataset.turn = String(t.n); m.dataset.cid = cid;
    var head = el('div', 'who'); head.appendChild(el('span', null, agent)); head.appendChild(el('span', 'spacer'));
    var cont = t.continuity === 'resumed session' ? 'same session' : (t.continuity || '');
    head.appendChild(el('span', null, t.status === 'running' ? 'replying…' : cont + (t.finished_at ? (cont ? ' · ' : '') + fmtTs(t.finished_at) : '')));
    m.appendChild(head);
    // the streamed activity lines are KEPT — a re-render mid-turn refills the
    // live box, and a finished turn keeps them under a collapsed disclosure
    var kept = leadLines(cid, t.n);
    if (t.status === 'running') { m.classList.add('working'); var live = linesBox(kept); m.appendChild(live); live.scrollTop = live.scrollHeight; }
    else {
      if (t.status === 'ok') { var b = el('div', 'body'); b.textContent = t.reply || '(no text reply)'; m.appendChild(b); }
      else {
        var f = el('div', 'fail');
        var when = t.finished_at ? ' at ' + fmtTs(t.finished_at) : '';
        var why = t.error ? plainError(t.error).replace(/^cancelled /, '') : '';
        var lead = why.indexOf('by you') === 0 ? 'Cancelled ' + why + when : (outcomeLabel(t.status).charAt(0).toUpperCase() + outcomeLabel(t.status).slice(1) + (why ? ' — ' + why : '') + when);
        f.textContent = lead + '. No reply came' + (kept.length ? ' — the activity below shows what ' + agent + ' did before that.' : '.');
        if (t.log_path) { f.title = 'log: ' + t.log_path; }
        m.appendChild(f);
      }
      if (kept.length) {
        var det = el('details', 'activity'); det.appendChild(el('summary', null, 'activity (' + kept.length + ' line' + (kept.length === 1 ? '' : 's') + ')'));
        det.appendChild(linesBox(kept)); m.appendChild(det);
      }
    }
    rec.row.appendChild(m);
    rec.sig = msgSig(cid, t);
  }
  function upsertLeadMessage(cid, t) {
    var key = 'msg:' + cid + ':' + t.n;
    var rec = LEAD.msgRows[key];
    var list = $('lead-timeline');
    if (!rec) {
      rec = LEAD.msgRows[key] = { cid: cid, turn: t, row: el('div', 'tl-msg'), sig: null };
      rec.row.dataset.id = key;
      fillMsgRow(rec);
      insertKeyed(list, rec.row, ascSortKey(t.ts, key), true);
    } else {
      rec.turn = t;
      if (rec.sig !== msgSig(cid, t)) { var stick = list.scrollTop + list.clientHeight >= list.scrollHeight - 40; fillMsgRow(rec); if (stick) list.scrollTop = list.scrollHeight; }
      if (rec.row.dataset.key !== ascSortKey(t.ts, key)) insertKeyed(list, rec.row, ascSortKey(t.ts, key), true);
    }
  }
  function renderLeadTimeline() {
    var conv = LEAD.conv; var cid = conv ? conv.id : null;
    var list = $('lead-timeline');
    // messages of another conversation leave; this conversation's are upserted
    Object.keys(LEAD.msgRows).forEach(function (key) {
      var rec = LEAD.msgRows[key];
      if (rec.cid !== cid) { if (rec.row.parentNode) rec.row.parentNode.removeChild(rec.row); delete LEAD.msgRows[key]; }
    });
    (conv && conv.turns || []).forEach(function (t) { upsertLeadMessage(cid, t); });
    renderLeadEmpty();
  }
  function renderLeadEmpty() {
    // the prompt speaks to the CHAT: it shows while the current conversation
    // has no messages — even when the lead's cycle-turn cards are already in
    // the timeline (the watcher worked before you said anything)
    var hasMessages = !!(LEAD.conv && LEAD.conv.turns && LEAD.conv.turns.length);
    $('lead-empty').classList.toggle('hidden', hasMessages);
  }

  function loadConversation(cid, force) {
    return getJSON('/api/lead/' + encodeURIComponent(cid)).then(function (r) {
      if (!r.ok) throw new Error('conversation ' + r.status);
      LEAD.conv = r.body; LEAD.slot = r.body.slot || LEAD.slot;
      var running = (r.body.turns || []).some(function (t) { return t.status === 'running'; });
      LEAD.sending = running;
      renderLeadTimeline();
      renderLeadGate();
      $('lane-lead').classList.toggle('chatting', running);
      subscribeLead(cid);
    });
  }

  function subscribeLead(cid) {
    var key = 'lead:' + cid;
    if (LEAD.streamKey === key) return;
    if (LEAD.streamKey) offStream(LEAD.streamKey, 'lead');
    LEAD.streamKey = key;
    // Shared with the flat list's row for the same conversation (one connection).
    onStream(key, leadStreamPath(cid), 'lead', {
      line: function (d) {
        LEAD.cursor[cid] = d.id;
        var kept = leadLines(cid, d.turn); kept.push(d.text);
        var box = $('lead-timeline').querySelector('.lead-msg[data-turn="' + String(d.turn) + '"][data-cid="' + cid + '"] .live');
        if (!box) return;
        var line = el('div', /\[tool|tool_use|Bash|Read|Edit|Write|\[tagteam\]/.test(d.text) ? 'tool' : null, d.text);
        box.appendChild(line); box.scrollTop = box.scrollHeight;
      },
      end: function (d) {
        LEAD.cursor[cid] = d.id;
        LEAD.sending = false; LEAD.sentAt = null;
        $('lane-lead').classList.remove('chatting');
        loadConversation(cid, true).then(function () { refreshAll('lead-turn'); });
      }
    });
  }

  $('lead-select').addEventListener('change', function () {
    LEAD.current = this.value; try { localStorage.setItem('tagteam.cockpit.lead', LEAD.current); } catch (e) { /* ignore */ }
    loadConversation(LEAD.current, true);
  });
  $('btn-reviewer-last').addEventListener('click', function () { toggleLastSession(); });
  $('btn-lead-new').addEventListener('click', function () {
    postJSON('/api/lead/new', {}).then(function (r) {
      if (!r.ok) { toast('err', (r.body && r.body.message) || 'Could not start a chat'); return; }
      LEAD.current = r.body.conversation.id; try { localStorage.setItem('tagteam.cockpit.lead', LEAD.current); } catch (e) { /* ignore */ }
      loadLead(true);
    });
  });
  function sendLead() {
    var ta = $('lead-text'); var text = ta.value.trim();
    if (!text) return;
    var go = function (cid) {
      LEAD.sending = true; LEAD.sentAt = Date.now(); renderLeadGate();
      postJSON('/api/lead/' + encodeURIComponent(cid) + '/send', { text: text }).then(function (r) {
        if (!r.ok) {
          LEAD.sending = false; LEAD.sentAt = null;
          leadStatus((r.body && r.body.message) || ('Send failed (' + r.status + ')'), r.body && r.body.busy ? 'busy' : 'error');
          $('btn-lead-send').disabled = false;
          return;
        }
        ta.value = '';
        loadConversation(cid, true);
      });
    };
    if (LEAD.current) go(LEAD.current);
    else postJSON('/api/lead/new', {}).then(function (r) { if (r.ok) { LEAD.current = r.body.conversation.id; go(LEAD.current); } else toast('err', (r.body && r.body.message) || 'Could not start a chat'); });
  }
  $('lead-form').addEventListener('submit', function (e) { e.preventDefault(); sendLead(); });
  $('lead-text').addEventListener('keydown', function (e) { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); sendLead(); } });
  $('btn-lead-cancel').addEventListener('click', function () {
    if (!LEAD.current) return;
    act($('btn-lead-cancel'), '/api/lead/' + encodeURIComponent(LEAD.current) + '/cancel', {}, { confirm: { title: 'Stop ' + ((LEAD.cfg && LEAD.cfg.agent) || 'the lead') + '\'s turn?', body: 'Stops the running agent process (identity-checked); the turn is recorded as cancelled — whatever it already did stays done.', labels: { ok: 'Stop the turn', cancel: 'Keep going', danger: true } } });
  });

  // ---------- Live connection: SSE with polling fallback ----------
  var connMode = 'connecting', es = null, pollTimer = null, pollDelay = 3000, sseRetry = null, sseAttempts = 0;
  function setConn(mode, text) {
    connMode = mode;
    var c = $('conn'); c.dataset.mode = mode;
    $('conn-text').textContent = text || ({ live: 'Live', polling: 'Polling', disconnected: 'Disconnected — retrying', connecting: 'Connecting…' })[mode];
  }
  function startPolling() {
    if (pollTimer) return;
    setConn('polling');
    var tick = function () {
      refreshAll('tick');
      pollDelay = Math.min(15000, Math.round(pollDelay * 1.25));
      pollTimer = setTimeout(tick, pollDelay);
    };
    pollTimer = setTimeout(tick, pollDelay);
  }
  function stopPolling() { if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } pollDelay = 3000; }
  function connectSSE() {
    // `?nosse=1` forces the polling path (dogfood / debugging the fallback).
    if (!window.EventSource || /[?&]nosse=1/.test(location.search)) { startPolling(); return; }
    try { es = new EventSource(url('/api/events')); } catch (e) { startPolling(); return; }
    es.addEventListener('open', function () { sseAttempts = 0; stopPolling(); setConn('live'); });
    es.addEventListener('change', function () { refreshAll('sse'); });
    es.addEventListener('error', function () {
      // EventSource auto-reconnects on transient errors; while it does, poll.
      if (es && es.readyState === EventSource.CLOSED) {
        setConn('disconnected');
        sseAttempts++;
        startPolling();
        if (sseRetry) clearTimeout(sseRetry);
        sseRetry = setTimeout(function () { try { es.close(); } catch (e2) { /* ignore */ } connectSSE(); }, Math.min(30000, 2000 * Math.pow(2, Math.min(sseAttempts, 4))));
      } else {
        setConn('disconnected');
        startPolling();
      }
    });
  }

  // ---------- boot ----------
  try { if (localStorage.getItem(DRAWER_KEY) === '1') setDrawer(true); } catch (e) {}   // Phase 68: the drawer stays as you left it
  var verMeta = document.querySelector('meta[name="tagteam-version"]');
  if (verMeta) $('now-version').textContent = verMeta.getAttribute('content') || '';
  if (BASE) {
    // Mounted by the hub: a way back, placed with the other navigation.
    var hubLink = el('a', 'theme-link', '\u2190 Hub'); hubLink.href = '/'; hubLink.title = 'the cross-project hub';
    hubLink.id = 'hub-link';
    var actions = document.querySelector('.now-actions');
    if (actions) actions.insertBefore(hubLink, actions.firstChild);
    // The Saloon's own JS talks to root-relative /api/… (a per-project
    // server); it is not offered under a hub mount.
    var saloon = document.querySelector('.theme-link[href$="theme=saloon"]');
    if (saloon) saloon.remove();
  }
  try { var saved = localStorage.getItem('tagteam.cockpit.tab'); if (saved && $('tab-' + saved)) showTab(saved); } catch (e) { /* ignore */ }
  refreshAll('boot');
  connectSSE();
  window.addEventListener('resize', fitLanes);
  // Ages in the strip tick locally between refreshes.
  setInterval(function () { if (NOW) renderNowAges(); }, 1000);
  // Safety net in live mode: a slow full refresh (process liveness that
  // leaves no file trace, clock drift) — cheap, local.
  setInterval(function () { if (connMode === 'live') refreshAll('tick'); }, 30000);
  function renderNowAges() {
    // Cheap local re-render of the age chips (no fetch).
    if (NOW.owed && NOW.owed.age_s != null) NOW.owed.age_s += 1;
    if (NOW.inflight && NOW.inflight.age_s != null) NOW.inflight.age_s += 1;
    if (NOW.paused && NOW.paused.age_s != null) NOW.paused.age_s += 1;
    if (NOW.launch && NOW.launch.age_s != null) NOW.launch.age_s += 1;
    tickTurnBar();
    renderLaneAges();
  }
})();
