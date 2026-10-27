from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

CONTRACT_VERSION = "integration-v1"
ValueState = Literal["known", "missing", "not_applicable", "not_checked", "calculation_error"]


class DomainError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class QuestionDefinition:
    question_code: str
    questionnaire_version: str
    text: str
    answer_type: Literal["single", "multiple", "number", "text"]
    instruction: str
    recall_window: str
    domain: str
    required: bool
    options: tuple[dict, ...]
    missing_policy: str
    not_applicable_policy: str
    source_version: str


@dataclass(frozen=True)
class AnswerCommand:
    session_id: str
    question_code: str
    expected_revision: int
    client_event_id: str
    state: ValueState
    value: Any


@dataclass(frozen=True)
class ValueEnvelope:
    state: ValueState
    value: Any = None
    provenance_id: str | None = None
    received_at: str | None = None
    version: str | None = None

    def __post_init__(self):
        if self.state not in {"known", "missing", "not_applicable", "not_checked", "calculation_error"}:
            raise ValueError("Unknown state")
        if (self.state == "known") != (self.value is not None):
            raise ValueError("Known requires a value; unknown requires None")


@dataclass(frozen=True)
class AssessmentResult:
    status: Literal["complete", "partial", "insufficient_data", "calculation_error", "not_evaluated"]
    algorithm_version: str
    domains: dict[str, ValueEnvelope] = field(default_factory=dict)
    safety_flags: tuple[dict, ...] = ()
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    schema_version: str = CONTRACT_VERSION
    clinical_action_executed: Literal[False] = False


@dataclass(frozen=True)
class TherapyProposal:
    proposal_id: str
    version: int
    patient_id: int
    assessment_id: int | None
    expected_plan_version: int
    content_versions: tuple[str, ...]
    options: tuple[dict, ...]
    status: Literal["draft", "reviewable", "withdrawn"] = "draft"


@dataclass(frozen=True)
class ConfirmAction:
    proposal_id: str
    proposal_version: int
    expected_plan_version: int
    decision: Literal["confirm", "reject"]
    reason: str
    idempotency_key: str


class TherapyAdapter(Protocol):
    def propose(self, *, actor_id: int, patient_id: int) -> TherapyProposal: ...
    def confirm(self, *, actor_id: int, patient_id: int, command: ConfirmAction) -> int: ...


class UnavailableTherapyAdapter:
    def propose(self, **kwargs):
        raise DomainError("clinical_publication_disabled")

    def confirm(self, **kwargs):
        raise DomainError("clinical_publication_disabled")
