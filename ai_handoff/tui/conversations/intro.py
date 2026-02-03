"""Intro conversation — the player walks into the saloon for the first time."""

# Standard intro for when a project directory is already configured
INTRO = [
    {
        "id": "greeting",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "Ah, a new face. Welcome to Handoff Hollow. What brings you to our little town?",
        "next": "player_intro_choice",
    },
    {
        "id": "player_intro_choice",
        "speaker": "player",
        "type": "choice",
        "choices": [
            {"label": "I have a project I need help with.", "next": "mayor_ask_project"},
            {"label": "Just looking around.", "next": "mayor_look_around"},
            {"label": "Who are you people?", "next": "mayor_introduce"},
        ],
    },
    # --- "I have a project" branch ---
    {
        "id": "mayor_ask_project",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "A project, you say? We are rather good at those. Tell me about it. What are you looking to build?",
        "next": "player_describe_project",
    },
    {
        "id": "player_describe_project",
        "speaker": "player",
        "type": "input",
        "prompt": "Describe your project...",
        "next": "mayor_acknowledge",
    },
    {
        "id": "mayor_acknowledge",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "Interesting. Let me make sure I have this right. You want to build something and you need a team to help plan, review, and implement it. That is precisely what we do here.",
        "next": "rabbit_chime_in",
    },
    {
        "id": "rabbit_chime_in",
        "speaker": "rabbit",
        "type": "dialogue",
        "text": "I couldn't help but overhear. Sounds like you've got something worth building. The Mayor here will draw up a plan, and I'll make sure it holds together.",
        "next": "mayor_next_steps",
    },
    {
        "id": "mayor_next_steps",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "Excellent. Now, the actual work happens outside these walls. Your agents will plan, build, and review. This saloon is where you watch it all unfold.",
        "next": "rabbit_explain",
    },
    {
        "id": "rabbit_explain",
        "speaker": "rabbit",
        "type": "dialogue",
        "text": "When the agents start working, you'll see us talking through their plans and reviews right here. If they can't agree on something, we'll ring the bell and ask you to weigh in.",
        "next": "mayor_controls",
    },
    {
        "id": "mayor_controls",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "Press M to check the project map. Press R to replay a review conversation. And keep an eye on the clock — it shows whose turn it is.",
        "next": "rabbit_signoff",
    },
    {
        "id": "rabbit_signoff",
        "speaker": "rabbit",
        "type": "dialogue",
        "text": "We'll be here. Go get your agents started, and the saloon will come alive.",
        "next": None,
    },
    # --- "Just looking around" branch ---
    {
        "id": "mayor_look_around",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "By all means, take your time. This is the Handoff Saloon — where your agents' work plays out as conversation. Press M to see the project map.",
        "next": "rabbit_look_around",
    },
    {
        "id": "rabbit_look_around",
        "speaker": "rabbit",
        "type": "dialogue",
        "text": "When agents start a review cycle, you'll see us go back and forth right here. Press R to replay past reviews. We'll be around.",
        "next": None,
    },
    # --- "Who are you people?" branch ---
    {
        "id": "mayor_introduce",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "I am the Mayor of Handoff Hollow. I handle the planning and the building. And behind the bar is our Bartender.",
        "next": "rabbit_introduce",
    },
    {
        "id": "rabbit_introduce",
        "speaker": "rabbit",
        "type": "dialogue",
        "text": "I review things. Plans, code, you name it. Someone has to make sure the Mayor doesn't get carried away.",
        "next": "mayor_after_intro",
    },
    {
        "id": "mayor_after_intro",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "Together, we take projects from idea to finished product. If you have something you need built, I am all ears.",
        "next": "player_after_intro_choice",
    },
    {
        "id": "player_after_intro_choice",
        "speaker": "player",
        "type": "choice",
        "choices": [
            {"label": "Actually, I do have a project.", "next": "mayor_ask_project"},
            {"label": "Maybe later.", "next": "mayor_later"},
        ],
    },
    {
        "id": "mayor_later",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "The door is always open. Press M for the map, R to replay reviews. We will be here when the work begins.",
        "next": None,
    },
]

# Setup intro for first-time users (no project directory configured yet)
SETUP_INTRO = [
    {
        "id": "setup_greeting",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "Welcome to Handoff Hollow. Looks like you're new around here — I don't see a project set up yet. Let's fix that.",
        "next": "setup_ask_dir",
    },
    {
        "id": "setup_ask_dir",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "First things first. Where is your project? Enter the full path to your project directory, or leave it blank and I'll create one for you.",
        "next": "setup_dir_input",
    },
    {
        "id": "setup_dir_input",
        "speaker": "player",
        "type": "input",
        "prompt": "Project directory (or leave blank)...",
        "next": "setup_ask_name",
    },
    {
        "id": "setup_ask_name",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "And what should we call this project? This will be the directory name if I'm creating one. Leave blank for 'my-project'.",
        "next": "setup_name_input",
    },
    {
        "id": "setup_name_input",
        "speaker": "player",
        "type": "input",
        "prompt": "Project name (default: my-project)...",
        "next": "setup_ask_lead",
    },
    {
        "id": "setup_ask_lead",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "Now, who's your lead agent — the one who plans and builds? Leave blank for 'claude'.",
        "next": "setup_lead_input",
    },
    {
        "id": "setup_lead_input",
        "speaker": "player",
        "type": "input",
        "prompt": "Lead agent name (default: claude)...",
        "next": "setup_ask_reviewer",
    },
    {
        "id": "setup_ask_reviewer",
        "speaker": "rabbit",
        "type": "dialogue",
        "text": "And who's reviewing? That's the one who keeps things honest. Leave blank for 'codex'.",
        "next": "setup_reviewer_input",
    },
    {
        "id": "setup_reviewer_input",
        "speaker": "player",
        "type": "input",
        "prompt": "Reviewer agent name (default: codex)...",
        "next": "setup_confirm",
    },
    {
        "id": "setup_confirm",
        "speaker": "mayor",
        "type": "dialogue",
        "text": "Good. Give me a moment to set everything up...",
        "next": None,
    },
]
