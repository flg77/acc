# Spec delta: acc-webgui signaling + observer resilience

## ADDED Requirements

### Requirement: Operator wires webgui signaling config
The `WebGUIReconciler` SHALL inject the signaling configuration the webgui
process needs to start into the `webgui` container, derived from the owning
`AgentCorpus`.

#### Scenario: NATS URL is namespaced to the corpus
- **WHEN** the reconciler builds the webgui Deployment for corpus `<corpus>`
- **THEN** the `webgui` container env SHALL include
  `ACC_NATS_URL=nats://<corpus>-nats:4222`

#### Scenario: Observed collectives come from the corpus
- **WHEN** the corpus lists one or more `AgentCollective`s that resolve
- **THEN** the `webgui` container env SHALL include `ACC_COLLECTIVE_IDS` set to
  the comma-joined `Spec.CollectiveID` of those collectives

#### Scenario: No collectives → default preserved
- **WHEN** the corpus has no resolvable collectives
- **THEN** `ACC_COLLECTIVE_IDS` SHALL be omitted (the app keeps its default)
- **AND** an unresolvable collective SHALL be logged and skipped, not fail the
  reconcile

### Requirement: webgui observer start is non-fatal on NATS outage
`ObserverHub.start()` SHALL NOT raise when a collective's NATS connection fails
at boot; the web backend SHALL continue serving `/health` and the SPA in a
degraded (not-yet-connected) state and SHALL connect in the background once NATS
is reachable.

#### Scenario: NATS unavailable at boot
- **WHEN** `start()` is called and the initial `connect()` raises
- **THEN** `start()` SHALL return without raising
- **AND** the collective SHALL have no live observer yet
- **AND** a background task SHALL retry the connect with capped backoff

#### Scenario: NATS recovers
- **WHEN** NATS becomes reachable after a failed boot connect
- **THEN** the background retry SHALL connect + subscribe the observer and start
  its drain task

#### Scenario: NATS available at boot (unchanged fast path)
- **WHEN** the initial `connect()` succeeds
- **THEN** the observer SHALL be registered synchronously before `start()`
  returns (so `observer(cid)` is immediately usable by the action layer)
