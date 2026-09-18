"""
Produktionsreife Event-getriebene Backtesting & Execution Engine.
Unterstützt Multi-Asset, dynamisches Queue-Modeling, präzise Slippage/Fee-Modelle
sowie robuste Numerik und Invarianten-Sicherung.
"""

import math
import logging
import heapq
import itertools
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Callable, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)

_order_counter = itertools.count(1)
def _next_order_id() -> str:
    return f"ord-{next(_order_counter):08d}"

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
    TRIGGERED = "TRIGGERED"
    ERROR = "ERROR"

class TimeInForce(Enum):
    IOC = "IOC"
    GTC = "GTC"

class PositionState(Enum):
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"

class ExitReason(Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    LIQUIDATION = "LIQUIDATION"
    SIGNAL = "SIGNAL"
    END_OF_BACKTEST = "END_OF_BACKTEST"

class EventPriority(IntEnum):
    TICK = 10
    ORDER = 20
    FUNDING = 30
    EVAL = 40

@dataclass(order=True)
class Event:
    timestamp: float
    priority: EventPriority
    event_id: int = field(default_factory=lambda: next(_order_counter))

@dataclass(order=True)
class Tick(Event):
    symbol: str = field(default="BTCUSD", compare=False)
    bid: float = field(default=0.0, compare=False)
    ask: float = field(default=0.0, compare=False)
    bid_volume: float = field(default=0.0, compare=False)
    ask_volume: float = field(default=0.0, compare=False)
    mark_price: Optional[float] = field(default=None, compare=False)

    def __post_init__(self):
        self.priority = EventPriority.TICK
        if self.mark_price is None and self.bid > 0 and self.ask > 0:
            self.mark_price = (self.bid + self.ask) / 2.0

    def is_valid(self) -> bool:
        vals = [self.timestamp, self.bid, self.ask, self.bid_volume, self.ask_volume]
        if any(not math.isfinite(v) for v in vals): return False
        if self.timestamp < 0: return False
        if self.bid <= 0 or self.ask <= 0: return False
        if self.ask < self.bid: return False
        if self.bid_volume < 0 or self.ask_volume < 0: return False
        if self.mark_price is not None and (not math.isfinite(self.mark_price) or self.mark_price <= 0):
            return False
        return True

@dataclass
class Order:
    order_id: str
    timestamp: float
    side: Side
    order_type: OrderType
    quantity: float
    symbol: str
    time_in_force: TimeInForce = TimeInForce.IOC
    price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    reject_reason: Optional[str] = None
    filled_qty: float = 0.0
    reduce_only: bool = False
    exit_reason: Optional[ExitReason] = None
    queue_position: float = 0.0

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
    symbol: str
    exit_reason: Optional[ExitReason] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    is_maker: bool = False

@dataclass
class Position:
    symbol: str
    side: Side
    quantity: float
    accumulated_notional: float = 0.0
    accumulated_qty: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    state: PositionState = PositionState.OPEN
    realized_pnl: float = 0.0

    @property
    def entry_price(self) -> float:
        return self.accumulated_notional / self.accumulated_qty if self.accumulated_qty > 0 else 0.0

class SlippageModel:
    def calculate(self, order: Order, tick: Tick, is_marketable: bool) -> float:
        raise NotImplementedError

class ConstantSlippage(SlippageModel):
    def __init__(self, tick_size: float, slippage_ticks: float):
        self.tick_size = tick_size
        self.slippage_ticks = slippage_ticks

    def calculate(self, order: Order, tick: Tick, is_marketable: bool) -> float:
        if not is_marketable:
            return 0.0
        return self.slippage_ticks * self.tick_size

class FeeModel:
    def calculate(self, qty: float, price: float, is_maker: bool) -> float:
        raise NotImplementedError

class MakerTakerFeeModel(FeeModel):
    def __init__(self, maker_rate: float, taker_rate: float):
        self.maker_rate = maker_rate
        self.taker_rate = taker_rate

    def calculate(self, qty: float, price: float, is_maker: bool) -> float:
        rate = self.maker_rate if is_maker else self.taker_rate
        return (qty * price) * rate

class RiskManager:
    EPS = 1e-8

    @staticmethod
    def calculate_position_size(
        equity: float, risk_pct: float, entry_price: float, stop_price: float,
        max_leverage: float, contract_multiplier: float = 1.0,
        side: Side = Side.LONG, current_exposure: float = 0.0
    ) -> float:
        if not (0 < risk_pct <= 1.0) or max_leverage <= 0: return 0.0
        if equity <= 0 or entry_price <= 0 or stop_price <= 0: return 0.0
        if abs(entry_price - stop_price) < RiskManager.EPS: return 0.0
        if side == Side.LONG and stop_price >= entry_price: return 0.0
        if side == Side.SHORT and stop_price <= entry_price: return 0.0
        if not math.isfinite(current_exposure): return 0.0

        stop_distance = abs(entry_price - stop_price)
        risk_capital = equity * risk_pct
        risk_per_unit = stop_distance * contract_multiplier

        raw_size = risk_capital / risk_per_unit
        available_notional = max(0.0, (equity * max_leverage) - current_exposure)
        max_size_by_margin = available_notional / (entry_price * contract_multiplier)

        final_size = min(raw_size, max_size_by_margin)
        return 0.0 if not math.isfinite(final_size) or final_size <= 0 else round(final_size, 6)


class PerformanceAnalytics:
    @staticmethod
    def calculate(trade_ledger: List[dict], equity_curve: List[dict], resample_seconds: float = 3600.0) -> dict:
        if not trade_ledger:
            return {}

        gross_profit = sum(t["pnl"] for t in trade_ledger if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trade_ledger if t["pnl"] < 0))
        net_profit = gross_profit - gross_loss
        total_fees = sum(t["fee"] for t in trade_ledger)

        win_trades = [t for t in trade_ledger if t["pnl"] > 0]
        loss_trades = [t for t in trade_ledger if t["pnl"] < 0]
        n_trades = len(trade_ledger)
        win_rate = len(win_trades) / n_trades if n_trades else 0.0

        avg_win = (gross_profit / len(win_trades)) if win_trades else 0.0
        avg_loss = (gross_loss / len(loss_trades)) if loss_trades else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

        max_dd = 0.0
        max_dd_pct = 0.0
        peak = -float('inf')
        for eq_point in equity_curve:
            eq = eq_point["equity"]
            if eq > peak: peak = eq
            dd = peak - eq
            if dd > max_dd: max_dd = dd
            if peak > 0 and (dd / peak) > max_dd_pct: max_dd_pct = dd / peak

        time_bars = {}
        for row in equity_curve:
            bar_idx = int(row["timestamp"] // resample_seconds)
            time_bars[bar_idx] = row["equity"]
        
        sorted_bars = sorted(time_bars.items())
        returns = []
        for i in range(1, len(sorted_bars)):
            prev = sorted_bars[i-1][1]
            cur = sorted_bars[i][1]
            if prev > 0:
                returns.append(cur / prev - 1)

        sharpe, sortino, calmar = None, None, None
        periods_per_year = (365.25 * 86400) / resample_seconds
        ann_factor = math.sqrt(periods_per_year)

        if len(returns) >= 2:
            mean_r = sum(returns) / len(returns)
            var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
            std_r = math.sqrt(var_r)
            if std_r > 0:
                sharpe = (mean_r / std_r) * ann_factor
            
            downside = [r for r in returns if r < 0]
            if downside:
                dvar = sum(r * r for r in downside) / len(downside)
                if dvar > 0:
                    sortino = (mean_r / math.sqrt(dvar)) * ann_factor
            
            total_ret = (equity_curve[-1]["equity"] / equity_curve[0]["equity"] - 1) if equity_curve[0]["equity"] > 0 else 0.0
            if max_dd_pct > 0:
                calmar = total_ret / max_dd_pct

        total_return = equity_curve[-1]["equity"] - equity_curve[0]["equity"] if equity_curve else (net_profit - total_fees)

        return {
            "Total Return": total_return, "Gross Profit": gross_profit, "Gross Loss": gross_loss,
            "Net Profit": net_profit, "Total Fees": total_fees, "Profit Factor": profit_factor,
            "Win Rate": win_rate, "Average Win": avg_win, "Average Loss": avg_loss,
            "Max Drawdown": max_dd, "Max Drawdown Pct": max_dd_pct,
            "Sharpe Ratio": sharpe, "Sortino Ratio": sortino, "Calmar Ratio": calmar,
            "Trade Count": n_trades
        }

class ExecutionEngine:
    def __init__(self, slippage_model: SlippageModel, fee_model: FeeModel):
        self.slippage_model = slippage_model
        self.fee_model = fee_model
        self.pending_orders: Dict[str, Order] = {}
        self.active_orders: Dict[str, Order] = {}

    def submit_order(self, order: Order):
        if not math.isfinite(order.quantity) or order.quantity <= 0:
            order.status, order.reject_reason = OrderStatus.REJECTED, "INVALID_QUANTITY"
            return
        if order.order_type == OrderType.MARKET and order.time_in_force == TimeInForce.GTC:
            order.time_in_force = TimeInForce.IOC
        if order.order_type == OrderType.STOP and order.time_in_force == TimeInForce.IOC:
            order.time_in_force = TimeInForce.GTC
            
        self.pending_orders[order.order_id] = order

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.pending_orders:
            self.pending_orders[order_id].status = OrderStatus.CANCELED
            del self.pending_orders[order_id]
            return True
        if order_id in self.active_orders:
            self.active_orders[order_id].status = OrderStatus.CANCELED
            del self.active_orders[order_id]
            return True
        return False

    def modify_order(self, order_id: str, new_qty: Optional[float] = None, new_price: Optional[float] = None) -> bool:
        order = self.pending_orders.get(order_id) or self.active_orders.get(order_id)
        if not order or order.time_in_force == TimeInForce.IOC:
            return False
            
        if new_qty is not None:
            if new_qty <= order.filled_qty:
                return False
            order.quantity = new_qty
            
        if new_price is not None and new_price > 0: 
            order.price = new_price
        return True

    def process_tick(self, tick: Tick, on_fill: Optional[Callable[[Fill], bool]] = None) -> List[Fill]:
        fills: List[Fill] = []
        
        to_activate = [o for o in self.pending_orders.values() if tick.timestamp >= o.timestamp]
        for o in to_activate:
            o.status = OrderStatus.ACTIVE
            if o.order_type == OrderType.LIMIT and o.price:
                o.queue_position = tick.bid_volume if o.side == Side.LONG else tick.ask_volume
            self.active_orders[o.order_id] = o
            del self.pending_orders[o.order_id]

        avail_bid, avail_ask = tick.bid_volume, tick.ask_volume
        to_remove = []

        for o in self.active_orders.values():
            if o.symbol != tick.symbol: continue

            candidate, is_maker = self._try_execute(o, tick, avail_bid, avail_ask)
            if candidate:
                accepted = True
                if on_fill:
                    try:
                        accepted = bool(on_fill(candidate))
                    except Exception:
                        logger.exception("Exception in on_fill callback")
                        o.status = OrderStatus.ERROR
                        to_remove.append(o.order_id)
                        continue

                if accepted:
                    fills.append(candidate)
                    o.filled_qty += candidate.quantity
                    if o.side == Side.LONG: avail_ask = max(0.0, avail_ask - candidate.quantity)
                    else: avail_bid = max(0.0, avail_bid - candidate.quantity)

                    if math.isclose(o.filled_qty, o.quantity, rel_tol=1e-8) or o.filled_qty >= o.quantity:
                        o.status = OrderStatus.FILLED
                        to_remove.append(o.order_id)
                    else:
                        o.status = OrderStatus.PARTIAL

        for oid in to_remove:
            if oid in self.active_orders: del self.active_orders[oid]

        ioc_cancellations = []
        for o in self.active_orders.values():
            if o.symbol == tick.symbol and o.time_in_force == TimeInForce.IOC:
                o.status = OrderStatus.CANCELED
                ioc_cancellations.append(o.order_id)
        for oid in ioc_cancellations:
            del self.active_orders[oid]

        return fills

    def _try_execute(self, order: Order, tick: Tick, avail_bid: float, avail_ask: float) -> Tuple[Optional[Fill], bool]:
        if order.order_type == OrderType.STOP:
            if order.status != OrderStatus.TRIGGERED:
                trigger = (order.side == Side.LONG and tick.ask >= order.stop_price) or \
                          (order.side == Side.SHORT and tick.bid <= order.stop_price)
                if not trigger: return None, False
                order.status = OrderStatus.TRIGGERED

        avail_vol = avail_ask if order.side == Side.LONG else avail_bid
        if avail_vol <= 0: return None, False

        exec_price = tick.ask if order.side == Side.LONG else tick.bid
        fill_price, is_limit, is_marketable, is_maker = None, False, True, False

        if order.order_type == OrderType.LIMIT:
            is_limit = True
            crossed_spread = (order.side == Side.LONG and tick.ask <= order.price) or \
                             (order.side == Side.SHORT and tick.bid >= order.price)
            if crossed_spread:
                fill_price = tick.ask if order.side == Side.LONG else tick.bid
                is_marketable = True
                is_maker = False
            else:
                queue_drain = tick.bid_volume if order.side == Side.LONG else tick.ask_volume
                order.queue_position -= queue_drain
                if order.queue_position <= 0:
                    fill_price = order.price
                    is_marketable = False
                    is_maker = True
                else:
                    return None, False
        else:
            fill_price = exec_price

        if not fill_price or fill_price <= 0: return None, False

        qty_to_fill = min(order.quantity - order.filled_qty, avail_vol)
        if qty_to_fill <= 1e-8: return None, False

        applied_slippage = self.slippage_model.calculate(order, tick, is_marketable)
        final_fill_price = fill_price + applied_slippage if order.side == Side.LONG else fill_price - applied_slippage
        
        if is_limit and is_marketable:
            if order.side == Side.LONG: final_fill_price = min(final_fill_price, order.price)
            else: final_fill_price = max(final_fill_price, order.price)

        fee = self.fee_model.calculate(qty_to_fill, final_fill_price, is_maker)

        return Fill(
            order_id=order.order_id, timestamp=tick.timestamp, side=order.side,
            quantity=qty_to_fill, price=final_fill_price, fee=fee,
            slippage=applied_slippage, reduce_only=order.reduce_only,
            exit_reason=order.exit_reason, stop_price=order.stop_price,
            take_profit_price=order.take_profit_price, symbol=order.symbol, is_maker=is_maker
        ), is_maker


class PortfolioManager:
    def __init__(self, initial_capital: float, contract_multiplier: float = 1.0, max_leverage: float = 2.0):
        self.cash = initial_capital
        self.equity = initial_capital
        self.contract_multiplier = contract_multiplier
        self.max_leverage = max_leverage
        self.positions: Dict[str, Position] = {}
        self.trade_ledger: List[dict] = []
        self.equity_curve: List[dict] = []
        self.latest_prices: Dict[str, Tick] = {}

    def mark_to_market(self, tick: Tick):
        self.latest_prices[tick.symbol] = tick
        unrealized = 0.0
        for sym, pos in self.positions.items():
            if pos.state == PositionState.CLOSED: continue
            pt = self.latest_prices.get(sym)
            if not pt: continue
            current_price = pt.mark_price or (pt.bid if pos.side == Side.LONG else pt.ask)
            diff = (current_price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - current_price)
            unrealized += diff * pos.quantity * self.contract_multiplier
        self.equity = self.cash + unrealized

    def record_equity(self, timestamp: float):
        self.equity_curve.append({"timestamp": timestamp, "cash": self.cash, "equity": self.equity})

    def apply_funding(self, timestamp: float, symbol: str, rate: float):
        pos = self.positions.get(symbol)
        if pos and pos.state == PositionState.OPEN:
            pt = self.latest_prices.get(symbol)
            if pt:
                notional = pos.quantity * (pt.mark_price or pt.bid) * self.contract_multiplier
                funding_payment = notional * rate * (1 if pos.side == Side.LONG else -1)
                self.cash -= funding_payment
                self.equity -= funding_payment

    def get_exposure(self, engine: ExecutionEngine) -> float:
        exposure = 0.0
        for sym, p in self.positions.items():
            if p.state == PositionState.CLOSED: continue
            pt = self.latest_prices.get(sym)
            mark = (pt.mark_price or p.entry_price) if pt else p.entry_price
            exposure += p.quantity * mark * self.contract_multiplier

        for o in list(engine.pending_orders.values()) + list(engine.active_orders.values()):
            if o.reduce_only: continue
            rem = o.quantity - o.filled_qty
            if rem <= 0: continue
            if o.order_type == OrderType.LIMIT and o.price: est = o.price
            else:
                pt = self.latest_prices.get(o.symbol)
                if not pt: return float('inf')
                est = pt.mark_price or pt.ask
            exposure += rem * est * self.contract_multiplier
        return exposure

    def process_fill(self, fill: Fill) -> bool:
        existing = self.positions.get(fill.symbol)
        pos_is_closed = not existing or existing.state == PositionState.CLOSED

        if pos_is_closed and fill.reduce_only:
            return False

        if not pos_is_closed and fill.reduce_only and fill.quantity > existing.quantity:
            capped_qty = existing.quantity
            fill.fee = fill.fee * (capped_qty / fill.quantity) if fill.quantity > 0 else 0
            fill.quantity = capped_qty

        if pos_is_closed:
            self.cash -= fill.fee
            self.positions[fill.symbol] = Position(
                symbol=fill.symbol, side=fill.side, quantity=fill.quantity,
                accumulated_notional=(fill.price * fill.quantity), accumulated_qty=fill.quantity,
                stop_loss=fill.stop_price, take_profit=fill.take_profit_price
            )
            return True

        pos = existing

        if pos.side == fill.side:
            if fill.reduce_only: 
                return False
            self.cash -= fill.fee
            pos.accumulated_notional += (fill.price * fill.quantity)
            pos.accumulated_qty += fill.quantity
            pos.quantity += fill.quantity
            if fill.stop_price:
                pos.stop_loss = max(pos.stop_loss or 0, fill.stop_price) if pos.side == Side.LONG else min(pos.stop_loss or float('inf'), fill.stop_price)
            if fill.take_profit_price:
                pos.take_profit = min(pos.take_profit or float('inf'), fill.take_profit_price) if pos.side == Side.LONG else max(pos.take_profit or 0, fill.take_profit_price)
            pos.state = PositionState.OPEN
            return True

        self.cash -= fill.fee
        close_qty = min(pos.quantity, fill.quantity)
        price_diff = (fill.price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - fill.price)
        realized = price_diff * close_qty * self.contract_multiplier

        pos.realized_pnl += realized
        self.cash += realized
        pos.quantity -= close_qty

        if pos.quantity > 0:
            ratio = pos.quantity / pos.accumulated_qty
            pos.accumulated_notional *= ratio
            pos.accumulated_qty = pos.quantity

        self.trade_ledger.append({
            "time": fill.timestamp, "symbol": fill.symbol, "side": pos.side.name,
            "qty": close_qty, "entry_price": pos.entry_price, "exit_price": fill.price,
            "pnl": realized, "fee": fill.fee,
            "exit_reason": fill.exit_reason.name if fill.exit_reason else ExitReason.SIGNAL.name,
        })

        if pos.quantity <= 1e-8:
            pos.quantity = 0.0
            pos.state = PositionState.CLOSED
            del self.positions[fill.symbol]
        else:
            pos.state = PositionState.OPEN

        rem_qty = fill.quantity - close_qty
        if rem_qty > 1e-8 and not fill.reduce_only:
            self.positions[fill.symbol] = Position(
                symbol=fill.symbol, side=fill.side, quantity=rem_qty,
                accumulated_notional=(fill.price * rem_qty), accumulated_qty=rem_qty,
                stop_loss=fill.stop_price, take_profit=fill.take_profit_price
            )
        return True


class BacktestEngine:
    def __init__(self, initial_capital: float = 10000.0, risk_pct: float = 0.01,
                 max_leverage: float = 2.0, contract_multiplier: float = 1.0,
                 tick_size: float = 0.01, maintenance_margin_rate: float = 0.5,
                 fee_maker: float = 0.0001, fee_taker: float = 0.0002, slippage_ticks: float = 1.0):
        self.contract_multiplier = contract_multiplier
        self.portfolio = PortfolioManager(initial_capital, contract_multiplier, max_leverage)
        self.execution = ExecutionEngine(
            slippage_model=ConstantSlippage(tick_size, slippage_ticks),
            fee_model=MakerTakerFeeModel(fee_maker, fee_taker)
        )
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.maintenance_margin_rate = maintenance_margin_rate
        self.last_timestamps: Dict[str, float] = defaultdict(float)
        self.event_queue: List[Event] = []

    def process_ticks(self, ticks: List[Tick]):
        for t in ticks:
            heapq.heappush(self.event_queue, t)
        self._run_queue()

    def _run_queue(self):
        while self.event_queue:
            event = heapq.heappop(self.event_queue)
            
            if isinstance(event, Tick):
                if not event.is_valid(): continue
                if event.timestamp < self.last_timestamps[event.symbol]:
                    raise ValueError(f"Timestamp Regression: {event.timestamp} < {self.last_timestamps[event.symbol]}")
                self.last_timestamps[event.symbol] = event.timestamp
                
                self.portfolio.mark_to_market(event)
                self._eval_exits(event)
                
                self.execution.process_tick(event, on_fill=lambda f: self.portfolio.process_fill(f))
                self._revert_stuck_exits(event.symbol)
                self.portfolio.record_equity(event.timestamp)

    def enter_trade(self, tick: Tick, side: Side, stop_distance: float, tp_distance: float, symbol: str = "BTCUSD"):
        current_price = tick.ask if side == Side.LONG else tick.bid
        
        pseudo_order = Order("tmp", tick.timestamp, side, OrderType.MARKET, 1.0, symbol)
        expected_slippage = self.execution.slippage_model.calculate(pseudo_order, tick, is_marketable=True)
        eff_entry = current_price + expected_slippage if side == Side.LONG else current_price - expected_slippage
        
        stop_price = eff_entry - stop_distance if side == Side.LONG else eff_entry + stop_distance
        tp_price = eff_entry + tp_distance if side == Side.LONG else eff_entry - tp_distance

        exp = self.portfolio.get_exposure(self.execution)
        if not math.isfinite(exp): return

        qty = RiskManager.calculate_position_size(
            equity=self.portfolio.equity, risk_pct=self.risk_pct, entry_price=eff_entry,
            stop_price=stop_price, max_leverage=self.max_leverage, 
            contract_multiplier=self.contract_multiplier, side=side, current_exposure=exp
        )
        if qty <= 0: return

        order = Order(
            order_id=_next_order_id(), timestamp=tick.timestamp, side=side,
            order_type=OrderType.MARKET, time_in_force=TimeInForce.IOC,
            quantity=qty, stop_price=stop_price, take_profit_price=tp_price, symbol=symbol
        )
        self.execution.submit_order(order)

    def finalize(self, current_time: float):
        for sym, pos in list(self.portfolio.positions.items()):
            if pos.state == PositionState.CLOSED: continue
            exit_order = Order(
                order_id=_next_order_id(), timestamp=current_time, 
                side=Side.SHORT if pos.side == Side.LONG else Side.LONG,
                order_type=OrderType.MARKET, time_in_force=TimeInForce.IOC,
                quantity=pos.quantity, reduce_only=True, symbol=sym, exit_reason=ExitReason.END_OF_BACKTEST
            )
            self.execution.submit_order(exit_order)
            
            pt = self.portfolio.latest_prices.get(sym)
            if pt:
                synth = Tick(timestamp=current_time, symbol=sym, bid=pt.bid, ask=pt.ask, bid_volume=1e9, ask_volume=1e9)
                self.execution.process_tick(synth, on_fill=lambda f: self.portfolio.process_fill(f))
                
        self.portfolio.record_equity(current_time)
        if self.portfolio.positions:
            raise RuntimeError(f"Offene Positionen nach finalize: {list(self.portfolio.positions)}")

    def _eval_exits(self, tick: Tick):
        current_notional = 0.0
        for sym, p in self.portfolio.positions.items():
            if p.state == PositionState.CLOSED: continue
            pt = self.portfolio.latest_prices.get(sym)
            mark = pt.mark_price or (pt.bid if p.side == Side.LONG else pt.ask) if pt else p.entry_price
            current_notional += p.quantity * mark * self.contract_multiplier

        maint_req = (current_notional / self.max_leverage) * self.maintenance_margin_rate if current_notional > 0 else 0
        is_liq = current_notional > 0 and self.portfolio.equity <= maint_req

        if is_liq:
            for sym, p in list(self.portfolio.positions.items()):
                if p.state in (PositionState.CLOSED, PositionState.EXIT_PENDING): 
                    continue
                exit_side = Side.SHORT if p.side == Side.LONG else Side.LONG
                liq_order = Order(
                    order_id=_next_order_id(), timestamp=tick.timestamp, side=exit_side,
                    order_type=OrderType.MARKET, time_in_force=TimeInForce.IOC,
                    quantity=p.quantity, reduce_only=True, symbol=sym, exit_reason=ExitReason.LIQUIDATION
                )
                self.execution.submit_order(liq_order)
                p.state = PositionState.EXIT_PENDING
            return

        pos = self.portfolio.positions.get(tick.symbol)
        if not pos or pos.state in (PositionState.CLOSED, PositionState.EXIT_PENDING): 
            return

        trigger, reason = False, None
        if pos.side == Side.LONG:
            if pos.stop_loss and tick.bid <= pos.stop_loss: trigger, reason = True, ExitReason.STOP_LOSS
            elif pos.take_profit and tick.bid >= pos.take_profit: trigger, reason = True, ExitReason.TAKE_PROFIT
        else:
            if pos.stop_loss and tick.ask >= pos.stop_loss: trigger, reason = True, ExitReason.STOP_LOSS
            elif pos.take_profit and tick.ask <= pos.take_profit: trigger, reason = True, ExitReason.TAKE_PROFIT

        if not trigger: return

        exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
        covered_qty = sum(
            (o.quantity - o.filled_qty) for o in itertools.chain(self.execution.pending_orders.values(), self.execution.active_orders.values())
            if o.symbol == tick.symbol and o.reduce_only and o.side == exit_side
        )
        uncovered = pos.quantity - covered_qty

        if uncovered > 1e-8:
            exit_order = Order(
                order_id=_next_order_id(), timestamp=tick.timestamp, side=exit_side,
                order_type=OrderType.MARKET, time_in_force=TimeInForce.IOC,
                quantity=uncovered, reduce_only=True, symbol=tick.symbol, exit_reason=reason
            )
            self.execution.submit_order(exit_order)
            pos.state = PositionState.EXIT_PENDING

    def _revert_stuck_exits(self, symbol: str):
        pos = self.portfolio.positions.get(symbol)
        if not pos or pos.state != PositionState.EXIT_PENDING: return
        exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
        has_live = any(
            o.symbol == symbol and o.reduce_only and o.side == exit_side and o.status in (OrderStatus.PENDING, OrderStatus.ACTIVE, OrderStatus.PARTIAL)
            for o in itertools.chain(self.execution.pending_orders.values(), self.execution.active_orders.values())
        )
        if not has_live and pos.quantity > 1e-8:
            pos.state = PositionState.OPEN
