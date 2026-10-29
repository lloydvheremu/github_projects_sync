import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError

GITHUB_REPO_PATTERN = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")


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
