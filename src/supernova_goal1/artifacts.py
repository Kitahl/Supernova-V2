from __future__ import annotations

from collections import namedtuple
from enum import StrEnum
from hashlib import sha256
import unicodedata
from typing import Mapping

from .contracts import Arm


class ScheduledChatArtifactKind(StrEnum):
    REQUEST = "scheduled_chat_request"
    TERMINAL_RESPONSE = "scheduled_chat_terminal_response"


_HTTP_TCHAR_PUNCTUATION = frozenset("!#$%&'*+-.^_`|~")


def _token(value: str, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    if not value or value != value.strip():
        raise ValueError(f"{field} must be an exact non-empty trimmed string")
    if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
        raise ValueError(f"{field} must not contain control or format characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain only Unicode scalar values") from exc
    return value


def _http_token(value: str) -> bool:
    return bool(value) and all(
        char.isascii() and (char.isalnum() or char in _HTTP_TCHAR_PUNCTUATION)
        for char in value
    )


def _attempt(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("attempt must be a non-negative integer")
    return value


def _media_type(value: str) -> str:
    value = _token(value, "media_type")
    if ";" not in value:
        raise ValueError("media_type must declare charset=utf-8")
    parts = [part.strip() for part in value.split(";")]
    if parts[0].count("/") != 1:
        raise ValueError("media_type must contain a valid type/subtype")
    type_name, subtype_name = parts[0].split("/", 1)
    if not _http_token(type_name) or not _http_token(subtype_name):
        raise ValueError("media_type must contain a valid type/subtype")
    charset_values = [
        part.split("=", 1)[1].strip().strip('"').lower()
        for part in parts[1:]
        if part.lower().startswith("charset=") and "=" in part
    ]
    if charset_values != ["utf-8"]:
        raise ValueError("media_type must declare exactly one charset=utf-8 parameter")
    return value


def _arm(value: Arm | str) -> Arm:
    if type(value) is Arm:
        return value
    if type(value) is not str:
        raise ValueError(f"unknown arm: {value!r}")
    try:
        return Arm(value)
    except ValueError as exc:
        raise ValueError(f"unknown arm: {value!r}") from exc


def _kind(value: ScheduledChatArtifactKind | str) -> ScheduledChatArtifactKind:
    if type(value) is ScheduledChatArtifactKind:
        return value
    if type(value) is not str:
        raise ValueError(f"unknown artifact kind: {value!r}")
    try:
        return ScheduledChatArtifactKind(value)
    except ValueError as exc:
        raise ValueError(f"unknown artifact kind: {value!r}") from exc


def _utf8_bytes(value: str | bytes) -> bytes:
    if type(value) is str:
        try:
            return value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("artifact text must contain only Unicode scalar values") from exc
    if type(value) is bytes:
        try:
            value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("artifact bytes must be valid UTF-8") from exc
        return value
    raise ValueError("artifact payload must be exact str or bytes")


_ArtifactEnvelopeTuple = namedtuple(
    "ScheduledChatArtifactEnvelope",
    (
        "kind",
        "run_id",
        "problem_id",
        "arm",
        "attempt",
        "media_type",
        "byte_length",
        "sha256_hex",
    ),
    module=__name__,
)


class ScheduledChatArtifactEnvelope(_ArtifactEnvelopeTuple):
    """Immutable metadata binding one visible scheduled-chat UTF-8 artifact.

    The digest and byte length are computed over the exact visible UTF-8 bytes. The
    envelope deliberately carries no provider-token count, reasoning-token count, or
    hidden-compute claim.
    """

    __slots__ = ()
    kind: ScheduledChatArtifactKind
    run_id: str
    problem_id: str
    arm: Arm
    attempt: int
    media_type: str
    byte_length: int
    sha256_hex: str

    def __new__(
        cls,
        *,
        kind: ScheduledChatArtifactKind | str,
        run_id: str,
        problem_id: str,
        arm: Arm | str,
        attempt: int,
        media_type: str,
        byte_length: int,
        sha256_hex: str,
    ) -> "ScheduledChatArtifactEnvelope":
        kind = _kind(kind)
        run_id = _token(run_id, "run_id")
        problem_id = _token(problem_id, "problem_id")
        arm = _arm(arm)
        attempt = _attempt(attempt)
        media_type = _media_type(media_type)
        if type(byte_length) is not int or byte_length < 0:
            raise ValueError("byte_length must be a non-negative integer")
        sha256_hex = _token(sha256_hex, "sha256_hex").lower()
        if len(sha256_hex) != 64 or any(ch not in "0123456789abcdef" for ch in sha256_hex):
            raise ValueError("sha256_hex must be exactly 64 lowercase hexadecimal characters")
        return super().__new__(
            cls,
            kind,
            run_id,
            problem_id,
            arm,
            attempt,
            media_type,
            byte_length,
            sha256_hex,
        )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ScheduledChatArtifactEnvelope may not be subclassed")

    @classmethod
    def from_visible_utf8(
        cls,
        payload: str | bytes,
        *,
        kind: ScheduledChatArtifactKind | str,
        run_id: str,
        problem_id: str,
        arm: Arm | str,
        attempt: int,
        media_type: str = "text/plain; charset=utf-8",
    ) -> "ScheduledChatArtifactEnvelope":
        content = _utf8_bytes(payload)
        return cls(
            kind=kind,
            run_id=run_id,
            problem_id=problem_id,
            arm=arm,
            attempt=attempt,
            media_type=media_type,
            byte_length=len(content),
            sha256_hex=sha256(content).hexdigest(),
        )

    @property
    def artifact_id(self) -> str:
        return f"sha256:{self.sha256_hex}"

    def verifies(self, payload: str | bytes) -> bool:
        try:
            content = _utf8_bytes(payload)
        except ValueError:
            return False
        return len(content) == self.byte_length and sha256(content).hexdigest() == self.sha256_hex

    def to_mapping(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "run_id": self.run_id,
            "problem_id": self.problem_id,
            "arm": self.arm.value,
            "attempt": self.attempt,
            "media_type": self.media_type,
            "byte_length": self.byte_length,
            "sha256": self.sha256_hex,
            "artifact_id": self.artifact_id,
            "measurement_basis": "visible_utf8_bytes",
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "ScheduledChatArtifactEnvelope":
        expected = {
            "kind",
            "run_id",
            "problem_id",
            "arm",
            "attempt",
            "media_type",
            "byte_length",
            "sha256",
            "artifact_id",
            "measurement_basis",
        }
        if set(raw) != expected:
            raise ValueError(f"artifact envelope fields must be exactly {sorted(expected)}")
        if raw["measurement_basis"] != "visible_utf8_bytes":
            raise ValueError("measurement_basis must be visible_utf8_bytes")
        envelope = cls(
            kind=raw["kind"],  # type: ignore[arg-type]
            run_id=raw["run_id"],  # type: ignore[arg-type]
            problem_id=raw["problem_id"],  # type: ignore[arg-type]
            arm=raw["arm"],  # type: ignore[arg-type]
            attempt=raw["attempt"],  # type: ignore[arg-type]
            media_type=raw["media_type"],  # type: ignore[arg-type]
            byte_length=raw["byte_length"],  # type: ignore[arg-type]
            sha256_hex=raw["sha256"],  # type: ignore[arg-type]
        )
        if raw["artifact_id"] != envelope.artifact_id:
            raise ValueError("artifact_id does not match sha256")
        return envelope
