import json
import logging
import re

from app.services.llm_client import LLMResult, call_llm

logger = logging.getLogger(__name__)

# Cheap Haiku tier — this is a narrow, structured generation task (a short
# JSON array of drawing primitives), same tier used for other small
# auxiliary LLM calls in this codebase (see chat_core.NOTES_MODEL).
SKETCH_MODEL = "claude-haiku-4-5-20251001"

_VALID_TYPES = {"rect", "ellipse", "line", "arrow", "text"}

_MAX_ELEMENTS = 20
_CANVAS_W = 400
_CANVAS_H = 300

_SYSTEM_PROMPT = f"""\
You turn a short description of a diagram into a simple hand-drawn-style vector sketch, \
described as a JSON array of drawing primitives. Respond with ONLY the JSON array — no \
markdown code fences, no explanation, nothing else.

Canvas is a fixed {_CANVAS_W}x{_CANVAS_H} coordinate space, origin top-left. Keep every \
coordinate within 0-{_CANVAS_W} (x) and 0-{_CANVAS_H} (y).

Each element is one of:
  {{"type": "rect", "x": <number>, "y": <number>, "w": <number>, "h": <number>}}
  {{"type": "ellipse", "x": <number>, "y": <number>, "rx": <number>, "ry": <number>}}
  {{"type": "line", "points": [[x,y], [x,y], ...]}}
  {{"type": "arrow", "x1": <number>, "y1": <number>, "x2": <number>, "y2": <number>}}
  {{"type": "text", "x": <number>, "y": <number>, "text": "<short label>"}}

Order matters — the array is drawn/animated in order, one element at a time, so put outline \
shapes first and label ("text") elements last, after the shapes they label already exist.

Keep the whole scene to at most {_MAX_ELEMENTS} elements. Favor a small number of clear, \
well-labeled shapes over a cluttered diagram.

Text labels must never collide. A short label (one word) needs roughly 60px of horizontal \
clearance from the next label at a similar y; a longer label (a phrase) needs 120px or more. \
If several labels would naturally cluster in one area (e.g. multiple parts of the same small \
structure), stack them vertically instead — each on its own line, at least 16px of y apart — \
rather than placing them side by side at the same height. When unsure whether two labels have \
enough room, give them more room, not less.

Be internally consistent: don't draw an arrow depicting a quantity at the same point where a \
label says that quantity is zero or absent (e.g. if you label a point "vy = 0", don't also draw \
a vertical velocity arrow there — the correct diagram shows NO vertical arrow at that point, \
only the horizontal one). Every arrow and every label must agree with each other and with the \
real physical/scientific facts of what's being illustrated, not just look plausible in isolation.

Example 1 — prompt "the water cycle":
[
  {{"type": "ellipse", "x": 80, "y": 220, "rx": 60, "ry": 30}},
  {{"type": "arrow", "x1": 80, "y1": 190, "x2": 150, "y2": 90}},
  {{"type": "ellipse", "x": 200, "y": 60, "rx": 45, "ry": 25}},
  {{"type": "arrow", "x1": 200, "y1": 85, "x2": 260, "y2": 180}},
  {{"type": "text", "x": 50, "y": 260, "text": "Ocean"}},
  {{"type": "text", "x": 170, "y": 40, "text": "Cloud"}},
  {{"type": "text", "x": 230, "y": 200, "text": "Rain"}}
]

Example 2 — prompt "a plant cell's nucleus and cell wall":
[
  {{"type": "rect", "x": 30, "y": 30, "w": 340, "h": 240}},
  {{"type": "ellipse", "x": 200, "y": 150, "rx": 60, "ry": 45}},
  {{"type": "text", "x": 20, "y": 20, "text": "Cell wall"}},
  {{"type": "text", "x": 175, "y": 145, "text": "Nucleus"}}
]

Example 3 — prompt "a simple electric circuit with a battery and a bulb":
[
  {{"type": "rect", "x": 40, "y": 40, "w": 50, "h": 90}},
  {{"type": "ellipse", "x": 300, "y": 85, "rx": 35, "ry": 35}},
  {{"type": "line", "points": [[65, 130], [65, 220], [300, 220], [300, 120]]}},
  {{"type": "line", "points": [[65, 40], [300, 40], [300, 50]]}},
  {{"type": "text", "x": 30, "y": 145, "text": "Battery"}},
  {{"type": "text", "x": 280, "y": 130, "text": "Bulb"}}
]
"""


def _strip_code_fences(text: str) -> str:
    return re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def _is_valid_element(element: dict) -> bool:
    """
    Loosely validates one element's shape is sane enough to hand to the
    frontend renderer. Deliberately not strict about numeric ranges beyond
    a basic type check — a slightly-out-of-bounds coordinate just draws
    slightly off-canvas rather than being worth failing the whole scene
    over; only structurally broken elements (wrong/missing fields, an
    unrecognized "type") are dropped.
    """
    if not isinstance(element, dict):
        return False
    element_type = element.get("type")
    if element_type not in _VALID_TYPES:
        return False
    if element_type == "rect":
        return all(isinstance(element.get(k), (int, float)) for k in ("x", "y", "w", "h"))
    if element_type == "ellipse":
        return all(isinstance(element.get(k), (int, float)) for k in ("x", "y", "rx", "ry"))
    if element_type == "line":
        points = element.get("points")
        return isinstance(points, list) and len(points) >= 2 and all(
            isinstance(p, list) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p) for p in points
        )
    if element_type == "arrow":
        return all(isinstance(element.get(k), (int, float)) for k in ("x1", "y1", "x2", "y2"))
    if element_type == "text":
        return (
            isinstance(element.get("x"), (int, float))
            and isinstance(element.get("y"), (int, float))
            and isinstance(element.get("text"), str)
            and bool(element.get("text").strip())
        )
    return False


def _parse_scene(raw_text: str) -> list[dict] | None:
    text = _strip_code_fences(raw_text)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(parsed, list) or not parsed:
        return None

    # Drop individually-broken elements rather than failing the whole scene
    # for one bad element (a model occasionally emits one malformed shape
    # in an otherwise-good scene) — but if EVERYTHING was broken, that's
    # the same as a parse failure, so fall back to None like any other
    # unusable response.
    valid_elements = [element for element in parsed if _is_valid_element(element)]
    if not valid_elements:
        return None

    return valid_elements[:_MAX_ELEMENTS]


async def generate_sketch_scene(prompt: str) -> tuple[list[dict], LLMResult] | tuple[None, None]:
    """
    Generates a simple labeled hand-drawn-style diagram (as a list of
    drawing primitives, see the schema in _SYSTEM_PROMPT) matching `prompt`,
    for the frontend to render/animate with rough.js. Returns
    (scene, llm_result) on success so the caller can bill actual token
    usage (mirrors quiz_service.generate_quiz_questions's tuple-return
    shape), or (None, None) on any parse/generation failure (mirrors
    image_client.generate_image's None-on-failure convention) — callers
    must treat that as "nothing to render", not an error.
    """
    result = await call_llm(
        system_prompt=_SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}], model=SKETCH_MODEL,
    )

    scene = _parse_scene(result.text)
    if scene is None:
        logger.warning("generate_sketch_scene: could not parse a usable scene for prompt=%r", prompt)
        return None, None

    return scene, result
