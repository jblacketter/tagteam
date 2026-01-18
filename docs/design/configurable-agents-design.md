# Design: Configurable AI Agents

**Status**: Revision 3 - Final Refinements
**Author**: Claude (Lead)
**Reviewer**: Codex
**Date**: 2025-01-17

---

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| Draft | 2025-01-17 | Initial design |
| Rev 2 | 2025-01-17 | Addressed Codex review findings (see below) |
| Rev 3 | 2025-01-17 | Refinements from Codex re-review |

### Codex Review 1 Findings (Addressed in Rev 2)

| Severity | Finding | Resolution |
|----------|---------|------------|
| **High** | Role resolution underspecified - AI can't determine which agent it is | Added Section 1.1: Agent Self-Identification |
| **Medium** | Primary lead/reviewer undefined for templates | Added explicit `primary: true` flag in schema |
| **Medium** | `.claude/skills/` only - other AIs won't load skills | Changed to `.ai-handoff/skills/` with symlinks |
| **Medium** | Regeneration drift when users edit files | Added drift detection and merge strategy |
| **Medium** | Migration preserves old hardcoded names | Added `--regenerate-docs` flag to migration |
| **Low** | Arbiter validation unclear | Clarified arbiter rules in schema |

### Codex Review 2 Findings (Addressed in Rev 3)

| Severity | Finding | Resolution |
|----------|---------|------------|
| **Medium** | `model_patterns` matching underspecified (exact vs regex, source of model identifier) | Added Section 1.2: Model Identifier Specification |
| **Medium** | Per-agent directory naming unstable for names with spaces/punctuation | Added `id` field for filesystem-safe slug |
| **Low** | Windows `sync-skills` command undefined | Added Section 5.1: Windows Skill Sync

---

## Problem Statement

The current AI Handoff Framework hardcodes "Claude" as Lead and "Codex" as Reviewer throughout the documentation and skills. This limits adoption for users who:

- Use different AI combinations (e.g., Claude + Gemini, Grok + Codex)
- Want multiple reviewers (e.g., Codex AND Gemini)
- Want multiple leads with specialties (e.g., Claude for backend, Gemini for frontend)
- Need to adapt as new AI models emerge

## Proposed Solution

Introduce a configuration file that defines which AIs are used and their roles, with templates and skills that dynamically adapt to the configuration.

---

## Design

### 1. Configuration File

**Location**: `ai-handoff.yaml` in project root (visible, easy to find)

**Schema**:

```yaml
# ai-handoff.yaml

# Project metadata (optional)
project_name: "My Project"

# AI agents and their roles
agents:
  - name: Claude
    id: claude            # NEW: filesystem-safe identifier (auto-generated if omitted)
    role: lead
    specialty: backend    # optional - for multiple leads
    primary: true         # designates primary lead for templates
    model_patterns:       # for self-identification (see Section 1.2)
      - "claude"

  - name: Gemini
    id: gemini
    role: lead
    specialty: frontend
    model_patterns:
      - "gemini"

  - name: Codex
    id: codex
    role: reviewer
    primary: true         # designates primary reviewer for templates
    model_patterns:
      - "codex"

  - name: Grok
    id: grok
    role: reviewer
    model_patterns:
      - "grok"

  # Optional: include an arbiter agent when arbiter.type is "ai"
  # - name: Morgan Freeman  # Display name can have spaces
  #   id: morgan            # ID must be filesystem-safe
  #   role: arbiter

# Arbiter configuration (required)
arbiter:
  name: Human           # Can be "Human" or an AI name
  type: human           # "human" or "ai" - affects workflow expectations
  # If type: ai, name must match an agent with role: arbiter

# Optional: default behaviors
defaults:
  review_cycles_before_escalation: 2
```

**Role Types**:
- `lead` - Creates plans, implements code, creates handoffs
- `reviewer` - Reviews plans and implementations, provides feedback
- `arbiter` - Breaks ties, makes final decisions (typically Human, can be AI). If `arbiter.type: ai`, include an agent with `role: arbiter`.

**Validation Rules**:
- At least one `lead` required
- At least one `reviewer` required
- `arbiter` required with `name` and `type`
- Agent `name` must be unique across agents
- Agent `id` must be unique, lowercase, alphanumeric with hyphens only (regex: `^[a-z][a-z0-9-]*$`)
- If `id` omitted, auto-generate from `name`: lowercase, replace spaces/punctuation with hyphens, strip invalid chars
- If `arbiter.type: human`, `arbiter.name` must NOT match any agent name
- If `arbiter.type: ai`, `arbiter.name` must match exactly one agent with `role: arbiter`
- Exactly one `primary: true` per role (lead/reviewer) when multiple exist
- If only one agent per role, `primary` defaults to `true`
- `model_patterns` must not overlap across agents (see Section 1.2 for matching rules)

