"""
Real (Buzan-style) mind maps: one central topic, curved organic branches
radiating out — thick near the centre, thinner outward — ONE colour per
main branch, a single keyword written along each branch, and thinner
sub-branches off each main branch.

This is deliberately NOT another prompt for sketch_client: that module's
schema (labels in ellipses joined by straight lines) has no colour, no
curves and no text-along-branch, so asking it for a "mind map" only ever
produced a concept diagram. Here the LLM decides only the CONTENT (a tiny
tree: centre, branches, children — see generate_mindmap_tree) and all the
geometry is deterministic code (layout_mindmap), so there's no critique
pass and no collision-repair pass: the layout constants below were chosen
so that even the worst-case tree (6 branches x 3 children, every label at
its maximum length) has no two label boxes overlapping and everything
stays on the 400x300 canvas — see tests/test_mindmap.py's worst-case test,
which is what pins these numbers.

Scene schema (shared with the frontend renderer — keep in sync):

  {"type": "ellipse", "x", "y", "rx", "ry", "color", "width", "fill"}
  {"type": "text", "x", "y", "text", "size", "weight", "align", "color"}
      align "center" => the text is centred horizontally AND vertically on
      (x, y); otherwise (x, y) is the left/baseline point, as in
      sketch_client's plain "text" element.
  {"type": "branch", "points": [[sx,sy],[cx,cy],[ex,ey]], "color", "width",
   "label", "level": 1|2, "label_at": "mid"|"end"}
      points are [start, quadratic control, end] of a quadratic Bezier.
      Label placement (renderers MUST do exactly this, the layout relies
      on it): level 1 => label centred at the curve's t=0.5 point,
      baseline 8px above it, bold 13px in the branch colour; level 2 =>
      label centred at the END point, baseline 6px above it, normal 11px
      #333333.

Final layout constants (all in canvas px, centre C=(200,150)):
  centre ellipse rx=60 ry=26; main branch start radius 62, end radius 124;
  quadratic control = chord midpoint pushed 10px perpendicular (downward on
  screen); per-side angles: 1 branch [0], 2 branches [-26, 26], 3 branches
  [-42, 0, 42] degrees; children: horizontal run 26px (control at 13px),
  rows 16px apart, first row 16px from the end point, fanning AWAY from the
  horizontal axis (upward for upward branches, downward for horizontal
  and downward branches). The "fan away" rule is what keeps a branch's own
  level-1 label clear of its children's labels; the 124 end radius keeps
  the two lower 42-degree labels from meeting at the centre line; and the
  42-degree spread for 3-per-side keeps the middle branch's children clear
  of the lower branch's label — all with 16-char (128px) level-1 labels.
  (The original 62/105 radii and [-32, 0, 32] angles failed that test:
  a 128px label centred on a 43px chord overran its own children.)
"""
import json
import logging
import math
import os
import re
from io import BytesIO

from app.services.branding import stamp_logo
from app.services.llm_client import LLMResult, call_llm

logger = logging.getLogger(__name__)

MINDMAP_MODEL = "claude-sonnet-4-6"

CANVAS_W = 400
CANVAS_H = 300
CENTER_X = 200
CENTER_Y = 150

CENTER_RX = 60
CENTER_RY = 26
CENTER_STROKE = "#23252b"
CENTER_FILL = "#f3efe6"

MAIN_START_RADIUS = 62
MAIN_END_RADIUS = 124
MAIN_CURVE_PUSH = 10  # perpendicular push of the quadratic control point, px
MAIN_WIDTH = 4
CHILD_WIDTH = 2
CHILD_RUN = 26  # horizontal length of a level-2 branch, px
CHILD_ROW_GAP = 16
CHILD_ROW_OFFSET = 16  # distance of the first child row from the main branch's end point

# Angles (degrees, measured from +x, y grows downward so negative = up)
# for the RIGHT side, indexed by how many branches that side holds. The
# left side mirrors them (180 - theta).
SIDE_ANGLES = {1: [0], 2: [-26, 26], 3: [-42, 0, 42]}

PALETTE = ["#d3543a", "#2f7dd1", "#2f9e5b", "#d9962b", "#8657c2", "#1f9aa8"]

