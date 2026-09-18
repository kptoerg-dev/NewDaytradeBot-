# Quantitative Event-Driven Trading Engine v2.0

## Architektur
Diese Engine implementiert einen strikten Event-Loop (`t` zu `t+1`). 
Market Data, Signal Generation, Risk Management und Execution sind vollständig entkoppelt.

## Behobene Fehler gegenüber v1.0
* **CRITICAL:** Exit-Orders erzeugen keine gegenläufigen Positionen (Flips) mehr (`reduce_only` Flag implementiert).
* **CRITICAL:** PnL und Portfolio-Bilanz korrigiert (strikte Trennung Cash, Unrealized PnL, Notional).
* **HIGH:** Orderbuch-Liquidität wird pro Tick dekrementiert.
* **HIGH:** Stop Loss und TP sind persistent und unterliegen Latenz, kein Lookahead.

## Installation & Tests
```bash
pip install -r requirements.txt
python -m unittest discover -s tests
