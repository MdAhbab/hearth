"""Canonicalizers: turn a human/model-supplied reference into a stable,
comparable identity that a policy can bind to and an executor can re-verify.

Every function is pure and side-effect free except :func:`canonical_file`,
which reads file bytes to compute a content hash (it never writes). The point
of canonicalization is complete mediation: a decision made about
``canonical_id`` stays valid only if the same ``canonical_id`` resolves at
execution time, which defeats path-alias, redirect, DNS-rebind, and
display-name spoofing.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .types import CanonicalTarget, ToolIdentity, stable_hash

# --------------------------------------------------------------------------- #
# Recipients / accounts
# --------------------------------------------------------------------------- #
_ANGLE_ADDR = re.compile(r"<([^>]+)>")
# Characters that look alike across scripts are not folded here; instead the
# raw normalized address is the identity, so any mismatch is visible.


def canonical_recipient(raw: str) -> CanonicalTarget:
    """Normalize an email recipient to ``localpart@domain`` (lowercased).

    A display name is stripped — ``"Alice <a@x.test>"`` and ``a@X.test`` share
    one identity, so display-name impersonation cannot smuggle in a different
    address. The domain is recorded separately so audience/domain look-alike
    checks can compare it.
    """
    text = (raw or "").strip()
    match = _ANGLE_ADDR.search(text)
    if match:
        text = match.group(1).strip()
    text = text.strip().strip(",;")
    addr = text.lower()
    domain = addr.split("@", 1)[1] if "@" in addr else ""
    return CanonicalTarget(
        kind="recipient",
        canonical_id=addr,
        attributes={"domain": domain, "raw": raw},
    )


def canonical_account(raw: str) -> str:
    """Stable identity for an account principal (``provider:address``)."""
    return (raw or "").strip().lower()


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
def _content_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_file(raw_path: str) -> CanonicalTarget:
    """Realpath + content hash for a file target.

    ``realpath`` resolves symlinks and ``..`` at *this* moment, so an alias
    that later points elsewhere no longer matches the sealed identity. The
    content hash pins the exact bytes an approval was shown for; if the file
    changes between preview and execution the hash differs (TOCTOU defense).
    A missing file hashes to the empty sentinel but still canonicalizes its
    path, so a create/overwrite target is comparable.
    """
    p = Path(raw_path).expanduser()
    try:
        real = os.path.realpath(str(p))
    except OSError:
        real = str(p)
    exists = os.path.isfile(real)
    content = _content_hash(Path(real)) if exists else ""
    return CanonicalTarget(
        kind="file",
        canonical_id=real,
        attributes={"content_hash": content, "exists": exists, "raw": raw_path},
    )


# --------------------------------------------------------------------------- #
# URLs and network peers
# --------------------------------------------------------------------------- #
# Reserved documentation / test names that must never resolve to real hosts.
_INERT_SUFFIXES = (".test", ".example", ".invalid", ".localhost")
_INERT_TLDS = ("example.com", "example.net", "example.org")


def classify_host(host: str) -> str:
    """Classify a hostname/IP into a network trust zone.

    Returns one of: ``loopback``, ``private``, ``link_local``, ``mdns_local``,
    ``inert``, ``public``. Anything not clearly public and not clearly inert is
    treated conservatively. Used by the SSRF/local-network policy checks.
    """
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return "public"
    if h == "localhost" or h.endswith(".localhost"):
        return "loopback"
    if h.endswith(".local"):
        return "mdns_local"
    if h.endswith(_INERT_SUFFIXES) or h in _INERT_TLDS or h.endswith(_INERT_TLDS):
        return "inert"
    # Bracketed IPv6 or bare IP? Strip an IPv6 scope-id ("fe80::1%eth0") first —
    # ip_address() rejects it, which would otherwise misclassify a link-local
    # address as public and let it slip past the local-zone egress block.
    candidate = h[1:-1] if h.startswith("[") and h.endswith("]") else h
    candidate = candidate.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        return "public"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    # RFC 5737 TEST-NET documentation blocks are inert. Checked before the
    # private test because modern ``ipaddress`` marks these ranges is_private.
    for block in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"):
        if ip in ipaddress.ip_network(block):
            return "inert"
    if ip.is_private:
        return "private"
    return "public"


# Network zones a public-web action must never silently reach.
LOCAL_ZONES = frozenset({"loopback", "private", "link_local", "mdns_local"})


def canonical_url(raw: str) -> CanonicalTarget:
    """Normalize a URL to ``scheme://host:port/path`` and classify its host.

    Default ports are filled in so ``http://h`` and ``http://h:80`` match. The
    host zone classification is attached so the policy can stop a public-web
    task before it dereferences a loopback/private/link-local destination.
    """
    parsed = urlparse((raw or "").strip())
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is None:
        port = {"http": 80, "https": 443, "ws": 80, "wss": 443}.get(scheme, 0)
    path = parsed.path or "/"
    # Credentials embedded in the URL (user:pass@host) are a smell — they can
    # smuggle a secret to a sink or point at an unexpected principal. Flag them
    # rather than silently dropping them from the canonical identity.
    has_credentials = bool(parsed.username or parsed.password)
    canonical = f"{scheme}://{host}:{port}{path}"
    return CanonicalTarget(
        kind="url",
        canonical_id=canonical,
        attributes={
            "scheme": scheme,
            "host": host,
            "port": port,
            "path": path,
            "zone": classify_host(host),
            "raw": raw,
            "custom_scheme": scheme not in {"http", "https"},
            "has_credentials": has_credentials,
        },
    )


# --------------------------------------------------------------------------- #
# Apps, shortcuts, MCP, devices
# --------------------------------------------------------------------------- #
def canonical_app(raw: str) -> CanonicalTarget:
    """Bundle-id identity for a desktop app; a bare name stays a name."""
    text = (raw or "").strip()
    is_bundle = "." in text and " " not in text
    return CanonicalTarget(
        kind="app",
        canonical_id=text.lower(),
        attributes={"bundle_id": text if is_bundle else "", "name": text},
    )


def canonical_shortcut(name: str, changes_data: bool = True) -> CanonicalTarget:
    return CanonicalTarget(
        kind="shortcut",
        canonical_id=(name or "").strip(),
        attributes={"changes_data": changes_data},
    )


def canonical_mcp_tool(server: str, tool: str, manifest: dict | None) -> ToolIdentity:
    """Pin an MCP tool to its server plus a hash of its full manifest.

    If the manifest (schema/description/annotations) changes after approval the
    hash changes, which the seal verifier treats as tool-identity drift.
    """
    manifest_hash = stable_hash(manifest or {})
    return ToolIdentity(name=f"mcp:{server}:{tool}", manifest_hash=manifest_hash)


def canonical_device(service: str, host: str, port: int, protocol: str) -> CanonicalTarget:
    """Bind a device/service to host+port+protocol+service, never hostname alone.

    Used by the inert TCP/IoT emulators so a ``.local`` alias or a banner that
    claims a different identity cannot be treated as the approved device.
    """
    canonical = f"{protocol.lower()}://{host.lower()}:{port}/{service.lower()}"
    return CanonicalTarget(
        kind="device",
        canonical_id=canonical,
        attributes={
            "service": service,
            "host": host.lower(),
            "port": port,
            "protocol": protocol.lower(),
            "zone": classify_host(host),
        },
    )
