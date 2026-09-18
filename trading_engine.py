import math
from dataclasses import dataclass, field
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

@dataclass
class Tick:
    timestamp: float
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float

    def is_valid(self) -> bool:
        if any(not math.isfinite(v) for v in [self.timestamp, self.bid, self.ask, self.bid_volume, self.ask_volume]):
            return False
        if self.bid <= 0 or self.ask <= 0: return False
        if self.ask < self.bid: return False
        if self.bid_volume < 0 or self.ask_volume < 0: return False
        return True

@dataclass
class Order:
    order_id: str
    timestamp: float
    side: Side
    order_type: OrderType
    quantity: float
    time_in_force: TimeInForce = TimeInForce.IOC
    price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    reject_reason: Optional[str] = None
    filled_qty: float = 0.0
    reduce_only: bool = False
    symbol: str = "BTCUSD"
    exit_reason: Optional[ExitReason] = None

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
    exit_reason: Optional[ExitReason] = None
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
# 2. RISK & PERFORMANCE MANAGER
# ==========================================

class RiskManager:
    @staticmethod
    def calculate_position_size(
        equity: float, risk_pct: float, entry_price: float, stop_price: float, 
        max_leverage: float, contract_multiplier: float = 1.0, 
        side: Side = Side.LONG, current_exposure: float = 0.0
    ) -> float:
        if not (0 < risk_pct <= 1.0) or max_leverage <= 0: return 0.0
        if equity <= 0 or entry_price <= 0 or stop_price <= 0 or entry_price == stop_price: return 0.0
        if side == Side.LONG and stop_price >= entry_price: return 0.0
        if side == Side.SHORT and stop_price <= entry_price: return 0.0
            
        stop_distance = abs(entry_price - stop_price)
        risk_capital = equity * risk_pct
        risk_per_unit = stop_distance * contract_multiplier
        
        raw_size = risk_capital / risk_per_unit
        
        available_notional = max(0.0, (equity * max_leverage) - current_exposure)
        max_size_by_margin = available_notional / (entry_price * contract_multiplier)
        
        final_size = min(raw_size, max_size_by_margin)
        if not math.isfinite(final_size) or final_size <= 0: 
            return 0.0
            
        return round(final_size, 6)

class PerformanceAnalytics:
    @staticmethod
    def calculate(trade_ledger: List[dict], equity_curve: List[dict]) -> dict:
        if not trade_ledger: return {}
        
        gross_profit = sum(t["pnl"] for t in trade_ledger if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trade_ledger if t["pnl"] < 0))
        net_profit = gross_profit - gross_loss
        total_fees = sum(t["fee"] for t in trade_ledger)
        
        win_trades = [t for t in trade_ledger if t["pnl"] > 0]
        loss_trades = [t for t in trade_ledger if t["pnl"] <= 0]
        win_rate = len(win_trades) / len(trade_ledger) if trade_ledger else 0.0
        
        avg_win = (gross_profit / len(win_trades)) if win_trades else 0.0
        avg_loss = (gross_loss / len(loss_trades)) if loss_trades else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        
        max_dd = 0.0
        max_dd_pct = 0.0
        peak = -float('inf')
        for eq_point in equity_curve:
            eq = eq_point["equity"]
            if eq > peak: peak = eq
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0
            if dd > max_dd: max_dd = dd
            if dd_pct > max_dd_pct: max_dd_pct = dd_pct
            
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        
        return {
            "Total Return": net_profit - total_fees,
            "Gross Profit": gross_profit,
            "Gross Loss": gross_loss,
            "Net Profit": net_profit,
            "Total Fees": total_fees,
            "Profit Factor": profit_factor,
            "Win Rate": win_rate,
            "Average Win": avg_win,
            "Average Loss": avg_loss,
            "Expectancy": expectancy,
            "Max Drawdown": max_dd,
            "Max Drawdown %": max_dd_pct * 100,
            "Trade Count": len(trade_ledger)
        }

# ==========================================
# 3. EXECUTION ENGINE
# ==========================================

