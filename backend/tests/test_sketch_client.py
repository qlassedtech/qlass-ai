import json

from app.services.llm_client import LLMResult
from app.services import sketch_client


def _result(scene) -> LLMResult:
    return LLMResult(
        text=json.dumps(scene), model="claude-haiku-4-5-20251001",
        input_tokens=10, output_tokens=10, cache_write_tokens=0, cache_read_tokens=0,
    )


_VALID_SCENE = [
    {"type": "rect", "x": 40, "y": 30, "w": 120, "h": 80},
    {"type": "ellipse", "x": 200, "y": 100, "rx": 50, "ry": 30},
    {"type": "line", "points": [[10, 10], [50, 50], [90, 10]]},
    {"type": "arrow", "x1": 60, "y1": 90, "x2": 60, "y2": 140},
    {"type": "text", "x": 45, "y": 25, "text": "Nucleus"},
]


async def test_valid_json_scene_parses_correctly(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return _result(_VALID_SCENE)

    monkeypatch.setattr(sketch_client, "call_llm", fake_call_llm)
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene == _VALID_SCENE
    assert result.input_tokens == 10


async def test_markdown_fenced_json_is_stripped_and_parses(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        fenced = "```json\n" + json.dumps(_VALID_SCENE) + "\n```"
        return LLMResult(text=fenced, model="claude-haiku-4-5-20251001", input_tokens=8, output_tokens=8)

    monkeypatch.setattr(sketch_client, "call_llm", fake_call_llm)
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene == _VALID_SCENE
    assert result.input_tokens == 8


async def test_malformed_non_json_response_returns_none_none(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return LLMResult(text="not json at all, sorry", model="claude-haiku-4-5-20251001", input_tokens=5, output_tokens=5)

    monkeypatch.setattr(sketch_client, "call_llm", fake_call_llm)
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene is None
    assert result is None


async def test_unrecognized_type_element_is_dropped_but_scene_still_usable(monkeypatch):
    """
    A single structurally-broken element (missing fields, or a "type" the
    frontend renderer doesn't know how to draw) is dropped rather than
    failing the whole scene — see the comment on _parse_scene/
    _is_valid_element for the reasoning. Only a scene with NO valid
    elements at all falls back to (None, None), covered separately below.
    """
    scene_with_one_bad_element = _VALID_SCENE + [{"type": "sparkle", "x": 1, "y": 2}]

    async def fake_call_llm(system_prompt, messages, model):
        return _result(scene_with_one_bad_element)

    monkeypatch.setattr(sketch_client, "call_llm", fake_call_llm)
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene == _VALID_SCENE  # the bad "sparkle" element was dropped
    assert result is not None


async def test_scene_with_only_invalid_elements_returns_none_none(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return _result([{"type": "sparkle", "x": 1, "y": 2}, {"type": "rect", "x": "not-a-number"}])

    monkeypatch.setattr(sketch_client, "call_llm", fake_call_llm)
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene is None
    assert result is None
