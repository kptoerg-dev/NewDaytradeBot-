# Quantitative Event-Driven Trading Engine v2.0

Eine hochrobuste, event-gesteuerte Backtesting-Engine für quantitative Trading-Strategien. 

Diese Engine simuliert Marktmikrostruktur, Latenz und Risikomanagement ohne Lookahead-Bias. Sie erzwingt eine strikte Ausführungsreihenfolge: `Mark-to-Market -> Execution -> Portfolio Update -> SL/TP Trigger`.

## 🏗 Architektur-Garantien

1. **Der `reduce_only` Flip-Schutz:** Fills erben das `reduce_only`-Flag untrennbar von ihrer Order. Ein ungewollter Wechsel von Long zu Short (Flip) bei Überfills ist architektonisch ausgeschlossen.
2. **Persistenter SL/TP-Transfer:** Stop-Loss und Take-Profit-Level gehen beim Fill physisch in den Besitz des `Position`-Objekts über. 
3. **Single-Layer Slippage & Gaps:** Slippage wird direkt in den `fill_price` eingepreist. Überspringt der Markt einen Stop-Loss (Price Gap), wird die Order zur Market-Order und füllt zum (schlechteren) tatsächlichen Marktpreis.
4. **Orderbuch-Simulation:** Mehrere Orders im selben Event konsumieren das Tick-Volumen nacheinander.

## 🚀 Quickstart

```python
from trading_engine import BacktestEngine, Tick, Side

# 1. Engine initialisieren (10k Kapital, 1% Risk, 2x Leverage)
engine = BacktestEngine(initial_capital=10000.0, risk_pct=0.01, max_leverage=2.0)

# 2. Marktdaten-Event empfangen & verarbeiten
tick = Tick(timestamp=1.0, bid=100.0, ask=100.5, bid_volume=10, ask_volume=10)
engine.process_ticks([tick])

# 3. Trade platzieren (Nutzt automatisch den Risk Manager für das Sizing)
# Side, Stop-Loss Distanz (2 Punkte), Take-Profit Distanz (5 Punkte)
engine.enter_trade(tick, Side.LONG, stop_distance=2.0, tp_distance=5.0)
