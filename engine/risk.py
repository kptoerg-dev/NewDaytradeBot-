import math

class RiskManager:
    def __init__(self, max_risk_per_trade: float = 0.01, max_leverage: float = 2.0):
        self.max_risk = max_risk_per_trade
        self.max_leverage = max_leverage

    def calculate_position_size(self, equity: float, current_price: float, stop_price: float, contract_multiplier: float) -> float:
        # Phase 16: Numerische Robustheit
        if equity <= 0 or current_price <= 0 or stop_price <= 0: return 0.0
        
        stop_distance = abs(current_price - stop_price)
        if stop_distance < 1e-5: return 0.0 # Division durch Null verhindern
        
        risk_capital = equity * self.max_risk
        risk_per_unit = stop_distance * contract_multiplier
        
        raw_size = risk_capital / risk_per_unit
        
        # Phase 9: Max Leverage Check
        max_notional = equity * self.max_leverage
        max_size_by_lev = max_notional / (current_price * contract_multiplier)
        
        final_size = min(raw_size, max_size_by_lev)
        
        if math.isnan(final_size) or math.isinf(final_size): return 0.0
        return round(final_size, 4) # Quantity Step
