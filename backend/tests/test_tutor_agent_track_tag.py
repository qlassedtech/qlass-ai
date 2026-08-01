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
