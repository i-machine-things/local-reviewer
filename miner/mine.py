"""Download merged PRs and their review threads into the store."""
import json

from . import gh


def parse_page(repo, nodes):
    """Flatten a GraphQL page into store rows. Threads with no surviving root comment are dropped."""
    prs, threads = [], []
    for node in nodes:
        review_threads = node["reviewThreads"]
        prs.append(
            {
                "repo": repo,
                "number": node["number"],
                "title": node["title"],
                "merged_at": node["mergedAt"],
                "base_oid": node["baseRefOid"],
                "head_oid": node["headRefOid"],
                "author": (node["author"] or {}).get("login"),
                "threads_truncated": int(review_threads["pageInfo"]["hasNextPage"]),
            }
        )
        for thread in review_threads["nodes"]:
            comments = thread["comments"]["nodes"]
            if not comments:
                continue
            root = comments[0]
            author = root["author"] or {}
            threads.append(
                {
                    "root_id": root["databaseId"],
                    "repo": repo,
                    "number": node["number"],
                    "path": thread["path"],
                    "diff_side": thread["diffSide"],
                    "line": thread["line"],
                    "original_line": thread["originalLine"],
                    "original_start_line": thread["originalStartLine"],
                    "is_resolved": int(thread["isResolved"]),
                    "is_outdated": int(thread["isOutdated"]),
                    "author": author.get("login"),
                    "author_type": author.get("__typename"),
                    "created_at": root["createdAt"],
                    "body": root["body"],
                    "diff_hunk": root["diffHunk"],
                    "original_commit": (root["originalCommit"] or {}).get("oid"),
                    "replies_json": json.dumps(
                        [{"author": (c["author"] or {}).get("login"), "body": c["body"]} for c in comments[1:]]
                    ),
                }
            )
    return prs, threads


def mine_repo(store, repo, max_prs, fetch=gh.fetch_pr_page, log=print):
    """Mine until max_prs have been seen. Raising max_prs later continues from the saved cursor."""
    cursor, seen, done = store.repo_state(repo)
    page_size = 25
    while not done and seen < max_prs:
        try:
            page = fetch(repo, min(page_size, max_prs - seen), cursor)
        except gh.GhError:
            # A page of very large PRs can time out server-side. Asking for fewer usually gets through.
            if page_size == 1:
                raise
            page_size = max(1, page_size // 2)
            log(f"{repo}: page failed, retrying with {page_size} PRs per page")
            continue
        page_size = min(25, page_size * 2)  # one oversized PR should not slow every later page
        prs, threads = parse_page(repo, page["nodes"])
        seen += len(prs)
        cursor = page["pageInfo"]["endCursor"] or cursor
        done = not page["pageInfo"]["hasNextPage"]
        store.save_page(repo, prs, threads, cursor, seen, done)
        log(f"{repo}: {seen} PRs")
