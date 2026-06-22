"""The Stage-3 OUTPUT contract: ``PoCBuildArtifact`` (design §4.3) + persistence.

NOT to be confused with ``poc_foundry.stage2_artifact`` (the vendored Stage-2 INPUT schema). Pure
pydantic/stdlib — stays out of the heavy agent stack.
"""
from .schema import (
    Budget,
    CleanroomResult,
    DescopeItem,
    FinalVerdict,
    IterationRecord,
    PoCBuildArtifact,
    SecurityInfo,
    ServiceRef,
    SourceArtifact,
    StackItem,
    SuccessCriterion,
    TemplateRef,
    TemplateRepo,
    TestsSummary,
)
from .store import load, new_build_id, save

__all__ = [
    "PoCBuildArtifact",
    "SourceArtifact",
    "SuccessCriterion",
    "IterationRecord",
    "TestsSummary",
    "CleanroomResult",
    "FinalVerdict",
    "DescopeItem",
    "StackItem",
    "ServiceRef",
    "TemplateRef",
    "TemplateRepo",
    "SecurityInfo",
    "Budget",
    "new_build_id",
    "save",
    "load",
]
