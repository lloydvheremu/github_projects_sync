"""Helpers that build fake GitHub API responses for offline tests."""

import json
from unittest.mock import MagicMock

import requests
from requests.structures import CaseInsensitiveDict

from odoo.addons.helpdesk_mgmt_github.github_client import API_URL


def make_response(status=200, body=None, headers=None, url=API_URL):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.headers = CaseInsensitiveDict(headers or {})
    response._content = b"" if body is None else json.dumps(body).encode()
    return response


def make_issue(number, **values):
    issue = {
        "id": 3_000_000_000 + number,
        "number": number,
        "title": f"Issue {number}",
        "body": f"Body of issue {number}",
        "state": "open",
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "labels": [],
        "assignees": [],
        "updated_at": "2026-10-01T12:00:00Z",
    }
    issue.update(values)
    return issue


def make_pull_request(number):
    return make_issue(
        number,
        pull_request={"url": f"{API_URL}/repos/owner/repo/pulls/{number}"},
    )


def fake_session(*responses):
    """A session whose get() returns ``responses`` in order (or raises them)."""
    session = MagicMock(spec=requests.Session)
    session.headers = CaseInsensitiveDict()
    session.get.side_effect = list(responses)
    return session
