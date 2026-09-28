"""Application configuration.

All financial-control thresholds live here so they are explicit, reviewable and
configurable via environment variables (see .env.example). Nothing in this file
is decided by the AI layer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def _load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Tolerances:
    """PO matching tolerances. A price variance must be within BOTH limits."""

    quantity: Decimal = Decimal("0")
    price_amount: Decimal = Decimal("25.00")
    price_percent: Decimal = Decimal("5.0")


@dataclass(frozen=True)
class ConfidenceThresholds:
    """AI coding confidence bands.

    >= straight_through      -> eligible for straight-through processing
    review <= c < straight   -> human review (AP Review Queue)
    < review                 -> LOW_CODING_CONFIDENCE exception
    """

    straight_through: float = 0.90
    review: float = 0.75


@dataclass(frozen=True)
class ApprovalThresholds:
    auto_approval_limit: Decimal = Decimal("1000.00")
    controller_threshold: Decimal = Decimal("10000.00")


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///ap_demo.db"
    llm_provider: str = "mock"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    tolerances: Tolerances = field(default_factory=Tolerances)
    confidence: ConfidenceThresholds = field(default_factory=ConfidenceThresholds)
    approvals: ApprovalThresholds = field(default_factory=ApprovalThresholds)
    duplicate_window_days: int = 60


def load_settings() -> Settings:
    _load_dotenv()
    return Settings(
        database_url=_env("AP_DATABASE_URL", f"sqlite:///{PROJECT_ROOT / 'ap_demo.db'}"),
        llm_provider=_env("AP_LLM_PROVIDER", "mock").lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
        tolerances=Tolerances(
            quantity=Decimal(_env("AP_QUANTITY_TOLERANCE", "0")),
            price_amount=Decimal(_env("AP_PRICE_TOLERANCE_AMOUNT", "25.00")),
            price_percent=Decimal(_env("AP_PRICE_TOLERANCE_PERCENT", "5.0")),
        ),
        confidence=ConfidenceThresholds(
            straight_through=float(_env("AP_CONFIDENCE_STRAIGHT_THROUGH", "0.90")),
            review=float(_env("AP_CONFIDENCE_REVIEW", "0.75")),
        ),
        approvals=ApprovalThresholds(
            auto_approval_limit=Decimal(_env("AP_AUTO_APPROVAL_LIMIT", "1000.00")),
            controller_threshold=Decimal(_env("AP_CONTROLLER_THRESHOLD", "10000.00")),
        ),
        duplicate_window_days=int(_env("AP_DUPLICATE_WINDOW_DAYS", "60")),
    )
