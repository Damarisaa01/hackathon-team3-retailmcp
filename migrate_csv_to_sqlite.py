"""Clean the CSV dataset in data/ and load it into a SQLite database (data/glazed.db).

The raw CSVs have known quality problems (mixed date formats, inconsistent
currency/SKU spellings, duplicates, orphan keys, sign errors, impossible
values, ...). Each fix below is logged to the `data_quality_log` table so every
change is auditable. The resulting database enforces PKs and FKs.

Run with the project venv:  .venv/bin/python migrate_csv_to_sqlite.py [db_path]
"""
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "glazed.db"
DATA_END = pd.Timestamp("2026-09-30")
LOG: list[tuple[str, str, str, int]] = []


def log(table, issue, action, n):
    if n:
        LOG.append((table, issue, action, int(n)))


def read(name):
    return pd.read_csv(DATA_DIR / f"{name}.csv", dtype=str, keep_default_na=False, na_values=[""])


def num(s):
    """Parse to numeric; placeholders like 'TBD' / 'N/A' / '?' become NaN."""
    return pd.to_numeric(s, errors="coerce")


def norm_sku(s):
    """' sku-100284', 'SKU100040' -> 'SKU-100284', 'SKU-100040'."""
    s = s.str.strip().str.upper()
    return s.str.replace(r"^SKU-?(\d+)$", r"SKU-\1", regex=True)


def parse_date(s):
    """Accepts ISO, dd-Mon-yy and US mm/dd/yyyy; returns datetimes (NaT if unparseable)."""
    s = s.str.strip()
    out = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    for fmt in ("%d-%b-%y", "%m/%d/%Y"):
        m = out.isna() & s.notna()
        out[m] = pd.to_datetime(s[m], format=fmt, errors="coerce")
    return out


def iso(s):
    return s.dt.strftime("%Y-%m-%d")


def dedupe(df, table, key, what="duplicate rows"):
    n = len(df)
    df = df.drop_duplicates(subset=key, keep="first")
    log(table, f"{what} on {key}", "kept first occurrence", n - len(df))
    return df


def drop_orphans(df, table, col, valid, label):
    bad = ~df[col].isin(valid)
    log(table, f"{col} not in {label}", "rows dropped", bad.sum())
    return df[~bad]


def interpolate_by(df, group, cols):
    """Fill NaNs by linear interpolation within each group (ordered by date)."""
    df = df.sort_values([group, "date"])
    df[cols] = df.groupby(group)[cols].transform(lambda c: c.interpolate(limit_direction="both"))
    return df


# --------------------------------------------------------------------------- dims
def clean_sites():
    return read("sites").astype({"traffic_tier": float})


def clean_suppliers():
    df = read("suppliers")
    for c in ("standard_lead_time_days", "contract_fill_rate", "contract_on_time_rate", "min_order_value"):
        df[c] = num(df[c])
    return df.astype({"standard_lead_time_days": int})


def clean_stores(sites):
    df = read("stores")
    for c in ("store_name", "format", "operator_type"):
        df[c] = df[c].str.strip()
    log("stores", "inconsistent casing / trailing spaces in store_name", "title-cased + trimmed",
        (df.store_name != df.store_name.str.title().str.replace("Shop-In-Shop", "Shop-in-Shop")).sum())
    df["store_name"] = df.store_name.str.title().str.replace("Shop-In-Shop", "Shop-in-Shop")
    df = drop_orphans(df, "stores", "site_id", sites.site_id, "sites")
    for c in ("opening_time", "closing_time"):
        miss = df[c].isna()
        # fill with the most common value for the same store format
        mode = df.groupby("format")[c].agg(lambda s: s.mode().iat[0])
        df.loc[miss, c] = df.loc[miss, "format"].map(mode)
        log("stores", f"missing {c}", "filled with most common value for the store format", miss.sum())
    df["sqft"] = num(df.sqft).astype(int)
    df["seats"] = num(df.seats).astype(int)
    return df


def clean_closures(stores):
    df = read("store_closures")
    return drop_orphans(df, "store_closures", "store_id", stores.store_id, "stores")


def clean_calendar():
    df = read("calendar")
    for c in ("iso_week", "month", "year", "quarter_idx", "is_weekend", "is_holiday", "is_school_break"):
        df[c] = num(df[c]).astype(int)
    return df


