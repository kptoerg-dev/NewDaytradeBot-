import unittest
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

# ==========================================
# 1. ENUMS & DATACLASSES
# ==========================================

class Direction(Enum):
    LONG = 1
    SHORT = -1

class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"

class OrderStatus(Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"

class PositionState(Enum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"

@dataclass
class Tick:
    timestamp: int
    bid: float
    ask: float
    bid_vol: float
    ask_vol: float

    def is_valid(self) -> bool:
        return self.bid > 0 and self.ask >= self.bid and self.bid_vol >= 0 and self.ask_vol >= 0

@dataclass
class Order:
    order_id: int
    direction: Direction
    order_type: OrderType
    quantity: float
    price: float = 0.0          
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    reduce_only: bool = False

@dataclass
class Fill:
    order_id: int
    direction: Direction
    fill_price: float
    quantity: float
    timestamp: int

class Position:
    def __init__(self, direction: Direction, entry_price: float, quantity: float, sl_price: float = None, tp_price: float = None):
        self.direction = direction
        self.entry_price = entry_price
        self.quantity = quantity
        self.sl_price = sl_price
        self.tp_price = tp_price
        self.state = PositionState.OPEN
        self.realized_pnl = 0.0

# ==========================================
# 2. CORE COMPONENTS
# ==========================================

class RiskManager:
    @staticmethod
    def calculate_position_size(equity: float, risk_per_trade: float, entry_price: float, sl_price: float, contract_multiplier: float = 1.0) -> float:
        if equity <= 0 or entry_price <= 0 or sl_price <= 0 or entry_price == sl_price:
            return 0.0
            
        risk_capital = equity * risk_per_trade
        risk_per_unit = abs(entry_price - sl_price) * contract_multiplier
        
        if risk_per_unit == 0: return 0.0
            
        position_size = risk_capital / risk_per_unit
        if math.isnan(position_size) or math.isinf(position_size): return 0.0
            
        return math.floor(position_size)

class ExecutionHandler:
    def __init__(self, slippage_model: float = 0.1):
        self.slippage_model = slippage_model
        
    def process_orders(self, orders: List[Order], tick: Tick) -> List[Fill]:
        fills = []
        # Phase 9: Liquidität verbleibt pro Tick
        avail_bid = tick.bid_vol
        avail_ask = tick.ask_vol

        for order in orders:
            if order.status in (OrderStatus.FILLED, OrderStatus.CANCELED):
                continue
                
            remaining_qty = order.quantity - order.filled_quantity
            fill_price = 0.0
            fill_qty = 0.0

            if order.order_type == OrderType.MARKET:
                if order.direction == Direction.LONG and avail_ask > 0:
                    fill_price = tick.ask + self.slippage_model
                    fill_qty = min(remaining_qty, avail_ask)
                    avail_ask -= fill_qty
                elif order.direction == Direction.SHORT and avail_bid > 0:
                    fill_price = tick.bid - self.slippage_model
                    fill_qty = min(remaining_qty, avail_bid)
                    avail_bid -= fill_qty

            # (Hier würden analog LIMIT und STOP Executions integriert werden)
                    
            if fill_qty > 0:
                fills.append(Fill(order.order_id, order.direction, fill_price, fill_qty, tick.timestamp))
                order.filled_quantity += fill_qty
                order.status = OrderStatus.FILLED if order.filled_quantity >= order.quantity else OrderStatus.PARTIAL
                
        return fills

class BacktestEngine:
    def __init__(self, initial_equity: float = 10000.0, risk_per_trade: float = 0.01):
        self.initial_equity = initial_equity
        self.cash = initial_equity
        self.equity = initial_equity
        self.risk_per_trade = risk_per_trade
        self.positions: List[Position] = []
        self.active_orders: List[Order] = []
        self.trade_ledger: List[dict] = []
        self.order_counter = 0
        self.execution_handler = ExecutionHandler(slippage_model=0.5)

    def on_tick(self, tick: Tick):
        if not tick.is_valid(): return

        # 1. Mark-to-Market (Phase 4 & 5)
        self._update_equity(tick)
        
        # 2. Execution (Phase 9 & 10)
        new_fills = self.execution_handler.process_orders(self.active_orders, tick)
        self.active_orders = [o for o in self.active_orders if o.status not in (OrderStatus.FILLED, OrderStatus.CANCELED)]
        
        # 3. Portfolio & Ledger Update (Phase 1 & 3)
        for fill in new_fills:
            self._handle_fill(fill)
            
        # 4. SL/TP Logic (Phase 6)
        self._check_stops(tick)

    def enter_trade(self, direction: Direction, current_price: float, sl_distance: float, tp_distance: float):
        """ Phase 2: Strikte Risk-Manager Nutzung """
        sl_price = current_price - sl_distance if direction == Direction.LONG else current_price + sl_distance
        
        qty = RiskManager.calculate_position_size(self.equity, self.risk_per_trade, current_price, sl_price)
        if qty <= 0: return

        # SL/TP temporär in der Engine merken, um sie dem Fill anzuhängen
        self._pending_sl = sl_price
        self._pending_tp = current_price + tp_distance if direction == Direction.LONG else current_price - tp_distance
        
        self.send_order(direction, OrderType.MARKET, qty)

    def _handle_fill(self, fill: Fill):
        # Position Exit / Flip Logik (Phase 1)
        if not self.positions:
            pos = Position(fill.direction, fill.fill_price, fill.quantity, getattr(self, '_pending_sl', None), getattr(self, '_pending_tp', None))
            self.positions.append(pos)
            return

        pos = self.positions[0] # Single Asset Annahme
        
        if pos.direction == fill.direction:
            # Phase 10: Partial Fill Aggregation
            total_qty = pos.quantity + fill.quantity
            pos.entry_price = ((pos.entry_price * pos.quantity) + (fill.fill_price * fill.quantity)) / total_qty
            pos.quantity = total_qty
        else:
            # Phase 1 & 3: Position Reduzierung & Realized PnL
            close_qty = min(pos.quantity, fill.quantity)
            price_diff = (fill.fill_price - pos.entry_price) if pos.direction == Direction.LONG else (pos.entry_price - fill.fill_price)
            
            realized = price_diff * close_qty
            pos.realized_pnl += realized
            self.cash += realized
            pos.quantity -= close_qty
            
            self.trade_ledger.append({"time": fill.timestamp, "pnl": realized, "qty": close_qty})
            
            if pos.quantity <= 1e-8:
                pos.state = PositionState.CLOSED
                self.positions.remove(pos)
                
            # Restmenge -> Echter Flip
            remaining = fill.quantity - close_qty
            if remaining > 1e-8:
                self.positions.append(Position(fill.direction, fill.fill_price, remaining))

    def _check_stops(self, tick: Tick):
        for pos in self.positions:
            if pos.state in (PositionState.CLOSING, PositionState.CLOSED):
                continue
                
            trigger_close = False
            
            # Phase 6: SL UND TP Implementierung
            if pos.direction == Direction.LONG:
                if pos.sl_price and tick.bid <= pos.sl_price: trigger_close = True
                if pos.tp_price and tick.bid >= pos.tp_price: trigger_close = True
            else:
                if pos.sl_price and tick.ask >= pos.sl_price: trigger_close = True
                if pos.tp_price and tick.ask <= pos.tp_price: trigger_close = True
                    
            if trigger_close:
                close_dir = Direction.SHORT if pos.direction == Direction.LONG else Direction.LONG
                self.send_order(close_dir, OrderType.MARKET, pos.quantity, reduce_only=True)
                pos.state = PositionState.CLOSING # Phase 3: Erst CLOSING, CLOSED erst nach echtem Fill

    def _update_equity(self, tick: Tick):
        unrealized = 0.0
        for pos in self.positions:
            if pos.state == PositionState.OPEN:
                current_price = tick.bid if pos.direction == Direction.LONG else tick.ask
                diff = (current_price - pos.entry_price) if pos.direction == Direction.LONG else (pos.entry_price - current_price)
                unrealized += diff * pos.quantity
        self.equity = self.cash + unrealized

    def send_order(self, direction: Direction, order_type: OrderType, quantity: float, reduce_only: bool = False) -> int:
        self.order_counter += 1
        order = Order(self.order_counter, direction, order_type, quantity, reduce_only=reduce_only)
        self.active_orders.append(order)
        return self.order_counter


# ==========================================
# 3. UNIT TESTS (VALIDIERUNG)
# ==========================================

class TestTradingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = BacktestEngine(initial_equity=10000.0)
        self.engine.execution_handler.slippage_model = 0.5
        
    def test_phase_1_exit_does_not_flip(self):
        """ Prüft, ob ein Exit die Position sauber schließt und PnL bucht """
        # LONG 10 @ 100.5 (mit Slippage)
        self.engine.active_orders.append(Order(1, Direction.LONG, OrderType.MARKET, 10.0))
        self.engine.on_tick(Tick(1, 100.0, 100.0, 100, 100))
        
        # MARKET SELL 10 @ 109.5 (mit Slippage)
        self.engine.active_orders.append(Order(2, Direction.SHORT, OrderType.MARKET, 10.0, reduce_only=True))
        self.engine.on_tick(Tick(2, 110.0, 110.0, 100, 100))
        
        self.assertEqual(len(self.engine.positions), 0) # Position MUSS komplett gelöscht sein
        self.assertEqual(len(self.engine.trade_ledger), 1)
        # Entry 100.5, Exit 109.5 -> Diff 9.0 * 10 = 90.0 Realized PnL
        self.assertEqual(self.engine.trade_ledger[0]['pnl'], 90.0)
        self.assertEqual(self.engine.cash, 10090.0)

    def test_phase_2_position_sizing_integrated(self):
        """ Risk Manager wird nun von der Engine erzwungen """
        self.engine.execution_handler.slippage_model = 0.0
        # Equity = 10000, Risk = 1% (100). SL Distanz = 2. -> Size = 50 Lots
        self.engine.enter_trade(Direction.LONG, current_price=100.0, sl_distance=2.0, tp_distance=5.0)
        
        tick = Tick(1, 100.0, 100.0, 100.0, 100.0)
        self.engine.on_tick(tick)
        
        self.assertEqual(self.engine.positions[0].quantity, 50.0)

    def test_phase_4_equity_and_unrealized_pnl(self):
        """ Mark-to-Market verbucht Unrealized PnL """
        self.engine.execution_handler.slippage_model = 0.0
        self.engine.active_orders.append(Order(1, Direction.LONG, OrderType.MARKET, 10.0))
        self.engine.on_tick(Tick(1, 100.0, 100.0, 100, 100))
        
        # Markt steigt auf 110
        self.engine.on_tick(Tick(2, 110.0, 111.0, 100, 100))
        
        # 10 Lots * (Bid 110 - Entry 100) = +100 Unrealized. Cash = 10k.
        self.assertEqual(self.engine.equity, 10100.0)

    def test_phase_9_liquidity_consumption(self):
        """ Orderbuch Volumen wird abgezogen """
        self.engine.execution_handler.slippage_model = 0.0
        self.engine.active_orders.append(Order(1, Direction.LONG, OrderType.MARKET, 5.0))
        self.engine.active_orders.append(Order(2, Direction.LONG, OrderType.MARKET, 5.0))
        
        # Nur 7 Lots vorhanden!
        self.engine.on_tick(Tick(1, 100.0, 100.0, 10, 7.0))
        
        self.assertEqual(self.engine.positions[0].quantity, 7.0)
        self.assertEqual(self.engine.active_orders[0].status, OrderStatus.PARTIAL)
        self.assertEqual(self.engine.active_orders[0].filled_quantity, 2.0) # Rest der zweiten Order

if __name__ == '__main__':
    unittest.main(verbosity=2)
