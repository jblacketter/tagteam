"""State transition dialogue templates.

Short dialogue snippets triggered by handoff state changes.
These are template strings — {feedback} is replaced with extracted
text from the handoff document when available.
"""

from __future__ import annotations

# Mayor hands off to reviewer
MAYOR_HANDOFF = [
    "Bartender, take a look at this, would you?",
    "I have something ready for review. Your thoughts?",
    "The next piece is ready. Mind giving it a once-over?",
]

# Reviewer returns feedback to lead
RABBIT_FEEDBACK = [
    "I've had a look. {feedback}",
    "Here's what I think. {feedback}",
    "Alright, I reviewed it. {feedback}",
]

# Reviewer approves
RABBIT_APPROVE = [
    "Looks good to me.",
    "I'm satisfied. Ship it.",
    "No complaints. You're clear.",
]

# Mayor acknowledges approval
MAYOR_APPROVED = [
    "Excellent. Moving on to the next step.",
    "Good. That settles it.",
    "Noted. Let us proceed.",
]

# Escalation — needs human input
ESCALATION_MAYOR = [
    "We need your input on something.",
    "A decision needs to be made. Could you weigh in?",
]

ESCALATION_RABBIT = [
    "Hey, we've hit a snag. Need you to step in.",
    "Something needs your attention.",
]

# Status: working (subtle)
MAYOR_WORKING = [
    "Working on it.",
    "Give me a moment.",
]

RABBIT_WORKING = [
    "Let me take a closer look.",
    "Reviewing now.",
]

# Aborted
ABORTED = [
    "That cycle has been called off. {reason}",
]

# Escalation choice acknowledgments
ESCALATION_CHOICE_MAYOR = [
    "Thank you. I'll proceed as planned.",
    "Understood. We'll go with my approach.",
]

ESCALATION_CHOICE_RABBIT = [
    "Good call. I'll note the changes needed.",
    "Right. Let's make those adjustments.",
]

ESCALATION_CHOICE_DEFER = [
    "Take your time. We'll be here.",
    "No rush. Come back when you've decided.",
]

# Escalation choices (labels for the player)
ESCALATION_CHOICES = [
    "I agree with the Mayor's approach",
    "The Rabbit has a point — make the changes",
    "Let me think about it",
]
