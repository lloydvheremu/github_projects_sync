"""Small client for the parts of the GitHub REST API that the sync needs."""

from datetime import datetime, timedelta, timezone

import requests

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_TIMEOUT = (5, 30)  # seconds to connect, seconds to read


class GithubError(Exception):
    """A GitHub request failed. The message is safe to show to users."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class GithubRateLimitError(GithubError):
    """GitHub refused the request because a rate limit was hit."""

    def __init__(self, message, status_code=None, reset_at=None):
        super().__init__(message, status_code)
        self.reset_at = reset_at


class GithubClient:
    def __init__(self, token=None, timeout=DEFAULT_TIMEOUT, session=None):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "odoo-helpdesk-mgmt-github",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def iter_issues(self, repo, since=None, state="all"):
        """Yield the issues of ``repo`` (``owner/name``), oldest update first.

        Pull requests are skipped, although the issues endpoint returns them
        too. ``since`` is a naive UTC datetime, as Odoo stores them; only
        issues updated at or after it are returned.
        """
        params = {
            "state": state,
            "sort": "updated",
            "direction": "asc",
            "per_page": 100,
        }
        if since:
            params["since"] = format_github_datetime(since)
        url = f"{API_URL}/repos/{repo}/issues"
        while url:
            response = self._get(url, params)
            for item in response.json():
                if "pull_request" not in item:
                    yield item
            # The "next" link already carries the query string.
            url = response.links.get("next", {}).get("url")
            params = None

    def _get(self, url, params=None):
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.Timeout as error:
            raise GithubError("GitHub did not answer in time.") from error
        except requests.RequestException as error:
            raise GithubError(f"Could not reach GitHub: {error}") from error
        if response.status_code >= 400:
            raise self._error_from_response(response)
        return response

    def _error_from_response(self, response):
        status = response.status_code
        headers = response.headers
        try:
            detail = response.json().get("message") or ""
        except ValueError:
            detail = ""

        rate_limited = status == 429 or (
            status == 403
            and (
                headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers
            )
        )
        if rate_limited:
            reset_at = _rate_limit_reset(headers)
            when = (
                f" Try again after {reset_at:%Y-%m-%d %H:%M} UTC."
                if reset_at
                else " Try again later."
            )
            return GithubRateLimitError(
                f"GitHub rate limit reached.{when}", status, reset_at
            )
        if status == 401:
            return GithubError(
                "GitHub rejected the access token. Check it in Settings.", status
            )
        if status == 404:
            return GithubError(
                "Repository not found, or the access token cannot see it.", status
            )
        message = f"GitHub returned an error ({status})"
        return GithubError(f"{message}: {detail}" if detail else f"{message}.", status)


def format_github_datetime(value):
    """Format a naive UTC datetime the way the GitHub API expects."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_github_datetime(value):
    """Turn a GitHub timestamp into a naive UTC datetime for Odoo."""
    if not value:
        return None
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


def _rate_limit_reset(headers):
    """Return when the rate limit resets, as a naive UTC datetime, if known."""
    # Secondary rate limits send Retry-After, which then takes precedence.
    retry_after = headers.get("retry-after")
    if retry_after and retry_after.isdigit():
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return now + timedelta(seconds=int(retry_after))
    reset = headers.get("x-ratelimit-reset")
    if reset and reset.isdigit():
        return datetime.fromtimestamp(int(reset), timezone.utc).replace(tzinfo=None)
    return None
