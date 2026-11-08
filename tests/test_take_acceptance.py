from local_studio.take_acceptance import take_is_accepted


def test_changed_spec_or_selected_take_invalidates_previous_acceptance():
    cut = {"shotJobId": "j1", "shotQcReview": "accepted",
           "shotQc": {"job_id": "j1", "decision": "accept"}}
    assert take_is_accepted(cut)  # Legacy selectedTakeId is optional.
    assert not take_is_accepted({**cut, "productionSpecChanged": True})
    assert not take_is_accepted({**cut, "selectedTakeId": "j2"})
    assert not take_is_accepted({**cut, "takeReview": {"state": "stale"}})
    assert not take_is_accepted({**cut, "shotQc": {"job_id": "j2", "decision": "accept"}})


def test_pending_or_different_job_review_cannot_reuse_older_qc():
    cut = {"shotJobId": "j1", "shotQcReview": "accepted",
           "shotQc": {"job_id": "j1", "decision": "accept"}}
    for review in ({"state": "running", "job_id": "j1"},
                   {"state": "accepted", "job_id": "j2"}):
        assert not take_is_accepted({**cut, "takeReview": review})


def test_browser_and_server_take_contract_agree():
    import json
    import shutil
    import subprocess
    from pathlib import Path
    import pytest

    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for cross-runtime acceptance contract')
    base = {'shotJobId': 'j1', 'shotQcReview': 'accepted',
            'shotQc': {'job_id': 'j1', 'decision': 'accept'}}
    cases = [None, {}, base]
    cases.extend({**base, 'shotQc': {'job_id': 'j1', 'decision': decision}}
                 for decision in ('adapt_next', 'manual_review', 'reject', 'unknown'))
    cases.extend({**base, **change} for change in (
        {'selectedTakeId': 'j2'}, {'selectedTakeId': None}, {'productionSpecChanged': True},
        {'takeReview': {}}, {'takeReview': {'state': 'accepted', 'job_id': 'j1'}},
        {'takeReview': {'state': 'accepted', 'job_id': 'j2'}},
        {'takeReview': {'state': 'running', 'job_id': 'j1'}},
        {'takeReview': {'state': 'stale', 'job_id': 'j1'}}))
    root = Path(__file__).resolve().parents[1]
    script = "import {acceptedShot} from './preview/h3-director/storyboard-model.mjs';" \
             "let data='';for await (const s of process.stdin)data+=s;" \
             "console.log(JSON.stringify(JSON.parse(data).map(acceptedShot)));"
    response = subprocess.run([node, '--input-type=module', '-e', script], cwd=root,
                              input=json.dumps(cases), text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(response.stdout) == [take_is_accepted(cut or {}) for cut in cases]
