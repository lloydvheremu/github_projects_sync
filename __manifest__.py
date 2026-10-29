{
    "name": "Helpdesk GitHub Sync",
    "summary": "Import GitHub issues as Helpdesk tickets",
    "author": "Lloyd Vheremu",
    "website": "https://github.com/lloydvheremu/helpdesk_mgmt_github",
    "license": "LGPL-3",
    "category": "After-Sales",
    "version": "19.0.2.0.0",
    "depends": ["helpdesk_mgmt"],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [
        "data/ir_cron_data.xml",
        "views/helpdesk_ticket_team_views.xml",
        "views/helpdesk_ticket_views.xml",
        "views/res_config_settings_views.xml",
        "views/res_users_views.xml",
    ],
}