def clean_promotions(sales_raw):
    df = read("promotions")
    for c in ("discount_pct", "expected_uplift_pct"):
        df[c] = num(df[c])
    # a window whose end precedes its start: re-derive the end from the sales that used the promo
    sd = parse_date(df.start_date)
    ed = parse_date(df.end_date)
    bad = ed < sd
    used = sales_raw.dropna(subset=["promo_id"]).groupby("promo_id")["date"].max()
    for i in df.index[bad]:
        pid = df.at[i, "promo_id"]
        ed[i] = used.get(pid, sd[i] + pd.Timedelta(30, unit="D"))
    log("promotions", "end_date before start_date", "end_date re-derived from last sale using the promo", bad.sum())
    df["start_date"], df["end_date"] = iso(sd), iso(ed)
    return df


def clean_products():
    df = read("products")
    df["sku"] = norm_sku(df.sku)
    log("products", "malformed SKU (case / spaces / missing dash)", "normalized to SKU-######",
        (df.sku != read("products").sku).sum())
    for c in ("unit_cost", "list_price_base"):
        df[c] = num(df[c])
    for c in ("case_size", "shelf_life_days", "is_perishable", "inventory_tracked", "is_seasonal"):
        df[c] = num(df[c])

    # category inconsistencies ('BAKERY ', 'Bakery Goods', wrong category on a duplicate row, ...):
    # each (department, subcategory) maps to its most common category
    df["category"] = df.category.str.strip()
    df["subcategory"] = df.subcategory.str.strip()
    mode = df.groupby(["department", "subcategory"]).category.agg(lambda s: s.mode().iat[0])
    new_cat = pd.Series(list(zip(df.department, df.subcategory)), index=df.index).map(mode)
    log("products", "category inconsistent with its subcategory", "set to the subcategory's most common category",
        (new_cat != df.category).sum())
    df["category"] = new_cat

    # duplicate SKUs: after the category fix the survivors are identical or differ only in cost
    df = dedupe(df.sort_values("sku", kind="stable"), "products", ["sku"], "duplicate SKU")

    # prices / costs: 0 or missing -> derive from the typical cost/price ratio of the subcategory
    df.loc[df.list_price_base < 0.05, "list_price_base"] = np.nan  # 0 / 0.01 placeholders
    ok = (df.unit_cost > 0) & (df.list_price_base > 0) & (df.unit_cost < df.list_price_base)
    ratio = (df.unit_cost / df.list_price_base)[ok].groupby([df.department, df.subcategory]).median()
    g_ratio = (df.unit_cost / df.list_price_base)[ok].median()
    r = pd.Series(list(zip(df.department, df.subcategory)), index=df.index).map(ratio).fillna(g_ratio)
    med_list = df.list_price_base[ok].groupby([df.department, df.subcategory]).median()
    med_cost = df.unit_cost[ok].groupby([df.department, df.subcategory]).median()
    key = pd.Series(list(zip(df.department, df.subcategory)), index=df.index)

    cost_bad = df.unit_cost.isna() | (df.unit_cost <= 0) | ((df.unit_cost >= df.list_price_base) & (df.list_price_base > 0))
    both_bad = cost_bad & (df.list_price_base.isna() | (df.list_price_base <= 0))
    list_bad = (df.list_price_base.isna() | (df.list_price_base <= 0)) & ~both_bad
    df.loc[list_bad, "list_price_base"] = (df.unit_cost[list_bad] / r[list_bad]).round(2)
    df.loc[both_bad, "list_price_base"] = key[both_bad].map(med_list).fillna(df.list_price_base[ok].median())
    cost_fix = cost_bad
    df.loc[cost_fix, "unit_cost"] = (df.list_price_base[cost_fix] * r[cost_fix]).round(2)
    log("products", "missing / zero list_price_base", "derived from unit_cost and subcategory margin (or subcategory median)",
        list_bad.sum() + both_bad.sum())
    log("products", "missing / zero / above-price unit_cost", "derived from list_price_base and subcategory margin", cost_fix.sum())

    ld, dd = parse_date(df.launch_date), parse_date(df.discontinued_date)
    swap = dd < ld
    ld[swap], dd[swap] = dd[swap], ld[swap]
    log("products", "discontinued_date before launch_date", "dates swapped", swap.sum())
    miss = (df.status == "Discontinued") & dd.isna()
    dd[miss] = (ld[miss] + pd.Timedelta(365, unit="D")).clip(upper=DATA_END)
    log("products", "Discontinued product without discontinued_date", "set to launch + 1 year (capped at data end)", miss.sum())
    df["launch_date"], df["discontinued_date"] = iso(ld), iso(dd)
    for c in ("case_size", "is_perishable", "inventory_tracked", "is_seasonal"):
        df[c] = df[c].astype(int)
    df["shelf_life_days"] = df.shelf_life_days.astype("Int64")
    return df


