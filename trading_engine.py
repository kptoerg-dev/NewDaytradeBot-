import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Optional
import uuid

# ==========================================
# 1. CORE DATATYPES
# ==========================================

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
    reduce_only: bool = False
    symbol: str = "BTCUSD"

@dataclass
class Fill:
    order_id: str
    timestamp: float
    side: Side
    quantity: float
    price: float  
    fee: float
    slippage: float  
    reduce_only: bool 
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    symbol: str = "BTCUSD"

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


# ==========================================
# 2. RISK MANAGER
# ==========================================

class RiskManager:
    @staticmethod
    def calculate_position_size(
        equity: float, risk_pct: float, entry_price: float, stop_price: float, 
        max_leverage: float, contract_multiplier: float = 1.0, 
        side: Side = Side.LONG, current_notional: float = 0.0
    ) -> float:
        if not (0 < risk_pct <= 1.0): return 0.0
        if max_leverage <= 0: return 0.0
        if equity <= 0 or entry_price <= 0 or stop_price <= 0 or entry_price == stop_price:
            return 0.0
            
        if side == Side.LONG and stop_price >= entry_price: return 0.0
        if side == Side.SHORT and stop_price <= entry_price: return 0.0
            
        stop_distance = abs(entry_price - stop_price)
        risk_capital = equity * risk_pct
        risk_per_unit = stop_distance * contract_multiplier
        
        raw_size = risk_capital / risk_per_unit
        
        available_notional = max(0.0, (equity * max_leverage) - current_notional)
        max_size_by_margin = available_notional / (entry_price * contract_multiplier)
        
        final_size = min(raw_size, max_size_by_margin)
        if math.isnan(final_size) or math.isinf(final_size) or final_size <= 0: 
            return 0.0
            
        return round(final_size, 6)


# ==========================================
# 3. EXECUTION ENGINE
# ==========================================

class ExecutionEngine:
    def __init__(self, latency_ms: float = 10.0, fee_rate: float = 0.0002, slippage_ticks: float = 1.0):
        self.latency_sec = latency_ms / 1000.0
        self.fee_rate = fee_rate
        self.slippage_ticks = slippage_ticks
        self.pending_orders: List[Order] = []
        self.active_orders: List[Order] = []

    def submit_order(self, order: Order, current_time: float):
        if order.order_type == OrderType.LIMIT and order.price is None:
            order.status = OrderStatus.REJECTED
            return
        if order.order_type == OrderType.STOP and order.stop_price is None:
            order.status = OrderStatus.REJECTED
            return

        order.timestamp = current_time + self.latency_sec
        order.status = OrderStatus.PENDING
        self.pending_orders.append(order)

    def process_tick(self, tick: Tick, symbol: str = "BTCUSD") -> List[Fill]:
        fills = []
        
        for o in self.pending_orders[:]:
            if tick.timestamp >= o.timestamp:
                o.status = OrderStatus.ACTIVE
                self.active_orders.append(o)
                self.pending_orders.remove(o)

        avail_bid = tick.bid_volume
        avail_ask = tick.ask_volume

        for o in self.active_orders[:]:
            if o.symbol != symbol:
                continue
            if avail_bid <= 0 and avail_ask <= 0:
                break 
                
            fill = self._try_execute(o, tick, avail_bid, avail_ask)
            if fill:
                fills.append(fill)
                if o.side == Side.LONG:
                    avail_ask = max(0.0, avail_ask - fill.quantity)
                else:
                    avail_bid = max(0.0, avail_bid - fill.quantity)
                
                if o.filled_qty >= o.quantity - 1e-8:
                    o.status = OrderStatus.FILLED
                    self.active_orders.remove(o)
                else:
                    o.status = OrderStatus.PARTIAL

        # IOC-Logik: Ungefüllte Market-Orders verfallen am Ende des Ticks
        for o in self.active_orders[:]:
            if o.order_type == OrderType.MARKET:
                o.status = OrderStatus.CANCELED
                self.active_orders.remove(o)
                    
        return fills

    def _try_execute(self, order: Order, tick: Tick, avail_bid: float, avail_ask: float) -> Optional[Fill]:
        fill_price = 0.0
        avail_vol = avail_ask if order.side == Side.LONG else avail_bid
        if avail_vol <= 0: return None
        
        exec_price = tick.ask if order.side == Side.LONG else tick.bid
        is_limit_execution = False

        if order.order_type == OrderType.STOP:
            if order.side == Side.LONG and tick.ask >= order.stop_price:
                order.order_type = OrderType.MARKET
            elif order.side == Side.SHORT and tick.bid <= order.stop_price:
                order.order_type = OrderType.MARKET
            else:
                return None

        if order.order_type == OrderType.LIMIT:
            if order.side == Side.LONG and tick.ask <= order.price:
                fill_price = order.price
                is_limit_execution = True
            elif order.side == Side.SHORT and tick.bid >= order.price:
                fill_price = order.price
                is_limit_execution = True
            else:
                return None
                
        elif order.order_type == OrderType.MARKET:
            fill_price = exec_price

        if fill_price > 0:
            qty_to_fill = min(order.quantity - order.filled_qty, avail_vol)
            if qty_to_fill <= 0: return None
            
            if is_limit_execution:
                final_fill_price = fill_price
                applied_slippage = 0.0
            else:
                applied_slippage = self.slippage_ticks if order.side == Side.LONG else -self.slippage_ticks
                final_fill_price = fill_price + applied_slippage
            
            fee = (qty_to_fill * final_fill_price) * self.fee_rate
            order.filled_qty += qty_to_fill
            
            return Fill(
                order_id=order.order_id, timestamp=tick.timestamp, side=order.side, quantity=qty_to_fill,
                price=final_fill_price, fee=fee, slippage=abs(applied_slippage), 
                reduce_only=order.reduce_only, stop_price=order.stop_price,       
                take_profit_price=order.take_profit_price, symbol=order.symbol
            )
        return None


