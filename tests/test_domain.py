"""Tests for acc/domain.py — DomainRegistry and RubricValidator (ACC-11).

Covers:
- DomainRegistry.update_domain_centroid (EMA math; GOOD-only update)
- DomainRegistry.get_domain_centroid (in-memory cache; Redis round-trip)
- DomainRegistry.compute_domain_drift (cosine distance)
- DomainRegistry.register_rubric / get_rubric_criteria
- DomainRegistry.validate_eval_outcome
- RubricValidator.load_rubric / compute_hash / validate
"""

import json
import math
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from acc.domain import DomainRegistry, RubricValidator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry_no_redis():
    return DomainRegistry(redis_client=None, collective_id="sol-01")


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def registry_with_redis(mock_redis):
    return DomainRegistry(redis_client=mock_redis, collective_id="sol-01")


# ---------------------------------------------------------------------------
# DomainRegistry — centroid update (EMA math)
# ---------------------------------------------------------------------------

class TestDomainRegistryCentroid:

    @pytest.mark.asyncio
    async def test_first_good_outcome_seeds_centroid(self, registry_no_redis):
        """First GOOD outcome seeds the centroid directly (no prior centroid)."""
        embedding = [1.0, 0.0, 0.0]
        new_centroid = await registry_no_redis.update_domain_centroid(
            "software_engineering", embedding, is_good_outcome=True
        )
        assert new_centroid == embedding

    @pytest.mark.asyncio
    async def test_bad_outcome_does_not_update_centroid(self, registry_no_redis):
        """BAD outcomes are ignored — domain centroid tracks only GOOD work."""
        seed = [1.0, 0.0, 0.0]
        await registry_no_redis.update_domain_centroid(
            "se", seed, is_good_outcome=True
        )
        bad_embedding = [0.0, 1.0, 0.0]
        result = await registry_no_redis.update_domain_centroid(
            "se", bad_embedding, is_good_outcome=False
        )
        # Centroid should still be the seeded value
        assert result == seed

    @pytest.mark.asyncio
    async def test_ema_update_math(self, registry_no_redis):
        """EMA: new = (1-0.1)*centroid + 0.1*embedding."""
        seed = [1.0, 0.0, 0.0]
        await registry_no_redis.update_domain_centroid(
            "se", seed, is_good_outcome=True
        )
        second = [0.0, 1.0, 0.0]
        result = await registry_no_redis.update_domain_centroid(
            "se", second, is_good_outcome=True
        )
        expected = [
            0.9 * seed[i] + 0.1 * second[i]
            for i in range(3)
        ]
        for r, e in zip(result, expected):
            assert abs(r - e) < 1e-9

    @pytest.mark.asyncio
    async def test_zero_embedding_does_not_update(self, registry_no_redis):
        """All-zero embedding leaves centroid unchanged (no information content)."""
        seed = [1.0, 0.0, 0.0]
        await registry_no_redis.update_domain_centroid(
            "se", seed, is_good_outcome=True
        )
        zero = [0.0, 0.0, 0.0]
        result = await registry_no_redis.update_domain_centroid(
            "se", zero, is_good_outcome=True
        )
        assert result == seed

    @pytest.mark.asyncio
    async def test_get_domain_centroid_returns_empty_when_unset(self, registry_no_redis):
        result = await registry_no_redis.get_domain_centroid("unknown_domain")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_domain_centroid_uses_in_memory_cache(self, registry_no_redis):
        seed = [0.5, 0.5, 0.0]
        await registry_no_redis.update_domain_centroid("se", seed, is_good_outcome=True)
        result = await registry_no_redis.get_domain_centroid("se")
        assert result == seed

    @pytest.mark.asyncio
    async def test_redis_round_trip(self, registry_with_redis, mock_redis):
        """update_domain_centroid persists to Redis; get loads from Redis on cache miss."""
        centroid = [0.1, 0.2, 0.3]
        mock_redis.get.return_value = json.dumps(centroid).encode()

        # Clear in-memory cache to force Redis load
        domain = DomainRegistry(redis_client=mock_redis, collective_id="sol-01")
        result = await domain.get_domain_centroid("se")
        assert result == centroid
        mock_redis.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_set_called_on_update(self, registry_with_redis, mock_redis):
        seed = [1.0, 0.0, 0.0]
        await registry_with_redis.update_domain_centroid(
            "software_engineering", seed, is_good_outcome=True
        )
        mock_redis.set.assert_called_once()
        args = mock_redis.set.call_args[0]
        assert "domain_centroid" in args[0]
        saved = json.loads(args[1])
        assert saved == seed


# ---------------------------------------------------------------------------
# DomainRegistry — domain drift
# ---------------------------------------------------------------------------