def clean_barcodes(products):
    df = read("product_barcodes")
    df["sku"] = norm_sku(df.sku)
    df = drop_orphans(df, "product_barcodes", "sku", products.sku, "products")
    df = dedupe(df, "product_barcodes", ["barcode"], "barcode assigned to several SKUs")
    wrong = (df.barcode_type == "EAN-13") & (df.barcode.str.len() == 12)
    df.loc[wrong, "barcode_type"] = "UPC-A"
    log("product_barcodes", "EAN-13 label on a 12-digit code", "relabelled UPC-A", wrong.sum())
    return df


# --------------------------------------------------------------------------- facts
def clean_prices(products, stores):
    df = read("prices")
    df["sku"] = norm_sku(df.sku)
    cur = df.currency.str.strip().str.upper()
    log("prices", "currency spelled US$ / usd / 'USD '", "normalized to USD", (df.currency != "USD").sum())
    df["currency"] = "USD"
    df = dedupe(df, "prices", ["price_id"], "duplicate price_id")
    df = drop_orphans(df, "prices", "store_id", stores.store_id, "stores")
    df = drop_orphans(df, "prices", "sku", products.sku, "products")

    ef, et = parse_date(df.effective_from), parse_date(df.effective_to)
    df["last_updated"] = iso(parse_date(df.last_updated))
    log("prices", "non-ISO effective_from", "parsed (US mm/dd/yyyy) to ISO", (df.effective_from != iso(ef)).sum())

    # unit_price: placeholders, <=0, and x100 typos -> list price of the product
    p = num(df.unit_price)
    list_price = df.sku.map(products.set_index("sku").list_price_base)
    ratio = p / list_price
    typo = ratio.notna() & ((ratio > 3) | (ratio < 0.33))
    p[typo] = list_price[typo]
    log("prices", "unit_price implausible vs product list price (x10 / x100 typos, 0.01 placeholders)", "replaced by product list_price_base", typo.sum())
    bad = p.isna() | (p <= 0)
    p[bad] = list_price[bad]
    log("prices", "unit_price missing / TBD / N/A / zero / negative", "replaced by product list_price_base", bad.sum())
    df["unit_price"] = p.round(2)

    # validity window ending before it starts: the open-ended price is the effective one
    inv = et < ef
    et[inv] = pd.NaT
    log("prices", "effective_to before effective_from", "effective_to cleared (price is current)", inv.sum())
    df["effective_from"], df["effective_to"] = iso(ef), iso(et)
    df = dedupe(df.sort_values("last_updated", ascending=False, kind="stable"), "prices",
                ["store_id", "sku", "effective_from"], "price conflict for the same store/sku/start date")
    return df.sort_values("price_id")


