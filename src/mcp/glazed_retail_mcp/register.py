"""NeMo Agent Toolkit registration: exposes the Glazed Co. analytics as one function group.

Serve with:  nat mcp serve --config_file src/mcp/config.yml
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Annotated
from typing import Literal
from typing import get_type_hints

from pydantic import BaseModel
from pydantic import Field
from pydantic import create_model

from nat.builder.builder import Builder
from nat.builder.function import FunctionGroup
from nat.cli.register_workflow import register_function_group
from nat.data_models.function import FunctionGroupBaseConfig

from . import analytics as an
from .data import get_dataset

logger = logging.getLogger(__name__)


TOOL_NAMES = ["get_dataset_overview", "get_data_quality_report", "list_stores", "search_products", "get_product",
              "get_calendar", "sales_summary", "inventory_health", "reorder_recommendations", "supplier_performance",
              "store_kpis", "promotion_performance", "weather_impact"]


class GlazedRetailConfig(FunctionGroupBaseConfig, name="glazed_retail"):
    """Glazed Co. retail dataset tools (sales, inventory, suppliers, store KPIs, promotions, weather)."""

    # NAT only exposes group members listed in `include`; default to every tool.
    include: list[str] = Field(default_factory=lambda: sorted(TOOL_NAMES),
                               description="Tools to expose (default: all). Use `exclude` instead to remove some.")

    data_dir: str | None = Field(default=None,
                                 description="Directory with the dataset CSVs. Defaults to <repo>/data or $GLAZED_DATA_DIR.")


# Shared parameter types -------------------------------------------------------
DateFrom = Annotated[str | None, Field(description="Start date, inclusive, YYYY-MM-DD. Data spans 2025-10-01 to 2026-09-30.")]
DateTo = Annotated[str | None, Field(description="End date, inclusive, YYYY-MM-DD.")]
StoreId = Annotated[str | None, Field(description="Store ID like 'S007'. Comma-separate for several. Use list_stores to find IDs.")]
SiteId = Annotated[str | None, Field(description="Site (venue) ID like 'ST01'. Comma-separate for several.")]
Region = Annotated[str | None, Field(description="Region, e.g. 'Northeast', 'Southeast', 'Midwest', 'Pacific'. Case-insensitive.")]
Department = Annotated[str | None, Field(description="Product department: Beverage, Food, Bakery, Merchandise, Catering, Modifiers, Grab & Go.")]
Category = Annotated[str | None, Field(description="Product category, e.g. 'Coffee & Tea', 'Bakery', 'Sandwiches', 'Specialty Beverage'.")]
Sku = Annotated[str | None, Field(description="SKU like 'SKU-100003'. Comma-separate for several.")]
Limit = Annotated[int, Field(ge=1, le=an.MAX_LIMIT, description="Max rows to return.")]
Asc = Annotated[bool, Field(description="Sort ascending instead of descending.")]


def _single_input(fn: Callable) -> tuple[Callable, type[BaseModel]]:
    """NAT functions take exactly one pydantic model. Build it from fn's typed signature
    (so Field descriptions and Literal enums reach the MCP tool schema) and adapt fn to it."""
    hints = get_type_hints(fn, include_extras=True)
    fields = {n: (hints[n], ... if p.default is inspect.Parameter.empty else p.default)
              for n, p in inspect.signature(fn).parameters.items()}
    model = create_model(f"{fn.__name__}_input", **fields)

    async def single(inp):
        return await fn(**inp.model_dump())

    single.__annotations__ = {"inp": model, "return": dict}
    single.__name__ = fn.__name__
    return single, model


def _build_tools(ds) -> list[tuple[str, Callable, str]]:
    """(name, function, description) for every tool. Functions are async, typed and documented for the LLM."""

    async def run(fn, *a, **kw):
        # pandas work is CPU-bound; keep the MCP event loop responsive
        return await asyncio.to_thread(fn, ds, *a, **kw)

    async def get_dataset_overview() -> dict:
        return await run(an.dataset_overview)

    async def get_data_quality_report(table: Annotated[str | None, Field(description="Optional table name, e.g. 'sales_daily'.")] = None) -> dict:
        return await run(an.data_quality_report, table)

    async def list_stores(site_id: SiteId = None, region: Region = None,
                          format: Annotated[str | None, Field(description="'Full Counter', 'Express Kiosk' or 'Shop-in-Shop'.")] = None,
                          status: Annotated[Literal["open", "closed"] | None, Field(description="'closed' = permanently closed.")] = None,
                          limit: Limit = 100) -> dict:
        return await run(an.list_stores, site_id, region, format, status, limit)

    async def search_products(query: Annotated[str | None, Field(description="Text to look for in product name or SKU.")] = None,
                              department: Department = None, category: Category = None,
                              status: Annotated[str | None, Field(description="Active, Discontinued, Inactive or Seasonal-Off.")] = None,
                              inventory_tracked: Annotated[bool | None, Field(description="Only SKUs with inventory/PO data.")] = None,
                              supplier_id: Annotated[str | None, Field(description="e.g. 'SUP01'.")] = None,
                              limit: Limit = 25) -> dict:
        return await run(an.search_products, query, department, category, status, inventory_tracked, supplier_id, limit)

    async def get_product(sku_or_barcode: Annotated[str, Field(description="A SKU (e.g. 'SKU-100003') or a barcode.")]) -> dict:
        return await run(an.get_product, sku_or_barcode)

    async def get_calendar(date_from: DateFrom = None, date_to: DateTo = None,
                           holidays_only: Annotated[bool, Field(description="Only holidays (default true).")] = True,
                           limit: Limit = 60) -> dict:
        return await run(an.calendar_lookup, date_from, date_to, holidays_only, limit)

    async def sales_summary(
            group_by: Annotated[Literal["none", "day", "week", "month", "store", "site", "region", "format", "department",
                                        "category", "subcategory", "sku", "day_of_week", "holiday", "promo"],
                                Field(description="Dimension to aggregate by. 'none' = single grand total. "
                                      "'holiday' keeps only holiday days.")] = "none",
            date_from: DateFrom = None, date_to: DateTo = None, store_id: StoreId = None, site_id: SiteId = None,
            region: Region = None, department: Department = None, category: Category = None, sku: Sku = None,
            sort_by: Annotated[Literal["net_sales", "units", "gross_sales", "discount", "discount_rate",
                                       "avg_net_price_per_unit"], Field(description="Ranking metric (ignored for day/week/month).")] = "net_sales",
            ascending: Asc = False, limit: Limit = 25) -> dict:
        return await run(an.sales_summary, group_by, date_from, date_to, store_id, site_id, region, department, category,
                         sku, sort_by, ascending, limit)

    async def inventory_health(
            group_by: Annotated[Literal["none", "store", "site", "region", "department", "category", "sku", "month",
                                        "store_sku"], Field(description="Dimension to aggregate by.")] = "category",
            date_from: DateFrom = None, date_to: DateTo = None, store_id: StoreId = None, site_id: SiteId = None,
            region: Region = None, department: Department = None, category: Category = None, sku: Sku = None,
            sort_by: Annotated[Literal["waste_cost", "expired_qty", "expired_pct_of_received", "stockout_hours",
                                       "stockout_days", "stockout_day_rate", "damaged_qty", "units_sold",
                                       "avg_closing_qty"], Field(description="Ranking metric (ignored for month).")] = "waste_cost",
            ascending: Asc = False, limit: Limit = 25) -> dict:
        return await run(an.inventory_health, group_by, date_from, date_to, store_id, site_id, region, department,
                         category, sku, sort_by, ascending, limit)

    async def reorder_recommendations(
            store_id: Annotated[str, Field(description="Store ID like 'S007' (required).")],
            as_of: Annotated[str | None, Field(description="Snapshot date YYYY-MM-DD; default = latest in the data.")] = None,
            lookback_days: Annotated[int, Field(ge=1, le=180, description="Days of sales used for average demand.")] = 28,
            safety_days: Annotated[float, Field(ge=0, le=14, description="Extra days of safety stock.")] = 1.0,
            category: Category = None, limit: Limit = 25) -> dict:
        return await run(an.reorder_recommendations, store_id, as_of, lookback_days, safety_days, category, limit)

    async def supplier_performance(supplier_id: Annotated[str | None, Field(description="e.g. 'SUP03'. Comma-separate for several; omit for all.")] = None,
                                   store_id: StoreId = None, date_from: Annotated[str | None, Field(description="Order date from, YYYY-MM-DD.")] = None,
                                   date_to: Annotated[str | None, Field(description="Order date to, YYYY-MM-DD.")] = None,
                                   by_month: Annotated[bool, Field(description="Split results by order month.")] = False) -> dict:
        return await run(an.supplier_performance, supplier_id, store_id, date_from, date_to, by_month)

    async def store_kpis(
            group_by: Annotated[Literal["none", "store", "site", "region", "format", "month", "day_of_week"],
                                Field(description="Dimension to aggregate by.")] = "store",
            date_from: DateFrom = None, date_to: DateTo = None, store_id: StoreId = None, site_id: SiteId = None,
            region: Region = None,
            sort_by: Annotated[Literal["net_sales", "footfall", "transactions", "conversion_rate", "avg_ticket",
                                       "units_per_transaction", "sales_per_staff_hour", "sales_per_open_hour",
                                       "store_days"], Field(description="Ranking metric (ignored for month).")] = "net_sales",
            ascending: Asc = False, limit: Limit = 25) -> dict:
        return await run(an.store_kpis, group_by, date_from, date_to, store_id, site_id, region, sort_by, ascending, limit)

    async def promotion_performance(promo_id: Annotated[str | None, Field(description="e.g. 'PR003'. Omit for all promotions.")] = None) -> dict:
        return await run(an.promotion_performance, promo_id)

    async def weather_impact(
            by: Annotated[Literal["temperature", "precipitation"], Field(description="Weather dimension to bucket by.")] = "temperature",
            department: Department = None, category: Category = None, sku: Sku = None, region: Region = None,
            site_id: SiteId = None, date_from: DateFrom = None, date_to: DateTo = None) -> dict:
        return await run(an.weather_impact, by, department, category, sku, region, site_id, date_from, date_to)

    return [
        ("get_dataset_overview", get_dataset_overview,
         "Describe the Glazed Co. dataset: business context, date range, tables and columns, valid departments, "
         "categories and regions. Call this first to orient yourself."),
        ("get_data_quality_report", get_data_quality_report,
         "List the data-quality problems found in the raw CSVs and how each was handled (duplicates, bad IDs, "
         "mixed date formats, sentinel values, orphan keys). Use it to state caveats about any figure."),
        ("list_stores", list_stores,
         "List stores with their venue (site), region, format, opening hours, opened/closed dates and closure windows."),
        ("search_products", search_products,
         "Search the product master by name/SKU text and filter by department, category, status, supplier or "
         "inventory tracking. Returns cost and base price."),
        ("get_product", get_product,
         "Full detail for one product by SKU or barcode: attributes, supplier contract, barcodes, current price "
         "range across stores, price-change history and lifetime sales."),
        ("get_calendar", get_calendar,
         "Look up holidays/weekends/school breaks in a date range (useful to explain sales peaks)."),
        ("sales_summary", sales_summary,
         "Aggregate sales (units, gross, discount, net sales) by time, store, site, region, product dimension, "
         "weekday, holiday or promotion, with filters. Use for top/bottom sellers, trends and comparisons."),
        ("inventory_health", inventory_health,
         "Waste and availability metrics for inventory-tracked SKUs: expired/damaged units, waste cost, stockout "
         "hours and days, by any dimension. Use to find where perishables are wasted or products run out."),
        ("reorder_recommendations", reorder_recommendations,
         "Suggested replenishment quantities for one store: demand, days of cover, supplier lead time and case "
         "size, capped by shelf life. A heuristic to start the planning conversation."),
        ("supplier_performance", supplier_performance,
         "Supplier fill rate, on-time rate and lateness from purchase orders, compared against contract terms."),
        ("store_kpis", store_kpis,
         "Store operations KPIs: footfall, transactions, conversion, average ticket, units per transaction, sales "
         "per staff hour, by store/site/region/format/month/weekday."),
        ("promotion_performance", promotion_performance,
         "Promotion results: sales lift vs the equally long period before, against the expected uplift, plus "
         "discount given on promo-tagged sales."),
        ("weather_impact", weather_impact,
         "How temperature or rain changes average units and sales per store-day for a product scope "
         "(e.g. iced vs hot beverages)."),
    ]


@register_function_group(config_type=GlazedRetailConfig)
async def glazed_retail_group(config: GlazedRetailConfig, _builder: Builder):
    # Load and clean once, up front, so startup fails fast and the first tool call is instant.
    ds = await asyncio.to_thread(get_dataset, config.data_dir)
    group = FunctionGroup(config=config)
    tools = _build_tools(ds)
    assert sorted(n for n, _, _ in tools) == sorted(TOOL_NAMES), "TOOL_NAMES out of sync with _build_tools"
    for name, fn, description in tools:
        single, model = _single_input(fn)
        group.add_function(name=name, fn=single, input_schema=model, description=description)
    logger.info("glazed_retail: registered %d tools", len(tools))
    yield group
