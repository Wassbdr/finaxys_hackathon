"""Liste des services et fabrique des cœurs."""

from services.core_ledger import CoreLedger
from services.kyc_docs import KycDocs
from services.market_feed import MarketFeed
from services.news_wire import NewsWire
from services.payment_gateway import PaymentGateway

SERVICE_CLASSES = {cls.service_id: cls for cls in (MarketFeed, NewsWire, KycDocs, PaymentGateway, CoreLedger)}


def build_cores(cfg, store, prefix="", publisher_for=None):
    """publisher_for(service_id) -> callable(type, payload) qui publie un message signé."""
    return {
        sid: cls(cfg, store, prefix, publisher_for(sid) if publisher_for else None)
        for sid, cls in SERVICE_CLASSES.items()
    }
