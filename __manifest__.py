{
    "name": "GitHub Projects Sync",
    "summary": "Sync GitHub issues with Odoo project tasks",
    "author": "Lloyd Vheremu",
    "website": "https://github.com/lloydvheremu/github_projects_sync",
    "license": "LGPL-3",
    "category": "Services/Project",
    "version": "19.0.1.0.0",
    "depends": ["project"],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [
        "data/ir_cron_data.xml",
        "views/project_project_views.xml",
        "views/project_task_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
    ],
}