---

### 1.1 Agent Self-Identification (NEW - Addresses High Finding)

**Problem**: Skills say "read config to determine your role" but don't specify HOW an AI knows which `agents.name` entry it corresponds to.

**Solution**: Multi-layered identification with explicit matching rules.

#### Identification Methods (in priority order):

1. **Environment Variable** (recommended for automation):
   ```bash
   export AI_HANDOFF_AGENT="Claude"
   ```

2. **CLI Flag** (for manual invocation):
   ```bash
   ai-handoff --agent Claude /plan create phase-1
   ```

3. **Model Self-Identification** (fallback):
   The AI reads the config and matches against known model identifiers:
   ```yaml
   agents:
     - name: Claude
       role: lead
       model_patterns:        # NEW: patterns for self-identification
         - "claude"
         - "anthropic"

     - name: Codex
       role: reviewer
       model_patterns:
         - "codex"
         - "openai"
   ```

4. **Explicit Prompt Injection** (IDE/tool integration):
   Tools can inject identity context:
   ```
   You are operating as "Claude" in this project.
   See ai-handoff.yaml for your role configuration.
   ```

#### Skill Preamble (Updated):

```markdown
# /plan

> **Identity Check**: Determine which agent you are:
> 1. Check for AI_HANDOFF_AGENT environment variable
> 2. Check for --agent flag in invocation
> 3. Match your model identifier against `model_patterns` in ai-handoff.yaml
> 4. If still ambiguous, ask the user to clarify
>
> Once identified, read your `role` from the config to determine behavior.
```

#### Ambiguity Handling:

If the AI cannot determine its identity:
1. Output a warning: "Unable to determine agent identity"
2. List configured agents and ask user to specify
3. Do NOT proceed with role-specific behavior until resolved
4. If multiple `model_patterns` match, treat as ambiguous and request explicit identity

---

### 1.2 Model Identifier Specification (NEW - Addresses Rev 2 Finding)

**Problem**: Section 1.1 references "model identifier" and `model_patterns` but doesn't define:
- Where the model identifier comes from
- Whether matching is exact, substring, prefix, or regex
- How overlap detection works

#### Model Identifier Sources

The "model identifier" is a string the AI can introspect to determine its own identity. Sources in priority order:

| Priority | Source | Example Value | How to Access |
|----------|--------|---------------|---------------|
| 1 | Environment variable | `AI_MODEL_ID=claude-3-opus` | `$AI_MODEL_ID` or equivalent |
| 2 | API response metadata | `"model": "claude-3-opus-20240229"` | Returned in API responses |
| 3 | System prompt injection | `"You are Claude, made by Anthropic"` | Parsed from system context |
| 4 | Self-identification | `"I am Claude"` | AI's own knowledge of its identity |

**Canonical identifier**: The first non-empty value from the priority list above.

#### Pattern Matching Rules

`model_patterns` uses **case-insensitive substring matching**:

```yaml
model_patterns:
  - "claude"      # Matches: "claude-3-opus", "Claude", "CLAUDE-SONNET"
  - "anthropic"   # Matches: "anthropic-claude", "Anthropic AI"
```

**Matching algorithm**:
```python
def matches(model_identifier: str, patterns: list[str]) -> bool:
    model_lower = model_identifier.lower()
    return any(pattern.lower() in model_lower for pattern in patterns)
```

**Why substring (not regex)**:
- Simpler to write and validate
- Less error-prone for users
- Sufficient for model identification (model names are predictable)
- Regex can be added later if needed

#### Overlap Detection

During config validation, check for pattern overlaps:

```python
def has_overlap(agent1_patterns: list[str], agent2_patterns: list[str]) -> bool:
    # Check if any pattern from agent1 could match agent2's patterns or vice versa
    for p1 in agent1_patterns:
        for p2 in agent2_patterns:
            p1_lower, p2_lower = p1.lower(), p2.lower()
            if p1_lower in p2_lower or p2_lower in p1_lower:
                return True
    return False
```

