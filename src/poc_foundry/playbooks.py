"""Two-tier playbooks + the experience loop (design §5.9).

PURE module (stdlib only → ``py_compile``-able + import-light on the 3.10 box). Loads the curated,
role-scoped playbooks under ``playbooks/`` and the low-authority EXPIRING auto-hints under
``playbooks/hints/``, and composes the per-role injection section that ``prompts.py`` / ``coder.py``
append into an agent's prompt BODY — with the role's hard-rule / output-format suffix kept LAST, so
playbook / hint text can never displace the code-appended rules.

Tier 2 (curated) = the tracked ``*.md`` files, full authority. Tier 1 (auto) = ``hints/*.md`` written
by the emit step from a build's interrogation lessons: gitignored, expiring, size-capped, injected
with an explicit "unverified hint" frame. Promotion (Tier-1 → Tier-2) is a human merge (out of scope).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Module-level dirs (env-overridable; also monkeypatched in tests). PF_HINTS_DIR lets the local fakes
# runner + server keep auto-hints out of the tracked tree.
PLAYBOOKS_DIR = Path(os.environ.get("PF_PLAYBOOKS_DIR", "").strip() or (_REPO_ROOT / "playbooks"))
HINTS_DIR = Path(os.environ.get("PF_HINTS_DIR", "").strip() or (PLAYBOOKS_DIR / "hints"))

# role → curated playbook names (file stems under PLAYBOOKS_DIR).
ROLE_PLAYBOOKS: dict[str, list[str]] = {
    "architect": ["building"],
    "tester": ["testing"],
    "coder": ["building", "gotchas"],
    "research": ["research"],
}

PLAYBOOK_BUDGET_CHARS = 2400   # per-role total injected budget (≈600 tokens); truncate over this
HINT_MAX_CHARS = 600           # a hint body over this is skipped (read) / truncated (write)
HINT_LIFETIME_DAYS = 30        # auto-hints expire this many days after they're written


# ── curated playbooks ─────────────────────────────────────────────────────────
def load_playbook(name: str, *, playbooks_dir: Path | None = None) -> str:
    """Return the body of ``<name>.md`` (frontmatter/title left intact), or '' if absent/unreadable."""
    p = Path(playbooks_dir or PLAYBOOKS_DIR) / f"{name}.md"
    try:
        return p.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return ""


# ── auto-hints (Tier 1) ────────────────────────────────────────────────────────
@dataclass
class Hint:
    body: str
    applies_to: list[str] = field(default_factory=list)
    expires: date | None = None
    source_build: str = ""
    written: date | None = None

    @property
    def size(self) -> int:
        return len(self.body)


def _parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


def parse_hint(text: str) -> Hint:
    """Parse a hint file (optional ``---`` frontmatter + body). Tolerant: a body-only file is valid."""
    meta: dict[str, str] = {}
    body = text
    if text.lstrip().startswith("---"):
        rest = text.lstrip()[3:]
        end = rest.find("\n---")
        if end != -1:
            for line in rest[:end].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip().lower()] = v.strip()
            body = rest[end + 4:].lstrip("\n")
    applies = [x.strip() for x in meta.get("applies_to", "").split(",") if x.strip()]
    return Hint(body=body.strip(), applies_to=applies,
                expires=_parse_date(meta.get("expires", "")),
                source_build=meta.get("source_build", ""),
                written=_parse_date(meta.get("date", "")))


def load_hints(role: str, applies_names: list[str], *, now: date | None = None,
               hints_dir: Path | None = None) -> list[Hint]:
    """Non-expired, non-oversized hints whose ``applies_to`` pins intersect the role name OR its
    playbook names. An unpinned hint (no ``applies_to``) is treated as applying to everyone."""
    now = now or date.today()
    hd = Path(hints_dir or HINTS_DIR)
    if not hd.is_dir():
        return []
    targets = {role} | set(applies_names)
    out: list[Hint] = []
    for f in sorted(hd.glob("*.md")):
        if f.name == "README.md":
            continue
        try:
            h = parse_hint(f.read_text())
        except (OSError, UnicodeDecodeError):
            continue
        if not h.body or h.size > HINT_MAX_CHARS:        # oversized → skip
            continue
        if h.expires and h.expires < now:                # expired → skip
            continue
        if h.applies_to and not (set(h.applies_to) & targets):   # pin mismatch → skip
            continue
        out.append(h)
    return out


def write_hint(body: str, *, source_build: str, applies_to: list[str],
               lifetime_days: int = HINT_LIFETIME_DAYS, now: date | None = None,
               hints_dir: Path | None = None) -> Path | None:
    """Write ONE low-authority expiring hint (``<source_build>.md``). Body is size-capped on write so a
    written hint is never skipped as oversized. Caller MUST scrub the body first (rule #1). Returns the
    path, or None for an empty body."""
    body = (body or "").strip()
    if not body:
        return None
    if len(body) > HINT_MAX_CHARS:
        marker = "\n…(truncated)"
        body = body[:HINT_MAX_CHARS - len(marker)].rstrip() + marker   # final body stays ≤ cap (readable back)
    now = now or date.today()
    expires = now + timedelta(days=lifetime_days)
    hd = Path(hints_dir or HINTS_DIR)
    hd.mkdir(parents=True, exist_ok=True)
    fm = (f"---\ndate: {now.isoformat()}\nsource_build: {source_build}\n"
          f"applies_to: {', '.join(applies_to)}\nexpires: {expires.isoformat()}\n---\n")
    path = hd / f"{source_build}.md"
    path.write_text(fm + body + "\n")
    return path


# ── composition (the injection seam) ───────────────────────────────────────────
def playbook_section(role: str, *, now: date | None = None,
                     budget_chars: int = PLAYBOOK_BUDGET_CHARS,
                     playbooks_dir: Path | None = None, hints_dir: Path | None = None) -> str:
    """The injectable section for ``role``: curated playbook bodies + matching non-expired hints
    (framed low-authority), capped to ``budget_chars``. Returns '' when there's nothing to inject."""
    names = ROLE_PLAYBOOKS.get(role, [])
    parts: list[str] = []
    for name in names:
        body = load_playbook(name, playbooks_dir=playbooks_dir)
        if body:
            parts.append(body)
    for h in load_hints(role, names, now=now, hints_dir=hints_dir):
        exp = f" (expires {h.expires.isoformat()})" if h.expires else ""
        parts.append(f"> **Unverified hint (low authority{exp}):**\n> " + h.body.replace("\n", "\n> "))
    if not parts:
        return ""
    section = "## Playbook (guidance — the output-format / hard rules below still apply)\n\n" + \
              "\n\n".join(parts)
    if len(section) > budget_chars:
        section = section[:budget_chars].rstrip() + "\n…(playbook truncated)"
    return section


def compose(body: str, role: str, suffix: str = "", *, now: date | None = None,
            playbooks_dir: Path | None = None, hints_dir: Path | None = None) -> str:
    """body → playbook section → suffix (suffix LAST, so its hard rules can't be displaced)."""
    section = playbook_section(role, now=now, playbooks_dir=playbooks_dir, hints_dir=hints_dir)
    parts = [p for p in (body.rstrip(), section, suffix.strip()) if p]
    return "\n\n".join(parts)
