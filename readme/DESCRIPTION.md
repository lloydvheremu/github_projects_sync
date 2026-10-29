This module imports the issues of a GitHub repository as tickets of the
OCA [Helpdesk Management](https://github.com/OCA/helpdesk/tree/19.0/helpdesk_mgmt)
module (`helpdesk.ticket`), and keeps them up to date with a scheduled job.

A Helpdesk team is linked to one GitHub repository. Every issue becomes a
ticket of that team and is updated on later runs. Pull requests are skipped.

| GitHub | Helpdesk ticket |
| --- | --- |
| Title | Title |
| Body | Description (plain text). A placeholder is used when the body is empty, because the description is required |
| Labels | Tags (reused by name, created when missing) |
| Assignees | Assigned user: the first assignee whose GitHub Login matches a user allowed on the team |
| Open / reopened | The team's first open stage, or the stage chosen on the team |
| Closed | The team's first closed stage, or the stage chosen on the team |
| Number, URL, state | Read-only GitHub tab on the ticket |

GitHub is the source of truth: changes made in Odoo to the title,
description, tags and assignee are overwritten the next time the issue
changes on GitHub. Creating tickets does not notify the assignee.
