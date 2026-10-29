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
        cls.project = cls.env["project.project"].create({"name": "Sync Project"})

    def test_repo_format_is_validated(self):
        self.project.github_repo = "lloydvheremu/github_projects_sync"
        for bad_value in (
            "no-slash",
            "owner/",
            "/name",
            "a/b/c",
            "own er/name",
            "owner/name\n",
        ):
            with self.subTest(value=bad_value), self.assertRaises(ValidationError):
                self.project.github_repo = bad_value

    def test_sync_needs_a_repo(self):
        with self.assertRaises(ValidationError):
            self.project.github_sync_enabled = True
        self.project.write({"github_repo": "odoo/odoo", "github_sync_enabled": True})
        self.assertTrue(self.project.github_sync_enabled)

    def test_large_issue_id_is_kept_exactly(self):
        task = self.env["project.task"].create(
            {
                "name": "Big id",
                "project_id": self.project.id,
                "github_issue_id": "3456789012",
            }
        )
        self.assertEqual(task.github_issue_id, "3456789012")

    @mute_logger("odoo.sql_db")
    def test_issue_linked_once_per_project(self):
        Task = self.env["project.task"]
        Task.create(
            {"name": "First", "project_id": self.project.id, "github_issue_id": "42"}
        )
        with self.assertRaises(IntegrityError):
            Task.create(
                {
                    "name": "Duplicate",
                    "project_id": self.project.id,
                    "github_issue_id": "42",
                }
            )
            self.env.flush_all()

    def test_same_issue_allowed_in_other_project(self):
        other = self.env["project.project"].create({"name": "Other"})
        Task = self.env["project.task"]
        Task.create(
            {"name": "A", "project_id": self.project.id, "github_issue_id": "7"}
        )
        Task.create({"name": "B", "project_id": other.id, "github_issue_id": "7"})
        self.env.flush_all()

    def test_tasks_without_issue_are_not_constrained(self):
        Task = self.env["project.task"]
        Task.create([{"name": "x", "project_id": self.project.id}] * 2)
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

    def test_copied_project_does_not_keep_github_link(self):
        self.project.write({"github_repo": "odoo/odoo", "github_sync_enabled": True})
        copy = self.project.copy()
        self.assertFalse(copy.github_repo)
        self.assertFalse(copy.github_sync_enabled)
