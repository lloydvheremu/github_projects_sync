from datetime import datetime
from unittest.mock import patch

import requests
from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools import mute_logger

from odoo.addons.helpdesk_mgmt_github.github_client import API_URL, GithubClient

from .github_mocks import fake_session, make_issue, make_pull_request, make_response

ISSUES_URL = f"{API_URL}/repos/owner/repo/issues"
LOGGER = "odoo.addons.helpdesk_mgmt_github.models.helpdesk_ticket_team"


@tagged("post_install", "-at_install")
class TestGithubSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Stage = cls.env["helpdesk.ticket.stage"]
        # Negative sequences put these ahead of any stage from demo data.
        cls.stage_new = Stage.create({"name": "GH New", "sequence": -30})
        cls.stage_wip = Stage.create({"name": "GH Working", "sequence": -20})
        cls.stage_done = Stage.create(
            {"name": "GH Done", "sequence": -10, "closed": True}
        )
        cls.team = cls.env["helpdesk.ticket.team"].create(
            {
                "name": "Synced",
                "github_repo": "owner/repo",
                "github_sync_enabled": True,
            }
        )
        cls.alice = new_test_user(
            cls.env, login="alice", groups="helpdesk_mgmt.group_helpdesk_user"
        )
        cls.alice.github_login = "Alice-GH"

    def _sync(self, *responses, team=None):
        """Run the sync with GitHub answering ``responses`` in order."""
        session = fake_session(*responses)
        client = GithubClient("token", session=session)
        Team = self.registry["helpdesk.ticket.team"]
        with patch.object(Team, "_github_get_client", return_value=client):
            result = (team or self.team)._github_sync()
        return result, session

    def _tickets(self):
        return (
            self.env["helpdesk.ticket"]
            .with_context(active_test=False)
            .search([("team_id", "=", self.team.id)])
        )

    def test_issues_become_tickets(self):
        issue = make_issue(
            1,
            title="Fix login",
            body="Steps:\n1. open <page>",
            labels=[{"name": "bug"}, {"name": "backend"}],
            assignees=[{"login": "stranger"}, {"login": "alice-gh"}],
        )
        result, _session = self._sync(make_response(body=[issue, make_pull_request(2)]))
        self.assertEqual(result, {"created": 1, "updated": 0})
        ticket = self._tickets()
        self.assertEqual(len(ticket), 1, "the pull request must not become a ticket")
        self.assertEqual(ticket.name, "Fix login")
        self.assertEqual(ticket.github_issue_id, str(issue["id"]))
        self.assertEqual(ticket.github_issue_number, 1)
        self.assertEqual(ticket.github_issue_url, issue["html_url"])
        self.assertEqual(ticket.github_issue_state, "open")
        self.assertTrue(ticket.github_last_sync)
        self.assertIn("open &lt;page&gt;", ticket.description)
        self.assertEqual(sorted(ticket.tag_ids.mapped("name")), ["backend", "bug"])
        self.assertEqual(ticket.user_id, self.alice, "logins match without case")
        self.assertEqual(ticket.stage_id, self.stage_new)
        self.assertFalse(ticket.partner_id)

    def test_sync_is_safe_to_repeat(self):
        self._sync(make_response(body=[make_issue(1), make_issue(2)]))
        result, _session = self._sync(
            make_response(body=[make_issue(1, title="Renamed"), make_issue(3)])
        )
        self.assertEqual(result, {"created": 1, "updated": 1})
        tickets = self._tickets()
        self.assertEqual(len(tickets), 3)
        self.assertIn("Renamed", tickets.mapped("name"))

    def test_existing_tag_is_reused(self):
        tag = self.env["helpdesk.ticket.tag"].create({"name": "bug"})
        self._sync(make_response(body=[make_issue(1, labels=[{"name": "bug"}])]))
        self.assertEqual(self._tickets().tag_ids, tag)
        self.assertEqual(
            self.env["helpdesk.ticket.tag"].search_count([("name", "=", "bug")]), 1
        )

    def test_missing_tag_is_created_once(self):
        labels = [{"name": "brand-new"}]
        self._sync(make_response(body=[make_issue(1, labels=labels)]))
        self._sync(make_response(body=[make_issue(1, labels=labels)]))
        self.assertEqual(
            self.env["helpdesk.ticket.tag"].search_count([("name", "=", "brand-new")]),
            1,
        )

    def test_github_is_source_of_truth_for_labels_and_assignee(self):
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
        ticket = self._tickets()
        self.assertFalse(ticket.tag_ids)
        self.assertFalse(ticket.user_id)

    def test_assignee_must_be_on_the_team(self):
        carol = new_test_user(
            self.env, login="carol", groups="helpdesk_mgmt.group_helpdesk_user"
        )
        carol.github_login = "carol-gh"
        self.team.user_ids = self.alice
        self._sync(
            make_response(
                body=[
                    make_issue(1, assignees=[{"login": "carol-gh"}]),
                    make_issue(
                        2, assignees=[{"login": "carol-gh"}, {"login": "alice-gh"}]
                    ),
                ]
            )
        )
        by_number = {t.github_issue_number: t for t in self._tickets()}
        self.assertFalse(by_number[1].user_id, "carol is not a team member")
        self.assertEqual(by_number[2].user_id, self.alice, "first allowed assignee")

    def test_any_user_when_team_has_no_members(self):
        self.assertFalse(self.team.user_ids)
        self._sync(
            make_response(body=[make_issue(1, assignees=[{"login": "alice-gh"}])])
        )
        self.assertEqual(self._tickets().user_id, self.alice)

    def test_since_and_last_sync(self):
        _result, session = self._sync(make_response(body=[]))
        self.assertNotIn("since", session.get.call_args.kwargs["params"])
        self.assertTrue(self.team.github_last_sync)

        self.team.github_last_sync = datetime(2026, 10, 1, 12, 0, 0)
        _result, session = self._sync(make_response(body=[]))
        self.assertEqual(
            session.get.call_args.kwargs["params"]["since"], "2026-10-01T11:55:00Z"
        )
        self.assertGreater(self.team.github_last_sync, datetime(2026, 10, 1, 12))

    def test_closed_and_reopened_issues_use_first_matching_stage(self):
        self._sync(make_response(body=[make_issue(1)]))
        ticket = self._tickets()
        self.assertEqual(ticket.stage_id, self.stage_new)

        self._sync(make_response(body=[make_issue(1, state="closed")]))
        self.assertEqual(ticket.stage_id, self.stage_done)
        self.assertEqual(ticket.github_issue_state, "closed")
        self.assertTrue(ticket.closed)

        self._sync(make_response(body=[make_issue(1, state="open")]))
        self.assertEqual(ticket.stage_id, self.stage_new)

    def test_new_closed_issue_starts_in_closed_stage(self):
        self._sync(make_response(body=[make_issue(1, state="closed")]))
        self.assertEqual(self._tickets().stage_id, self.stage_done)

    def test_team_can_choose_the_stages(self):
        self.team.write(
            {
                "github_open_stage_id": self.stage_wip.id,
                "github_closed_stage_id": self.stage_done.id,
            }
        )
        self._sync(make_response(body=[make_issue(1)]))
        ticket = self._tickets()
        self.assertEqual(ticket.stage_id, self.stage_wip)
        self._sync(make_response(body=[make_issue(1, state="closed")]))
        self._sync(make_response(body=[make_issue(1)]))
        self.assertEqual(ticket.stage_id, self.stage_wip)

    def test_stages_of_other_teams_are_ignored(self):
        other = self.env["helpdesk.ticket.team"].create({"name": "Other"})
        early_closed = self.env["helpdesk.ticket.stage"].create(
            {"name": "Other closed", "sequence": 0, "closed": True, "team_ids": other}
        )
        self._sync(make_response(body=[make_issue(1, state="closed")]))
        self.assertEqual(self._tickets().stage_id, self.stage_done)
        self.assertNotEqual(self._tickets().stage_id, early_closed)

    def test_open_issue_keeps_stage_chosen_in_odoo(self):
        self._sync(make_response(body=[make_issue(1)]))
        ticket = self._tickets()
        ticket.stage_id = self.stage_wip
        self._sync(make_response(body=[make_issue(1, title="Edited on GitHub")]))
        self.assertEqual(ticket.stage_id, self.stage_wip)

    def test_empty_body_gets_a_placeholder(self):
        self._sync(
            make_response(
                body=[
                    make_issue(1, body=None),
                    make_issue(2, body="  \n"),
                    make_issue(3),
                ]
            )
        )
        by_number = {t.github_issue_number: t for t in self._tickets()}
        for number in (1, 2):
            self.assertIn("No description on GitHub", by_number[number].description)
        self.assertIn("Body of issue 3", by_number[3].description)

    def test_archived_ticket_is_updated_not_duplicated(self):
        self._sync(make_response(body=[make_issue(1)]))
        self._tickets().active = False
        result, _session = self._sync(make_response(body=[make_issue(1, title="New")]))
        self.assertEqual(result, {"created": 0, "updated": 1})
        self.assertEqual(self._tickets().name, "New")

    def test_github_error_is_logged_and_progress_kept(self):
        self.team.github_last_sync = datetime(2026, 10, 1)
        page2 = f"{ISSUES_URL}?page=2"
        with self.assertLogs(LOGGER, "WARNING"):
            result, _session = self._sync(
                make_response(
                    body=[make_issue(1)], headers={"Link": f'<{page2}>; rel="next"'}
                ),
                make_response(500, {"message": "Server Error"}),
            )
        self.assertIsNone(result)
        self.assertEqual(len(self._tickets()), 1, "page 1 tickets are kept")
        self.assertEqual(self.team.github_last_sync, datetime(2026, 10, 1))
        message = self.team.message_ids[0]
        self.assertIn("GitHub sync failed", message.body)
        self.assertIn("Server Error", message.body)

    def test_error_text_is_escaped_in_chatter(self):
        with self.assertLogs(LOGGER, "WARNING"):
            self._sync(make_response(422, {"message": "<script>x</script>"}))
        body = self.team.message_ids[0].body
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_sync_now_button_as_helpdesk_manager(self):
        manager = new_test_user(
            self.env, login="hm", groups="helpdesk_mgmt.group_helpdesk_manager"
        )
        session = fake_session(
            make_response(body=[make_issue(1, labels=[{"name": "new-label"}])])
        )
        client = GithubClient("token", session=session)
        Team = self.registry["helpdesk.ticket.team"]
        with patch.object(Team, "_github_get_client", return_value=client):
            action = self.team.with_user(manager).action_github_sync()
        self.assertEqual(action["params"]["type"], "success")
        self.assertIn("1 tickets created", action["params"]["message"])
        self.assertEqual(self._tickets().tag_ids.name, "new-label")

        session = fake_session(requests.Timeout())
        client = GithubClient("token", session=session)
        with (
            patch.object(Team, "_github_get_client", return_value=client),
            self.assertLogs(LOGGER, "WARNING"),
        ):
            action = self.team.with_user(manager).action_github_sync()
        self.assertEqual(action["params"]["type"], "danger")

    def test_client_uses_stored_token(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "helpdesk_mgmt_github.token", "secret"
        )
        client = self.team._github_get_client()
        self.assertEqual(client.session.headers["Authorization"], "Bearer secret")

    def test_cron_syncs_enabled_teams_and_isolates_failures(self):
        Team = self.registry["helpdesk.ticket.team"]
        Teams = self.env["helpdesk.ticket.team"]
        broken = Teams.create(
            {
                "name": "Broken",
                "github_repo": "owner/broken",
                "github_sync_enabled": True,
            }
        )
        disabled = Teams.create(
            {"name": "Off", "github_repo": "owner/off", "github_sync_enabled": False}
        )
        synced = self.env["helpdesk.ticket.team"]
        original = Team._github_sync

        def fake_sync(team):
            nonlocal synced
            synced |= team
            if team == broken:
                raise RuntimeError("boom")
            return original(team)

        client = GithubClient(
            "token", session=fake_session(make_response(body=[make_issue(1)]))
        )
        with (
            patch.object(Team, "_github_sync", fake_sync),
            patch.object(Team, "_github_get_client", return_value=client),
            patch.object(self.registry["ir.cron"], "_commit_progress"),
            self.assertLogs(LOGGER, "ERROR"),
        ):
            Team._cron_github_sync(self.env["helpdesk.ticket.team"])
        self.assertIn(self.team, synced)
        self.assertIn(broken, synced)
        self.assertNotIn(disabled, synced)
        self.assertEqual(len(self._tickets()), 1, "a failing team does not stop others")
        self.assertIn("Unexpected error", broken.message_ids[0].body)

    def test_github_login_is_unique_ignoring_case(self):
        bob = new_test_user(self.env, login="bob")
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            bob.github_login = "alice-gh"
            self.env.flush_all()
