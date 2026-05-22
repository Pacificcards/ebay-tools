from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class ListingMetadata:
    listing_id: str
    title: Optional[str]
    sku: Optional[str]
    category: Optional[str]
    current_price: Optional[Decimal]


@dataclass
class ListingMetricRaw:
    listing_id: str
    date: date
    impressions: Optional[int]
    clicks: Optional[int]
    page_views: Optional[int]


@dataclass
class OrderRaw:
    order_id: str
    listing_id: str
    order_date: date
    quantity: int
    sale_price: Decimal
