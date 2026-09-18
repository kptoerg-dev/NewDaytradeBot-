import unittest
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


@dataclass
class Fill:
    order_id: str
    timestamp: float
    side: Side
    quantity: float
    price: float  # Beinhaltet bereits die Slippage!
    fee: float
    slippage: float  # Nur informativ, darf NICHT mehr vom Cash abgezogen werden
    # BUGFIX: reduce_only und SL/TP-Specs direkt an den Fill binden.
    reduce_only: bool 
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None


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
        order.timestamp = current_time + self.latency_sec
        order.status = OrderStatus.PENDING
        self.pending_orders.append(order)

    def process_tick(self, tick: Tick) -> List[Fill]:
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
            # STOP Trigger: Market-Order Aktivierung. 
            # Nutzt danach den exec_price (aktuelle Liquidität), was realistische Gap-Slippage erzeugt.
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
            
            # Slippage wird DIREKT in den Preis eingerechnet.
            slippage_impact = self.slippage_ticks if order.side == Side.LONG else -self.slippage_ticks
            final_fill_price = fill_price + slippage_impact
            
            fee = (qty_to_fill * final_fill_price) * self.fee_rate
            order.filled_qty += qty_to_fill
            
            return Fill(
                order_id=order.order_id,
                timestamp=tick.timestamp,
                side=order.side,
                quantity=qty_to_fill,
                price=final_fill_price,  # <-- Inklusive Slippage!
                fee=fee,
                slippage=abs(self.slippage_ticks), # Nur Reporting
                reduce_only=order.reduce_only,     # <-- Rettet das Flag vor dem Order-Tod!
                stop_price=order.stop_price,       # <-- Transfer auf Fill
                take_profit_price=order.take_profit_price
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

    def mark_to_market(self, tick: Tick, symbol: str):
        """MTM OHNE Lookahead - berechnet auf Basis der Positionen vor den neuen Fills."""
        unrealized = 0.0
        if symbol in self.positions and self.positions[symbol].state != PositionState.CLOSED:
            pos = self.positions[symbol]
            current_price = tick.bid if pos.side == Side.LONG else tick.ask
            price_diff = (current_price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - current_price)
            unrealized = price_diff * pos.quantity * self.contract_multiplier
            
        self.equity = self.cash + unrealized

    def process_fill(self, fill: Fill, symbol: str):
        # Wir ziehen NUR die Fee ab. Slippage ist bereits im fill.price eingepreist.
        self.cash -= fill.fee 
        
        # 1. Keine Position vorhanden
        if symbol not in self.positions or self.positions[symbol].state == PositionState.CLOSED:
            if fill.reduce_only:
                return # Reduzierung einer Geister-Position -> ignorieren
                
            # Wir übernehmen SL/TP nun direkt aus dem robusten Fill-Objekt
            self.positions[symbol] = Position(
                symbol=symbol, side=fill.side, quantity=fill.quantity, entry_price=fill.price,
                stop_loss=fill.stop_price, take_profit=fill.take_profit_price
            )
            return

        pos = self.positions[symbol]

        # 2. Bestehende Position vergrößern (Nachkauf)
        if pos.side == fill.side:
            if fill.reduce_only: return # Sicherheitsnetz
            total_qty = pos.quantity + fill.quantity
            # Average Entry berechnen
            pos.entry_price = ((pos.entry_price * pos.quantity) + (fill.price * fill.quantity)) / total_qty
            pos.quantity = total_qty
            
        # 3. Position reduzieren oder flippen
        else:
            close_qty = min(pos.quantity, fill.quantity)
            
            # Realized PnL verbuchen
            price_diff = (fill.price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - fill.price)
            realized = price_diff * close_qty * self.contract_multiplier
            
            pos.realized_pnl += realized
            self.cash += realized # Cash-Zuwachs durch PnL
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
            
            # Position aufräumen
            if pos.quantity <= 1e-8:
                pos.quantity = 0.0
                pos.state = PositionState.CLOSED
                del self.positions[symbol]
                
            # Handhabung von Overfills / Flips
            remaining_qty = fill.quantity - close_qty
            if remaining_qty > 1e-8:
                if fill.reduce_only:
                    # KRITISCH: Bei reduce_only lassen wir den Restbetrag rigoros unter den Tisch fallen. 
                    # Niemals in Gegenrichtung flippen!
                    pass 
                else:
                    # Echtes Flippen in neue Position
                    self.positions[symbol] = Position(
                        symbol=symbol, side=fill.side, quantity=remaining_qty, entry_price=fill.price,
                        stop_loss=fill.stop_price, take_profit=fill.take_profit_price
                    )


# ==========================================
# 5. BACKTEST ENGINE
# ==========================================

class BacktestEngine:
    def __init__(self, initial_capital: float = 10000.0, risk_pct: float = 0.01, max_leverage: float = 2.0):
        self.portfolio = PortfolioManager(initial_capital)
        self.execution = ExecutionEngine(latency_ms=0) # Latenz standardmäßig 0 für einfache Tests
        self.risk_pct = risk_pct
        self.max_leverage = max_leverage
        self.symbol = "BTCUSD"

    def process_ticks(self, ticks: List[Tick]):
        for tick in ticks:
            if not tick.is_valid(): continue
            
            # 1. Mark-to-Market (ohne Lookahead)
            self.portfolio.mark_to_market(tick, self.symbol)
            
            # 2. Execution (generiert Fills)
            fills = self.execution.process_tick(tick)
            
            # 3. Portfolio & Ledger Update
            for fill in fills:
                self.portfolio.process_fill(fill, self.symbol)
                
            # 4. Automatischer SL/TP Check (greift erst für den *nächsten* Tick)
            self._check_sl_tp(tick)

    def enter_trade(self, tick: Tick, side: Side, stop_distance: float, tp_distance: float):
        current_price = tick.ask if side == Side.LONG else tick.bid
        stop_price = current_price - stop_distance if side == Side.LONG else current_price + stop_distance
        tp_price = current_price + tp_distance if side == Side.LONG else current_price - tp_distance
        
        # Nutzung des obligatorischen Risk-Managers
        qty = RiskManager.calculate_position_size(
            equity=self.portfolio.equity, risk_pct=self.risk_pct, 
            entry_price=current_price, stop_price=stop_price, max_leverage=self.max_leverage
        )
        if qty <= 0: return

        order = Order(
            order_id=str(uuid.uuid4()), timestamp=tick.timestamp, side=side, 
            order_type=OrderType.MARKET, quantity=qty,
            stop_price=stop_price, take_profit_price=tp_price
        )
        self.execution.submit_order(order, tick.timestamp)

    def _check_sl_tp(self, tick: Tick):
        if self.symbol not in self.portfolio.positions: return
        pos = self.portfolio.positions[self.symbol]
        
        # Verhindert Spamming, falls eine Schließung bereits im Gang ist
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
                order_type=OrderType.MARKET, quantity=pos.quantity, reduce_only=True
            )
            self.execution.submit_order(exit_order, tick.timestamp)
            pos.state = PositionState.CLOSING


