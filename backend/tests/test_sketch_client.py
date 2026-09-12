import json

from app.services.llm_client import LLMResult
from app.services import sketch_client


def _result(scene, *, model="claude-sonnet-4-6", input_tokens=10, output_tokens=10) -> LLMResult:
    return LLMResult(
        text=json.dumps(scene), model=model,
        input_tokens=input_tokens, output_tokens=output_tokens, cache_write_tokens=0, cache_read_tokens=0,
    )


def _ok_critique(input_tokens=3, output_tokens=1) -> LLMResult:
    return LLMResult(
        text="OK", model="claude-sonnet-4-6",
        input_tokens=input_tokens, output_tokens=output_tokens, cache_write_tokens=0, cache_read_tokens=0,
    )


_VALID_SCENE = [
    {"type": "rect", "x": 40, "y": 30, "w": 120, "h": 80},
    {"type": "ellipse", "x": 200, "y": 100, "rx": 50, "ry": 30},
    {"type": "line", "points": [[10, 10], [50, 50], [90, 10]]},
    {"type": "arrow", "x1": 60, "y1": 90, "x2": 60, "y2": 140},
    {"type": "text", "x": 45, "y": 25, "text": "Nucleus"},
]


def _sequenced_call_llm(*results):
    """
    Returns a fake call_llm that hands back `results` in order across
    successive calls — generate_sketch_scene now makes two calls
    (generation, then critique) per invocation, so tests need to mock
    both in sequence rather than a single call.
    """
    calls = list(results)

    async def fake_call_llm(system_prompt, messages, model):
        return calls.pop(0)

    return fake_call_llm


# --- Basic generation + critique="OK" pass-through -----------------------


async def test_valid_json_scene_parses_correctly_and_critique_ok_keeps_it(monkeypatch):
    monkeypatch.setattr(
        sketch_client, "call_llm", _sequenced_call_llm(_result(_VALID_SCENE), _ok_critique()),
    )
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene == _VALID_SCENE
    # combined usage across both calls
    assert result.input_tokens == 10 + 3
    assert result.output_tokens == 10 + 1


async def test_markdown_fenced_json_is_stripped_and_parses(monkeypatch):
    fenced = "```json\n" + json.dumps(_VALID_SCENE) + "\n```"
    monkeypatch.setattr(
        sketch_client,
        "call_llm",
        _sequenced_call_llm(
            LLMResult(text=fenced, model="claude-sonnet-4-6", input_tokens=8, output_tokens=8),
            _ok_critique(),
        ),
    )
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene == _VALID_SCENE
    assert result.input_tokens == 8 + 3


