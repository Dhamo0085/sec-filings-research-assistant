"""T4-07: the V0 baseline reads the pre-rewrite index, by configuration alone.

P4-00 (D25) rewrites section boundaries, which changes what a chunk contains and
therefore what every retrieval number means. The V0 variant in P4-05 is supposed
to be v1's own behaviour, so it has to read v1's own index — and it has to do so
without a code change, or V0 stops being the thing it claims to measure.

``data/qdrant_v1_backup/`` is that index, copied before anything was re-indexed.
These tests pin the two properties the comparison depends on: the backup is
selectable through configuration, and the two stores never share a path.

The other half of T4-07 — that a chunk id present only in the v1 index is
retrievable from the backup — needs both indexes to exist on disk and so lands
with the P4-05 runner; it is not something an offline unit test can assert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

V1_BACKUP_DIRNAME = "qdrant_v1_backup"


def _settings_with(monkeypatch, **env):
    """A freshly constructed Settings, so env overrides are actually read."""
    import config

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return config.Settings()


def test_qdrant_path_is_overridable_by_environment(monkeypatch, tmp_path):
    """No code change: QDRANT_PATH alone repoints the store.

    P4-00 requires V0 to be pointed at the backup "by configuration, not by code
    change". That only holds while qdrant_path stays an ordinary settings field,
    so this fails if someone hard-codes the path back into the client.
    """
    backup = tmp_path / V1_BACKUP_DIRNAME
    settings = _settings_with(monkeypatch, QDRANT_PATH=str(backup))
    assert Path(settings.qdrant_path) == backup


def test_the_backup_and_the_live_index_are_never_the_same_store(monkeypatch, tmp_path):
    """A shared path would silently make V0 read the rewritten index."""
    import config

    live = tmp_path / "qdrant"
    backup = tmp_path / V1_BACKUP_DIRNAME

    monkeypatch.setenv("QDRANT_PATH", str(live))
    live_settings = config.Settings()
    monkeypatch.setenv("QDRANT_PATH", str(backup))
    backup_settings = config.Settings()

    assert Path(live_settings.qdrant_path) != Path(backup_settings.qdrant_path)
    # Neither is a parent of the other: a nested backup would be scanned as a
    # collection of the live store (Qdrant local mode walks its storage folder).
    assert backup not in Path(live_settings.qdrant_path).parents
    assert live not in Path(backup_settings.qdrant_path).parents


def test_the_default_store_is_not_the_backup(monkeypatch):
    """The default configuration must never be the frozen v1 copy.

    Negative control for the two tests above: they would both still pass if
    qdrant_path defaulted to the backup, and every post-rewrite measurement
    would then silently be a v1 measurement.
    """
    monkeypatch.delenv("QDRANT_PATH", raising=False)
    import config

    assert Path(config.Settings().qdrant_path).name != V1_BACKUP_DIRNAME
