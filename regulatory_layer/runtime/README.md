# Runtime-security rule samples (proposal 015)

ACC is a **consumer** of runtime-security evidence — it does not own or
force-provision the rules its backends run. These files are
**recommended samples** an operator applies to *their own*
runtime-security tool so it emits the events ACC's Category-A
kernel-event evaluator (`acc.governance.KernelEventEvaluator`, and the
reference `regulatory_layer/category_a/kernel_events.rego`) acts on.

| File | Backend | Apply with |
|---|---|---|
| `tetragon-tracingpolicy.yaml` | Tetragon | `kubectl apply -f` |
| `falco-rules.yaml` | Falco | mount into the Falco `rules.d` directory |

RHACS uses its own process-baseline + policy model in RHACS Central —
no rule file is shipped; ACC consumes whatever runtime violations RHACS
already raises.

NetObserv needs no rule file — it captures all eBPF flow logs; the
runtime-evidence bridge filters them to ACC agent pods.

See `docs/runtime-evidence.md` for the per-backend setup and the
observe→enforce rollout.
