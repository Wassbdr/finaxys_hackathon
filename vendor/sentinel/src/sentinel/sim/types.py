"""Shared constants for the simulator: event codes, sides and id strides."""
from __future__ import annotations

NEW, CANCEL, MODIFY, EXEC = 0, 1, 2, 3
EVENT_TYPES = ("NEW", "CANCEL", "MODIFY", "EXEC")

BID, ASK = 1, -1  # side of an order; -side is the opposite side

NS_PER_SEC = 1_000_000_000
NO_QUOTE = 0  # best price reported when a side of the book is empty
NO_EPISODE = -1  # episode tag for normal (non-manipulation) orders
DEPTH_LEVELS = 5  # number of price levels summed into the depth snapshot

ORDER_ID_STRIDE = 1_000_000_000  # order ids are index * stride + counter
TRADER_ID_STRIDE = 1_000  # trader ids are index * stride + local id
EPISODE_ID_STRIDE = 1_000_000  # episode ids are index * stride + counter
