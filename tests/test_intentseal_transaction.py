"""Transaction staging/rollback, postconditions, and tamper-evident audit."""

from __future__ import annotations

from hearth.assurance import (
    HashChainAudit,
    StagedFileWrite,
    Transaction,
    check_postcondition,
    redact_text,
)


def test_staged_write_discard_touches_nothing(tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text("original")
    op = StagedFileWrite(target, "REPLACED", tmp_path / ".staging").stage()
    diff = op.diff()
    assert "original" in diff.before and "REPLACED" in diff.after
    op.discard()
    assert target.read_text() == "original"  # never touched


def test_staged_write_commit_and_undo_restores(tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text("v1")
    op = StagedFileWrite(target, "v2", tmp_path / ".staging")
    record = op.stage().commit()
    assert target.read_text() == "v2"
    assert record.kind == "restore_file"
    op.undo(record)
    assert target.read_text() == "v1"


def test_commit_new_file_undo_deletes(tmp_path):
    target = tmp_path / "new.txt"
    op = StagedFileWrite(target, "hello", tmp_path / ".staging")
    record = op.stage().commit()
    assert target.exists() and record.kind == "delete_created"
    op.undo(record)
    assert not target.exists()


def test_transaction_rollback_discards_all(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("A0")
    txn = Transaction()
    txn.add(StagedFileWrite(a, "A1", tmp_path / ".staging"))
    txn.add(StagedFileWrite(b, "B1", tmp_path / ".staging"))
    assert len(txn.diffs()) == 2
    txn.rollback()
    assert a.read_text() == "A0" and not b.exists()  # nothing committed


def test_transaction_commit_then_undo(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("A0")
    txn = Transaction()
    txn.add(StagedFileWrite(a, "A1", tmp_path / ".staging"))
    txn.commit()
    assert a.read_text() == "A1"
    txn.undo_committed()
    assert a.read_text() == "A0"


def test_postcondition_expected_change():
    ok = check_postcondition(expected_change=True, pre_state_hash="a", post_state_hash="b")
    assert ok.ok
    bad = check_postcondition(expected_change=True, pre_state_hash="a", post_state_hash="a")
    assert not bad.ok and "expected a state change" in bad.reason


def test_postcondition_denied_must_not_change():
    ok = check_postcondition(expected_change=False, pre_state_hash="a", post_state_hash="a")
    assert ok.ok
    bad = check_postcondition(expected_change=False, pre_state_hash="a", post_state_hash="z")
    assert not bad.ok and "should have" in bad.reason


def test_audit_hash_chain_detects_tampering():
    audit = HashChainAudit()
    audit.append(tool="gmail_send", action="send_external", decision="DENY",
                 reasons=("canary would leave",), seal_nonce="", outcome="blocked")
    audit.append(tool="files_write", action="write_local", decision="ALLOW",
                 reasons=("within mandate",), seal_nonce="n1", outcome="executed")
    assert audit.verify_chain()
    # Tamper with a record's content: the chain no longer verifies.
    audit._records[0].decision = "ALLOW"  # noqa: SLF001 — deliberately corrupting
    assert not audit.verify_chain()


def test_audit_redacts_canary_and_secret_in_reasons():
    audit = HashChainAudit()
    rec = audit.append(tool="t", action="a", decision="DENY",
                       reasons=("leaked ACCOUNT_CANARY_9 and sk-abcdef123456",),
                       seal_nonce="", outcome="blocked")
    joined = " ".join(rec.reasons)
    assert "ACCOUNT_CANARY_9" not in joined and "sk-abcdef123456" not in joined
    assert "REDACTED" in joined


def test_redact_text_structured_log_injection():
    # A forged log-line attempt in content is redacted / neutralized as data.
    txt = "user note CANARY_TOKEN_42 with sk-deadbeef00112233"
    out = redact_text(txt)
    assert "CANARY_TOKEN_42" not in out and "sk-deadbeef00112233" not in out
