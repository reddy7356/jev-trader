from __future__ import annotations

import logging
import time
from types import TracebackType

from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from jev_trader.config import Settings
from jev_trader.domain import JudgmentSet
from jev_trader.judgment.battery import build_questions, parse_response
from jev_trader.state.snapshot import MarketState

logger = logging.getLogger(__name__)


class JevClient:
    """Async wrapper around the TypeSafe System One API.

    For HFT use the direct API (no gateways), pin the model version, and log the
    version returned on every response so silent upgrades cannot break calibrated
    thresholds quietly.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.has_api_key:
            raise ValueError("TYPESAFE_API_KEY is required for the live Jev client")
        self._settings = settings
        self._questions = build_questions()
        self._client: AsyncTypeSafeClient | None = None

    async def __aenter__(self) -> JevClient:
        self._client = AsyncTypeSafeClient(
            api_key=self._settings.typesafe_api_key.get_secret_value(),
            model=self._settings.jev_model,
            retry=RetryPolicy(
                max_retries=0,
                timeout=self._settings.decision_timeout_s * 0.8,
            ),
        )
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.__aexit__(exc_type, exc, tb)
            self._client = None

    async def judge(self, state: MarketState) -> JudgmentSet:
        if self._client is None:
            raise RuntimeError("JevClient must be used as an async context manager")
        started = time.perf_counter()
        response = await self._client.system_one(
            state=state.to_state_dict(),
            questions=self._questions,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        if response.model != self._settings.jev_model:
            logger.warning(
                "model mismatch: pinned=%s returned=%s",
                self._settings.jev_model,
                response.model,
            )
        return parse_response(response, latency_ms=latency_ms, source="jev")
