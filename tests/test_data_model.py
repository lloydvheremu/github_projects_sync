from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestGithubDataModel(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team = cls.env["helpdesk.ticket.team"].create({"name": "Sync Team"})

    def _ticket(self, team=None, **values):
        return self.env["helpdesk.ticket"].create(
            {
                "name": "Ticket",
                "description": "<p>Text</p>",
                "team_id": (team or self.team).id,
                **values,
            }
        )

    def test_repo_format_is_validated(self):
        self.team.github_repo = "lloydvheremu/github_projects_sync"
        for bad_value in (
            "no-slash",
            "owner/",
            "/name",
            "a/b/c",
            "own er/name",
            "owner/name\n",
        ):
            with self.subTest(value=bad_value), self.assertRaises(ValidationError):
                self.team.github_repo = bad_value

    def test_sync_needs_a_repo(self):
        with self.assertRaises(ValidationError):
            self.team.github_sync_enabled = True
        self.team.write({"github_repo": "odoo/odoo", "github_sync_enabled": True})
        self.assertTrue(self.team.github_sync_enabled)

    def test_large_issue_id_is_kept_exactly(self):
        ticket = self._ticket(github_issue_id="3456789012")
        self.assertEqual(ticket.github_issue_id, "3456789012")

    @mute_logger("odoo.sql_db")
    def test_issue_linked_once_per_team(self):
        self._ticket(github_issue_id="42")
        with self.assertRaises(IntegrityError):
            self._ticket(github_issue_id="42")
            self.env.flush_all()

    def test_same_issue_allowed_in_other_team(self):
        other = self.env["helpdesk.ticket.team"].create({"name": "Other"})
        self._ticket(github_issue_id="7")
        self._ticket(team=other, github_issue_id="7")
        self.env.flush_all()

    def test_tickets_without_issue_are_not_constrained(self):
        self._ticket()
        self._ticket()
        self.env.flush_all()

    def test_token_is_stored_as_system_parameter(self):
        settings = self.env["res.config.settings"].create({"github_token": "tok"})
        settings.execute()
        self.assertEqual(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("github_projects_sync.token"),
            "tok",
        )

    def test_copied_team_does_not_keep_github_link(self):
        self.team.write({"github_repo": "odoo/odoo", "github_sync_enabled": True})
        copy = self.team.copy()
        self.assertFalse(copy.github_repo)
        self.assertFalse(copy.github_sync_enabled)
