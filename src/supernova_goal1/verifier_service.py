"""Locator-only client for the separately administered Goal-1 verifier.

The confirmatory runner is deliberately not a verifier host.  It does not load a
verifier private key, open the verifier evidence database, construct a
``VerifierSupervisor``, or pass candidate/source bytes to this service.  Its only
request is the durable locator ``(run_id, actual_dispatch_id)``.  A separately
provisioned authority resolves that locator, performs verification, persists the
evidence, and returns a signed readback.

This module authenticates the returned evidence cryptographically.  It cannot
portably prove Windows/Unix process ownership or ACLs.  Consequently the client
configuration must carry an external OS-identity qualification record, and a
real launch remains blocked until that record was produced by checking the
deployed service account and endpoint permissions outside this process.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .execution.baselines import BaselineDispatch
from .production_verifier import (
    ProductionVerification,
    ProductionVerifierPort,
    verifier_result_from_evidence,
)
from .verifier import VerifierResult
from .verifier_evidence import (
    VerifierBinding,
    VerifierEvidenceBlobs,
    VerifierEvidenceRecord,
    VerifierIdentity,
    canonical_bytes,
)

REQUEST_SCHEMA = "supernova.goal1.verifier-service-locator.v1"
RESPONSE_SCHEMA = "supernova.goal1.verifier-service-readback.v1"
CONFIG_SCHEMA = "supernova.goal1.verifier-service-client.v1"
OS_QUALIFICATION_MODE = "EXTERNAL_OS_SECURITY_DESCRIPTOR"
MAX_REQUEST_BYTES = 512
_HEX = frozenset("0123456789abcdef")
_BLOB_FIELDS = (
    "source",
    "candidate",
    "exported_artifact",
    "checker_output",
    "stdout",
    "stderr",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _token(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be one exact non-empty trimmed string")
    value.encode("utf-8")
    return value


def _sha256(value: object, field: str) -> str:
    value = _token(value, field)
    if len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field} must be one lowercase sha256")
    return value


def _strict_object(raw: bytes, field: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"{field} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value {item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field} is not canonical UTF-8 JSON") from exc
    if type(value) is not dict or canonical_bytes(value) != raw:
        raise ValueError(f"{field} is not one canonical JSON object")
    return value


def _b64(value: object, field: str) -> bytes:
    if type(value) is not str:
        raise ValueError(f"{field} must be canonical base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be canonical base64") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{field} must be canonical base64")
    return decoded


@dataclass(frozen=True)
class VerifierServiceLocator:
    run_id: str
    actual_dispatch_id: str

    def __post_init__(self) -> None:
        _token(self.run_id, "run_id")
        _sha256(self.actual_dispatch_id, "actual_dispatch_id")

    def body(self) -> dict[str, object]:
        # These are intentionally the only caller-controlled service inputs.
        return {
            "actual_dispatch_id": self.actual_dispatch_id,
            "run_id": self.run_id,
            "schema": REQUEST_SCHEMA,
        }

    def request_body(self) -> dict[str, object]:
        """Compatibility spelling for the canonical locator body."""

        return self.body()

    @property
    def request_bytes(self) -> bytes:
        return canonical_bytes(self.body())

    @classmethod
    def from_request(cls, raw: bytes) -> VerifierServiceLocator:
        if type(raw) is not bytes:
            raise TypeError("verifier service request must be exact bytes")
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("verifier service request exceeds the fixed byte limit")
        value = _strict_object(raw, "verifier service request")
        if set(value) != {"actual_dispatch_id", "run_id", "schema"}:
            raise ValueError("verifier service accepts only an attempt locator")
        if value["schema"] != REQUEST_SCHEMA:
            raise ValueError("verifier service request schema changed")
        return cls(
            run_id=value["run_id"],
            actual_dispatch_id=value["actual_dispatch_id"],
        )


# Both sides deliberately share one locator type and one canonical encoding.
VerifierAttemptLocator = VerifierServiceLocator


@dataclass(frozen=True)
class AuthoritativeVerifierAttempt:
    """Service-owned resolution of a locator to observed candidate bytes."""

    dispatch: BaselineDispatch
    candidate: bytes

    def __post_init__(self) -> None:
        if type(self.dispatch) is not BaselineDispatch:
            raise TypeError("dispatch must be an exact BaselineDispatch")
        if type(self.candidate) is not bytes or not self.candidate:
            raise TypeError("candidate must be non-empty exact bytes")
        try:
            self.candidate.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("candidate must be UTF-8") from exc

    @property
    def locator(self) -> VerifierServiceLocator:
        return VerifierServiceLocator(
            run_id=self.dispatch.request.run_id,
            actual_dispatch_id=self.dispatch.entry.dispatch_id,
        )


class ProductionVerifierService:
    """Trusted server: resolve locators, verify, persist, and return readback."""

    def __init__(
        self,
        port: ProductionVerifierPort,
        attempts: Mapping[VerifierServiceLocator, AuthoritativeVerifierAttempt],
    ) -> None:
        if type(port) is not ProductionVerifierPort:
            raise TypeError("port must be an exact ProductionVerifierPort")
        if port.subject_builder is not None:
            raise ValueError(
                "locator-only service requires authority-derived verification subjects"
            )
        if type(attempts) is not dict:
            raise TypeError("attempts must be one exact authoritative dict")
        copied: dict[VerifierServiceLocator, AuthoritativeVerifierAttempt] = {}
        for locator, attempt in attempts.items():
            if type(locator) is not VerifierServiceLocator:
                raise TypeError("attempt locator must be exact")
            if type(attempt) is not AuthoritativeVerifierAttempt:
                raise TypeError("authoritative attempt must be exact")
            if attempt.locator != locator:
                raise ValueError("authoritative attempt differs from its locator")
            if locator in copied:
                raise ValueError("duplicate authoritative attempt locator")
            copied[locator] = attempt
        if not copied:
            raise ValueError("authoritative attempt catalog must not be empty")
        self.__port = port
        self.__attempts = copied

    def verify(self, locator: VerifierServiceLocator) -> ProductionVerification:
        if type(locator) is not VerifierServiceLocator:
            raise TypeError("locator must be exact")
        try:
            attempt = self.__attempts[locator]
        except KeyError as exc:
            raise KeyError("unknown authoritative attempt locator") from exc
        return self.__port.verify(attempt.dispatch, attempt.candidate)

    def handle_request(self, raw: bytes) -> bytes:
        locator = VerifierServiceLocator.from_request(raw)
        verification = self.verify(locator)
        record = verification.record
        blobs = verification.blobs
        return canonical_bytes(
            {
                "authority_id": record.body["issuer_id"],
                "blobs": {
                    field: base64.b64encode(getattr(blobs, field)).decode("ascii")
                    for field in _BLOB_FIELDS
                },
                "record": {
                    "body_json_b64": base64.b64encode(record.body_json).decode(
                        "ascii"
                    ),
                    "signature_b64": record.signature_b64,
                },
                "request_sha256": _sha(locator.request_bytes),
                "schema": RESPONSE_SCHEMA,
            }
        )


def serve_one(service: ProductionVerifierService, stdin: Any, stdout: Any) -> None:
    """Serve one bounded canonical request; persist evidence before replying."""

    if type(service) is not ProductionVerifierService:
        raise TypeError("service must be exact")
    request = stdin.read(MAX_REQUEST_BYTES + 1)
    response = service.handle_request(request)
    stdout.write(response)
    stdout.flush()


@dataclass(frozen=True)
class VerifierServiceEndpoint:
    transport: str
    address: str
    port: int | None
    timeout_seconds: int
    max_response_bytes: int

    def __post_init__(self) -> None:
        if self.transport not in {"unix", "tcp-loopback"}:
            raise ValueError("verifier service transport must be unix or tcp-loopback")
        _token(self.address, "verifier service address")
        if self.transport == "unix":
            if self.port is not None or not Path(self.address).is_absolute():
                raise ValueError("unix verifier endpoint must be one absolute path")
        else:
            if self.address not in {"127.0.0.1", "::1"}:
                raise ValueError("tcp verifier endpoint must be loopback-only")
            if type(self.port) is not int or not 1 <= self.port <= 65535:
                raise ValueError("tcp verifier endpoint port is invalid")
        if type(self.timeout_seconds) is not int or self.timeout_seconds < 1:
            raise ValueError("verifier service timeout must be a positive integer")
        if (
            type(self.max_response_bytes) is not int
            or not 1024 <= self.max_response_bytes <= 64 * 1024 * 1024
        ):
            raise ValueError("verifier service response bound is invalid")


@dataclass(frozen=True)
class VerifierServiceConfig:
    endpoint: VerifierServiceEndpoint
    authority_id: str
    signing_key_id: str
    verification_key: bytes
    expected_identity: VerifierIdentity
    client_principal: str
    service_principal: str
    os_identity_evidence_sha256: str

    def __post_init__(self) -> None:
        if type(self.endpoint) is not VerifierServiceEndpoint:
            raise TypeError("endpoint must be an exact VerifierServiceEndpoint")
        _token(self.authority_id, "verifier authority_id")
        _token(self.signing_key_id, "verifier signing_key_id")
        if type(self.verification_key) is not bytes or len(self.verification_key) != 32:
            raise ValueError("verification_key must be one raw Ed25519 public key")
        if type(self.expected_identity) is not VerifierIdentity:
            raise TypeError("expected_identity must be an exact VerifierIdentity")
        _token(self.client_principal, "client_principal")
        _token(self.service_principal, "service_principal")
        if self.client_principal == self.service_principal:
            raise ValueError("verifier service must use a distinct OS principal")
        _sha256(
            self.os_identity_evidence_sha256,
            "os_identity_evidence_sha256",
        )

    @classmethod
    def from_mapping(
        cls,
        raw: object,
        *,
        execution_authority_id: str,
    ) -> VerifierServiceConfig:
        expected = {
            "authority_id",
            "endpoint",
            "expected_identity",
            "os_identity_qualification",
            "schema",
            "signing_key_id",
            "verification_key_b64",
        }
        if type(raw) is not dict or set(raw) != expected:
            raise ValueError("verifier service configuration fields changed")
        if raw["schema"] != CONFIG_SCHEMA:
            raise ValueError("verifier service configuration schema changed")
        authority_id = _token(raw["authority_id"], "verifier authority_id")
        if authority_id == _token(
            execution_authority_id, "execution_authority_id"
        ):
            raise ValueError("verifier and execution authorities must be distinct")

        endpoint = raw["endpoint"]
        endpoint_fields = {
            "address",
            "max_response_bytes",
            "port",
            "timeout_seconds",
            "transport",
        }
        if type(endpoint) is not dict or set(endpoint) != endpoint_fields:
            raise ValueError("verifier service endpoint fields changed")

        qualification = raw["os_identity_qualification"]
        qualification_fields = {
            "client_principal",
            "evidence_sha256",
            "mode",
            "service_principal",
        }
        if (
            type(qualification) is not dict
            or set(qualification) != qualification_fields
            or qualification.get("mode") != OS_QUALIFICATION_MODE
        ):
            raise PermissionError(
                "verifier service OS-identity qualification is absent"
            )

        return cls(
            endpoint=VerifierServiceEndpoint(
                transport=endpoint["transport"],
                address=endpoint["address"],
                port=endpoint["port"],
                timeout_seconds=endpoint["timeout_seconds"],
                max_response_bytes=endpoint["max_response_bytes"],
            ),
            authority_id=authority_id,
            signing_key_id=raw["signing_key_id"],
            verification_key=_b64(
                raw["verification_key_b64"], "verification_key_b64"
            ),
            expected_identity=VerifierIdentity.from_body(raw["expected_identity"]),
            client_principal=qualification["client_principal"],
            service_principal=qualification["service_principal"],
            os_identity_evidence_sha256=qualification["evidence_sha256"],
        )


class VerifierServiceTransport(Protocol):
    def exchange(self, request: bytes) -> bytes:
        """Exchange one canonical locator for one canonical readback."""


class SocketVerifierServiceTransport:
    """Length-framed connection to an already-running local verifier service."""

    def __init__(self, endpoint: VerifierServiceEndpoint) -> None:
        if type(endpoint) is not VerifierServiceEndpoint:
            raise TypeError("endpoint must be an exact VerifierServiceEndpoint")
        self.endpoint = endpoint

    @staticmethod
    def _recv_exact(connection: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = connection.recv(remaining)
            if not chunk:
                raise ConnectionError("verifier service closed an incomplete response")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def exchange(self, request: bytes) -> bytes:
        if type(request) is not bytes or not request:
            raise TypeError("verifier service request must be non-empty exact bytes")
        endpoint = self.endpoint
        family = socket.AF_UNIX if endpoint.transport == "unix" else socket.AF_INET6 if endpoint.address == "::1" else socket.AF_INET
        address: object = (
            endpoint.address
            if endpoint.transport == "unix"
            else (endpoint.address, endpoint.port)
        )
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(endpoint.timeout_seconds)
            connection.connect(address)  # type: ignore[arg-type]
            connection.sendall(struct.pack("!I", len(request)) + request)
            size = struct.unpack("!I", self._recv_exact(connection, 4))[0]
            if size < 2 or size > endpoint.max_response_bytes:
                raise ValueError("verifier service response exceeds its frozen bound")
            return self._recv_exact(connection, size)


@dataclass(frozen=True)
class VerifierServiceReadback:
    locator: VerifierServiceLocator
    verification: ProductionVerification

    @property
    def record_sha256(self) -> str:
        return self.verification.record.record_sha256


class VerifierServiceClient:
    """Authenticate locator-only readbacks from a distinct verifier authority."""

    def __init__(
        self,
        config: VerifierServiceConfig,
        *,
        transport: VerifierServiceTransport | None = None,
    ) -> None:
        if type(config) is not VerifierServiceConfig:
            raise TypeError("config must be an exact VerifierServiceConfig")
        self.config = config
        self.transport = (
            SocketVerifierServiceTransport(config.endpoint)
            if transport is None
            else transport
        )
        if not callable(getattr(self.transport, "exchange", None)):
            raise TypeError("transport must provide exchange(request)")
        self._readbacks: dict[str, VerifierServiceReadback] = {}

    def _decode(
        self,
        raw: bytes,
        *,
        locator: VerifierServiceLocator,
        expected_candidate: bytes,
    ) -> VerifierServiceReadback:
        response = _strict_object(raw, "verifier service response")
        expected = {
            "authority_id",
            "blobs",
            "record",
            "request_sha256",
            "schema",
        }
        if type(response) is not dict or set(response) != expected:
            raise ValueError("verifier service response fields changed")
        if response["schema"] != RESPONSE_SCHEMA:
            raise ValueError("verifier service response schema changed")
        if response["authority_id"] != self.config.authority_id:
            raise ValueError("verifier service authority changed")
        if response["request_sha256"] != _sha(locator.request_bytes):
            raise ValueError("verifier service response is for another locator")

        record_raw = response["record"]
        if type(record_raw) is not dict or set(record_raw) != {
            "body_json_b64",
            "signature_b64",
        }:
            raise ValueError("verifier service record envelope changed")
        record = VerifierEvidenceRecord(
            body_json=_b64(record_raw["body_json_b64"], "record body_json_b64"),
            signature_b64=record_raw["signature_b64"],
        )
        record.verify(
            self.config.verification_key,
            expected_signing_key_id=self.config.signing_key_id,
            expected_identity=self.config.expected_identity,
        )
        if record.body["issuer_id"] != self.config.authority_id:
            raise ValueError("signed verifier issuer differs from service authority")
        binding = VerifierBinding.from_body(record.body["binding"])
        if (
            binding.run_id != locator.run_id
            or binding.actual_dispatch_id != locator.actual_dispatch_id
        ):
            raise ValueError("signed verifier evidence is for another locator")
        if _sha(expected_candidate) != binding.candidate_source_sha256:
            raise ValueError("signed verifier evidence is for another candidate")

        blobs_raw = response["blobs"]
        if type(blobs_raw) is not dict or set(blobs_raw) != set(_BLOB_FIELDS):
            raise ValueError("verifier service blob envelope changed")
        blobs = VerifierEvidenceBlobs(
            **{
                field: _b64(blobs_raw[field], f"verifier blob {field}")
                for field in _BLOB_FIELDS
            }
        )
        result = verifier_result_from_evidence(
            record,
            blobs,
            command=("verifier-service", self.config.endpoint.transport),
        )
        verification = ProductionVerification(binding, record, blobs, result)
        if verification.blobs.candidate != expected_candidate:
            raise ValueError("verifier service candidate readback changed")
        return VerifierServiceReadback(locator, verification)

    def verify(
        self,
        dispatch: BaselineDispatch,
        candidate: bytes,
    ) -> VerifierServiceReadback:
        if type(dispatch) is not BaselineDispatch:
            raise TypeError("dispatch must be an exact BaselineDispatch")
        if type(candidate) is not bytes:
            raise TypeError("candidate must be exact bytes")
        locator = VerifierServiceLocator(
            run_id=dispatch.request.run_id,
            actual_dispatch_id=dispatch.entry.dispatch_id,
        )
        if locator.actual_dispatch_id in self._readbacks:
            raise ValueError("verifier service dispatch replay rejected")
        raw = self.transport.exchange(locator.request_bytes)
        readback = self._decode(
            raw,
            locator=locator,
            expected_candidate=candidate,
        )
        self._readbacks[locator.actual_dispatch_id] = readback
        return readback

    def readback_for_dispatch(self, actual_dispatch_id: str) -> VerifierServiceReadback:
        actual_dispatch_id = _sha256(actual_dispatch_id, "actual_dispatch_id")
        try:
            return self._readbacks[actual_dispatch_id]
        except KeyError as exc:
            raise LookupError("verifier service has no authenticated readback") from exc

    def __call__(self, dispatch: BaselineDispatch, candidate: bytes) -> VerifierResult:
        return self.verify(dispatch, candidate).verification.result


def load_verifier_service_client(
    path: Path,
    *,
    execution_authority_id: str,
) -> VerifierServiceClient:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("verifier service configuration path must be absolute")
    raw = path.resolve(strict=True).read_bytes()
    mapping = _strict_object(raw, "verifier service configuration")
    config = VerifierServiceConfig.from_mapping(
        mapping,
        execution_authority_id=execution_authority_id,
    )
    return VerifierServiceClient(config)


__all__ = [
    "CONFIG_SCHEMA",
    "MAX_REQUEST_BYTES",
    "OS_QUALIFICATION_MODE",
    "REQUEST_SCHEMA",
    "RESPONSE_SCHEMA",
    "AuthoritativeVerifierAttempt",
    "ProductionVerifierService",
    "SocketVerifierServiceTransport",
    "VerifierAttemptLocator",
    "VerifierServiceClient",
    "VerifierServiceConfig",
    "VerifierServiceEndpoint",
    "VerifierServiceLocator",
    "VerifierServiceReadback",
    "load_verifier_service_client",
    "serve_one",
]
