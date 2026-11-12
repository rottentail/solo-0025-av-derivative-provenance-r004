"""Recompute a completed rendition and compare it with recorded output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import RenderFailed, UnsupportedMedia
from ..media import MediaOperationFailure, apply_media_operations
from ..models import MediaAsset
from ..serialization import sha256_hex


@dataclass(frozen=True)
class VerificationOutcome:
    status: str
    expected_digest: str
    reproduced_digest: str | None
    expected_descriptor: dict[str, Any]
    reproduced_descriptor: dict[str, Any] | None
    reason: str | None


def reproduce_output(
    *,
    output_asset: MediaAsset,
    recorded_output_bytes: bytes,
    primary_bytes: bytes,
    operations: list[dict[str, Any]],
    secondary_bytes: bytes | None,
) -> VerificationOutcome:
    expected_descriptor = output_asset.descriptor.to_dict()
    expected_digest = output_asset.sha256
    recorded_digest = sha256_hex(recorded_output_bytes)
    if recorded_digest != expected_digest:
        return VerificationOutcome(
            status="mismatch",
            expected_digest=expected_digest,
            reproduced_digest=None,
            expected_descriptor=expected_descriptor,
            reproduced_descriptor=None,
            reason="recorded_output_digest_drift",
        )

    try:
        outcome = apply_media_operations(
            primary_bytes,
            operations,
            secondary_bytes,
        )
    except (MediaOperationFailure, UnsupportedMedia, RenderFailed) as exc:
        # UnsupportedMedia here means a parent blob cannot be opened by this
        # interpreter's wave module. On Python <= 3.11 that includes any
        # WAVE_FORMAT_EXTENSIBLE source (stdlib "unknown format: 65534"), so a
        # rendition created on 3.12+ can replay to a durable mismatch with
        # reason UNSUPPORTED_MEDIA on 3.11 without any byte drift. See README
        # "Cross-runtime WAV handoff"; the mismatch record is retained as
        # evidence and must not be rewritten into a pass.
        reason = getattr(exc, "code", "reproduction_failed")
        return VerificationOutcome(
            status="mismatch",
            expected_digest=expected_digest,
            reproduced_digest=None,
            expected_descriptor=expected_descriptor,
            reproduced_descriptor=None,
            reason=str(reason),
        )

    reproduced_digest = sha256_hex(outcome.data)
    reproduced_descriptor = outcome.descriptor.to_dict()
    if reproduced_digest != expected_digest:
        return VerificationOutcome(
            status="mismatch",
            expected_digest=expected_digest,
            reproduced_digest=reproduced_digest,
            expected_descriptor=expected_descriptor,
            reproduced_descriptor=reproduced_descriptor,
            reason="reproduced_bytes_differ",
        )
    if reproduced_descriptor != expected_descriptor:
        return VerificationOutcome(
            status="mismatch",
            expected_digest=expected_digest,
            reproduced_digest=reproduced_digest,
            expected_descriptor=expected_descriptor,
            reproduced_descriptor=reproduced_descriptor,
            reason="reproduced_descriptor_differs",
        )
    return VerificationOutcome(
        status="verified",
        expected_digest=expected_digest,
        reproduced_digest=reproduced_digest,
        expected_descriptor=expected_descriptor,
        reproduced_descriptor=reproduced_descriptor,
        reason=None,
    )