**Example conflicts**:
```yaml
# INVALID - "gpt" is substring of "chatgpt"
agents:
  - name: GPT-4
    model_patterns: ["gpt"]
  - name: ChatGPT
    model_patterns: ["chatgpt"]

# VALID - no substring overlap
agents:
  - name: GPT-4
    model_patterns: ["gpt-4"]
  - name: ChatGPT
    model_patterns: ["chatgpt"]
```

**Validation error**:
```
Error: Model pattern overlap detected.
  Agent "GPT-4" pattern "gpt" overlaps with agent "ChatGPT" pattern "chatgpt".
  Use more specific patterns to avoid ambiguity.
```

---

### 2. Template System (Hybrid Approach)

**Approach**: Templates use variables, docs are generated during init, with regeneration on demand.

**Template Variables**:

| Variable | Description | Selection Rule | Example Value |
|----------|-------------|----------------|---------------|
| `{{lead}}` | Primary lead name | Agent with `role: lead` AND `primary: true` | `Claude` |
| `{{leads}}` | All leads with specialties | All agents with `role: lead` | `Claude (backend), Gemini (frontend)` |
| `{{reviewer}}` | Primary reviewer name | Agent with `role: reviewer` AND `primary: true` | `Codex` |
| `{{reviewers}}` | All reviewers | All agents with `role: reviewer` | `Codex, Grok` |
| `{{arbiter}}` | Arbiter name | From `arbiter.name` | `Human` |
| `{{project_name}}` | Project name | From `project_name` | `My Project` |

**Primary Selection Rules**:
- If only one agent has a role, it is automatically primary
- If multiple agents share a role, exactly one must have `primary: true`
- Validation fails if multiple or zero primaries exist for a multi-agent role

**Example Template** (`templates/phase_plan.md`):

```markdown
# Phase Plan: {{phase_name}}

**Lead**: {{lead}}
**Reviewers**: {{reviewers}}

## Overview
...

## Review Process
This plan will be reviewed by {{reviewers}}.
Disputes will be escalated to {{arbiter}}.
```

**Generation Flow**:

```
ai-handoff-setup myproject/
  ├── Read/create ai-handoff.yaml
  ├── Load templates from templates/
  ├── Substitute variables
  └── Write to docs/
```

**Regeneration Command**:

```bash
ai-handoff regenerate [--force] [--merge]
```

**Drift Detection and Handling** (Addresses Medium Finding):

| Scenario | Default Behavior | `--force` | `--merge` |
|----------|-----------------|-----------|-----------|
| File unmodified | Regenerate | Regenerate | Regenerate |
| File modified by user | Skip + warn | Backup to `.backup/` then regenerate | Attempt 3-way merge |
| Config changed | Regenerate unmodified only | Regenerate all | Merge changes into modified |

**Drift Detection**:
- On generation, store SHA256 hash of generated content in `.ai-handoff/checksums.json`
- Also store last-generated content in `.ai-handoff/generated/` to serve as merge base
- On regeneration, compare current file hash to stored hash
- If different, file was user-modified
- `--merge` uses base (last generated), current file, and newly generated content
- If base is missing, warn and skip merge for that file

**Example Output**:
```
ai-handoff regenerate
✓ docs/roadmap.md - regenerated
⚠ docs/phases/phase-1.md - modified by user, skipped (use --force or --merge)
✓ docs/decision_log.md - regenerated
```

---

### 3. Dynamic Skills (Config-Aware Approach)

**Approach**: Skills contain role-based conditional logic. Each skill reads the config and adapts behavior based on the AI's configured role.

**Skill Structure**:

```markdown
# /plan

> **Before using this skill**: Read `ai-handoff.yaml` to determine your role.

## If you are a **Lead**:

You are responsible for creating and updating phase plans.

### Actions:
- `/plan create [phase]` - Create a new phase plan
- `/plan update [phase]` - Update existing plan based on feedback

### Process:
1. Read the roadmap from `docs/roadmap.md`
2. Check for existing phases in `docs/phases/`
3. Create detailed plan using `templates/phase_plan.md`
...

## If you are a **Reviewer**:

You should not create plans directly. Your role is to review plans created by leads.

Redirect to: `/review plan [phase]`

## If you are the **Arbiter**:

Plans require your approval before implementation begins. Review the plan and any reviewer feedback, then make a final decision.

Redirect to: `/decide` or `/escalate resolve`
```

**Benefits**:
- Single skill file handles all roles
- No skill generation/duplication needed
- Any AI can read and understand their role
- Easy to update skill logic in one place

