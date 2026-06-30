"""core — the EDITABLE glue for the tool-calling chatbot. The tool primitives live in ``toolkit`` (a
library you import, NOT edit). Your job: implement ``generate_reply`` by COMPOSING them. Do not
reimplement them, invent prices/SKUs, or remove these imports.

Available from ``toolkit`` (see that module's docstrings for details):
  • ``call_tool(product) -> {"product","sku","price_usd","found"}`` — the PRIVATE catalogue lookup via
    the real tool sibling; the price/sku are values you can ONLY get by calling it.
  • ``llm(prompt, system=None) -> str`` — a real model completion (non-deterministic wording).
  • ``CATALOG_PRODUCTS`` — the product names the tool knows.
"""
from __future__ import annotations

from toolkit import CATALOG_PRODUCTS, call_tool, llm   # the provided primitives — use these, don't reinvent


def generate_reply(message: str, history: list | None = None) -> str:
    """SCAFFOLD STUB — implement a tool-calling reply HERE by composing the toolkit primitives.

    A correct reply should: work out which catalogue product the user is asking about, CALL THE TOOL
    (``call_tool``) to get its real price/sku, and answer using that structured result (you may use
    ``llm`` to phrase the answer); for a product NOT in the catalogue, return a no-match reply. Do NOT
    invent prices — only ``call_tool`` yields them, so a reply that states a real price proves the tool
    was actually called. This stub does none of that, so a real criterion test is RED first.
    """
    return "not implemented"
