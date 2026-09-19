from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from jev_trader.state.events import BlockEvent


class Feed(Protocol):
    def events(self) -> AsyncIterator[BlockEvent]: ...
