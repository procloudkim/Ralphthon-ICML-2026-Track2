"""Atomic, secret-redacting persistence for isolated paper artifacts."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from threading import Lock
from typing import TYPE_CHECKING, Final, override

if TYPE_CHECKING:
    from _thread import LockType
    from collections.abc import Mapping


type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)

_REDACTED: Final = "[REDACTED]"
_SAFE_COMPONENT_RE: Final = re.compile(r"[^A-Za-z0-9._-]+")
_CAMEL_BOUNDARY_RE: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_KEY_PART_RE: Final = re.compile(r"[^a-z0-9]+")
_AUTH_SECRET_RE: Final = re.compile(
    r"(?i)\b(authorization\s*:\s*bearer\s+)[^\s,;]+",
)
_NAMED_VALUE_PREFIX: Final = (
    r"(?i)\b((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|"
)
_NAMED_SECRET_RE: Final = re.compile(
    _NAMED_VALUE_PREFIX + r"secret|cookie|credential)\s*[:=]\s*)[^\s,;]+",
)
_OPENAI_KEY_RE: Final = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_SECRET_KEY_WORDS: Final = frozenset(
    {"authorization", "cookie", "credential", "password", "secret", "token"},
)
_SECRET_KEY_COMPACTS: Final = frozenset({"apikey", "accesstoken", "refreshtoken"})
_WINDOWS_RESERVED: Final = frozenset(
    {"AUX", "CON", "NUL", "PRN"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)},
)
_CONTROL_CODEPOINT_LIMIT: Final = 32


@dataclass(frozen=True, slots=True)
class ArtifactPathError(ValueError):
    """An identifier cannot be mapped safely beneath the artifact root."""

    field: str
    value: str
    reason: str

    @override
    def __str__(self) -> str:
        return f"unsafe {self.field}: {self.reason}"


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """Committed artifact metadata returned to the pipeline."""

    stage: str
    path: Path
    manifest_path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _ArtifactWrite:
    stage: str
    suffix: str
    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class ArtifactStore:
    """Persist isolated stages and stream completion from one process safely."""

    root: Path
    _lock: LockType = field(default_factory=Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Create and canonicalize the caller-owned artifact root."""
        self.root.mkdir(parents=True, exist_ok=True)
        object.__setattr__(self, "root", self.root.resolve())

    @property
    def completion_path(self) -> Path:
        """Return the append-only batch completion stream path."""
        return self.root / "completions.jsonl"

    def paper_directory(self, paper_id: str) -> Path:
        """Create and return the isolated directory for one trusted paper ID."""
        component = _safe_component(paper_id, field="paper_id")
        candidate = (self.root / component).resolve()
        if candidate.parent != self.root:
            raise ArtifactPathError(
                field="paper_id",
                value=paper_id,
                reason="resolved path escapes the artifact root",
            )
        candidate.mkdir(parents=False, exist_ok=True)
        return candidate

    def write_json(
        self,
        paper_id: str,
        stage: str,
        payload: JsonValue,
    ) -> ArtifactRecord:
        """Atomically persist redacted JSON and its stage manifest."""
        redacted = _redact_json(payload)
        return self._commit(
            paper_id,
            _ArtifactWrite(
                stage=stage,
                suffix=".json",
                media_type="application/json",
                data=_encode_json(redacted, compact=False),
            ),
        )

    def write_text(self, paper_id: str, stage: str, text: str) -> ArtifactRecord:
        """Atomically persist redacted UTF-8 text and its stage manifest."""
        return self._commit(
            paper_id,
            _ArtifactWrite(
                stage=stage,
                suffix=".txt",
                media_type="text/plain; charset=utf-8",
                data=_redact_text(text).encode(),
            ),
        )

    def append_completion(
        self,
        paper_id: str,
        payload: Mapping[str, JsonValue],
    ) -> bool:
        """Append one redacted receipt, returning false for an exact rerun."""
        paper_directory = self.paper_directory(paper_id)
        envelope: JsonValue = {
            "paper_directory": paper_directory.name,
            "paper_id": paper_id,
            "result": {
                key: _REDACTED if _is_secret_key(key) else _redact_json(item)
                for key, item in payload.items()
            },
        }
        encoded = _encode_json(envelope, compact=True)
        line = encoded.rstrip(b"\n") + b"\n"
        with self._lock, self.completion_path.open("a+b", buffering=0) as stream:
            _ = stream.seek(0)
            if line.rstrip(b"\n") in stream.read().splitlines():
                return False
            _ = stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        return True

    def _commit(self, paper_id: str, write: _ArtifactWrite) -> ArtifactRecord:
        stage_component = _safe_component(write.stage, field="stage")
        paper_directory = self.paper_directory(paper_id)
        artifact_path = paper_directory / f"{stage_component}{write.suffix}"
        manifest_path = artifact_path.with_name(f"{artifact_path.name}.manifest.json")
        digest = sha256(write.data).hexdigest()
        record = ArtifactRecord(
            stage=write.stage,
            path=artifact_path,
            manifest_path=manifest_path,
            sha256=digest,
            size_bytes=len(write.data),
        )
        manifest: JsonValue = {
            "artifact_path": artifact_path.name,
            "media_type": write.media_type,
            "paper_id": paper_id,
            "schema_version": 1,
            "sha256": digest,
            "size_bytes": len(write.data),
            "stage": write.stage,
        }
        with self._lock:
            _atomic_write(artifact_path, write.data)
            _atomic_write(manifest_path, _encode_json(manifest, compact=False))
        return record


def _safe_component(value: str, *, field: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or bool(PureWindowsPath(value).drive)
        or any(ord(character) < _CONTROL_CODEPOINT_LIMIT for character in value)
    ):
        raise ArtifactPathError(
            field=field,
            value=value,
            reason="path syntax is forbidden",
        )
    normalized = unicodedata.normalize("NFKC", value).strip()
    component = _SAFE_COMPONENT_RE.sub("_", normalized).strip("._-")
    if not component:
        component = field
    if component.upper() in _WINDOWS_RESERVED:
        component = f"_{component}"
    if component == value:
        return component
    digest = sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{component}--{digest}"


def _redact_json(value: JsonValue) -> JsonValue:
    # Exhaustive JsonValue union.
    match value:
        case None | bool() | int() | float():
            return value
        case str():
            return _redact_text(value)
        case list():
            return [_redact_json(item) for item in value]
        case dict():
            return {
                key: _REDACTED if _is_secret_key(key) else _redact_json(item)
                for key, item in value.items()
            }


def _is_secret_key(key: str) -> bool:
    expanded = _CAMEL_BOUNDARY_RE.sub("_", key).lower()
    compact = _KEY_PART_RE.sub("", expanded)
    words = frozenset(_KEY_PART_RE.split(expanded))
    return bool(words & _SECRET_KEY_WORDS) or compact in _SECRET_KEY_COMPACTS


def _redact_text(text: str) -> str:
    redacted = _AUTH_SECRET_RE.sub(r"\1[REDACTED]", text)
    redacted = _NAMED_SECRET_RE.sub(r"\1[REDACTED]", redacted)
    return _OPENAI_KEY_RE.sub(_REDACTED, redacted)


def _encode_json(value: JsonValue, *, compact: bool) -> bytes:
    text = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
        sort_keys=True,
    )
    return f"{text}\n".encode()


def _atomic_write(path: Path, data: bytes) -> None:
    try:
        existing_matches = path.read_bytes() == data
    except FileNotFoundError:
        existing_matches = False
    if existing_matches:
        return
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _ = temporary_path.replace(path)
    finally:
        with suppress(FileNotFoundError):
            temporary_path.unlink()
