"""Runtime manager: daemon detection, autostart policy, shutdown ownership."""

from unittest.mock import AsyncMock, MagicMock, patch

from hearth.config import OllamaConfig
from hearth.runtime.ollama_manager import OllamaRuntimeManager, RuntimeState


def make_manager(**config_kwargs) -> OllamaRuntimeManager:
    config = OllamaConfig(**config_kwargs)
    return OllamaRuntimeManager(config, ollama_binary="/fake/ollama")


async def test_ready_when_daemon_already_up():
    manager = make_manager()
    manager.is_daemon_up = AsyncMock(return_value=True)
    with patch("subprocess.Popen") as popen:
        state = await manager.ensure_running()
    assert state is RuntimeState.READY
    popen.assert_not_called()
    assert not manager.started_by_app  # never claim a daemon we didn't start


async def test_autostart_disabled_reports_unavailable():
    manager = make_manager(autostart=False)
    manager.is_daemon_up = AsyncMock(return_value=False)
    state = await manager.ensure_running()
    assert state is RuntimeState.UNAVAILABLE


async def test_starts_daemon_and_owns_it():
    manager = make_manager(startup_timeout_s=5)
    manager.is_daemon_up = AsyncMock(side_effect=[False, False, True])
    with patch("subprocess.Popen") as popen:
        state = await manager.ensure_running()
    assert state is RuntimeState.READY
    popen.assert_called_once()
    assert popen.call_args.args[0] == ["/fake/ollama", "serve"]
    assert manager.started_by_app


async def test_startup_timeout_reports_unavailable():
    manager = make_manager(startup_timeout_s=0.2)
    manager.is_daemon_up = AsyncMock(return_value=False)
    with patch("subprocess.Popen"):
        state = await manager.ensure_running()
    assert state is RuntimeState.UNAVAILABLE
    assert not manager.started_by_app


async def test_binary_missing_reports_unavailable():
    manager = make_manager()
    manager.is_daemon_up = AsyncMock(return_value=False)
    with patch("subprocess.Popen", side_effect=OSError("no such file")):
        state = await manager.ensure_running()
    assert state is RuntimeState.UNAVAILABLE


def test_shutdown_only_kills_own_process():
    manager = make_manager()
    process = MagicMock()
    process.poll.return_value = None

    # Not started by app: never touched.
    manager._process = process
    manager.started_by_app = False
    manager.shutdown()
    process.terminate.assert_not_called()

    # Started by app: terminated.
    manager._process = process
    manager.started_by_app = True
    manager.shutdown()
    process.terminate.assert_called_once()


async def test_model_available_checks_tags():
    manager = make_manager()

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "gemma4:e2b"}, {"name": "llama3:latest"}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResponse()

    with patch("hearth.runtime.ollama_manager.httpx.AsyncClient", FakeClient):
        assert await manager.model_available("gemma4:e2b")
        assert await manager.model_available("llama3")
        assert not await manager.model_available("gemma4:e4b")
