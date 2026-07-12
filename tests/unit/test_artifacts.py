from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest

from reviewharness.artifacts import ArtifactPathError, ArtifactStore, JsonValue


def test_write_json_commits_artifact_with_hash_manifest(tmp_path: Path) -> None:
    # Given
    store = ArtifactStore(tmp_path / "run")
    payload: JsonValue = {"paper_id": "SAMPLE-001", "scores": [3, 4, 3, 2]}

    # When
    record = store.write_json("SAMPLE-001", "final_review", payload)

    # Then
    artifact_bytes = record.path.read_bytes()
    manifest = record.manifest_path.read_text(encoding="utf-8")
    assert record.path.parent == store.paper_directory("SAMPLE-001")
    assert artifact_bytes.endswith(b"\n")
    assert record.sha256 == sha256(artifact_bytes).hexdigest()
    assert record.size_bytes == len(artifact_bytes)
    assert f'"artifact_path": "{record.path.name}"' in manifest
    assert f'"sha256": "{record.sha256}"' in manifest
    assert '"stage": "final_review"' in manifest


def test_write_text_preserves_utf8_and_redacts_common_secrets(
    tmp_path: Path,
) -> None:
    # Given
    store = ArtifactStore(tmp_path / "run")
    source = (
        "Résumé: café\n"
        "Authorization: Bearer top-secret-token\n"
        "OPENAI_API_KEY=sk-live-value-123456789\n"
    )

    # When
    record = store.write_text("SAMPLE-001", "event_log", source)

    # Then
    persisted = record.path.read_text(encoding="utf-8")
    assert "Résumé: café" in persisted
    assert "top-secret-token" not in persisted
    assert "sk-live-value-123456789" not in persisted
    assert persisted.count("[REDACTED]") == 2


def test_write_json_redacts_nested_secret_fields(tmp_path: Path) -> None:
    # Given
    store = ArtifactStore(tmp_path / "run")
    payload: JsonValue = {
        "public": "retained",
        "credentials": {
            "api_key": "json-secret-value",
            "nested": [{"password": "nested-secret-value"}],
        },
    }

    # When
    record = store.write_json("SAMPLE-001", "assignment", payload)

    # Then
    persisted = record.path.read_text(encoding="utf-8")
    assert '"public": "retained"' in persisted
    assert "json-secret-value" not in persisted
    assert "nested-secret-value" not in persisted
    assert persisted.count("[REDACTED]") == 2


def test_rerun_replaces_stage_idempotently_and_deduplicates_completion(
    tmp_path: Path,
) -> None:
    # Given
    store = ArtifactStore(tmp_path / "run")
    payload: JsonValue = {"status": "complete", "valid": True}

    # When
    first = store.write_json("SAMPLE-001", "final_review", payload)
    first_manifest = first.manifest_path.read_bytes()
    second = store.write_json("SAMPLE-001", "final_review", payload)
    receipt = {"status": "complete", "valid": True}
    first_append = store.append_completion("SAMPLE-001", receipt)
    second_append = store.append_completion("SAMPLE-001", receipt)

    # Then
    assert first.path == second.path
    assert first.sha256 == second.sha256
    assert first_manifest == second.manifest_path.read_bytes()
    assert first_append is True
    assert second_append is False
    assert len(store.completion_path.read_text(encoding="utf-8").splitlines()) == 1
    assert list(store.root.rglob("*.tmp")) == []


def test_completion_stream_keeps_concurrent_receipts_whole(tmp_path: Path) -> None:
    # Given
    store = ArtifactStore(tmp_path / "run")
    paper_ids = [f"PAPER-{index:02d}" for index in range(10)]

    # When
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(
                store.append_completion,
                paper_id,
                {"status": "complete"},
            )
            for paper_id in paper_ids
        ]
        appended = [future.result() for future in futures]

    # Then
    lines = store.completion_path.read_text(encoding="utf-8").splitlines()
    assert appended == [True] * 10
    assert len(lines) == 10
    assert all(line.startswith("{") and line.endswith("}") for line in lines)
    assert all(
        any(f'"paper_id":"{paper_id}"' in line for line in lines)
        for paper_id in paper_ids
    )


def test_paper_directory_uses_collision_resistant_sanitized_id(
    tmp_path: Path,
) -> None:
    # Given
    store = ArtifactStore(tmp_path / "run")

    # When
    paper_directory = store.paper_directory("Paper: 42")

    # Then
    assert paper_directory.parent == store.root
    assert paper_directory.name.startswith("Paper_42--")
    assert paper_directory.is_dir()


@pytest.mark.parametrize(
    "unsafe_id",
    ["../escape", "..\\escape", "/absolute", "C:\\escape", ".", "..", "\x00"],
)
def test_paper_directory_rejects_path_traversal(
    tmp_path: Path,
    unsafe_id: str,
) -> None:
    # Given
    store = ArtifactStore(tmp_path / "run")

    # When
    with pytest.raises(ArtifactPathError) as captured:
        _ = store.paper_directory(unsafe_id)

    # Then
    assert captured.value.field == "paper_id"
    assert captured.value.value == unsafe_id
    assert list(tmp_path.parent.glob("escape")) == []


def test_write_rejects_traversal_in_stage_name(tmp_path: Path) -> None:
    # Given
    store = ArtifactStore(tmp_path / "run")

    # When
    with pytest.raises(ArtifactPathError) as captured:
        _ = store.write_json("SAMPLE-001", "../outside", {"valid": True})

    # Then
    assert captured.value.field == "stage"
    assert list(tmp_path.rglob("outside.json")) == []
