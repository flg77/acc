"""Capturing a deployment, and refusing to hand back a broken one.

The load-bearing test scans a real archive for planted credential values. The
claim "we do not include secrets" is worth nothing asserted about the code that
writes the archive; it has to be checked against the bytes that come out.

Everything else is about refusing rather than half-doing:

* restoring onto a host with no credentials reports which names are missing and
  writes nothing — a deployment that looks restored and cannot authenticate is
  the worst outcome available;
* restoring over existing files needs an explicit acknowledgement, because the
  moment someone restores the wrong archive is the moment they most need it to
  have asked;
* an archive from another format version refuses with a reason rather than
  writing files a different schema will reject at boot.
"""

from __future__ import annotations

import json
import tarfile

import pytest

from acc import backup as B

SECRET_A = "sk-planted-anthropic-value"
SECRET_B = "redis-planted-password"

MODELS = """\
models:
  - model_id: cloud
    backend: anthropic
    model: claude-haiku-4-6
    api_key_env: ANTHROPIC_API_KEY
role_models:
  analyst: cloud
"""

ACC_CONFIG = """\
deploy_mode: standalone
operator_mode: prod
llm:
  backend: anthropic
  api_key_env: ANTHROPIC_API_KEY
working_memory:
  url: "redis://acc-redis:6379"
  password: "{secret_b}"
"""


@pytest.fixture
def host(tmp_path, monkeypatch):
    (tmp_path / "acc-config.yaml").write_text(
        ACC_CONFIG.format(secret_b=SECRET_B), encoding="utf-8"
    )
    (tmp_path / "models.yaml").write_text(MODELS, encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"ANTHROPIC_API_KEY={SECRET_A}\nREDIS_PASSWORD={SECRET_B}\n", encoding="utf-8"
    )
    (tmp_path / "catalogs.yaml").write_text("catalogs: []\n", encoding="utf-8")
    monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "acc-config.yaml"))
    monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "models.yaml"))
    monkeypatch.setenv("ACC_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setenv("ACC_CATALOGS_PATH", str(tmp_path / "catalogs.yaml"))
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "collective.yaml"))
    return tmp_path


# --------------------------------------------------------------------------
# The claim that has to be checked against bytes
# --------------------------------------------------------------------------


