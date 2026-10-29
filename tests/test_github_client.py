from datetime import datetime
from unittest.mock import call

import requests

from odoo.tests.common import BaseCase

from odoo.addons.github_projects_sync.github_client import (
    API_URL,
    GithubClient,
    GithubError,
    GithubRateLimitError,
    parse_github_datetime,
)

from .github_mocks import (
    fake_session,
    make_issue,
    make_pull_request,
    make_response,
)

ISSUES_URL = f"{API_URL}/repos/owner/repo/issues"


class TestGithubClient(BaseCase):
    def test_headers_with_token(self):
        session = fake_session()
        GithubClient("secret", session=session)
        self.assertEqual(session.headers["Authorization"], "Bearer secret")
        self.assertEqual(session.headers["Accept"], "application/vnd.github+json")
        self.assertIn("X-GitHub-Api-Version", session.headers)

    def test_no_auth_header_without_token(self):
        session = fake_session()
        GithubClient(None, session=session)
        self.assertNotIn("Authorization", session.headers)

    def test_first_request_parameters_and_timeout(self):
        session = fake_session(make_response(body=[]))
        client = GithubClient("t", session=session, timeout=(1, 2))
        list(client.iter_issues("owner/repo", since=datetime(2026, 10, 1, 8, 30)))
        session.get.assert_called_once_with(
            ISSUES_URL,
            params={
                "state": "all",
                "sort": "updated",
                "direction": "asc",
                "per_page": 100,
                "since": "2026-10-01T08:30:00Z",
            },
            timeout=(1, 2),
        )

    def test_pagination_follows_next_links(self):
        page2 = f"{ISSUES_URL}?page=2"
        page3 = f"{ISSUES_URL}?page=3"
        session = fake_session(
            make_response(
                body=[make_issue(1), make_issue(2)],
                headers={"Link": f'<{page2}>; rel="next", <{page3}>; rel="last"'},
            ),
            make_response(
                body=[make_issue(3)],
                headers={"Link": f'<{page3}>; rel="next", <{page3}>; rel="last"'},
            ),
            make_response(body=[make_issue(4)]),
        )
        issues = list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertEqual([i["number"] for i in issues], [1, 2, 3, 4])
        self.assertEqual(session.get.call_count, 3)
        # Follow-up pages reuse the query string from the link.
        self.assertEqual(
            session.get.call_args_list[1], call(page2, params=None, timeout=(5, 30))
        )

    def test_pull_requests_are_skipped(self):
        session = fake_session(
            make_response(body=[make_issue(1), make_pull_request(2), make_issue(3)])
        )
        issues = list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertEqual([i["number"] for i in issues], [1, 3])

    def test_bad_token(self):
        session = fake_session(make_response(401, {"message": "Bad credentials"}))
        with self.assertRaises(GithubError) as caught:
            list(GithubClient("bad", session=session).iter_issues("owner/repo"))
        self.assertEqual(caught.exception.status_code, 401)
        self.assertIn("access token", str(caught.exception))

    def test_unknown_repository(self):
        session = fake_session(make_response(404, {"message": "Not Found"}))
        with self.assertRaises(GithubError) as caught:
            list(GithubClient("t", session=session).iter_issues("owner/missing"))
        self.assertIn("Repository not found", str(caught.exception))

    def test_primary_rate_limit(self):
        session = fake_session(
            make_response(
                403,
                {"message": "API rate limit exceeded"},
                {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1792065600"},
            )
        )
        with self.assertRaises(GithubRateLimitError) as caught:
            list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertEqual(caught.exception.reset_at, datetime(2026, 10, 15, 12, 0))
        self.assertIn("2026-10-15 12:00 UTC", str(caught.exception))

    def test_secondary_rate_limit_uses_retry_after(self):
        session = fake_session(
            make_response(
                403,
                {"message": "You have exceeded a secondary rate limit"},
                {"retry-after": "60", "x-ratelimit-remaining": "4000"},
            )
        )
        with self.assertRaises(GithubRateLimitError) as caught:
            list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertIsNotNone(caught.exception.reset_at)

    def test_too_many_requests(self):
        session = fake_session(make_response(429))
        with self.assertRaises(GithubRateLimitError) as caught:
            list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertIsNone(caught.exception.reset_at)
        self.assertIn("Try again later", str(caught.exception))

    def test_forbidden_without_rate_limit_is_a_plain_error(self):
        session = fake_session(
            make_response(
                403,
                {"message": "Resource not accessible"},
                {"x-ratelimit-remaining": "4999"},
            )
        )
        with self.assertRaises(GithubError) as caught:
            list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertNotIsInstance(caught.exception, GithubRateLimitError)
        self.assertIn("Resource not accessible", str(caught.exception))

    def test_server_error_without_json_body(self):
        response = make_response(502)
        response._content = b"<html>Bad gateway</html>"
        session = fake_session(response)
        with self.assertRaises(GithubError) as caught:
            list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertEqual(str(caught.exception), "GitHub returned an error (502).")

    def test_timeout(self):
        session = fake_session(requests.Timeout("read timed out"))
        with self.assertRaises(GithubError) as caught:
            list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertIn("did not answer in time", str(caught.exception))

    def test_connection_error(self):
        session = fake_session(requests.ConnectionError("DNS failure"))
        with self.assertRaises(GithubError) as caught:
            list(GithubClient("t", session=session).iter_issues("owner/repo"))
        self.assertIn("Could not reach GitHub", str(caught.exception))

    def test_parse_github_datetime(self):
        self.assertEqual(
            parse_github_datetime("2026-10-01T12:34:56Z"),
            datetime(2026, 10, 1, 12, 34, 56),
        )
        self.assertIsNone(parse_github_datetime(None))
