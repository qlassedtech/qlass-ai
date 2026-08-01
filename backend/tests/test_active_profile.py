import asyncio

from app.services.active_profile import build_disambiguation_prompt, classify_profile_routing
from app.services.llm_client import LLMResult


def test_build_disambiguation_prompt_two_names():
    prompt = build_disambiguation_prompt(["Priya", "Raj"])
    assert "Priya or Raj" in prompt


def test_build_disambiguation_prompt_three_names():
    prompt = build_disambiguation_prompt(["Priya", "Raj", "Amit"])
    assert "Priya, Raj or Amit" in prompt


def test_classify_profile_routing_detects_switch_target(monkeypatch):
    async def fake_classify(system_prompt, messages, fallback, model, max_tokens=10):
        return LLMResult(text='[[ROUTE new_profile=no switch_target="Raj" name_match=NONE]]', model=model)

    monkeypatch.setattr("app.services.active_profile.classify", fake_classify)

    routing = asyncio.run(classify_profile_routing("switch to Raj", ["Priya", "Raj"]))

    assert routing.new_profile_request is False
    assert routing.switch_target == "Raj"
    assert routing.name_match is None


def test_classify_profile_routing_detects_new_profile_request(monkeypatch):
    async def fake_classify(system_prompt, messages, fallback, model, max_tokens=10):
        return LLMResult(text='[[ROUTE new_profile=yes switch_target=NONE name_match=NONE]]', model=model)

    monkeypatch.setattr("app.services.active_profile.classify", fake_classify)

    routing = asyncio.run(classify_profile_routing("this is for my other son", ["Priya"]))

    assert routing.new_profile_request is True


def test_classify_profile_routing_name_match_only_resolves_known_names(monkeypatch):
    # Regression guard: even if the model hallucinates or mis-cases a name,
    # name_match must never resolve to anything outside the known profiles
    # on this phone — that's the hard guarantee against silently switching
    # the active profile to nobody (or the wrong record).
    async def fake_classify(system_prompt, messages, fallback, model, max_tokens=10):
        return LLMResult(text='[[ROUTE new_profile=no switch_target=NONE name_match="raj"]]', model=model)

    monkeypatch.setattr("app.services.active_profile.classify", fake_classify)

    routing = asyncio.run(classify_profile_routing("hi it's raj here", ["Priya", "Raj"]))

    assert routing.name_match == "Raj"  # resolved to the correctly-cased known name


def test_classify_profile_routing_rejects_unknown_name_match(monkeypatch):
    async def fake_classify(system_prompt, messages, fallback, model, max_tokens=10):
        return LLMResult(text='[[ROUTE new_profile=no switch_target=NONE name_match="Timmy"]]', model=model)

    monkeypatch.setattr("app.services.active_profile.classify", fake_classify)

    routing = asyncio.run(classify_profile_routing("some message", ["Priya", "Raj"]))

    assert routing.name_match is None


def test_classify_profile_routing_returns_none_for_single_profile_phone_with_no_names():
    # No LLM call at all when there are no known names to route against.
    routing = asyncio.run(classify_profile_routing("what is gravity", []))
    assert routing.new_profile_request is False
    assert routing.switch_target is None
    assert routing.name_match is None
