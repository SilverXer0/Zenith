import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    displayName: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8)

    @field_validator("displayName")
    @classmethod
    def trim_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A display name is required.")
        return value.strip()


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)
    project: str | None = Field(default=None, max_length=80)
    priority: Literal["low", "medium", "high"] | None = None
    dueDate: str | None = None
    completed: StrictBool = False

    @field_validator("title", "notes", "project")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("dueDate")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use a due date in YYYY-MM-DD format.")
        date.fromisoformat(value)
        return value


class MemoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=40)

    @field_validator("content", "category")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class AssistantHistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def cap_content(cls, value: str) -> str:
        return value[:2000]


class AssistantChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=4000)
    history: list[AssistantHistoryItem] = Field(default_factory=list)

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("history", mode="before")
    @classmethod
    def cap_history(cls, value):
        return value[-8:] if isinstance(value, list) else value


class AssistantActionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    actions: list[dict[str, Any]] = Field(min_length=1, max_length=5)


class AssistantUnloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str | None = Field(default=None, max_length=120)

    @field_validator("model")
    @classmethod
    def trim_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None