def clean_sales(sales_raw, products, stores, closures, promos):
    df = sales_raw.copy()
    df["sku"] = norm_sku(df.sku)
    df = drop_orphans(df, "sales_daily", "store_id", stores.store_id, "stores")
    df = drop_orphans(df, "sales_daily", "sku", products.sku, "products")
    # sales while the store was closed / not yet open / permanently closed
    st = stores.set_index("store_id")
    d = df.date
    outside = (d < pd.to_datetime(df.store_id.map(st.opened_date))) | (
        d > pd.to_datetime(df.store_id.map(st.closed_date)).fillna(DATA_END))
    for c in closures.itertuples():
        outside |= (df.store_id == c.store_id) & d.between(c.start_date, c.end_date)
    log("sales_daily", "sales while store was closed / not yet open", "rows dropped", outside.sum())
    df = df[~outside].copy()

    # sign errors (returns without a flag) -> absolute values
    neg = (df.units_sold < 0) | (df.gross_sales < 0) | (df.net_sales < 0)
    for c in ("units_sold", "gross_sales", "net_sales"):
        df[c] = df[c].abs()
    log("sales_daily", "negative units / sales", "sign corrected (abs)", neg.sum())
    list_price = df.sku.map(products.set_index("sku").list_price_base)
    ratio = df.unit_price / list_price
    reprice = ~(df.unit_price > 0) | (ratio > 3) | (ratio < 0.33)
    df.loc[reprice, "unit_price"] = list_price[reprice]
    df.loc[reprice, "gross_sales"] = np.nan
    log("sales_daily", "unit_price implausible vs product list price (0.01 placeholders)", "replaced by product list_price_base", reprice.sum())

    # reconcile units * price = gross. Prices seen on >=5 rows for that SKU are trusted.
    trusted = df.groupby(["sku", "unit_price"]).size()
    trusted = set(trusted[trusted >= 5].index)
    units_null = df.units_sold.isna()
    df.loc[units_null, "units_sold"] = (df.gross_sales / df.unit_price).round()[units_null]
    log("sales_daily", "missing units_sold", "derived as gross_sales / unit_price", units_null.sum())
    df = df[df.units_sold > 0].copy()
    reprice = reprice.reindex(df.index)
    mism = (df.gross_sales - df.units_sold * df.unit_price).abs() > 0.02
    implied_price = (df.gross_sales / df.units_sold).round(2)
    implied_units = df.gross_sales / df.unit_price
    price_wrong = mism & pd.Series([(s, p) in trusted for s, p in zip(df.sku, implied_price)], index=df.index)
    df.loc[price_wrong, "unit_price"] = implied_price[price_wrong]
    units_wrong = mism & ~price_wrong & ((implied_units - implied_units.round()).abs() < 0.02) & (implied_units.round() >= 1)
    df.loc[units_wrong, "units_sold"] = implied_units.round()[units_wrong]
    rest = mism & ~price_wrong & ~units_wrong
    log("sales_daily", "unit_price off (gross / units is a known price)", "unit_price restored", price_wrong.sum())
    log("sales_daily", "units_sold off by a factor (gross / price is whole)", "units_sold restored", units_wrong.sum())
    log("sales_daily", "units*price != gross_sales, no clear culprit", "gross_sales recomputed", rest.sum())
    df["gross_sales"] = (df.units_sold * df.unit_price).round(2)
    df.loc[reprice, "discount_amount"] = np.nan  # recomputed below from the promo %

    # discounts: must be within [0, gross]; otherwise use the promo %, else none
    pct = df.promo_id.map(promos.set_index("promo_id").discount_pct)
    unknown_promo = df.promo_id.notna() & ~df.promo_id.isin(promos.promo_id)
    df.loc[unknown_promo, "promo_id"] = None
    log("sales_daily", "promo_id not in promotions", "promo_id cleared", unknown_promo.sum())
    bad_disc = df.discount_amount.isna() | (df.discount_amount < 0) | (df.discount_amount > df.gross_sales)
    df.loc[bad_disc, "discount_amount"] = (df.gross_sales * pct.fillna(0) / 100).round(2)[bad_disc]
    log("sales_daily", "discount negative / above gross", "recomputed from promo % (0 if none)", bad_disc.sum())
    mism_net = (df.net_sales - (df.gross_sales - df.discount_amount)).abs() > 0.02
    log("sales_daily", "net_sales != gross - discount", "net_sales recomputed", mism_net.sum())
    df["net_sales"] = (df.gross_sales - df.discount_amount).round(2)

    df["date"] = iso(df.date)
    df = dedupe(df, "sales_daily", ["date", "store_id", "sku"], "duplicate date/store/sku")
    df["units_sold"] = df.units_sold.astype(int)
    return df


