"""Small web API and storefront for the Glazed retail MCP server."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


MCP_URL = os.environ.get("GLAZED_MCP_URL", "http://127.0.0.1:9901/mcp")
STATIC_DIR = Path(__file__).parent / "web_static"

app = FastAPI(title="Glazed Co. Shop", docs_url=None, redoc_url=None)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


def _decode_tool_result(result: Any) -> Any:
    if getattr(result, "isError", False):
        message = "; ".join(getattr(item, "text", "") for item in result.content)
        raise HTTPException(status_code=502, detail=message or "The retail MCP tool failed.")

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict) and isinstance(structured.get("result"), str):
            try:
                return json.loads(structured["result"])
            except json.JSONDecodeError:
                pass
        return structured

    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
    raise HTTPException(status_code=502, detail="The retail MCP returned no readable result.")


async def _call_retail_tool(name: str, arguments: dict[str, Any]) -> Any:
    try:
        async with streamablehttp_client(MCP_URL) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments=arguments)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Retail MCP unavailable at {MCP_URL}. Start it using the src/mcp README instructions.",
        ) from exc
    return _decode_tool_result(result)


@app.get("/")
async def storefront() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/products")
async def search_products(
    query: str | None = Query(default=None, max_length=120),
    category: str | None = Query(default=None, max_length=80),
    status: str = Query(default="Active", max_length=30),
    limit: int = Query(default=60, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Any:
    arguments: dict[str, Any] = {"limit": limit, "offset": offset}
    if status.lower() != "all":
        arguments["status"] = status
    if query:
        arguments["query"] = query
    if category:
        arguments["category"] = category
    return await _call_retail_tool("retail__search_products", arguments)


@app.get("/api/product/{sku}")
async def get_product(sku: str) -> Any:
    if len(sku) > 40:
        raise HTTPException(status_code=400, detail="SKU is too long.")
    return await _call_retail_tool("retail__get_product", {"sku_or_barcode": sku})


@app.get("/api/catalog")
async def get_catalog_overview() -> Any:
    return await _call_retail_tool("retail__get_dataset_overview", {})


@app.get("/api/comparison")
async def get_sales_comparison(
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
    store_id: str | None = Query(default=None, max_length=12),
    category: str | None = Query(default=None, max_length=80),
    sku: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=20, ge=1, le=100),
    forecast_days: int = Query(default=30, ge=1, le=90),
    sort_by: str = Query(default="net_sales", pattern="^(net_sales|units_sold)$"),
) -> Any:
    arguments: dict[str, Any] = {"limit": limit, "forecast_days": forecast_days, "sort_by": sort_by}
    if date_from:
        arguments["date_from"] = date_from
    if date_to:
        arguments["date_to"] = date_to
    if store_id:
        arguments["store_id"] = store_id
    if category:
        arguments["category"] = category
    if sku:
        arguments["sku"] = sku
    return await _call_retail_tool("retail__sales_comparison", arguments)


