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


class HelpdeskTicketTeam(models.Model):
    _inherit = "helpdesk.ticket.team"

    github_repo = fields.Char(
        string="GitHub Repository",
        help="Repository to sync with, written as owner/name, for example odoo/odoo.",
        copy=False,
    )
    github_sync_enabled = fields.Boolean(
        string="Sync with GitHub",
        help="Include this team in the scheduled GitHub sync.",
        copy=False,
    )
    github_last_sync = fields.Datetime(
        string="Last GitHub Sync",
        readonly=True,
        copy=False,
    )
    github_open_stage_id = fields.Many2one(
        "helpdesk.ticket.stage",
        string="Stage for Open Issues",
        domain="['|', ('team_ids', '=', False), ('team_ids', 'in', id)]",
        copy=False,
        help="Stage for new tickets and for issues that are reopened. "
        "Leave empty to use the team's first open stage.",
    )
    github_closed_stage_id = fields.Many2one(
        "helpdesk.ticket.stage",
        string="Stage for Closed Issues",
        domain="['|', ('team_ids', '=', False), ('team_ids', 'in', id)]",
        copy=False,
        help="Stage for tickets whose issue is closed. "
        "Leave empty to use the team's first closed stage.",
    )

    @api.constrains("github_repo")
    def _check_github_repo(self):
        for team in self:
            if team.github_repo and not GITHUB_REPO_PATTERN.fullmatch(team.github_repo):
                raise ValidationError(
                    self.env._(
                        "The GitHub repository must be written as owner/name, "
                        "for example odoo/odoo."
                    )
                )

    @api.constrains("github_sync_enabled", "github_repo")
    def _check_github_sync_has_repo(self):
        for team in self:
            if team.github_sync_enabled and not team.github_repo:
                raise ValidationError(
                    self.env._("Set a GitHub repository before turning on the sync.")
                )

    def action_github_sync(self):
        """Sync now button: import the issues and report the result."""
        self.ensure_one()
        result = self._github_sync()
        if result is None:
            message = self.env._(
                "The sync failed. The error is logged in the team's chatter."
            )
            notification_type = "danger"
        else:
            message = self.env._(
                "%(created)s tickets created, %(updated)s tickets updated.", **result
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
        teams = self.search(
            [("github_sync_enabled", "=", True), ("github_repo", "!=", False)]
        )
        self.env["ir.cron"]._commit_progress(remaining=len(teams))
        for team in teams:
            try:
                with self.env.cr.savepoint():
                    team._github_sync()
            except Exception:
                _logger.exception("GitHub sync failed for team %s", team.id)
                team._github_log_error(
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

        Returns a dict with the number of tickets created and updated, or None
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
            # Tickets imported before the error are kept. The next run fetches
            # the same issues again, which is safe.
            _logger.warning("GitHub sync failed for team %s: %s", self.id, error)
            self._github_log_error(str(error))
            return None
        self.github_last_sync = sync_started
        return {"created": created, "updated": updated}

    def _github_import_issue(self, issue, cache):
        """Create or update the ticket of one issue. Returns True if created."""
        Ticket = self.env["helpdesk.ticket"].with_context(
            active_test=False, mail_auto_subscribe_no_notify=True
        )
        ticket = Ticket.search(
            [
                ("team_id", "=", self.id),
                ("github_issue_id", "=", str(issue["id"])),
            ],
            limit=1,
        )
        values = self._github_ticket_values(issue, ticket, cache)
        if ticket:
            ticket.write(values)
            return False
        Ticket.create({"team_id": self.id, **values})
        return True

    def _github_ticket_values(self, issue, ticket, cache):
        body = (issue.get("body") or "").strip()
        values = {
            "name": issue["title"],
            # The description is required, so an empty issue gets a placeholder.
            "description": plaintext2html(
                body or self.env._("No description on GitHub.")
            ),
            "github_issue_id": str(issue["id"]),
            "github_issue_number": issue["number"],
            "github_issue_url": issue["html_url"],
            "github_issue_state": issue["state"],
            "github_last_sync": fields.Datetime.now(),
            "tag_ids": [Command.set(self._github_tags(issue, cache).ids)],
            "user_id": self._github_assignee(issue, cache).id,
        }
        if issue["state"] == "closed":
            stage = self._github_closed_stage()
        elif not ticket or ticket.github_issue_state == "closed":
            stage = self._github_open_stage()
        else:
            stage = self.env["helpdesk.ticket.stage"]
        if stage:
            values["stage_id"] = stage.id
        return values

    def _github_open_stage(self):
        return (
            self.github_open_stage_id
            or self._get_applicable_stages().filtered(lambda stage: not stage.closed)[
                :1
            ]
        )

    def _github_closed_stage(self):
        return (
            self.github_closed_stage_id
            or self._get_applicable_stages().filtered("closed")[:1]
        )

    def _github_tags(self, issue, cache):
        Tag = self.env["helpdesk.ticket.tag"]
        tags = Tag
        for label in issue.get("labels") or []:
            name = label["name"] if isinstance(label, dict) else label
            tag = cache["tags"].get(name)
            if not tag:
                tag = Tag.search(
                    [
                        ("name", "=", name),
                        ("company_id", "in", [False, self.company_id.id]),
                    ],
                    limit=1,
                ) or Tag.create({"name": name, "company_id": self.company_id.id})
                cache["tags"][name] = tag
            tags |= tag
        return tags

    def _github_assignee(self, issue, cache):
        """The first assignee who is an Odoo user allowed on this team.

        A ticket has a single assignee. When the team has members, only they
        can be assigned, as on the ticket form.
        """
        for assignee in issue.get("assignees") or []:
            user = cache["users"].get(assignee["login"].lower())
            if user and (not self.user_ids or user in self.user_ids):
                return user
        return self.env["res.users"]

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
