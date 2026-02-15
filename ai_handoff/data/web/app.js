(function() {
  'use strict';

  // --- DOM refs ---
  const saloonArt    = document.getElementById('saloon-art');
  const mayorHitzone = document.getElementById('mayor-hitzone');
  const mayorDialogue = document.getElementById('mayor-dialogue');
  const dialogueText = document.getElementById('dialogue-text');
  const dialogueActions = document.getElementById('dialogue-actions');
  const dialogueCloseBtn = document.getElementById('dialogue-close');
  const mayorPrompt  = document.getElementById('mayor-prompt');

  const setupFormEl   = document.getElementById('setup-form');
  const setupLeadIn   = document.getElementById('setup-lead');
  const setupRevIn    = document.getElementById('setup-reviewer');
  const setupSubmit   = document.getElementById('setup-submit');

  const phaseFormEl   = document.getElementById('phase-form');
  const phaseNameIn   = document.getElementById('phase-name');
  const phaseSubmit   = document.getElementById('phase-submit');

  const statusPanel  = document.getElementById('status-panel');
  const controlsPanel = document.getElementById('controls-panel');
  const timelineSec  = document.getElementById('timeline-section');
  const cycleSec     = document.getElementById('cycle-section');

  const stPhase  = document.getElementById('st-phase');
  const stType   = document.getElementById('st-type');
  const stRound  = document.getElementById('st-round');
  const stTurn   = document.getElementById('st-turn');
  const stStatus = document.getElementById('st-status');
  const stResult = document.getElementById('st-result');

  const btnApprove  = document.getElementById('btn-approve');
  const btnChanges  = document.getElementById('btn-changes');
  const btnEscalate = document.getElementById('btn-escalate');
  const btnAbort    = document.getElementById('btn-abort');

  const connDot  = document.getElementById('conn-dot');
  const connText = document.getElementById('conn-text');

  const timelineEl   = document.getElementById('timeline');
  const cycleToggle  = document.getElementById('cycle-toggle');
  const cycleSelect  = document.getElementById('cycle-select');
  const cycleContent = document.getElementById('cycle-content');

  const abortModal   = document.getElementById('abort-modal');
  const abortReason  = document.getElementById('abort-reason');
  const abortCancel  = document.getElementById('abort-cancel');
  const abortConfirm = document.getElementById('abort-confirm');

  const escalationChoices = document.getElementById('escalation-choices');
  const btnAgreeLead      = document.getElementById('btn-agree-lead');
  const btnAgreeReviewer  = document.getElementById('btn-agree-reviewer');
  const btnDefer          = document.getElementById('btn-defer');

  const phaseMapPanel = document.getElementById('phase-map-panel');
  const phaseMapList  = document.getElementById('phase-map-list');

  // --- State ---
  let currentState = {};
  let agentConfig = {};
  let lastUpdatedAt = null;
  let cycleOpen = false;
  let dialogueVisible = false;
  let activeForm = null; // null, 'setup', 'phase'

  // --- Mode computation ---
  function getMode() {
    if (!agentConfig || !agentConfig.agents) return 'welcome';
    const status = currentState && currentState.status;
    if (!status || status === 'done' || status === 'aborted') return 'idle';
    return 'active'; // ready, working, escalated
  }

  function updateVisibility() {
    const mode = getMode();

    // If mode no longer matches form, close it
    if (mode !== 'welcome' && activeForm === 'setup') activeForm = null;
    if (mode === 'active' && activeForm === 'phase') activeForm = null;

    // Setup form: shown when activeForm is 'setup'
    show(setupFormEl, activeForm === 'setup');

    // Phase form: shown when activeForm is 'phase'
    show(phaseFormEl, activeForm === 'phase');

    // Status panel: visible in idle and active modes (hidden when a form is active)
    show(statusPanel, mode !== 'welcome' && activeForm !== 'setup' && activeForm !== 'phase');

    // Controls: only in active mode
    show(controlsPanel, mode === 'active');

    // Escalation choices: only when escalated
    const isEscalated = currentState.status === 'escalated';
    show(escalationChoices, mode === 'active' && isEscalated);

    // Timeline: visible if there's history and not in welcome mode
    const hasHistory = currentState.history && currentState.history.length > 0;
    show(timelineSec, mode !== 'welcome' && hasHistory);

    // Cycle viewer: visible if not in welcome mode
    show(cycleSec, mode !== 'welcome');

    // Phase map: visible if not in welcome mode
    show(phaseMapPanel, mode !== 'welcome');

    // Mayor prompt: welcome mode, no dialogue, no setup form
    show(mayorPrompt, mode === 'welcome' && !dialogueVisible && activeForm !== 'setup');
  }

  function show(el, visible) {
    if (!el) return;
    el.classList.toggle('hidden', !visible);
  }

  // --- Saloon ASCII art scene ---
  const SALOON_LINES = [
    { text: "\u2554" + "\u2550".repeat(78) + "\u2557", cls: "ch-border" },
    { parts: [
      { text: "\u2551  ", cls: "ch-border" },
      { text: "THE HANDOFF SALOON", cls: "ch-title" },
      { text: " ".repeat(58) + "\u2551", cls: "ch-border" },
    ]},
    { text: "\u2551" + "\u2500".repeat(78) + "\u2551", cls: "ch-border" },
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "                                            \u250C\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510", cls: "ch-shelf" },
      { text: "       \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "       \u250C\u2500\u2500\u2500\u2510", cls: "ch-mayor" },
      { text: "              ", cls: "bg" },
      { text: "\u2554\u2550\u2550\u2550\u2550\u2557", cls: "ch-clock" },
      { text: "            ", cls: "bg" },
      { text: "\u2502 \u2591 WHISKEY  RYE  CORN  \u2591 \u2502", cls: "ch-shelf" },
      { text: "       \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "       \u2502 \u2666 \u2502", cls: "ch-mayor" },
      { text: "              ", cls: "bg" },
      { text: "\u2551 \u2302  \u2551", cls: "ch-clock" },
      { text: "            ", cls: "bg" },
      { text: "\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518", cls: "ch-shelf" },
      { text: "       \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "       \u2514\u2500\u252C\u2500\u2518", cls: "ch-mayor" },
      { text: "              ", cls: "bg" },
      { text: "\u2551\u2500\u2500\u2500\u2500\u2551", cls: "ch-clock" },
      { text: "                          ", cls: "bg" },
      { text: "(\\ /)", cls: "ch-rabbit" },
      { text: "               \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "      \u250C\u2500\u2500\u2534\u2500\u2500\u2510", cls: "ch-mayor" },
      { text: "             ", cls: "bg" },
      { text: "\u2551 \u25F7  \u2551", cls: "ch-clock" },
      { text: "                          ", cls: "bg" },
      { text: "( . .)", cls: "ch-rabbit" },
      { text: "              \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "      \u2502 \u25C9 \u25C9 \u2502", cls: "ch-mayor" },
      { text: "             ", cls: "bg" },
      { text: "\u2551    \u2551", cls: "ch-clock" },
      { text: "                          ", cls: "bg" },
      { text: "c(\")(\")", cls: "ch-rabbit" },
      { text: "             \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "      \u2502  \u25BD  \u2502", cls: "ch-mayor" },
      { text: "             ", cls: "bg" },
      { text: "\u255A\u2564\u2550\u2550\u2564\u255D", cls: "ch-clock" },
      { text: "                         ", cls: "bg" },
      { text: "\u250C\u2500\u2500\u2534\u2500\u2500\u2510", cls: "ch-rabbit" },
      { text: "              \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "      \u2502 \u256E\u2500\u256F \u2502", cls: "ch-mayor" },
      { text: "              ", cls: "bg" },
      { id: "pendulum", text: " \u2502  \u2502", cls: "ch-pendulum" },
      { text: "                          ", cls: "bg" },
      { text: "\u2502     \u2502", cls: "ch-rabbit" },
      { text: "              \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "      \u2514\u2500\u2500\u252C\u2500\u2500\u2518", cls: "ch-mayor" },
      { text: "              ", cls: "bg" },
      { id: "pendulum-bobs", text: " \u25EF  \u25EF", cls: "ch-pendulum" },
      { text: "                          ", cls: "bg" },
      { text: "\u2502 \u250C\u2500\u2510 \u2502", cls: "ch-rabbit" },
      { text: "              \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "      \u2554\u2550\u2550\u2567\u2550\u2550\u2557", cls: "ch-mayor" },
      { text: "                                            ", cls: "bg" },
      { text: "\u2502 \u2502B\u2502 \u2502", cls: "ch-rabbit" },
      { text: "              \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "      \u2551MAYOR\u2551", cls: "ch-mayor" },
      { text: "                                            ", cls: "bg" },
      { text: "\u2502 \u2514\u2500\u2518 \u2502", cls: "ch-rabbit" },
      { text: "              \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "      \u255A\u2550\u2550\u2564\u2550\u2550\u255D", cls: "ch-mayor" },
      { text: "                               ", cls: "bg" },
      { text: "\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550", cls: "ch-counter" },
      { text: "\u2514\u2500\u2500\u252C\u2500\u2500\u2518", cls: "ch-rabbit" },
      { text: "\u2550\u2550\u2550\u2550\u2550\u2550", cls: "ch-counter" },
      { text: "        \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "        /|\\", cls: "ch-mayor" },
      { text: "   ", cls: "bg" },
      { text: "\u250C\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2510", cls: "ch-table" },
      { text: "                                 ", cls: "bg" },
      { text: "/|\\", cls: "ch-rabbit" },
      { text: "                \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "       / | \\", cls: "ch-mayor" },
      { text: "  ", cls: "bg" },
      { text: "\u2502          \u2502", cls: "ch-table" },
      { text: "                                ", cls: "bg" },
      { text: "/ | \\", cls: "ch-rabbit" },
      { text: "               \u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551", cls: "ch-border" },
      { text: "              ", cls: "bg" },
      { text: "\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2518", cls: "ch-table" },
      { text: " ".repeat(53) + "\u2551", cls: "ch-border" },
    ]},
    { parts: [
      { text: "\u2551  ", cls: "ch-border" },
      { text: "\u2584".repeat(64), cls: "ch-floor" },
      { text: "            \u2551", cls: "ch-border" },
    ]},
    { text: "\u255A" + "\u2550".repeat(78) + "\u255D", cls: "ch-border" },
  ];

  // --- Render saloon art ---
  function renderSaloon() {
    let html = '';
    for (const line of SALOON_LINES) {
      if (line.text !== undefined) {
        html += '<span class="' + line.cls + '">' + escHtml(line.text) + '</span>\n';
      } else if (line.parts) {
        for (const part of line.parts) {
          const idAttr = part.id ? ' id="' + part.id + '"' : '';
          html += '<span class="' + part.cls + '"' + idAttr + '>' + escHtml(part.text) + '</span>';
        }
        html += '\n';
      }
    }
    saloonArt.innerHTML = html;
  }

  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  renderSaloon();

  // --- Pendulum animation ---
  let pendulumTick = 0;
  const pendulumFrames = [
    [" \u25EF  \u2502", " \u25EF  \u25EF"],
    [" \u2502  \u2502", " \u25EF  \u25EF"],
    [" \u2502  \u25EF", " \u25EF  \u25EF"],
    [" \u2502  \u2502", " \u25EF  \u25EF"],
  ];
  let pendulumRunning = true;

  function animatePendulum() {
    if (!pendulumRunning) return;
    const pendEl = document.getElementById('pendulum');
    const bobsEl = document.getElementById('pendulum-bobs');
    if (pendEl && bobsEl) {
      const frame = pendulumFrames[pendulumTick % pendulumFrames.length];
      pendEl.textContent = frame[0];
      bobsEl.textContent = frame[1];
      pendulumTick++;
    }
  }

  setInterval(animatePendulum, 800);

  // --- Saloon state mapping ---
  function applySaloonState(state) {
    const status = state.status || '';
    const result = state.result || '';

    saloonArt.classList.remove('muted', 'state-working', 'state-approved', 'state-escalated');
    pendulumRunning = true;

    if (status === 'working') {
      saloonArt.classList.add('state-working');
    } else if (status === 'done' && result === 'approved') {
      saloonArt.classList.add('state-approved');
    } else if (status === 'escalated') {
      saloonArt.classList.add('state-escalated');
      pendulumRunning = false;
    } else if (status === 'aborted') {
      saloonArt.classList.add('muted');
      pendulumRunning = false;
    }
  }

  // --- Mayor dialogue system ---
  function showDialogue(text, actions) {
    dialogueText.textContent = text;
    dialogueActions.innerHTML = '';
    for (const action of actions) {
      const btn = document.createElement('button');
      btn.className = 'dialogue-action-btn';
      btn.textContent = action.label;
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        action.action();
      });
      dialogueActions.appendChild(btn);
    }
    mayorDialogue.classList.remove('hidden');
    dialogueVisible = true;
    updateVisibility();
  }

  function closeDialogue() {
    mayorDialogue.classList.add('hidden');
    dialogueVisible = false;
    updateVisibility();
  }

  dialogueCloseBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    closeDialogue();
  });

  function getMayorIdleText() {
    const status = currentState.status;
    const result = currentState.result;
    if (status === 'done' && result === 'approved') {
      return "Phase complete! Ready for the next one?";
    }
    if (status === 'aborted') {
      return "This cycle was aborted. Ready to start fresh?";
    }
    return "Your saloon is ready. What would you like to do?";
  }

  function getMayorActiveText() {
    const status = currentState.status;
    const turn = currentState.turn;
    if (status === 'working') {
      return "An agent is working. Sit tight, partner.";
    }
    if (status === 'ready') {
      return "Handoff ready \u2014 " + agentName(turn) + " is up next.";
    }
    if (status === 'escalated') {
      return "This got escalated. A human needs to step in.";
    }
    return "The saloon is busy.";
  }

  function onMayorClick() {
    if (dialogueVisible) {
      closeDialogue();
      return;
    }

    const mode = getMode();

    if (mode === 'welcome') {
      showDialogue(
        "Welcome to the Handoff Saloon! I'll help you set up your project. Enter your agent names on the right.",
        []
      );
      activeForm = 'setup';
      updateVisibility();
    } else if (mode === 'idle') {
      const actions = [
        { label: 'Start new phase', action: startNewPhase },
        { label: 'Status summary', action: showStatusSummary },
        { label: 'How it works', action: showHowItWorks },
      ];
      showDialogue(getMayorIdleText(), actions);
    } else if (mode === 'active') {
      const actions = [
        { label: 'Status summary', action: showStatusSummary },
        { label: 'How it works', action: showHowItWorks },
      ];
      showDialogue(getMayorActiveText(), actions);
    }
  }

  function startNewPhase() {
    closeDialogue();
    activeForm = 'phase';
    updateVisibility();
  }

  function showStatusSummary() {
    const s = currentState;
    const lines = [];
    if (s.phase) lines.push("Phase: " + s.phase);
    if (s.type)  lines.push("Type: " + s.type);
    if (s.round) lines.push("Round: " + s.round);
    if (s.turn)  lines.push("Turn: " + agentName(s.turn));
    if (s.status) lines.push("Status: " + s.status);
    if (s.result) lines.push("Result: " + s.result);
    const text = lines.length > 0 ? lines.join("\n") : "No active handoff.";
    showDialogue(text, [{ label: 'Close', action: closeDialogue }]);
  }

  function showHowItWorks() {
    showDialogue(
      "The Handoff Saloon orchestrates work between two AI agents. " +
      "A lead agent does the work, then hands off to a reviewer. " +
      "They go back and forth until the work is approved. " +
      "You can monitor, approve, request changes, escalate, or abort from here.",
      [{ label: 'Got it', action: closeDialogue }]
    );
  }

  mayorHitzone.addEventListener('click', onMayorClick);

  // --- Setup form ---
  async function submitSetup() {
    const lead = setupLeadIn.value.trim();
    const reviewer = setupRevIn.value.trim();

    if (!lead || !reviewer) {
      showDialogue("Please fill in both agent names.", []);
      return;
    }

    setupSubmit.disabled = true;
    try {
      const r = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lead: lead, reviewer: reviewer }),
      });
      const data = await r.json();

      if (r.ok) {
        agentConfig = data;
        activeForm = null;
        showDialogue("You're all set! Click me anytime to start a new phase or get help.", []);
        updateVisibility();
      } else if (r.status === 409) {
        showDialogue("Config already exists \u2014 overwrite?", [
          { label: 'Overwrite', action: function() { submitSetupOverwrite(lead, reviewer); } },
          { label: 'Cancel', action: closeDialogue },
        ]);
      } else {
        showDialogue(data.error || "Something went wrong.", []);
      }
    } catch (e) {
      showDialogue("Failed to connect to the server.", []);
    } finally {
      setupSubmit.disabled = false;
    }
  }

  async function submitSetupOverwrite(lead, reviewer) {
    try {
      const r = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lead: lead, reviewer: reviewer, overwrite: true }),
      });
      if (r.ok) {
        agentConfig = await r.json();
        activeForm = null;
        showDialogue("Config updated! Click me to start a new phase.", []);
        updateVisibility();
      } else {
        const data = await r.json();
        showDialogue(data.error || "Something went wrong.", []);
      }
    } catch (e) {
      showDialogue("Failed to connect to the server.", []);
    }
  }

  setupSubmit.addEventListener('click', submitSetup);
  setupLeadIn.addEventListener('keydown', function(e) { if (e.key === 'Enter') setupRevIn.focus(); });
  setupRevIn.addEventListener('keydown', function(e) { if (e.key === 'Enter') submitSetup(); });

  // --- Phase form ---
  async function submitPhase() {
    const phase = phaseNameIn.value.trim();
    const typeEl = document.querySelector('input[name="phase-type"]:checked');
    const type = typeEl ? typeEl.value : '';

    if (!phase) {
      showDialogue("Please enter a phase name.", []);
      return;
    }
    if (!type) {
      showDialogue("Please select a phase type.", []);
      return;
    }

    phaseSubmit.disabled = true;
    try {
      const r = await fetch('/api/start-phase', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phase: phase, type: type }),
      });
      const data = await r.json();

      if (r.ok) {
        activeForm = null;
        handleNewState(data);
        showDialogue('Phase "' + phase + '" started! The lead agent is up first.', []);
        updateVisibility();
      } else if (r.status === 409) {
        showDialogue(data.error || "There's already an active handoff. Finish or abort it first.", []);
      } else {
        showDialogue(data.error || "Something went wrong.", []);
      }
    } catch (e) {
      showDialogue("Failed to connect to the server.", []);
    } finally {
      phaseSubmit.disabled = false;
    }
  }

  phaseSubmit.addEventListener('click', submitPhase);
  phaseNameIn.addEventListener('keydown', function(e) { if (e.key === 'Enter') submitPhase(); });

  // --- Status badges ---
  function statusBadge(status) {
    const map = {
      'ready': 'badge-yellow', 'working': 'badge-blue', 'done': 'badge-green',
      'escalated': 'badge-red', 'aborted': 'badge-gray', 'approved': 'badge-green',
    };
    return '<span class="badge ' + (map[status] || 'badge-gray') + '">' + escHtml(status || '--') + '</span>';
  }

  function agentName(role) {
    if (!agentConfig.agents) return role;
    const agent = agentConfig.agents[role];
    return agent ? agent.name : role;
  }

  function updateStatusPanel(state) {
    stPhase.textContent  = state.phase  || '--';
    stType.textContent   = state.type   || '--';
    stRound.textContent  = state.round  || '--';
    const turn = state.turn || '--';
    stTurn.innerHTML = escHtml(turn) + ' <span style="color:var(--text-dim)">(' + escHtml(agentName(turn)) + ')</span>';
    stStatus.innerHTML = statusBadge(state.status);
    stResult.innerHTML = state.result ? statusBadge(state.result) : '--';
  }

  function updateControls(state) {
    const status = state.status || '';
    const active = status === 'ready' || status === 'working';
    const notDone = status !== 'done' && status !== 'aborted';
    btnApprove.disabled  = !active;
    btnChanges.disabled  = !active;
    btnEscalate.disabled = !notDone;
    btnAbort.disabled    = !notDone;
  }

  function updateTimeline(state) {
    const history = state.history || [];
    if (history.length === 0) return;
    const entries = history.slice(-10).reverse();
    timelineEl.innerHTML = entries.map(function(h) {
      const ts = h.timestamp ? formatTime(h.timestamp) : '--:--';
      const turn = h.turn || '?';
      const status = h.status || '?';
      return '<div class="timeline-entry">' +
        '<span class="timeline-dot"></span>' +
        '<span class="timeline-time">' + ts + '</span>' +
        '<span class="timeline-msg">' + escHtml(agentName(turn)) + ' &rarr; ' + escHtml(status) + ' (' + escHtml(turn) + ' turn)</span>' +
        '</div>';
    }).join('');
  }

  function formatTime(iso) {
    try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
    catch (e) { return '--:--'; }
  }

  // --- Cycle document ---
  cycleToggle.addEventListener('click', function() {
    cycleOpen = !cycleOpen;
    cycleToggle.classList.toggle('open', cycleOpen);
    cycleContent.classList.toggle('visible', cycleOpen);
    cycleSelect.style.display = cycleOpen ? 'block' : 'none';
    if (cycleOpen && cycleSelect.options.length <= 1) loadCycleList();
  });

  cycleSelect.addEventListener('change', function() {
    if (cycleSelect.value) loadCycleDoc(cycleSelect.value);
  });

  function loadCycleList() {
    fetch('/api/cycles').then(function(r) { return r.json(); }).then(function(files) {
      while (cycleSelect.options.length > 1) cycleSelect.remove(1);
      files.forEach(function(f) {
        var opt = document.createElement('option');
        opt.value = f;
        opt.textContent = f.replace(/_/g, ' ').replace('.md', '');
        cycleSelect.appendChild(opt);
      });
      autoSelectCycle();
    }).catch(function() {});
  }

  function autoSelectCycle() {
    if (!currentState.phase || !currentState.type) return;
    var target = currentState.phase + '_' + currentState.type + '_cycle.md';
    for (var i = 0; i < cycleSelect.options.length; i++) {
      if (cycleSelect.options[i].value === target) {
        cycleSelect.selectedIndex = i;
        loadCycleDoc(target);
        break;
      }
    }
  }

  function loadCycleDoc(filename) {
    fetch('/api/cycle/' + encodeURIComponent(filename))
      .then(function(r) { if (!r.ok) throw new Error(); return r.text(); })
      .then(function(md) { cycleContent.innerHTML = renderMarkdown(md); })
      .catch(function() { cycleContent.innerHTML = '<em>Could not load document.</em>'; });
    // Also load structured rounds view
    loadRounds(filename);
  }

  function renderMarkdown(md) {
    return md
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/^### (.+)$/gm, '<h3>$1</h3>')
      .replace(/^## (.+)$/gm, '<h2>$1</h2>')
      .replace(/^# (.+)$/gm, '<h1>$1</h1>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/^---+$/gm, '<hr>')
      .replace(/\n/g, '<br>');
  }

  // --- Structured rounds viewer ---
  function loadRounds(filename) {
    fetch('/api/rounds/' + encodeURIComponent(filename))
      .then(function(r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function(data) { renderRounds(data); })
      .catch(function() {
        var container = document.getElementById('rounds-content');
        if (container) container.innerHTML = '<em>Could not load rounds.</em>';
      });
  }

  function renderRounds(data) {
    var container = document.getElementById('rounds-content');
    if (!container) return;

    // Use pre-rendered HTML from the backend (format_rounds_html)
    if (data.html) {
      container.innerHTML = data.html;
      container.classList.remove('hidden');
      return;
    }

    var rounds = data.rounds || [];
    if (rounds.length === 0) {
      container.innerHTML = '<em>No rounds recorded yet.</em>';
      container.classList.remove('hidden');
      return;
    }

    container.innerHTML = '<em>No rounds recorded yet.</em>';
    container.classList.remove('hidden');
  }

  // --- Control actions ---
  async function postState(updates) {
    try {
      const r = await fetch('/api/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      if (r.ok) handleNewState(await r.json());
    } catch (e) { console.error('Failed to update state:', e); }
  }

  btnApprove.addEventListener('click', function() {
    postState({ status: 'done', result: 'approved' });
  });
  btnChanges.addEventListener('click', function() {
    const otherTurn = currentState.turn === 'lead' ? 'reviewer' : 'lead';
    postState({ status: 'ready', turn: otherTurn, round: (currentState.round || 1) + 1 });
  });
  btnEscalate.addEventListener('click', function() { postState({ status: 'escalated' }); });
  btnAbort.addEventListener('click', function() {
    abortReason.value = '';
    abortModal.classList.add('visible');
    abortReason.focus();
  });
  abortCancel.addEventListener('click', function() { abortModal.classList.remove('visible'); });
  abortConfirm.addEventListener('click', function() {
    abortModal.classList.remove('visible');
    postState({ status: 'aborted', reason: abortReason.value.trim() || 'Aborted via dashboard' });
  });
  abortModal.addEventListener('click', function(e) {
    if (e.target === abortModal) abortModal.classList.remove('visible');
  });

  // --- Escalation choice buttons ---
  btnAgreeLead.addEventListener('click', function() {
    postState({ status: 'done', result: 'agree_with_lead' });
  });
  btnAgreeReviewer.addEventListener('click', function() {
    postState({ status: 'done', result: 'agree_with_reviewer' });
  });
  btnDefer.addEventListener('click', function() {
    postState({ status: 'done', result: 'deferred' });
  });

  // --- Phase map ---
  function loadPhaseMap() {
    fetch('/api/phases')
      .then(function(r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function(phases) { renderPhaseMap(phases); })
      .catch(function() {
        if (phaseMapList) phaseMapList.innerHTML = '<em>Could not load phases.</em>';
      });
  }

  function renderPhaseMap(phases) {
    if (!phaseMapList) return;
    if (!phases || phases.length === 0) {
      phaseMapList.innerHTML = '<li class="phase-map-item"><span style="color:var(--text-dim)">No phases yet.</span></li>';
      return;
    }

    var html = '';
    for (var i = 0; i < phases.length; i++) {
      var p = phases[i];
      var indicatorClass = 'pending';
      if (p.status === 'done' || p.result === 'approved') {
        indicatorClass = 'done';
      } else if (p.status === 'aborted') {
        indicatorClass = 'aborted';
      } else if (p.status === 'working' || p.status === 'ready' || p.status === 'escalated') {
        indicatorClass = 'active';
      }

      html += '<li class="phase-map-item">';
      html += '<span class="phase-indicator ' + indicatorClass + '"></span>';
      html += '<span class="phase-map-name">' + escHtml(p.phase || p.name || ('Phase ' + (i + 1))) + '</span>';
      if (p.type) {
        html += '<span class="phase-map-type">' + escHtml(p.type) + '</span>';
      }
      html += '</li>';
    }

    phaseMapList.innerHTML = html;
  }

  // --- State transition ---
  function handleNewState(state) {
    lastUpdatedAt = state.updated_at || null;
    currentState = state;
    updateStatusPanel(state);
    updateControls(state);
    updateTimeline(state);
    applySaloonState(state);
    updateVisibility();
  }

  // --- Polling ---
  let pollOk = false;
  async function poll() {
    try {
      const [stateR, configR] = await Promise.all([
        fetch('/api/state'),
        fetch('/api/config'),
      ]);
      if (stateR.ok && configR.ok) {
        if (!pollOk) { pollOk = true; connDot.className = 'conn-dot ok'; connText.textContent = 'Connected'; }
        agentConfig = await configR.json();
        handleNewState(await stateR.json());
        loadPhaseMap();
      } else {
        setDisconnected();
      }
    } catch (e) { setDisconnected(); }
  }
  function setDisconnected() {
    pollOk = false; connDot.className = 'conn-dot err'; connText.textContent = 'Disconnected';
  }

  async function init() {
    try {
      const [stateR, configR] = await Promise.all([
        fetch('/api/state'),
        fetch('/api/config'),
      ]);
      if (configR.ok) agentConfig = await configR.json();
      if (stateR.ok) {
        pollOk = true;
        connDot.className = 'conn-dot ok';
        connText.textContent = 'Connected';
        handleNewState(await stateR.json());
      }
    } catch (e) {}
    updateVisibility();
    loadPhaseMap();
  }

  init();
  setInterval(poll, 2000);

})();