class TestDomainDrift:

    def test_zero_drift_for_identical_vectors(self, registry_no_redis):
        v = [1.0, 0.0, 0.0]
        drift = registry_no_redis.compute_domain_drift(v, v)
        assert drift == pytest.approx(0.0, abs=1e-9)

    def test_max_drift_for_orthogonal_vectors(self, registry_no_redis):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        drift = registry_no_redis.compute_domain_drift(a, b)
        assert drift == pytest.approx(1.0, abs=1e-9)

    def test_drift_between_zero_and_one(self, registry_no_redis):
        a = [1.0, 1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        drift = registry_no_redis.compute_domain_drift(a, b)
        assert 0.0 <= drift <= 1.0

    def test_empty_domain_centroid_returns_zero(self, registry_no_redis):
        drift = registry_no_redis.compute_domain_drift([1.0, 0.0], [])
        assert drift == 0.0

    def test_all_zero_centroid_returns_zero(self, registry_no_redis):
        drift = registry_no_redis.compute_domain_drift([1.0, 0.0], [0.0, 0.0])
        assert drift == 0.0


# ---------------------------------------------------------------------------
# DomainRegistry — rubric registration and validation
# ---------------------------------------------------------------------------

class TestDomainRegistryRubric:

    @pytest.mark.asyncio
    async def test_register_and_get_criteria(self, registry_no_redis):
        await registry_no_redis.register_rubric(
            "software_engineering",
            "sha256:abc123",
            ["correctness", "test_coverage", "security"],
        )
        criteria = registry_no_redis.get_rubric_criteria("software_engineering")
        assert criteria == ["correctness", "test_coverage", "security"]

    def test_unknown_domain_returns_empty_criteria(self, registry_no_redis):
        criteria = registry_no_redis.get_rubric_criteria("nonexistent_domain")
        assert criteria == []

    def test_validate_outcome_valid(self, registry_no_redis):
        registry_no_redis._rubrics["se"] = {
            "hash": "abc",
            "criteria": ["correctness", "test_coverage"],
        }
        valid, reason = registry_no_redis.validate_eval_outcome(
            {"rubric_scores": {"correctness": 0.9, "test_coverage": 0.8}},
            "se",
        )
        assert valid is True
        assert reason == ""

    def test_validate_outcome_unknown_criterion(self, registry_no_redis):
        registry_no_redis._rubrics["se"] = {
            "hash": "abc",
            "criteria": ["correctness"],
        }
        valid, reason = registry_no_redis.validate_eval_outcome(
            {"rubric_scores": {"correctness": 0.9, "narrative_coherence": 0.7}},
            "se",
        )
        assert valid is False
        assert "narrative_coherence" in reason

    def test_validate_outcome_no_registered_rubric_allows(self, registry_no_redis):
        """No registered rubric for domain → validation passes (domain not yet onboarded)."""
        valid, reason = registry_no_redis.validate_eval_outcome(
            {"rubric_scores": {"anything": 0.5}},
            "unknown_domain",
        )
        assert valid is True

    @pytest.mark.asyncio
    async def test_register_rubric_persists_to_redis(self, registry_with_redis, mock_redis):
        await registry_with_redis.register_rubric("se", "sha:abc", ["correctness"])
        mock_redis.set.assert_called_once()
        key_arg = mock_redis.set.call_args[0][0]
        assert "domain_rubric" in key_arg


# ---------------------------------------------------------------------------
# RubricValidator
# ---------------------------------------------------------------------------

class TestRubricValidator:

    @pytest.fixture
    def validator(self):
        return RubricValidator()

    @pytest.fixture
    def rubric_file(self, tmp_path):
        rubric = {
            "criteria": {
                "correctness": {"weight": 0.35, "description": "Code correctness"},
                "test_coverage": {"weight": 0.20, "description": "Test coverage"},
            }
        }
        path = tmp_path / "eval_rubric.yaml"
        with path.open("w") as fh:
            yaml.dump(rubric, fh)
        return path, rubric

    def test_load_rubric_returns_dict(self, validator, rubric_file):
        path, expected = rubric_file
        data = validator.load_rubric(path)
        assert isinstance(data, dict)
        assert "criteria" in data

    def test_load_rubric_missing_file_returns_empty(self, validator, tmp_path):
        data = validator.load_rubric(tmp_path / "nonexistent.yaml")
        assert data == {}

    def test_compute_hash_returns_64_char_hex(self, validator, rubric_file):
        path, data = rubric_file
        rubric_data = validator.load_rubric(path)
        digest = validator.compute_hash(rubric_data)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_compute_hash_empty_returns_empty(self, validator):
        assert validator.compute_hash({}) == ""

    def test_compute_hash_is_stable(self, validator, rubric_file):
        path, _ = rubric_file
        data = validator.load_rubric(path)
        h1 = validator.compute_hash(data)
        h2 = validator.compute_hash(data)
        assert h1 == h2

    def test_compute_hash_differs_for_different_rubrics(self, validator, tmp_path):
        r1 = {"criteria": {"a": {"weight": 1.0}}}
        r2 = {"criteria": {"b": {"weight": 1.0}}}
        p1 = tmp_path / "r1.yaml"
        p2 = tmp_path / "r2.yaml"
        for path, data in [(p1, r1), (p2, r2)]:
            with path.open("w") as fh:
                yaml.dump(data, fh)
        h1 = validator.compute_hash(validator.load_rubric(p1))
        h2 = validator.compute_hash(validator.load_rubric(p2))
        assert h1 != h2

    def test_validate_all_known_criteria_passes(self, validator):
        assert validator.validate(
            {"correctness": 0.9, "test_coverage": 0.8},
            ["correctness", "test_coverage", "security"],
        ) is True

    def test_validate_unknown_criterion_fails(self, validator):
        assert validator.validate(
            {"correctness": 0.9, "narrative": 0.7},
            ["correctness", "test_coverage"],
        ) is False

    def test_validate_empty_registered_criteria_always_passes(self, validator):
        assert validator.validate({"anything": 0.5}, []) is True

    def test_validate_empty_rubric_scores_passes(self, validator):
        assert validator.validate({}, ["correctness"]) is True
