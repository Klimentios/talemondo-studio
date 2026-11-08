import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_studio.agents.contracts import AgentTaskRequest, validate_agent_contract
from local_studio.core.idempotency import EventConflict, StateConflict, StateTransactionManager, UnauthorizedAgent


class AgentStateTransactionsTests(unittest.TestCase):
    def test_contract_rejects_invalid_mutations(self):
        bad = AgentTaskRequest(event_id="", agent_role="director", project_id="film",
                               base_revision=-1, patch={})
        self.assertIn("Missing event_id", validate_agent_contract(bad))
        self.assertIn("Invalid base_revision", validate_agent_contract(bad))
        self.assertIn("Missing shot_id", validate_agent_contract(bad))
        self.assertIn("Empty mutation patch", validate_agent_contract(bad))

    def test_replay_conflict_and_rollback_survive_new_manager(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "studio.sqlite3")
            manager = StateTransactionManager(path)
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE shots (id TEXT PRIMARY KEY, prompt TEXT NOT NULL, revision INTEGER NOT NULL)")
                db.execute("INSERT INTO shots VALUES ('s1', 'original', 0)")

            def read_revision(db, request):
                return db.execute("SELECT revision FROM shots WHERE id=?", (request.shot_id,)).fetchone()[0]

            def mutate(db, request):
                if request.patch.get("prompt") == "broken":
                    db.execute("UPDATE shots SET prompt='broken', revision=revision+1 WHERE id='s1'")
                    raise RuntimeError("domain validation failed")
                db.execute("UPDATE shots SET prompt=?, revision=revision+1 WHERE id=?",
                           (request.patch["prompt"], request.shot_id))
                return request.base_revision + 1

            def apply(request, allowed_roles={"director"}):
                return manager.apply(request, allowed_roles=allowed_roles,
                                     read_revision=read_revision, mutate=mutate)

            first = AgentTaskRequest("e1", "director", "film", "s1", 0, patch={"prompt": "accepted"})
            self.assertEqual(apply(first), (1, False))
            manager = StateTransactionManager(path)
            self.assertEqual(apply(first), (1, True))
            with self.assertRaises(EventConflict):
                apply(AgentTaskRequest("e1", "director", "film", "s1", 0,
                                       patch={"prompt": "different"}))
            with self.assertRaises(StateConflict):
                apply(AgentTaskRequest("e2", "director", "film", "s1", 0,
                                       patch={"prompt": "stale"}))
            with self.assertRaises(UnauthorizedAgent):
                apply(AgentTaskRequest("e3", "viewer", "film", "s1", 1,
                                       patch={"prompt": "forbidden"}))
            with self.assertRaises(RuntimeError):
                apply(AgentTaskRequest("e4", "director", "film", "s1", 1,
                                       patch={"prompt": "broken"}))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT prompt FROM shots WHERE id='s1'").fetchone()[0], "accepted")
                self.assertIsNone(db.execute("SELECT 1 FROM agent_event_receipts WHERE event_id='e4'").fetchone())
            self.assertEqual(apply(AgentTaskRequest("e4", "director", "film", "s1", 1,
                                                    patch={"prompt": "fixed"})), (2, False))


if __name__ == "__main__":
    unittest.main()