# ==========================================
# 4. PORTFOLIO MANAGER
# ==========================================

class PortfolioManager:
    def __init__(self, initial_capital: float, contract_multiplier: float = 1.0):
        self.cash = initial_capital
        self.equity = initial_capital
        self.contract_multiplier = contract_multiplier
        self.positions: Dict[str, Position] = {}
        self.trade_ledger: List[dict] = []
        self.latest_prices: Dict[str, Tick] = {}

    def mark_to_market(self, tick: Tick, symbol: str):
        self.latest_prices[symbol] = tick
        unrealized = 0.0
        for sym, pos in self.positions.items():
            if pos.state != PositionState.CLOSED and sym in self.latest_prices:
                latest_tick = self.latest_prices[sym]
                current_price = latest_tick.bid if pos.side == Side.LONG else latest_tick.ask
                price_diff = (current_price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - current_price)
                unrealized += price_diff * pos.quantity * self.contract_multiplier
        self.equity = self.cash + unrealized

    def process_fill(self, fill: Fill, symbol: str):
        self.cash -= fill.fee 
        if symbol not in self.positions or self.positions[symbol].state == PositionState.CLOSED:
            if fill.reduce_only: return 
            self.positions[symbol] = Position(
                symbol=symbol, side=fill.side, quantity=fill.quantity, entry_price=fill.price,
                stop_loss=fill.stop_price, take_profit=fill.take_profit_price
            )
            return

        pos = self.positions[symbol]

        if pos.side == fill.side:
            if fill.reduce_only: return 
            total_qty = pos.quantity + fill.quantity
            pos.entry_price = ((pos.entry_price * pos.quantity) + (fill.price * fill.quantity)) / total_qty
            pos.quantity = total_qty
            
            if fill.stop_price is not None: pos.stop_loss = fill.stop_price
            if fill.take_profit_price is not None: pos.take_profit = fill.take_profit_price
        else:
            close_qty = min(pos.quantity, fill.quantity)
            price_diff = (fill.price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - fill.price)
            realized = price_diff * close_qty * self.contract_multiplier
            
            pos.realized_pnl += realized
            self.cash += realized 
            pos.quantity -= close_qty
            
            self.trade_ledger.append({
                "time": fill.timestamp, "symbol": symbol, "side": pos.side.name,
                "qty": close_qty, "entry_price": pos.entry_price, "exit_price": fill.price,
                "pnl": realized, "fee": fill.fee
            })
            
            if pos.quantity <= 1e-8:
                pos.quantity = 0.0
                pos.state = PositionState.CLOSED
                del self.positions[symbol]
                
            remaining_qty = fill.quantity - close_qty
            if remaining_qty > 1e-8 and not fill.reduce_only:
                self.positions[symbol] = Position(
                    symbol=symbol, side=fill.side, quantity=remaining_qty, entry_price=fill.price,
                    stop_loss=fill.stop_price, take_profit=fill.take_profit_price
                )


# ==========================================
# 5. BACKTEST ENGINE
# ==========================================

