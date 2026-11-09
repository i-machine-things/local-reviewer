"""Decide from comment text alone whether a review comment is about a defect. Favors precision over recall."""
import re

BOT_LOGINS = re.compile(
    r"(\[bot\]$|^coderabbit|^codecov|^github-actions|^dependabot|^sonarcloud|^copilot)", re.IGNORECASE
)

CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
QUOTED_LINE = re.compile(r"^>.*$", re.MULTILINE)

NOISE_PREFIX = re.compile(r"^\W*(nit|nitpick|typo|minor|style|optional|cosmetic|naming|bikeshed)\b", re.IGNORECASE)
NOISE_ANYWHERE = re.compile(
    r"\b(typos?|docstrings?|whitespace|indentation|formatting|rename|spelling|grammar|changelog|wording|lint|"
    r"nit|code style|readability|more readable|unused import)\b",
    re.IGNORECASE,
)

CATEGORIES = {
    "concurrency": r"race condition|data race|\bracy\b|deadlock|thread[- ]safe|not atomic|lock ordering|"
    r"concurrent(ly)? (access|write|read|modif)",
    "resource": r"\bleak(s|ed|ing)?\b|never (closed|freed|released)|not (closed|freed|released)|unclosed|"
    r"file descriptor|use[- ]after[- ]free|double[- ]free|dangling",
    "memory": r"out[- ]of[- ]bounds|buffer overflow|integer overflow|\boverflows?\b|\bunderflows?\b|uninitiali[sz]ed|"
    r"segfault|null pointer|null deref|nullptr deref|undefined behaviou?r|\bUB\b",
    "error-handling": r"unhandled|swallow(s|ed|ing)? (the )?(error|exception)|error is ignored|ignor(es|ing) the error|"
    r"not checked|doesn'?t check|does not check|will (panic|crash|throw)|can (panic|crash|throw)|"
    r"could (panic|crash|throw)|\bcrash(es)?\b|\bpanics?\b",
    "logic": r"off[- ]by[- ]one|this is (a bug|wrong|incorrect|broken)|\bbug\b|\bbuggy\b|incorrect(ly)?\b|"
    r"always (true|false)|never (true|false|called|reached|run|runs|executed)|infinite loop|regression|"
    r"edge case|corner case|will fail|would fail|won'?t work|wouldn'?t work|doesn'?t work|does not work|"
    r"\bbreaks\b|could be (none|null|nil|empty)|can be (none|null|nil|empty)|might be (none|null|nil|empty)",
    "security": r"injection|vulnerab|path traversal|auth(entication|orization)? bypass|unsanitized|\bXSS\b|\bCSRF\b",
}
COMPILED = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in CATEGORIES.items()}


def is_bot(login, author_type):
    return author_type == "Bot" or bool(login and BOT_LOGINS.search(login))


def prose_of(body):
    """Drop code and quotations, so a keyword inside someone else's words or a snippet does not count."""
    return QUOTED_LINE.sub("", CODE_BLOCK.sub("", body or "")).strip()


def categorize(body):
    """Return defect categories for a comment, or an empty list if it is noise or shows no defect signal."""
    prose = prose_of(body)
    if len(prose) < 20 or NOISE_PREFIX.search(prose) or NOISE_ANYWHERE.search(prose):
        return []
    return [name for name, pattern in COMPILED.items() if pattern.search(prose)]
