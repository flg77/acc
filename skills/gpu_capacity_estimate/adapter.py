"""gpu_capacity_estimate — vLLM memory sizing (thin wrapper over acc.gpu_sizing)."""

from __future__ import annotations

from typing import Any

from acc import gpu_sizing
from acc.skills import Skill
from acc.skills.skill_runtime import SkillInvocationError

_PASSTHROUGH = (
    "max_model_len",
    "tensor_parallel_size",
    "gpu_memory_utilization",
    "kv_cache_dtype",
    "weight_bytes_per_param",
    "num_parameters",
    "avg_tokens_per_request",
    "target_concurrency",
    "max_num_batched_tokens",
)


class GpuCapacityEstimateSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        kwargs = {k: args[k] for k in _PASSTHROUGH if args.get(k) is not None}
        try:
            return gpu_sizing.estimate(args["model_config"], args["gpu"], **kwargs)
        except ValueError as exc:
            raise SkillInvocationError(f"gpu_capacity_estimate: {exc}") from exc
