from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    # GitHub issue ids no longer fit in a 32-bit integer, so keep them as text.
    github_issue_id = fields.Char(string="GitHub Issue ID", readonly=True, copy=False)
    github_issue_number = fields.Integer(
        string="GitHub Issue Number", readonly=True, copy=False
    )
    github_issue_url = fields.Char(string="GitHub Issue", readonly=True, copy=False)
    github_issue_state = fields.Selection(
        [("open", "Open"), ("closed", "Closed")],
        string="GitHub State",
        readonly=True,
        copy=False,
    )
    github_last_sync = fields.Datetime(
        string="Last GitHub Sync", readonly=True, copy=False
    )

    _github_issue_unique = models.Constraint(
        "UNIQUE(project_id, github_issue_id)",
        "This GitHub issue is already linked to a task in this project.",
    )

    def create_github_issue(self):
        for task in self:
            if "dev" in task.tag_ids.mapped("name"):
                # Placeholder logic — replace with real GitHub call
                return {
                    "title": task.name,
                    "body": f"Task ID: {task.id}",
                    "labels": task.tag_ids.mapped("name"),
                }
        return None