**Skills to Update**:

| Skill | Lead Behavior | Reviewer Behavior | Arbiter Behavior |
|-------|---------------|-------------------|------------------|
| `/plan` | Create/update plans | Redirect to /review | Approve/reject |
| `/handoff` | Create handoffs | Read handoffs | N/A |
| `/review` | Read feedback | Perform reviews | N/A |
| `/implement` | Track implementation | Redirect to /review | N/A |
| `/phase` | Manage lifecycle | View only | Approve transitions |
| `/status` | Full status | Full status | Full status |
| `/decide` | Log decisions | Propose decisions | Final decisions |
| `/escalate` | Create escalations | Create escalations | Resolve escalations |
| `/sync` | Generate syncs | Generate syncs | Generate syncs |

---

### 4. Setup Flow Changes

**Current Flow**:
```bash
ai-handoff-setup myproject/
# Copies files, done
```

**New Flow**:
```bash
ai-handoff-setup myproject/

# Interactive prompts:
? Project name: My Project
? Add an AI agent:
  Name: Claude
  Role: lead / reviewer
  Specialty (optional):
? Add another agent? (y/n)
...
? Arbiter (default: Human): Human

# Actions:
✓ Created ai-handoff.yaml
✓ Generated docs from templates
✓ Installed skills to .ai-handoff/skills/ and linked to .claude/skills/
✓ Setup complete!

# Output:
Run /status to verify your setup.
```

**Non-Interactive Mode**:
```bash
ai-handoff-setup myproject/ --config path/to/ai-handoff.yaml
```

---

### 5. File Structure Changes (Addresses Medium Finding)

**Problem**: `.claude/skills/` only works for Claude; other AIs won't load skills from there.

**Solution**: Use `.ai-handoff/skills/` as source of truth, with symlinks/copies to AI-specific directories.

**Before**:
```
project/
├── .claude/skills/        # Hardcoded skills
├── docs/
│   ├── phases/
│   ├── handoffs/
│   └── ...
└── templates/             # Static templates
```

**After**:
```
project/
├── ai-handoff.yaml              # Configuration file
├── .ai-handoff/
│   ├── skills/                  # Source of truth for all skills
│   │   ├── plan.md
│   │   ├── handoff.md
│   │   ├── review.md
│   │   └── ...
│   ├── generated/               # Merge base for --merge
│   ├── checksums.json           # For drift detection
│   └── config.schema.json       # Optional: JSON schema for validation
├── .claude/
│   └── skills/ → ../.ai-handoff/skills/   # Symlink (or copy on Windows)
├── .codex/
│   └── skills/ → ../.ai-handoff/skills/   # Created per configured agent
├── .gemini/
│   └── skills/ → ../.ai-handoff/skills/
├── docs/
│   ├── phases/
│   ├── handoffs/
│   └── ...                      # Generated from templates
└── templates/                   # Source templates with variables
```

**Setup Creates AI-Specific Directories**:
- For each agent in config, create `.{agent.id}/skills/` (uses `id` field, not `name`)
- Example: agent with `id: claude` → `.claude/skills/`
- Example: agent with `name: "GPT-4 Turbo"`, `id: gpt4` → `.gpt4/skills/`
- On Unix/macOS: symlink to `.ai-handoff/skills/`
- On Windows: copy files (symlinks require admin)

**Benefits**:
- Single source of truth (`.ai-handoff/skills/`)
- Each AI loads skills from its expected location
- Updates to skills automatically propagate via symlinks
- `id` field ensures stable, filesystem-safe directory names

---

### 5.1 Windows Skill Sync (NEW - Addresses Low Finding)

**Problem**: Windows uses file copies instead of symlinks. When skills are updated in `.ai-handoff/skills/`, the copies in `.{agent.id}/skills/` become stale.

**Solution**: Add `ai-handoff sync-skills` command.

```bash
ai-handoff sync-skills [--dry-run]
```

**Behavior**:
1. Read `ai-handoff.yaml` to get list of configured agents
2. For each agent, compare `.ai-handoff/skills/` to `.{agent.id}/skills/`
3. Copy any files that differ (based on content hash, not mtime)
4. Report what was synced

**Example Output**:
```
ai-handoff sync-skills

Syncing skills for 3 agents...
  .claude/skills/: 0 files updated (symlink, no sync needed)
  .codex/skills/: 2 files updated
    - plan.md (updated)
    - review.md (updated)
  .grok/skills/: 2 files updated
    - plan.md (updated)
    - review.md (updated)

Sync complete.
```

