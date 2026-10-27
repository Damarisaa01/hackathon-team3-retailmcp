"""Analytics over the cleaned Glazed Co. dataset.

Every function takes the ``Dataset`` first, returns a JSON-serializable dict, and
raises ``ValueError`` with a message meant for the LLM caller on bad input.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any

import numpy as np
import pandas as pd

from .data import Dataset
from .data import norm_sku
from .data import norm_store
from .data import parse_dates

MAX_LIMIT = 500

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _jsonable(v: Any) -> Any:
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, (np.integer, )):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if (math.isnan(v) or math.isinf(v)) else round(float(v), 4)
    if isinstance(v, (np.bool_, )):
        return bool(v)
    if v is pd.NA:
        return None
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: _jsonable(v) for k, v in row.items()} for row in df.to_dict("records")]


def _limit(limit: int | None, default: int = 25) -> int:
    return max(1, min(int(limit or default), MAX_LIMIT))


def _one_date(v: str | None, name: str) -> pd.Timestamp | None:
    if not v:
        return None
    t = parse_dates(pd.Series([v])).iloc[0]
    if pd.isna(t):
        raise ValueError(f"{name}: '{v}' is not a valid date; use YYYY-MM-DD")
    return t


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in str(v).split(",") if x.strip()] if v else []


def _text_mask(s: pd.Series, value: str | None) -> pd.Series | None:
    """Case-insensitive match; comma-separated values mean 'any of'."""
    vals = [x.lower() for x in _split(value)]
    if not vals:
        return None
    return s.astype("string").str.strip().str.lower().isin(vals)


def _id_mask(s: pd.Series, value: str | None, kind: str) -> pd.Series | None:
    vals = _split(value)
    if not vals:
        return None
    norm = norm_store if kind == "store" else norm_sku
    return s.isin(list(norm(pd.Series(vals))))


def apply_filters(df: pd.DataFrame, *, date_from=None, date_to=None, date_col="date", store_id=None, site_id=None,
                  region=None, department=None, category=None, sku=None) -> pd.DataFrame:
    masks = []
    d0, d1 = _one_date(date_from, "date_from"), _one_date(date_to, "date_to")
    if d0 is not None and date_col in df:
        masks.append(df[date_col] >= d0)
    if d1 is not None and date_col in df:
        masks.append(df[date_col] <= d1)
    for col, val, kind in (("store_id", store_id, "store"), ("sku", sku, "sku")):
        if val and col in df:
            masks.append(_id_mask(df[col], val, kind))
    for col, val in (("site_id", site_id), ("region", region), ("department", department), ("category", category)):
        if val and col in df:
            masks.append(_text_mask(df[col], val))
    keep = pd.Series(True, index=df.index)
    for m in masks:
        keep &= m.fillna(False)  # every mask is built on the original, unfiltered frame
    return df[keep.values]


def _filters_echo(**kw) -> dict:
    return {k: v for k, v in kw.items() if v not in (None, "", [])}


def _frame_cache(ds: Dataset, key: str, build) -> pd.DataFrame:
    cache = ds.__dict__.setdefault("_derived", {})
    if key not in cache:
        cache[key] = build()
    return cache[key]


def sales_frame(ds: Dataset) -> pd.DataFrame:
    """sales_daily enriched with store, product and calendar attributes."""

    def build():
        s = ds.sales_daily.merge(ds.stores[["store_id", "site_id", "site_name", "region", "format"]], on="store_id",
                                 how="left")
        s = s.merge(ds.products[["sku", "product_name", "department", "category", "subcategory", "unit_cost"]],
                    on="sku", how="left")
        s = s.merge(ds.calendar[["date", "day_of_week", "is_holiday", "holiday_name", "is_weekend"]], on="date",
                    how="left")
        s["week"] = s["date"].dt.to_period("W-SUN").dt.start_time.dt.strftime("%Y-%m-%d")
        s["month"] = s["date"].dt.strftime("%Y-%m")
        return s

    return _frame_cache(ds, "sales", build)


def inventory_frame(ds: Dataset) -> pd.DataFrame:

    def build():
        i = ds.inventory_daily.merge(ds.stores[["store_id", "site_id", "site_name", "region"]], on="store_id",
                                     how="left")
        i = i.merge(ds.products[["sku", "product_name", "department", "category", "unit_cost", "case_size",
                                 "shelf_life_days", "supplier_id"]], on="sku", how="left")
        i["month"] = i["date"].dt.strftime("%Y-%m")
        i["waste_cost"] = i["expired_qty"] * i["unit_cost"]
        return i

    return _frame_cache(ds, "inventory", build)


def _finish(grouped: pd.DataFrame, key_cols: list[str], sort_by: str | None, ascending: bool, limit: int,
            chronological: bool) -> tuple[pd.DataFrame, int]:
    total = len(grouped)
    if chronological and key_cols:
        grouped = grouped.sort_values(key_cols)
    elif sort_by:
        if sort_by not in grouped.columns:
            raise ValueError(f"sort_by '{sort_by}' invalid; choose one of {[c for c in grouped.columns if c not in key_cols]}")
        grouped = grouped.sort_values(sort_by, ascending=ascending, na_position="last")
    return grouped.head(limit), total


def _div(a, b):
    return np.where(np.asarray(b) > 0, np.asarray(a) / np.where(np.asarray(b) > 0, b, 1), np.nan)


def _check_choice(value: str, choices: list[str] | dict, name: str) -> str:
    v = (value or "").strip().lower()
    if v not in choices:
        raise ValueError(f"{name} '{value}' invalid; choose one of {list(choices)}")
    return v


# --------------------------------------------------------------------------- #
# Dataset / quality
# --------------------------------------------------------------------------- #


def dataset_overview(ds: Dataset) -> dict:
    tables = {}
    for name, df in ds.tables.items():
        tables[name] = {"rows": len(df), "columns": list(df.columns)}
    s = ds.sales_daily
    return {
        "business": "Glazed Co. (fictional US chain of donut/coffee/bakery shops in airports, rail & bus stations, one resort)",
        "currency": "USD",
        "period": {"from": _jsonable(s["date"].min()), "to": _jsonable(s["date"].max())},
        "counts": {"sites": len(ds.sites), "stores": len(ds.stores), "skus": len(ds.products),
                   "suppliers": len(ds.suppliers), "promotions": len(ds.promotions)},
        "departments": sorted(ds.products["department"].dropna().unique().tolist()),
        "categories": sorted(ds.products["category"].dropna().unique().tolist()),
        "regions": sorted(ds.sites["region"].unique().tolist()),
        "store_formats": sorted(ds.stores["format"].unique().tolist()),
        "tables": tables,
        "notes": ["Data is synthetic and has intentional quality issues; tools run on a cleaned copy. "
                  "Call get_data_quality_report to see what was fixed or dropped.",
                  "inventory_daily/purchase_orders only cover SKUs with inventory_tracked=1 "
                  "(made-to-order drinks are sold but not inventory-tracked)."],
    }


def data_quality_report(ds: Dataset, table: str | None = None) -> dict:
    q = ds.quality
    if table:
        t = table.strip().lower()
        if t not in q:
            return {"table": t, "issues": [], "note": f"No issues logged for '{t}'. Tables with issues: {sorted(q)}"}
        return {"table": t, "issues": q[t]}
    return {"issues_by_table": q, "total_tables_with_issues": len(q),
            "note": "Source files are untouched; these are the cleaning actions applied to the in-memory copy."}


# --------------------------------------------------------------------------- #
# Master data
# --------------------------------------------------------------------------- #


def list_stores(ds: Dataset, site_id=None, region=None, format=None, status=None, limit=100) -> dict:
    st = ds.stores
    for col, val in (("site_id", site_id), ("region", region), ("format", format)):
        m = _text_mask(st[col], val)
        if m is not None:
            st = st[m]
    if status:
        s = _check_choice(status, ["open", "closed"], "status")
        st = st[st["closed_date"].isna()] if s == "open" else st[st["closed_date"].notna()]
    cl = ds.store_closures
    out = st.sort_values("store_id").head(_limit(limit, 100)).copy()
    rows = records(out[["store_id", "store_name", "site_id", "site_name", "site_type", "region", "format",
                        "operator_type", "opening_time", "closing_time", "opened_date", "closed_date", "sqft", "seats"]])
    for r in rows:
        r["closures"] = records(cl[cl["store_id"] == r["store_id"]][["start_date", "end_date", "reason"]])
    return {"total_matching": len(st), "returned": len(rows), "stores": rows}


def search_products(ds: Dataset, query=None, department=None, category=None, status=None, inventory_tracked=None,
                    supplier_id=None, limit=25, offset=0) -> dict:
    p = ds.products
    if query:
        q = query.strip()
        def search_key(value: object) -> str:
            normalized = unicodedata.normalize("NFKD", str(value)).casefold()
            return "".join(character for character in normalized if not unicodedata.combining(character))

        synonyms = {
            "cafe": "coffee",
            "cafes": "coffee",
            "dona": "donut",
            "donas": "donut",
            "donuts": "donut",
            "sandwiches": "sandwich",
        }
        q = search_key(query.strip())
        query_variants = {q}
        words = q.split()
        for index, word in enumerate(words):
            if word in synonyms:
                translated = words.copy()
                translated[index] = synonyms[word]
                query_variants.add(" ".join(translated))
        names = p["product_name"].map(search_key)
        skus = p["sku"].map(search_key)
        m = pd.Series(False, index=p.index)
        for term in query_variants:
            m |= names.str.contains(term, regex=False, na=False) | skus.str.contains(term, regex=False, na=False)
        p = p[m.fillna(False)]
    for col, val in (("department", department), ("category", category), ("status", status),
                     ("supplier_id", supplier_id)):
        m = _text_mask(p[col], val)
        if m is not None:
            p = p[m]
    if inventory_tracked is not None:
        p = p[p["inventory_tracked"] == int(bool(inventory_tracked))]
    cols = ["sku", "product_name", "department", "category", "subcategory", "supplier_id", "unit_cost",
            "list_price_base", "status", "inventory_tracked", "is_perishable", "shelf_life_days"]
    start = max(0, int(offset))
    page = p.sort_values("sku").iloc[start:start + _limit(limit)]
    return {"total_matching": len(p), "returned": len(page), "offset": start,
            "products": records(page[cols])}


def get_product(ds: Dataset, sku_or_barcode: str) -> dict:
    key = (sku_or_barcode or "").strip()
    if not key:
        raise ValueError("sku_or_barcode is required")
    sku = norm_sku(pd.Series([key])).iloc[0]
    bc = ds.product_barcodes
    if sku not in set(ds.products["sku"]):
        hit = bc[bc["barcode"] == key]
        if hit.empty:
            raise ValueError(f"No product found for '{key}'. Try search_products.")
        sku = hit.iloc[0]["sku"]
    p = ds.products[ds.products["sku"] == sku].iloc[0]
    sup = ds.suppliers[ds.suppliers["supplier_id"] == p["supplier_id"]]
    pr = ds.prices[ds.prices["sku"] == sku]
    current = pr[pr["effective_to"].isna()]
    s = sales_frame(ds)
    s = s[s["sku"] == sku]
    changes = (pr.groupby(["effective_from", "effective_to"], dropna=False)["unit_price"].agg(["mean", "min", "max", "count"])
               .reset_index().rename(columns={"count": "stores"}).sort_values("effective_from"))
    out = {k: _jsonable(v) for k, v in p.to_dict().items()}
    out.update({
        "barcodes": records(bc[bc["sku"] == sku][["barcode", "barcode_type"]]),
        "supplier": records(sup)[0] if len(sup) else None,
        "current_price": ({"stores": int(len(current)), "min": _jsonable(current["unit_price"].min()),
                           "max": _jsonable(current["unit_price"].max()), "avg": _jsonable(current["unit_price"].mean())}
                          if len(current) else None),
        "price_periods": records(changes),
        "sales_total": {"units": int(s["units_sold"].sum()), "net_sales": _jsonable(round(s["net_sales"].sum(), 2)),
                        "stores_selling": int(s["store_id"].nunique()),
                        "first_sale": _jsonable(s["date"].min()), "last_sale": _jsonable(s["date"].max())},
    })
    return out


def calendar_lookup(ds: Dataset, date_from=None, date_to=None, holidays_only=True, limit=60) -> dict:
    c = ds.calendar
    c = apply_filters(c, date_from=date_from, date_to=date_to)
    if holidays_only:
        c = c[c["is_holiday"] == 1]
    return {"returned": min(len(c), _limit(limit, 60)), "total_matching": len(c),
            "days": records(c.sort_values("date").head(_limit(limit, 60))[
                ["date", "day_of_week", "iso_week", "is_weekend", "is_holiday", "holiday_name", "is_school_break"]])}


# --------------------------------------------------------------------------- #
# Sales
# --------------------------------------------------------------------------- #

SALES_GROUPS = {
    "none": [], "day": ["date"], "week": ["week"], "month": ["month"], "store": ["store_id", "store_name"],
    "site": ["site_id", "site_name"], "region": ["region"], "format": ["format"], "department": ["department"],
    "category": ["category"], "subcategory": ["subcategory"], "sku": ["sku", "product_name"],
    "day_of_week": ["day_of_week"], "holiday": ["holiday_name"], "promo": ["promo_id"],
}
TIME_GROUPS = {"day", "week", "month"}


def sales_summary(ds: Dataset, group_by="none", date_from=None, date_to=None, store_id=None, site_id=None, region=None,
                  department=None, category=None, sku=None, sort_by="net_sales", ascending=False, limit=25) -> dict:
    g = _check_choice(group_by, SALES_GROUPS, "group_by")
    s = apply_filters(sales_frame(ds), date_from=date_from, date_to=date_to, store_id=store_id, site_id=site_id,
                      region=region, department=department, category=category, sku=sku)
    if g == "holiday":
        s = s[s["is_holiday"] == 1]
    if g == "store":
        s = s.merge(ds.stores[["store_id", "store_name"]], on="store_id", how="left")
    keys = SALES_GROUPS[g]
    agg = {"units": ("units_sold", "sum"), "gross_sales": ("gross_sales", "sum"), "discount": ("discount_amount", "sum"),
           "net_sales": ("net_sales", "sum"), "sku_store_days": ("units_sold", "size")}

    def finish(df: pd.DataFrame) -> pd.DataFrame:
        df["discount_rate"] = _div(df["discount"], df["gross_sales"])
        df["avg_net_price_per_unit"] = _div(df["net_sales"], df["units"])
        return df

    grouped = (s.groupby(keys, dropna=False, observed=True).agg(**agg).reset_index() if keys else
               pd.DataFrame([s.agg({"units_sold": "sum", "gross_sales": "sum", "discount_amount": "sum",
                                    "net_sales": "sum"}).rename({"units_sold": "units", "discount_amount": "discount"})
                             .to_dict() | {"sku_store_days": len(s)}]))
    grouped = finish(grouped)
    total = finish(pd.DataFrame([{"units": s["units_sold"].sum(), "gross_sales": s["gross_sales"].sum(),
                                  "discount": s["discount_amount"].sum(), "net_sales": s["net_sales"].sum(),
                                  "sku_store_days": len(s)}]))
    grouped = grouped.round({"gross_sales": 2, "discount": 2, "net_sales": 2, "discount_rate": 4,
                             "avg_net_price_per_unit": 3})
    rows, n = _finish(grouped, keys, sort_by, ascending, _limit(limit), g in TIME_GROUPS)
    return {
        "filters": _filters_echo(date_from=date_from, date_to=date_to, store_id=store_id, site_id=site_id, region=region,
                                 department=department, category=category, sku=sku),
        "group_by": g, "total_groups": n, "returned": len(rows), "truncated": n > len(rows),
        "totals": records(total.round(4))[0], "rows": records(rows),
        "notes": ["net_sales = gross_sales - discount. Rows with missing/negative units or net_sales were removed "
                  "in cleaning. Time groups are in chronological order; raise `limit` to see all periods."],
    }


def sales_comparison(ds: Dataset, date_from=None, date_to=None, store_id=None, category=None, limit=20,
                     forecast_days=30, sort_by="net_sales", sku=None) -> dict:
    if len(_split(sku)) > 3:
        raise ValueError("Select no more than 3 product SKUs for comparison.")
    sales = sales_frame(ds)
    latest_date = sales["date"].max()
    end = _one_date(date_to, "date_to") or latest_date
    start = _one_date(date_from, "date_from") or end - pd.Timedelta(days=29)
    if start > end:
        raise ValueError("date_from must be on or before date_to")

    period_days = (end - start).days + 1
    previous_end = start - pd.Timedelta(days=1)
    previous_start = previous_end - pd.Timedelta(days=period_days - 1)
    current = apply_filters(sales, date_from=start, date_to=end, store_id=store_id, category=category, sku=sku)
    previous = apply_filters(sales, date_from=previous_start, date_to=previous_end,
                             store_id=store_id, category=category, sku=sku)

    def totals(frame: pd.DataFrame) -> dict:
        units = int(frame["units_sold"].sum())
        net_sales = float(frame["net_sales"].sum())
        return {"units_sold": units, "net_sales": round(net_sales, 2),
                "average_net_price": round(net_sales / units, 2) if units else None}

    current_totals = totals(current)
    previous_totals = totals(previous)

    def change(current_value: float, previous_value: float) -> float | None:
        return round((current_value / previous_value - 1) * 100, 1) if previous_value else None

    recent_start = max(start, end - pd.Timedelta(days=27))
    recent = current[current["date"] >= recent_start]
    recent_days = max(1, (end - recent_start).days + 1)
    projected_units = float(recent["units_sold"].sum() / recent_days * forecast_days)
    projected_net_sales = float(recent["net_sales"].sum() / recent_days * forecast_days)
    recent_by_product = recent.groupby("sku").agg(
        recent_units=("units_sold", "sum"), recent_net_sales=("net_sales", "sum")
    )

    grouped = current.groupby(["sku", "product_name", "category"], dropna=False).agg(
        units_sold=("units_sold", "sum"), net_sales=("net_sales", "sum")
    ).reset_index()
    grouped["average_net_price"] = np.where(
        grouped["units_sold"] > 0, grouped["net_sales"] / grouped["units_sold"], np.nan
    )
    grouped = grouped.join(recent_by_product, on="sku")
    grouped[["recent_units", "recent_net_sales"]] = grouped[["recent_units", "recent_net_sales"]].fillna(0)
    grouped["projected_units"] = (grouped["recent_units"] / recent_days * forecast_days).round(1)
    grouped["projected_net_sales"] = (grouped["recent_net_sales"] / recent_days * forecast_days).round(2)

    valid_prices = ds.prices[
        (ds.prices["effective_from"].isna() | (ds.prices["effective_from"] <= end))
        & (ds.prices["effective_to"].isna() | (ds.prices["effective_to"] >= end))
    ]
    if store_id:
        store_mask = _id_mask(valid_prices["store_id"], store_id, "store")
        if store_mask is not None:
            valid_prices = valid_prices[store_mask]
    price_by_sku = valid_prices.groupby("sku")["unit_price"].mean()
    grouped["current_list_price"] = grouped["sku"].map(price_by_sku)
    sort_by = _check_choice(sort_by, ["net_sales", "units_sold"], "sort_by")
    grouped = grouped.sort_values([sort_by, "net_sales", "units_sold"], ascending=[False, False, False]).head(
        _limit(limit)
    )

    monthly = current.assign(month=current["date"].dt.to_period("M").dt.start_time).groupby("month").agg(
        units_sold=("units_sold", "sum"), net_sales=("net_sales", "sum")
    ).reset_index()

    return {
        "filters": _filters_echo(date_from=date_from, date_to=date_to, store_id=store_id, category=category, sku=sku),
        "period": {"from": _jsonable(start), "to": _jsonable(end), "days": period_days},
        "previous_period": {"from": _jsonable(previous_start), "to": _jsonable(previous_end)},
        "summary": {
            **current_totals,
            "previous_units_sold": previous_totals["units_sold"],
            "previous_net_sales": previous_totals["net_sales"],
            "units_change_pct": change(current_totals["units_sold"], previous_totals["units_sold"]),
            "net_sales_change_pct": change(current_totals["net_sales"], previous_totals["net_sales"]),
        },
        "projection": {
            "days": forecast_days,
            "through": _jsonable(end + pd.Timedelta(days=forecast_days)),
            "units_sold": round(projected_units, 1),
            "net_sales": round(projected_net_sales, 2),
            "method": f"Promedio diario de los últimos {recent_days} días seleccionados, extrapolado a {forecast_days} días.",
        },
        "monthly_sales": records(monthly),
        "top_products": records(grouped[["sku", "product_name", "category", "units_sold", "net_sales",
                                          "average_net_price", "current_list_price", "projected_units",
                                          "projected_net_sales"]].round(2)),
        "notes": [
            "El precio promedio cobrado es net_sales / units_sold; current_list_price es el promedio de precios vigentes en la fecha final (por tienda si se filtra una tienda).",
            "La proyección es una extrapolación simple, no un pronóstico estadístico ni una garantía de ventas.",
        ],
    }


# --------------------------------------------------------------------------- #
# Inventory
# --------------------------------------------------------------------------- #

INV_GROUPS = {"none": [], "store": ["store_id"], "site": ["site_id", "site_name"], "region": ["region"],
              "department": ["department"], "category": ["category"], "sku": ["sku", "product_name"],
              "month": ["month"], "store_sku": ["store_id", "sku", "product_name"]}


def inventory_health(ds: Dataset, group_by="category", date_from=None, date_to=None, store_id=None, site_id=None,
                     region=None, department=None, category=None, sku=None, sort_by="waste_cost", ascending=False,
                     limit=25) -> dict:
    g = _check_choice(group_by, INV_GROUPS, "group_by")
    i = apply_filters(inventory_frame(ds), date_from=date_from, date_to=date_to, store_id=store_id, site_id=site_id,
                      region=region, department=department, category=category, sku=sku)
    i = i.assign(stockout_day=(i["stockout_hours"] > 0).astype(int))
    keys = INV_GROUPS[g]
    spec = dict(units_sold=("units_sold", "sum"), received_qty=("received_qty", "sum"),
                expired_qty=("expired_qty", "sum"), damaged_qty=("damaged_qty", "sum"),
                waste_cost=("waste_cost", "sum"), stockout_hours=("stockout_hours", "sum"),
                stockout_days=("stockout_day", "sum"), sku_store_days=("units_sold", "size"),
                avg_closing_qty=("closing_qty", "mean"))

    def finish(df):
        df["expired_pct_of_received"] = _div(df["expired_qty"], df["received_qty"])
        df["stockout_day_rate"] = _div(df["stockout_days"], df["sku_store_days"])
        return df.round({"waste_cost": 2, "stockout_hours": 1, "avg_closing_qty": 2, "expired_pct_of_received": 4,
                         "stockout_day_rate": 4})

    if keys:
        grouped = finish(i.groupby(keys, dropna=False, observed=True).agg(**spec).reset_index())
    else:
        grouped = finish(pd.DataFrame([{k: getattr(i[c], f)() for k, (c, f) in spec.items() if f != "size"} |
                                       {"sku_store_days": len(i)}]))
    total = finish(pd.DataFrame([{k: (getattr(i[c], f)() if f != "size" else len(i)) for k, (c, f) in spec.items()}]))
    rows, n = _finish(grouped, keys, sort_by, ascending, _limit(limit), g == "month")
    return {
        "filters": _filters_echo(date_from=date_from, date_to=date_to, store_id=store_id, site_id=site_id, region=region,
                                 department=department, category=category, sku=sku),
        "group_by": g, "total_groups": n, "returned": len(rows), "truncated": n > len(rows),
        "totals": records(total)[0], "rows": records(rows),
        "notes": ["waste_cost = expired_qty * product unit_cost (rows without a cost contribute 0). "
                  "stockout_hours = hours the SKU was unavailable while the store was open (a proxy for lost sales). "
                  "Only inventory-tracked SKUs are covered."],
    }


def reorder_recommendations(ds: Dataset, store_id: str, as_of=None, lookback_days=28, safety_days=1.0,
                            category=None, limit=25) -> dict:
    if not store_id:
        raise ValueError("store_id is required")
    sid = norm_store(pd.Series([store_id])).iloc[0]
    if sid not in set(ds.stores["store_id"]):
        raise ValueError(f"Unknown store_id '{store_id}'")
    inv = inventory_frame(ds)
    inv = inv[inv["store_id"] == sid]
    if inv.empty:
        raise ValueError(f"No inventory data for {sid}")
    day = _one_date(as_of, "as_of") or inv["date"].max()
    lookback = max(1, min(int(lookback_days or 28), 180))
    snap = inv[inv["date"] == day]
    if snap.empty:
        snap = inv[inv["date"] == inv["date"].max()]
        day = snap["date"].iloc[0]
    # average daily demand: units sold in the lookback window / window length (zero days count)
    w = inv[(inv["date"] > day - pd.Timedelta(days=lookback)) & (inv["date"] <= day)]
    demand = w.groupby("sku")["units_sold"].sum().div(lookback).rename("avg_daily_units")
    stockout_days = w.assign(so=(w["stockout_hours"] > 0)).groupby("sku")["so"].sum().rename("stockout_days_in_window")
    r = snap.merge(demand, on="sku", how="left").merge(stockout_days, on="sku", how="left")
    r = r.merge(ds.suppliers[["supplier_id", "standard_lead_time_days", "delivery_days", "contract_fill_rate"]],
                on="supplier_id", how="left")
    if category:
        r = r[_text_mask(r["category"], category).fillna(False).values]
    r["lead_time_days"] = r["standard_lead_time_days"].fillna(2)
    horizon = r["lead_time_days"] + 1 + float(safety_days)
    # perishables: never plan more cover than the shelf life allows
    horizon = np.where(r["shelf_life_days"].notna(), np.minimum(horizon, r["shelf_life_days"].clip(lower=1)), horizon)
    r["cover_days_target"] = horizon
    r["stock_position"] = r["closing_qty"] + r["on_order_qty"]
    r["days_of_cover"] = _div(r["stock_position"], r["avg_daily_units"])
    # inflate by the supplier's contract fill rate: short shipments are expected
    need = r["avg_daily_units"] * r["cover_days_target"] - r["stock_position"]
    cs = r["case_size"].fillna(1).clip(lower=1)
    r["suggested_order_qty"] = np.where(need > 0, np.ceil(need / r["contract_fill_rate"].fillna(1) / cs) * cs, 0).astype(int)
    r["est_order_cost"] = r["suggested_order_qty"] * r["unit_cost"]
    r = r[r["avg_daily_units"] > 0]
    r = r.sort_values(["suggested_order_qty", "days_of_cover"], ascending=[False, True])
    out = r.head(_limit(limit))[["sku", "product_name", "category", "closing_qty", "on_order_qty", "avg_daily_units",
                                 "days_of_cover", "lead_time_days", "cover_days_target", "case_size",
                                 "suggested_order_qty", "est_order_cost", "stockout_days_in_window", "supplier_id",
                                 "delivery_days"]].round(2)
    return {"store_id": sid, "as_of": _jsonable(day), "lookback_days": lookback,
            "skus_needing_order": int((r["suggested_order_qty"] > 0).sum()), "returned": len(out),
            "recommendations": records(out),
            "method": "target = avg_daily_units * min(lead_time + 1 review day + safety_days, shelf_life); "
                      "order = target - (closing + on_order), grossed up by supplier contract_fill_rate and rounded "
                      "up to case_size. Sorted by largest suggested order first. A heuristic, not a forecast."}


# --------------------------------------------------------------------------- #
# Suppliers
# --------------------------------------------------------------------------- #


def supplier_performance(ds: Dataset, supplier_id=None, store_id=None, date_from=None, date_to=None, by_month=False) -> dict:
    po = apply_filters(ds.purchase_orders, date_from=date_from, date_to=date_to, date_col="order_date",
                       store_id=store_id)
    sid = _split(supplier_id)
    if sid:
        po = po[po["supplier_id"].str.upper().isin([x.upper() for x in sid])]
    done = po[po["received_date"].notna() & po["qty_received"].notna()].copy()
    done["late_days"] = (done["received_date"] - done["expected_date"]).dt.days
    done["on_time"] = (done["late_days"] <= 0).where(done["late_days"].notna())
    keys = ["supplier_id"] + (["month"] if by_month else [])
    if by_month:
        done["month"] = done["order_date"].dt.strftime("%Y-%m")
        po = po.assign(month=po["order_date"].dt.strftime("%Y-%m"))
    g = done.groupby(keys).agg(delivered_lines=("po_id", "size"), qty_ordered=("qty_ordered", "sum"),
                               qty_received=("qty_received", "sum"), on_time_rate=("on_time", "mean"),
                               avg_days_late=("late_days", "mean"), max_days_late=("late_days", "max")).reset_index()
    g["fill_rate"] = _div(g["qty_received"], g["qty_ordered"])
    short = po[po["status"] == "Short-shipped"].groupby(keys).size().rename("short_shipped_lines")
    transit = po[po["status"] == "In transit"].groupby(keys).size().rename("in_transit_lines")
    g = g.merge(short.reset_index(), on=keys, how="left").merge(transit.reset_index(), on=keys, how="left").fillna(
        {"short_shipped_lines": 0, "in_transit_lines": 0})
    g = g.merge(ds.suppliers[["supplier_id", "supplier_name", "standard_lead_time_days", "contract_fill_rate",
                              "contract_on_time_rate", "delivery_days"]], on="supplier_id", how="left")
    g["fill_rate_vs_contract"] = g["fill_rate"] - g["contract_fill_rate"]
    g["on_time_vs_contract"] = g["on_time_rate"] - g["contract_on_time_rate"]
    g = g.round(4).sort_values(keys)
    unknown = sorted(set(po["supplier_id"]) - set(ds.suppliers["supplier_id"]))
    return {"filters": _filters_echo(supplier_id=supplier_id, store_id=store_id, date_from=date_from, date_to=date_to),
            "rows": records(g),
            "notes": ["fill_rate = qty_received / qty_ordered over delivered lines (in-transit lines excluded). "
                      "on_time = received_date <= expected_date. Positive *_vs_contract means better than contract.",
                      *([f"Supplier IDs not in suppliers.csv appear in the POs: {unknown} (no contract data)."]
                        if unknown else [])]}


# --------------------------------------------------------------------------- #
# Store KPIs
# --------------------------------------------------------------------------- #

KPI_GROUPS = {"none": [], "store": ["store_id", "store_name"], "site": ["site_id", "site_name"], "region": ["region"],
              "format": ["format"], "month": ["month"], "day_of_week": ["day_of_week"]}


def store_kpis(ds: Dataset, group_by="store", date_from=None, date_to=None, store_id=None, site_id=None, region=None,
               sort_by="net_sales", ascending=False, limit=25) -> dict:
    g = _check_choice(group_by, KPI_GROUPS, "group_by")

    def build():
        k = ds.store_daily_kpis.merge(ds.stores[["store_id", "store_name", "site_id", "site_name", "region", "format"]],
                                      on="store_id", how="left")
        k["month"] = k["date"].dt.strftime("%Y-%m")
        k["day_of_week"] = k["date"].dt.strftime("%a")
        return k

    k = apply_filters(_frame_cache(ds, "kpi", build), date_from=date_from, date_to=date_to, store_id=store_id,
                      site_id=site_id, region=region)
    k = k.assign(tx_with_footfall=k["transactions"].where(k["footfall"].notna()),
                 sales_with_staff=k["net_sales"].where(k["staff_hours"].notna()))
    keys = KPI_GROUPS[g]

    if keys:
        a = k.groupby(keys, dropna=False, observed=True).agg(
            store_days=("date", "size"), footfall=("footfall", "sum"), transactions=("transactions", "sum"),
            units=("units_sold", "sum"), net_sales=("net_sales", "sum"), staff_hours=("staff_hours", "sum"),
            open_hours=("open_hours", "sum"), tx_with_footfall=("tx_with_footfall", "sum"),
            sales_with_staff=("sales_with_staff", "sum")).reset_index()
    else:
        a = pd.DataFrame([{"store_days": len(k), "footfall": k["footfall"].sum(), "transactions": k["transactions"].sum(),
                           "units": k["units_sold"].sum(), "net_sales": k["net_sales"].sum(),
                           "staff_hours": k["staff_hours"].sum(), "open_hours": k["open_hours"].sum(),
                           "tx_with_footfall": k["tx_with_footfall"].sum(), "sales_with_staff": k["sales_with_staff"].sum()}])
    # ratios only use rows where both numerator and denominator are known
    a["conversion_rate"] = _div(a["tx_with_footfall"], a["footfall"])
    a["avg_ticket"] = _div(a["net_sales"], a["transactions"])
    a["units_per_transaction"] = _div(a["units"], a["transactions"])
    a["sales_per_staff_hour"] = _div(a["sales_with_staff"], a["staff_hours"])
    a["sales_per_open_hour"] = _div(a["net_sales"], a["open_hours"])
    a = a.drop(columns=["tx_with_footfall", "sales_with_staff"]).round(
        {"net_sales": 2, "conversion_rate": 4, "avg_ticket": 2, "units_per_transaction": 2, "sales_per_staff_hour": 2,
         "sales_per_open_hour": 2, "staff_hours": 1})
    rows, n = _finish(a, keys, sort_by, ascending, _limit(limit), g == "month")
    return {"filters": _filters_echo(date_from=date_from, date_to=date_to, store_id=store_id, site_id=site_id, region=region),
            "group_by": g, "total_groups": n, "returned": len(rows), "truncated": n > len(rows), "rows": records(rows),
            "notes": ["conversion_rate = transactions / footfall (days with missing footfall excluded). "
                      "net_sales here come from store_daily_kpis and can differ from sales_daily, which is SKU-level "
                      "and was cleaned more aggressively. Implausible staff_hours were nulled."]}


# --------------------------------------------------------------------------- #
# Promotions
# --------------------------------------------------------------------------- #


def promotion_performance(ds: Dataset, promo_id: str | None = None) -> dict:
    s = sales_frame(ds)
    promos = ds.promotions
    if promo_id:
        promos = promos[promos["promo_id"].str.upper().isin([x.upper() for x in _split(promo_id)])]
        if promos.empty:
            raise ValueError(f"Unknown promo_id '{promo_id}'. Valid: {sorted(ds.promotions['promo_id'])}")
    rows = []
    for p in promos.itertuples():
        scope = s
        scope = scope[_text_mask(scope["department"], p.department).fillna(False).values] if isinstance(p.department, str) else scope
        if isinstance(p.category, str):
            scope = scope[_text_mask(scope["category"], p.category).fillna(False).values]
        if p.region_scope and p.region_scope.upper() != "ALL":
            scope = scope[_text_mask(scope["region"], p.region_scope).fillna(False).values]
        days = (p.end_date - p.start_date).days + 1
        during = scope[(scope["date"] >= p.start_date) & (scope["date"] <= p.end_date)]
        base_end = p.start_date - pd.Timedelta(days=1)
        base = scope[(scope["date"] >= base_end - pd.Timedelta(days=days - 1)) & (scope["date"] <= base_end)]
        d_units, b_units = during["units_sold"].sum() / days, base["units_sold"].sum() / days
        d_net, b_net = during["net_sales"].sum() / days, base["net_sales"].sum() / days
        tagged = during[during["promo_id"] == p.promo_id]
        # the baseline window can fall before the first day of data (e.g. a promo starting 2025-10-01)
        has_base = base_end - pd.Timedelta(days=days - 1) >= s["date"].min()
        rows.append({
            "promo_id": p.promo_id, "promo_name": p.promo_name, "promo_type": p.promo_type,
            "start_date": _jsonable(p.start_date), "end_date": _jsonable(p.end_date), "days": days,
            "scope": {"department": p.department, "category": None if pd.isna(p.category) else p.category,
                      "region": p.region_scope},
            "discount_pct": p.discount_pct, "expected_uplift_pct": p.expected_uplift_pct,
            "avg_daily_units_during": round(d_units, 1), "avg_daily_units_before": round(b_units, 1) if has_base else None,
            "units_lift_pct": round((d_units / b_units - 1) * 100, 1) if has_base and b_units else None,
            "avg_daily_net_sales_during": round(d_net, 2), "avg_daily_net_sales_before": round(b_net, 2) if has_base else None,
            "net_sales_lift_pct": round((d_net / b_net - 1) * 100, 1) if has_base and b_net else None,
            "baseline_note": None if has_base else "No data before this promo window (it starts at the beginning of the dataset); lift not computed.",
            "promo_tagged_units": int(tagged["units_sold"].sum()), "promo_tagged_net_sales": round(tagged["net_sales"].sum(), 2),
            "promo_discount_given": round(tagged["discount_amount"].sum(), 2),
        })
    return {"promotions": rows,
            "notes": ["Lift compares avg daily units/net sales over the promo window with an equally long window "
                      "immediately before it, for the same department/category/region scope. It does not control for "
                      "seasonality, holidays or weather, so treat it as indicative, not causal."]}


# --------------------------------------------------------------------------- #
# Weather
# --------------------------------------------------------------------------- #

TEMP_BUCKETS = [(-np.inf, 5, "1_freezing(<5C)"), (5, 15, "2_cold(5-15C)"), (15, 25, "3_mild(15-25C)"),
                (25, 32, "4_warm(25-32C)"), (32, np.inf, "5_hot(>=32C)")]


def weather_impact(ds: Dataset, by="temperature", department=None, category=None, sku=None, region=None, site_id=None,
                   date_from=None, date_to=None) -> dict:
    b = _check_choice(by, ["temperature", "precipitation"], "by")
    s = apply_filters(sales_frame(ds), date_from=date_from, date_to=date_to, site_id=site_id, region=region,
                      department=department, category=category, sku=sku)
    daily = s.groupby(["store_id", "site_id", "date"], observed=True).agg(units=("units_sold", "sum"),
                                                                           net_sales=("net_sales", "sum")).reset_index()
    w = ds.weather_daily
    d = daily.merge(w, on=["site_id", "date"], how="left")
    if b == "temperature":
        d = d[d["temp_max_c"].notna()]
        d["bucket"] = pd.cut(d["temp_max_c"], bins=[x[0] for x in TEMP_BUCKETS] + [np.inf],
                             labels=[x[2] for x in TEMP_BUCKETS], right=False)
    else:
        d = d[d["precip_mm"].notna()]
        d["bucket"] = pd.cut(d["precip_mm"], bins=[-0.01, 0.0, 2.0, 10.0, np.inf],
                             labels=["1_dry(0mm)", "2_light(0-2mm)", "3_moderate(2-10mm)", "4_heavy(>10mm)"])
    g = d.groupby("bucket", observed=True).agg(store_days=("units", "size"), avg_units_per_store_day=("units", "mean"),
                                               avg_net_sales_per_store_day=("net_sales", "mean"),
                                               avg_temp_max_c=("temp_max_c", "mean")).reset_index()
    base = d["units"].mean()
    g["units_vs_overall_avg_pct"] = (g["avg_units_per_store_day"] / base - 1) * 100 if base else np.nan
    return {"by": b, "filters": _filters_echo(department=department, category=category, sku=sku, region=region,
                                              site_id=site_id, date_from=date_from, date_to=date_to),
            "overall_avg_units_per_store_day": _jsonable(base), "buckets": records(g.round(2)),
            "notes": ["Joins sales (filtered above) to weather by the store's site and date. Averages are per "
                      "store-day (store-days with sales of the filtered products). Weather is synthetic. "
                      "Tip: compare Coffee & Tea / Specialty Beverage across temperature buckets."]}
