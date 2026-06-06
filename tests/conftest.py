"""Shared pytest fixtures for zmm tests."""

import sys
from pathlib import Path

import pytest

# Ensure the project root and tests dir are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import zoom_meeting_manager as zmm  # noqa: E402

# Re-export for convenience (helpers module holds the data/tree builders).
from helpers import VALID_MODEL_OUTPUT, make_meeting_tree  # noqa: E402,F401


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = FakeMessage(content)
        self.finish_reason = finish_reason


class FakeResponse:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [FakeChoice(content, finish_reason)]


class FakeCompletions:
    def __init__(self, parent):
        self._parent = parent

    def create(self, *, model, messages, **kwargs):
        self._parent.calls.append({"model": model, "messages": messages, "kwargs": kwargs})
        content = self._parent.next_content
        if callable(content):
            content = content(model, messages)
        return FakeResponse(content, getattr(self._parent, "finish_reason", "stop"))


class FakeChat:
    def __init__(self, parent):
        self.completions = FakeCompletions(parent)


class FakeModelsList:
    def __init__(self, ids):
        self.data = [type("M", (), {"id": i})() for i in ids]


class FakeModels:
    def __init__(self, ids):
        self._ids = ids

    def list(self):
        return FakeModelsList(self._ids)


class FakeClient:
    """Stand-in for openai.OpenAI used in tests."""

    def __init__(self, content="{}", model_ids=None, finish_reason="stop"):
        self.next_content = content
        self.finish_reason = finish_reason
        self.calls = []
        self.chat = FakeChat(self)
        self.models = FakeModels(model_ids or [])


@pytest.fixture
def fake_client(monkeypatch):
    """Patch zmm.client_for to return a controllable FakeClient."""
    def _install(content="{}", model_ids=None, finish_reason="stop"):
        client = FakeClient(content=content, model_ids=model_ids, finish_reason=finish_reason)
        monkeypatch.setattr(zmm, "client_for", lambda cfg: client)
        return client
    return _install


@pytest.fixture
def parse_args():
    """Parse a zmm argv list into a Namespace using the real parser."""
    parser = zmm.build_parser()

    def _parse(argv):
        return parser.parse_args(argv)

    return _parse
