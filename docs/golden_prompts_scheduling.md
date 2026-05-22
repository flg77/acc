# Golden-Prompt Suite — Scheduled Runs (D-005 Phase 3 / PR-O)

The golden-prompt suite (see `docs/TESTING.md` and `acc/golden_prompts.py`)
has three runner modes that share one loader + assertion engine:

| Mode | Command | Where |
|------|---------|-------|
| CLI on-demand | `acc-cli e2e run` | any host with NATS reachable |
| TUI Diagnostics | pane **9 Diagnostics** | edge / operator workstation |
| **Scheduled** | `acc-cli e2e run --loop … --history …` | DC / CI / cron |

This doc covers the **scheduled** mode.

## Quick start — built-in loop

The simplest scheduler is the CLI's own `--loop`:

```bash
acc-cli e2e run \
    --loop 3600 \
    --history test/history/golden.jsonl \
    --collective-id sol-01 \
    --nats-url nats://localhost:4222
```

This re-runs the full suite every 3600 s (1 h), appends one JSONL
row per prompt per run to `test/history/golden.jsonl`, and prints a
summary to stdout each pass.  Ctrl-C stops it; the exit code
reflects the last pass.

Each history row is self-contained:

```json
{"name":"coding_webscraper_basic","passed":true,"elapsed_ms":1820,
 "output_excerpt":"import yfinance…","failures":[],"error":"",
 "run_ts":1779600000.12,"collective_id":"sol-01",
 "nats_url":"nats://localhost:4222"}
```

Grep regressions with the same tooling as the
`acc-llm-test-history` archive:

```bash
jq 'select(.passed==false) | {name, run_ts, failures}' test/history/golden.jsonl
```

## systemd timer (DC / bare-metal)

For a managed host, prefer a systemd timer over a long-lived
`--loop` process (survives reboots, gets journald logging):

`/etc/systemd/system/acc-golden.service`:

```ini
[Unit]
Description=ACC golden-prompt suite (one shot)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/acc
Environment=ACC_NATS_URL=nats://localhost:4222
Environment=ACC_COLLECTIVE_ID=sol-01
ExecStart=/usr/bin/env acc-cli e2e run --history /var/lib/acc/golden.jsonl
User=acc
```

`/etc/systemd/system/acc-golden.timer`:

```ini
[Unit]
Description=Run ACC golden-prompt suite hourly

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl enable --now acc-golden.timer
journalctl -u acc-golden.service -f
```

## Kubernetes CronJob (RHOAI / cloud)

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: acc-golden-prompts
  namespace: acc
spec:
  schedule: "0 * * * *"          # hourly
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: e2e
              image: localhost/acc-agent-core:0.2.0
              command: ["acc-cli", "e2e", "run",
                        "--history", "/data/golden.jsonl"]
              env:
                - name: ACC_NATS_URL
                  value: nats://acc-nats.acc.svc:4222
                - name: ACC_COLLECTIVE_ID
                  value: sol-01
              volumeMounts:
                - name: history
                  mountPath: /data
          volumes:
            - name: history
              persistentVolumeClaim:
                claimName: acc-golden-history
```

The CronJob's pod exit code (non-zero on any prompt failure) drives
the Job's success/failure state, so a failing suite shows up in
`kubectl get jobs` and any Prometheus `kube_job_failed` alerting.

## Dedicated maintenance agent (future)

A future enhancement folds the scheduled run into a dedicated
maintenance agent that also writes results to LanceDB and posts a
summary to the Comms feed, so the operator sees suite health in the
TUI without leaving it.  Tracked in `docs/DECISIONS.md`
(D-005 Phase 3 follow-up).  For now the systemd timer / CronJob
recipes above are the supported scheduling paths.

## CI gate

For a PR gate, run once (no loop) and let the exit code fail the
job:

```yaml
# .github/workflows/e2e.yml (excerpt)
- name: golden-prompt suite
  run: |
    ./acc-deploy.sh up
    acc-cli e2e validate           # schema gate (no network)
    acc-cli e2e run                # exit non-zero on any failure
```