class TestNoSecretValuesInAnArchive:
    def test_no_planted_secret_appears_anywhere(self, host, tmp_path):
        """Scans the real archive, not the code that wrote it."""
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        assert B.scan_for_secrets(archive, [SECRET_A, SECRET_B]) == []

    def test_the_env_file_is_not_captured_at_all(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        with tarfile.open(archive) as tar:
            names = tar.getnames()
        assert not any(name.endswith(".env") for name in names)

    def test_a_secret_in_a_config_file_is_redacted(self, host, tmp_path):
        """Belt and braces for a key the schema has not marked."""
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        with tarfile.open(archive) as tar:
            body = tar.extractfile("config/acc-config.yaml").read().decode()
        assert SECRET_B not in body
        assert "redacted" in body

    def test_non_secret_configuration_survives_redaction(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        with tarfile.open(archive) as tar:
            body = tar.extractfile("config/acc-config.yaml").read().decode()
        assert "deploy_mode: standalone" in body
        assert "api_key_env: ANTHROPIC_API_KEY" in body, (
            "a *_env key names a variable and must not be redacted"
        )

    def test_the_manifest_records_required_secret_names(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        manifest = B.create(archive, repo_root=host)
        assert "ANTHROPIC_API_KEY" in manifest.required_secrets
        assert SECRET_A not in json.dumps(manifest.as_dict())


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


class TestRestoreRefuses:
    def test_missing_secrets_stop_the_restore(self, host, tmp_path, monkeypatch):
        """A deployment that looks restored and cannot authenticate is the worst case."""
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)

        target = tmp_path / "fresh"
        target.mkdir()
        with pytest.raises(B.BackupError, match="ANTHROPIC_API_KEY"):
            B.restore(archive, repo_root=target, environ={})
        assert list(target.iterdir()) == [], "nothing may be written"

    def test_missing_secrets_can_be_acknowledged(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        target = tmp_path / "fresh2"
        target.mkdir()
        B.restore(archive, repo_root=target, environ={}, allow_missing_secrets=True)
        assert (target / "acc-config.yaml").is_file()

    def test_overwriting_needs_an_explicit_acknowledgement(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        with pytest.raises(B.BackupError, match="--force"):
            B.restore(
                archive, repo_root=host,
                environ={"ANTHROPIC_API_KEY": "x", "REDIS_PASSWORD": "y"},
            )

    def test_force_allows_the_overwrite(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        B.restore(
            archive, repo_root=host, force=True,
            environ={"ANTHROPIC_API_KEY": "x", "REDIS_PASSWORD": "y"},
        )

    def test_an_incompatible_archive_version_refuses(self, host, tmp_path, monkeypatch):
        archive = tmp_path / "b.tar.gz"
        monkeypatch.setattr(B, "ARCHIVE_VERSION", 99)
        B.create(archive, repo_root=host)
        monkeypatch.setattr(B, "ARCHIVE_VERSION", 1)

        target = tmp_path / "fresh3"
        target.mkdir()
        with pytest.raises(B.BackupError, match="archive format"):
            B.restore(archive, repo_root=target, environ={}, allow_missing_secrets=True)

    def test_a_non_archive_is_refused_clearly(self, tmp_path):
        bogus = tmp_path / "notes.txt"
        bogus.write_text("hello", encoding="utf-8")
        with pytest.raises(B.BackupError, match="not a readable ACC backup"):
            B.read_manifest(bogus)

    def test_an_unknown_tier_is_refused(self, host, tmp_path):
        with pytest.raises(B.BackupError, match="unknown backup tier"):
            B.create(tmp_path / "b.tar.gz", tiers=["config", "moon"], repo_root=host)


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


class TestPlan:
    def test_the_plan_names_exactly_what_would_be_replaced(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        result = B.plan(archive, repo_root=host, environ={})
        assert any(name.endswith("acc-config.yaml") for name in result.would_replace)

    def test_the_plan_distinguishes_create_from_replace(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        target = tmp_path / "fresh4"
        target.mkdir()
        result = B.plan(archive, repo_root=target, environ={})
        assert result.would_replace == []
        assert result.would_create

    def test_the_plan_reports_missing_secrets_without_writing(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        result = B.plan(archive, repo_root=host, environ={})
        assert "ANTHROPIC_API_KEY" in result.missing_secrets
        assert not result.ok

    def test_a_plan_with_everything_present_is_ok(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        B.create(archive, repo_root=host)
        result = B.plan(
            archive, repo_root=host,
            environ={"ANTHROPIC_API_KEY": "x", "REDIS_PASSWORD": "y"},
        )
        assert result.ok


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


class TestRoundTrip:
    def test_a_configuration_change_can_be_restored(self, host, tmp_path):
        """The criterion: back up, change, restore, compare resolved state."""
        from acc import configstore as store

        archive = tmp_path / "before.tar.gz"
        B.create(archive, repo_root=host)
        original = store.get("deploy_mode", repo_root=host).value

        store.set_key("deploy_mode", "rhoai", repo_root=host)
        assert store.get("deploy_mode", repo_root=host).value == "rhoai"

        B.restore(
            archive, repo_root=host, force=True,
            environ={"ANTHROPIC_API_KEY": "x", "REDIS_PASSWORD": "y"},
        )
        assert store.get("deploy_mode", repo_root=host).value == original

    def test_the_manifest_survives_the_round_trip(self, host, tmp_path):
        archive = tmp_path / "b.tar.gz"
        written = B.create(archive, label="nightly", repo_root=host)
        read = B.read_manifest(archive)
        assert read.label == "nightly"
        assert read.tiers == written.tiers
        assert read.acc_version == written.acc_version

    def test_tiers_control_what_is_captured(self, host, tmp_path):
        archive = tmp_path / "cfg-only.tar.gz"
        manifest = B.create(archive, tiers=["config"], repo_root=host)
        assert all(name.startswith("config/") for name in manifest.files)
