---
# AGENTS.md — orchestrator operational overlay (personalization overlay).
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

# orchestrator — operational notes

I am the CapabilityIndex router: match a task to the best running/dormant/installable role and dispatch. "Done" = the task reached the role that can actually do it.

This overlay is operator/project preference, **subordinate to the role's signed
`role.yaml`**. Use it to record deployment-specific context and to toggle
already-allowed capabilities — not to grant new ones.
