import unittest
from engine.datatypes import Tick, Side, OrderType, Order
from engine.backtester import BacktestEngine

class TestTradingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = BacktestEngine(initial_capital=10000.0)
        self.engine.execution.latency_sec = 0.0 # Für deterministische State-Tests ohne Queue-Delay

    def test_01_exit_no_flip(self):
        """PHASE 1: Exit darf keine neue Position erzeugen"""
        # 1. Setup Long Position
        t1 = Tick(1.0, 100.0, 101.0, 10.0, 10.0)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=10.0, tp_distance=20.0)
        self.engine.process_data([t1])
        
        qty = self.engine.portfolio.positions["BTCUSD"].quantity
        self.assertTrue(qty > 0)
        
        # 2. Sende übergroße Exit Order (Simulierter Bug-Versuch)
        exit_order = Order("exit_1", 2.0, Side.SHORT, OrderType.MARKET, qty * 2, reduce_only=True)
        self.engine.execution.submit_order(exit_order, 2.0)
        
        t2 = Tick(2.0, 105.0, 106.0, 10.0, 10.0)
        self.engine.process_data([t2])
        
        # Assertions
        self.assertNotIn("BTCUSD", self.engine.portfolio.positions) # Position ist CLOSED
        # Wichtig: Cash muss korrekt berechnet sein, keine fehlerhaften Short-Restbestände
        self.assertTrue(self.engine.portfolio.cash > 10000.0) # Profit gemacht

    def test_02_position_sizing(self):
        """PHASE 2 & 16: Echtes Risk Sizing und Zero-Division Schutz"""
        t1 = Tick(1.0, 100.0, 100.0, 10.0, 10.0) # Zero spread tick
        # Zero stop distance should result in 0 quantity (safe)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=0.0, tp_distance=10.0)
        self.assertEqual(len(self.engine.execution.pending_orders), 0)

        # Normal Risk Sizing: Equity=10k, Risk=1%, Stop=10 -> Risk Amount=100. Qty = 100 / 10 = 10
        self.engine.enter_trade(t1, Side.LONG, stop_distance=10.0, tp_distance=20.0)
        order = self.engine.execution.pending_orders[0]
        self.assertAlmostEqual(order.quantity, 10.0)

    def test_03_liquidity_consumption(self):
        """PHASE 9: Liquidität darf im Event nicht doppelt genutzt werden"""
        self.engine.execution.latency_sec = 0.0
        
        # Orderbuch hat nur 5 Lots Ask-Volume
        o1 = Order("1", 1.0, Side.LONG, OrderType.MARKET, 4.0)
        o2 = Order("2", 1.0, Side.LONG, OrderType.MARKET, 4.0)
        
        self.engine.execution.submit_order(o1, 1.0)
        self.engine.execution.submit_order(o2, 1.0)
        
        t1 = Tick(1.0, 100.0, 101.0, 10.0, 5.0) # Nur 5 Ask Vol
        self.engine.process_data([t1])
        
        self.assertEqual(o1.filled_qty, 4.0)
        self.assertEqual(o2.filled_qty, 1.0) # Nur noch 1 übrig!

    def test_04_pnl_and_equity(self):
        """PHASE 3, 4, 5: Realized vs Unrealized"""
        t1 = Tick(1.0, 100.0, 100.0, 100.0, 100.0)
        order = Order("1", 1.0, Side.LONG, OrderType.MARKET, 10.0)
        self.engine.execution.submit_order(order, 1.0)
        self.engine.process_data([t1])
        
        # Tick steigt. Ask=110, Bid=109. Unrealized basis ist Bid für Long.
        t2 = Tick(2.0, 109.0, 110.0, 100.0, 100.0)
        self.engine.process_data([t2])
        
        # Entry=100. Bid=109. Profit = 9 * 10 = 90. Minus Fees (0.0002 * 1000 = 0.2). Eq ~ 10089.8
        self.assertTrue(self.engine.portfolio.equity > 10089.0)
        self.assertEqual(self.engine.portfolio.positions["BTCUSD"].realized_pnl, 0.0) # Noch nicht realisiert

if __name__ == '__main__':
    unittest.main()
