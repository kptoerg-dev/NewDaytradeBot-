import unittest
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
        """Validiert historische Tick-Daten auf mathematische Sinnhaftigkeit."""
        if any(v is None for v in [self.bid, self.ask, self.bid_volume, self.ask_volume]): return False
        if self.bid <= 0 or self.ask <= 0: return False
        if self.ask < self.bid: return False
        if self.bid_volume < 0 or self.ask_volume < 0: return False
        return True

    @property
    def microprice(self) -> float:
        """Volumengewichteter Microprice."""
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
    def calculate_position_size(equity: float, risk_pct: float, entry_price: float, stop_price: float, max_leverage: float, contract_multiplier: float = 1.0) -> float:
        """Positionsgröße basierend auf Stop-Distanz und Max-Leverage absichern."""
        if equity <= 0 or entry_price <= 0 or stop_price <= 0 or entry_price == stop_price:
            return 0.0
            
        stop_distance = abs(entry_price - stop_price)
        risk_capital = equity * risk_pct
        risk_per_unit = stop_distance * contract_multiplier
        
        raw_size = risk_capital / risk_per_unit
        
        max_notional = equity * max_leverage
        max_size = max_notional / (entry_price * contract_multiplier)
        
        final_size = min(raw_size, max_size)
        if math.isnan(final_size) or math.isinf(final_size): 
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
        """Simuliert Netzwerklatenz, bevor die Order das Orderbuch erreicht."""
        # BUGFIX (Problem 1): Validierung von Preisen bei LIMIT und STOP
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
        
        # 1. Orders aktivieren, deren Latenz abgelaufen ist
        for o in self.pending_orders[:]:
            if tick.timestamp >= o.timestamp:
                o.status = OrderStatus.ACTIVE
                self.active_orders.append(o)
                self.pending_orders.remove(o)

        # 2. Tracking des noch verfügbaren Volumens für DIESEN Tick
        avail_bid = tick.bid_volume
        avail_ask = tick.ask_volume

        for o in self.active_orders[:]:
            # BUGFIX (Problem 3): Nur Orders des aktuellen Symbols ausführen
            if o.symbol != symbol:
                continue

            if avail_bid <= 0 and avail_ask <= 0:
                break  # Orderbuch auf diesem Preisniveau leer gesaugt
                
            fill = self._try_execute(o, tick, avail_bid, avail_ask)
            if fill:
                fills.append(fill)
                
                # Volumen abziehen, um Doppelnutzung zu verhindern
                if o.side == Side.LONG:
                    avail_ask = max(0.0, avail_ask - fill.quantity)
                else:
                    avail_bid = max(0.0, avail_bid - fill.quantity)
                
                # Order Status verwalten
                if o.filled_qty >= o.quantity - 1e-8:
                    o.status = OrderStatus.FILLED
                    self.active_orders.remove(o)
                else:
                    o.status = OrderStatus.PARTIAL
                    
        return fills

    def _try_execute(self, order: Order, tick: Tick, avail_bid: float, avail_ask: float) -> Optional[Fill]:
        fill_price = 0.0
        avail_vol = avail_ask if order.side == Side.LONG else avail_bid
        if avail_vol <= 0: return None
        
        exec_price = tick.ask if order.side == Side.LONG else tick.bid

        # Trigger-Logik für Stops
        if order.order_type == OrderType.STOP:
            if order.side == Side.LONG and tick.ask >= order.stop_price:
                order.order_type = OrderType.MARKET
            elif order.side == Side.SHORT and tick.bid <= order.stop_price:
                order.order_type = OrderType.MARKET
            else:
                return None

        # Ausführung Limits & Markets
        if order.order_type == OrderType.LIMIT:
            if order.side == Side.LONG and tick.ask <= order.price:
                fill_price = order.price
            elif order.side == Side.SHORT and tick.bid >= order.price:
                fill_price = order.price
            else:
                return None
                
        elif order.order_type == OrderType.MARKET:
            fill_price = exec_price

        if fill_price > 0:
            qty_to_fill = min(order.quantity - order.filled_qty, avail_vol)
            if qty_to_fill <= 0: return None
            
            slippage_impact = self.slippage_ticks if order.side == Side.LONG else -self.slippage_ticks
            final_fill_price = fill_price + slippage_impact
            
            fee = (qty_to_fill * final_fill_price) * self.fee_rate
            order.filled_qty += qty_to_fill
            
            return Fill(
                order_id=order.order_id,
                timestamp=tick.timestamp,
                side=order.side,
                quantity=qty_to_fill,
                price=final_fill_price,  
                fee=fee,
                slippage=abs(self.slippage_ticks), 
                reduce_only=order.reduce_only,     
                stop_price=order.stop_price,       
                take_profit_price=order.take_profit_price,
                symbol=order.symbol
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
        self.latest_prices: Dict[str, Tick] = {} # Für Multi-Symbol MTM

    def mark_to_market(self, tick: Tick, symbol: str):
        """MTM OHNE Lookahead - berechnet auf Basis der Positionen vor den neuen Fills."""
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
        
        # 1. Keine Position vorhanden
        if symbol not in self.positions or self.positions[symbol].state == PositionState.CLOSED:
            if fill.reduce_only:
                return 
                
            self.positions[symbol] = Position(
                symbol=symbol, side=fill.side, quantity=fill.quantity, entry_price=fill.price,
                stop_loss=fill.stop_price, take_profit=fill.take_profit_price
            )
            return

        pos = self.positions[symbol]

        # 2. Bestehende Position vergrößern (Nachkauf / Pyramiding)
        if pos.side == fill.side:
            if fill.reduce_only: return 
            total_qty = pos.quantity + fill.quantity
            # Average Entry berechnen
            pos.entry_price = ((pos.entry_price * pos.quantity) + (fill.price * fill.quantity)) / total_qty
            pos.quantity = total_qty
            
            # BUGFIX (Problem 5): SL/TP auf die aktuellsten Parameter des neuen Fills anpassen (falls gesetzt)
            if fill.stop_price is not None:
                pos.stop_loss = fill.stop_price
            if fill.take_profit_price is not None:
                pos.take_profit = fill.take_profit_price
            
        # 3. Position reduzieren oder flippen
        else:
            close_qty = min(pos.quantity, fill.quantity)
            
            price_diff = (fill.price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - fill.price)
            realized = price_diff * close_qty * self.contract_multiplier
            
            pos.realized_pnl += realized
            self.cash += realized 
            pos.quantity -= close_qty
            
            self.trade_ledger.append({
                "time": fill.timestamp, 
                "symbol": symbol, 
                "side": pos.side.name,
                "qty": close_qty, 
                "entry_price": pos.entry_price,
                "exit_price": fill.price,
                "pnl": realized, 
                "fee": fill.fee
            })
            
            if pos.quantity <= 1e-8:
                pos.quantity = 0.0
                pos.state = PositionState.CLOSED
                del self.positions[symbol]
                
            remaining_qty = fill.quantity - close_qty
            if remaining_qty > 1e-8:
                if fill.reduce_only:
                    pass 
                else:
                    self.positions[symbol] = Position(
                        symbol=symbol, side=fill.side, quantity=remaining_qty, entry_price=fill.price,
                        stop_loss=fill.stop_price, take_profit=fill.take_profit_price
                    )


# ==========================================
# 5. BACKTEST ENGINE
# ==========================================

class BacktestEngine:
    def __init__(self, initial_capital: float = 10000.0, risk_pct: float = 0.01, max_leverage: float = 2.0, contract_multiplier: float = 1.0):
        # BUGFIX (Problem 2): Contract Multiplier sauber durchreichen
        self.contract_multiplier = contract_multiplier
        self.portfolio = PortfolioManager(initial_capital, contract_multiplier)
        self.execution = ExecutionEngine(latency_ms=0)
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.symbol = "BTCUSD" # Standard-Symbol für Kompatibilität

    def process_ticks(self, ticks: List[Tick], symbol: str = "BTCUSD"):
        # BUGFIX (Problem 3): process_ticks nimmt das Symbol explizit entgegen
        for tick in ticks:
            if not tick.is_valid(): continue
            
            # 1. Mark-to-Market
            self.portfolio.mark_to_market(tick, symbol)
            
            # BUGFIX (Problem 4): Zwangsliquidation bei negativer/null Equity
            if self.portfolio.equity <= 0:
                for sym, pos in self.portfolio.positions.items():
                    if pos.state == PositionState.OPEN:
                        exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
                        liq_order = Order(
                            order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=exit_side, 
                            order_type=OrderType.MARKET, quantity=pos.quantity, reduce_only=True,
                            symbol=sym
                        )
                        self.execution.submit_order(liq_order, tick.timestamp)
                        pos.state = PositionState.CLOSING

            # 2. Execution
            fills = self.execution.process_tick(tick, symbol)
            
            # 3. Portfolio & Ledger Update
            for fill in fills:
                self.portfolio.process_fill(fill, fill.symbol)
                
            # 4. Automatischer SL/TP Check
            self._check_sl_tp(tick, symbol)

    def enter_trade(self, tick: Tick, side: Side, stop_distance: float, tp_distance: float, symbol: str = "BTCUSD"):
        # BUGFIX (Problem 3): symbol wird unterstützt
        current_price = tick.ask if side == Side.LONG else tick.bid
        stop_price = current_price - stop_distance if side == Side.LONG else current_price + stop_distance
        tp_price = current_price + tp_distance if side == Side.LONG else current_price - tp_distance
        
        qty = RiskManager.calculate_position_size(
            equity=self.portfolio.equity, risk_pct=self.risk_pct, 
            entry_price=current_price, stop_price=stop_price, max_leverage=self.max_leverage,
            contract_multiplier=self.contract_multiplier # BUGFIX (Problem 2)
        )
        if qty <= 0: return

        order = Order(
            order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=side, 
            order_type=OrderType.MARKET, quantity=qty,
            stop_price=stop_price, take_profit_price=tp_price, symbol=symbol
        )
        self.execution.submit_order(order, tick.timestamp)

    def _check_sl_tp(self, tick: Tick, symbol: str = "BTCUSD"):
        if symbol not in self.portfolio.positions: return
        pos = self.portfolio.positions[symbol]
        
        if pos.state != PositionState.OPEN: return 
        
        trigger = False
        if pos.side == Side.LONG:
            if pos.stop_loss and tick.bid <= pos.stop_loss: trigger = True
            elif pos.take_profit and tick.bid >= pos.take_profit: trigger = True
        else:
            if pos.stop_loss and tick.ask >= pos.stop_loss: trigger = True
            elif pos.take_profit and tick.ask <= pos.take_profit: trigger = True
            
        if trigger:
            exit_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
            exit_order = Order(
                order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=exit_side, 
                order_type=OrderType.MARKET, quantity=pos.quantity, reduce_only=True,
                symbol=symbol
            )
            self.execution.submit_order(exit_order, tick.timestamp)
            pos.state = PositionState.CLOSING


# ==========================================
# 6. TEST SUITE
# ==========================================

class TestTradingEngine(unittest.TestCase):
    
    def setUp(self):
        self.engine = BacktestEngine(initial_capital=10000.0)
        self.engine.execution.fee_rate = 0.0 
        self.engine.execution.slippage_ticks = 0.0

    def test_critical_reduce_only_no_flip(self):
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        fill_long = Fill("o1", 1.0, Side.LONG, 10.0, 100.0, 0, 0, reduce_only=False)
        self.engine.portfolio.process_fill(fill_long, self.engine.symbol)
        
        fill_short = Fill("o2", 2.0, Side.SHORT, 25.0, 105.0, 0, 0, reduce_only=True)
        self.engine.portfolio.process_fill(fill_short, self.engine.symbol)
        
        self.assertNotIn(self.engine.symbol, self.engine.portfolio.positions)
        self.assertEqual(self.engine.portfolio.cash, 10050.0) 

    def test_critical_sl_tp_transfer_and_trigger(self):
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1]) 
        
        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(pos.stop_loss, 95.0) 
        
        t2 = Tick(2.0, 94.0, 95.0, 100, 100)
        self.engine.process_ticks([t2])
        
        self.assertEqual(pos.state, PositionState.CLOSING)
        self.assertEqual(len(self.engine.execution.pending_orders), 1)
        self.assertTrue(self.engine.execution.pending_orders[0].reduce_only)

    def test_critical_no_double_slippage(self):
        self.engine.execution.slippage_ticks = 2.0 
        order = Order("id", 1.0, Side.LONG, OrderType.MARKET, 5.0)
        self.engine.execution.submit_order(order, 1.0)
        
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.process_ticks([t1])
        
        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(pos.entry_price, 102.0)
        self.assertEqual(self.engine.portfolio.cash, 10000.0)

    def test_liquidity_consumption(self):
        o1 = Order("id1", 1.0, Side.LONG, OrderType.MARKET, 10.0)
        o2 = Order("id2", 1.0, Side.LONG, OrderType.MARKET, 10.0)
        self.engine.execution.submit_order(o1, 1.0)
        self.engine.execution.submit_order(o2, 1.0)
        
        t1 = Tick(1.0, 100.0, 100.0, 100, 12.0)
        fills = self.engine.execution.process_tick(t1)
        
        self.assertEqual(len(fills), 2)
        self.assertEqual(fills[0].quantity, 10.0) 
        self.assertEqual(fills[1].quantity, 2.0)  

    def test_gap_slippage_on_stop(self):
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1])
        
        t2 = Tick(2.0, 80.0, 81.0, 100, 100)
        self.engine.process_ticks([t2])
        
        t3 = Tick(3.0, 80.0, 81.0, 100, 100)
        self.engine.process_ticks([t3])
        
        ledger_entry = self.engine.portfolio.trade_ledger[0]
        self.assertEqual(ledger_entry['exit_price'], 80.0)
        self.assertEqual(ledger_entry['pnl'], -20.0 * ledger_entry['qty'])

    # --- NEUE TESTS (Problem 6 Fixes) ---

    def test_order_rejection_on_missing_price(self):
        """AUDIT FIX (Prob 1): LIMIT/STOP Orders ohne passendes Preis-Feld werden abgelehnt."""
        order_limit = Order("id_lim", 1.0, Side.LONG, OrderType.LIMIT, 5.0) # Ohne price
        order_stop = Order("id_stop", 1.0, Side.LONG, OrderType.STOP, 5.0)  # Ohne stop_price
        
        self.engine.execution.submit_order(order_limit, 1.0)
        self.engine.execution.submit_order(order_stop, 1.0)
        
        self.assertEqual(order_limit.status, OrderStatus.REJECTED)
        self.assertEqual(order_stop.status, OrderStatus.REJECTED)
        self.assertEqual(len(self.engine.execution.pending_orders), 0)

    def test_pyramiding_updates_sl_tp(self):
        """AUDIT FIX (Prob 5): Pyramiding aktualisiert Average Entry und überschreibt SL/TP mit den neusten Werten."""
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        fill1 = Fill("1", 1.0, Side.LONG, 10.0, 100.0, 0, 0, reduce_only=False, stop_price=90.0, take_profit_price=110.0)
        self.engine.portfolio.process_fill(fill1, self.engine.symbol)

        # Nachkauf mit neuen, engeren Stops
        fill2 = Fill("2", 2.0, Side.LONG, 10.0, 110.0, 0, 0, reduce_only=False, stop_price=100.0, take_profit_price=120.0)
        self.engine.portfolio.process_fill(fill2, self.engine.symbol)

        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(pos.quantity, 20.0)
        self.assertEqual(pos.entry_price, 105.0)
        self.assertEqual(pos.stop_loss, 100.0)
        self.assertEqual(pos.take_profit, 120.0)

    def test_multi_symbol_support(self):
        """AUDIT FIX (Prob 3): Paralleler Handel mehrerer Symbole inkl. kombiniertem Mark-to-Market."""
        t1_btc = Tick(1.0, 100.0, 100.0, 100, 100)
        t1_eth = Tick(1.0, 50.0, 50.0, 100, 100)

        # BTC Trade (Stop = 5 Punkte) -> Risk $100 -> Qty = 20
        self.engine.enter_trade(t1_btc, Side.LONG, 5.0, 10.0, symbol="BTCUSD")
        self.engine.process_ticks([t1_btc], symbol="BTCUSD")

        # ETH Trade (Stop = 2 Punkte) -> Risk $100 -> Qty = 50
        self.engine.enter_trade(t1_eth, Side.LONG, 2.0, 5.0, symbol="ETHUSD")
        self.engine.process_ticks([t1_eth], symbol="ETHUSD")

        self.assertIn("BTCUSD", self.engine.portfolio.positions)
        self.assertIn("ETHUSD", self.engine.portfolio.positions)

        # MTM Check: Beide Ticks +10 Punkte
        t2_btc = Tick(2.0, 110.0, 110.0, 100, 100) # +10 Punkte * 20 Qty = +200 Unrealized
        t2_eth = Tick(2.0, 60.0, 60.0, 100, 100)   # +10 Punkte * 50 Qty = +500 Unrealized
        
        self.engine.process_ticks([t2_btc], symbol="BTCUSD")
        self.engine.process_ticks([t2_eth], symbol="ETHUSD")

        # Gesamtes Portfolio = 10000 + 200 + 500 = 10700
        self.assertEqual(self.engine.portfolio.equity, 10700.0)

    def test_liquidation_on_negative_equity(self):
        """AUDIT FIX (Prob 4): Extreme Gaps unter 0 Equity lösen Zwangsliquidation aus."""
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.max_leverage = 10.0 # Erlaube riesige Position für den Test
        self.engine.risk_pct = 1.0 # Setze komplettes Account-Equity als Risiko
        
        # Risk 100% per trade (10k Equity). Stop Distance 10. Qty = 1000 (Ausgereizt am Max Leverage).
        self.engine.enter_trade(t1, Side.LONG, stop_distance=10.0, tp_distance=50.0)
        self.engine.process_ticks([t1])

        # Extremer Crash: Preis fällt von 100 auf 80 (Verlust = 20 * 1000 = -20000)
        t2 = Tick(2.0, 80.0, 81.0, 100, 100)
        self.engine.process_ticks([t2]) # Verarbeitet den Gap und löst sofort Liquidation aus

        pos = self.engine.portfolio.positions[self.engine.symbol]
        # Position muss im CLOSING Status sein, Liquidation-Order muss existieren
        self.assertEqual(pos.state, PositionState.CLOSING) 
        self.assertEqual(len(self.engine.execution.pending_orders), 1)
        self.assertTrue(self.engine.portfolio.equity <= 0)

    def test_limit_order_partial_fill(self):
        """AUDIT FIX (Prob 6): LIMIT Orders füllen sich strikt nach Liquidität über mehrere Ticks."""
        order = Order("limit1", 1.0, Side.LONG, OrderType.LIMIT, 10.0, price=99.0)
        self.engine.execution.submit_order(order, 1.0)

        # Tick 1: Preis berührt das Limit (99.0), aber nur 4 Lots sind auf der Gegenseite da
        t1 = Tick(1.0, 99.0, 99.0, 10, 4.0)
        fills1 = self.engine.execution.process_tick(t1)
        self.assertEqual(len(fills1), 1)
        self.assertEqual(fills1[0].quantity, 4.0)
        self.assertEqual(order.status, OrderStatus.PARTIAL)

        # Tick 2: Weiterer Tick auf 99.0, Rest der Order (6 Lots) wird gefüllt
        t2 = Tick(2.0, 99.0, 99.0, 10, 10.0)
        fills2 = self.engine.execution.process_tick(t2)
        self.assertEqual(len(fills2), 1)
        self.assertEqual(fills2[0].quantity, 6.0)
        self.assertEqual(order.status, OrderStatus.FILLED)

if __name__ == '__main__':
    unittest.main(verbosity=2)
