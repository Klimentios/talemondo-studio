from local_studio.side_jobs import side_job_summary


class FakeQueue:
    def __init__(self, rows):
        self.rows = rows

    def list(self):
        return self.rows


def test_queue_page_sees_side_jobs_on_the_gpu():
    # Stand e2e 2026-10-08: «Очередь» said «0 в работе» while an upscale held the GPU.
    summary = side_job_summary({
        "upscale": FakeQueue([{"upscale_id": "u1", "source_job_id": "j1", "mode": "upscale", "state": "running",
                               "error": None, "created_at": "2026-10-08 14:23:16"},
                              {"upscale_id": "k1", "state": "queued_local", "created_at": "2026-10-08 10:00:00"}]),
        "audio": FakeQueue([{"audio_id": f"e{i}", "state": "failed", "error": "\n" if i == 0 else "boom",
                             "created_at": f"2026-10-08 14:2{i}:00"} for i in range(9)]),
    }, recent=3)
    assert [row["id"] for row in summary] == ["u1", "k1", "e8", "e7", "e6"]  # active first, then 3 newest finished
    assert summary[0] == {"kind": "upscale", "id": "u1", "title": "Улучшение ролика", "href": "/#jobQueue",
                          "state": "running", "error": None, "source_job_id": "j1", "mode": "upscale",
                          "created_at": "2026-10-08 14:23:16", "updated_at": None}
    blank = side_job_summary({"audio": FakeQueue([{"audio_id": "e0", "state": "failed", "error": "\n",
                                                         "created_at": "x"}])})
    assert blank[0]["error"] is None


def test_finished_side_jobs_can_be_hidden(tmp_path):
    import pytest
    from local_studio.side_jobs import hidden_side_jobs, hide_side_jobs
    from local_studio.store import StudioStore
    store = StudioStore(tmp_path)
    queues = {"upscale": FakeQueue([{"upscale_id": "e1", "state": "failed", "created_at": "2"},
                                    {"upscale_id": "e2", "state": "running", "created_at": "3"}]),
              "audio": FakeQueue([{"audio_id": "a1", "state": "failed", "created_at": "1"}])}
    with pytest.raises(ValueError):
        hide_side_jobs(store, queues, only=("upscale", "e2"))  # on the GPU: stays
    with pytest.raises(LookupError):
        hide_side_jobs(store, queues, only=("upscale", "nope"))
    assert hide_side_jobs(store, queues, only=("upscale", "e1")) == 1
    assert [row["id"] for row in side_job_summary(queues, hidden=hidden_side_jobs(store))] == ["e2", "a1"]
    assert hide_side_jobs(store, queues) == 2  # «Удалить всё»: every finished one, the running edit stays
    assert [row["id"] for row in side_job_summary(queues, hidden=hidden_side_jobs(store))] == ["e2"]
