"""Risk-gated order execution: paper broker today, live adapters later.

No component in this package places live orders on its own. `OrderManager` is
the only sanctioned path from a trading decision to a broker call, and it
always consults the deterministic `RiskEngine` first.
"""

from .broker import BrokerAdapter
from .kite_broker import KiteBrokerAdapter
from .models import Fill, Order, OrderRequest, OrderSide, OrderStatus, Position
from .order_manager import OrderManager, OrderOutcome
from .order_store import OrderStore, OrderStoreEntry, derive_tag
from .paper_broker import PaperBroker
from .reconciliation import ReconciliationReport, reconcile_startup_state

__all__ = [
    "BrokerAdapter",
    "Fill",
    "KiteBrokerAdapter",
    "Order",
    "OrderManager",
    "OrderOutcome",
    "OrderRequest",
    "OrderSide",
    "OrderStatus",
    "OrderStore",
    "OrderStoreEntry",
    "PaperBroker",
    "Position",
    "ReconciliationReport",
    "derive_tag",
    "reconcile_startup_state",
]
