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


class QuestionContext(Context):
    departments: list[ShortText] | None = Field(default=None, min_length=1, max_length=5)

    @model_validator(mode="after")
    def normalize_departments(self) -> "QuestionContext":
        if self.departments:
            if self.department:
                raise ValueError("Specify department or departments")
            choices = {" ".join(value.casefold().replace("ё", "е").split()): value for value in self.departments}
            self.departments = [choices[key] for key in sorted(choices)]
            if len(self.departments) == 1:
                self.department, self.departments = self.departments[0], None
        return self

    def stored(self) -> dict:
        return self.model_dump(exclude={"departments"} if self.departments is None else None)


class Ask(StrictModel):
    question: QuestionText
    as_of: date | None = None
    context: QuestionContext = Field(default_factory=QuestionContext)
    document_ids: list[str] = Field(default_factory=list, max_length=25)


class FollowUp(StrictModel):
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
    context: QuestionContext = Field(default_factory=QuestionContext)


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
    in_reply_to: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")] | None = None

    @model_validator(mode="after")
    def validate_publication(self) -> "CaseReview":
        if self.decision == "publish" and (not self.answer or not self.question):
            raise ValueError("Publication requires a question and verified answer")
        return self


class SourceEntry(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    key: Key
    version: Key
    revision_label: ShortText | None = None
    title: ShortText
    kind: Literal["regulations", "product_licensing"]
    status: Literal["published", "unknown", "withdrawn"] = "unknown"
    effective_from: date | None = None
    effective_to: date | None = None
    publication_confirmed_on: date | None = None
    scope: Context = Field(default_factory=Context)
    groups: list[AccessGroup] = Field(default_factory=lambda: ["staff"], min_length=1, max_length=3)
    supersedes: list[VersionRef] = Field(default_factory=list, max_length=20)
    related_to: list[VersionRef] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_version(self) -> "SourceEntry":
        if self.effective_from and self.effective_to and self.effective_from > self.effective_to:
            raise ValueError("The version end date is earlier than its start date")
        if self.publication_confirmed_on:
            if self.status != "published":
                raise ValueError("Current publication confirmation requires published status")
            if ((self.effective_from and self.publication_confirmed_on < self.effective_from)
                    or (self.effective_to and self.publication_confirmed_on > self.effective_to)):
                raise ValueError("Publication confirmation is outside the effective dates")
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