MAX_BRANCHES = 6
MIN_BRANCHES = 3
MAX_CHILDREN = 3
MAX_CENTER_CHARS = 24
MAX_BRANCH_CHARS = 16
MAX_CHILD_CHARS = 14

# Label rendering rules (also documented in the module docstring — the
# frontend renderer implements the same numbers).
LEVEL1_LABEL_SIZE = 13
LEVEL1_LABEL_LIFT = 18
LEVEL2_LABEL_SIZE = 11
LEVEL2_LABEL_LIFT = 13
LEVEL2_LABEL_COLOR = "#333333"

_SYSTEM_PROMPT = (
    "You design the content of a student's mind map. Reply with ONLY a JSON object, no code "
    "fences, no prose:\n"
    '{"center": "<topic, at most 3 words>", "branches": [{"label": "<at most 2 words>", '
    '"children": ["<at most 2 words>", ...]}, ...]}\n'
    "Rules: 3-6 branches, each with 0-3 children. Every label is a short KEYWORD or key phrase "
    "(never a sentence), factually correct and at school-textbook level. Branches are the "
    "main sub-topics of the centre topic; children are the key facts/examples under each branch. "
    "Keep labels short enough to write along a line."
)


def _strip_code_fences(text: str) -> str:
    return re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()


def _clean_label(value, max_chars: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) <= max_chars:
        return cleaned
    # Cut at the last whole word rather than mid-word — a hard character
    # slice produced garbled labels like "Cloud formatio" / "Falls to
    # groun" (confirmed on a real generated map), which is worse than a
    # shorter-but-clean label.
    truncated = cleaned[:max_chars].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated


def parse_mindmap_tree(raw_text: str) -> dict | None:
    """
    Defensive parse of the LLM's JSON: strips fences, validates shapes,
    clamps branch/child counts and truncates labels. Returns None for
    anything unusable (non-JSON, wrong shapes, fewer than MIN_BRANCHES
    usable branches).
    """
    try:
        parsed = json.loads(_strip_code_fences(raw_text))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None

    center = _clean_label(parsed.get("center"), MAX_CENTER_CHARS)
    if not center:
        return None

    raw_branches = parsed.get("branches")
    if not isinstance(raw_branches, list):
        return None

    branches: list[dict] = []
    for raw in raw_branches:
        if not isinstance(raw, dict):
            continue
        label = _clean_label(raw.get("label"), MAX_BRANCH_CHARS)
        if not label:
            continue
        raw_children = raw.get("children")
        children: list[str] = []
        if isinstance(raw_children, list):
            for child in raw_children:
                cleaned = _clean_label(child, MAX_CHILD_CHARS)
                if cleaned:
                    children.append(cleaned)
        branches.append({"label": label, "children": children[:MAX_CHILDREN]})
        if len(branches) == MAX_BRANCHES:
            break

    if len(branches) < MIN_BRANCHES:
        return None
    return {"center": center, "branches": branches}


async def generate_mindmap_tree(topic: str) -> tuple[dict | None, LLMResult | None]:
    """
    One Claude call that decides the mind map's CONTENT only (see the
    module docstring). Returns (None, None) on any parse failure, like
    sketch_client.generate_sketch_scene — callers treat that as "nothing
    to render", not an error.
    """
    result = await call_llm(
        system_prompt=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Mind map topic: {topic}"}],
        model=MINDMAP_MODEL,
    )
    tree = parse_mindmap_tree(result.text)
    if tree is None:
        logger.warning("generate_mindmap_tree: could not parse a usable tree for topic=%r", topic)
        return None, None
    return tree, result


def _round_point(x: float, y: float) -> list[int]:
    px, py = int(round(x)), int(round(y))
    assert 0 <= px <= CANVAS_W and 0 <= py <= CANVAS_H, f"point ({px}, {py}) is off the canvas"
    return [px, py]


def _main_branch_points(theta_deg: float) -> tuple[list[int], list[int], list[int]]:
    theta = math.radians(theta_deg)
    dx, dy = math.cos(theta), math.sin(theta)
    sx, sy = CENTER_X + MAIN_START_RADIUS * dx, CENTER_Y + MAIN_START_RADIUS * dy
    ex, ey = CENTER_X + MAIN_END_RADIUS * dx, CENTER_Y + MAIN_END_RADIUS * dy
    # Perpendicular to the chord, pointing DOWN on screen (positive y).
    px, py = -dy, dx
    if py < 0:
        px, py = -px, -py
    cx, cy = (sx + ex) / 2 + MAIN_CURVE_PUSH * px, (sy + ey) / 2 + MAIN_CURVE_PUSH * py
    return _round_point(sx, sy), _round_point(cx, cy), _round_point(ex, ey)


