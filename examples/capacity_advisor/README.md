# Capacity advisor — Feast feature repo

`feature_repo/` holds the Feast definitions the `capacity_advisor` role reads
(proposal 057 §5 step 2): entities `gpu_node`, `model_deployment` and
`cluster`, and the 5-minute views `gpu_node_stats`, `model_usage`,
`gpu_queue` and `model_predictions`.

`gpu_queue.deployment_ids` (`Array(String)`) lists every GPU-holding model
deployment (`ns/name`) the feature job saw in the window. The advisor's
wake-up prefetch reads it first. Its `model_usage` and `model_predictions`
reads then use it as their entity list (`"@prefetch:deployment_ids"`), so the
role keeps no deployment list by hand. Whatever writes the features (the
`gpu-features` job in lab-gitops) must fill `deployment_ids` alongside the
other `gpu_queue` features.

Each view has a push source `<view>_push` (the job POSTs `/push` with
`to: online`) over a batch source: the directory `data/offline/<view>/`
(override the root with `GPU_CAPACITY_OFFLINE_ROOT`), where the job writes one
parquet part per run. That history is what `model_predictions` trains on
(057 P2).

## Try it locally

```bash
pip install "feast[mcp]"
cd examples/capacity_advisor/feature_repo
# optional history: data/offline/<view>/part-*.parquet
feast apply --skip-source-validation    # empty history directories are fine
feast serve -p 6566        # MCP (streamable HTTP) at http://localhost:6566/mcp
# then push rows: POST /push {"push_source_name": "gpu_queue_push", "df": {...}, "to": "online"}
```

Point the `feast` MCP manifest's `url` at it. The recorded session in
`tests/fixtures/feast/capacity_prefetch_session.json` came from exactly this
setup with sample data.

On the dgx cluster, lab-gitops role `gpu_features`
(`ansible/arrow-lab/roles/gpu_features`) deploys a `FeatureStore` with this
project (`gpu_capacity`), a copy of these definitions, and the 5-minute
`gpu-features` CronJob that fills them. Change both copies together.
