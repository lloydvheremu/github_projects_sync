from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    github_token = fields.Char(
        string="GitHub Access Token",
        config_parameter="github_projects_sync.token",
        groups="base.group_system",
        help="Personal access token used to read issues. It is stored as a "
        "system parameter that only administrators can read.",
    )
