"""Phần chung của client AI: JSON schema strict từ Pydantic, tên model gateway trả về."""

import pytest
from pydantic import BaseModel

from app.ai.client import served_model, strict_schema
from app.ai.prompts import AnswerOut, QuestionPlan, TranslationOut


def test_strict_schema_closes_objects_and_keeps_fields() -> None:
    # Cùng nội dung với schema gửi đi trước khi bỏ SDK cũ (2026-11-05): gateway nhận như cũ.
    assert strict_schema(AnswerOut) == {
        "type": "object",
        "title": "AnswerOut",
        "properties": {
            "answer": {
                "type": "string",
                "description": "answer in Vietnamese with [n] citations",
                "title": "Answer",
            },
            "cited_sources": {
                "type": "array",
                "description": "ids of the cited sources",
                "title": "Cited Sources",
                "items": {"type": "integer"},
            },
            "insufficient": {
                "type": "boolean",
                "description": "true if the sources are not enough to answer",
                "title": "Insufficient",
            },
        },
        "additionalProperties": False,
        "required": ["answer", "cited_sources", "insufficient"],
    }


def test_strict_schema_nullable_fields() -> None:
    schema = strict_schema(QuestionPlan)
    assert schema["additionalProperties"] is False
    assert schema["required"] == [
        "search_queries",
        "keywords",
        "sender",
        "sent_after",
        "sent_before",
    ]
    assert schema["properties"]["sender"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert strict_schema(TranslationOut)["required"] == ["subject", "body", "notes"]


def test_strict_schema_nested_objects() -> None:
    class Inner(BaseModel):
        x: int

    class Outer(BaseModel):
        inner: Inner
        items: list[Inner]

    schema = strict_schema(Outer)
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Inner"]["additionalProperties"] is False


@pytest.mark.parametrize("served", ["a\nb", "\x1b[2Jm", "x" * 201, "", None, 5])
def test_odd_model_name_falls_back_to_requested(served: object) -> None:
    assert served_model(served, "local/model-1") == "local/model-1"


def test_model_name_of_gateway_is_kept() -> None:
    assert served_model("gw/model-2", "local/model-1") == "gw/model-2"
