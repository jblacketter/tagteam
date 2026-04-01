(function() {
  'use strict';

  // --- DOM refs ---
  var bannerMayor    = document.getElementById('banner-mayor');
  var bannerRabbit   = document.getElementById('banner-rabbit');
  var bannerWatcher  = document.getElementById('banner-watcher');
  var clockBody      = document.getElementById('clock-body');
  var pendulumWrap   = document.getElementById('pendulum-wrap');
  var cuckooWrap     = document.getElementById('cuckoo-wrap');
  var bannerBackdrop = document.getElementById('banner-backdrop');
  var dialoguePanel  = document.getElementById('dialogue-panel');

  var setupFormEl   = document.getElementById('setup-form');
  var setupLeadIn   = document.getElementById('setup-lead');
  var setupRevIn    = document.getElementById('setup-reviewer');
  var setupSubmit   = document.getElementById('setup-submit');

  var phaseFormEl   = document.getElementById('phase-form');
  var phaseNameIn   = document.getElementById('phase-name');
  var phaseSubmit   = document.getElementById('phase-submit');

  var statusPanel   = document.getElementById('status-panel');
  var controlsPanel = document.getElementById('controls-panel');
  var timelineSec   = document.getElementById('timeline-section');
  var cycleSec      = document.getElementById('cycle-section');

  var stPhase  = document.getElementById('st-phase');
  var stType   = document.getElementById('st-type');
  var stRound  = document.getElementById('st-round');
  var stTurn   = document.getElementById('st-turn');
  var stStatus = document.getElementById('st-status');
  var stResult = document.getElementById('st-result');

  var btnApprove  = document.getElementById('btn-approve');
  var btnChanges  = document.getElementById('btn-changes');
  var btnEscalate = document.getElementById('btn-escalate');
  var btnAbort    = document.getElementById('btn-abort');

  var connDot  = document.getElementById('conn-dot');
  var connText = document.getElementById('conn-text');

  var timelineEl   = document.getElementById('timeline');
  var cycleToggle  = document.getElementById('cycle-toggle');
  var cycleSelect  = document.getElementById('cycle-select');
  var cycleContent = document.getElementById('cycle-content');

  var abortModal   = document.getElementById('abort-modal');
  var abortReason  = document.getElementById('abort-reason');
  var abortCancel  = document.getElementById('abort-cancel');
  var abortConfirm = document.getElementById('abort-confirm');

  var escalationChoices = document.getElementById('escalation-choices');
  var btnAgreeLead      = document.getElementById('btn-agree-lead');
  var btnAgreeReviewer  = document.getElementById('btn-agree-reviewer');
  var btnDefer          = document.getElementById('btn-defer');

  var phaseMapPanel = document.getElementById('phase-map-panel');
  var phaseMapList  = document.getElementById('phase-map-list');

  // --- State ---
  var currentState = {};
  var prevState = {};
  var agentConfig = {};
  var lastUpdatedAt = null;
  var cycleOpen = false;
  var activeForm = null; // null, 'setup', 'phase'
  var dialogueCtrl = null;
  var bannerRendered = false;

  // Multi-character setup state (persisted to localStorage)
  var setupState = {
    step: null,       // null | 'mayor' | 'bartender' | 'watcher' | 'complete'
    leadName: null,
    reviewerName: null,
    wantsTmux: false,
  };

  var SETUP_STORAGE_KEY = 'ai-handoff-setup';

  function persistSetupState() {
    try { localStorage.setItem(SETUP_STORAGE_KEY, JSON.stringify(setupState)); } catch (e) {}
  }

  function restoreSetupState() {
    try {
      var saved = localStorage.getItem(SETUP_STORAGE_KEY);
      if (saved) {
        var parsed = JSON.parse(saved);
        if (parsed && parsed.step && parsed.step !== 'complete') {
          setupState = parsed;
          return true;
        }
      }
    } catch (e) {}
    return false;
  }

  function clearSetupState() {
    setupState = { step: null, leadName: null, reviewerName: null, wantsTmux: false };
    try { localStorage.removeItem(SETUP_STORAGE_KEY); } catch (e) {}
  }

  // --- Initialize dialogue controller ---
  dialogueCtrl = new Conversation.DialogueController(dialoguePanel);

  // --- Render banner scene ---
  function renderBanner() {
    if (bannerRendered) return;
    bannerRendered = true;

    // Backdrop
    bannerBackdrop.innerHTML = Sprites.renderSaloonBackdrop();

    // Characters
    updateBannerCharacters('');

    // Clock
    clockBody.innerHTML = Sprites.renderClock('');
    pendulumWrap.innerHTML = Sprites.renderPendulum();
    cuckooWrap.innerHTML = Sprites.renderCuckoo();

    // Mayor glow on welcome
    bannerMayor.classList.add('mayor-glow');
  }

  // --- Character glow management ---
  function setCharGlow(charName) {
    // Remove all glows
    bannerMayor.classList.remove('mayor-glow', 'char-glow');
    bannerRabbit.classList.remove('char-glow');
    if (bannerWatcher) bannerWatcher.classList.remove('char-glow');
    // Add glow to target
    if (charName === 'mayor') bannerMayor.classList.add('char-glow');
    else if (charName === 'rabbit' || charName === 'bartender') bannerRabbit.classList.add('char-glow');
    else if (charName === 'watcher' && bannerWatcher) bannerWatcher.classList.add('char-glow');
  }

  function clearAllGlows() {
    bannerMayor.classList.remove('mayor-glow', 'char-glow');
    bannerRabbit.classList.remove('char-glow');
    if (bannerWatcher) bannerWatcher.classList.remove('char-glow');
  }

  function updateBannerCharacters(status) {
    var state = '';
    if (status === 'working') state = 'working';
    else if (status === 'done') state = 'approved';
    else if (status === 'escalated') state = 'escalated';
    else if (status === 'aborted') state = 'aborted';

    bannerMayor.innerHTML = Sprites.renderMayor(state);
    bannerRabbit.innerHTML = Sprites.renderRabbit(state);
    if (bannerWatcher) bannerWatcher.innerHTML = Sprites.renderWatcher(state);
    clockBody.innerHTML = Sprites.renderClock(state);

    // Pendulum animation state
    if (status === 'escalated' || status === 'aborted') {
      pendulumWrap.classList.add('paused');
    } else {
      pendulumWrap.classList.remove('paused');
    }
  }

  function triggerCuckoo() {
    cuckooWrap.classList.remove('hidden');
    cuckooWrap.classList.add('cuckoo-active');
    setTimeout(function() {
      cuckooWrap.classList.remove('cuckoo-active');
      setTimeout(function() {
        cuckooWrap.classList.add('hidden');
      }, 300);
    }, 2000);
  }

  renderBanner();

  // --- Mode computation ---
  function getMode() {
    if (!agentConfig || !agentConfig.agents) return 'welcome';
    var status = currentState && currentState.status;
    if (!status || status === 'done' || status === 'aborted') return 'idle';
    return 'active'; // ready, working, escalated
  }

  function updateVisibility() {
    var mode = getMode();

    // If mode no longer matches form, close it
    if (mode !== 'welcome' && activeForm === 'setup') activeForm = null;
    if (mode === 'active' && activeForm === 'phase') activeForm = null;

    // Setup form
    show(setupFormEl, activeForm === 'setup');

    // Phase form
    show(phaseFormEl, activeForm === 'phase');

    // Status panel
    show(statusPanel, mode !== 'welcome' && activeForm !== 'setup' && activeForm !== 'phase');

    // Controls
    show(controlsPanel, mode === 'active');

    // Escalation choices
    var isEscalated = currentState.status === 'escalated';
    show(escalationChoices, mode === 'active' && isEscalated);

    // Timeline
    var hasHistory = currentState.history && currentState.history.length > 0;
    show(timelineSec, mode !== 'welcome' && hasHistory);

    // Cycle viewer
    show(cycleSec, mode !== 'welcome');

    // Phase map
    show(phaseMapPanel, mode !== 'welcome');

    // Character glow for setup guidance
    if (mode === 'welcome' || (setupState.step && setupState.step !== 'complete')) {
      if (!setupState.step) {
        // Initial state — glow Mayor to attract click
        setCharGlow('mayor');
      }
      // During setup flow, glow is managed by click handlers
    } else {
      clearAllGlows();
    }
  }

  function show(el, visible) {
    if (!el) return;
    el.classList.toggle('hidden', !visible);
  }

  // --- Character click handlers ---

  function onMayorClick() {
    if (dialogueCtrl.visible) {
      dialogueCtrl.hide();
      return;
    }

    // During setup flow, redirect to the right character
    if (setupState.step === 'bartender') {
      dialogueCtrl.playLines([{ speaker: 'mayor', text: "Go talk to the Bartender \u2014 they're waiting for you!" }]);
      return;
    }
    if (setupState.step === 'watcher') {
      // Watcher step is optional — skip it and proceed to idle
      clearSetupState();
      // Fall through to idle mode below
    }

    var mode = getMode();

    if (mode === 'welcome') {
      // Multi-character setup flow — Mayor's part
      setupState.step = 'mayor';
      clearAllGlows();
      dialogueCtrl.playScript(Conversation.SETUP_FLOW_MAYOR, function(inputs) {
        setupState.leadName = inputs.sf_mayor_lead || 'Claude';
        setupState.step = 'bartender';
        persistSetupState();
        setCharGlow('bartender');
        // Handoff message — state is already set so clicking Bartender will work
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: "Great! Now go click on the Bartender \u2014 they handle the review side of things." },
        ]);
      });
    } else if (mode === 'idle') {
      var idleScript = [
        {
          id: 'idle_menu',
          speaker: 'mayor',
          type: 'choice',
          text: getMayorIdleText(),
          choices: [
            { label: 'Start new phase', next: 'start_phase' },
            { label: 'How it works', next: 'how' },
            { label: 'Never mind', next: null },
          ],
        },
        {
          id: 'start_phase',
          speaker: 'mayor',
          type: 'dialogue',
          text: "Fill in the phase details below and we'll get rolling!",
          next: null,
        },
        {
          id: 'how',
          speaker: 'mayor',
          type: 'dialogue',
          text: "The Handoff Saloon orchestrates work between two AI agents. A Lead does the work, a Reviewer checks it, and you \u2014 the human \u2014 are the arbiter. They go back and forth until the work is approved.",
          next: 'how_rabbit',
        },
        {
          id: 'how_rabbit',
          speaker: 'rabbit',
          type: 'dialogue',
          text: "I keep track of every round and make sure the handoffs run smooth. Click the Mayor when you're ready to start a phase!",
          next: null,
        },
      ];

      dialogueCtrl.playScript(idleScript, function(inputs, lastNodeId) {
        if (lastNodeId === 'start_phase') {
          activeForm = 'phase';
          updateVisibility();
        }
      });
    } else if (mode === 'active') {
      dialogueCtrl.playScript([{
        id: 'active_info',
        speaker: 'mayor',
        type: 'dialogue',
        text: getMayorActiveText(),
        next: null,
      }]);
    }
  }

  function onRabbitClick() {
    if (dialogueCtrl.visible) {
      dialogueCtrl.hide();
      // During setup, if it's the bartender's turn, fall through to start bartender flow
      if (setupState.step !== 'bartender') return;
    }

    // Check setup flow state first (mode may be 'idle' after config save)
    if (setupState.step === 'bartender') {
      clearAllGlows();
      dialogueCtrl.playScript(Conversation.SETUP_FLOW_BARTENDER, function(inputs) {
        setupState.reviewerName = inputs.sf_bart_reviewer || 'Codex';
        setupState.step = 'watcher';
        persistSetupState();
        // Save config silently (don't interrupt setup flow with "all set" dialogue)
        saveConfigSilent(setupState.leadName, setupState.reviewerName);
        setCharGlow('watcher');
        // Handoff message — state is already set so clicking Watcher will work
        dialogueCtrl.playLines([
          { speaker: 'rabbit', text: "All set on my end! Go talk to the Watcher if you want automated monitoring, or click the Mayor to get started." },
        ]);
      });
      return;
    }

    var mode = getMode();

    if (mode === 'welcome') {
      dialogueCtrl.playLines([
        { speaker: 'rabbit', text: "Talk to the Mayor first \u2014 they'll get you started!" },
      ]);
    } else if (mode === 'idle') {
      var idleScript = [
        {
          id: 'bart_idle',
          speaker: 'rabbit',
          type: 'choice',
          text: getRabbitIdleText(),
          choices: [
            { label: 'Review history', next: 'bart_history' },
            { label: 'How reviews work', next: 'bart_explain' },
            { label: 'Never mind', next: null },
          ],
        },
        {
          id: 'bart_history',
          speaker: 'rabbit',
          type: 'dialogue',
          text: currentState.phase
            ? "Last phase was \"" + currentState.phase + "\" \u2014 " + (currentState.result === 'approved' ? 'approved!' : 'status: ' + (currentState.status || 'unknown')) + ". Check the cycle viewer below for details."
            : "No review history yet. Start a phase and I'll track every round.",
          next: null,
        },
        {
          id: 'bart_explain',
          speaker: 'rabbit',
          type: 'dialogue',
          text: "The Lead submits work, I help track the review. The Reviewer gives feedback or approves. If they can't agree after 5 rounds, it escalates to you \u2014 the arbiter.",
          next: null,
        },
      ];
      dialogueCtrl.playScript(idleScript);
    } else if (mode === 'active') {
      dialogueCtrl.playScript([{
        id: 'bart_active',
        speaker: 'rabbit',
        type: 'dialogue',
        text: getRabbitActiveText(),
        next: null,
      }]);
    }
  }

  function onWatcherClick() {
    if (dialogueCtrl.visible) {
      dialogueCtrl.hide();
      // During setup, if it's the watcher's turn, fall through to start watcher flow
      if (setupState.step !== 'watcher') return;
    }

    // Check setup flow state first (mode may be 'idle' after config save)
    if (setupState.step === 'watcher') {
      clearAllGlows();
      dialogueCtrl.playScript(Conversation.SETUP_FLOW_WATCHER, function(inputs, lastNodeId) {
        setupState.wantsTmux = (lastNodeId === 'sf_watch_tmux');
        setupState.step = 'complete';
        clearSetupState();
        // Guide user to start their first phase
        setCharGlow('mayor');
      });
      return;
    }

    var mode = getMode();

    if (mode === 'welcome') {
      dialogueCtrl.playLines([
        { speaker: 'watcher', text: "Talk to the Mayor first \u2014 they'll introduce us properly." },
      ]);
    } else if (mode === 'idle') {
      var idleScript = [
        {
          id: 'watch_idle',
          speaker: 'watcher',
          type: 'choice',
          text: "What can I do for you, partner?",
          choices: [
            { label: 'Watcher status', next: 'watch_status' },
            { label: 'Start session', next: 'watch_session' },
            { label: 'Never mind', next: null },
          ],
        },
        {
          id: 'watch_status',
          speaker: 'watcher',
          type: 'dialogue',
          text: "Checking watcher daemon status...",
          next: null,
        },
        {
          id: 'watch_session',
          speaker: 'watcher',
          type: 'dialogue',
          text: "To start a tmux session, run: python -m ai_handoff session start",
          next: null,
        },
      ];

      dialogueCtrl.playScript(idleScript, function(inputs, lastNodeId) {
        if (lastNodeId === 'watch_status') {
          fetchWatcherAndSessionStatus();
        }
      });
    } else if (mode === 'active') {
      // Fetch live daemon + session status for active mode
      fetchWatcherAndSessionStatus();
    }
  }

  function getMayorIdleText() {
    var status = currentState.status;
    var result = currentState.result;
    if (status === 'done' && result === 'approved') {
      return "Phase complete! Ready for the next one?";
    }
    if (status === 'aborted') {
      return "This cycle was aborted. Ready to start fresh?";
    }
    return "Your saloon is ready. What would you like to do?";
  }

  function getMayorActiveText() {
    var status = currentState.status;
    var turn = currentState.turn;
    if (status === 'working') {
      return "An agent is working. Sit tight, partner.";
    }
    if (status === 'ready') {
      return "Handoff ready \u2014 " + agentName(turn) + " is up next.";
    }
    if (status === 'escalated') {
      return "This got escalated. A human needs to step in. Use the controls below.";
    }
    return "The saloon is busy.";
  }

  function getRabbitIdleText() {
    var status = currentState.status;
    var result = currentState.result;
    if (status === 'done' && result === 'approved') {
      return "That last review went well! Anything else?";
    }
    return "No active reviews right now. What can I help with?";
  }

  function getRabbitActiveText() {
    var status = currentState.status;
    var turn = currentState.turn;
    var round = currentState.round || 1;
    if (status === 'escalated') {
      return "Round " + round + " got escalated. The arbiter needs to make the call.";
    }
    return "We're on round " + round + ". " + (turn === 'reviewer' ? "Reviewer's up next." : "Waiting on the Lead.");
  }

  function getWatcherActiveText() {
    var turn = currentState.turn;
    var status = currentState.status;
    if (status === 'ready') {
      return "It's " + agentName(turn) + "'s turn. I'm watching for their response.";
    }
    if (status === 'escalated') {
      return "Cycle is escalated. Agents are standing down until the arbiter decides.";
    }
    return "Monitoring the handoff. All looks normal.";
  }

  // --- Watcher API helpers ---
  function fetchWatcherAndSessionStatus() {
    Promise.all([
      fetch('/api/watcher/status').then(function(r) { return r.json(); }),
      fetch('/api/session/status').then(function(r) { return r.json(); }),
    ]).then(function(results) {
      var watcher = results[0];
      var session = results[1];
      var lines = [];
      lines.push({
        speaker: 'watcher',
        text: watcher.running
          ? "Watcher daemon is running (PID: " + watcher.pid + ")."
          : "Watcher daemon is not running.",
      });
      lines.push({
        speaker: 'watcher',
        text: session.active
          ? 'tmux session "' + session.session + '" is active.'
          : "No tmux session detected. Run: python -m ai_handoff session start",
      });
      dialogueCtrl.playLines(lines);
    }).catch(function() {
      dialogueCtrl.playLines([{ speaker: 'watcher', text: "Couldn't reach the monitoring APIs." }]);
    });
  }

  bannerMayor.addEventListener('click', onMayorClick);
  bannerRabbit.addEventListener('click', onRabbitClick);
  if (bannerWatcher) bannerWatcher.addEventListener('click', onWatcherClick);

  // --- Setup ---

  // Silent config save — used during multi-character setup flow to avoid
  // interrupting the guided sequence with "all set" dialogue.
  async function saveConfigSilent(lead, reviewer) {
    try {
      var r = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lead: lead, reviewer: reviewer }),
      });
      if (r.ok) {
        agentConfig = await r.json();
      } else if (r.status === 409) {
        // Config exists — overwrite silently during setup flow
        var r2 = await fetch('/api/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lead: lead, reviewer: reviewer, overwrite: true }),
        });
        if (r2.ok) agentConfig = await r2.json();
      }
    } catch (e) {
      console.error('Failed to save config silently:', e);
    }
  }

  async function submitSetupFromDialogue(lead, reviewer) {
    try {
      var r = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lead: lead, reviewer: reviewer }),
      });
      var data = await r.json();

      if (r.ok) {
        agentConfig = data;
        activeForm = null;
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: "You're all set! Click me anytime to start a new phase." },
        ]);
        updateVisibility();
      } else if (r.status === 409) {
        dialogueCtrl.playScript([
          {
            id: 'overwrite_q',
            speaker: 'mayor',
            type: 'choice',
            text: "Config already exists. Want to overwrite it?",
            choices: [
              { label: 'Yes, overwrite', next: 'do_overwrite' },
              { label: 'Cancel', next: null },
            ],
          },
          {
            id: 'do_overwrite',
            speaker: 'mayor',
            type: 'dialogue',
            text: "Overwriting...",
            next: null,
          },
        ], function(inputs, lastNodeId) {
          // Only overwrite if they chose "Yes, overwrite" (reached do_overwrite node)
          if (lastNodeId === 'do_overwrite') {
            submitSetupOverwrite(lead, reviewer);
          }
        });
      } else {
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: data.error || "Something went wrong." },
        ]);
      }
    } catch (e) {
      dialogueCtrl.playLines([
        { speaker: 'mayor', text: "Failed to connect to the server." },
      ]);
    }
  }

  async function submitSetupOverwrite(lead, reviewer) {
    try {
      var r = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lead: lead, reviewer: reviewer, overwrite: true }),
      });
      if (r.ok) {
        agentConfig = await r.json();
        activeForm = null;
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: "Config updated! Click me to start a new phase." },
        ]);
        updateVisibility();
      }
    } catch (e) {
      dialogueCtrl.playLines([
        { speaker: 'mayor', text: "Failed to connect to the server." },
      ]);
    }
  }

  // --- Setup form (manual, as fallback) ---
  async function submitSetup() {
    var lead = setupLeadIn.value.trim();
    var reviewer = setupRevIn.value.trim();

    if (!lead || !reviewer) {
      dialogueCtrl.playLines([
        { speaker: 'mayor', text: "Please fill in both agent names." },
      ]);
      return;
    }

    setupSubmit.disabled = true;
    try {
      var r = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lead: lead, reviewer: reviewer }),
      });
      var data = await r.json();

      if (r.ok) {
        agentConfig = data;
        activeForm = null;
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: "You're all set! Click me to start a new phase." },
        ]);
        updateVisibility();
      } else if (r.status === 409) {
        dialogueCtrl.playScript([
          {
            id: 'ow',
            speaker: 'mayor',
            type: 'choice',
            text: "Config already exists \u2014 overwrite?",
            choices: [
              { label: 'Overwrite', next: 'ow_confirm' },
              { label: 'Cancel', next: null },
            ],
          },
          {
            id: 'ow_confirm',
            speaker: 'mayor',
            type: 'dialogue',
            text: "Overwriting...",
            next: null,
          },
        ], function(inputs, lastNodeId) {
          if (lastNodeId === 'ow_confirm') {
            submitSetupOverwrite(lead, reviewer);
          }
        });
      } else {
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: data.error || "Something went wrong." },
        ]);
      }
    } catch (e) {
      dialogueCtrl.playLines([
        { speaker: 'mayor', text: "Failed to connect to the server." },
      ]);
    } finally {
      setupSubmit.disabled = false;
    }
  }

  setupSubmit.addEventListener('click', submitSetup);
  setupLeadIn.addEventListener('keydown', function(e) { if (e.key === 'Enter') setupRevIn.focus(); });
  setupRevIn.addEventListener('keydown', function(e) { if (e.key === 'Enter') submitSetup(); });

  // --- Phase form ---
  async function submitPhase() {
    var phase = phaseNameIn.value.trim();
    var typeEl = document.querySelector('input[name="phase-type"]:checked');
    var type = typeEl ? typeEl.value : '';

    if (!phase) {
      dialogueCtrl.playLines([
        { speaker: 'mayor', text: "Please enter a phase name." },
      ]);
      return;
    }
    if (!type) {
      dialogueCtrl.playLines([
        { speaker: 'mayor', text: "Please select a phase type." },
      ]);
      return;
    }

    phaseSubmit.disabled = true;
    try {
      var r = await fetch('/api/start-phase', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phase: phase, type: type }),
      });
      var data = await r.json();

      if (r.ok) {
        activeForm = null;
        handleNewState(data);
        triggerCuckoo();
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: 'Phase "' + phase + '" started! The lead agent is up first.' },
          { speaker: 'rabbit', text: "I've got my notebook ready. Let's see what they come up with!" },
        ]);
        updateVisibility();
      } else if (r.status === 409) {
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: data.error || "There's already an active handoff. Finish or abort it first." },
        ]);
      } else {
        dialogueCtrl.playLines([
          { speaker: 'mayor', text: data.error || "Something went wrong." },
        ]);
      }
    } catch (e) {
      dialogueCtrl.playLines([
        { speaker: 'mayor', text: "Failed to connect to the server." },
      ]);
    } finally {
      phaseSubmit.disabled = false;
    }
  }

  phaseSubmit.addEventListener('click', submitPhase);
  phaseNameIn.addEventListener('keydown', function(e) { if (e.key === 'Enter') submitPhase(); });

  // --- Status badges ---
  function statusBadge(status) {
    var map = {
      'ready': 'badge-yellow', 'working': 'badge-blue', 'done': 'badge-green',
      'escalated': 'badge-red', 'aborted': 'badge-gray', 'approved': 'badge-green',
    };
    return '<span class="badge ' + (map[status] || 'badge-gray') + '">' + escHtml(status || '--') + '</span>';
  }

  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function agentName(role) {
    if (!agentConfig.agents) return role;
    var agent = agentConfig.agents[role];
    return agent ? agent.name : role;
  }

  function updateStatusPanel(state) {
    stPhase.textContent  = state.phase  || '--';
    stType.textContent   = state.type   || '--';
    stRound.textContent  = state.round  || '--';
    var turn = state.turn || '--';
    stTurn.innerHTML = escHtml(turn) + ' <span style="color:var(--text-dim)">(' + escHtml(agentName(turn)) + ')</span>';
    stStatus.innerHTML = statusBadge(state.status);
    stResult.innerHTML = state.result ? statusBadge(state.result) : '--';
  }

  function updateControls(state) {
    var status = state.status || '';
    var active = status === 'ready' || status === 'working';
    var notDone = status !== 'done' && status !== 'aborted';
    btnApprove.disabled  = !active;
    btnChanges.disabled  = !active;
    btnEscalate.disabled = !notDone;
    btnAbort.disabled    = !notDone;
  }

  function updateTimeline(state) {
    var history = state.history || [];
    if (history.length === 0) return;
    var entries = history.slice(-10).reverse();
    timelineEl.innerHTML = entries.map(function(h) {
      var ts = h.timestamp ? formatTime(h.timestamp) : '--:--';
      var turn = h.turn || '?';
      var status = h.status || '?';
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

  // --- State-change dialogue ---
  function playStateDialogue(state, prev) {
    var lines = Conversation.buildStateDialogue(state, prev);
    if (lines.length > 0 && !dialogueCtrl.visible) {
      dialogueCtrl.playLines(lines);
    }
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
        opt.textContent = f.replace(/_/g, ' ').replace('.md', '').replace('_cycle', '');
        cycleSelect.appendChild(opt);
      });
      autoSelectCycle();
    }).catch(function() {});
  }

  function autoSelectCycle() {
    if (!currentState.phase || !currentState.type) return;
    var target = currentState.phase + '_' + currentState.type;
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

  // --- Error banner ---
  var errorBanner = document.createElement('div');
  errorBanner.id = 'error-banner';
  errorBanner.style.cssText = 'display:none;position:fixed;top:0;left:0;right:0;padding:8px 16px;background:#c0392b;color:#fff;text-align:center;z-index:9999;font-size:14px;';
  document.body.prepend(errorBanner);

  function showError(msg) {
    errorBanner.textContent = msg;
    errorBanner.style.display = 'block';
  }
  function clearError() {
    errorBanner.style.display = 'none';
  }

  // --- Control actions ---
  async function postState(updates) {
    try {
      var r = await fetch('/api/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      });
      if (r.ok) {
        clearError();
        handleNewState(await r.json());
      } else {
        var detail = await r.text().catch(function() { return ''; });
        showError('Failed to update state: ' + (r.status) + ' ' + (detail || r.statusText));
      }
    } catch (e) {
      showError('Network error: could not reach server.');
      console.error('Failed to update state:', e);
    }
  }

  btnApprove.addEventListener('click', function() {
    postState({ status: 'done', result: 'approved' });
  });
  btnChanges.addEventListener('click', function() {
    var otherTurn = currentState.turn === 'lead' ? 'reviewer' : 'lead';
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
    prevState = Object.assign({}, currentState);
    lastUpdatedAt = state.updated_at || null;
    currentState = state;

    updateStatusPanel(state);
    updateControls(state);
    updateTimeline(state);
    updateBannerCharacters(state.status || '');
    updateVisibility();

    // State-change dialogue
    if (prevState.status && prevState.status !== state.status) {
      playStateDialogue(state, prevState);
      // Trigger cuckoo on significant transitions
      if (state.status === 'done' || state.status === 'escalated') {
        triggerCuckoo();
      }
    }
  }

  // --- Polling with exponential backoff ---
  var pollOk = false;
  var pollDelay = 2000;
  var POLL_MIN = 2000;
  var POLL_MAX = 30000;
  var pollTimer = null;

  async function poll() {
    try {
      var results = await Promise.all([
        fetch('/api/state'),
        fetch('/api/config'),
      ]);
      var stateR = results[0];
      var configR = results[1];
      if (stateR.ok && configR.ok) {
        if (!pollOk) {
          pollOk = true;
          connDot.className = 'conn-dot ok';
          connText.textContent = 'Connected';
          clearError();
        }
        pollDelay = POLL_MIN;
        agentConfig = await configR.json();
        handleNewState(await stateR.json());
        loadPhaseMap();
      } else {
        setDisconnected('Server error (' + (stateR.status || configR.status) + ')');
      }
    } catch (e) {
      setDisconnected('Reconnecting...');
    }
    schedulePoll();
  }

  function setDisconnected(msg) {
    pollOk = false;
    connDot.className = 'conn-dot err';
    connText.textContent = msg || 'Disconnected';
    pollDelay = Math.min(pollDelay * 2, POLL_MAX);
  }

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, pollDelay);
  }

  async function init() {
    try {
      var results = await Promise.all([
        fetch('/api/state'),
        fetch('/api/config'),
      ]);
      var stateR = results[0];
      var configR = results[1];
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

    // Always attempt to restore setup state first, then decide what to do
    var hasRestoredSetup = restoreSetupState();
    var mode = getMode();
    var inSetupFlow = hasRestoredSetup && setupState.step && setupState.step !== 'complete';

    if (inSetupFlow) {
      // Resume mid-flow: glow the character the user needs to click next
      if (setupState.step === 'bartender') {
        setCharGlow('bartender');
      } else if (setupState.step === 'watcher') {
        setCharGlow('watcher');
      } else if (setupState.step === 'mayor') {
        setCharGlow('mayor');
      }
    } else if (mode === 'welcome') {
      // Fresh start — auto-play Mayor intro
      setTimeout(function() {
        setupState.step = 'mayor';
        persistSetupState();
        dialogueCtrl.playScript(Conversation.SETUP_FLOW_MAYOR, function(inputs) {
          setupState.leadName = inputs.sf_mayor_lead || 'Claude';
          setupState.step = 'bartender';
          persistSetupState();
          setCharGlow('bartender');
          // Handoff message — state is already set so clicking Bartender will work
          dialogueCtrl.playLines([
            { speaker: 'mayor', text: "Great! Now go click on the Bartender \u2014 they handle the review side of things." },
          ]);
        });
      }, 500);
    } else {
      // Not in setup — clear any stale state
      clearSetupState();
    }
  }

  init();
  schedulePoll();

})();
