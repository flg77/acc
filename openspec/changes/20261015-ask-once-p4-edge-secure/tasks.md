# 20261015-ask-once-p4-edge-secure — tasks

- [x] 1.1 `EvalsConfig`; env overrides; profile SETTABLE / POSTURE_KEYS
- [x] 1.2 `profiles/edge_secure.yaml`
- [x] 1.3 MLflow URI from the profile
- [x] 2.1 promptfoo config generator + `acc-cli evals promptfoo-config`; container + loop
- [x] 2.2 WebGUI Settings → Evals (promptfoo switch; posture read-only)
- [x] 3.1 garak summarise / gate + `acc-cli evals garak-gate`; one-shot container
- [ ] 3.2 call `garak-gate` from the release routine (acc-promote / tools/gate)
- [ ] 4.1 **[op]** Q5: runtime guard model
- [ ] 4.2 **[op]** verify the pinned image tags; first promptfoo + garak run on lighthouse; baselines into evidence
- [ ] 4.3 AgentBOM records the profile and the eval baselines
