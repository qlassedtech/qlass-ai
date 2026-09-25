"""
app.services.mindmap (tree parsing, deterministic layout, PNG rendering)
and app.services.branding.stamp_logo. Every LLM call is mocked — nothing
here touches the network.
"""
import asyncio
from io import BytesIO

import pytest
from PIL import Image

from app.services import branding, mindmap
from app.services.llm_client import LLMResult
from app.services.sketch_client import _is_valid_element


# --- tree parsing -------------------------------------------------------

_GOOD_JSON = (
    '{"center": "Photosynthesis", "branches": ['
    '{"label": "Inputs", "children": ["Sunlight", "Water", "CO2"]}, '
    '{"label": "Outputs", "children": ["Glucose", "Oxygen"]}, '
    '{"label": "Where", "children": []}]}'
)


def test_parse_good_json():
    tree = mindmap.parse_mindmap_tree(_GOOD_JSON)
    assert tree == {
        "center": "Photosynthesis",
        "branches": [
            {"label": "Inputs", "children": ["Sunlight", "Water", "CO2"]},
            {"label": "Outputs", "children": ["Glucose", "Oxygen"]},
            {"label": "Where", "children": []},
        ],
    }


def test_parse_strips_markdown_fences():
    tree = mindmap.parse_mindmap_tree(f"```json\n{_GOOD_JSON}\n```")
    assert tree is not None and tree["center"] == "Photosynthesis"


@pytest.mark.parametrize("raw", [
    "not json at all",
    "[1, 2, 3]",  # a list, not an object
    '{"center": "", "branches": []}',  # blank centre
    '{"center": "Topic"}',  # no branches key
    '{"center": "Topic", "branches": [{"label": "One"}, {"label": "Two"}]}',  # fewer than 3 usable branches
    '{"center": "Topic", "branches": [{"label": 5}, {"nope": 1}, "str", {"label": "Only"}]}',
])
def test_parse_garbage_returns_none(raw):
    assert mindmap.parse_mindmap_tree(raw) is None


def test_parse_clamps_counts_and_truncates_labels():
    branches = [{"label": "B" * 40, "children": ["C" * 40] * 6} for _ in range(9)]
    raw = '{"center": "%s", "branches": %s}' % ("X" * 50, __import__("json").dumps(branches))
    tree = mindmap.parse_mindmap_tree(raw)
    assert len(tree["center"]) == mindmap.MAX_CENTER_CHARS == 24
    assert len(tree["branches"]) == mindmap.MAX_BRANCHES == 6
    for branch in tree["branches"]:
        assert len(branch["label"]) == mindmap.MAX_BRANCH_CHARS == 16
        assert len(branch["children"]) == mindmap.MAX_CHILDREN == 3
        assert all(len(child) == mindmap.MAX_CHILD_CHARS == 14 for child in branch["children"])


def test_parse_drops_non_string_children_and_collapses_whitespace():
    raw = '{"center": "  Water   cycle ", "branches": [{"label": "A", "children": [1, null, "  Rain  "]}, {"label": "B"}, {"label": "C", "children": "oops"}]}'
    tree = mindmap.parse_mindmap_tree(raw)
    assert tree["center"] == "Water cycle"
    assert tree["branches"][0]["children"] == ["Rain"]
    assert tree["branches"][2]["children"] == []


def test_generate_mindmap_tree_uses_sonnet_and_returns_usage(monkeypatch):
    calls = []

    async def fake_call_llm(system_prompt, messages, model):
        calls.append((system_prompt, messages, model))
        return LLMResult(text=_GOOD_JSON, model=model, input_tokens=50, output_tokens=40)

    monkeypatch.setattr(mindmap, "call_llm", fake_call_llm)
    tree, result = asyncio.run(mindmap.generate_mindmap_tree("photosynthesis"))
    assert tree["center"] == "Photosynthesis"
    assert result.model == "claude-sonnet-4-6" and result.output_tokens == 40
    assert len(calls) == 1  # one call, no critique pass
    assert calls[0][2] == "claude-sonnet-4-6"
    assert "photosynthesis" in calls[0][1][-1]["content"]


