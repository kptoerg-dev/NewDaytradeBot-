import unittest
from trading_engine import (
    BacktestEngine, Tick, Side, Order, OrderType, OrderStatus, Fill, PositionState
)

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
        t1 = Tick(1.0, 100.0, 100.0, 1000, 1000)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1]) 
        
        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(pos.stop_loss, 95.0) 
        
        t2 = Tick(2.0, 94.0, 95.0, 1000, 1000)
        self.engine.process_ticks([t2])
        self.assertEqual(pos.state, PositionState.CLOSING)

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
        self.assertEqual(ledger_entry['pnl'], -20.0 * ledger_entry['qty'])

    def test_order_rejection_on_missing_price(self):
        order_limit = Order("id_lim", 1.0, Side.LONG, OrderType.LIMIT, 5.0) 
        order_stop = Order("id_stop", 1.0, Side.LONG, OrderType.STOP, 5.0)  
        
        self.engine.execution.submit_order(order_limit, 1.0)
        self.engine.execution.submit_order(order_stop, 1.0)
        
        self.assertEqual(order_limit.status, OrderStatus.REJECTED)
        self.assertEqual(order_stop.status, OrderStatus.REJECTED)

    def test_pyramiding_updates_sl_tp(self):
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        fill1 = Fill("1", 1.0, Side.LONG, 10.0, 100.0, 0, 0, reduce_only=False, stop_price=90.0, take_profit_price=110.0)
        self.engine.portfolio.process_fill(fill1, self.engine.symbol)

        fill2 = Fill("2", 2.0, Side.LONG, 10.0, 110.0, 0, 0, reduce_only=False, stop_price=100.0, take_profit_price=120.0)
        self.engine.portfolio.process_fill(fill2, self.engine.symbol)

        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(pos.quantity, 20.0)
        self.assertEqual(pos.stop_loss, 100.0)

    def test_multi_symbol_support(self):
        t1_btc = Tick(1.0, 100.0, 100.0, 1000, 1000)
        t1_eth = Tick(1.0, 50.0, 50.0, 1000, 1000)

        self.engine.enter_trade(t1_btc, Side.LONG, 5.0, 10.0, symbol="BTCUSD")
        self.engine.process_ticks([t1_btc], symbol="BTCUSD")

        self.engine.enter_trade(t1_eth, Side.LONG, 2.0, 5.0, symbol="ETHUSD")
        self.engine.process_ticks([t1_eth], symbol="ETHUSD")

        self.assertIn("BTCUSD", self.engine.portfolio.positions)
        self.assertIn("ETHUSD", self.engine.portfolio.positions)

        t2_btc = Tick(2.0, 110.0, 110.0, 1000, 1000) 
        t2_eth = Tick(2.0, 60.0, 60.0, 1000, 1000)   
        
        self.engine.process_ticks([t2_btc], symbol="BTCUSD")
        self.engine.process_ticks([t2_eth], symbol="ETHUSD")

        self.assertEqual(self.engine.portfolio.equity, 10700.0)

    def test_audit_fix_1_liquidation_blindspot_and_ioc(self):
        """Beweist, dass eine IOC Partial-Fill Order bei Kontocrash korrekte Folgeorders generiert (Kein Lookahead)."""
        t1 = Tick(1.0, 100.0, 100.0, 1000, 1000)
        self.engine.max_leverage = 10.0 
        self.engine.risk_pct = 1.0 
        self.engine.enter_trade(t1, Side.LONG, stop_distance=10.0, tp_distance=50.0)
        self.engine.process_ticks([t1]) # Qty 1000 filled

        # Tick 2: Trigger Stop-Loss. Generiert Exit Order (1000 Qty) am Ende des Ticks.
        t2 = Tick(2.0, 89.0, 90.0, 100, 1) 
        self.engine.process_ticks([t2])
        self.assertEqual(self.engine.portfolio.positions[self.engine.symbol].state, PositionState.CLOSING) 
        
        # Tick 3: Crash Equity < 0. Führt SL Order aus T2 aus. Aber Volume ist nur 1!
        # Restliche 999 verfallen wegen IOC. Dann erkennt _check_liquidation den Rest und schickt Order für 999 nach.
        t3 = Tick(3.0, 10.0, 11.0, 1000, 1)
        self.engine.process_ticks([t3])
        
        self.assertTrue(self.engine.portfolio.equity <= 0)
        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(pos.quantity, 999.0) # 1 wurde gefüllt, 999 bleiben offen
        self.assertEqual(len(self.engine.execution.pending_orders), 1)
        self.assertEqual(self.engine.execution.pending_orders[0].quantity, 999.0) # Neue Order sitzt perfekt

    def test_limit_order_partial_fill(self):
        order = Order("limit1", 1.0, Side.LONG, OrderType.LIMIT, 10.0, price=99.0)
        self.engine.execution.submit_order(order, 1.0)

        t1 = Tick(1.0, 99.0, 99.0, 10, 4.0)
        self.engine.execution.process_tick(t1)
        self.assertEqual(order.status, OrderStatus.PARTIAL)

        t2 = Tick(2.0, 99.0, 99.0, 10, 10.0)
        self.engine.execution.process_tick(t2)
        self.assertEqual(order.status, OrderStatus.FILLED)

    def test_ioc_market_order_remainder_cancelled(self):
        order = Order("ioc1", 1.0, Side.LONG, OrderType.MARKET, 100.0)
        self.engine.execution.submit_order(order, 1.0)
        
        t1 = Tick(1.0, 100.0, 100.0, 100, 40.0)
        self.engine.execution.process_tick(t1)
        
        self.assertEqual(order.filled_qty, 40.0)
        self.assertEqual(order.status, OrderStatus.CANCELED)
        
        t2 = Tick(2.0, 100.0, 100.0, 100, 100.0)
        fills = self.engine.execution.process_tick(t2)
        self.assertEqual(len(fills), 0)

    def test_limit_fill_no_slippage(self):
        self.engine.execution.slippage_ticks = 2.0 
        order = Order("lim_slip", 1.0, Side.LONG, OrderType.LIMIT, 5.0, price=99.0)
        self.engine.execution.submit_order(order, 1.0)
        
        t1 = Tick(1.0, 99.0, 99.0, 100, 100)
        self.engine.process_ticks([t1])
        
        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(pos.entry_price, 99.0)

    def test_portfolio_leverage_cap(self):
        t1 = Tick(1.0, 100.0, 100.0, 1000, 1000)
        self.engine.max_leverage = 2.0 
        self.engine.risk_pct = 1.0 
        
        self.engine.enter_trade(t1, Side.LONG, 10.0, 10.0, symbol="BTCUSD")
        self.engine.process_ticks([t1])
        
        self.engine.enter_trade(t1, Side.LONG, 10.0, 10.0, symbol="ETHUSD")
        self.assertNotIn("ETHUSD", self.engine.portfolio.positions)

    def test_audit_fix_2_spread_trap(self):
        t1 = Tick(1.0, 95.0, 100.0, 100, 100) 
        with self.assertRaises(ValueError):
            self.engine.enter_trade(t1, Side.LONG, stop_distance=2.0, tp_distance=10.0)

    def test_audit_fix_3_zombie_state_healing(self):
        t1 = Tick(1.0, 100.0, 100.0, 100, 100)
        self.engine.enter_trade(t1, Side.LONG, stop_distance=5.0, tp_distance=10.0)
        self.engine.process_ticks([t1])
        
        t2 = Tick(2.0, 94.0, 95.0, 0, 0)
        self.engine.process_ticks([t2])
        
        pos = self.engine.portfolio.positions[self.engine.symbol]
        self.assertEqual(len(self.engine.execution.pending_orders), 0) 
        
        t3 = Tick(3.0, 105.0, 106.0, 100, 100)
        self.engine.process_ticks([t3])
        self.assertEqual(pos.state, PositionState.OPEN)

if __name__ == '__main__':
    unittest.main(verbosity=2)
