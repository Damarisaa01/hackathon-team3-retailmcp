# Glazed Co. retail MCP server (NVIDIA NeMo Agent Toolkit)

Exposes the dataset in `data/` as MCP tools. Built as a NAT *function group* (`glazed_retail`) and
served with `nat mcp serve`.

## Run

```bash
uv sync
uv run nat mcp serve --config_file src/mcp/config.yml --host 0.0.0.0 --port 9901
# MCP endpoint (streamable-http): http://localhost:9901/mcp
```

Smoke test with NAT's own client:

```bash
uv run nat mcp client tool list --url http://localhost:9901/mcp
uv run nat mcp client tool call retail__sales_summary --url http://localhost:9901/mcp \
    --json-args '{"group_by": "category", "limit": 5}'
```

Point an agent at it with `mcp_client.yaml` (set the tool names under `include` to the ones below).
Data location: `<repo>/data`, overridable with `$GLAZED_DATA_DIR` or `data_dir:` in `config.yml`.

## Tools (exposed as `retail__<name>`)

| Tool | Purpose |
| --- | --- |
| `get_dataset_overview` | Business context, date range, tables/columns, valid departments/categories/regions |
| `get_data_quality_report` | What was wrong in the raw CSVs and how it was handled |
| `list_stores` | Stores with venue, region, format, hours, closures |
| `search_products` / `get_product` | Product master search; full detail by SKU or barcode (supplier, prices, sales) |
| `get_calendar` | Holidays, weekends, school breaks |
| `sales_summary` | Units / gross / discount / net by time, store, site, region, product dimension, weekday, holiday, promo |
| `inventory_health` | Waste (expired/damaged, cost) and stockouts by any dimension |
| `reorder_recommendations` | Suggested order quantities per store (demand, lead time, case size, shelf life) |
| `supplier_performance` | Fill rate / on-time vs contract from purchase orders |
| `store_kpis` | Footfall, conversion, avg ticket, sales per staff hour |
| `promotion_performance` | Sales lift vs. the period before, vs. expected uplift |
| `weather_impact` | Sales per store-day by temperature or rain bucket |

## Data cleaning

The raw CSVs are never modified. `glazed_retail_mcp/data.py` cleans an in-memory copy once at startup
(~6 s) and records every action; `get_data_quality_report` returns that log. Main fixes: SKU/store ID
formats (`sku100237`, `S0019`, `s018 `), three date formats, duplicate rows, `-999` weather sentinels,
`?`/`TBD` prices, USD spelling variants, negative quantities, conflicting duplicate SKUs/POs.
Rows with negative or missing units are dropped, not repaired. Review the rules there if you disagree.

## Layout

`src/mcp` is its own installable project (`glazed-retail-mcp`) so that nothing here is importable as
`mcp`, which would shadow the MCP SDK that NAT depends on. NAT finds the plugin through the
`nat.components` entry point in `pyproject.toml`.

- `glazed_retail_mcp/data.py` load + clean
- `glazed_retail_mcp/analytics.py` the tool logic (plain functions, unit-testable)
- `glazed_retail_mcp/register.py` NAT registration and tool schemas
