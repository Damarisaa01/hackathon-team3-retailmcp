"""Loading and cleaning of the Glazed Co. hackathon dataset.

The CSVs ship with intentional quality problems (see data/README.md). Every
table is cleaned once, in memory, and cached. The raw CSVs are never modified.
What was fixed or discarded is recorded in ``Dataset.quality`` so tools (and the
LLM calling them) can state their caveats.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR_ENV = "GLAZED_DATA_DIR"


def default_data_dir() -> Path:
    """`<repo>/data`, or the directory named by $GLAZED_DATA_DIR."""
    if os.environ.get(DATA_DIR_ENV):
        return Path(os.environ[DATA_DIR_ENV]).expanduser()
    # .../src/mcp/glazed_retail_mcp/data.py -> repo root is 4 levels up
    return Path(__file__).resolve().parents[3] / "data"


# --------------------------------------------------------------------------- #
# Normalizers
# --------------------------------------------------------------------------- #


def norm_sku(s: pd.Series) -> pd.Series:
    """' sku-100237', 'SKU100237', 'SKU-100237 ' -> 'SKU-100237'."""
    s = s.astype("string").str.strip().str.upper()
    return s.str.replace(r"^SKU-?(\d+)$", r"SKU-\1", regex=True)


def norm_store(s: pd.Series) -> pd.Series:
    """'s018', 'S0019', 'S021 ' -> 'S018', 'S019', 'S021'."""
    s = s.astype("string").str.strip().str.upper()
    num = s.str.extract(r"^S0*(\d+)$")[0]
    return ("S" + num.str.zfill(3)).where(num.notna(), s)


def norm_text(s: pd.Series) -> pd.Series:
    """Trim and collapse whitespace."""
    return s.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)


def canon_labels(s: pd.Series) -> pd.Series:
    """Unify case/whitespace variants of a label to its most frequent spelling."""
    s = norm_text(s)
    key = s.str.lower()
    best = s.groupby(key).agg(lambda x: x.value_counts().idxmax())
    return key.map(best)


def parse_dates(s: pd.Series) -> pd.Series:
    """Parse ISO, MM/DD/YYYY and DD-Mon-YY strings into datetimes (NaT if none)."""
    s = s.astype("string").str.strip()
    out = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    for fmt in ("%m/%d/%Y", "%d-%b-%y"):
        miss = out.isna() & s.notna()
        if not miss.any():
            break
        out[miss] = pd.to_datetime(s[miss], format=fmt, errors="coerce")
    return out


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #


@dataclass
class Dataset:
    """Cleaned tables plus a log of what the cleaning did."""

    tables: dict[str, pd.DataFrame]
    quality: dict[str, list[str]] = field(default_factory=dict)
    data_dir: Path | None = None

    def __getattr__(self, name: str) -> pd.DataFrame:
        try:
            return self.__dict__["tables"][name]
        except KeyError as e:
            raise AttributeError(name) from e


class _Log:

    def __init__(self) -> None:
        self.notes: dict[str, list[str]] = {}

    def add(self, table: str, n: int, msg: str) -> None:
        if n:
            self.notes.setdefault(table, []).append(f"{int(n):,} {msg}")


def _read(d: Path, name: str, **kw) -> pd.DataFrame:
    return pd.read_csv(d / f"{name}.csv", low_memory=False, **kw)


def _dedupe(df: pd.DataFrame, log: _Log, table: str, subset: list[str] | None = None, what: str = "") -> pd.DataFrame:
    before = len(df)
    df = df.drop_duplicates(subset=subset, keep="first")
    log.add(table, before - len(df), what or "duplicate rows removed")
    return df


def _filter(df: pd.DataFrame, mask: pd.Series, log: _Log, table: str, msg: str) -> pd.DataFrame:
    """Keep rows where mask is True (NA counts as False) and log the rest."""
    mask = mask.fillna(False).astype(bool)
    log.add(table, int((~mask).sum()), msg)
    return df[mask]


def load_dataset(data_dir: str | Path | None = None) -> Dataset:
    d = Path(data_dir).expanduser() if data_dir else default_data_dir()
    if not (d / "sales_daily.csv").exists():
        raise FileNotFoundError(f"Dataset not found in {d} (set ${DATA_DIR_ENV} or the data_dir option)")
    log = _Log()

    # ---- dimensions ------------------------------------------------------- #
    sites = _read(d, "sites")
    sites["site_id"] = norm_text(sites["site_id"])

    stores = _read(d, "stores")
    stores["store_id"] = norm_store(stores["store_id"])
    stores["store_name"] = norm_text(stores["store_name"])
    stores["site_id"] = norm_text(stores["site_id"])
    for c in ("opened_date", "closed_date"):
        stores[c] = parse_dates(stores[c])
    stores = stores.merge(sites[["site_id", "site_name", "site_type", "region"]], on="site_id", how="left")

    closures = _read(d, "store_closures")
    closures["store_id"] = norm_store(closures["store_id"])
    for c in ("start_date", "end_date"):
        closures[c] = parse_dates(closures[c])

    products = _read(d, "products")
    products["sku"] = norm_sku(products["sku"])
    products["product_name"] = norm_text(products["product_name"])
    for c in ("department", "category", "subcategory", "storage_type", "status"):
        products[c] = canon_labels(products[c])
    for c in ("launch_date", "discontinued_date"):
        products[c] = parse_dates(products[c])
    products.loc[products["unit_cost"] < 0, "unit_cost"] = np.nan
    log.add("products", int(products["unit_cost"].isna().sum()), "rows with missing/negative unit_cost (left as NaN)")
    n_sl = int((products["shelf_life_days"] < 0).sum())
    products.loc[products["shelf_life_days"] < 0, "shelf_life_days"] = np.nan
    log.add("products", n_sl, "negative shelf_life_days set to NaN")
    products = _dedupe(products, log, "products", ["sku"], "duplicate SKU rows removed (first occurrence kept; "
                       "duplicates had conflicting cost/category/name)")

    barcodes = _read(d, "product_barcodes", dtype={"barcode": str})
    barcodes["sku"] = norm_sku(barcodes["sku"])
    barcodes = _dedupe(barcodes, log, "product_barcodes")

    suppliers = _read(d, "suppliers")

    promotions = _read(d, "promotions")
    for c in ("start_date", "end_date"):
        promotions[c] = parse_dates(promotions[c])

    calendar = _read(d, "calendar")
    calendar["date"] = parse_dates(calendar["date"])

    valid_stores = set(stores["store_id"])
    valid_skus = set(products["sku"])

    # ---- prices ----------------------------------------------------------- #
    prices = _read(d, "prices")
    prices["store_id"] = norm_store(prices["store_id"])
    prices["sku"] = norm_sku(prices["sku"])
    prices["currency"] = "USD"  # USD / 'USD ' / Usd / US$ / usd are all the same currency
    for c in ("effective_from", "effective_to", "last_updated"):
        prices[c] = parse_dates(prices[c])
    prices = _dedupe(prices, log, "prices")
    # unit_price has '?', 'TBD' and 'UNKNOWN' placeholders, so it loads as text
    prices["unit_price"] = pd.to_numeric(prices["unit_price"], errors="coerce")
    prices = _filter(prices, prices["unit_price"] > 0, log, "prices",
                     "rows with missing/placeholder/non-positive unit_price dropped")
    prices = _filter(prices, ~(prices["effective_to"] < prices["effective_from"]), log, "prices",
                     "rows whose effective_to is before effective_from dropped")
    prices = _filter(prices, prices["store_id"].isin(valid_stores), log, "prices",
                     "rows for stores not in stores.csv dropped")

    # ---- sales ------------------------------------------------------------ #
    sales = _read(d, "sales_daily", dtype={"date": str, "store_id": str, "sku": str})
    sales["store_id"] = norm_store(sales["store_id"])
    sales["sku"] = norm_sku(sales["sku"])
    sales["date"] = parse_dates(sales["date"])
    sales = _filter(sales, sales["date"].notna(), log, "sales_daily", "rows with unparseable date dropped")
    sales = _dedupe(sales, log, "sales_daily", None, "exact duplicate rows removed (after ID/date normalization)")
    sales = _dedupe(sales, log, "sales_daily", ["date", "store_id", "sku"],
                    "rows sharing (date, store, sku) with conflicting values removed (first kept)")
    sales = _filter(sales, sales["units_sold"].notna(), log, "sales_daily", "rows with missing units_sold dropped")
    sales = _filter(sales, sales["units_sold"] >= 0, log, "sales_daily",
                    "rows with negative units_sold dropped (no returns concept in the data)")
    sales = _filter(sales, sales["net_sales"] >= 0, log, "sales_daily", "rows with negative net_sales dropped")
    sales = _filter(sales, sales["store_id"].isin(valid_stores), log, "sales_daily", "rows for unknown stores dropped")
    orphan = int((~sales["sku"].isin(valid_skus)).sum())
    log.add("sales_daily", orphan, "rows whose SKU is not in products.csv (kept; no product attributes)")
    sales["units_sold"] = sales["units_sold"].astype("int32")
    sales["promo_id"] = norm_text(sales["promo_id"])

    # ---- inventory -------------------------------------------------------- #
    inv = _read(d, "inventory_daily")
    inv["store_id"] = norm_store(inv["store_id"])
    inv["sku"] = norm_sku(inv["sku"])
    inv["date"] = parse_dates(inv["date"])
    inv = _dedupe(inv, log, "inventory_daily", None, "exact duplicate rows removed (after normalization)")
    inv = _dedupe(inv, log, "inventory_daily", ["date", "store_id", "sku"],
                  "rows sharing (date, store, sku) with conflicting values removed (first kept)")
    inv = _filter(inv, inv["closing_qty"] >= 0, log, "inventory_daily",
                  "rows with negative closing_qty dropped (violate the stock identity)")
    inv = _filter(inv, inv["store_id"].isin(valid_stores), log, "inventory_daily", "rows for unknown stores dropped")

    # ---- purchase orders -------------------------------------------------- #
    po = _read(d, "purchase_orders")
    po["store_id"] = norm_store(po["store_id"])
    po["sku"] = norm_sku(po["sku"])
    for c in ("order_date", "expected_date", "received_date"):
        po[c] = parse_dates(po[c])
    po = _dedupe(po, log, "purchase_orders", ["po_id", "store_id", "sku"],
                 "duplicate PO lines removed (same po_id/store/sku with conflicting qty_ordered; first kept)")
    po = _filter(po, po["qty_ordered"] > 0, log, "purchase_orders", "lines with non-positive qty_ordered dropped")
    po.loc[po["unit_cost"] < 0, "unit_cost"] = np.nan
    log.add("purchase_orders", int(po["unit_cost"].isna().sum()), "lines with missing/negative unit_cost (left as NaN)")
    unk_sup = int((~po["supplier_id"].isin(suppliers["supplier_id"])).sum())
    log.add("purchase_orders", unk_sup, "lines with a supplier not in suppliers.csv (e.g. SUP99; kept, flagged)")

    # ---- store KPIs / weather -------------------------------------------- #
    kpi = _read(d, "store_daily_kpis")
    kpi["store_id"] = norm_store(kpi["store_id"])
    kpi["date"] = parse_dates(kpi["date"])
    kpi = _dedupe(kpi, log, "store_daily_kpis")
    kpi = _dedupe(kpi, log, "store_daily_kpis", ["store_id", "date"], "rows with conflicting duplicate (store, date) removed")
    bad_staff = (kpi["staff_hours"] <= 0) | (kpi["staff_hours"] > 400)
    log.add("store_daily_kpis", int(bad_staff.sum()), "implausible staff_hours (<=0 or >400) set to NaN")
    kpi.loc[bad_staff, "staff_hours"] = np.nan
    log.add("store_daily_kpis", int(kpi["footfall"].isna().sum()), "rows with missing footfall (left as NaN)")
    kpi.loc[kpi["footfall"] <= 0, "footfall"] = np.nan

    weather = _read(d, "weather_daily")
    weather["site_id"] = norm_text(weather["site_id"])
    weather["date"] = parse_dates(weather["date"])
    weather = _dedupe(weather, log, "weather_daily")
    weather = _dedupe(weather, log, "weather_daily", ["site_id", "date"], "conflicting duplicate (site, date) rows removed")
    sentinel = (weather["temp_max_c"] <= -900) | (weather["temp_min_c"] <= -900)
    log.add("weather_daily", int(sentinel.sum()), "rows with -999 temperature sentinel set to NaN")
    weather.loc[sentinel, ["temp_max_c", "temp_min_c"]] = np.nan
    log.add("weather_daily", int(weather["precip_mm"].isna().sum()), "rows with missing precip_mm (left as NaN)")

    # ---- store-level facts the README mentions ---------------------------- #
    n_unused = len(valid_stores - set(sales["store_id"]))
    log.add("stores", n_unused, "stores in stores.csv with no sales at all (README says 38 stores; file lists 40)")

    tables = dict(sites=sites, stores=stores, store_closures=closures, products=products,
                  product_barcodes=barcodes, suppliers=suppliers, promotions=promotions, calendar=calendar,
                  prices=prices, sales_daily=sales, inventory_daily=inv, purchase_orders=po,
                  store_daily_kpis=kpi, weather_daily=weather)
    for k in ("sales_daily", "inventory_daily", "purchase_orders"):
        tables[k] = tables[k].reset_index(drop=True)
    ds = Dataset(tables=tables, quality=log.notes, data_dir=d)
    logger.info("Loaded Glazed Co. dataset from %s (%s)", d,
                ", ".join(f"{k}={len(v):,}" for k, v in tables.items() if len(v) > 10_000))
    return ds


_CACHE: dict[Path, Dataset] = {}


def get_dataset(data_dir: str | Path | None = None) -> Dataset:
    """Load (once) and return the cleaned dataset."""
    key = (Path(data_dir).expanduser() if data_dir else default_data_dir()).resolve()
    if key not in _CACHE:
        _CACHE[key] = load_dataset(key)
    return _CACHE[key]