# ==========================================
# 6. TEST SUITE
# ==========================================

class TestTradingEngine(unittest.TestCase):
    
    def setUp(self):
        self.engine = BacktestEngine(initial_capital=10000.0)
        self.engine.execution.fee_rate = 0.0 # Fees genullt für klarere Assertions
        self.engine.execution.slippage_ticks = 0.0

    def test_critical_reduce_only_no_flip(self):
        """CRITICAL FIX: Eine Order mit reduce_only darf NIEMALS flippen."""
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        
        # 1. Manuell Position LONG 10 erzeugen
        fill_long = Fill("o1", 1.0, Side.LONG, 10.0, 100.0, 0, 0, reduce_only=False)
        self.engine.portfolio.process_fill(fill_long, self.engine.symbol)
        self.assertEqual(self.engine.portfolio.positions[self.engine.symbol].quantity, 10.0)
        
        # 2. Übergroßen Fill SHORT 25 mit reduce_only=True schicken (z.B. Bug in Order-Logik)
        fill_short = Fill("o2", 2.0, Side.SHORT, 25.0, 105.0, 0, 0, reduce_only=True)
        self.engine.portfolio.process_fill(fill_short, self.engine.symbol)
        
        # 3. Assertions: Position muss weg sein, darf NICHT Short 15 sein!
        self.assertNotIn(self.engine.symbol, self.engine.portfolio.positions)
        self.assertEqual(self.engine.portfolio.cash, 10050.0) # 10000 + (5 PnL * 10 Qty)

    def test_critical_sl_tp_transfer_and_trigger(self):
        """CRITICAL FIX: SL/TP muss auf die Position übergehen und bei Kurssturz auslösen."""
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        
        # Wir enteren LONG. Ask ist 100. Stop-Distance ist 5 -> SL bei 95.
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1]) # Fill generieren
        
        pos = self.engine.portfolio.positions[self.engine.symbol]
        # Beweist, dass der Transfer vom Order -> Fill -> Position geklappt hat
        self.assertEqual(pos.stop_loss, 95.0) 
        
        # Crash Tick unter Stop Loss
        t2 = Tick(2.0, 94.0, 95.0, 100, 100)
        self.engine.process_ticks([t2])
        
        # Beweist, dass Check-Logic ausgelöst und CLOSING Status aktiviert hat
        self.assertEqual(pos.state, PositionState.CLOSING)
        self.assertEqual(len(self.engine.execution.pending_orders), 1)
        self.assertTrue(self.engine.execution.pending_orders[0].reduce_only)

    def test_critical_no_double_slippage(self):
        """CRITICAL FIX: Slippage wird in den Preis gerechnet und nicht noch mal vom Cash abgezogen."""
        self.engine.execution.slippage_ticks = 2.0 # 2 Ticks Slippage konfigurieren
        
        order = Order("id", 1.0, Side.LONG, OrderType.MARKET, 5.0)
        self.engine.execution.submit_order(order, 1.0)
        
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.process_ticks([t1])
        
        # Ask = 100 + 2 Slippage = Fill_Price 102
        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(pos.entry_price, 102.0)
        
        # Cash muss unverändert sein (wird erst beim Realized PnL wirksam)
        # Bzw. Fee=0, Slippage wurde nicht als Kost abgezogen!
        self.assertEqual(self.engine.portfolio.cash, 10000.0)

    def test_liquidity_consumption(self):
        """Standard: Das Volumen im Orderbuch darf im gleichen Tick nicht doppelt gespendet werden."""
        o1 = Order("id1", 1.0, Side.LONG, OrderType.MARKET, 10.0)
        o2 = Order("id2", 1.0, Side.LONG, OrderType.MARKET, 10.0)
        self.engine.execution.submit_order(o1, 1.0)
        self.engine.execution.submit_order(o2, 1.0)
        
        # Nur 12 Ask-Volumen verfügbar
        t1 = Tick(1.0, 100.0, 100.0, 100, 12.0)
        fills = self.engine.execution.process_tick(t1)
        
        self.assertEqual(len(fills), 2)
        self.assertEqual(fills[0].quantity, 10.0) # Order 1 nimmt 10
        self.assertEqual(fills[1].quantity, 2.0)  # Order 2 bekommt den Rest (2)

    def test_gap_slippage_on_stop(self):
        """AUDIT FIX: Beweist, dass Gaps den Execution-Preis realistisch verschlechtern."""
        # 1. Position LONG bei Ask=100. Stop-Loss liegt bei 95.
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1])
        
        # 2. Crash (Wochenend-Gap). Bid öffnet bei 80.
        # Stop-Loss (95) ist gebrochen. Exit-Order wird generiert.
        t2 = Tick(2.0, 80.0, 81.0, 100, 100)
        self.engine.process_ticks([t2])
        
        # 3. Nächster Tick führt die generierte Stop-Order aus.
        t3 = Tick(3.0, 80.0, 81.0, 100, 100)
        self.engine.process_ticks([t3])
        
        # 4. Der Fill-Preis (Exit) MUSS bei 80.0 liegen, NICHT beim magischen SL von 95.0.
        ledger_entry = self.engine.portfolio.trade_ledger[0]
        self.assertEqual(ledger_entry['exit_price'], 80.0)
        self.assertEqual(ledger_entry['pnl'], -20.0 * ledger_entry['qty']) # 20 Punkte Verlust pro Lot

if __name__ == '__main__':
    unittest.main(verbosity=2)