def clean_inventory(products, stores):
    df = read("inventory_daily")
    df["sku"] = norm_sku(df.sku)
    for c in df.columns[3:]:
        df[c] = num(df[c])
    df["date"] = iso(parse_date(df.date))
    df = drop_orphans(df, "inventory_daily", "store_id", stores.store_id, "stores")
    df = drop_orphans(df, "inventory_daily", "sku", products.sku, "products")
    df = dedupe(df, "inventory_daily", ["date", "store_id", "sku"], "duplicate date/store/sku")

    qty = ["opening_qty", "received_qty", "units_sold", "expired_qty", "damaged_qty", "on_order_qty"]
    neg = (df[qty + ["closing_qty", "stockout_hours"]] < 0).any(axis=1)
    df[qty] = df[qty].abs()
    df["stockout_hours"] = df.stockout_hours.abs()
    log("inventory_daily", "negative quantities / hours", "sign corrected (abs)", neg.sum())
    over = df.stockout_hours > 24
    df.loc[over, "stockout_hours"] = 24
    log("inventory_daily", "stockout_hours above 24", "capped at 24", over.sum())

    # enforce closing = opening + received - sold - expired - damaged (units_sold shrinks if stock would go negative)
    avail = df.opening_qty + df.received_qty - df.expired_qty - df.damaged_qty
    short = df.units_sold > avail
    df.loc[short, "units_sold"] = avail[short].clip(lower=0)
    # if expired + damaged alone exceed stock, trim damaged then expired
    still = avail < 0
    df.loc[still, "damaged_qty"] = (df.damaged_qty - (-avail)).clip(lower=0)[still]
    avail = df.opening_qty + df.received_qty - df.expired_qty - df.damaged_qty
    still = avail < 0
    df.loc[still, "expired_qty"] = (df.expired_qty + avail).clip(lower=0)[still]
    new_closing = df.opening_qty + df.received_qty - df.units_sold - df.expired_qty - df.damaged_qty
    log("inventory_daily", "closing_qty != opening + received - sold - expired - damaged", "closing_qty recomputed",
        (new_closing != df.closing_qty).sum())
    log("inventory_daily", "units_sold above available stock", "units_sold capped at available stock", short.sum())
    df["closing_qty"] = new_closing
    for c in qty + ["closing_qty"]:
        df[c] = df[c].astype(int)
    return df


def clean_purchase_orders(products, stores, suppliers):
    df = read("purchase_orders")
    df["sku"] = norm_sku(df.sku)
    df = drop_orphans(df, "purchase_orders", "store_id", stores.store_id, "stores")
    df = drop_orphans(df, "purchase_orders", "sku", products.sku, "products")
    df = dedupe(df, "purchase_orders", ["po_id"], "duplicate po_id")
    for c in ("qty_ordered", "qty_received", "unit_cost"):
        df[c] = num(df[c])
    od, ed, rd = (parse_date(df[c]) for c in ("order_date", "expected_date", "received_date"))

    # supplier must be the product's supplier
    sup = df.sku.map(products.set_index("sku").supplier_id)
    bad = df.supplier_id != sup
    df.loc[bad, "supplier_id"] = sup[bad]
    log("purchase_orders", "supplier_id unknown / differs from the product's supplier", "set to product's supplier", bad.sum())

    # dates
    lead = pd.to_timedelta(df.supplier_id.map(suppliers.set_index("supplier_id").standard_lead_time_days), unit="D")
    bad = ed.isna() | (ed < od)
    ed[bad] = (od + lead)[bad]
    log("purchase_orders", "expected_date missing / before order_date", "order_date + supplier lead time", bad.sum())
    delivered = df.status.isin(["Delivered", "Short-shipped"])
    bad = delivered & (rd.isna() | (rd < od))
    rd[bad] = ed[bad]
    log("purchase_orders", "received_date missing / before order_date on a received PO", "set to expected_date", bad.sum())

    # quantities / costs
    bad = df.qty_ordered.isna() | (df.qty_ordered <= 0)
    df.loc[bad, "qty_ordered"] = df.sku.map(products.set_index("sku").case_size)[bad]
    log("purchase_orders", "qty_ordered missing / <= 0", "set to product case_size", bad.sum())
    df["qty_ordered"] = df.qty_ordered.abs()
    df.loc[delivered & df.qty_received.isna(), "qty_received"] = df.qty_ordered
    over = delivered & (df.qty_received > df.qty_ordered)
    df.loc[over, "qty_received"] = df.qty_ordered[over]
    log("purchase_orders", "qty_received above qty_ordered", "capped at qty_ordered", over.sum())
    df.loc[delivered & (df.qty_received < 0), "qty_received"] = df.qty_ordered
    short = (df.status == "Delivered") & (df.qty_received < df.qty_ordered)
    df.loc[short, "status"] = "Short-shipped"
    log("purchase_orders", "Delivered with qty_received < qty_ordered", "status set to Short-shipped", short.sum())
    full = (df.status == "Short-shipped") & (df.qty_received >= df.qty_ordered)
    df.loc[full, "status"] = "Delivered"
    bad = df.unit_cost.isna() | (df.unit_cost <= 0)
    df.loc[bad, "unit_cost"] = df.sku.map(products.set_index("sku").unit_cost)[bad]
    log("purchase_orders", "unit_cost missing / <= 0", "set to product unit_cost", bad.sum())

    df["order_date"], df["expected_date"], df["received_date"] = iso(od), iso(ed), iso(rd)
    for c in ("qty_ordered", "qty_received"):
        df[c] = df[c].astype("Int64")
    return df