def test_generate_mindmap_tree_garbage_returns_none_none(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return LLMResult(text="Sorry, I can't do that.", model=model)

    monkeypatch.setattr(mindmap, "call_llm", fake_call_llm)
    assert asyncio.run(mindmap.generate_mindmap_tree("anything")) == (None, None)


def test_generate_mindmap_scene_is_tree_then_layout(monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return LLMResult(text=_GOOD_JSON, model=model, input_tokens=1, output_tokens=1)

    monkeypatch.setattr(mindmap, "call_llm", fake_call_llm)
    scene, result = asyncio.run(mindmap.generate_mindmap_scene("photosynthesis"))
    assert result is not None
    assert scene[0]["type"] == "ellipse" and scene[1]["type"] == "text"
    # scene order is right side then left side, not branch order
    assert sorted(e["label"] for e in scene if e.get("level") == 1) == ["Inputs", "Outputs", "Where"]


# --- layout geometry ----------------------------------------------------

def _tree(n_branches: int, n_children: int, branch_len: int = 6, child_len: int = 6) -> dict:
    return {
        "center": "Centre",
        "branches": [
            {"label": ("B%d" % i).ljust(branch_len, "x")[:branch_len], "children": [("c%d" % j).ljust(child_len, "y")[:child_len] for j in range(n_children)]}
            for i in range(n_branches)
        ],
    }


def test_layout_centre_elements_match_schema():
    scene = mindmap.layout_mindmap({"center": "Photosynthesis", "branches": _tree(3, 0)["branches"]})
    assert scene[0] == {
        "type": "ellipse", "x": 200, "y": 150, "rx": 60, "ry": 26, "color": "#23252b", "width": 2.5, "fill": "#f3efe6",
    }
    assert scene[1] == {
        "type": "text", "x": 200, "y": 150, "text": "Photosynthesis", "size": 14, "weight": "bold",
        "align": "center", "color": "#23252b",
    }
    long_scene = mindmap.layout_mindmap({"center": "A much longer centre topic", "branches": _tree(3, 0)["branches"]})
    assert long_scene[1]["size"] == 12


def test_layout_branch_elements_match_schema_and_pass_sketch_validation():
    scene = mindmap.layout_mindmap(_tree(4, 2))
    branches = [e for e in scene if e["type"] == "branch"]
    assert len(branches) == 4 + 4 * 2
    for element in branches:
        assert set(element) == {"type", "points", "color", "width", "label", "level", "label_at"}
        assert len(element["points"]) == 3
        assert all(isinstance(c, int) for p in element["points"] for c in p)
        assert all(0 <= p[0] <= 400 and 0 <= p[1] <= 300 for p in element["points"])
        assert element["level"] in (1, 2)
        assert (element["width"], element["label_at"]) == ((4, "mid") if element["level"] == 1 else (2, "end"))
        assert _is_valid_element(element)  # the shared validator accepts the new type
    # every element (centre included) is valid for the shared renderer schema
    assert all(_is_valid_element(e) for e in scene)


def test_layout_colours_cycle_and_sides_alternate():
    scene = mindmap.layout_mindmap(_tree(6, 1))
    mains = sorted((e for e in scene if e.get("level") == 1), key=lambda e: e["label"])
    assert [e["color"] for e in mains] == mindmap.PALETTE
    for i, element in enumerate(mains):
        start_x = element["points"][0][0]
        end_x = element["points"][2][0]
        if i % 2 == 0:
            assert start_x > 200 and end_x > start_x, f"branch {i} should be on the right"
        else:
            assert start_x < 200 and end_x < start_x, f"branch {i} should be on the left"
        # children run in the same direction as their branch and share its colour
        children = [c for c in scene if c.get("level") == 2 and c["points"][0] == element["points"][2]]
        assert len(children) == 1
        assert children[0]["color"] == element["color"]
        assert (children[0]["points"][2][0] > end_x) == (i % 2 == 0)


def test_layout_seven_branches_cycles_palette():
    tree = _tree(6, 0)
    tree["branches"].append({"label": "Seven", "children": []})
    # layout doesn't clamp (parse does) — a 7th branch just reuses colour 0
    # and lands on the right side; there's no 4-per-side angle set, so this
    # must raise clearly rather than silently mis-lay out.
    with pytest.raises(KeyError):
        mindmap.layout_mindmap(tree)


def test_layout_main_branch_geometry():
    scene = mindmap.layout_mindmap(_tree(1, 0))
    (branch,) = [e for e in scene if e["type"] == "branch"]
    # single right-side branch at 0 degrees: start at radius 62, end at 124, control pushed 10px down
    assert branch["points"] == [[262, 150], [293, 160], [324, 150]]


def _label_boxes(scene: list[dict]) -> list[tuple[str, float, float, float, float]]:
    """Estimated label boxes per the renderer rules: 13px bold ~ 8px/char,
    11px normal ~ 6px/char, height = font size, baseline at the anchor y."""
    boxes = []
    for element in scene:
        if element["type"] != "branch":
            continue
        x, y, size, _weight, _color = mindmap.branch_label_anchor(element)
        width = (8 if size == 13 else 6) * len(element["label"])
        boxes.append((element["label"], x - width / 2, y - size, x + width / 2, y))
    return boxes


def _overlap(a, b) -> bool:
    return a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]


@pytest.mark.parametrize("n_branches", [3, 4, 5, 6])
def test_worst_case_labels_never_overlap_and_stay_on_canvas(n_branches):
    tree = {
        "center": "X" * mindmap.MAX_CENTER_CHARS,
        "branches": [
            {"label": "W" * mindmap.MAX_BRANCH_CHARS, "children": ["M" * mindmap.MAX_CHILD_CHARS] * mindmap.MAX_CHILDREN}
            for _ in range(n_branches)
        ],
    }
    scene = mindmap.layout_mindmap(tree)  # asserts every point is on-canvas
    boxes = _label_boxes(scene)
    assert len(boxes) == n_branches * (1 + mindmap.MAX_CHILDREN)
    for box in boxes:
        assert box[1] >= 0 and box[2] >= 0 and box[3] <= 400 and box[4] <= 300, box
    collisions = [
        (a[0], b[0]) for i, a in enumerate(boxes) for b in boxes[i + 1:] if _overlap(a, b)
    ]
    assert collisions == []


# --- PNG rendering -------------------------------------------------------

def test_render_mindmap_png_round_trip():
    png = mindmap.render_mindmap_png(mindmap.layout_mindmap(_tree(5, 3)))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(png)) as image:
        assert image.size == (1200, 900)
        # something was actually drawn: not a blank white canvas
        assert image.convert("L").getextrema()[0] < 128


