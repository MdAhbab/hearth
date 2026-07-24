"""Future local-network / IoT effect-adapter interfaces — defined but DISABLED.

Hearth deliberately ships no raw TCP, WebSocket, MQTT, or device access in
production. The benchmark exercises those capabilities only inside its inert
emulators (``benchmarks/intentseal/emulators.py``) behind a network kill
switch. This module declares the *shape* a future production adapter would take
so IntentSeal's policy already knows how to reason about such effects — but
every adapter here is inert and raises if anyone tries to use it, and the
feature flag stays off.

To enable any of these in production would require: a real network-egress proxy
with a destination allowlist, per-device capability scoping, rate/time bounds,
digital-twin previews, idempotency keys, and explicit confirmation for
safety-relevant or group actuation (S24, S38-S42). None of that exists yet, by
design.
"""

from __future__ import annotations

from typing import Any, Protocol

from .types import PredictedEffect

# Master switch. Local-network / IoT effects are OFF in production. The
# benchmark never reads this flag — it uses emulators directly.
LOCAL_NETWORK_ENABLED = False
IOT_ENABLED = False


class DisabledAdapterError(RuntimeError):
    """Raised if disabled future adapters are invoked in production."""


class FutureEffectAdapter(Protocol):
    """Predicts a :class:`PredictedEffect` for a future capability's tool."""

    def predict(self, args: dict[str, Any]) -> PredictedEffect: ...


class LocalNetworkAdapter:
    """Interface for future loopback/LAN/TCP/WebSocket effects. Inert."""

    def predict(self, args: dict[str, Any]) -> PredictedEffect:  # noqa: ARG002
        raise DisabledAdapterError(
            "local-network adapters are disabled in production Hearth; "
            "TCP/WebSocket effects exist only under the benchmark emulators"
        )


class IoTDeviceAdapter:
    """Interface for future physical-device (lock, thermostat, light, …). Inert."""

    def predict(self, args: dict[str, Any]) -> PredictedEffect:  # noqa: ARG002
        raise DisabledAdapterError(
            "IoT device adapters are disabled in production Hearth; "
            "physical effects exist only under the benchmark emulators"
        )


def register_future_adapters(_registry) -> None:
    """No-op in production: future adapters stay unregistered while disabled.

    Kept as the single, explicit place a future maintainer would wire real,
    fully-mediated local-network/IoT adapters — never by adding a second
    execution path around the ActionGate.
    """
    if LOCAL_NETWORK_ENABLED or IOT_ENABLED:  # pragma: no cover - disabled by design
        raise DisabledAdapterError(
            "enabling local-network/IoT requires a mediated egress proxy and "
            "device capability broker that are not part of this project"
        )