class ExecutionEngine:
    def __init__(self, latency_ms: float = 10.0, fee_rate: float = 0.0002, slippage_ticks: float = 1.0, tick_size: float = 0.01):
        self.latency_sec = latency_ms / 1000.0
        self.fee_rate = fee_rate
        self.slippage_ticks = slippage_ticks
        self.tick_size = tick_size
        self.pending_orders: List[Order] = []
        self.active_orders: List[Order] = []

    def submit_order(self, order: Order, current_time: float):
        if not math.isfinite(order.quantity) or order.quantity <= 0:
            order.status, order.reject_reason = OrderStatus.REJECTED, "INVALID_QUANTITY"
            return
        if order.order_type == OrderType.LIMIT and (order.price is None or not math.isfinite(order.price)):
            order.status, order.reject_reason = OrderStatus.REJECTED, "INVALID_PRICE"
            return
        if order.order_type == OrderType.STOP and (order.stop_price is None or not math.isfinite(order.stop_price)):
            order.status, order.reject_reason = OrderStatus.REJECTED, "INVALID_STOP"
            return

        order.timestamp = current_time + self.latency_sec
        order.status = OrderStatus.PENDING
        self.pending_orders.append(order)

    def process_tick(self, tick: Tick, symbol: str) -> List[Fill]:
        fills = []
        
        for o in self.pending_orders[:]:
            if tick.timestamp >= o.timestamp:
                o.status = OrderStatus.ACTIVE
                self.active_orders.append(o)
                self.pending_orders.remove(o)

        avail_bid, avail_ask = tick.bid_volume, tick.ask_volume

        for o in self.active_orders[:]:
            if o.symbol != symbol:
                continue
            if avail_bid <= 0 and avail_ask <= 0:
                break 
                
            fill = self._try_execute(o, tick, avail_bid, avail_ask)
            if fill:
                fills.append(fill)
                if o.side == Side.LONG: avail_ask = max(0.0, avail_ask - fill.quantity)
                else: avail_bid = max(0.0, avail_bid - fill.quantity)
                
                if o.filled_qty >= o.quantity - 1e-8:
                    o.status = OrderStatus.FILLED
                    self.active_orders.remove(o)
                else:
                    o.status = OrderStatus.PARTIAL

        for o in self.active_orders[:]:
            if o.time_in_force == TimeInForce.IOC and o.status in [OrderStatus.ACTIVE, OrderStatus.PARTIAL]:
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
            if (order.side == Side.LONG and tick.ask >= order.stop_price) or \
               (order.side == Side.SHORT and tick.bid <= order.stop_price):
                order.order_type = OrderType.MARKET
            else: return None

        if order.order_type == OrderType.LIMIT:
            if (order.side == Side.LONG and tick.ask <= order.price) or \
               (order.side == Side.SHORT and tick.bid >= order.price):
                fill_price = order.price
                is_limit_execution = True
            else: return None
                
        elif order.order_type == OrderType.MARKET:
            fill_price = exec_price

        if fill_price > 0:
            qty_to_fill = min(order.quantity - order.filled_qty, avail_vol)
            if qty_to_fill <= 0: return None
            
            if is_limit_execution:
                final_fill_price, applied_slippage_price = fill_price, 0.0
            else:
                applied_slippage_price = self.slippage_ticks * self.tick_size
                final_fill_price = fill_price + applied_slippage_price if order.side == Side.LONG else fill_price - applied_slippage_price
            
            fee = (qty_to_fill * final_fill_price) * self.fee_rate
            order.filled_qty += qty_to_fill
            
            return Fill(
                order_id=order.order_id, timestamp=tick.timestamp, side=order.side, quantity=qty_to_fill,
                price=final_fill_price, fee=fee, slippage=abs(applied_slippage_price), 
                reduce_only=order.reduce_only, exit_reason=order.exit_reason, stop_price=order.stop_price,       
                take_profit_price=order.take_profit_price, symbol=order.symbol
            )
        return None

