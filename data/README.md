# Dataset del Hackathon – Cadena de cafeterías y donas "Glazed Co." (empresa ficticia)

## ⚠️ Aviso legal y descargo de responsabilidad

- Este dataset es **100 % sintético y ficticio**. Fue generado para uso exclusivo en este hackathon y **no contiene información real, confidencial ni sensible** de ninguna empresa, persona, cliente, proveedor o ubicación.
- Cualquier parecido con empresas, marcas, lugares, productos o cifras reales es **pura coincidencia**. Los nombres de empresa, sitios, tiendas y proveedores son inventados.
- **No debe utilizarse fuera del hackathon**: no puede usarse con fines comerciales, de entrenamiento de modelos para producción, publicación de resultados como si fueran datos reales, ni redistribuirse como si representara a una empresa real.
- Los datos se entregan "tal cual", sin garantía alguna de exactitud, integridad o idoneidad para ningún propósito. Las conclusiones obtenidas **no representan el desempeño de ninguna compañía real**.
- Los datos contienen **errores de calidad intencionales** (ver sección "Calidad de datos").

## Contexto del negocio

"Glazed Co." es una **compañía ficticia de alimentos y bebidas de Estados Unidos**, especializada en donas, panadería, sándwiches de desayuno, café y bebidas. Opera puntos de venta en **aeropuertos, estaciones de tren, terminales de autobuses y un resort** (zonas de alto tránsito de viajeros). Su modelo se inspira en el de una cadena estadounidense de cafeterías, pero **ningún dato, nombre ni código corresponde a una empresa real**.

Los viajeros compran de forma rápida y con picos fuertes (fines de semana, feriados, temporada de viajes). Esto hace difícil planear el inventario: se vende producto perecedero con vida útil corta (1–3 días), hay proveedores que entregan incompleto o tarde, y se generan al mismo tiempo **quiebres de stock (ventas perdidas)** y **mermas (desperdicio)**.

- **Periodo:** 2025-10-01 → 2026-09-30 (365 días)
- **Alcance:** 20 sitios (venues) y 38 tiendas (algunas abrieron, cerraron definitivamente o estuvieron cerradas temporalmente durante el año)
- **Moneda:** USD
- **Formato:** archivos CSV, codificación UTF-8, separador coma

## Calidad de datos

> Este es un extracto operativo, no un data warehouse pulido. Como en el mundo real, **los datos tienen inconsistencias**. Evalúa la calidad antes de confiar en una cifra y documenta cómo trataste cada problema que encuentres. Parte del reto es identificarlos.

## Tablas

| Archivo                | Granularidad                                                                      | Llaves principales                     |
| ---------------------- | --------------------------------------------------------------------------------- | -------------------------------------- |
| `sites.csv`            | un registro por sitio/venue (aeropuerto, estación, etc.)                          | `site_id`                              |
| `stores.csv`           | un registro por tienda                                                            | `store_id`, `site_id`                  |
| `store_closures.csv`   | ventanas de cierre de tienda                                                      | `store_id`, `start_date`, `end_date`   |
| `products.csv`         | maestro de productos                                                              | `sku`                                  |
| `product_barcodes.csv` | códigos de barras por SKU                                                         | `barcode`, `sku`                       |
| `suppliers.csv`        | maestro de proveedores y condiciones contractuales                                | `supplier_id`                          |
| `prices.csv`           | lista de precios por tienda con vigencia                                          | `store_id`, `sku`, `effective_from/to` |
| `sales_daily.csv`      | unidades e ingresos por tienda/SKU/día                                            | `date`, `store_id`, `sku`              |
| `inventory_daily.csv`  | movimiento de inventario por tienda/SKU/día (solo SKUs con control de inventario) | `date`, `store_id`, `sku`              |
| `purchase_orders.csv`  | líneas de órdenes de reabastecimiento                                             | `po_id`, `store_id`, `sku`             |
| `store_daily_kpis.csv` | tránsito, transacciones, ventas y horas de personal por tienda/día                | `store_id`, `date`                     |
| `promotions.csv`       | calendario de promociones                                                         | `promo_id`                             |
| `calendar.csv`         | dimensión de fechas y feriados                                                    | `date`                                 |
| `weather_daily.csv`    | temperatura y precipitación diaria por sitio                                      | `site_id`, `date`                      |

### Relaciones

`sales_daily`, `inventory_daily`, `purchase_orders`, `prices` → `products` (por `sku`) y `stores` (por `store_id`) → `sites` (por `site_id`). `products.supplier_id` y `purchase_orders.supplier_id` → `suppliers`. `sales_daily.promo_id` → `promotions`. Las fechas se unen con `calendar`. `weather_daily` se une con `sites` (por `site_id`) y con las ventas a través de `stores.site_id` y la fecha.

## Diccionario de datos (columnas clave)

**products**

- `department`, `category`, `subcategory`: jerarquía del producto.
- `unit_cost`: costo unitario de compra; `list_price_base`: precio base de referencia.
- `case_size`: los pedidos se hacen en múltiplos de esta cantidad.
- `shelf_life_days`: días que el producto puede venderse tras recibirse; lo que excede se da de baja como merma.
- `is_perishable`, `storage_type` (Ambient / Chilled / Frozen), `is_seasonal`, `status`, `launch_date`, `discontinued_date`.
- `inventory_tracked = 1`: el SKU aparece en `inventory_daily` y `purchase_orders`. Las bebidas preparadas al momento se venden pero no tienen control de inventario.

**stores / sites**

- `format`: Full Counter, Express Kiosk, Shop-in-Shop. `operator_type`: Owned F&B / Concession Retail.
- `opening_time`/`closing_time`: horario (00:00/00:00 = 24 h). `opened_date`/`closed_date`, `sqft`, `seats`.
- `traffic_tier` (sites): nivel relativo de tránsito de pasajeros.

**prices**

- `effective_to` vacío = precio vigente. Durante el año hubo un cambio de precios en parte del catálogo.

**sales_daily**

- `units_sold`, `unit_price`, `gross_sales`, `discount_amount`, `net_sales`, `promo_id`. El descuento también incluye pequeños descuentos de lealtad sin promoción.

**inventory_daily**

- `closing_qty = opening_qty + received_qty − units_sold − expired_qty − damaged_qty`.
- `stockout_hours`: horas aproximadas en que el SKU no estuvo disponible con la tienda abierta.
- `on_order_qty`: inventario en tránsito al cierre del día.

**purchase_orders**

- `status`: Delivered / Short-shipped / In transit / Cancelled. `qty_received` puede ser menor que `qty_ordered`.

**store_daily_kpis**

- `footfall` (tránsito de personas), `transactions`, `units_sold`, `net_sales`, `staff_hours`, `open_hours`.

**promotions**

- Tipo, vigencia, % de descuento, alcance (departamento/categoría/región) y uplift esperado.

**calendar**

- Día de la semana, semana ISO, mes, fin de semana, feriado (con nombre) y vacaciones escolares.

**weather_daily**

- `temp_max_c`, `temp_min_c` (°C) y `precip_mm` (milímetros) por sitio y día. Datos sintéticos; la temperatura influye en el mix de bebidas frías y calientes.

## Reglas del hackathon sobre los datos

- Usa estos datos únicamente durante el evento y para los fines del reto.
- No intentes identificar o relacionar los datos con empresas reales.
- No redistribuyas el dataset fuera del repositorio/evento.
