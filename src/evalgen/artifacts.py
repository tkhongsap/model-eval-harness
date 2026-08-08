"""Durable and privacy-aware artifact primitives for paid evaluation runs.

The scoring package stays network-free; this module sits in ``evalgen`` because it
owns execution artifacts.  It deliberately knows nothing about models or metrics.
Its contracts are small:

* private/internal/customer artifacts must resolve below ``EVAL_HARNESS_DATA_DIR``;
* writes that become authoritative are atomic;
* a run journal fsyncs every completed cell and is bound to one immutable contract;
* shareable payloads are allowlisted recursively rather than protected by a flat
  top-level column check.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from evalharness.paths import UnsafeDataDir, data_dir

__all__ = [
    "ArtifactError",
    "DATA_CLASSIFICATIONS",
    "RunJournal",
    "assert_shareable_payload",
    "append_jsonl",
    "atomic_write_bytes",
    "atomic_write_text",
    "canonical_sha",
    "file_sha256",
    "require_private_destination",
]

DATA_CLASSIFICATIONS = ("synthetic", "internal", "customer")
_JOURNAL_SCHEMA = 2

# Free-form values under any of these keys are private even when the parent object is
# otherwise an aggregate report.  The list is intentionally semantic: a phone number
# placed under ``cited_span`` is still a leak even though the key is not ``phone``.
_PRIVATE_KEYS = frozenset(
    {
        "phone",
        "phone_number",
        "msisdn",
        "customer_name",
        "agent_name",
        "transcript",
        "transcript_th",
        "raw",
        "raw_content",
        "raw_output",
        "raw_response_text",
        "completion_raw",
        "cited_span",
        "rationale",
        "messages",
        "request",
        "payload",
    }
)
_PHONE_LIKE = re.compile(r"(?<!\d)(?:\+?66|0)\d{8,9}(?!\d)")


class ArtifactError(RuntimeError):
    """An artifact is unsafe, incomplete, corrupt, or belongs to another run."""


def canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_private_destination(path: Path) -> Path:
    """Return the resolved destination only when it is below the approved data root."""
    try:
        root = data_dir()
    except UnsafeDataDir as exc:
        raise ArtifactError(str(exc)) from exc
    candidate = Path(path).expanduser().resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise ArtifactError(
            f"private artifact destination {candidate} is outside {root}. Set "
            "EVAL_HARNESS_DATA_DIR to an existing directory outside Git and write "
            "private artifacts below it."
        )
    return candidate


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write bytes durably, then atomically replace the destination."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        _fsync_directory(target.parent)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        finally:
            raise


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8/LF text durably, then atomically replace the destination."""
    atomic_write_bytes(Path(path), text.encode("utf-8"))


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    """Append one canonical JSON object and fsync it before returning.

    This is used for paid-call evidence that must survive a later transport or parser
    failure.  The file is append-only at this layer; callers are responsible for
    refusing an existing file when mixing two executions would be ambiguous.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "ab", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    _fsync_directory(target.parent)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability; Windows does not expose directory fsync."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def assert_shareable_payload(value: Any, *, path: str = "$") -> None:
    """Recursively refuse private fields and obvious phone-like values.

    This is a final guard, not a redactor.  Shareable schemas should omit private
    fields deliberately; silently editing them here would make the stored hash describe
    content different from the content a reviewer approved.
    """
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().lower()
            child_path = f"{path}.{raw_key}"
            if key in _PRIVATE_KEYS:
                raise ArtifactError(
                    f"shareable artifact contains private field {child_path}. Store it "
                    "in the private bundle and expose only a hash or HMAC identifier."
                )
            assert_shareable_payload(child, path=child_path)
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_shareable_payload(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and _PHONE_LIKE.search(value):
        raise ArtifactError(
            f"shareable artifact contains a phone-like value at {path}. Use a stable "
            "HMAC item id; do not redact after hashing."
        )


@dataclass
class RunJournal:
    """Append-only, fsynced result journal bound to one immutable run contract.

    Rows are generic mappings so this module remains independent of ``runner``.  The
    caller owns the row schema and can reconstruct its typed result without a network
    call.  Duplicate logical cells are refused both while appending and while loading.
    """

    path: Path
    contract: Mapping[str, Any]
    _rows: dict[tuple[str, int], dict[str, Any]]
    _started: set[tuple[str, int]]
    _trailing_torn_record: bool = False

    @classmethod
    def create(cls, path: Path, contract: Mapping[str, Any]) -> "RunJournal":
        target = Path(path)
        if target.exists():
            raise ArtifactError(
                f"journal already exists: {target}. Open it for resume instead of "
                "overwriting paid evidence."
            )
        header = {
            "type": "run_journal",
            "schema_version": _JOURNAL_SCHEMA,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "contract": dict(contract),
            "contract_sha": canonical_sha(contract),
        }
        atomic_write_text(target, json.dumps(header, ensure_ascii=False, sort_keys=True) + "\n")
        return cls(path=target, contract=dict(contract), _rows={}, _started=set())

    @classmethod
    def open(cls, path: Path, expected_contract: Mapping[str, Any]) -> "RunJournal":
        target = Path(path)
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ArtifactError(f"cannot read run journal {target}: {exc}") from exc
        if not lines:
            raise ArtifactError(f"run journal {target} is empty")
        try:
            header = json.loads(lines[0])
        except (json.JSONDecodeError, TypeError) as exc:
            raise ArtifactError(f"run journal {target} has an invalid header: {exc}") from exc
        expected_sha = canonical_sha(expected_contract)
        if (
            not isinstance(header, dict)
            or header.get("type") != "run_journal"
            or header.get("schema_version") not in {1, _JOURNAL_SCHEMA}
            or header.get("contract_sha") != expected_sha
            or header.get("contract") != dict(expected_contract)
        ):
            raise ArtifactError(
                f"run journal {target} belongs to a different or unsupported run "
                f"contract (expected {expected_sha})"
            )

        schema_version = int(header["schema_version"])
        rows: dict[tuple[str, int], dict[str, Any]] = {}
        started: set[tuple[str, int]] = set()
        trailing_torn = False
        for line_number, line in enumerate(lines[1:], start=2):
            if not line.strip():
                continue
            try:
                envelope = json.loads(line)
            except (json.JSONDecodeError, TypeError) as exc:
                if line_number == len(lines):
                    # Recover every earlier fsynced event. A torn final result leaves
                    # its complete ``started`` event unresolved, so automatic replay
                    # is still refused rather than guessing whether the server ran it.
                    trailing_torn = True
                    break
                raise ArtifactError(
                    f"run journal {target}:{line_number} is invalid JSON: {exc}"
                ) from exc
            if not isinstance(envelope, dict):
                raise ArtifactError(f"run journal {target}:{line_number} is not an event")
            event_type = envelope.get("event", "result" if schema_version == 1 else None)
            if event_type == "started":
                event = {
                    "event": "started",
                    "item_id": envelope.get("item_id"),
                    "replicate": envelope.get("replicate"),
                }
                if envelope.get("event_sha") != canonical_sha(event):
                    raise ArtifactError(
                        f"run journal {target}:{line_number} failed its event hash"
                    )
                cell = _journal_cell(event, target=target, line_number=line_number)
                if cell in started or cell in rows:
                    raise ArtifactError(f"run journal {target} repeats start for cell {cell}")
                started.add(cell)
                continue
            if event_type != "result" or not isinstance(envelope.get("row"), dict):
                raise ArtifactError(f"run journal {target}:{line_number} is not a result event")
            row = envelope["row"]
            if envelope.get("row_sha") != canonical_sha(row):
                raise ArtifactError(f"run journal {target}:{line_number} failed its row hash")
            cell = _journal_cell(row, target=target, line_number=line_number)
            if cell in rows:
                raise ArtifactError(f"run journal {target} repeats logical cell {cell}")
            if schema_version >= 2 and cell not in started:
                raise ArtifactError(
                    f"run journal {target}:{line_number} records a result before its "
                    f"durable start event for cell {cell}"
                )
            rows[cell] = row
        return cls(
            path=target,
            contract=dict(expected_contract),
            _rows=rows,
            _started=started,
            _trailing_torn_record=trailing_torn,
        )

    def completed_rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self._rows.values())

    def unresolved_cells(self) -> tuple[tuple[str, int], ...]:
        """Cells dispatched but lacking a durable result, never safe to auto-replay."""
        return tuple(sorted(self._started - set(self._rows)))

    @property
    def trailing_torn_record(self) -> bool:
        return self._trailing_torn_record

    def mark_started(self, item_id: str, replicate: int) -> None:
        event = {"event": "started", "item_id": str(item_id), "replicate": replicate}
        cell = _journal_cell(event)
        if cell in self._started or cell in self._rows:
            raise ArtifactError(f"refusing duplicate journal start for cell {cell}")
        envelope = {**event, "event_sha": canonical_sha(event)}
        append_jsonl(self.path, envelope)
        self._started.add(cell)

    def append(self, row: Mapping[str, Any]) -> None:
        try:
            cell = (str(row["item_id"]), int(row["replicate"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactError("journal row needs item_id and positive integer replicate") from exc
        if cell[1] < 1:
            raise ArtifactError(f"journal row has invalid replicate {cell[1]}")
        if cell in self._rows:
            raise ArtifactError(f"refusing duplicate journal cell {cell}")
        if cell not in self._started:
            raise ArtifactError(
                f"refusing result for cell {cell} before a durable started event"
            )
        payload = dict(row)
        envelope = {"event": "result", "row": payload, "row_sha": canonical_sha(payload)}
        encoded = (json.dumps(envelope, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND)
        try:
            with os.fdopen(descriptor, "ab", closefd=False) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        self._rows[cell] = payload


def _journal_cell(
    value: Mapping[str, Any],
    *,
    target: Path | None = None,
    line_number: int | None = None,
) -> tuple[str, int]:
    try:
        item_id = value["item_id"]
        replicate = value["replicate"]
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("item_id must be a non-empty string")
        if isinstance(replicate, bool) or not isinstance(replicate, int) or replicate < 1:
            raise ValueError("replicate must be a positive integer")
    except (KeyError, TypeError, ValueError) as exc:
        location = f" {target}:{line_number}" if target is not None else ""
        raise ArtifactError(
            f"run journal{location} has no valid item/replicate identity"
        ) from exc
    return item_id, replicate
