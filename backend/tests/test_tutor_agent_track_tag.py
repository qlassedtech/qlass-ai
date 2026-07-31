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
