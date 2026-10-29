from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    github_login = fields.Char(
        string="GitHub Login",
        copy=False,
        help="Used to assign synced GitHub issues to this user.",
    )

    _github_login_unique = models.UniqueIndex(
        "(lower(github_login)) WHERE github_login IS NOT NULL",
        "Another user already has this GitHub login.",
    )