class BacktestEngine:
    def __init__(self, initial_capital: float = 10000.0, risk_pct: float = 0.01, max_leverage: float = 2.0, contract_multiplier: float = 1.0):
        self.contract_multiplier = contract_multiplier
        self.portfolio = PortfolioManager(initial_capital, contract_multiplier)
        self.execution = ExecutionEngine(latency_ms=0)
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.symbol = "BTCUSD" 

    def process_ticks(self, ticks: List[Tick], symbol: str = "BTCUSD"):
        for tick in ticks:
            if not tick.is_valid(): continue
            
            # 1. Mark-to-Market
            self.portfolio.mark_to_market(tick, symbol)
            
            # 2. Historische Orders ausführen
            fills = self.execution.process_tick(tick, symbol)
            for fill in fills:
                self.portfolio.process_fill(fill, fill.symbol)
                
            # 3. Neue Trigger / Exits evaluieren (Ohne Lookahead!)
            self._check_liquidation(tick)
            self._check_sl_tp(tick, symbol)

    def enter_trade(self, tick: Tick, side: Side, stop_distance: float, tp_distance: float, symbol: str = "BTCUSD"):
        if stop_distance <= 0 or tp_distance <= 0:
            raise ValueError("stop_distance und tp_distance müssen > 0 sein")

        current_price = tick.ask if side == Side.LONG else tick.bid
        stop_price = current_price - stop_distance if side == Side.LONG else current_price + stop_distance
        tp_price = current_price + tp_distance if side == Side.LONG else current_price - tp_distance
        
        if side == Side.LONG and stop_price >= tick.bid:
            raise ValueError(f"Stop-Loss liegt im oder über dem Spread. Position würde sofort ausstoppen.")
        if side == Side.SHORT and stop_price <= tick.ask:
            raise ValueError(f"Stop-Loss liegt im oder unter dem Spread. Position würde sofort ausstoppen.")

        current_notional = sum(
            pos.quantity * (self.portfolio.latest_prices[sym].bid if pos.side == Side.LONG else self.portfolio.latest_prices[sym].ask) 
            for sym, pos in self.portfolio.positions.items() 
            if pos.state != PositionState.CLOSED and sym in self.portfolio.latest_prices
        ) * self.contract_multiplier

        qty = RiskManager.calculate_position_size(
            equity=self.portfolio.equity, risk_pct=self.risk_pct, entry_price=current_price, stop_price=stop_price, 
            max_leverage=self.max_leverage, contract_multiplier=self.contract_multiplier,
            side=side, current_notional=current_notional
        )
        if qty <= 0: return

        order = Order(
            order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=side, 
            order_type=OrderType.MARKET, quantity=qty,
            stop_price=stop_price, take_profit_price=tp_price, symbol=symbol
        )
        self.execution.submit_order(order, tick.timestamp)

    def _check_liquidation(self, tick: Tick):
        """Zwangsliquidation ohne Order-Spam und Lookahead."""
        if self.portfolio.equity > 0:
            return
            
        for sym, pos in self.portfolio.positions.items():
            if pos.state == PositionState.CLOSED:
                continue
                
            exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
            
            covered_qty = sum(
                o.quantity - o.filled_qty 
                for o in self.execution.pending_orders + self.execution.active_orders 
                if o.symbol == sym and o.reduce_only and o.side == exit_side
            )
            
            uncovered_qty = pos.quantity - covered_qty
            
            if uncovered_qty > 1e-8:
                liq_order = Order(
                    order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=exit_side, 
                    order_type=OrderType.MARKET, quantity=uncovered_qty, reduce_only=True, symbol=sym
                )
                self.execution.submit_order(liq_order, tick.timestamp)
                pos.state = PositionState.CLOSING

    def _check_sl_tp(self, tick: Tick, symbol: str = "BTCUSD"):
        if symbol not in self.portfolio.positions: return
        pos = self.portfolio.positions[symbol]
        
        if pos.state == PositionState.CLOSED: return 
        
        trigger = False
        if pos.side == Side.LONG:
            if pos.stop_loss and tick.bid <= pos.stop_loss: trigger = True
            elif pos.take_profit and tick.bid >= pos.take_profit: trigger = True
        else:
            if pos.stop_loss and tick.ask >= pos.stop_loss: trigger = True
            elif pos.take_profit and tick.ask <= pos.take_profit: trigger = True
            
        exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
        
        covered_qty = sum(
            o.quantity - o.filled_qty 
            for o in self.execution.pending_orders + self.execution.active_orders 
            if o.symbol == symbol and o.reduce_only and o.side == exit_side
        )
        
        uncovered_qty = pos.quantity - covered_qty

        if not trigger and uncovered_qty == pos.quantity and pos.state == PositionState.CLOSING:
            pos.state = PositionState.OPEN
            return

        if trigger and uncovered_qty > 1e-8:
            exit_order = Order(
                order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=exit_side, 
                order_type=OrderType.MARKET, quantity=uncovered_qty, reduce_only=True, symbol=symbol
            )
            self.execution.submit_order(exit_order, tick.timestamp)
            pos.state = PositionState.CLOSING
