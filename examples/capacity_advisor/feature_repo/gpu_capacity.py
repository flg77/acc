"""Feature definitions the capacity advisor reads (proposal 057 §5 step 2).

Entities and 5-minute views, pushed by the gpu-features job from Thanos
(lab-gitops ansible/arrow-lab/roles/gpu_features; push source "<view>_push"):

    gpu_node          gpu_node_stats     per GPU node
    model_deployment  model_usage        per model deployment ("ns/name")
    cluster           gpu_queue          cluster-wide queue + the deployment list
    model_deployment  model_predictions  CPU-model scores (P2)

``gpu_queue.deployment_ids`` lists every GPU-holding model deployment the job
saw this window ("ns/name"). The advisor's wake-up prefetch reads it first and
uses it as the entity list of its ``model_usage`` read, so no deployment list
is maintained by hand in the role.
"""

import os
from datetime import timedelta

from feast import Entity, FeatureService, FeatureView, Field, FileSource, PushSource
from feast.types import Array, Bool, Float64, Int64, String

TTL = timedelta(minutes=30)
# Where the offline history lives: one directory per view, one parquet part
# file per 5-minute window, written by the gpu-features job. On the cluster
# this is the shared RWX volume the job and the offline store both mount
# (set GPU_CAPACITY_OFFLINE_ROOT when running `feast apply`).
OFFLINE_ROOT = os.environ.get("GPU_CAPACITY_OFFLINE_ROOT", "data/offline")

gpu_node = Entity(name="gpu_node", join_keys=["gpu_node"], description="GPU node name")
model_deployment = Entity(name="model_deployment", join_keys=["model_deployment"],
                          description='Model deployment as "namespace/name"')
cluster = Entity(name="cluster", join_keys=["cluster"], description="Cluster name, e.g. dgx")


def _source(name: str) -> PushSource:
    """The gpu-features job POSTs each window to the feature server's /push
    (to: online) and writes the same rows as a parquet part file under
    OFFLINE_ROOT/<view>/ -- the history for training (057 step 4). Feast's
    file offline store cannot append to a file that does not exist yet, so
    the job writes the history itself rather than pushing it offline."""
    batch = FileSource(name=f"{name}_batch", path=os.path.join(OFFLINE_ROOT, name),
                       timestamp_field="event_timestamp")
    return PushSource(name=f"{name}_push", batch_source=batch)


gpu_node_stats = FeatureView(
    name="gpu_node_stats", entities=[gpu_node], ttl=TTL, source=_source("gpu_node_stats"),
    schema=[
        Field(name="gpu_node", dtype=String),  # join key, declared: no inference from files
        Field(name="gpus_alloc", dtype=Int64),
        Field(name="gpus_used", dtype=Int64),
        Field(name="util_avg_5m", dtype=Float64),
        Field(name="fb_used_gb", dtype=Float64),
        Field(name="cpu_req_pct", dtype=Float64),
    ],
)

model_usage = FeatureView(
    name="model_usage", entities=[model_deployment], ttl=TTL, source=_source("model_usage"),
    schema=[
        Field(name="model_deployment", dtype=String),  # join key, declared: no inference from files
        Field(name="gpus_held", dtype=Int64),
        Field(name="req_running", dtype=Int64),
        Field(name="req_waiting", dtype=Int64),
        Field(name="tokens_5m", dtype=Int64),
        Field(name="minutes_since_last_req", dtype=Float64),
        Field(name="stopped", dtype=Bool),
    ],
)

gpu_queue = FeatureView(
    name="gpu_queue", entities=[cluster], ttl=TTL, source=_source("gpu_queue"),
    schema=[
        Field(name="cluster", dtype=String),  # join key, declared: no inference from files
        Field(name="pending_gpu_pods", dtype=Int64),
        Field(name="pending_gpus_requested", dtype=Int64),
        Field(name="oldest_pending_min", dtype=Float64),
        # Every GPU-holding model deployment seen this window, "ns/name".
        # Read first by the advisor's prefetch; feeds the model_usage read.
        Field(name="deployment_ids", dtype=Array(String)),
    ],
)

model_predictions = FeatureView(
    name="model_predictions", entities=[model_deployment], ttl=TTL,
    source=_source("model_predictions"),
    schema=[
        Field(name="model_deployment", dtype=String),  # join key, declared: no inference from files
        Field(name="idle_score", dtype=Float64),
        Field(name="demand_next_60m", dtype=Float64),
    ],
)

capacity_cluster_v1 = FeatureService(name="capacity_cluster_v1", features=[gpu_queue])
capacity_deployments_v1 = FeatureService(name="capacity_deployments_v1", features=[model_usage])
