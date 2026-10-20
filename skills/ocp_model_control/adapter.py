"""ocp_model_control — stop / start a KServe model via the serving.kserve.io/stop annotation."""

from __future__ import annotations

import os
from typing import Any

from acc.api_group import annotation
from acc.integrations import cluster_http
from acc.skills import Skill
from acc.skills.skill_runtime import SkillInvocationError

NAMESPACES_ENV = "ACC_OCP_CONTROL_NAMESPACES"
TOKEN_ENV = "ACC_KUBERNETES_TOKEN"
CA_ENV = "ACC_KUBERNETES_CA_FILE"

STOP_ANNOTATION = "serving.kserve.io/stop"
REASON_ANNOTATION = annotation("model-control-reason")

# kind -> (API version, plural) in the serving.kserve.io group.
KINDS = {
    "LLMInferenceService": ("v1alpha1", "llminferenceservices"),
    "InferenceService": ("v1beta1", "inferenceservices"),
}


def allowed_namespaces() -> frozenset[str]:
    raw = os.environ.get(NAMESPACES_ENV, "")
    return frozenset(ns.strip() for ns in raw.split(",") if ns.strip())


def object_url(api: str, kind: str, namespace: str, name: str) -> str:
    version, plural = KINDS[kind]
    return f"{api}/apis/serving.kserve.io/{version}/namespaces/{namespace}/{plural}/{name}"


def patch_body(action: str, reason: str) -> dict[str, Any]:
    return {"metadata": {"annotations": {
        STOP_ANNOTATION: "true" if action == "stop" else "false",
        REASON_ANNOTATION: reason,
    }}}


class OcpModelControlSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        action, namespace, name = args["action"], args["namespace"], args["name"]
        kind = args.get("kind", "LLMInferenceService")
        dry_run = bool(args.get("dry_run", False))

        # The allowlist is the deployment's, never the caller's: checked before
        # anything leaves the pod, and an empty list allows nothing.
        allowed = allowed_namespaces()
        if not allowed:
            raise SkillInvocationError(
                f"ocp_model_control: {NAMESPACES_ENV} is not set — no namespace is allowed (fail-closed)."
            )
        if namespace not in allowed:
            raise SkillInvocationError(
                f"ocp_model_control: namespace {namespace!r} is not in {NAMESPACES_ENV} (refused)."
            )
        if kind not in KINDS:
            raise SkillInvocationError(f"ocp_model_control: unsupported kind {kind!r}")
        api = cluster_http.kubernetes_api_url()
        if not api:
            raise SkillInvocationError(
                "ocp_model_control: no Kubernetes API (not in a pod and ACC_KUBERNETES_API_URL unset)."
            )
        token = cluster_http.bearer_token(TOKEN_ENV)
        if not token:
            raise SkillInvocationError("ocp_model_control: no service-account token (fail-closed).")

        try:
            obj = await cluster_http.request_json(
                "PATCH", object_url(api, kind, namespace, name),
                token=token,
                params={"dryRun": "All"} if dry_run else None,
                json_body=patch_body(action, args["reason"]),
                headers={"Content-Type": "application/merge-patch+json"},
                verify=cluster_http.ca_bundle(CA_ENV, prefer=cluster_http.CLUSTER_CA),
                timeout_s=30.0,
            )
        except cluster_http.ClusterHTTPError as exc:
            raise SkillInvocationError(f"ocp_model_control: {exc}") from exc
        annotations = ((obj or {}).get("metadata") or {}).get("annotations") or {}
        return {
            "action": action,
            "kind": kind,
            "namespace": namespace,
            "name": name,
            "dry_run": dry_run,
            "stopped": annotations.get(STOP_ANNOTATION) == "true",
        }
