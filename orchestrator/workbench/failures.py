from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PreexistingTask3AuthorityCode = Literal["preexisting_task3_authority_without_events"]


class _FrozenFailureModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class FailureSubject(_FrozenFailureModel):
    kind: Literal["migration"]
    id: Literal["0004"]


class Task3AuthorityRowCount(_FrozenFailureModel):
    table_name: Literal[
        "decision_requests",
        "evidence_refs",
        "frame_edges",
        "frame_nodes",
        "frame_proposals",
        "service_run_inputs",
        "service_runs",
    ]
    row_count: int = Field(gt=0)


class PreexistingTask3AuthorityDetails(_FrozenFailureModel):
    code: PreexistingTask3AuthorityCode
    migration_version: Literal[4]
    row_counts: tuple[Task3AuthorityRowCount, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _row_counts_are_sorted_unique(self) -> PreexistingTask3AuthorityDetails:
        names = tuple(item.table_name for item in self.row_counts)
        if names != tuple(sorted(set(names))):
            raise ValueError("row_counts must be sorted by unique table_name")
        return self


class Task3Failure(_FrozenFailureModel):
    schema_version: Literal[1]
    code: PreexistingTask3AuthorityCode
    task_id: None = None
    branch_id: None = None
    subject: FailureSubject
    details: PreexistingTask3AuthorityDetails
    message: str = Field(min_length=1)

    @model_validator(mode="after")
    def _codes_match(self) -> Task3Failure:
        if self.details.code != self.code:
            raise ValueError("failure detail code must equal top-level code")
        if self.subject != FailureSubject(kind="migration", id="0004"):
            raise ValueError("migration-four precondition failure must identify migration 0004")
        return self


def preexisting_task3_authority_failure(
    row_counts: tuple[Task3AuthorityRowCount, ...],
) -> Task3Failure:
    return Task3Failure(
        schema_version=1,
        code="preexisting_task3_authority_without_events",
        subject=FailureSubject(kind="migration", id="0004"),
        details=PreexistingTask3AuthorityDetails(
            code="preexisting_task3_authority_without_events",
            migration_version=4,
            row_counts=row_counts,
        ),
        message="migration 4 refuses to authenticate preexisting Task-3 authority without events",
    )
