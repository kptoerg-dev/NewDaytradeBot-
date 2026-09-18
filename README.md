# Quantitative Event-Driven Trading Engine v2.0

Eine hochrobuste, event-gesteuerte Backtesting-Engine für quantitative Trading-Strategien. 
Diese Engine simuliert Marktmikrostruktur, Latenz und Risikomanagement ohne Lookahead-Bias.

## 🏗 Architektur-Garantien

1. **Strenge Chronologie (No Lookahead):** MTM -> Ausführung -> Liquidation & SL/TP. Liquidations-Events werden in die Warteschlange gestellt und füllen realitätsnah erst im darauffolgenden Tick.
2. **IOC-Market-Orders & Liquidität:** Market-Orders verhalten sich wie *Immediate-Or-Cancel* (IOC). Was nicht im selben Tick gefüllt werden kann, verfällt. 
3. **Aggregierte Margin:** Die Positionsgröße wird dynamisch geclippt, sodass das kombinierte Notional aller offenen Positionen das Portfolio-Leverage-Cap nie übersteigt.
4. **Der `reduce_only` Flip-Schutz:** Fills erben das `reduce_only`-Flag untrennbar von ihrer Order. Ein ungewollter Wechsel von Long zu Short (Flip) bei Überfills ist architektonisch ausgeschlossen.
5. **Single-Layer Slippage:** Slippage wird direkt in den `fill_price` eingepreist, und zwar **nur** bei Market- und Stop-Orders. Passive Limit-Orders füllen ohne Slippage-Aufschlag.

## 🚀 Quickstart

```python
from trading_engine import BacktestEngine, Tick, Side

# 1. Engine initialisieren (10k Kapital, 1% Risk, 2x Leverage)
engine = BacktestEngine(initial_capital=10000.0, risk_pct=0.01, max_leverage=2.0)

# 2. Marktdaten-Event empfangen & verarbeiten
tick = Tick(timestamp=1.0, bid=100.0, ask=100.5, bid_volume=10, ask_volume=10)
engine.process_ticks([tick])

# 3. Trade platzieren 
engine.enter_trade(tick, Side.LONG, stop_distance=2.0, tp_distance=5.0)
