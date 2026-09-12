import json
import logging
import re

from app.services.llm_client import LLMResult, call_llm

logger = logging.getLogger(__name__)

# Sonnet tier, not Haiku — this module makes two calls per diagram (see
# generate_sketch_scene's docstring): a first pass that generates the scene,
# and a second self-critique pass that checks it for internal/domain
# consistency (the "vy=0 point with a vertical arrow drawn on it" class of
# bug — see _CRITIQUE_SYSTEM_PROMPT). Both need real reasoning, not just
# structured-output compliance, so this uses the same "good" tier as the
# tutor's own top level (see business_rules.TUTOR_LEVEL_MODELS[4]) rather
# than the cheap auxiliary tier chat_core.NOTES_MODEL uses — the added cost
# for two short JSON round-trips is small next to the reliability win
# across every subject (see this module's own docstring for a real number).
SKETCH_MODEL = "claude-sonnet-4-6"

_VALID_TYPES = {"rect", "ellipse", "line", "arrow", "text"}

_MAX_ELEMENTS = 20
_CANVAS_W = 400
_CANVAS_H = 300

# --- Text-collision auto-repair parameters -----------------------------
#
# The frontend renders text with `ctx.font = "14px sans-serif"` (see
# Call.tsx's drawDiagramElement) and Python has no access to that canvas's
# real `measureText`, so this is a fixed-width APPROXIMATION of a 14px
# sans-serif glyph, not an exact measurement — real sans-serif fonts are
# proportional (an "i" is much narrower than an "M"), so this will
# over-estimate narrow-character labels and under-estimate wide-character
# ones. It's deliberately a bit generous (biased toward over-estimating)
# since a slightly-too-large estimated box just pushes a label a little
# further than strictly necessary, which is harmless, whereas
# under-estimating risks leaving a real overlap unresolved.
_CHAR_WIDTH_PX = 7.5
_TEXT_HEIGHT_PX = 16
# Vertical extent of the estimated box relative to the (x, y) the element
# specifies — canvas fillText's default baseline is "alphabetic", so most
# of a line of text sits ABOVE y (ascenders), with a small allowance below
# for descenders (g, y, p, ...).
_TEXT_ABOVE_BASELINE_PX = 12
_TEXT_BELOW_BASELINE_PX = 4
_COLLISION_STEP_PX = 18  # a reasonable line-height step for 14px text
_MAX_COLLISION_ATTEMPTS = 15  # search budget, shared between the down/up directions below

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


def _text_box(element: dict) -> tuple[float, float, float, float]:
    """
    Estimated (x1, y1, x2, y2) bounding box for a "text" element, using the
    fixed-width approximation documented above _CHAR_WIDTH_PX. This is not
    an exact measurement (see that comment) — it's good enough to catch
    and fix the overwhelming majority of real collisions, not a geometric
    guarantee of zero false negatives/positives on every possible label.
    """
    x, y, text = element["x"], element["y"], element["text"]
    width = len(text) * _CHAR_WIDTH_PX
    return x, y - _TEXT_ABOVE_BASELINE_PX, x + width, y + _TEXT_BELOW_BASELINE_PX


def _boxes_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2


def _overlap_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    dx = min(ax2, bx2) - max(ax1, bx1)
    dy = min(ay2, by2) - max(ay1, by1)
    return max(dx, 0.0) * max(dy, 0.0)