# ==========================================
# 4. PORTFOLIO MANAGER
# ==========================================

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
        
    def record_equity(self, timestamp: float):
        self.equity_curve.append({
            "timestamp": timestamp, "cash": self.cash, "equity": self.equity
        })

    def get_exposure(self, engine: ExecutionEngine) -> float:
        exposure = sum(p.quantity * p.entry_price * self.contract_multiplier for p in self.positions.values() if p.state != PositionState.CLOSED)
        for o in engine.pending_orders + engine.active_orders:
            if not o.reduce_only:
                price_est = o.price if o.price else (self.latest_prices[o.symbol].microprice if o.symbol in self.latest_prices else 0.0)
                exposure += (o.quantity - o.filled_qty) * price_est * self.contract_multiplier
        return exposure

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
            
            # Update zu konservativerem Stop (Pyramiding)
            if fill.stop_price is not None:
                if pos.stop_loss is None: pos.stop_loss = fill.stop_price
                else: pos.stop_loss = max(pos.stop_loss, fill.stop_price) if pos.side == Side.LONG else min(pos.stop_loss, fill.stop_price)
            if fill.take_profit_price is not None:
                if pos.take_profit is None: pos.take_profit = fill.take_profit_price
                else: pos.take_profit = min(pos.take_profit, fill.take_profit_price) if pos.side == Side.LONG else max(pos.take_profit, fill.take_profit_price)
                
            pos.state = PositionState.OPEN
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
                "pnl": realized, "fee": fill.fee,
                "exit_reason": fill.exit_reason.name if fill.exit_reason else ExitReason.SIGNAL.name
            })
            
            if pos.quantity <= 1e-8:
                pos.quantity = 0.0
                pos.state = PositionState.CLOSED
                del self.positions[symbol]
            else:
                pos.state = PositionState.OPEN # Fallback if only partial exit
                
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
    def __init__(self, initial_capital: float = 10000.0, risk_pct: float = 0.01, max_leverage: float = 2.0, contract_multiplier: float = 1.0, tick_size: float = 0.01):
        self.contract_multiplier = contract_multiplier
        self.portfolio = PortfolioManager(initial_capital, contract_multiplier, max_leverage)
        self.execution = ExecutionEngine(latency_ms=0, tick_size=tick_size)
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.last_timestamp = -1.0

    def process_ticks(self, ticks: List[Tick], symbol: str = "BTCUSD"):
        for tick in ticks:
            if not tick.is_valid(): continue
            if tick.timestamp <= self.last_timestamp:
                raise ValueError(f"Timestamp Regression: {tick.timestamp} <= {self.last_timestamp}")
            self.last_timestamp = tick.timestamp
            
            self.portfolio.mark_to_market(tick, symbol)
            self._check_liquidation(tick)
            self._check_sl_tp(tick, symbol)
            
            fills = self.execution.process_tick(tick, symbol)
            for fill in fills:
                self.portfolio.process_fill(fill, fill.symbol)
            
            self.portfolio.record_equity(tick.timestamp)

    def enter_trade(self, tick: Tick, side: Side, stop_distance: float, tp_distance: float, symbol: str = "BTCUSD"):
        if stop_distance <= 0 or tp_distance <= 0:
            raise ValueError("stop_distance und tp_distance müssen > 0 sein")

        current_price = tick.ask if side == Side.LONG else tick.bid
        stop_price = current_price - stop_distance if side == Side.LONG else current_price + stop_distance
        tp_price = current_price + tp_distance if side == Side.LONG else current_price - tp_distance
        
        if side == Side.LONG and stop_price >= tick.bid:
            raise ValueError("Stop-Loss liegt im oder über dem Spread.")
        if side == Side.SHORT and stop_price <= tick.ask:
            raise ValueError("Stop-Loss liegt im oder unter dem Spread.")

        current_exposure = self.portfolio.get_exposure(self.execution)

        qty = RiskManager.calculate_position_size(
            equity=self.portfolio.equity, risk_pct=self.risk_pct, entry_price=current_price, stop_price=stop_price, 
            max_leverage=self.max_leverage, contract_multiplier=self.contract_multiplier,
            side=side, current_exposure=current_exposure
        )
        if qty <= 0: return

        order = Order(
            order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=side, 
            order_type=OrderType.MARKET, time_in_force=TimeInForce.IOC, quantity=qty,
            stop_price=stop_price, take_profit_price=tp_price, symbol=symbol
        )
        self.execution.submit_order(order, tick.timestamp)

    def _check_liquidation(self, tick: Tick):
        current_notional = sum(p.quantity * p.entry_price * self.contract_multiplier for p in self.portfolio.positions.values())
        if current_notional <= 0: return
        
        # Realistische Maintenance Margin (50% der Initial Margin)
        maintenance_margin_req = (current_notional / self.max_leverage) * 0.5
        if self.portfolio.equity > maintenance_margin_req:
            return
            
        for sym, pos in self.portfolio.positions.items():
            if pos.state == PositionState.CLOSED: continue
            
            exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
            covered_qty = sum(o.quantity - o.filled_qty for o in self.execution.pending_orders + self.execution.active_orders if o.symbol == sym and o.reduce_only and o.side == exit_side)
            uncovered_qty = pos.quantity - covered_qty
            
            if uncovered_qty > 1e-8:
                liq_order = Order(
                    order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=exit_side, 
                    order_type=OrderType.MARKET, time_in_force=TimeInForce.GTC, quantity=uncovered_qty, 
                    reduce_only=True, symbol=sym, exit_reason=ExitReason.LIQUIDATION
                )
                self.execution.submit_order(liq_order, tick.timestamp)
                pos.state = PositionState.EXIT_PENDING

    def _check_sl_tp(self, tick: Tick, symbol: str):
        if symbol not in self.portfolio.positions: return
        pos = self.portfolio.positions[symbol]
        if pos.state in [PositionState.CLOSED, PositionState.EXIT_PENDING]: return 
        
        trigger, reason = False, None
        if pos.side == Side.LONG:
            if pos.stop_loss and tick.bid <= pos.stop_loss: trigger, reason = True, ExitReason.STOP_LOSS
            elif pos.take_profit and tick.bid >= pos.take_profit: trigger, reason = True, ExitReason.TAKE_PROFIT
        else:
            if pos.stop_loss and tick.ask >= pos.stop_loss: trigger, reason = True, ExitReason.STOP_LOSS
            elif pos.take_profit and tick.ask <= pos.take_profit: trigger, reason = True, ExitReason.TAKE_PROFIT
            
        if trigger:
            exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
            covered_qty = sum(o.quantity - o.filled_qty for o in self.execution.pending_orders + self.execution.active_orders if o.symbol == symbol and o.reduce_only and o.side == exit_side)
            uncovered_qty = pos.quantity - covered_qty

            if uncovered_qty > 1e-8:
                exit_order = Order(
                    order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=exit_side, 
                    order_type=OrderType.MARKET, time_in_force=TimeInForce.GTC, quantity=uncovered_qty, 
                    reduce_only=True, symbol=symbol, exit_reason=reason
                )
                self.execution.submit_order(exit_order, tick.timestamp)
                pos.state = PositionState.EXIT_PENDING

    def finalize(self, current_time: float):
        for sym, pos in list(self.portfolio.positions.items()):
            if pos.state == PositionState.CLOSED: continue
            tick = self.portfolio.latest_prices.get(sym)
            if not tick: continue
            
            exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
            exit_order = Order(
                order_id=str(uuid.uuid4()), timestamp=current_time, side=exit_side, 
                order_type=OrderType.MARKET, time_in_force=TimeInForce.IOC, quantity=pos.quantity, 
                reduce_only=True, symbol=sym, exit_reason=ExitReason.END_OF_BACKTEST
            )
            self.execution.submit_order(exit_order, current_time)
            
        if self.portfolio.latest_prices:
            for sym, tick in self.portfolio.latest_prices.items():
                fills = self.execution.process_tick(tick, sym)
                for f in fills: self.portfolio.process_fill(f, sym)
