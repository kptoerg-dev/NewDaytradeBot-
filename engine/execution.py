from engine.datatypes import Order, Tick, Fill, OrderStatus, Side, OrderType
from typing import List, Optional

class ExecutionEngine:
    def __init__(self, latency_ms: float = 10.0, maker_fee: float = 0.0001, taker_fee: float = 0.0002):
        self.latency_sec = latency_ms / 1000.0
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.active_orders: List[Order] = []
        self.pending_orders: List[Order] = []

    def submit_order(self, order: Order, current_time: float):
        # Phase 6: Latenz. Order wird erst nach Latenz aktiv.
        order.timestamp = current_time + self.latency_sec
        order.status = OrderStatus.PENDING
        self.pending_orders.append(order)

    def process_tick(self, tick: Tick) -> List[Fill]:
        fills = []
        # Activate pending orders
        for o in self.pending_orders[:]:
            if tick.timestamp >= o.timestamp:
                o.status = OrderStatus.ACTIVE
                self.active_orders.append(o)
                self.pending_orders.remove(o)

        # Phase 9: Liquidität darf nicht mehrfach verwendet werden
        avail_bid_vol = tick.bid_volume
        avail_ask_vol = tick.ask_volume

        for o in self.active_orders[:]:
            if o.status in [OrderStatus.FILLED, OrderStatus.CANCELED]:
                self.active_orders.remove(o)
                continue

            fill = self._try_fill(o, tick, avail_bid_vol, avail_ask_vol)
            if fill:
                fills.append(fill)
                if o.side == Side.LONG:
                    avail_ask_vol = max(0.0, avail_ask_vol - fill.quantity)
                else:
                    avail_bid_vol = max(0.0, avail_bid_vol - fill.quantity)

                if o.filled_qty >= o.quantity - 1e-8:
                    o.status = OrderStatus.FILLED
                    self.active_orders.remove(o)
                else:
                    o.status = OrderStatus.PARTIAL

        return fills

    def _try_fill(self, order: Order, tick: Tick, bid_vol: float, ask_vol: float) -> Optional[Fill]:
        fill_price = 0.0
        avail_vol = 0.0
        fee_rate = self.taker_fee

        if order.side == Side.LONG:
            exec_price = tick.ask
            avail_vol = ask_vol
        else:
            exec_price = tick.bid
            avail_vol = bid_vol

        if avail_vol <= 0: return None

        # Stop Logic (Phase 8)
        if order.order_type == OrderType.STOP:
            if order.side == Side.LONG and tick.ask >= order.stop_price:
                order.order_type = OrderType.MARKET # Activate
            elif order.side == Side.SHORT and tick.bid <= order.stop_price:
                order.order_type = OrderType.MARKET # Activate
            else:
                return None

        # Limit Logic (Phase 7)
        if order.order_type == OrderType.LIMIT:
            if order.side == Side.LONG and tick.ask <= order.price:
                fill_price = order.price # Idealer Fill am Limit
                fee_rate = self.maker_fee
            elif order.side == Side.SHORT and tick.bid >= order.price:
                fill_price = order.price
                fee_rate = self.maker_fee
            else:
                return None
        
        # Market Logic
        if order.order_type == OrderType.MARKET:
            fill_price = exec_price
            
        if fill_price > 0:
            qty_to_fill = min(order.quantity - order.filled_qty, avail_vol)
            if qty_to_fill <= 0: return None
            
            # Phase 8: Slippage Modell (vereinfacht: 5% des Spreads pro vollem Lot)
            spread = tick.ask - tick.bid
            slippage = (spread * 0.05) * qty_to_fill

            order.filled_qty += qty_to_fill
            notional = qty_to_fill * fill_price
            
            return Fill(
                order_id=order.order_id,
                timestamp=tick.timestamp,
                side=order.side,
                quantity=qty_to_fill,
                price=fill_price + (slippage/qty_to_fill if order.side == Side.LONG else -slippage/qty_to_fill),
                fee=notional * fee_rate,
                slippage=slippage
            )
        return None
