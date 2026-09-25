from app.agents.tutor_agent import parse_track_reply as _parsed


def test_track_tag_stripped_regardless_of_field_order():
    # The model doesn't reliably keep fields in the documented order —
    # off_level landing after solved (rather than before video) instead of
    # the documented position previously made the strict regex fail to
    # match, leaving the raw tag visible to the student.
    raw = (
        'All done!\n[[TRACK topic="net force" evaluated=true correct=true '
        'image=false audio=true video=true solved=na off_level=true]]'
    )
    result = _parsed(raw)
    assert "[[TRACK" not in result["reply"]
    assert result["topic"] == "net force"
    assert result["evaluated"] is True
    assert result["correct"] is True


def test_track_tag_stripped_when_off_level_field_is_missing_entirely():
    # off_level is often omitted altogether rather than written as
    # "off_level=false" — must still parse and strip cleanly, not fall
    # back to the "no tag found" path that leaves the raw tag visible.
    raw = (
        'Nice to meet you!\n[[TRACK topic="Irodov mechanics" evaluated=false '
        'correct=null image=false audio=false video=true solved=na]]'
    )
    result = _parsed(raw)
    assert "[[TRACK" not in result["reply"]
    assert result["topic"] == "Irodov mechanics"
    assert result["evaluated"] is False
    assert result["correct"] is None


def test_track_tag_missing_entirely_falls_back_gracefully():
    raw = "Just a plain reply with no tag at all."
    result = _parsed(raw)
    assert result["reply"] == raw
    assert result["topic"] is None
    assert result["evaluated"] is False
    assert result["class_confirm"] is None


def test_class_confirm_yes_is_parsed():
    # The LLM reads the whole message (even mixed content like "12.. no")
    # and reports its own yes/no reading directly — this replaces a local
    # regex heuristic that used to discard real content sitting alongside
    # the confirmation (see app.routers.whatsapp's pending_class_confirm).
    raw = (
        'Sure, updating your class!\n[[TRACK topic="classes" evaluated=false '
        'correct=null image=false audio=false off_level=false video=false '
        'solved=na class_confirm=yes]]'
    )
    result = _parsed(raw)
    assert result["class_confirm"] is True


def test_class_confirm_no_is_parsed():
    raw = (
        'No worries, leaving your class as is!\n[[TRACK topic="classes" '
        'evaluated=false correct=null image=false audio=false off_level=false '
        'video=false solved=na class_confirm=no]]'
    )
    result = _parsed(raw)
    assert result["class_confirm"] is False


def test_class_confirm_na_is_parsed_as_none():
    raw = (
        'Sure, here is the next question.\n[[TRACK topic="fractions" '
        'evaluated=false correct=null image=false audio=false off_level=false '
        'video=false solved=na class_confirm=na]]'
    )
    result = _parsed(raw)
    assert result["class_confirm"] is None


def test_profile_answer_extracted_from_track_tag():
    # Replaces the old extract_profile_answer regex — the LLM reads the
    # whole message itself (even "I don't know. Nikhil") and reports the
    # clean extracted value directly, instead of a local sentence-splitting
    # heuristic that used to silently discard real academic content
    # sitting alongside the answer.
    raw = (
        'Nice to meet you, Nikhil! Let\'s get back to your question.\n'
        '[[TRACK topic="algebra" evaluated=false correct=null image=false audio=false '
        'off_level=false video=false solved=na class_confirm=na profile_answer="Nikhil"]]'
    )
    result = _parsed(raw)
    assert result["profile_answer"] == "Nikhil"


def test_profile_answer_none_when_not_addressed():
    raw = (
        'Sure, here\'s the next step.\n[[TRACK topic="algebra" evaluated=false correct=null '
        'image=false audio=false off_level=false video=false solved=na class_confirm=na '
        'profile_answer=NONE]]'
    )
    result = _parsed(raw)
    assert result["profile_answer"] is None


def test_profile_answer_missing_entirely_falls_back_to_none():
    raw = "Just a plain reply with no tag at all."
    result = _parsed(raw)
    assert result["profile_answer"] is None


def test_mindmap_tag_extracted_and_stripped_without_image_prompt():
    raw = (
        "Here's a mind map of photosynthesis: it needs sunlight, water and CO2...\n"
        "[[MINDMAP: photosynthesis]]\n"
        '[[TRACK topic="photosynthesis" evaluated=false correct=null image=true audio=false '
        "off_level=false video=false solved=na class_confirm=na profile_answer=NONE closing=false]]"
    )
    result = _parsed(raw)
    assert result["mindmap_topic"] == "photosynthesis"
    assert result["image_prompt"] is None
    assert "[[MINDMAP" not in result["reply"] and "[[TRACK" not in result["reply"]
    assert result["reply"].startswith("Here's a mind map")


def test_mindmap_tag_wins_if_model_emits_both_tags():
    raw = (
        "Sure!\n[[IMAGE_PROMPT: a diagram of photosynthesis]]\n[[MINDMAP: photosynthesis]]\n"
        '[[TRACK topic="photosynthesis" evaluated=false correct=null image=true audio=false video=false solved=na]]'
    )
    result = _parsed(raw)
    assert result["mindmap_topic"] == "photosynthesis"
    assert result["image_prompt"] is None  # never both
    assert "[[IMAGE_PROMPT" not in result["reply"] and "[[MINDMAP" not in result["reply"]


def test_mindmap_tag_ignored_when_image_is_false():
    raw = (
        "Sure!\n[[MINDMAP: photosynthesis]]\n"
        '[[TRACK topic="photosynthesis" evaluated=false correct=null image=false audio=false video=false solved=na]]'
    )
    result = _parsed(raw)
    assert result["mindmap_topic"] is None
    assert "[[MINDMAP" not in result["reply"]


def test_image_prompt_still_works_without_mindmap_tag():
    raw = (
        "Here's a plant cell.\n[[IMAGE_PROMPT: a labeled diagram of a plant cell]]\n"
        '[[TRACK topic="plant cell" evaluated=false correct=null image=true audio=false video=false solved=na]]'
    )
    result = _parsed(raw)
    assert result["image_prompt"] == "a labeled diagram of a plant cell"
    assert result["mindmap_topic"] is None


def test_mindmap_tag_stripped_even_without_track_tag():
    result = _parsed("Reply text.\n[[MINDMAP: gravity]]")
    assert result["reply"] == "Reply text."
    assert result["mindmap_topic"] is None


def test_prompt_routes_mind_maps_to_mindmap_tag_not_image_prompt():
    from app.agents.tutor_agent import TutorAgent

    static, _dynamic = TutorAgent().build_context({"class": "8", "board": "CBSE"}, [], [], image_generation_enabled=True)
    assert "[[MINDMAP:" in static
    assert "Never emit both an IMAGE_PROMPT line and a MINDMAP line" in static
    # diagrams/pictures still go through IMAGE_PROMPT
    assert "[[IMAGE_PROMPT:" in static
