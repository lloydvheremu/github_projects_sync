from odoo import models

class ProjectTask(models.Model):
    _inherit = 'project.task'

    def create_github_issue(self):
        for task in self:
            if 'dev' in task.tag_ids.mapped('name'):
                # Placeholder logic — replace with real GitHub call
                return {
                    'title': task.name,
                    'body': f"Task ID: {task.id}",
                    'labels': task.tag_ids.mapped('name')
                }
        return None
