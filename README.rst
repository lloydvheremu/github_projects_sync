====================
GitHub Projects Sync
====================

An Odoo 19 module that keeps Odoo project tasks in sync with the issues of a
GitHub repository.

Status
======

Work in progress. The module installs cleanly, but it does not sync anything
yet. Planned for the first release:

* Link an Odoo project to a GitHub repository (``owner/name``).
* Import GitHub issues as tasks, safe to run repeatedly.
* Map issue state, labels and assignees to task stages, tags and users.
* A "Sync now" button on the project and a scheduled job.
* Offline tests that use mocked GitHub responses.

Installation
============

1. Install the Python dependency: ``pip install requests``.
2. Put this repository in your Odoo addons path. The folder must be named
   ``github_projects_sync``.
3. Update the apps list and install **GitHub Projects Sync**.

Development
===========

Run the linters with `pre-commit <https://pre-commit.com>`_::

    pre-commit install
    pre-commit run --all-files

Run the tests on a throwaway database::

    odoo-bin -d test_github_sync -i github_projects_sync --test-tags /github_projects_sync --stop-after-init

License
=======

LGPL-3. See ``LICENSE``.
