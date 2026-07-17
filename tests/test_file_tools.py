"""File tools end-to-end through the gate (deletes mocked away from Trash)."""

import pytest

from hearth.connectors.files import ApprovedRoots, register_file_tools


@pytest.fixture
def file_env(tmp_path, harness, registry, monkeypatch):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "hello.txt").write_text("hello world")
    (root / "data.bin").write_bytes(b"\x00\x01")
    register_file_tools(registry, ApprovedRoots(lambda: [str(root)]))
    harness.granted.add("files")
    trashed: list[str] = []
    monkeypatch.setattr("send2trash.send2trash", lambda p: trashed.append(p))
    return root, harness, trashed


async def test_list(file_env):
    root, h, _ = file_env
    result = await h.gate.execute("files_list", {"folder": str(root)})
    names = [e["name"] for e in result.data["entries"]]
    assert "hello.txt" in names


async def test_read_text(file_env):
    root, h, _ = file_env
    result = await h.gate.execute("files_read", {"path": str(root / "hello.txt")})
    assert result.data["content"] == "hello world"


async def test_read_binary_refused(file_env):
    root, h, _ = file_env
    result = await h.gate.execute("files_read", {"path": str(root / "data.bin")})
    assert not result.ok


async def test_write_requires_approval_and_respects_rejection(file_env):
    root, h, _ = file_env
    h.approve_next = False
    target = root / "new.txt"
    result = await h.gate.execute("files_write", {"path": str(target), "content": "x"})
    assert not result.ok and not target.exists()

    h.approve_next = True
    result = await h.gate.execute("files_write", {"path": str(target), "content": "x"})
    assert result.ok and target.read_text() == "x"


async def test_no_silent_overwrite(file_env):
    root, h, _ = file_env
    h.approve_next = True
    result = await h.gate.execute(
        "files_write", {"path": str(root / "hello.txt"), "content": "clobber"}
    )
    assert not result.ok and (root / "hello.txt").read_text() == "hello world"


async def test_move_and_delete_to_trash(file_env):
    root, h, trashed = file_env
    h.approve_next = True
    result = await h.gate.execute(
        "files_move",
        {"source": str(root / "hello.txt"), "destination": str(root / "hi.txt")},
    )
    assert result.ok and (root / "hi.txt").exists()

    result = await h.gate.execute("files_delete", {"path": str(root / "hi.txt")})
    assert result.ok and trashed == [str(root / "hi.txt")]


async def test_search_by_content(file_env):
    root, h, _ = file_env
    result = await h.gate.execute("files_search", {"query": "hello"})
    assert any("hello.txt" in hit["path"] for hit in result.data["results"])


async def test_outside_root_blocked_through_tool(file_env, tmp_path):
    _, h, _ = file_env
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    result = await h.gate.execute("files_read", {"path": str(outside)})
    assert not result.ok and "outside" in result.error.lower()
