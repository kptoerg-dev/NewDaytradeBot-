from engine.datatypes import Tick, Order, Side, OrderType
from engine.execution import ExecutionEngine
from engine.portfolio import PortfolioManager
from engine.risk import RiskManager
import uuid

class BacktestEngine:
    def __init__(self, initial_capital: float = 10000.0):
        self.portfolio = PortfolioManager(initial_capital)
        self.execution = ExecutionEngine()
        self.risk = RiskManager()
        self.symbol = "BTCUSD"

    def process_data(self, data: list[Tick]):
        for tick in data:
            if not tick.is_valid():
                continue # Phase 10: Datenvalidierung
            
            # 1. MTM Update
            self.portfolio.mark_to_market(tick, self.symbol)
            
            # 2. Execution Update (Fills generieren)
            fills = self.execution.process_tick(tick)
            
            # 3. Portfolio Update durch Fills
            for fill in fills:
                order = next((o for o in self.execution.active_orders + self.execution.pending_orders if o.order_id == fill.order_id), None)
                is_reduce_only = order.reduce_only if order else False
                self.portfolio.update_from_fill(fill, self.symbol, is_reduce_only)

            # 4. SL / TP Check für offene Positionen (Phase 6)
            self._check_sl_tp(tick)

    def _check_sl_tp(self, tick: Tick):
        if self.symbol not in self.portfolio.positions: return
        pos = self.portfolio.positions[self.symbol]
        
        exit_order = None
        if pos.side == Side.LONG:
            if pos.stop_loss and tick.bid <= pos.stop_loss:
                exit_order = Order(str(uuid.uuid4()), tick.timestamp, Side.SHORT, OrderType.MARKET, pos.quantity, reduce_only=True)
            elif pos.take_profit and tick.bid >= pos.take_profit:
                exit_order = Order(str(uuid.uuid4()), tick.timestamp, Side.SHORT, OrderType.MARKET, pos.quantity, reduce_only=True)
        else:
            if pos.stop_loss and tick.ask >= pos.stop_loss:
                exit_order = Order(str(uuid.uuid4()), tick.timestamp, Side.LONG, OrderType.MARKET, pos.quantity, reduce_only=True)
            elif pos.take_profit and tick.ask <= pos.take_profit:
                exit_order = Order(str(uuid.uuid4()), tick.timestamp, Side.LONG, OrderType.MARKET, pos.quantity, reduce_only=True)

        # Prüfen ob bereits eine Exit Order in Pending/Active existiert
        if exit_order and not any(o.reduce_only for o in self.execution.pending_orders + self.execution.active_orders):
            self.execution.submit_order(exit_order, tick.timestamp)

    def enter_trade(self, current_tick: Tick, side: Side, stop_distance: float, tp_distance: float):
        # Phase 2: Die Engine MUSS den Risk Manager benutzen
        current_price = current_tick.ask if side == Side.LONG else current_tick.bid
        stop_price = current_price - stop_distance if side == Side.LONG else current_price + stop_distance
        tp_price = current_price + tp_distance if side == Side.LONG else current_price - tp_distance
        
        qty = self.risk.calculate_position_size(self.portfolio.equity, current_price, stop_price, self.portfolio.contract_multiplier)
        if qty <= 0: return

        order = Order(str(uuid.uuid4()), current_tick.timestamp, side, OrderType.MARKET, qty, stop_price=stop_price, take_profit_price=tp_price)
        self.execution.submit_order(order, current_tick.timestamp)
        
        # Info: SL und TP werden im echten System der Position zugewiesen, 
        # sobald die Order durch PortfolioManager gefüllt wird.
        # (Im Rahmen des LLM-Limits hier via Post-Fill Update im Backtester zu handhaben)
