from datetime import datetime
from unittest.mock import patch

import requests
from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools import mute_logger

from odoo.addons.github_projects_sync.github_client import API_URL, GithubClient

from .github_mocks import fake_session, make_issue, make_pull_request, make_response

ISSUES_URL = f"{API_URL}/repos/owner/repo/issues"
LOGGER = "odoo.addons.github_projects_sync.models.project_project"


@tagged("post_install", "-at_install")
class TestGithubSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_todo = cls.env["project.task.type"].create({"name": "To Do"})
        cls.stage_done = cls.env["project.task.type"].create({"name": "Done"})
        cls.project = cls.env["project.project"].create(
            {
                "name": "Synced",
                "github_repo": "owner/repo",
                "github_sync_enabled": True,
                "type_ids": [(6, 0, [cls.stage_todo.id, cls.stage_done.id])],
            }
        )
        cls.alice = new_test_user(
            cls.env, login="alice", groups="project.group_project_user"
        )
        cls.alice.github_login = "Alice-GH"

    def _sync(self, *responses, project=None):
        """Run the sync with GitHub answering ``responses`` in order."""
        session = fake_session(*responses)
        client = GithubClient("token", session=session)
        Project = self.registry["project.project"]
        with patch.object(Project, "_github_get_client", return_value=client):
            result = (project or self.project)._github_sync()
        return result, session

    def _tasks(self):
        return (
            self.env["project.task"]
            .with_context(active_test=False)
            .search([("project_id", "=", self.project.id)])
        )

    def test_issues_become_tasks(self):
        issue = make_issue(
            1,
            title="Fix login",
            body="Steps:\n1. open <page>",
            labels=[{"name": "bug"}, {"name": "backend"}],
            assignees=[{"login": "alice-gh"}, {"login": "stranger"}],
        )
        result, _session = self._sync(make_response(body=[issue, make_pull_request(2)]))
        self.assertEqual(result, {"created": 1, "updated": 0})
        task = self._tasks()
        self.assertEqual(len(task), 1, "the pull request must not become a task")
        self.assertEqual(task.name, "Fix login")
        self.assertEqual(task.github_issue_id, str(issue["id"]))
        self.assertEqual(task.github_issue_number, 1)
        self.assertEqual(task.github_issue_url, issue["html_url"])
        self.assertEqual(task.github_issue_state, "open")
        self.assertTrue(task.github_last_sync)
        self.assertIn("open &lt;page&gt;", task.description)
        self.assertEqual(sorted(task.tag_ids.mapped("name")), ["backend", "bug"])
        self.assertEqual(task.user_ids, self.alice, "logins match without case")
        self.assertEqual(task.state, "01_in_progress")

    def test_sync_is_safe_to_repeat(self):
        self._sync(make_response(body=[make_issue(1), make_issue(2)]))
        result, _session = self._sync(
            make_response(body=[make_issue(1, title="Renamed"), make_issue(3)])
        )
        self.assertEqual(result, {"created": 1, "updated": 1})
        tasks = self._tasks()
        self.assertEqual(len(tasks), 3)
        self.assertIn("Renamed", tasks.mapped("name"))

    def test_existing_tag_is_reused(self):
        tag = self.env["project.tags"].create({"name": "bug"})
        self._sync(make_response(body=[make_issue(1, labels=[{"name": "bug"}])]))
        self.assertEqual(self._tasks().tag_ids, tag)
        self.assertEqual(
            self.env["project.tags"].search_count([("name", "=", "bug")]), 1
        )

    def test_github_is_source_of_truth_for_labels_and_assignees(self):
        self._sync(
            make_response(
                body=[
                    make_issue(
                        1, labels=[{"name": "bug"}], assignees=[{"login": "Alice-GH"}]
                    )
                ]
            )
        )
        self._sync(make_response(body=[make_issue(1)]))
        task = self._tasks()
        self.assertFalse(task.tag_ids)
        self.assertFalse(task.user_ids)

    def test_since_and_last_sync(self):
        _result, session = self._sync(make_response(body=[]))
        self.assertNotIn("since", session.get.call_args.kwargs["params"])
        self.assertTrue(self.project.github_last_sync)

        self.project.github_last_sync = datetime(2026, 10, 1, 12, 0, 0)
        _result, session = self._sync(make_response(body=[]))
        self.assertEqual(
            session.get.call_args.kwargs["params"]["since"], "2026-10-01T11:55:00Z"
        )
        self.assertGreater(self.project.github_last_sync, datetime(2026, 10, 1, 12))

    def test_closed_and_reopened_issues(self):
        self.project.write(
            {
                "github_open_stage_id": self.stage_todo.id,
                "github_closed_stage_id": self.stage_done.id,
            }
        )
        self._sync(make_response(body=[make_issue(1), make_issue(2)]))
        tasks = self._tasks()
        self.assertEqual(tasks.stage_id, self.stage_todo)

        self._sync(
            make_response(
                body=[
                    make_issue(1, state="closed", state_reason="completed"),
                    make_issue(2, state="closed", state_reason="not_planned"),
                ]
            )
        )
        done = tasks.filtered(lambda t: t.github_issue_number == 1)
        dropped = tasks - done
        self.assertEqual(done.state, "1_done")
        self.assertEqual(dropped.state, "1_canceled")
        self.assertEqual(tasks.stage_id, self.stage_done)
        self.assertEqual(set(tasks.mapped("github_issue_state")), {"closed"})

        self._sync(make_response(body=[make_issue(1, state="open")]))
        self.assertEqual(done.state, "01_in_progress")
        self.assertEqual(done.stage_id, self.stage_todo)

    def test_open_issue_keeps_stage_chosen_in_odoo(self):
        self.project.github_open_stage_id = self.stage_todo
        self._sync(make_response(body=[make_issue(1)]))
        task = self._tasks()
        task.stage_id = self.stage_done
        self._sync(make_response(body=[make_issue(1, title="Edited on GitHub")]))
        self.assertEqual(task.stage_id, self.stage_done)

    def test_archived_task_is_updated_not_duplicated(self):
        self._sync(make_response(body=[make_issue(1)]))
        self._tasks().active = False
        result, _session = self._sync(make_response(body=[make_issue(1, title="New")]))
        self.assertEqual(result, {"created": 0, "updated": 1})
        self.assertEqual(self._tasks().name, "New")

    def test_github_error_is_logged_and_progress_kept(self):
        self.project.github_last_sync = datetime(2026, 10, 1)
        page2 = f"{ISSUES_URL}?page=2"
        with self.assertLogs(LOGGER, "WARNING"):
            result, _session = self._sync(
                make_response(
                    body=[make_issue(1)], headers={"Link": f'<{page2}>; rel="next"'}
                ),
                make_response(500, {"message": "Server Error"}),
            )
        self.assertIsNone(result)
        self.assertEqual(len(self._tasks()), 1, "tasks from the first page are kept")
        self.assertEqual(self.project.github_last_sync, datetime(2026, 10, 1))
        message = self.project.message_ids[0]
        self.assertIn("GitHub sync failed", message.body)
        self.assertIn("Server Error", message.body)

    def test_error_text_is_escaped_in_chatter(self):
        with self.assertLogs(LOGGER, "WARNING"):
            self._sync(make_response(422, {"message": "<script>x</script>"}))
        body = self.project.message_ids[0].body
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_sync_now_button(self):
        manager = new_test_user(
            self.env, login="pm", groups="project.group_project_manager"
        )
        session = fake_session(
            make_response(body=[make_issue(1, labels=[{"name": "new-label"}])])
        )
        client = GithubClient("token", session=session)
        Project = self.registry["project.project"]
        with patch.object(Project, "_github_get_client", return_value=client):
            action = self.project.with_user(manager).action_github_sync()
        self.assertEqual(action["params"]["type"], "success")
        self.assertIn("1 tasks created", action["params"]["message"])

        session = fake_session(requests.Timeout())
        client = GithubClient("token", session=session)
        with (
            patch.object(Project, "_github_get_client", return_value=client),
            self.assertLogs(LOGGER, "WARNING"),
        ):
            action = self.project.with_user(manager).action_github_sync()
        self.assertEqual(action["params"]["type"], "danger")

    def test_client_uses_stored_token(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "github_projects_sync.token", "secret"
        )
        client = self.project._github_get_client()
        self.assertEqual(client.session.headers["Authorization"], "Bearer secret")

    def test_cron_syncs_enabled_projects_and_isolates_failures(self):
        broken = self.env["project.project"].create(
            {
                "name": "Broken",
                "github_repo": "owner/broken",
                "github_sync_enabled": True,
            }
        )
        disabled = self.env["project.project"].create(
            {"name": "Off", "github_repo": "owner/off", "github_sync_enabled": False}
        )
        synced = self.env["project.project"]
        Project = self.registry["project.project"]
        original = Project._github_sync

        def fake_sync(project):
            nonlocal synced
            synced |= project
            if project == broken:
                raise RuntimeError("boom")
            return original(project)

        client = GithubClient(
            "token", session=fake_session(make_response(body=[make_issue(1)]))
        )
        with (
            patch.object(Project, "_github_sync", fake_sync),
            patch.object(Project, "_github_get_client", return_value=client),
            patch.object(self.registry["ir.cron"], "_commit_progress"),
            self.assertLogs(LOGGER, "ERROR"),
        ):
            Project._cron_github_sync(self.env["project.project"])
        self.assertIn(self.project, synced)
        self.assertIn(broken, synced)
        self.assertNotIn(disabled, synced)
        self.assertEqual(
            len(self._tasks()), 1, "a failing project does not stop others"
        )
        self.assertIn("Unexpected error", broken.message_ids[0].body)

    def test_github_login_is_unique_ignoring_case(self):
        bob = new_test_user(self.env, login="bob")
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            bob.github_login = "alice-gh"
            self.env.flush_all()
