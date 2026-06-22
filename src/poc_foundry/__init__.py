"""poc-foundry (Stage 3) — turns ONE Stage-2 DeepResearchArtifact into a verified, runnable,
documented PoC. The stable headless contract is ``poc_foundry.core.build_poc`` (lands at M1).

This package is import-light at the top level on purpose (rule #3): heavy deps (langgraph,
langchain, deepagents) are lazy-imported inside the functions that need them, so pure modules stay
``py_compile``-able on the 3.10 dev box. Nothing here imports the agent stack at module load.
"""

__version__ = "0.1.0"
