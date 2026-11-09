"""SQLite store for raw mined data. Everything is resumable, so an interrupted run never costs API quota twice."""
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS repo_state (
    repo TEXT PRIMARY KEY, cursor TEXT, prs_seen INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prs (
    repo TEXT, number INTEGER, title TEXT, merged_at TEXT, base_oid TEXT, head_oid TEXT, author TEXT,
    threads_truncated INTEGER NOT NULL,
    PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS threads (
    root_id INTEGER PRIMARY KEY, repo TEXT, number INTEGER, path TEXT, diff_side TEXT, line INTEGER,
    original_line INTEGER, original_start_line INTEGER, is_resolved INTEGER, is_outdated INTEGER,
    author TEXT, author_type TEXT, created_at TEXT, body TEXT, diff_hunk TEXT, original_commit TEXT,
    replies_json TEXT
);
CREATE INDEX IF NOT EXISTS threads_pr ON threads (repo, number);
CREATE TABLE IF NOT EXISTS compares (
    repo TEXT, base TEXT, head TEXT, status TEXT NOT NULL, files_json TEXT,
    PRIMARY KEY (repo, base, head)
);
"""

UNAVAILABLE = "unavailable"


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def repo_state(self, repo):
        row = self.db.execute("SELECT cursor, prs_seen, done FROM repo_state WHERE repo = ?", (repo,)).fetchone()
        return (row["cursor"], row["prs_seen"], bool(row["done"])) if row else (None, 0, False)

    def save_page(self, repo, prs, threads, cursor, prs_seen, done):
        """Write a page and its cursor in one transaction, so a crash cannot skip or half-store a page."""
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO prs VALUES "
                "(:repo, :number, :title, :merged_at, :base_oid, :head_oid, :author, :threads_truncated)",
                prs,
            )
            self.db.executemany(
                "INSERT OR REPLACE INTO threads VALUES (:root_id, :repo, :number, :path, :diff_side, :line, "
                ":original_line, :original_start_line, :is_resolved, :is_outdated, :author, :author_type, "
                ":created_at, :body, :diff_hunk, :original_commit, :replies_json)",
                threads,
            )
            self.db.execute(
                "INSERT OR REPLACE INTO repo_state (repo, cursor, prs_seen, done) VALUES (?, ?, ?, ?)",
                (repo, cursor, prs_seen, int(done)),
            )

    def prs(self):
        return self.db.execute("SELECT * FROM prs ORDER BY repo, number").fetchall()

    def threads_for(self, repo, number):
        return self.db.execute(
            "SELECT * FROM threads WHERE repo = ? AND number = ? ORDER BY root_id", (repo, number)
        ).fetchall()

    def get_compare(self, repo, base, head):
        row = self.db.execute(
            "SELECT status, files_json FROM compares WHERE repo = ? AND base = ? AND head = ?", (repo, base, head)
        ).fetchone()
        if row is None:
            return None
        return {"status": row["status"], "files": json.loads(row["files_json"] or "[]")}

    def put_compare(self, repo, base, head, status, files):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO compares VALUES (?, ?, ?, ?, ?)", (repo, base, head, status, json.dumps(files))
            )
