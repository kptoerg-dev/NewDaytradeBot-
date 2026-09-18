from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List

class Side(Enum):
    LONG = 1
    SHORT = -1

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"

class OrderStatus(Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"

class PositionState(Enum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"

@dataclass
class Tick:
    timestamp: float
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float

    def is_valid(self) -> bool:
        if any(v is None for v in [self.bid, self.ask, self.bid_volume, self.ask_volume]): return False
        if self.bid <= 0 or self.ask <= 0: return False
        if self.ask < self.bid: return False
        if self.bid_volume < 0 or self.ask_volume < 0: return False
        return True

    @property
    def microprice(self) -> float:
        total_vol = self.bid_volume + self.ask_volume
        if total_vol == 0: return (self.bid + self.ask) / 2.0
        return (self.bid * self.ask_volume + self.ask * self.bid_volume) / total_vol

@dataclass
class Order:
    order_id: str
    timestamp: float
    side: Side
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: float = 0.0
    reduce_only: bool = False # PHASE 1: Exit darf keine neue Position erzeugen

@dataclass
class Fill:
    order_id: str
    timestamp: float
    side: Side
    quantity: float
    price: float
    fee: float
    slippage: float

@dataclass
class Position:
    symbol: str
    side: Side
    quantity: float
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    state: PositionState = PositionState.OPEN
    realized_pnl: float = 0.0