def _child_rows(n: int, theta_deg: float) -> list[float]:
    """dy of each child row relative to the main branch's end point —
    fanning away from the horizontal axis (see module docstring)."""
    sign = -1 if theta_deg < 0 else 1
    return [sign * (CHILD_ROW_OFFSET + j * CHILD_ROW_GAP) for j in range(n)]


def layout_mindmap(tree: dict) -> list[dict]:
    """Deterministic geometry: tree (see parse_mindmap_tree) -> scene elements."""
    center = tree["center"]
    branches = tree["branches"]
    scene: list[dict] = [
        {
            "type": "ellipse", "x": CENTER_X, "y": CENTER_Y, "rx": CENTER_RX, "ry": CENTER_RY,
            "color": CENTER_STROKE, "width": 2.5, "fill": CENTER_FILL,
        },
        {
            "type": "text", "x": CENTER_X, "y": CENTER_Y, "text": center,
            "size": 14 if len(center) <= 16 else 12, "weight": "bold", "align": "center", "color": CENTER_STROKE,
        },
    ]

    # Alternate right/left so both halves fill evenly.
    sides: dict[str, list[tuple[int, dict]]] = {"right": [], "left": []}
    for i, branch in enumerate(branches):
        sides["right" if i % 2 == 0 else "left"].append((i, branch))

    for side, members in sides.items():
        if not members:
            continue
        angles = SIDE_ANGLES[len(members)]
        direction = 1 if side == "right" else -1
        for (index, branch), theta in zip(members, angles):
            color = PALETTE[index % len(PALETTE)]
            screen_theta = theta if side == "right" else 180 - theta
            start, control, end = _main_branch_points(screen_theta)
            scene.append({
                "type": "branch", "points": [start, control, end], "color": color, "width": MAIN_WIDTH,
                "label": branch["label"], "level": 1, "label_at": "mid",
            })
            ex, ey = end
            for child, dy in zip(branch["children"], _child_rows(len(branch["children"]), theta)):
                scene.append({
                    "type": "branch",
                    "points": [
                        [ex, ey],
                        _round_point(ex + direction * CHILD_RUN / 2, ey + dy * 0.5),
                        _round_point(ex + direction * CHILD_RUN, ey + dy),
                    ],
                    "color": color, "width": CHILD_WIDTH, "label": child, "level": 2, "label_at": "end",
                })
    return scene


def quadratic_point(points: list, t: float) -> tuple[float, float]:
    (x0, y0), (x1, y1), (x2, y2) = points
    u = 1 - t
    return (u * u * x0 + 2 * u * t * x1 + t * t * x2, u * u * y0 + 2 * u * t * y1 + t * t * y2)


def branch_label_anchor(element: dict) -> tuple[float, float, int, str, str]:
    """(x, y_baseline, size, weight, color) of a branch label per the rules
    in the module docstring — shared by the PNG renderer and the tests.

    The y is clamped so the label's own box (baseline - size .. baseline)
    never runs off the top of the canvas — the worst case (a steep-angle
    branch with a max-length label, see test_worst_case_labels_...) lifts
    the label further than there's headroom for; clamping trades a touch
    less clearance from the branch curve in that rare case for a label
    that's still fully on-canvas, which is strictly better."""
    if element["level"] == 1:
        mx, my = quadratic_point(element["points"], 0.5)
        x, y, size, weight, color = mx, my - LEVEL1_LABEL_LIFT, LEVEL1_LABEL_SIZE, "bold", element["color"]
    else:
        ex, ey = element["points"][2]
        x, y, size, weight, color = ex, ey - LEVEL2_LABEL_LIFT, LEVEL2_LABEL_SIZE, "normal", LEVEL2_LABEL_COLOR
    y = max(y, size)
    return x, y, size, weight, color


