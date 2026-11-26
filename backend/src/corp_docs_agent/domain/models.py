from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
QuestionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=800)]
NoteText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]
Key = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,79}$")]
VersionRef = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,79}@[a-z0-9][a-z0-9_.-]{0,79}$")]
AccessGroup = Literal["demo", "staff", "quality"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Context(StrictModel):
    legal_entity: ShortText | None = None
    department: ShortText | None = None
    product: ShortText | None = None
    license_type: ShortText | None = None


class Ask(StrictModel):
    question: QuestionText
    as_of: date | None = None
    context: Context = Field(default_factory=Context)
    document_ids: list[str] = Field(default_factory=list, max_length=25)


class FollowUp(StrictModel):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
    context: Context = Field(default_factory=Context)


class Login(StrictModel):
    role: Literal["staff", "quality"] = "staff"
    code: str = Field(default="", max_length=256)


class PracticeReport(StrictModel):
    citation_id: str = Field(min_length=1, max_length=64)
    situation: NoteText
    action_taken: NoteText


class PracticeReview(StrictModel):
    decision: Literal["approve", "dismiss"]


class CaseReview(StrictModel):
    decision: Literal["publish", "dismiss", "withdraw"]
    answer: NoteText | None = None
    question: QuestionText | None = None
    aliases: list[QuestionText] = Field(default_factory=list, max_length=5)
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_publication(self) -> "CaseReview":
        if self.decision == "publish" and (not self.answer or not self.question):
            raise ValueError("Publication requires a question and verified answer")
        return self


class SourceEntry(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    key: Key
    version: Key
    title: ShortText
    kind: Literal["regulations", "product_licensing"]
    status: Literal["published", "unknown", "withdrawn"] = "unknown"
    effective_from: date | None = None
    effective_to: date | None = None
    scope: Context = Field(default_factory=Context)
    groups: list[AccessGroup] = Field(default_factory=lambda: ["staff"], min_length=1, max_length=3)
    supersedes: list[VersionRef] = Field(default_factory=list, max_length=20)
    related_to: list[VersionRef] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_version(self) -> "SourceEntry":
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("The version end date is earlier than its start date")
        reference = f"{self.key}@{self.version}"
        if reference in self.supersedes or reference in self.related_to:
            raise ValueError("A version cannot refer to itself")
        if self.supersedes and (not self.effective_from or self.status != "published"):
            raise ValueError("Supersession requires a published successor with an effective date")
        return self


class Catalog(StrictModel):
    sources: list[SourceEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_versions(self) -> "Catalog":
        versions = {(entry.key, entry.version) for entry in self.sources}
        if len(versions) != len(self.sources):
            raise ValueError("The catalog contains duplicate version identifiers")
        return self
