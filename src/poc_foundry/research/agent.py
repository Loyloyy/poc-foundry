"""The research-on-gaps runner (design §5.3 P4.a, §5.8).

A **bespoke single-pass** loop: search → fetch a few allowlisted result pages → ONE model call to
synthesize a CITATION-ONLY structured ``research.md`` from the fetched excerpts. Bespoke (not the
deepagents multi-turn agent) by the same reasoning that won the coder seat (M0(b)/DECISIONS #8):
reliable, fakes-testable, and a single model call keeps the budget meter EXACT (the memo's flagged
deepagents-undercount problem simply doesn't arise). The engine sits behind ``run_research`` with
injectable ``llm``/``search_fn``/``fetch_fn`` so a deepagents engine can slot into the same seam later
without touching callers.

Containment (defense-in-depth, design §5.2): the coder never sees raw fetched HTML — only this small,
audited, citation-only file (the air-gap); the synthesis prompt tells the model the excerpts are
untrusted DATA; a deterministic tripwire flags injection markers → security.incidents[]. The
downstream gates (red-first, diff-scanner, ledger, critic, the per-build allowlist) remain the wall.
Tolerated-absent: SearXNG down / no hosts fetchable / model unreachable → a recorded note, never a crash.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from poc_foundry.research import tools

_MAX_SNIPPET_CHARS = 1500


@dataclass
class ResearchResult:
    ran: bool = False                       # the rung actually executed (searched)
    markdown: str = ""                      # the research.md content (empty if nothing to write)
    citations: list[str] = field(default_factory=list)
    injection_hits: list[str] = field(default_factory=list)
    calls: int = 0                          # model calls spent (synthesis)
    note: str = ""


def _dedup(seq: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for s in seq:
        if s:
            seen.setdefault(s, None)
    return list(seen)


def _assemble_md(query: str, kind: str, body: str, snippets: list[dict],
                 citations: list[str], injection: list[str]) -> str:
    label = "error" if kind == "error" else "open question(s)"
    lines = [f"# Research — {label}", "", f"**Query:** {query[:400]}", ""]
    if injection:
        lines += ["> ⚠️ Potential prompt-injection markers were detected in the fetched content; the "
                  "synthesis below treats it as untrusted data. Verify before acting.", ""]
    if body.strip():
        lines += [body.strip(), ""]
    else:
        lines += ["_Synthesis unavailable — raw source excerpts only:_", ""]
        for i, s in enumerate(snippets, 1):
            excerpt = s["text"].replace("\n", " ")[:300]
            lines.append(f"- [{i}] {s.get('title') or s['url']}: {excerpt}")
        lines.append("")
    lines += ["## Sources", ""]
    lines += [f"- [{i}] {u}" for i, u in enumerate(citations, 1)] or ["- (none)"]
    return "\n".join(lines) + "\n"


def run_research(*, query: str, kind: str = "error", allow_hosts: list[str] | None = None,
                 searx_url: str | None = None, max_results: int = 4,
                 llm=None, search_fn=None, fetch_fn=None) -> ResearchResult:
    """Run the targeted lookup. ``kind`` ∈ {"error","questions"}. ``llm(role, prompt, system)`` and
    ``search_fn``/``fetch_fn`` are injectable (tests pass fakes; default = the real tools + chat_text)."""
    search_fn = search_fn or tools.search
    fetch_fn = fetch_fn or tools.fetch
    res = ResearchResult()

    from poc_foundry import tracing
    with tracing.span("research", kind=kind, query=query[:200]) as _sp:
        hits = search_fn(query, max_results=max_results, searx_url=searx_url)
        res.ran = True
        snippets: list[dict] = []
        citations: list[str] = []
        injection: list[str] = []
        for h in hits:
            url = h.get("url", "")
            if not url:
                continue
            with tracing.span("research.fetch", url=url[:200]):
                f = fetch_fn(url, allow_hosts=allow_hosts)
            injection += f.get("injection") or []
            if f.get("ok") and f.get("text"):
                snippets.append({"url": url, "title": h.get("title", ""),
                                 "text": f["text"][:_MAX_SNIPPET_CHARS]})
                citations.append(url)
            elif h.get("content"):     # fetch blocked/failed → fall back to the search snippet
                snippets.append({"url": url, "title": h.get("title", ""), "text": h["content"]})
                citations.append(url)
        res.injection_hits = sorted(set(injection))

        if not snippets:
            res.note = "no fetchable sources (SearXNG down or all hosts blocked/empty)"
            _sp.update(output={"ran": True, "sources": 0, "injection": len(res.injection_hits)})
            return res

        body = ""
        try:
            from poc_foundry import prompts
            if llm is None:
                from poc_foundry.models import chat_text as _ct
                llm = _ct
            body = llm("architect", prompts.research_synthesis_prompt(query, kind, snippets),
                       system=prompts.RESEARCH_SYSTEM)
            res.calls = 1
        except Exception as e:  # noqa: BLE001 — synthesis is best-effort (BudgetExceeded still escapes)
            res.note = f"synthesis unavailable ({type(e).__name__})"

        res.citations = _dedup(citations)
        res.markdown = _assemble_md(query, kind, body, snippets, res.citations, res.injection_hits)
        _sp.update(output={"ran": True, "sources": len(res.citations),
                           "injection": len(res.injection_hits), "calls": res.calls})
    return res
