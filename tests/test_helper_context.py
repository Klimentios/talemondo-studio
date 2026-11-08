import json

from local_studio.helper_context import build_check_key, load_check, parse_helper_response, save_check


def key(**overrides):
    args = dict(franchise_id="f1", role="continuity", task="Check contact", provider="codex",
                evidence_hash="a" * 64, skill_version="skill-1", model_identity="model-2026-10-01",
                model_pinned=True)
    return build_check_key(**{**args, **overrides})


def test_check_key_requires_evidence_and_pinned_identity_and_tracks_dependencies():
    assert key() == key()
    for field, value in (("franchise_id", "f2"), ("role", "acting"), ("task", "Check eyes"),
                         ("provider", "claude"), ("evidence_hash", "b" * 64),
                         ("skill_version", "skill-2"), ("model_identity", "model-2")):
        assert key(**{field: value}) != key()
    for missing in (dict(model_pinned=False), dict(evidence_hash=""), dict(skill_version=""),
                    dict(model_identity="default"), dict(franchise_id="")):
        assert key(**missing) is None


def test_only_structured_reports_are_cached_and_corruption_is_a_miss(tmp_path):
    plain = parse_helper_response("Looks good, but inspect the full clip.")
    assert plain == {"structured": False, "text": "Looks good, but inspect the full clip."}
    assert save_check(tmp_path, key(), plain) is False
    raw = {"verdict": "uncertain", "findings": ["Contact hidden"], "evidence": [{"time": 2}],
           "uncertainties": ["Occlusion"], "suggested_fix": "Inspect the following frame"}
    report = parse_helper_response("```json\n" + json.dumps(raw) + "\n```")
    assert save_check(tmp_path, key(), report)
    assert load_check(tmp_path, key()) == report
    (tmp_path / "helper_checks" / f"{key()}.json").write_text("broken")
    assert load_check(tmp_path, key()) is None
    assert load_check(tmp_path, "../secret") is None
    assert save_check(tmp_path, None, report) is False


def test_invalid_structured_response_remains_uncacheable():
    assert not parse_helper_response('{"verdict":"pass"}')["structured"]
