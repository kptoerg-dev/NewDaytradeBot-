import unittest
import math
from trading_engine import (
    BacktestEngine, Tick, Side, Order, OrderType, OrderStatus, Fill,
    PositionState, TimeInForce, ExitReason, PerformanceAnalytics,
    RiskManager, PortfolioManager, MakerTakerFeeModel, ConstantSlippage
)

try:
    from hypothesis import given, settings, strategies as st
    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

class TestTradingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = BacktestEngine(initial_capital=10000.0, tick_size=1.0)
        self.engine.execution.fee_model = MakerTakerFeeModel(0.0, 0.0)
        self.engine.execution.slippage_model = ConstantSlippage(1.0, 0.0)
        self.sym = "BTCUSD"

    def test_data_validation_nan_inf(self):
        self.assertFalse(Tick(1.0, "BTC", float('nan'), 100.0, 100, 100).is_valid())
        self.assertTrue(Tick(1.0, "BTC", 100.0, 100.0, 0, 0).is_valid())

    def test_timestamp_regression_rejection(self):
        t1 = Tick(1.0, "BTC", 100.0, 100.0, 100, 100)
        t3 = Tick(0.5, "BTC", 100.0, 100.0, 100, 100)
        self.engine.process_ticks([t1])
        with self.assertRaises(ValueError):
            self.engine.process_ticks([t3])

    def test_c3_reduce_only_overshoot_capping(self):
        fill_long = Fill("o1", 1.0, Side.LONG, 10.0, 100.0, 10.0, 0, False, "BTC")
        self.assertTrue(self.engine.portfolio.process_fill(fill_long))
        
        fill_short = Fill("o2", 2.0, Side.SHORT, 15.0, 105.0, 15.0, 0, True, "BTC")
        self.assertTrue(self.engine.portfolio.process_fill(fill_short))
        self.assertNotIn("BTC", self.engine.portfolio.positions)
        self.assertEqual(fill_short.quantity, 10.0)
        self.assertEqual(fill_short.fee, 10.0)

    def test_c1_cross_symbol_ioc_cancel(self):
        eth_order = Order("eth1", 1.0, Side.LONG, OrderType.MARKET, 1.0, "ETH", TimeInForce.IOC)
        self.engine.execution.submit_order(eth_order)
        self.engine.process_ticks([Tick(1.0, "BTC", 100.0, 100.0, 100, 100)])
        self.assertIn("eth1", self.engine.execution.active_orders)

    def test_c2_interleaved_multi_symbol_ticks(self):
        t1 = Tick(10.0, "BTC", 100, 100, 10, 10)
        t2 = Tick(2.0, "ETH", 200, 200, 10, 10)
        t3 = Tick(11.0, "BTC", 101, 101, 10, 10)
        try:
            self.engine.process_ticks([t1, t2, t3])
        except ValueError:
            self.fail("Multi-Symbol Ticks failed on disjoint timestamps")

    def test_h1_sizing_considers_slippage(self):
        self.engine.execution.slippage_model = ConstantSlippage(1.0, 5.0)
        t1 = Tick(1.0, "BTC", 100.0, 100.0, 1000, 1000)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=10.0, tp_distance=20.0)
        order = list(self.engine.execution.pending_orders.values())[0]
        self.assertEqual(order.stop_price, 95.0)
        self.assertEqual(order.take_profit_price, 125.0)

    def test_h2_marketable_limit_slippage(self):
        self.engine.execution.slippage_model = ConstantSlippage(1.0, 10.0)
        order = Order("l1", 1.0, Side.LONG, OrderType.LIMIT, 1.0, "BTC", TimeInForce.GTC, price=105.0)
        self.engine.execution.submit_order(order)
        t = Tick(1.0, "BTC", 99.0, 100.0, 10, 10)
        fills = self.engine.execution.process_tick(t)
        self.assertEqual(fills[0].price, 105.0)

    def test_h3_maker_taker_fee(self):
        self.engine.execution.fee_model = MakerTakerFeeModel(maker_rate=0.01, taker_rate=0.05)
        o_taker = Order("t1", 1.0, Side.LONG, OrderType.MARKET, 1.0, "BTC")
        self.engine.execution.submit_order(o_taker)
        o_maker = Order("m1", 1.0, Side.LONG, OrderType.LIMIT, 1.0, "ETH", TimeInForce.GTC, price=90.0)
        self.engine.execution.submit_order(o_maker)
        
        f1 = self.engine.execution.process_tick(Tick(1.0, "BTC", 100.0, 100.0, 10, 10))
        self.assertFalse(f1[0].is_maker)
        self.assertAlmostEqual(f1[0].fee, 100.0 * 0.05)

        self.engine.execution.process_tick(Tick(2.0, "ETH", 90.0, 90.0, 10, 10))
        self.engine.execution.process_tick(Tick(3.0, "ETH", 90.0, 90.0, 10, 10))
        f2 = self.engine.execution.process_tick(Tick(4.0, "ETH", 90.0, 90.0, 10, 10))
        self.assertTrue(f2[0].is_maker)
        self.assertAlmostEqual(f2[0].fee, 90.0 * 0.01)

    def test_numerics_entry_drift(self):
        t1 = Tick(1.0, "BTC", 100.0, 100.0, 1000, 1000)
        self.engine.process_ticks([t1])
        for i in range(1000):
            self.engine.portfolio.process_fill(Fill(f"x{i}", 2.0, Side.LONG, 0.001, 100.0, 0, 0, False, "BTC"))
        pos = self.engine.portfolio.positions["BTC"]
        self.assertAlmostEqual(pos.entry_price, 100.0, places=7)

    def test_m6_sharpe_annualization(self):
        curve = [
            {"timestamp": 0.0, "equity": 1000.0},
            {"timestamp": 3600.0, "equity": 1010.0},
            {"timestamp": 7200.0, "equity": 1020.1}
        ]
        stats = PerformanceAnalytics.calculate([{"pnl": 1, "fee":0}], curve, resample_seconds=3600)
        self.assertIsNotNone(stats.get("Sharpe Ratio"))
        self.assertTrue(stats["Sharpe Ratio"] > 0)

    def test_s1_stop_trigger_no_fill_reverts(self):
        order = Order("s", 1.0, Side.LONG, OrderType.STOP, 1.0, "BTC", stop_price=105.0)
        self.engine.execution.submit_order(order)
        self.engine.execution.process_tick(Tick(1.0, "BTC", 106.0, 106.0, 0, 0))
        self.assertEqual(order.status, OrderStatus.TRIGGERED)

    def test_s6_race_liq_vs_exit(self):
        self.engine.max_leverage = 10.0
        self.engine.risk_pct = 1.0
        t1 = Tick(1.0, "BTC", 100.0, 100.0, 1000, 1000)
        self.engine.enter_trade(t1, Side.LONG, 10.0, 50.0)
        self.engine.process_ticks([t1])
        self.engine.process_ticks([Tick(2.0, "BTC", 85.0, 85.0, 1000, 1000)])
        self.assertEqual(self.engine.portfolio.trade_ledger[-1]["exit_reason"], ExitReason.LIQUIDATION.name)

    def test_k2_exception_in_callback(self):
        def bad_cb(f): raise ValueError("Crash")
        o = Order("cb", 1.0, Side.LONG, OrderType.MARKET, 1.0, "BTC")
        self.engine.execution.submit_order(o)
        self.engine.execution.process_tick(Tick(1.0, "BTC", 100.0, 100.0, 10, 10), on_fill=bad_cb)
        self.assertEqual(o.status, OrderStatus.ERROR)
        self.assertNotIn("cb", self.engine.execution.active_orders)

    def test_cancel_modify_api(self):
        o = Order("gtc1", 1.0, Side.LONG, OrderType.LIMIT, 10.0, "BTC", TimeInForce.GTC, price=100.0)
        self.engine.execution.submit_order(o)
        self.engine.execution.modify_order("gtc1", new_qty=5.0, new_price=95.0)
        self.assertEqual(o.quantity, 5.0)
        self.assertEqual(o.price, 95.0)
        self.assertTrue(self.engine.execution.cancel_order("gtc1"))
        self.assertEqual(o.status, OrderStatus.CANCELED)

    def test_queue_position_model(self):
        o = Order("q1", 1.0, Side.LONG, OrderType.LIMIT, 1.0, "BTC", TimeInForce.GTC, price=100.0)
        self.engine.execution.submit_order(o)
        self.engine.execution.process_tick(Tick(1.0, "BTC", 100.0, 101.0, 50, 50))
        self.assertEqual(o.queue_position, 50)
        
        f1 = self.engine.execution.process_tick(Tick(2.0, "BTC", 100.0, 100.0, 30, 30))
        self.assertEqual(len(f1), 0)
        self.assertEqual(o.queue_position, 20)
        
        f2 = self.engine.execution.process_tick(Tick(3.0, "BTC", 100.0, 100.0, 30, 30))
        self.assertEqual(len(f2), 1)

    def test_funding_hook(self):
        self.engine.portfolio.cash = 1000
        self.engine.portfolio.equity = 1000
        self.engine.portfolio.positions["BTC"] = Position("BTC", Side.LONG, 1.0, 100.0, 1.0)
        self.engine.portfolio.latest_prices["BTC"] = Tick(1.0, "BTC", 100.0, 100.0, 0, 0)
        self.engine.portfolio.apply_funding(1.0, "BTC", 0.01)
        self.assertEqual(self.engine.portfolio.cash, 999.0)

    @unittest.skipIf(not HAS_HYPOTHESIS, "Hypothesis not installed")
    def test_invariants_property(self):
        @given(st.lists(st.tuples(st.floats(90, 110), st.floats(1, 10)), min_size=1, max_size=20))
        @settings(max_examples=50)
        def run_random_ticks(tick_data):
            engine = BacktestEngine()
            ts = 1.0
            for price, vol in tick_data:
                tick = Tick(ts, "BTC", price, price + 0.1, vol, vol)
                engine.process_ticks([tick])
                if not engine.portfolio.positions:
                    self.assertAlmostEqual(engine.portfolio.equity, engine.portfolio.cash, places=5)
                for p in engine.portfolio.positions.values():
                    self.assertGreaterEqual(p.quantity, 0.0)
                ts += 1.0
        run_random_ticks()

if __name__ == '__main__':
    unittest.main(verbosity=2)