async def test_malformed_non_json_response_returns_none_none(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return LLMResult(text="not json at all, sorry", model="claude-sonnet-4-6", input_tokens=5, output_tokens=5)

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
    monkeypatch.setattr(
        sketch_client,
        "call_llm",
        _sequenced_call_llm(_result(scene_with_one_bad_element), _ok_critique()),
    )
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
    # First-pass parse failure short-circuits before any critique call is
    # attempted — nothing left in `calls` to pop means a second call would
    # have raised IndexError, so this also implicitly asserts that.


# --- Critique pass behavior -----------------------------------------------


async def test_critique_returns_ok_keeps_collision_repaired_scene_unchanged(monkeypatch):
    # Two labels close enough to collide — collision repair should move
    # the second one, and critique="OK" should leave that repaired result
    # untouched (not revert to the original raw model output).
    colliding_scene = [
        {"type": "text", "x": 10, "y": 100, "text": "Alpha"},
        {"type": "text", "x": 12, "y": 101, "text": "Beta"},
    ]
    monkeypatch.setattr(
        sketch_client, "call_llm", _sequenced_call_llm(_result(colliding_scene), _ok_critique()),
    )
    scene, result = await sketch_client.generate_sketch_scene("two things")

    assert scene[0]["y"] == 100  # first label untouched
    assert scene[1]["y"] != 101  # second label moved to clear the collision
    assert result is not None


async def test_critique_returns_valid_correction_replaces_scene(monkeypatch):
    original_scene = [
        {"type": "arrow", "x1": 100, "y1": 50, "x2": 100, "y2": 10},
        {"type": "text", "x": 60, "y": 50, "text": "vy = 0"},
    ]
    corrected_scene = [
        {"type": "text", "x": 60, "y": 50, "text": "vy = 0"},
        {"type": "arrow", "x1": 20, "y1": 50, "x2": 80, "y2": 50},
    ]
    monkeypatch.setattr(
        sketch_client,
        "call_llm",
        _sequenced_call_llm(_result(original_scene), _result(corrected_scene, input_tokens=15, output_tokens=15)),
    )
    scene, result = await sketch_client.generate_sketch_scene("projectile at peak")

    assert scene == corrected_scene
    assert result.input_tokens == 10 + 15
    assert result.output_tokens == 10 + 15


async def test_critique_returns_invalid_correction_falls_back_to_original(monkeypatch, caplog):
    original_scene = _VALID_SCENE
    garbage_critique = LLMResult(
        text="not JSON and not the literal OK either", model="claude-sonnet-4-6",
        input_tokens=4, output_tokens=4,
    )
    monkeypatch.setattr(
        sketch_client, "call_llm", _sequenced_call_llm(_result(original_scene), garbage_critique),
    )
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene == original_scene
    assert result.input_tokens == 10 + 4  # critique call is still billed even though its output was discarded


async def test_critique_call_raising_falls_back_to_original_without_billing_the_failed_call(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return _result(_VALID_SCENE)

    calls = {"n": 0}

    async def fake_call_llm_seq(system_prompt, messages, model):
        calls["n"] += 1
        if calls["n"] == 1:
            return _result(_VALID_SCENE)
        raise RuntimeError("transient network blip")

    monkeypatch.setattr(sketch_client, "call_llm", fake_call_llm_seq)
    scene, result = await sketch_client.generate_sketch_scene("a plant cell")

    assert scene == _VALID_SCENE
    assert result.input_tokens == 10  # only the (successful) first call's usage


# --- _resolve_text_collisions in isolation ---------------------------------


def test_two_overlapping_labels_get_separated():
    scene = [
        {"type": "text", "x": 10, "y": 100, "text": "Stroma Lamellae"},
        {"type": "text", "x": 12, "y": 102, "text": "Light Reactions"},
    ]
    resolved = sketch_client._resolve_text_collisions(scene)

    assert resolved[0]["y"] == 100  # earlier element never moved
    box0 = sketch_client._text_box(resolved[0])
    box1 = sketch_client._text_box(resolved[1])
    assert not sketch_client._boxes_overlap(box0, box1)


def test_three_in_a_row_cascading_overlaps_all_resolve():
    scene = [
        {"type": "text", "x": 10, "y": 100, "text": "One"},
        {"type": "text", "x": 10, "y": 101, "text": "Two"},
        {"type": "text", "x": 10, "y": 102, "text": "Three"},
    ]
    resolved = sketch_client._resolve_text_collisions(scene)

    boxes = [sketch_client._text_box(e) for e in resolved]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            assert not sketch_client._boxes_overlap(boxes[i], boxes[j])


def test_non_overlapping_labels_left_untouched_exact_original_y():
    scene = [
        {"type": "text", "x": 10, "y": 20, "text": "Ocean"},
        {"type": "text", "x": 10, "y": 250, "text": "Cloud"},
    ]
    resolved = sketch_client._resolve_text_collisions(scene)

    assert resolved[0]["y"] == 20
    assert resolved[1]["y"] == 250
    assert resolved == scene


def test_label_that_would_go_off_canvas_is_clamped_sensibly():
    scene = [
        {"type": "text", "x": 10, "y": 296, "text": "Bottom Label"},
        {"type": "text", "x": 12, "y": 297, "text": "Overlapping Bottom"},
    ]
    resolved = sketch_client._resolve_text_collisions(scene)

    for element in resolved:
        assert 0 <= element["y"] <= sketch_client._CANVAS_H


def test_non_text_elements_always_passed_through_unchanged():
    scene = [
        {"type": "rect", "x": 1, "y": 2, "w": 3, "h": 4},
        {"type": "ellipse", "x": 5, "y": 6, "rx": 7, "ry": 8},
        {"type": "line", "points": [[0, 0], [1, 1]]},
        {"type": "arrow", "x1": 1, "y1": 2, "x2": 3, "y2": 4},
        {"type": "text", "x": 10, "y": 100, "text": "Label"},
    ]
    resolved = sketch_client._resolve_text_collisions(scene)

    assert resolved[0] is scene[0]
    assert resolved[1] is scene[1]
    assert resolved[2] is scene[2]
    assert resolved[3] is scene[3]


# --- Integration: collision-repair + critique together --------------------


async def test_pipeline_fixes_both_layout_collision_and_domain_inconsistency(monkeypatch):
    """
    A simplified recreation of the two real bugs live testing found: two
    labels close enough to smear together (chloroplast: "Stroma Lamellae"
    / "Light Reactions"), AND a vertical arrow drawn at the same point as
    a "vy = 0" label (projectile motion). Confirms the full pipeline —
    collision repair, then critique, then a second collision-repair pass —
    produces a clean result for both problems in one scene.
    """
    buggy_scene = [
        {"type": "arrow", "x1": 200, "y1": 100, "x2": 200, "y2": 60},  # wrong: vertical arrow at the peak
        {"type": "text", "x": 60, "y": 200, "text": "Stroma Lamellae"},
        {"type": "text", "x": 62, "y": 201, "text": "Light Reactions"},  # collides with the label above
        {"type": "text", "x": 180, "y": 100, "text": "vy = 0"},
    ]
    # The critique fixes the domain bug (drops the bad vertical arrow) but
    # doesn't itself worry about spacing — that's collision-repair's job,
    # exercised again afterward.
    critique_corrected = [
        {"type": "text", "x": 60, "y": 200, "text": "Stroma Lamellae"},
        {"type": "text", "x": 62, "y": 201, "text": "Light Reactions"},
        {"type": "text", "x": 180, "y": 100, "text": "vy = 0"},
    ]
    monkeypatch.setattr(
        sketch_client,
        "call_llm",
        _sequenced_call_llm(_result(buggy_scene), _result(critique_corrected, input_tokens=20, output_tokens=20)),
    )

    scene, result = await sketch_client.generate_sketch_scene("projectile motion peak + chloroplast labels")

    # Domain bug fixed: no vertical arrow left in the scene.
    arrows = [e for e in scene if e["type"] == "arrow"]
    assert arrows == []

    # Layout bug fixed: the two chloroplast labels no longer overlap.
    text_elements = [e for e in scene if e["type"] == "text"]
    stroma = next(e for e in text_elements if e["text"] == "Stroma Lamellae")
    light = next(e for e in text_elements if e["text"] == "Light Reactions")
    assert not sketch_client._boxes_overlap(sketch_client._text_box(stroma), sketch_client._text_box(light))

    assert result.input_tokens == 10 + 20
