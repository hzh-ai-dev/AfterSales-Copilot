from datetime import datetime, timedelta, timezone

import pytest

from backend.policy import evaluate


def state(days, intent="refund", defect=True):
    at = datetime(2026, 10, 26, tzinfo=timezone.utc)
    return {
        "evaluation_time": at.isoformat(),
        "collected_information": {"user_intent": intent, "defect": defect, "dispute": False},
        "tool_results": [
            {
                "kind": "order",
                "data": {
                    "delivered_at": (at - timedelta(days=days)).isoformat(),
                    "warranty_until": (at + timedelta(days=300)).isoformat(),
                    "status": "delivered",
                    "after_sales_status": "none",
                    "amount_cents": 10000,
                },
            },
            {
                "kind": "policy",
                "data": [
                    {
                        "id": "P",
                        "version": "v1",
                        "rules": {
                            "refund_days": 7,
                            "defect_exchange_days": 30,
                            "auto_refund_cents": 20000,
                        },
                    }
                ],
            },
        ],
    }


@pytest.mark.parametrize("days,expected", [(7, True), (7.0001, False), (-1, False)])
def test_refund_boundary(days, expected):
    assert evaluate(state(days))["allowed"] is expected


@pytest.mark.parametrize("days,expected", [(30, True), (30.0001, False)])
def test_exchange_boundary(days, expected):
    assert evaluate(state(days, "exchange"))["allowed"] is expected


def test_nondefect_exchange_not_granted():
    assert evaluate(state(3, "exchange", defect=False))["allowed"] is False
