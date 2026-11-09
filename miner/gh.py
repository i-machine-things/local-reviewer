"""Thin wrapper over the gh CLI, so the miner reuses gh's login and never handles a token itself."""
import json
import re
import subprocess
import time
import urllib.parse

PACE_SECONDS = 0.75  # stays under the 5000 requests/hour REST limit
PR_PAGE_PACE_SECONDS = 15  # a page costs about 13 of the 5000 hourly GraphQL points, so this leaves room for other gh use
RATE_LIMIT_WAIT_SECONDS = 600
MAX_RATE_LIMIT_WAITS = 12
RATE_LIMITED = re.compile(r"rate limit|abuse|secondary", re.IGNORECASE)
NOT_AVAILABLE = re.compile(r"HTTP (404|422)")
TRANSIENT = re.compile(
    r"HTTP 5\d\d|HTTP 403|rate limit|abuse|secondary|timeout|timed out|something went wrong|EOF|connection|TLS",
    re.IGNORECASE,
)

PR_QUERY = """
query($owner: String!, $name: String!, $first: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: $first, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title mergedAt baseRefOid headRefOid
        author { login }
        reviewThreads(first: 50) {
          pageInfo { hasNextPage }
          nodes {
            isResolved isOutdated path diffSide line originalLine startLine originalStartLine
            comments(first: 5) {
              nodes {
                databaseId createdAt body diffHunk
                author { __typename login }
                originalCommit { oid }
              }
            }
          }
        }
      }
    }
  }
}
"""


class GhError(RuntimeError):
    pass


class NotAvailable(GhError):
    """The object is gone or GitHub refuses to diff it. Worth recording, because retrying will not help."""


def run(args, retries=6, sleep=time.sleep, runner=subprocess.run):
    """Run `gh api`, backing off on transient failures."""
    sleep(PACE_SECONDS)
    attempt = waits = 0
    while True:
        try:
            proc = runner(["gh", "api", *args], capture_output=True, text=True, errors="replace", timeout=180)
        except subprocess.TimeoutExpired as exc:
            detail, returncode, stdout = f"timeout: {exc}", 1, ""
        else:
            detail, returncode, stdout = proc.stderr, proc.returncode, proc.stdout
        if returncode == 0:
            return stdout
        if NOT_AVAILABLE.search(detail):
            raise NotAvailable(detail.strip())
        output = f"{detail} {stdout}"
        if RATE_LIMITED.search(output) and waits < MAX_RATE_LIMIT_WAITS:
            # The quota refills on a timer, so waiting works and burning the retry budget does not.
            waits += 1
            sleep(RATE_LIMIT_WAIT_SECONDS)
            continue
        if attempt >= retries or not TRANSIENT.search(output):
            raise GhError(detail.strip() or "gh api failed")
        sleep(min(900, 30 * 2**attempt))
        attempt += 1


def fetch_pr_page(repo, first, cursor):
    time.sleep(PR_PAGE_PACE_SECONDS)
    owner, name = repo.split("/", 1)
    args = ["graphql", "-f", f"query={PR_QUERY}", "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"first={first}"]
    if cursor:
        args += ["-f", f"cursor={cursor}"]
    return json.loads(run(args, retries=2))["data"]["repository"]["pullRequests"]


def fetch_compare(repo, base, head):
    """Return {status, files}. Only the fields the build needs are kept, because compare responses are huge."""
    jq = "{status: .status, files: [(.files // [])[] | {filename, patch}]}"
    return json.loads(run([f"repos/{repo}/compare/{base}...{head}", "--jq", jq]))


def fetch_file(repo, oid, path):
    url = f"repos/{repo}/contents/{urllib.parse.quote(path)}?ref={oid}"
    return run([url, "-H", "Accept: application/vnd.github.raw+json"])
