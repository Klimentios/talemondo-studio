import asyncio

from local_studio import scene_frames


def test_frame_review_checks_across_the_cut_only_with_the_previous_frame(tmp_path, monkeypatch):
    seen = []

    async def fake_ask(request):
        seen.append(request)
        return {"data": {"accept": True, "problems": ""}}

    monkeypatch.setattr(scene_frames, "ask", fake_ask)
    frame, face, before = (tmp_path / name for name in ("frame.png", "vega.png", "before.png"))
    for path in (frame, face, before):
        path.write_bytes(b"x")
    refs = [{"role": "Лицо героя", "owner": "VEGA"}]
    asyncio.run(scene_frames.review(frame, "VEGA on the floor, screen left.", refs, [face]))
    asyncio.run(scene_frames.review(frame, "VEGA on the floor, screen left.", refs, [face],
                                    previous={"frame": before, "text": "ADAM on the sofa, screen right, looks left."}))
    alone, across = seen
    assert "180°" not in alone.prompt and str(before) not in alone.images
    assert "180°" in across.prompt and "ADAM on the sofa" in across.prompt and across.images[-1] == str(before)


def test_a_missing_previous_frame_is_an_error_not_a_silent_pass(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"x")
    try:
        asyncio.run(scene_frames.review(frame, "x", [], [], previous={"frame": tmp_path / "gone.png", "text": ""}))
    except ValueError as error:
        assert "предыдущего" in str(error)
    else:
        raise AssertionError("missing previous frame passed")


def test_a_source_still_without_the_franchise_place_is_refused(tmp_path):
    """Keyframes drawn from source stills alone copy the source room."""
    still, vega, flat = (tmp_path / name for name in ("still.png", "vega.png", "flat.png"))
    for path in (still, vega, flat):
        path.write_bytes(b"x")
    refs = [{"role": "Композиция кадра"}, {"role": "Персонаж целиком", "owner": "VEGA"}]
    try:
        scene_frames.validate_inputs(refs, [still, vega])
    except ValueError as exc:
        assert "Окружение" in str(exc)
    else:
        raise AssertionError("source still without an environment picture must be refused")
    scene_frames.validate_inputs(refs + [{"role": "Окружение"}], [still, vega, flat])
    assert "never copy its room" in scene_frames.DRAW_RULES