def clean_kpis(stores):
    df = read("store_daily_kpis")
    df["date"] = parse_date(df.date)
    cols = ["footfall", "transactions", "units_sold", "net_sales", "staff_hours", "open_hours"]
    for c in cols:
        df[c] = num(df[c])
    df = drop_orphans(df, "store_daily_kpis", "store_id", stores.store_id, "stores")
    df = dedupe(df, "store_daily_kpis", ["store_id", "date"], "duplicate store/date")

    # impossible values -> NaN -> interpolated per store
    bad_ranges = {
        "footfall": df.footfall < 0, "transactions": df.transactions < 0, "units_sold": df.units_sold < 0,
        "net_sales": df.net_sales < 0, "staff_hours": (df.staff_hours < 0) | (df.staff_hours > 24 * 10),
        "open_hours": (df.open_hours < 0) | (df.open_hours > 24),
    }
    n = 0
    for c, m in bad_ranges.items():
        df.loc[m, c] = np.nan
        n += m.sum()
    log("store_daily_kpis", "negative / impossible values (e.g. staff_hours 999)", "set to NULL then interpolated", n)
    m = df.transactions > df.footfall
    df.loc[m, "footfall"] = np.nan
    log("store_daily_kpis", "transactions > footfall", "footfall interpolated", m.sum())
    nulls = df[cols].isna().any(axis=1)
    df = interpolate_by(df, "store_id", cols)
    log("store_daily_kpis", "rows with missing values", "linear interpolation within the store", nulls.sum())
    for c in ("footfall", "transactions", "units_sold"):
        df[c] = df[c].round().astype(int)
    df["transactions"] = df[["transactions", "footfall"]].min(axis=1)
    df["net_sales"] = df.net_sales.round(2)
    df["staff_hours"] = df.staff_hours.round(1)
    df["open_hours"] = df.open_hours.round().astype(int)
    df["date"] = iso(df.date)
    return df


def clean_weather(sites):
    df = read("weather_daily")
    df["date"] = parse_date(df.date)
    for c in ("temp_max_c", "temp_min_c", "precip_mm"):
        df[c] = num(df[c])
    df = drop_orphans(df, "weather_daily", "site_id", sites.site_id, "sites")
    df = dedupe(df, "weather_daily", ["site_id", "date"], "duplicate site/date")
    sentinel = (df.temp_min_c <= -99) | (df.temp_max_c <= -99)
    df.loc[sentinel, ["temp_min_c", "temp_max_c"]] = np.nan
    log("weather_daily", "-999 sentinel temperatures", "set to NULL then interpolated", sentinel.sum())
    nulls = df.temp_min_c.isna() | df.temp_max_c.isna()
    d2 = df.rename(columns={"site_id": "grp"})
    d2 = interpolate_by(d2, "grp", ["temp_min_c", "temp_max_c"]).rename(columns={"grp": "site_id"})
    df = d2
    log("weather_daily", "missing temperatures", "linear interpolation within the site", nulls.sum())
    swap = df.temp_min_c > df.temp_max_c
    df.loc[swap, ["temp_min_c", "temp_max_c"]] = df.loc[swap, ["temp_max_c", "temp_min_c"]].values
    log("weather_daily", "temp_min_c > temp_max_c", "values swapped", swap.sum())
    miss = df.precip_mm.isna()
    df.loc[miss, "precip_mm"] = 0.0
    log("weather_daily", "missing precip_mm", "set to 0", miss.sum())
    df["date"] = iso(df.date)
    return df


