"""ACC OWASP LLM Top 10 Guardrail Engine (ACC-12).

Provides in-process guardrails covering the most critical OWASP LLM Top 10
attack categories:

    LLM01  Prompt Injection
    LLM02  Insecure Output Handling
    LLM04  Model Denial of Service
    LLM06  Sensitive Information Disclosure / PHI
    LLM08  Excessive Agency

All guardrails run concurrently via ``asyncio.gather`` in :class:`GuardrailEngine`.
A ``CRITICAL`` violation short-circuits and blocks the task.
A ``MEDIUM`` violation logs and continues (with redaction applied where relevant).

Guardrails are individually disableable via
``ComplianceConfig.disabled_guardrails``.

Usage::

    engine = GuardrailEngine(compliance_config)
    pre_result = await engine.pre_llm(prompt, role)
    if not pre_result.passed and pre_result.risk_level == "CRITICAL":
        return blocked_result(pre_result.violations)
    # ... LLM call ...
    post_result = await engine.post_llm(output, role)
    stored_output = post_result.redacted_content or output
"""

from acc.guardrails.engine import GuardrailEngine, GuardrailResult

__all__ = ["GuardrailEngine", "GuardrailResult"]