**When to Run**:
- After updating skills in `.ai-handoff/skills/`
- After pulling changes that modify skills
- After running `ai-handoff migrate` or `ai-handoff setup`

**Automation Options**:
- Git hook: `post-merge` to auto-sync after pulls
- npm/pip script: Add to build/dev scripts

---

## Migration Path (Updated - Addresses Medium Finding)

**Problem**: Original migration preserved old docs with hardcoded names, causing conflicts.

**Solution**: Add `--regenerate-docs` flag and provide clear guidance.

```bash
ai-handoff migrate [--regenerate-docs] [--dry-run]
```

**Migration Steps**:

1. **Detect existing setup**:
   - Look for `.claude/skills/` (old structure)
   - Scan docs for hardcoded "Claude"/"Codex" references

2. **Generate config**:
   - Create `ai-handoff.yaml` with defaults (Claude=lead, Codex=reviewer)
   - User can edit before proceeding

3. **Update skills**:
   - Move skills to `.ai-handoff/skills/`
   - Create symlinks from `.claude/skills/`
   - Replace old skills with config-aware versions

4. **Handle docs** (based on flags):

| Flag | Behavior |
|------|----------|
| (none) | Warn about hardcoded names, don't modify docs |
| `--regenerate-docs` | Regenerate docs from templates (backs up modified) |
| `--dry-run` | Show what would change without modifying |

**Example Migration Output**:
```
ai-handoff migrate --dry-run

Detected existing ai-handoff setup:
  - Skills: .claude/skills/ (9 files)
  - Docs: docs/ (hardcoded names found in 5 files)

Planned changes:
  ✓ Create ai-handoff.yaml (Claude=lead, Codex=reviewer)
  ✓ Move skills to .ai-handoff/skills/
  ✓ Create symlink .claude/skills/ → .ai-handoff/skills/
  ⚠ Docs contain hardcoded names (use --regenerate-docs to update):
    - docs/workflows.md: 12 references
    - docs/phases/phase-1.md: 3 references
    ...

Run without --dry-run to apply changes.
```

---

## Open Questions (Resolved per Codex Review)

### 1. Specialty Routing → RESOLVED: Explicit Owner Metadata

**Codex Recommendation**: Add explicit lead (owner) metadata to plans/handoffs; require reviewers to address feedback to that owner.

**Implementation**:

All plans and handoffs include an `owner` field:

```markdown
# Phase Plan: Authentication

**Owner**: Claude              <!-- Explicit owner, not just "lead" -->
**Specialty**: backend
**Reviewers**: Codex, Grok
```

Handoff documents include routing:

```markdown
# Handoff: Authentication Plan Review

**From**: Claude (Owner)
**To**: Codex, Grok (Reviewers)
**Response To**: Claude         <!-- Feedback goes back to owner -->
```

**Benefits**:
- Clear accountability for each artifact
- Reviewers know exactly who to address
- Specialties are advisory; ownership is authoritative

---

### 2. Skill File Location → RESOLVED: Hybrid Approach

**Codex Recommendation**: Use `.ai-handoff/skills` as source of truth, copy/symlink to client-specific folders.

**Implementation**: See Section 5 (File Structure Changes)

- Source: `.ai-handoff/skills/`
- Per-agent: `.claude/skills/`, `.codex/skills/`, etc. (symlinks)
- Windows: copies instead of symlinks

---

### 3. Config Validation → RESOLVED: Both

**Codex Recommendation**: Strict validation during setup/migrate, plus lightweight validation on skill use with actionable errors.

**Implementation**:

| When | Validation Level | Actions |
|------|-----------------|---------|
| `ai-handoff setup` | Strict | Full schema validation, fail on any error |
| `ai-handoff migrate` | Strict | Validate before applying changes |
| Skill invocation | Lightweight | Check config exists, agent identifiable, role valid |

**Skill-Level Validation Errors**:
```
Error: Cannot determine your agent identity.
Configured agents: Claude (lead), Codex (reviewer)
Set AI_HANDOFF_AGENT environment variable or use --agent flag.

Error: Config file missing or invalid.
Run 'ai-handoff validate' to check your configuration.
```

---

## Success Criteria

