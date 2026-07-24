"""Provenance tracking and argument binding.

The :class:`EvidenceStore` is turn-scoped. Every value pulled from email, ICS,
web, files, tool output, memory, or a device is *recorded* with its origin and
data classes and gets an immutable :class:`EvidenceRef`. The central invariant:

    Untrusted content cannot upgrade its origin.

If the same bytes reappear later — through a summary, a trusted-looking tool
echo, a history replay, or a storage round-trip — the store still recognizes
them as the original untrusted evidence. A sensitive tool argument must resolve
to a trusted user literal or a declared evidence reference; a free-form value
whose content matches untrusted evidence is bound to that untrusted origin, and
a free-form sensitive value with no provenance at all fails closed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .types import (
    BoundArg,
    DataClass,
    EvidenceRef,
    Origin,
    stable_hash,
)

_CANARY_RE = re.compile(r"(?i)\b[A-Z0-9_-]*CANARY[A-Z0-9_-]*\b")
_SECRET_RE = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{8,}\b|\b(?:ghp|github_pat|xox[baprs])_[a-z0-9_-]{8,}\b|"
    r"\bAKIA[0-9A-Z]{12,}\b|\bSYNTHETIC[_-]SECRET[A-Z0-9_-]*\b)"
)
_SECRET_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "token",
}
_PII_FIELDS = {"address", "email", "from", "phone", "recipient", "sender", "to"}
_PRIVATE_FIELDS = {"body", "content", "document", "message", "notes", "snippet"}


def content_hash(value: object) -> str:
    """Hash a value's *normalized text* so echoes and summaries collide.

    Whitespace and case are folded because a laundering attempt typically
    reformats the text; the goal is to recognize the same instruction however
    it is re-presented.
    """
    text = value if isinstance(value, str) else stable_hash(value)
    normalized = " ".join(str(text).lower().split())
    return stable_hash(normalized)


def classify_value(
    value: object,
    field_path: str = "",
    origin: Origin | None = None,
) -> frozenset[DataClass]:
    """Classify synthetic secrets and common connector fields deterministically."""
    classes: set[DataClass] = set()
    text = value if isinstance(value, str) else ""
    leaf = field_path.rsplit(".", 1)[-1].replace("[]", "").lower()
    if text and _CANARY_RE.search(text):
        classes.add(DataClass.CANARY)
    if (text and _SECRET_RE.search(text)) or leaf in _SECRET_FIELDS:
        classes.add(DataClass.SECRET)
    if leaf in _PII_FIELDS and origin is not None:
        classes.add(DataClass.PII)
    if leaf in _PRIVATE_FIELDS and origin in {
        Origin.EMAIL,
        Origin.FILE,
        Origin.MEMORY,
        Origin.CLIPBOARD,
        Origin.SCREENSHOT,
    }:
        classes.add(DataClass.PRIVATE_DOC)
    if origin is Origin.CLIPBOARD:
        classes.add(DataClass.CLIPBOARD)
    if origin is Origin.SCREENSHOT:
        classes.add(DataClass.PRIVATE_DOC)
    if not classes:
        classes.add(DataClass.PUBLIC)
    return frozenset(classes)


@dataclass
class EvidenceStore:
    """Per-turn provenance ledger. Never shared across turns."""

    _by_id: dict[str, EvidenceRef] = field(default_factory=dict)
    _by_content: dict[str, EvidenceRef] = field(default_factory=dict)
    _normalized_by_id: dict[str, str] = field(default_factory=dict)

    def record(
        self,
        origin: Origin,
        value: object,
        data_classes: frozenset[DataClass] | set[DataClass] | None = None,
        preview: str = "",
        *,
        source: str = "",
        field_path: str = "",
        lineage: tuple[str, ...] = (),
        ttl_s: float = 900.0,
    ) -> EvidenceRef:
        """Record a value's provenance and return an immutable reference.

        Content-addressed: recording the same bytes twice returns the *existing*
        reference, and if the bytes were already seen from a less-trusted origin
        that untrusted origin wins — so re-recording untrusted text as ``USER``
        can never launder it into a trusted literal.
        """
        ch = content_hash(value)
        classes = frozenset(data_classes or ()) | classify_value(value, field_path, origin)
        existing = self._by_content.get(ch)
        if existing is not None:
            # Keep the *less-trusted* origin; merge data classes (never drop a
            # sensitive label). Trust only ever moves downward, so re-recording
            # the same bytes from a more-trusted origin can never launder them.
            keep_origin = origin if _less_trusted(origin, existing.origin) else existing.origin
            merged = EvidenceRef(
                ref_id=existing.ref_id,
                origin=keep_origin,
                content_hash=ch,
                data_classes=existing.data_classes | classes,
                preview=existing.preview or preview,
                source=existing.source or source,
                field_path=existing.field_path or field_path,
                lineage=tuple(dict.fromkeys((*existing.lineage, *lineage))),
                expires_at=max(existing.expires_at, time.time() + ttl_s if ttl_s else 0.0),
            )
            self._by_id[merged.ref_id] = merged
            self._by_content[ch] = merged
            self._normalized_by_id[merged.ref_id] = _normalized_text(value)
            return merged
        ref = EvidenceRef(
            ref_id=f"ev_{stable_hash([origin, source, field_path, ch])[:16]}",
            origin=origin,
            content_hash=ch,
            data_classes=classes,
            preview=preview[:200],
            source=source,
            field_path=field_path,
            lineage=lineage,
            expires_at=time.time() + ttl_s if ttl_s else 0.0,
        )
        self._by_id[ref.ref_id] = ref
        self._by_content[ch] = ref
        self._normalized_by_id[ref.ref_id] = _normalized_text(value)
        return ref

    def record_fields(
        self,
        origin: Origin,
        value: object,
        data_classes: frozenset[DataClass] | set[DataClass] | None = None,
        *,
        source: str = "",
        field_path: str = "",
        lineage: tuple[str, ...] = (),
        ttl_s: float = 900.0,
    ) -> tuple[EvidenceRef, ...]:
        """Record each scalar leaf with a typed field path.

        Containers are traversed rather than flattened into one JSON blob, so
        a later argument can safely resolve to a single message body, recipient,
        attachment field, array item, or synthetic secret substring.
        """
        refs: list[EvidenceRef] = []

        def walk(item: object, path: str) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    child_path = f"{path}.{key}" if path else str(key)
                    walk(child, child_path)
                return
            if isinstance(item, (list, tuple)):
                for index, child in enumerate(item):
                    child_path = f"{path}.{index}" if path else str(index)
                    walk(child, child_path)
                return
            refs.append(
                self.record(
                    origin,
                    item,
                    data_classes,
                    preview=f"{source}:{path}" if source or path else "",
                    source=source,
                    field_path=path,
                    lineage=lineage,
                    ttl_s=ttl_s,
                )
            )

        walk(value, field_path)
        return tuple(refs)

    def get(self, ref_id: str) -> EvidenceRef | None:
        return self._by_id.get(ref_id)

    def match(self, value: object) -> EvidenceRef | None:
        """Find exact or containment-safe evidence for ``value``.

        Exact field matches win. For strings, containment also recognizes a
        secret/token or sentence copied out of a larger connector field.
        """
        exact = self._by_content.get(content_hash(value))
        if exact is not None and not exact.expired():
            return exact
        needle = _normalized_text(value)
        if not needle:
            return None
        candidates: list[tuple[int, int, EvidenceRef]] = []
        for ref_id, haystack in self._normalized_by_id.items():
            ref = self._by_id[ref_id]
            if ref.expired() or not haystack:
                continue
            shorter = min(len(needle), len(haystack))
            sensitive = bool(ref.data_classes - {DataClass.PUBLIC})
            if shorter < 4 and not sensitive:
                continue
            if needle in haystack or haystack in needle:
                trust_rank = _TRUST_RANK.get(ref.origin, 0)
                candidates.append((0 if sensitive else 1, trust_rank, ref))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    # -- argument binding ---------------------------------------------------- #
    def bind_arg(
        self,
        name: str,
        value: object,
        *,
        literal: bool = False,
        declared_ref: str = "",
        sensitive: bool = False,
    ) -> BoundArg:
        """Bind a tool argument to its true provenance.

        ``literal=True`` claims the authenticated user typed the value.
        ``declared_ref`` names an :class:`EvidenceRef` the value was taken from.
        Either claim is *verified* against recorded content: if the value's
        bytes match untrusted evidence, the binding is forced to that untrusted
        origin regardless of the claim — this is the anti-laundering step.
        """
        matched = self.match(value)
        if matched is not None and not matched.trusted:
            # The value is (also) known untrusted content: no upgrade allowed.
            return BoundArg(
                name=name,
                value=value,
                source_ref=matched.ref_id,
                data_classes=matched.data_classes,
            )
        if declared_ref:
            ref = self.get(declared_ref)
            if ref is not None and not ref.expired():
                return BoundArg(
                    name=name,
                    value=value,
                    source_ref=ref.ref_id,
                    data_classes=ref.data_classes,
                )
        detected = classify_value(value, name)
        sensitive_classes = detected - {DataClass.PUBLIC}
        if matched is not None and matched.trusted and literal:
            return BoundArg(
                name=name,
                value=value,
                source_ref=BoundArg.LITERAL,
                data_classes=matched.data_classes,
            )
        if literal and not sensitive_classes and not sensitive:
            return BoundArg(
                name=name,
                value=value,
                source_ref=BoundArg.LITERAL,
                data_classes=frozenset({DataClass.PUBLIC}),
            )
        # No literal claim, no declared/known provenance: unaudited free-form.
        classes = (
            frozenset({DataClass.SECRET})
            if sensitive
            else (sensitive_classes or frozenset())
        )
        return BoundArg(name=name, value=value, source_ref="", data_classes=classes)

    def data_classes_reaching(self, bound_args: tuple[BoundArg, ...]) -> frozenset[DataClass]:
        """Union of data classes carried by a set of bound arguments."""
        classes: set[DataClass] = set()
        for arg in bound_args:
            classes |= set(arg.data_classes)
        return frozenset(classes)


_TRUST_RANK = {
    Origin.USER: 3,
    Origin.SYSTEM: 3,
    Origin.MEMORY: 1,
    Origin.HISTORY: 1,
    Origin.TOOL_OUTPUT: 1,
    Origin.EMAIL: 0,
    Origin.ICS: 0,
    Origin.WEB: 0,
    Origin.FILE: 1,
    Origin.MCP: 0,
    Origin.DEVICE: 0,
    Origin.CLIPBOARD: 1,
    Origin.SCREENSHOT: 1,
}


def _less_trusted(a: Origin, b: Origin) -> bool:
    return _TRUST_RANK.get(a, 0) < _TRUST_RANK.get(b, 0)


def _normalized_text(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.lower().split())
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value).lower()
    return ""