def test_render_mindmap_png_is_stamped(monkeypatch):
    stamped = []

    def fake_stamp(png_bytes, **kwargs):
        stamped.append(kwargs)
        return b"stamped"

    monkeypatch.setattr(mindmap, "stamp_logo", fake_stamp)
    assert mindmap.render_mindmap_png(mindmap.layout_mindmap(_tree(3, 1))) == b"stamped"
    assert len(stamped) == 1


def test_build_mindmap_image_bills_and_counts_toward_image_quota(db_session, monkeypatch):
    from app.models.core import Centre, Student
    from app.services import cost_tracker

    centre = Centre(name="Test School")
    db_session.add(centre)
    db_session.commit()
    student = Student(name="S", phone="919000000077", centre_id=centre.id)
    db_session.add(student)
    db_session.commit()
    cost_tracker.add_credits(db_session, student.id, 50.0, note="test credit")

    async def fake_call_llm(system_prompt, messages, model):
        return LLMResult(text=_GOOD_JSON, model=model, input_tokens=100, output_tokens=60)

    monkeypatch.setattr(mindmap, "call_llm", fake_call_llm)
    png = asyncio.run(mindmap.build_mindmap_image(db_session, student.id, "photosynthesis"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    counts = cost_tracker.get_usage_counts_by_service(db_session, student.id, ["mindmap_image"], "week")
    assert counts == {"mindmap_image": 1}
    from app.models.core import CreditEvent
    features = [e.feature for e in db_session.query(CreditEvent).filter(CreditEvent.student_id == student.id)]
    assert "mindmap_generate" in features


def test_build_mindmap_image_returns_none_on_failure(db_session, monkeypatch):
    async def fake_call_llm(system_prompt, messages, model):
        return LLMResult(text="garbage", model=model)

    monkeypatch.setattr(mindmap, "call_llm", fake_call_llm)
    assert asyncio.run(mindmap.build_mindmap_image(db_session, 1, "x")) is None

    async def exploding_call_llm(system_prompt, messages, model):
        raise RuntimeError("boom")

    monkeypatch.setattr(mindmap, "call_llm", exploding_call_llm)
    assert asyncio.run(mindmap.build_mindmap_image(db_session, 1, "x")) is None


# --- logo stamping -------------------------------------------------------

def _blank_png(width: int, height: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_stamp_logo_outputs_png_of_same_size_with_logo_drawn():
    assert branding.LOGO_PATH.exists()
    original = _blank_png(1024, 768)
    stamped = branding.stamp_logo(original)
    assert stamped != original
    assert stamped[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(stamped)) as image:
        assert image.size == (1024, 768)
        grey = image.convert("L")
        # bottom-right corner region is no longer plain white...
        assert grey.crop((1024 - 160, 768 - 130, 1024 - 20, 768 - 15)).getextrema()[0] < 250
        # ...while the top-left is untouched
        assert grey.crop((0, 0, 400, 300)).getextrema() == (255, 255)


def test_stamp_logo_failure_returns_input_unchanged(monkeypatch):
    monkeypatch.setattr(branding, "LOGO_PATH", branding.LOGO_PATH.parent / "does-not-exist.png")
    original = _blank_png(200, 100)
    assert branding.stamp_logo(original) == original
    # undecodable input is also returned as-is rather than raising
    monkeypatch.undo()
    assert branding.stamp_logo(b"not a png") == b"not a png"


def test_generate_image_stamps_azure_output(monkeypatch):
    """image_client.generate_image stamps inside itself so whatsapp.py /
    student_app.py get branded images with no call-site changes."""
    import base64

    import httpx

    from app.config import settings
    from app.services import image_client

    monkeypatch.setattr(settings, "azure_image_endpoint", "https://example.invalid/images")
    monkeypatch.setattr(settings, "azure_image_key", "k")
    monkeypatch.setattr(settings, "azure_image_deployment", "d")
    raw = _blank_png(300, 300)

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(raw).decode()}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    stamped = []
    monkeypatch.setattr(image_client, "stamp_logo", lambda b: stamped.append(b) or b"stamped")
    assert asyncio.run(image_client.generate_image("a cell")) == b"stamped"
    assert stamped == [raw]


def test_pdf_footers_still_render_with_brand_logo():
    from app.services import pdf_render

    assert pdf_render._brand_logo_size() is not None
    pdf = pdf_render.render_workbook_pdf(
        "Fractions", "6", "Test School", None,
        [{"question": "1/2 + 1/4 = ?", "answer": "3/4"}], include_answer_key=True,
    )
    assert pdf[:4] == b"%PDF"
