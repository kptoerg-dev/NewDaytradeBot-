# 📈 Event-Driven Algorithmic Trading Engine

Eine produktionsreife, ereignisgesteuerte Backtesting- und Execution-Engine für algorithmischen Handel (Krypto/Aktien/Futures). 
Entwickelt für höchste Präzision, um Backtest-Overfitting und typische Simulationsfehler (Slippage-Cheats, Queue-Positionierung, Catastrophic Cancellation) zu eliminieren.

## 🚀 Core Features

- **Event-Queue Architektur:** Multi-Asset-Unterstützung durch striktes Zeit-basiertes (Priority Queue) Event-Handling ohne Timestamp-Regressionen.
- **Microstructure Realismus:**
  - **Queue-Position Modeling:** Passive Limit-Orders reihen sich virtuell in das Orderbuch ein und werden erst gefüllt, wenn vorausgehendes Volumen (`bid_volume`/`ask_volume`) absorbiert wurde.
  - **Slippage & Impact:** Konstantes oder dynamisches Slippage-Modell. Marketable Limits zahlen korrekterweise Slippage (gedeckt am Limit-Preis).
  - **Maker/Taker Fees:** Präzise Gebührendifferenzierung je nach Order-Routing (Passiv vs. Aggressiv).
- **Risk & Margin Engine:**
  - Mark-to-Market über Fair-Price (Mid/Mark), um Spread-Jitter zu ignorieren.
  - Symmetrisches Cross-Margin Liquidations-Handling mit strikter Ausführungspriorität.
  - Slippage-Antizipation im Positions-Sizing (`enter_trade`).
- **Numerische Stabilität:** 
  - Verhinderung von Catastrophic Cancellation bei zehntausenden Micro-Fills durch Notional-Tracking statt iterativer Durchschnittspreise.
  - Zeit-Bar-Resampling für realistische Sharpe/Sortino-Berechnungen in HFT-Umgebungen.

## 🛠 Voraussetzungen

- Python 3.10 oder neuer.
- Keine externen Abhängigkeiten für die Core-Engine (rein Standardbibliothek).
- `hypothesis` für Property-Based Invarianten-Tests.

## 📦 Installation

Repository klonen und Abhängigkeiten für die Testsuite installieren:

```bash
git clone [https://gitlab.com/dein-username/trading-engine.git](https://gitlab.com/dein-username/trading-engine.git)
cd trading-engine
python -m venv venv
source venv/bin/activate  # (Windows: venv\Scripts\activate)
pip install -r requirements.txt