def _resolve_text_collisions(elements: list[dict]) -> list[dict]:
    """
    Deterministic, pure post-process that guarantees (up to the
    fixed-width text-box approximation documented above) no two "text"
    elements' estimated boxes overlap — a geometric property no amount of
    prompting the model to "leave enough space" can actually guarantee,
    since the model has no real measurement of its own output either.

    Walks text elements IN ARRAY ORDER. Earlier text elements are never
    moved by a later one — later elements are drawn/animated last (see
    Call.tsx) and are what the model most recently decided needed
    labeling, so on a genuine conflict the later label is the one that
    moves. Each text element is checked only against EARLIER elements'
    already-resolved boxes.

    On a collision, the element is pushed straight down in
    _COLLISION_STEP_PX increments (matching a reasonable line-height for
    14px text) until clear of every earlier box, clamped to the 0-
    _CANVAS_H canvas. If pushing down runs out of room (would exceed the
    canvas, or exhausts the shared search budget below), the same search
    is tried pushing UP from the element's ORIGINAL position instead. If
    neither direction fully clears every earlier box within
    _MAX_COLLISION_ATTEMPTS total attempts (shared across both
    directions — this is a bounded search, not an unbounded loop), the
    least-bad position found (smallest total overlap area) is used rather
    than looping forever. A truly pathological scene (many labels forced
    into a tiny area) can therefore still end up with a rare residual
    overlap — the frontend's white-halo rendering under each label (see
    Call.tsx's drawDiagramElement) already degrades that case gracefully,
    same as it always has.

    Non-text elements (rect/ellipse/line/arrow) are passed through
    completely unchanged, including object identity.
    """
    resolved: list[dict] = []
    placed_boxes: list[tuple[float, float, float, float]] = []

    for element in elements:
        if element.get("type") != "text":
            resolved.append(element)
            continue

        original_y = element["y"]
        box = _text_box(element)

        if not any(_boxes_overlap(box, placed) for placed in placed_boxes):
            resolved.append(element)
            placed_boxes.append(box)
            continue

        best_y = original_y
        best_overlap = sum(_overlap_area(box, placed) for placed in placed_boxes)
        found = False
        attempts = 0

        # Push down first — increasing y moves a label further from
        # whatever it collided with above/beside it in most real diagrams
        # (labels tend to sit near the top of the shape they describe).
        down_attempt = 0
        while attempts < _MAX_COLLISION_ATTEMPTS and not found:
            attempts += 1
            down_attempt += 1
            candidate_y = original_y + down_attempt * _COLLISION_STEP_PX
            if candidate_y > _CANVAS_H:
                break
            candidate_box = _text_box({**element, "y": candidate_y})
            overlap = sum(_overlap_area(candidate_box, placed) for placed in placed_boxes)
            if overlap == 0:
                best_y, found = candidate_y, True
            elif overlap < best_overlap:
                best_y, best_overlap = candidate_y, overlap

        # Ran out of downward room without fully clearing — try pushing up
        # from the original position instead, with whatever search budget
        # is left.
        up_attempt = 0
        while attempts < _MAX_COLLISION_ATTEMPTS and not found:
            attempts += 1
            up_attempt += 1
            candidate_y = original_y - up_attempt * _COLLISION_STEP_PX
            if candidate_y < 0:
                break
            candidate_box = _text_box({**element, "y": candidate_y})
            overlap = sum(_overlap_area(candidate_box, placed) for placed in placed_boxes)
            if overlap == 0:
                best_y, found = candidate_y, True
            elif overlap < best_overlap:
                best_y, best_overlap = candidate_y, overlap

        adjusted = {**element, "y": best_y}
        resolved.append(adjusted)
        placed_boxes.append(_text_box(adjusted))
        if not found:
            logger.info(
                "_resolve_text_collisions: could not fully clear a collision for text=%r within the search "
                "budget; using the least-overlap position found (residual overlap area=%.1f)",
                element.get("text"), best_overlap,
            )

    return resolved


_CRITIQUE_SYSTEM_PROMPT = f"""\
You are reviewing a hand-drawn-style vector diagram — a JSON array of drawing primitives on a \
fixed {_CANVAS_W}x{_CANVAS_H} coordinate space — that was just generated for a tutoring request. \
Review it for INTERNAL CONSISTENCY and DOMAIN/FACTUAL correctness. Do NOT review it for style, \
aesthetics, or layout spacing — that is handled separately.

You will be given the original request and the generated scene JSON. Check things like:
- Does any arrow, shape, or position contradict a label at the same location? (e.g. a vertical \
velocity arrow drawn at a point labeled "vy = 0" is wrong — at that point the real vertical \
velocity is actually zero, so no such arrow belongs there. The same kind of contradiction can \
show up in any subject: a current arrow at a point labeled "no current flows here", a "closed" \
label on a valve drawn open, etc.)
- Is each labeled part positioned where it actually belongs relative to the shape(s) it's \
labeling, and not on top of / pointing at some other part instead?
- Does the diagram show the wrong number, direction, or relative position of anything, given \
real, established facts about the subject being illustrated?
- Is the overall diagram a reasonable, recognizable depiction of what was actually asked for?

If the scene is already fine, respond with EXACTLY these two characters and nothing else: OK

If something is actually wrong, respond with ONLY a corrected JSON array in the exact same \
schema as the input (same element types and fields, same {_CANVAS_W}x{_CANVAS_H} canvas). Fix \
ONLY what is actually wrong — do not restyle, do not add unrelated elements, do not change \
anything that was already correct. No markdown code fences, no explanation, nothing else.
"""