- [ ] Users can configure any combination of AIs
- [ ] Skills adapt behavior based on configured role
- [ ] Templates generate correct docs based on config
- [ ] Existing workflow (plan → handoff → review → implement) still works
- [ ] Migration path exists for current users
- [ ] Documentation updated to reflect new approach
- [ ] Agent self-identification works reliably
- [ ] Owner metadata correctly routes feedback

---

## Testing Requirements (per Codex Review)

### Config Parsing Tests

| Test Case | Expected Behavior |
|-----------|------------------|
| Single lead, single reviewer | Valid, both auto-primary |
| Multiple leads, one primary | Valid |
| Multiple leads, no primary | Error: "Exactly one lead must have primary: true" |
| Multiple leads, multiple primary | Error: "Only one lead can be primary" |
| Missing arbiter | Error: "Arbiter is required" |
| Duplicate names | Error: "Agent names must be unique" |
| Arbiter type human, name same as agent | Error: "Arbiter name conflicts with agent" |
| Arbiter type ai, name matches agent with role arbiter | Valid |
| Arbiter type ai, name missing from agents | Error: "Arbiter must match an arbiter agent" |
| Overlapping model_patterns (substring) | Error: "Model pattern overlap detected" |

### ID Field Tests (NEW)

| Test Case | Expected Behavior |
|-----------|------------------|
| `id` provided and valid | Use as-is |
| `id` omitted, simple name | Auto-generate: "Claude" → "claude" |
| `id` omitted, name with spaces | Auto-generate: "GPT-4 Turbo" → "gpt-4-turbo" |
| `id` omitted, name with punctuation | Auto-generate: "Claude (v3)" → "claude-v3" |
| `id` with invalid chars | Error: "ID must match ^[a-z][a-z0-9-]*$" |
| Duplicate `id` values | Error: "Agent IDs must be unique" |
| `id` starts with number | Error: "ID must start with a letter" |

### Model Pattern Tests (NEW)

| Test Case | Expected Behavior |
|-----------|------------------|
| Exact match | "claude" matches "claude" ✓ |
| Substring match | "claude" matches "claude-3-opus" ✓ |
| Case-insensitive | "claude" matches "CLAUDE" ✓ |
| No match | "claude" does not match "gemini" |
| Overlap: "gpt" vs "chatgpt" | Error: substring overlap detected |
| Overlap: "gpt-4" vs "gpt-4-turbo" | Error: substring overlap detected |
| No overlap: "gpt-4" vs "gpt-3" | Valid |

### Regeneration Tests

| Test Case | Expected Behavior |
|-----------|------------------|
| Config change, docs unmodified | All docs regenerated |
| Config change, some docs modified | Unmodified regenerated, modified skipped + warning |
| `--force` with modified docs | Backup created, all regenerated |
| `--merge` with modified docs | 3-way merge attempted |
| `--merge` without base | Warn and skip merge for that file |

### Migration Tests

| Test Case | Expected Behavior |
|-----------|------------------|
| Old setup with hardcoded names | Detect and warn |
| `--dry-run` | Show changes without modifying |
| `--regenerate-docs` | Update docs to use config values |
| Skills in `.claude/skills/` | Moved to `.ai-handoff/skills/`, symlink created |

### Agent Identification Tests

| Test Case | Expected Behavior |
|-----------|------------------|
| `AI_HANDOFF_AGENT=Claude` set | Identify as Claude |
| `--agent Codex` flag | Identify as Codex |
| Model pattern match | Fall back to model_patterns |
| No identification possible | Error with actionable message |
| Multiple model pattern matches | Error + request explicit identity |

### Windows Sync-Skills Tests (NEW)

| Test Case | Expected Behavior |
|-----------|------------------|
| Unix/macOS with symlinks | Report "symlink, no sync needed" |
| Windows, skills unchanged | Report "0 files updated" |
| Windows, skills modified | Copy updated files, report count |
| `--dry-run` | Show what would change, don't modify |
| Missing agent directory | Create directory, then copy |
| Invalid config | Error before any sync |

---

## Review Checklist (Rev 3)

For Codex review of Rev 3, please evaluate:

- [ ] **Model Identifier Spec**: Is the source priority and substring matching sufficiently defined?
- [ ] **ID Field**: Does the `id` field with auto-generation solve the filesystem naming issue?
- [ ] **Overlap Detection**: Is the substring-based overlap check correct and sufficient?
- [ ] **Sync-Skills Command**: Is the Windows sync workflow complete and testable?
- [ ] **Test Coverage**: Do the new tests adequately cover the Rev 2 findings?