# --------------------------------------------------------------------------- schema
SCHEMA = """
CREATE TABLE sites (site_id TEXT PRIMARY KEY, site_name TEXT NOT NULL, site_type TEXT NOT NULL, region TEXT NOT NULL, traffic_tier REAL NOT NULL);
CREATE TABLE suppliers (supplier_id TEXT PRIMARY KEY, supplier_name TEXT NOT NULL, category_focus TEXT, standard_lead_time_days INTEGER NOT NULL,
  contract_fill_rate REAL, contract_on_time_rate REAL, min_order_value REAL, delivery_days TEXT);
CREATE TABLE stores (store_id TEXT PRIMARY KEY, site_id TEXT NOT NULL REFERENCES sites, store_name TEXT NOT NULL, format TEXT NOT NULL,
  operator_type TEXT NOT NULL, opening_time TEXT NOT NULL, closing_time TEXT NOT NULL, opened_date TEXT NOT NULL, closed_date TEXT, sqft INTEGER, seats INTEGER);
CREATE TABLE store_closures (store_id TEXT NOT NULL REFERENCES stores, start_date TEXT NOT NULL, end_date TEXT NOT NULL, reason TEXT,
  PRIMARY KEY (store_id, start_date));
CREATE TABLE calendar (date TEXT PRIMARY KEY, day_of_week TEXT, iso_week INTEGER, month INTEGER, year INTEGER, quarter_idx INTEGER,
  is_weekend INTEGER, is_holiday INTEGER, holiday_name TEXT, is_school_break INTEGER);
CREATE TABLE promotions (promo_id TEXT PRIMARY KEY, promo_name TEXT NOT NULL, promo_type TEXT, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  discount_pct REAL, department TEXT, category TEXT, region_scope TEXT, expected_uplift_pct REAL);
CREATE TABLE products (sku TEXT PRIMARY KEY, product_name TEXT NOT NULL, department TEXT NOT NULL, category TEXT NOT NULL, subcategory TEXT,
  brand TEXT, supplier_id TEXT NOT NULL REFERENCES suppliers, unit_cost REAL NOT NULL, list_price_base REAL NOT NULL, case_size INTEGER,
  shelf_life_days INTEGER, is_perishable INTEGER, storage_type TEXT, inventory_tracked INTEGER, is_seasonal INTEGER, launch_date TEXT,
  discontinued_date TEXT, status TEXT);
CREATE TABLE product_barcodes (barcode TEXT PRIMARY KEY, sku TEXT NOT NULL REFERENCES products, barcode_type TEXT);
CREATE TABLE prices (price_id TEXT PRIMARY KEY, store_id TEXT NOT NULL REFERENCES stores, sku TEXT NOT NULL REFERENCES products,
  unit_price REAL NOT NULL, currency TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT, price_list TEXT, last_updated TEXT,
  UNIQUE (store_id, sku, effective_from));
CREATE TABLE sales_daily (date TEXT NOT NULL, store_id TEXT NOT NULL REFERENCES stores, sku TEXT NOT NULL REFERENCES products,
  units_sold INTEGER NOT NULL, unit_price REAL NOT NULL, gross_sales REAL NOT NULL, discount_amount REAL NOT NULL, net_sales REAL NOT NULL,
  promo_id TEXT REFERENCES promotions, PRIMARY KEY (date, store_id, sku));
CREATE TABLE inventory_daily (date TEXT NOT NULL, store_id TEXT NOT NULL REFERENCES stores, sku TEXT NOT NULL REFERENCES products,
  opening_qty INTEGER NOT NULL, received_qty INTEGER NOT NULL, units_sold INTEGER NOT NULL, expired_qty INTEGER NOT NULL, damaged_qty INTEGER NOT NULL,
  closing_qty INTEGER NOT NULL, on_order_qty INTEGER NOT NULL, stockout_hours REAL NOT NULL, PRIMARY KEY (date, store_id, sku));
CREATE TABLE purchase_orders (po_id TEXT PRIMARY KEY, store_id TEXT NOT NULL REFERENCES stores, supplier_id TEXT NOT NULL REFERENCES suppliers,
  sku TEXT NOT NULL REFERENCES products, order_date TEXT NOT NULL, expected_date TEXT NOT NULL, received_date TEXT, qty_ordered INTEGER NOT NULL,
  qty_received INTEGER, unit_cost REAL NOT NULL, status TEXT NOT NULL);
CREATE TABLE store_daily_kpis (store_id TEXT NOT NULL REFERENCES stores, date TEXT NOT NULL, footfall INTEGER NOT NULL, transactions INTEGER NOT NULL,
  units_sold INTEGER NOT NULL, net_sales REAL NOT NULL, staff_hours REAL NOT NULL, open_hours INTEGER NOT NULL, PRIMARY KEY (store_id, date));
CREATE TABLE weather_daily (site_id TEXT NOT NULL REFERENCES sites, date TEXT NOT NULL, temp_max_c REAL NOT NULL, temp_min_c REAL NOT NULL,
  precip_mm REAL NOT NULL, PRIMARY KEY (site_id, date));
CREATE TABLE data_quality_log (table_name TEXT, issue TEXT, action TEXT, rows_affected INTEGER);
CREATE INDEX idx_sales_store ON sales_daily(store_id); CREATE INDEX idx_sales_sku ON sales_daily(sku); CREATE INDEX idx_sales_promo ON sales_daily(promo_id);
CREATE INDEX idx_inv_store ON inventory_daily(store_id); CREATE INDEX idx_inv_sku ON inventory_daily(sku);
CREATE INDEX idx_po_store ON purchase_orders(store_id); CREATE INDEX idx_po_sku ON purchase_orders(sku); CREATE INDEX idx_po_supplier ON purchase_orders(supplier_id);
CREATE INDEX idx_prices_sku ON prices(sku); CREATE INDEX idx_stores_site ON stores(site_id); CREATE INDEX idx_products_supplier ON products(supplier_id);
"""


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH

    sales_raw = read("sales_daily")
    log("sales_daily", "non-ISO dates (dd-Mon-yy, mm/dd/yyyy)", "parsed to ISO", (~sales_raw.date.str.match(r"^\d{4}-")).sum())
    sales_raw["date"] = parse_date(sales_raw.date)
    for c in ("units_sold", "unit_price", "gross_sales", "discount_amount", "net_sales"):
        sales_raw[c] = num(sales_raw[c])

    sites, suppliers, calendar = clean_sites(), clean_suppliers(), clean_calendar()
    stores = clean_stores(sites)
    closures = clean_closures(stores)
    promos = clean_promotions(sales_raw)
    for c in ("start_date", "end_date"):
        closures[c] = pd.to_datetime(closures[c])
    products = clean_products()
    products = drop_orphans(products, "products", "supplier_id", suppliers.supplier_id, "suppliers")
    barcodes = clean_barcodes(products)
    prices = clean_prices(products, stores)
    sales = clean_sales(sales_raw, products, stores, closures, promos)
    for c in ("start_date", "end_date"):
        closures[c] = iso(closures[c])
    inventory = clean_inventory(products, stores)
    pos = clean_purchase_orders(products, stores, suppliers)
    kpis = clean_kpis(stores)
    weather = clean_weather(sites)

    tables = {"sites": sites, "suppliers": suppliers, "stores": stores, "store_closures": closures, "calendar": calendar,
              "promotions": promos, "products": products, "product_barcodes": barcodes, "prices": prices,
              "sales_daily": sales, "inventory_daily": inventory, "purchase_orders": pos,
              "store_daily_kpis": kpis, "weather_daily": weather}

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        for name, df in tables.items():
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
            out = df[cols].astype(object).where(df[cols].notna(), None)
            conn.executemany(f"INSERT INTO {name} VALUES ({','.join('?' * len(cols))})", out.itertuples(index=False, name=None))
            print(f"{name}: {len(df)} rows")
        conn.executemany("INSERT INTO data_quality_log VALUES (?,?,?,?)", LOG)
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    print(f"foreign key violations: {len(bad)}")
    conn.close()
    print("\nCleaning log:")
    for t, issue, action, n in LOG:
        print(f"  [{t}] {issue} -> {action}: {n}")


if __name__ == "__main__":
    main()
