from odoo.tests.common import TransactionCase


class TestDevIssueSync(TransactionCase):

    def setUp(self):
        super().setUp()
        self.dev_tag = self.env['project.tags'].create({'name': 'dev'})
        self.project = self.env['project.project'].create({'name': 'Test Project'})

    def test_task_tagged_dev_triggers_sync(self):
        task = self.env['project.task'].create({
            'name': 'Trigger test issue',
            'project_id': self.project.id,
            'tag_ids': [(6, 0, [self.dev_tag.id])]
        })

        # Assuming sync is automatic or manually triggered
        issue = task.create_github_issue()  # You’ll write this method

        self.assertIsNotNone(issue)
        self.assertEqual(issue['title'], task.name)
