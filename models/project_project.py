import logging
import re
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Command
from odoo.tools import plaintext2html

from ..github_client import GithubClient, GithubError

_logger = logging.getLogger(__name__)

GITHUB_REPO_PATTERN = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
# Fetch a little before the last sync, in case the clocks of Odoo and GitHub
# differ. Issues seen twice are simply updated again.
SYNC_OVERLAP = timedelta(minutes=5)


class ProjectProject(models.Model):
    _inherit = "project.project"

    github_repo = fields.Char(
        string="GitHub Repository",
        help="Repository to sync with, written as owner/name, for example odoo/odoo.",
        copy=False,
    )
    github_sync_enabled = fields.Boolean(
        string="Sync with GitHub",
        help="Include this project in the scheduled GitHub sync.",
        copy=False,
    )
    github_last_sync = fields.Datetime(
        string="Last GitHub Sync",
        readonly=True,
        copy=False,
    )
    github_open_stage_id = fields.Many2one(
        "project.task.type",
        string="Stage for Open Issues",
        domain="[('project_ids', 'in', id)]",
        copy=False,
        help="Stage for new tasks and for issues that are reopened. "
        "Leave empty to use the project's first stage.",
    )
    github_closed_stage_id = fields.Many2one(
        "project.task.type",
        string="Stage for Closed Issues",
        domain="[('project_ids', 'in', id)]",
        copy=False,
        help="Stage for tasks whose issue is closed. "
        "Leave empty to keep the current stage.",
    )

    @api.constrains("github_repo")
    def _check_github_repo(self):
        for project in self:
            if project.github_repo and not GITHUB_REPO_PATTERN.fullmatch(
                project.github_repo
            ):
                raise ValidationError(
                    self.env._(
                        "The GitHub repository must be written as owner/name, "
                        "for example odoo/odoo."
                    )
                )

    @api.constrains("github_sync_enabled", "github_repo")
    def _check_github_sync_has_repo(self):
        for project in self:
            if project.github_sync_enabled and not project.github_repo:
                raise ValidationError(
                    self.env._("Set a GitHub repository before turning on the sync.")
                )

    def action_github_sync(self):
        """Sync now button: import the issues and report the result."""
        self.ensure_one()
        result = self._github_sync()
        if result is None:
            message = self.env._(
                "The sync failed. The error is logged in the project's chatter."
            )
            notification_type = "danger"
        else:
            message = self.env._(
                "%(created)s tasks created, %(updated)s tasks updated.", **result
            )
            notification_type = "success"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": self.env._("GitHub Sync"),
                "message": message,
                "type": notification_type,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    @api.model
    def _cron_github_sync(self):
        projects = self.search(
            [("github_sync_enabled", "=", True), ("github_repo", "!=", False)]
        )
        self.env["ir.cron"]._commit_progress(remaining=len(projects))
        for project in projects:
            try:
                with self.env.cr.savepoint():
                    project._github_sync()
            except Exception:
                _logger.exception("GitHub sync failed for project %s", project.id)
                project._github_log_error(
                    self.env._("Unexpected error, see the server log for details.")
                )
            self.env["ir.cron"]._commit_progress(1)

    def _github_get_client(self):
        token = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("github_projects_sync.token")
        )
        return GithubClient(token or None)

    def _github_sync(self):
        """Import the issues updated since the last sync.

        Returns a dict with the number of tasks created and updated, or None
        when GitHub returned an error. Errors are posted in the chatter.
        """
        self.ensure_one()
        sync_started = fields.Datetime.now()
        since = self.github_last_sync and self.github_last_sync - SYNC_OVERLAP
        client = self._github_get_client()
        cache = {"users": self._github_users_by_login(), "tags": {}}
        created = updated = 0
        try:
            for issue in client.iter_issues(self.github_repo, since=since):
                if self._github_import_issue(issue, cache):
                    created += 1
                else:
                    updated += 1
        except GithubError as error:
            # Tasks imported before the error are kept. The next run fetches
            # the same issues again, which is safe.
            _logger.warning("GitHub sync failed for project %s: %s", self.id, error)
            self._github_log_error(str(error))
            return None
        self.github_last_sync = sync_started
        return {"created": created, "updated": updated}

    def _github_import_issue(self, issue, cache):
        """Create or update the task of one issue. Returns True if created."""
        Task = self.env["project.task"].with_context(
            active_test=False, mail_auto_subscribe_no_notify=True
        )
        task = Task.search(
            [
                ("project_id", "=", self.id),
                ("github_issue_id", "=", str(issue["id"])),
            ],
            limit=1,
        )
        values = self._github_task_values(issue, task, cache)
        if task:
            task.write(values)
            return False
        Task.create({"project_id": self.id, **values})
        return True

    def _github_task_values(self, issue, task, cache):
        closed = issue["state"] == "closed"
        values = {
            "name": issue["title"],
            "description": plaintext2html(issue.get("body") or ""),
            "github_issue_id": str(issue["id"]),
            "github_issue_number": issue["number"],
            "github_issue_url": issue["html_url"],
            "github_issue_state": issue["state"],
            "github_last_sync": fields.Datetime.now(),
            "tag_ids": [Command.set(self._github_tags(issue, cache).ids)],
            "user_ids": [Command.set(self._github_assignees(issue, cache).ids)],
        }
        if closed:
            if self.github_closed_stage_id:
                values["stage_id"] = self.github_closed_stage_id.id
            values["state"] = (
                "1_canceled" if issue.get("state_reason") == "not_planned" else "1_done"
            )
        else:
            if self.github_open_stage_id and (
                not task or task.github_issue_state == "closed"
            ):
                values["stage_id"] = self.github_open_stage_id.id
            if task and task.state in ("1_done", "1_canceled"):
                values["state"] = "01_in_progress"
        return values

    def _github_tags(self, issue, cache):
        Tag = self.env["project.tags"]
        tags = Tag
        for label in issue.get("labels") or []:
            name = label["name"] if isinstance(label, dict) else label
            tag = cache["tags"].get(name)
            if not tag:
                tag = Tag.search([("name", "=", name)], limit=1) or Tag.create(
                    {"name": name}
                )
                cache["tags"][name] = tag
            tags |= tag
        return tags

    def _github_assignees(self, issue, cache):
        users = self.env["res.users"]
        for assignee in issue.get("assignees") or []:
            users |= cache["users"].get(assignee["login"].lower(), users)
        return users

    def _github_users_by_login(self):
        users = self.env["res.users"].search(
            [("github_login", "!=", False), ("share", "=", False)]
        )
        return {user.github_login.lower(): user for user in users}

    def _github_log_error(self, message):
        self.message_post(
            body=self.env._("GitHub sync failed: %(error)s", error=message),
            subtype_xmlid="mail.mt_note",
        )
