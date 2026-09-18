from engine.datatypes import Fill, Position, Side, PositionState, Tick
from typing import Dict, List

class PortfolioManager:
    def __init__(self, initial_capital: float, contract_multiplier: float = 1.0):
        self.cash = initial_capital
        self.initial_capital = initial_capital
        self.contract_multiplier = contract_multiplier
        self.positions: Dict[str, Position] = {}
        self.trade_ledger: List[dict] = []
        self.equity = initial_capital

    def update_from_fill(self, fill: Fill, symbol: str, is_reduce_only: bool = False):
        self.cash -= fill.fee + fill.slippage # Gebühren sofort abziehen

        if symbol not in self.positions or self.positions[symbol].state == PositionState.CLOSED:
            if is_reduce_only:
                return # Ignoriere Reduzierung, wenn keine Position existiert
            # Neue Position (Phase 10: Robustheit)
            if fill.quantity > 0:
                self.positions[symbol] = Position(
                    symbol=symbol, side=fill.side, quantity=fill.quantity, entry_price=fill.price
                )
            return

        pos = self.positions[symbol]

        # Positionsvergrößerung (Phase 10: Partial Fills & Average Entry)
        if pos.side == fill.side:
            if is_reduce_only: return # Sollte nicht passieren, aber Safety First
            total_qty = pos.quantity + fill.quantity
            pos.entry_price = ((pos.entry_price * pos.quantity) + (fill.price * fill.quantity)) / total_qty
            pos.quantity = total_qty
            
        # Positionsreduzierung / Exit (Phase 1: EXIT erzeugt keinen Flip)
        else:
            close_qty = min(pos.quantity, fill.quantity)
            
            # Realized PnL Berechnung (Phase 3)
            price_diff = (fill.price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - fill.price)
            realized_gross = price_diff * close_qty * self.contract_multiplier
            
            pos.realized_pnl += realized_gross
            self.cash += realized_gross
            pos.quantity -= close_qty
            
            # Ledger Eintrag
            self.trade_ledger.append({
                "timestamp": fill.timestamp,
                "symbol": symbol,
                "side": pos.side.name,
                "closed_qty": close_qty,
                "entry_price": pos.entry_price,
                "exit_price": fill.price,
                "realized_pnl": realized_gross,
                "fees": fill.fee,
                "slippage": fill.slippage
            })

            # State Update (Phase 3: Position State Machine)
            if pos.quantity <= 1e-8:
                pos.quantity = 0.0
                pos.state = PositionState.CLOSED
                del self.positions[symbol]

            # Flip Handling (Nur wenn nicht reduce_only)
            remaining_qty = fill.quantity - close_qty
            if remaining_qty > 1e-8 and not is_reduce_only:
                self.positions[symbol] = Position(
                    symbol=symbol, side=fill.side, quantity=remaining_qty, entry_price=fill.price
                )

    def mark_to_market(self, tick: Tick, symbol: str):
        # Phase 4 & 5: Unrealized PnL und Equity
        unrealized_pnl = 0.0
        pos_value = 0.0

        if symbol in self.positions and self.positions[symbol].state != PositionState.CLOSED:
            pos = self.positions[symbol]
            current_price = tick.bid if pos.side == Side.LONG else tick.ask
            price_diff = (current_price - pos.entry_price) if pos.side == Side.LONG else (pos.entry_price - current_price)
            unrealized_pnl = price_diff * pos.quantity * self.contract_multiplier
            pos_value = pos.quantity * current_price # Notional

        self.equity = self.cash + unrealized_pnl
