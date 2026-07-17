"""SQLite migrations/audit log and the secret-store abstraction."""

from unittest.mock import patch

from hearth.storage.db import Database
from hearth.storage.keychain import InMemorySecretStore, KeychainSecretStore


def test_migrations_idempotent(tmp_path):
    path = tmp_path / "m.db"
    first = Database(path)
    version = first.schema_version
    first.close()
    second = Database(path)  # reopening must not re-run migrations
    assert second.schema_version == version
    second.close()


def test_conversations_and_messages(db):
    conversation = db.create_conversation("Test")
    db.add_message(conversation, "user", "hello")
    db.add_message(conversation, "assistant", "hi")
    messages = db.get_messages(conversation)
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_action_lifecycle(db):
    action = db.record_action("tool_x", {"a": 1}, "write", "pending", "preview text")
    db.update_action(action, "completed", "did it")
    (row,) = db.list_actions()
    assert row["status"] == "completed"
    assert row["preview"] == "preview text"
    assert row["decided_at"] is not None


def test_approved_folders_roundtrip(db):
    db.add_approved_folder("/tmp/a")
    db.add_approved_folder("/tmp/a")  # duplicate ignored
    db.add_approved_folder("/tmp/b")
    assert db.list_approved_folders() == ["/tmp/a", "/tmp/b"]
    db.remove_approved_folder("/tmp/a")
    assert db.list_approved_folders() == ["/tmp/b"]


def test_shortcuts_and_connectors(db):
    db.add_approved_shortcut("Log Water", changes_data=True)
    assert db.list_approved_shortcuts() == [("Log Water", True)]
    db.set_connector_status("web", "granted")
    assert db.get_connector_status("web") == "granted"
    db.set_connector_status("web", "revoked")
    assert db.get_connector_status("web") == "revoked"
    assert db.get_connector_status("never_set") == "disconnected"


def test_no_secrets_in_db_schema(db):
    """Guard: no column anywhere is named like a secret holder."""
    tables = db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    for table in tables:
        for col in db._conn.execute(f"PRAGMA table_info({table['name']})").fetchall():
            assert not any(
                word in col["name"].lower()
                for word in ("token", "secret", "password", "credential")
            )


def test_in_memory_secret_store():
    store = InMemorySecretStore()
    assert store.get("k") is None
    store.set("k", "v")
    assert store.get("k") == "v"
    store.delete("k")
    store.delete("k")  # deleting twice is fine
    assert store.get("k") is None


def test_keychain_store_calls_keyring():
    store = KeychainSecretStore(service="HearthTest")
    with (
        patch("keyring.set_password") as set_pw,
        patch("keyring.get_password", return_value="tok") as get_pw,
    ):
        store.set("k", "tok")
        assert store.get("k") == "tok"
    set_pw.assert_called_once_with("HearthTest", "k", "tok")
    get_pw.assert_called_once_with("HearthTest", "k")


def test_keychain_store_survives_locked_keyring():
    import keyring.errors

    store = KeychainSecretStore(service="HearthTest")
    with patch("keyring.get_password", side_effect=keyring.errors.KeyringError("locked")):
        assert store.get("k") is None  # degrades to 'not connected'