async def generate_mindmap_scene(topic: str) -> tuple[list[dict] | None, LLMResult | None]:
    tree, result = await generate_mindmap_tree(topic)
    if tree is None:
        return None, None
    return layout_mindmap(tree), result


# --- PNG rendering (WhatsApp / student portal delivery) -----------------


def _font_path(bold: bool) -> str:
    import reportlab

    return os.path.join(os.path.dirname(reportlab.__file__), "fonts", "VeraBd.ttf" if bold else "Vera.ttf")


def render_mindmap_png(scene: list[dict], scale: int = 3) -> bytes:
    """
    Rasterises a scene (see layout_mindmap) to a (CANVAS_W*scale) x
    (CANVAS_H*scale) PNG — 1200x900 at the default scale — with the
    Skoolgpt logo stamped bottom-right. Uses reportlab's bundled Vera TTFs
    (never Pillow's tiny default bitmap font) so the text is legible at
    phone size.
    """
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (CANVAS_W * scale, CANVAS_H * scale), "white")
    draw = ImageDraw.Draw(image)
    fonts: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}

    def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
        key = (bold, size)
        if key not in fonts:
            fonts[key] = ImageFont.truetype(_font_path(bold), size * scale)
        return fonts[key]

    def s(value: float) -> float:
        return value * scale

    # Shapes and branches first, every label after, so no line ever crosses text.
    labels: list[tuple[float, float, str, int, str, str, str]] = []
    for element in scene:
        kind = element.get("type")
        if kind == "ellipse":
            x, y, rx, ry = element["x"], element["y"], element["rx"], element["ry"]
            draw.ellipse(
                [s(x - rx), s(y - ry), s(x + rx), s(y + ry)],
                fill=element.get("fill"), outline=element.get("color", "#23252b"),
                width=max(1, int(round(s(element.get("width", 2))))),
            )
        elif kind == "branch":
            samples = [quadratic_point(element["points"], i / 24) for i in range(25)]
            draw.line(
                [(s(x), s(y)) for x, y in samples], fill=element.get("color", "#23252b"),
                width=max(1, int(round(s(element.get("width", 2))))), joint="curve",
            )
            x, y, size, weight, color = branch_label_anchor(element)
            labels.append((x, y, element.get("label") or "", size, weight, color, "center_baseline"))
        elif kind == "text":
            align = "center_middle" if element.get("align") == "center" else "left_baseline"
            labels.append((
                element["x"], element["y"], element["text"], int(element.get("size", 14)),
                element.get("weight", "normal"), element.get("color", "#23252b"), align,
            ))

    anchors = {"center_baseline": "ms", "center_middle": "mm", "left_baseline": "ls"}
    for x, y, text, size, weight, color, align in labels:
        if not text:
            continue
        draw.text((s(x), s(y)), text, fill=color, font=font(weight == "bold", size), anchor=anchors[align])

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    # A bit smaller than the default stamp: a lower branch's last child
    # label can reach the bottom-right corner of this canvas, and a 14%-wide
    # logo would sit on top of it.
    return stamp_logo(buffer.getvalue(), width_ratio=0.10, margin_ratio=0.015)


async def build_mindmap_image(db, student_id: int, topic: str) -> bytes | None:
    """
    The whole WhatsApp/portal delivery pipeline for one mind map: tree ->
    layout -> PNG, billing the one Claude call to the student's wallet
    (feature="mindmap_generate") and logging a $0 "mindmap_image" event so
    the map counts toward the weekly image quota exactly like a generated
    diagram (see business_rules.FEATURE_LIMITS["image_generation"]).
    Returns None on any failure — callers degrade to the text reply, same
    as a failed image_client.generate_image.
    """
    from app.services import cost_tracker

    try:
        scene, result = await generate_mindmap_scene(topic)
        if scene is None:
            return None
        if result is not None:
            cost_tracker.record_claude_usage(
                db, result.model, result.input_tokens, result.output_tokens, student_id,
                cache_write_tokens=result.cache_write_tokens, cache_read_tokens=result.cache_read_tokens,
                feature="mindmap_generate",
            )
        png = render_mindmap_png(scene)
        cost_tracker.record_free_call(db, "mindmap_image", student_id)
        return png
    except Exception:
        logger.exception("build_mindmap_image: failed for topic=%r", topic)
        return None
