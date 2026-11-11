from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints, model_validator

Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
MoneyOutput = Annotated[Decimal, PlainSerializer(lambda v: f"{v:.2f}", return_type=str, when_used="json")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecurringCreate(Input):
    title: Title
    anchor_date: date = Field(ge=date(2000, 1, 1), le=date(2200, 12, 31))
    frequency: Literal["days", "weeks", "months", "years"]
    interval: int = Field(default=1, ge=1, le=3660)
    amount: Money
    variable: bool = False


class TariffCreate(Input):
    effective_from: date
    amount: Money


class RecurringPatch(Input):
    title: Title | None = None
    amount: Money | None = None
    variable: bool | None = None
    anchor_date: date | None = Field(default=None, ge=date(2000, 1, 1), le=date(2200, 12, 31))
    frequency: Literal["days", "weeks", "months", "years"] | None = None
    interval: int | None = Field(default=None, ge=1, le=3660)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("Supply non-null fields")
        return self


class BrowserLogin(Input):
    code: str = Field(min_length=1, max_length=80)


class OccurrencePatch(Input):
    expected_amount: Money | None = None
    due_date: date | None = Field(default=None, ge=date(2000, 1, 1), le=date(2200, 12, 31))
    status: Literal["skipped", "cancelled"] | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("Supply at least one non-null field")
        return self


class PaymentCreate(Input):
    paid_on: date | None = Field(default=None, ge=date(2000, 1, 1), le=date(2200, 12, 31))
    amount: Money
    revised_expected_amount: Money | None = None
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class PaymentCorrectionCreate(Input):
    amount: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
    paid_on: date = Field(ge=date(2000, 1, 1), le=date(2200, 12, 31))
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")


class RestoreRequest(Input):
    first_date: date = Field(ge=date(2000, 1, 1), le=date(2200, 12, 31))


class StopRequest(Input):
    cancel_future_unpaid: bool = True


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RecurringOut(Output):
    id: UUID
    owner_id: int
    title: str
    anchor_date: date
    frequency: str
    interval: int
    variable: bool
    active: bool
    deleted_at: datetime | None
    created_at: datetime


class TariffOut(Output):
    id: UUID
    recurring_id: UUID
    effective_from: date
    amount: MoneyOutput


class PaymentOut(Output):
    paid_on: date
    original_amount: MoneyOutput
    original_paid_on: date
    revision: int
    cancelled: bool
    id: UUID
    occurrence_id: UUID
    amount: MoneyOutput
    idempotency_key: str
    created_at: datetime


class NotificationOut(Output):
    id: UUID
    occurrence_id: UUID
    due_snapshot: date
    days_before: int
    scheduled_at: datetime
    status: str
    attempts: int
    next_attempt_at: datetime | None
    sent_at: datetime | None
    last_error: str | None


class AuditOut(Output):
    id: UUID
    entity_id: UUID
    action: str
    details: dict
    created_at: datetime