async def _critique_and_fix_scene(original_prompt: str, scene: list[dict]) -> tuple[list[dict], LLMResult | None]:
    """
    Second LLM pass: asks the model to review its own (already collision-
    repaired) scene for internal-consistency / domain-correctness bugs —
    the class of problem no amount of geometric post-processing can catch,
    since it's about whether the CONTENT is true, not whether it fits on
    the canvas. See _CRITIQUE_SYSTEM_PROMPT for exactly what's checked.

    Returns (scene_to_use, llm_result). This second call's output is
    trusted no more than the first one's — a literal "OK" keeps the
    original scene, and a correction only replaces it after passing the
    exact same _is_valid_element/_parse_scene-family validation the first
    pass's output goes through; a correction that fails validation (or a
    response that's neither "OK" nor valid JSON) is logged and discarded,
    falling back to the original (already-valid) scene rather than
    risking a broken result. `llm_result` is None only if the critique
    call itself raised (e.g. a transient network error) — that failure
    must not take down a turn that already has a perfectly usable scene
    from the first pass, so it's swallowed here and the caller simply
    doesn't bill for a call that never completed.
    """
    critique_input = (
        f"Original request: {original_prompt}\n\n"
        f"Generated diagram (JSON array of drawing primitives):\n{json.dumps(scene)}"
    )
    try:
        result = await call_llm(
            system_prompt=_CRITIQUE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": critique_input}],
            model=SKETCH_MODEL,
        )
    except Exception:
        logger.exception("_critique_and_fix_scene: critique call failed; keeping the original scene")
        return scene, None

    stripped = _strip_code_fences(result.text)
    if stripped == "OK":
        return scene, result

    corrected = _parse_scene(result.text)
    if corrected is None:
        logger.warning(
            "_critique_and_fix_scene: critique response was neither \"OK\" nor a valid scene; "
            "keeping the original scene. response=%r",
            result.text[:200],
        )
        return scene, result

    return corrected, result


def _combine_llm_results(first: LLMResult, second: LLMResult | None) -> LLMResult:
    """
    generate_sketch_scene now makes two Claude calls (generation +
    critique) but its public return shape is still a single
    (scene, llm_result) tuple — voice_call.py bills once per diagram with
    a single cost_tracker.record_claude_usage call, so this sums both
    calls' token counts into one LLMResult rather than changing that call
    site's shape. `text` on the combined result is meaningless/unused —
    nothing downstream re-parses a "final" LLMResult.text, only its token
    fields are read (by cost_tracker). `model` is taken from `first`;
    both calls always use SKETCH_MODEL so this is never actually ambiguous.
    `second` is None when the critique call itself failed (see
    _critique_and_fix_scene) — in that case only the first call's usage is
    billed, since the second one never completed.
    """
    if second is None:
        return first
    return LLMResult(
        text="",
        model=first.model,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        cache_write_tokens=first.cache_write_tokens + second.cache_write_tokens,
        cache_read_tokens=first.cache_read_tokens + second.cache_read_tokens,
    )


async def generate_sketch_scene(prompt: str) -> tuple[list[dict], LLMResult] | tuple[None, None]:
    """
    Generates a simple labeled hand-drawn-style diagram (as a list of
    drawing primitives, see the schema in _SYSTEM_PROMPT) matching `prompt`,
    for the frontend to render/animate with rough.js. This is now a
    two-LLM-call pipeline, both to guarantee readability and to catch a
    class of bug live testing turned up that pure prompting can't reliably
    prevent:

      1. Generation (_SYSTEM_PROMPT) — same as before, produces the raw
         scene JSON.
      2. Deterministic collision auto-repair (_resolve_text_collisions) —
         pure code, no LLM, GUARANTEES (modulo the fixed-width text-box
         approximation it documents) that no two text labels' estimated
         boxes overlap. This fixes layout bugs like two organelle labels
         visually smearing together — a geometric property an LLM
         instruction alone can't guarantee, since the model has no real
         measurement of its own rendered output.
      3. Self-critique (_critique_and_fix_scene) — a second Claude call
         that reviews the (already collision-repaired) scene for internal-
         consistency / domain-correctness bugs, e.g. a vertical-velocity
         arrow drawn at a point labeled "vy = 0" (a real bug live testing
         found) — a content problem no amount of layout post-processing
         can catch, and one that recurs in different forms across every
         subject, not just physics. Its output is trusted no more than
         the first pass's — see that function's docstring.
      4. The critique's result is run back through
         _resolve_text_collisions too, since a correction could reinstate
         a collision the first repair pass had already fixed.

    Returns (scene, llm_result) on success so the caller can bill actual
    token usage (mirrors quiz_service.generate_quiz_questions's
    tuple-return shape) — `llm_result` now covers BOTH Claude calls
    combined (see _combine_llm_results) so voice_call.py's billing call
    site still only needs to bill once per diagram. Returns (None, None)
    on any parse/generation failure in the FIRST pass (mirrors
    image_client.generate_image's None-on-failure convention) — callers
    must treat that as "nothing to render", not an error. Once a scene
    exists, a critique-side failure never degrades to (None, None) — the
    original valid scene is always what's used at worst.
    """
    generation_result = await call_llm(
        system_prompt=_SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}], model=SKETCH_MODEL,
    )

    scene = _parse_scene(generation_result.text)
    if scene is None:
        logger.warning("generate_sketch_scene: could not parse a usable scene for prompt=%r", prompt)
        return None, None

    scene = _resolve_text_collisions(scene)

    scene, critique_result = await _critique_and_fix_scene(prompt, scene)
    scene = _resolve_text_collisions(scene)

    return scene, _combine_llm_results(generation_result, critique_result)
