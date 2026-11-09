"""Unified-diff parsing. Line numbers here decide whether a review comment actually led to a fix."""
import difflib
import re
from dataclasses import dataclass

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Hunk:
    old_start: int
    new_start: int
    new_count: int
    text: str
    old_count: int = 1

    @property
    def new_end(self):
        return self.new_start + self.new_count - 1

    @property
    def changed_lines(self):
        return sum(1 for line in self.text.split("\n")[1:] if line[:1] in ("+", "-"))

    def added_new_lines(self):
        """New-file line numbers of the lines this hunk adds."""
        added, current = set(), self.new_start
        for line in self.text.split("\n")[1:]:
            if line.startswith("+"):
                added.add(current)
                current += 1
            elif line.startswith(" "):
                current += 1
        return added

    def touched_old_lines(self):
        """Old-file line numbers this hunk removes, or inserts in front of."""
        # A hunk with no old lines names the line it follows, so its insertions land on the next one.
        touched, current = set(), self.old_start + (self.old_count == 0)
        for line in self.text.split("\n")[1:]:
            if line.startswith("-"):
                touched.add(current)
                current += 1
            elif line.startswith("+"):
                touched.add(current)
            elif line.startswith(" "):
                current += 1
        return touched


def parse_patch(patch):
    if not patch:
        return []
    hunks, header, body = [], None, []

    def flush():
        if header:
            old_count = int(header.group(2)) if header.group(2) is not None else 1
            new_count = int(header.group(4)) if header.group(4) is not None else 1
            hunks.append(Hunk(int(header.group(1)), int(header.group(3)), new_count, "\n".join(body), old_count))

    for line in patch.split("\n"):
        match = HUNK_HEADER.match(line)
        if match:
            flush()
            header, body = match, [line]
        elif header:
            body.append(line)
    flush()
    return hunks


def diff_texts(old, new):
    """Line-level diff of two file versions, in the same format GitHub returns."""
    return "\n".join(difflib.unified_diff(old.split("\n"), new.split("\n"), n=0, lineterm=""))


def hunk_at_new_line(hunks, line):
    return next((h for h in hunks if h.new_start <= line <= h.new_end), None)


def touches_range(hunks, start, end, window=3):
    """True if a later diff changes old-file lines within `window` of start..end.

    The window exists because the fix for "missing check here" usually lands on an adjacent line.
    """
    wanted = range(start - window, end + window + 1)
    return any(old in wanted for hunk in hunks for old in hunk.touched_old_lines())


def overlapping_hunks(hunks, start, end, window=3):
    return [h for h in hunks if any(old in range(start - window, end + window + 1) for old in h.touched_old_lines())]
