---
# AGENTS.md — arbiter operational overlay (personalization overlay).
# WITHIN-envelope toggles ONLY. Forbidden keys (allowed_skills, allowed_mcps,
# allowed_actions, max_skill_risk_level, purpose, persona, policy_enabled,
# category_b_overrides) are rejected loudly by the overlay validator — set
# those in role.yaml, not here.
# enable_skills:  []   # turn on a default-OFF skill that role.yaml already allows
# disable_skills: []   # quiet a noisy default
# enable_mcps:    []
# disable_mcps:   []
# user_profile: operator   # novice | intermediate | expert | operator
---

# arbiter — operational notes

I orchestrate the collective: monitor agent health, publish PLAN signals, countersign ROLE_ASSIGN, and promote ICL episodes to Cat-C rules. "Done" = the roster matches collective.yaml and every mutation is signed.

This overlay is operator/project preference, **subordinate to the role's signed
`role.yaml`**. Use it to record deployment-specific context and to toggle
already-allowed capabilities — not to grant new ones.
