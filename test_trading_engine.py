import unittest
import math
from trading_engine import (
    BacktestEngine, Tick, Side, Order, OrderType, OrderStatus, Fill, 
    PositionState, TimeInForce, ExitReason, PerformanceAnalytics
)

class TestTradingEngine(unittest.TestCase):
    
    def setUp(self):
        self.engine = BacktestEngine(initial_capital=10000.0, tick_size=1.0)
        self.engine.execution.fee_rate = 0.0 
        self.engine.execution.slippage_ticks = 0.0
        self.sym = "BTCUSD"

    def test_data_validation_nan_inf(self):
        t1 = Tick(1.0, float('nan'), 100.0, 100, 100)
        self.assertFalse(t1.is_valid())
        t2 = Tick(1.0, 100.0, float('inf'), 100, 100)
        self.assertFalse(t2.is_valid())
        
    def test_timestamp_regression_rejection(self):
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        t2 = Tick(0.5, 100.0, 100.0, 100, 100)
        self.engine.process_ticks([t1])
        with self.assertRaises(ValueError):
            self.engine.process_ticks([t2])

    def test_order_validation_rejects(self):
        order = Order("inv", 1.0, Side.LONG, OrderType.MARKET, -10.0)
        self.engine.execution.submit_order(order, 1.0)
        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertEqual(order.reject_reason, "INVALID_QUANTITY")

    def test_critical_reduce_only_no_flip(self):
        fill_long = Fill("o1", 1.0, Side.LONG, 10.0, 100.0, 0, 0, reduce_only=False)
        self.engine.portfolio.process_fill(fill_long, self.sym)
        fill_short = Fill("o2", 2.0, Side.SHORT, 25.0, 105.0, 0, 0, reduce_only=True)
        self.engine.portfolio.process_fill(fill_short, self.sym)
        self.assertNotIn(self.sym, self.engine.portfolio.positions)
        self.assertEqual(self.engine.portfolio.cash, 10050.0) 

    def test_critical_sl_tp_transfer_and_trigger(self):
        t1 = Tick(1.0, 100.0, 100.0, 1000, 1000)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1]) 
        pos = self.engine.portfolio.positions[self.sym]
        self.assertEqual(pos.stop_loss, 95.0) 
        
        t2 = Tick(2.0, 94.0, 95.0, 1000, 1000)
        self.engine.process_ticks([t2])
        self.assertEqual(pos.state, PositionState.EXIT_PENDING)

    def test_slippage_via_tick_size(self):
        self.engine.execution.tick_size = 0.5
        self.engine.execution.slippage_ticks = 2.0 
        order = Order("id", 1.0, Side.LONG, OrderType.MARKET, 5.0)
        self.engine.execution.submit_order(order, 1.0)
        
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.process_ticks([t1])
        
        pos = self.engine.portfolio.positions[self.sym]
        self.assertEqual(pos.entry_price, 101.0)  # 100.0 + (2.0 * 0.5)

    def test_liquidity_consumption_ioc(self):
        o1 = Order("id1", 1.0, Side.LONG, OrderType.MARKET, quantity=10.0, time_in_force=TimeInForce.IOC)
        o2 = Order("id2", 1.0, Side.LONG, OrderType.MARKET, quantity=10.0, time_in_force=TimeInForce.IOC)
        self.engine.execution.submit_order(o1, 1.0)
        self.engine.execution.submit_order(o2, 1.0)
        
        t1 = Tick(1.0, 100.0, 100.0, 100, 12.0)
        fills = self.engine.execution.process_tick(t1, self.sym)
        self.assertEqual(len(fills), 2)
        self.assertEqual(fills[0].quantity, 10.0) 
        self.assertEqual(fills[1].quantity, 2.0)
        self.assertEqual(o2.status, OrderStatus.CANCELED)

    def test_gap_slippage_on_stop(self):
        t1 = Tick(1.0, 100.0, 100.0, 1000, 1000)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1])
        
        t2 = Tick(2.0, 80.0, 81.0, 1000, 1000)
        self.engine.process_ticks([t2])
        t3 = Tick(3.0, 80.0, 81.0, 1000, 1000)
        self.engine.process_ticks([t3])
        
        ledger_entry = self.engine.portfolio.trade_ledger[0]
        self.assertEqual(ledger_entry['exit_price'], 80.0)
        self.assertEqual(ledger_entry['exit_reason'], ExitReason.STOP_LOSS.name)

    def test_limit_order_partial_fill(self):
        order = Order("limit1", 1.0, Side.LONG, OrderType.LIMIT, quantity=10.0, time_in_force=TimeInForce.GTC, price=99.0)
        self.engine.execution.submit_order(order, 1.0)
        t1 = Tick(1.0, 99.0, 99.0, 10, 4.0)
        self.engine.execution.process_tick(t1, self.sym)
        self.assertEqual(order.status, OrderStatus.PARTIAL)
        t2 = Tick(2.0, 99.0, 99.0, 10, 10.0)
        self.engine.execution.process_tick(t2, self.sym)
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_portfolio_leverage_cap_with_pending(self):
        self.engine.max_leverage = 2.0 
        order1 = Order("p1", 1.0, Side.LONG, OrderType.LIMIT, quantity=150.0, price=100.0)
        self.engine.execution.submit_order(order1, 1.0)
        
        t1 = Tick(1.0, 100.0, 100.0, 1000, 1000)
        self.engine.portfolio.mark_to_market(t1, self.sym)
        
        exposure = self.engine.portfolio.get_exposure(self.engine.execution)
        self.assertEqual(exposure, 15000.0)
        
        # Will fail because equity=10k, max leverage 2x -> max notional 20k. 
        # 15k is reserved, only 5k left. Risk Manager will cap position.
        qty = self.engine.portfolio.max_leverage * self.engine.portfolio.equity - exposure
        self.assertEqual(qty, 5000.0)

    def test_audit_fix_1_liquidation_triggers_properly(self):
        t1 = Tick(1.0, 100.0, 100.0, 1000, 1000)
        self.engine.max_leverage = 10.0 
        self.engine.risk_pct = 1.0 
        self.engine.enter_trade(t1, Side.LONG, stop_distance=10.0, tp_distance=50.0)
        self.engine.process_ticks([t1])
        
        # Dump price to trigger margin call (equity < 50% initial margin)
        t2 = Tick(2.0, 92.0, 92.0, 1000, 1000)
        self.engine.process_ticks([t2])
        pos = self.engine.portfolio.positions[self.sym]
        self.assertEqual(pos.state, PositionState.EXIT_PENDING)

    def test_audit_fix_3_no_zombie_state_healing(self):
        """Beweist, dass eine getriggerte SL Order NICHT durch Retracement gelöscht wird (Lifecycle Fix)."""
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1])
        
        # Trigger SL, aber keine Liquidität! Market Sell Order wird erstellt (TIF = GTC)
        t2 = Tick(2.0, 94.0, 95.0, 0, 0)
        self.engine.process_ticks([t2])
        self.assertEqual(len(self.engine.execution.active_orders), 1)
        
        # Retracement nach oben. 
        # Engine MUSS die Order im Orderbuch lassen, da Stop bereits getriggert.
        t3 = Tick(3.0, 105.0, 106.0, 100, 100)
        self.engine.process_ticks([t3])
        
        # Order hat gefüllt, Position ist geschlossen (Trade-Lifecycle garantiert).
        self.assertEqual(len(self.engine.portfolio.positions), 0)
        ledger = self.engine.portfolio.trade_ledger[-1]
        self.assertEqual(ledger["exit_reason"], ExitReason.STOP_LOSS.name)

    def test_performance_analytics(self):
        self.engine.portfolio.trade_ledger = [
            {"pnl": 100, "fee": 1},
            {"pnl": -50, "fee": 1},
            {"pnl": 200, "fee": 1},
        ]
        self.engine.portfolio.equity_curve = [
            {"equity": 10000}, {"equity": 10100}, {"equity": 10050}, {"equity": 10250}
        ]
        stats = PerformanceAnalytics.calculate(self.engine.portfolio.trade_ledger, self.engine.portfolio.equity_curve)
        
        self.assertEqual(stats["Total Return"], 247)
        self.assertAlmostEqual(stats["Win Rate"], 2/3)
        self.assertEqual(stats["Max Drawdown"], 50)
        self.assertEqual(stats["Profit Factor"], 300 / 50)

    def test_finalize_end_of_backtest(self):
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.enter_trade(t1, Side.LONG, 10.0, 10.0)
        self.engine.process_ticks([t1])
        
        self.assertEqual(len(self.engine.portfolio.positions), 1)
        self.engine.finalize(current_time=2.0)
        
        self.assertEqual(len(self.engine.portfolio.positions), 0)
        self.assertEqual(self.engine.portfolio.trade_ledger[-1]["exit_reason"], ExitReason.END_OF_BACKTEST.name)

if __name__ == '__main__':
    unittest.main(verbosity=2)
