/**
 * conversation.js — Dialogue Engine
 *
 * Ported from Python TUI: conversation.py, dialogue.py, conversations/intro.py, conversations/transitions.py
 * Provides ConversationEngine, TypewriterEffect, DialogueController, scripts, and transitions.
 */

/* global Sprites */
/* exported Conversation */
var Conversation = (function() {
  'use strict';

  // =========================================================================
  // ConversationEngine — state machine that walks script nodes
  // =========================================================================
  function ConversationEngine(script, callbacks) {
    this.script = script;
    this.nodes = {};
    this.current = null;
    this.lastNodeId = null;
    this.inputs = {};
    this.cb = callbacks || {};

    // Index nodes by id
    for (var i = 0; i < script.length; i++) {
      this.nodes[script[i].id] = script[i];
    }
  }

  ConversationEngine.prototype.start = function() {
    if (this.script.length > 0) {
      this.current = this.script[0];
      this._show(this.current);
    }
  };

  ConversationEngine.prototype.advance = function(nextId) {
    if (!this.current) return;
    this.lastNodeId = this.current.id;
    var nid = nextId || this.current.next;
    if (!nid) {
      this.current = null;
      if (this.cb.onComplete) this.cb.onComplete(this.inputs, this.lastNodeId);
      return;
    }
    this.current = this.nodes[nid] || null;
    if (this.current) {
      this._show(this.current);
    } else {
      if (this.cb.onComplete) this.cb.onComplete(this.inputs, this.lastNodeId);
    }
  };

  ConversationEngine.prototype.handleChoice = function(index) {
    if (!this.current || this.current.type !== 'choice') return;
    var choice = this.current.choices[index];
    if (choice) {
      this.advance(choice.next);
    }
  };

  ConversationEngine.prototype.handleInput = function(text) {
    if (!this.current || this.current.type !== 'input') return;
    this.inputs[this.current.id] = text;
    this.advance(this.current.next);
  };

  ConversationEngine.prototype._show = function(node) {
    if (node.type === 'dialogue') {
      if (this.cb.onDialogue) this.cb.onDialogue(node);
    } else if (node.type === 'choice') {
      if (this.cb.onChoice) this.cb.onChoice(node);
    } else if (node.type === 'input') {
      if (this.cb.onInput) this.cb.onInput(node);
    }
  };

  // =========================================================================
  // TypewriterEffect — types out text character by character
  // =========================================================================
  function TypewriterEffect(element, text, options) {
    this.element = element;
    this.text = text;
    this.speed = (options && options.speed) || 30; // ms per char
    this.onComplete = (options && options.onComplete) || null;
    this._index = 0;
    this._timer = null;
    this._done = false;
  }

  TypewriterEffect.prototype.start = function() {
    this.element.textContent = '';
    this._index = 0;
    this._done = false;
    this._tick();
  };

  TypewriterEffect.prototype._tick = function() {
    var self = this;
    if (this._done) return;
    if (this._index >= this.text.length) {
      this._finish();
      return;
    }
    this.element.textContent += this.text[this._index];
    this._index++;
    this._timer = setTimeout(function() { self._tick(); }, this.speed);
  };

  TypewriterEffect.prototype.skip = function() {
    if (this._done) return;
    if (this._timer) clearTimeout(this._timer);
    this.element.textContent = this.text;
    this._finish();
  };

  TypewriterEffect.prototype._finish = function() {
    this._done = true;
    if (this._timer) clearTimeout(this._timer);
    if (this.onComplete) this.onComplete();
  };

  TypewriterEffect.prototype.destroy = function() {
    this._done = true;
    if (this._timer) clearTimeout(this._timer);
  };

  // =========================================================================
  // DialogueController — manages the dialogue panel DOM
  // =========================================================================
  function DialogueController(panelEl) {
    this.panel = panelEl;
    this.portraitEl = panelEl.querySelector('.dialogue-portrait');
    this.speakerEl = panelEl.querySelector('.dialogue-speaker');
    this.textEl = panelEl.querySelector('.dialogue-body-text');
    this.actionsEl = panelEl.querySelector('.dialogue-body-actions');
    this.advanceEl = panelEl.querySelector('.dialogue-advance');
    this.engine = null;
    this.typewriter = null;
    this.visible = false;
    this._onAdvanceClick = null;
    this._onPanelClick = null;
  }

  DialogueController.prototype.show = function() {
    this.panel.classList.remove('hidden');
    this.panel.classList.add('dialogue-visible');
    this.visible = true;
  };

  DialogueController.prototype.hide = function() {
    this.panel.classList.add('hidden');
    this.panel.classList.remove('dialogue-visible');
    this.visible = false;
    if (this.typewriter) {
      this.typewriter.destroy();
      this.typewriter = null;
    }
    this._cleanup();
  };

  DialogueController.prototype._cleanup = function() {
    if (this._onAdvanceClick) {
      this.advanceEl.removeEventListener('click', this._onAdvanceClick);
      this._onAdvanceClick = null;
    }
    if (this._onPanelClick) {
      this.panel.removeEventListener('click', this._onPanelClick);
      this._onPanelClick = null;
    }
  };

  DialogueController.prototype.playScript = function(script, onComplete) {
    var self = this;
    this.show();

    this.engine = new ConversationEngine(script, {
      onDialogue: function(node) { self._showDialogue(node); },
      onChoice: function(node) { self._showChoices(node); },
      onInput: function(node) { self._showInput(node); },
      onComplete: function(inputs, lastNodeId) {
        self.hide();
        if (onComplete) onComplete(inputs, lastNodeId);
      },
    });

    this.engine.start();
  };

  DialogueController.prototype.playLines = function(lines, onComplete) {
    // Convert simple [{speaker, text}] array to a script
    var script = [];
    for (var i = 0; i < lines.length; i++) {
      script.push({
        id: 'line_' + i,
        speaker: lines[i].speaker,
        type: 'dialogue',
        text: lines[i].text,
        next: i < lines.length - 1 ? 'line_' + (i + 1) : null,
      });
    }
    this.playScript(script, onComplete);
  };

  DialogueController.prototype._showDialogue = function(node) {
    var self = this;
    this._cleanup();

    // Portrait
    var speaker = node.speaker || 'mayor';
    this.portraitEl.innerHTML = Sprites.renderPortrait(speaker);
    this.speakerEl.textContent = capitalize(speaker);

    // Clear actions
    this.actionsEl.innerHTML = '';
    this.actionsEl.style.display = 'none';

    // Show advance button
    this.advanceEl.style.display = '';
    this.advanceEl.textContent = '\u25B6';
    this.advanceEl.classList.add('dialogue-advance-blink');

    // Typewriter
    if (this.typewriter) this.typewriter.destroy();
    var typingDone = false;
    this.typewriter = new TypewriterEffect(this.textEl, node.text, {
      speed: 25,
      onComplete: function() { typingDone = true; },
    });
    this.typewriter.start();

    // Click to skip or advance
    this._onPanelClick = function(e) {
      // Don't intercept clicks on action buttons or inputs
      if (e.target.closest('.dialogue-body-actions') || e.target.closest('.dialogue-advance')) return;
      if (!typingDone) {
        self.typewriter.skip();
      }
    };
    this.panel.addEventListener('click', this._onPanelClick);

    this._onAdvanceClick = function() {
      if (!typingDone) {
        self.typewriter.skip();
      } else {
        self.engine.advance();
      }
    };
    this.advanceEl.addEventListener('click', this._onAdvanceClick);
  };

  DialogueController.prototype._showChoices = function(node) {
    var self = this;
    this._cleanup();

    var speaker = node.speaker || 'mayor';
    this.portraitEl.innerHTML = Sprites.renderPortrait(speaker);
    this.speakerEl.textContent = capitalize(speaker);
    this.textEl.textContent = node.text || '';

    // Hide advance
    this.advanceEl.style.display = 'none';

    // Show choices
    this.actionsEl.innerHTML = '';
    this.actionsEl.style.display = '';

    node.choices.forEach(function(choice, idx) {
      var btn = document.createElement('button');
      btn.className = 'dialogue-choice-btn';
      btn.textContent = choice.label;
      btn.addEventListener('click', function() {
        self.engine.handleChoice(idx);
      });
      self.actionsEl.appendChild(btn);
    });
  };

  DialogueController.prototype._showInput = function(node) {
    var self = this;
    this._cleanup();

    var speaker = node.speaker || 'mayor';
    this.portraitEl.innerHTML = Sprites.renderPortrait(speaker);
    this.speakerEl.textContent = capitalize(speaker);
    this.textEl.textContent = node.prompt || node.text || '';

    // Hide advance
    this.advanceEl.style.display = 'none';

    // Show input field
    this.actionsEl.innerHTML = '';
    this.actionsEl.style.display = '';

    var wrapper = document.createElement('div');
    wrapper.className = 'dialogue-input-wrapper';

    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'dialogue-input-field';
    input.placeholder = node.placeholder || '';

    var submit = document.createElement('button');
    submit.className = 'dialogue-input-submit';
    submit.textContent = 'OK';

    function doSubmit() {
      var val = input.value.trim();
      if (!val && node.default) val = node.default;
      if (val) self.engine.handleInput(val);
    }

    submit.addEventListener('click', doSubmit);
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') doSubmit();
    });

    wrapper.appendChild(input);
    wrapper.appendChild(submit);
    self.actionsEl.appendChild(wrapper);

    setTimeout(function() { input.focus(); }, 50);
  };

  function capitalize(s) {
    return s.charAt(0).toUpperCase() + s.slice(1);
  }


  // =========================================================================
  // Dialogue Scripts — ported from conversations/intro.py
  // =========================================================================

  var INTRO = [
    {
      id: 'welcome',
      speaker: 'mayor',
      type: 'choice',
      text: "Welcome to the Handoff Saloon! I'm the Mayor around here. What brings you in today?",
      choices: [
        { label: 'I have a project to work on', next: 'has_project' },
        { label: 'Just looking around', next: 'looking' },
        { label: 'Who are you people?', next: 'who' },
      ],
    },
    {
      id: 'has_project',
      speaker: 'mayor',
      type: 'dialogue',
      text: "Excellent! Here's how we work. You set up a Lead agent and a Reviewer agent. They take turns — the Lead does the work, the Reviewer checks it.",
      next: 'explain_rabbit',
    },
    {
      id: 'explain_rabbit',
      speaker: 'rabbit',
      type: 'dialogue',
      text: "That's where I come in! I keep the bar clean and the reviews flowing. I'll track every round and make sure nothing gets lost.",
      next: 'explain_controls',
    },
    {
      id: 'explain_controls',
      speaker: 'mayor',
      type: 'dialogue',
      text: "From this dashboard you can approve work, request changes, escalate disagreements, or abort if things go sideways. You're the arbiter — the final say.",
      next: 'signoff',
    },
    {
      id: 'signoff',
      speaker: 'mayor',
      type: 'dialogue',
      text: "Click me anytime you need help. When you're ready, set up your agents and start a new phase!",
      next: null,
    },
    {
      id: 'looking',
      speaker: 'mayor',
      type: 'dialogue',
      text: "Take your time! This here saloon orchestrates AI-to-AI handoffs. A Lead agent does the work, a Reviewer checks it, and you — the human — are the arbiter. Click around and explore.",
      next: 'looking_done',
    },
    {
      id: 'looking_done',
      speaker: 'rabbit',
      type: 'dialogue',
      text: "I've got drinks if you're thirsty! ...Well, metaphorical ones. Click the Mayor when you're ready to get started.",
      next: null,
    },
    {
      id: 'who',
      speaker: 'mayor',
      type: 'dialogue',
      text: "I'm the Mayor — I oversee the whole operation. I announce phases, keep order, and make sure the handoff runs smooth.",
      next: 'who_rabbit',
    },
    {
      id: 'who_rabbit',
      speaker: 'rabbit',
      type: 'choice',
      text: "And I'm the Bartender! I manage the day-to-day — tracking rounds, pouring feedback, making sure the Lead and Reviewer don't get into too much trouble.",
      choices: [
        { label: 'I have a project to work on', next: 'has_project' },
        { label: 'Maybe later', next: 'later' },
      ],
    },
    {
      id: 'later',
      speaker: 'mayor',
      type: 'dialogue',
      text: "No rush, partner. We'll be here when you're ready.",
      next: null,
    },
  ];

  var SETUP_INTRO = [
    {
      id: 'setup_welcome',
      speaker: 'mayor',
      type: 'dialogue',
      text: "Welcome, newcomer! Looks like this is your first time here. Let's get you set up.",
      next: 'setup_lead',
    },
    {
      id: 'setup_lead',
      speaker: 'mayor',
      type: 'input',
      prompt: "What's the name of your Lead agent? (The one that does the main work)",
      placeholder: 'e.g. claude',
      default: 'claude',
      next: 'setup_reviewer',
    },
    {
      id: 'setup_reviewer',
      speaker: 'rabbit',
      type: 'input',
      prompt: "And the Reviewer? (The one that checks the Lead's work)",
      placeholder: 'e.g. codex',
      default: 'codex',
      next: 'setup_done',
    },
    {
      id: 'setup_done',
      speaker: 'mayor',
      type: 'dialogue',
      text: "Perfect! Your saloon is set up and ready. Click me to start your first phase when you're ready to ride.",
      next: null,
    },
  ];


  // =========================================================================
  // Transition Templates — ported from conversations/transitions.py
  // =========================================================================

  var TRANSITIONS = {
    MAYOR_HANDOFF: [
      "The Lead's done their part. Time for the Reviewer to take a look.",
      "Work's been submitted. Let's see what the Reviewer thinks.",
      "Lead's finished up. Handing it over to the review side of the bar.",
    ],
    RABBIT_FEEDBACK: [
      "I've taken a look. Here's my feedback: {feedback}",
      "Review complete. Some notes: {feedback}",
      "Done reviewing! A few things to address: {feedback}",
    ],
    RABBIT_APPROVE: [
      "Everything looks good to me! Approved.",
      "Clean work. I'm giving this the stamp of approval.",
      "Reviewed and approved — no issues found.",
    ],
    MAYOR_APPROVED: [
      "The Reviewer has approved! This phase is complete. Well done, everyone.",
      "Stamp of approval received! Another successful handoff.",
      "And that's a wrap! Phase complete with flying colors.",
    ],
    ESCALATION_MAYOR: [
      "This disagreement needs a human touch. Your call, arbiter.",
      "The agents can't agree. As arbiter, you need to break the tie.",
    ],
    ESCALATION_RABBIT: [
      "I've flagged a concern. We need the arbiter to weigh in.",
      "Calling for human judgment — this one's above my pay grade.",
    ],
    MAYOR_WORKING: [
      "The Lead agent is hard at work. Sit tight, partner.",
      "Work's in progress. The Lead's got this.",
    ],
    RABBIT_WORKING: [
      "Reviewing now. I'll have feedback shortly.",
      "Taking a careful look at the work...",
    ],
    ABORTED: [
      "Cycle aborted. Reason: {reason}",
    ],
    ESCALATION_CHOICE_MAYOR: [
      "You sided with the Lead. Moving forward with their approach.",
      "The Lead's plan wins. Let's keep rolling.",
    ],
    ESCALATION_CHOICE_RABBIT: [
      "You agreed with the Reviewer. Changes will be made.",
      "Reviewer's feedback prevails. Back to the drawing board for the Lead.",
    ],
    ESCALATION_CHOICE_DEFER: [
      "Decision deferred. Let's let the agents work it out another round.",
      "Punting this one. Another round should sort things out.",
    ],
  };

  function pickTransition(category, vars) {
    var templates = TRANSITIONS[category];
    if (!templates || templates.length === 0) return '';
    var tmpl = templates[Math.floor(Math.random() * templates.length)];
    if (vars) {
      for (var key in vars) {
        tmpl = tmpl.replace('{' + key + '}', vars[key] || '');
      }
    }
    return tmpl;
  }


  // =========================================================================
  // buildStateDialogue — generate dialogue lines from state transitions
  // =========================================================================

  function buildStateDialogue(state, prevState) {
    var lines = [];
    if (!state) return lines;

    var status = state.status || '';
    var prevStatus = prevState ? (prevState.status || '') : '';
    var result = state.result || '';
    var turn = state.turn || '';
    var reason = state.reason || '';

    // No change
    if (prevState && status === prevStatus && result === (prevState.result || '')) {
      return lines;
    }

    if (status === 'working') {
      if (turn === 'lead') {
        lines.push({ speaker: 'mayor', text: pickTransition('MAYOR_WORKING') });
      } else {
        lines.push({ speaker: 'rabbit', text: pickTransition('RABBIT_WORKING') });
      }
    } else if (status === 'ready' && prevStatus === 'working') {
      if (turn === 'reviewer') {
        lines.push({ speaker: 'mayor', text: pickTransition('MAYOR_HANDOFF') });
      } else if (turn === 'lead') {
        lines.push({ speaker: 'rabbit', text: pickTransition('RABBIT_FEEDBACK', { feedback: '' }) });
      }
    } else if (status === 'done' && result === 'approved') {
      lines.push({ speaker: 'rabbit', text: pickTransition('RABBIT_APPROVE') });
      lines.push({ speaker: 'mayor', text: pickTransition('MAYOR_APPROVED') });
    } else if (status === 'escalated') {
      if (turn === 'lead') {
        lines.push({ speaker: 'rabbit', text: pickTransition('ESCALATION_RABBIT') });
      } else {
        lines.push({ speaker: 'mayor', text: pickTransition('ESCALATION_MAYOR') });
      }
    } else if (status === 'done' && result === 'agree_with_lead') {
      lines.push({ speaker: 'mayor', text: pickTransition('ESCALATION_CHOICE_MAYOR') });
    } else if (status === 'done' && result === 'agree_with_reviewer') {
      lines.push({ speaker: 'mayor', text: pickTransition('ESCALATION_CHOICE_RABBIT') });
    } else if (status === 'done' && result === 'deferred') {
      lines.push({ speaker: 'mayor', text: pickTransition('ESCALATION_CHOICE_DEFER') });
    } else if (status === 'aborted') {
      lines.push({ speaker: 'mayor', text: pickTransition('ABORTED', { reason: reason }) });
    }

    return lines;
  }


  // =========================================================================
  // Public API
  // =========================================================================

  return {
    ConversationEngine: ConversationEngine,
    TypewriterEffect: TypewriterEffect,
    DialogueController: DialogueController,
    INTRO: INTRO,
    SETUP_INTRO: SETUP_INTRO,
    TRANSITIONS: TRANSITIONS,
    pickTransition: pickTransition,
    buildStateDialogue: buildStateDialogue,
  };

})();
