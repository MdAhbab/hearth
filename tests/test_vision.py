"""Vision path: image encoding/downscaling, wire format, agent loop image
delivery, the files_view_image tool, and the gated screenshot tool."""

import base64
import json

import httpx
import pytest

from hearth.agent.loop import AgentLoop
from hearth.agent.tools import RiskLevel, ToolResult
from hearth.config import ModelConfig, OllamaConfig
from hearth.images import ImageError, encode_image_file, encode_qimage, is_image_path
from hearth.runtime.provider import ChatMessage, ChatResult, OllamaProvider, ToolCall


def _make_png(path, width=64, height=64):
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("orange"))
    assert image.save(str(path), "PNG")


def test_encode_downscales_large_images(tmp_path):
    from PySide6.QtGui import QImage

    big = tmp_path / "big.png"
    _make_png(big, width=2400, height=600)
    encoded = encode_image_file(big)
    reloaded = QImage.fromData(base64.b64decode(encoded))
    assert max(reloaded.width(), reloaded.height()) <= 1024
    assert reloaded.width() > 0


def test_encode_small_image_kept(tmp_path):
    from PySide6.QtGui import QImage

    small = tmp_path / "small.png"
    _make_png(small, 64, 64)
    reloaded = QImage.fromData(base64.b64decode(encode_image_file(small)))
    assert (reloaded.width(), reloaded.height()) == (64, 64)


def test_encode_rejects_non_image(tmp_path):
    bad = tmp_path / "not_image.png"
    bad.write_text("this is text")
    with pytest.raises(ImageError):
        encode_image_file(bad)


def test_is_image_path():
    assert is_image_path("photo.JPG") and is_image_path("x/y/z.webp")
    assert not is_image_path("notes.txt")


def test_encode_qimage_flattens_alpha():
    from PySide6.QtGui import QColor, QImage

    image = QImage(32, 32, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 0, 0, 128))
    encoded = encode_qimage(image)
    assert len(base64.b64decode(encoded)) > 0


async def test_provider_sends_images_on_wire():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, text=json.dumps({"message": {"content": "ok"}, "done": True}))

    provider = OllamaProvider(ModelConfig(), OllamaConfig(), transport=httpx.MockTransport(handler))
    await provider.chat([ChatMessage("user", "what is this?", images=["QUJD"])])
    assert seen["messages"][0]["images"] == ["QUJD"]


async def test_loop_delivers_tool_image_as_user_message(harness, registry):
    from pydantic import BaseModel

    from hearth.agent.tools import ToolSpec

    class P(BaseModel):
        pass

    async def photo_tool(_: P) -> ToolResult:
        return ToolResult(ok=True, data="loaded photo.png", image_b64="SU1H")

    registry.register(
        ToolSpec(
            name="photo_tool",
            description="",
            params_model=P,
            risk=RiskLevel.READ,
            permission="test",
            handler=photo_tool,
        )
    )

    class ScriptedProvider:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, tools=None, on_chunk=None):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return ChatResult("", [ToolCall("photo_tool", {})])
            return ChatResult("It is an orange square.", [])

    provider = ScriptedProvider()
    loop = AgentLoop(provider, registry, harness.gate, max_steps=4)
    answer = await loop.run([], "look at the photo")
    assert answer == "It is an orange square."
    final_messages = provider.calls[-1]
    image_messages = [m for m in final_messages if m.images]
    assert len(image_messages) == 1
    assert image_messages[0].role == "user"
    assert image_messages[0].images == ["SU1H"]
    assert "not instructions" in image_messages[0].content


async def test_user_images_reach_first_request(harness, registry):
    class Provider:
        def __init__(self):
            self.messages = None

        async def chat(self, messages, tools=None, on_chunk=None):
            self.messages = messages
            return ChatResult("nice photo", [])

    provider = Provider()
    loop = AgentLoop(provider, registry, harness.gate, max_steps=2)
    await loop.run([], "describe this", images=["QQ=="])
    user_messages = [m for m in provider.messages if m.role == "user"]
    assert user_messages[-1].images == ["QQ=="]


async def test_files_view_image_tool(tmp_path, harness, registry):
    from hearth.connectors.files import ApprovedRoots, register_file_tools

    root = tmp_path / "pics"
    root.mkdir()
    _make_png(root / "photo.png")
    (root / "notes.txt").write_text("text")
    register_file_tools(registry, ApprovedRoots(lambda: [str(root)]))
    harness.granted.add("files")

    result = await harness.gate.execute("files_view_image", {"path": str(root / "photo.png")})
    assert result.ok and result.image_b64

    result = await harness.gate.execute("files_view_image", {"path": str(root / "notes.txt")})
    assert not result.ok  # not an image

    outside = tmp_path / "secret.png"
    _make_png(outside)
    result = await harness.gate.execute("files_view_image", {"path": str(outside)})
    assert not result.ok and "outside" in result.error.lower()


async def test_screenshot_gated_and_confirmed(harness, registry):
    from hearth.connectors.system.tools import register_system_tools

    register_system_tools(
        registry,
        clipboard_get=lambda: "",
        clipboard_set=lambda t: None,
        notifier=lambda a, b: None,
        approved_shortcuts=lambda: [],
        screen_capture=lambda: "U0NSRUVO",
    )
    harness.granted.add("system")

    spec = registry.get("system_screenshot")
    assert spec.risk is RiskLevel.WRITE  # sensitive: always confirm

    harness.approve_next = False
    result = await harness.gate.execute("system_screenshot", {})
    assert not result.ok  # rejected → nothing captured

    harness.approve_next = True
    result = await harness.gate.execute("system_screenshot", {})
    assert result.ok and result.image_b64 == "U0NSRUVO"


async def test_screenshot_failure_is_clean_error(harness, registry):
    from hearth.connectors.system.tools import register_system_tools

    def broken_capture() -> str:
        raise RuntimeError("no permission")

    register_system_tools(
        registry,
        clipboard_get=lambda: "",
        clipboard_set=lambda t: None,
        notifier=lambda a, b: None,
        approved_shortcuts=lambda: [],
        screen_capture=broken_capture,
    )
    harness.granted.add("system")
    harness.approve_next = True
    result = await harness.gate.execute("system_screenshot", {})
    assert not result.ok and "Screen Recording" in result.error
