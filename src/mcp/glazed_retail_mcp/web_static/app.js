const productGrid = document.querySelector("#product-grid");
const searchInput = document.querySelector("#search-input");
const categorySelect = document.querySelector("#category-select");
const sortSelect = document.querySelector("#sort-select");
const resultCount = document.querySelector("#result-count");
const connectionStatus = document.querySelector("#connection-status");
const emptyState = document.querySelector("#empty-state");
const compareSelection = new Map();
const cart = new Map(JSON.parse(localStorage.getItem("glazed-cart") || "[]"));
let currentProducts = [];
let catalogSize = null;
let toastTimer;

const imageByCategory = {
  Bakery: "https://images.unsplash.com/photo-1551024506-0becd828d307?auto=format&fit=crop&w=800&q=80",
  "Coffee & Tea": "https://images.unsplash.com/photo-1461023058943-07fcbe16d735?auto=format&fit=crop&w=800&q=80",
  "Specialty Beverage": "https://images.unsplash.com/photo-1517701604599-bb29b565090c?auto=format&fit=crop&w=800&q=80",
  Sandwiches: "https://images.unsplash.com/photo-1528735602780-2552fd46c7af?auto=format&fit=crop&w=800&q=80",
  Catering: "https://images.unsplash.com/photo-1547592180-85f173990554?auto=format&fit=crop&w=800&q=80",
  "Souvenir & Merch": "https://images.unsplash.com/photo-1513364776144-60967b0f800f?auto=format&fit=crop&w=800&q=80",
};
const fallbackImage = "https://images.unsplash.com/photo-1555507036-ab1f4038808a?auto=format&fit=crop&w=800&q=80";

function money(value) {
  return new Intl.NumberFormat("es-MX", { style: "currency", currency: "USD" }).format(Number(value) || 0);
}

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2200);
}

function setConnection(connected) {
  connectionStatus.classList.toggle("connected", connected);
  connectionStatus.classList.toggle("disconnected", !connected);
  connectionStatus.textContent = connected ? "MCP conectado" : "MCP sin conexión";
}

async function requestJson(url) {
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "No fue posible consultar el catálogo.");
  return body;
}

function price(product) {
  return Number(product.list_price_base ?? product.current_price?.avg ?? 0);
}

function imageFor(product) {
  return imageByCategory[product.category] || fallbackImage;
}

function sortedProducts(products) {
  const sorted = [...products];
  if (sortSelect.value === "price-low") sorted.sort((a, b) => price(a) - price(b));
  if (sortSelect.value === "price-high") sorted.sort((a, b) => price(b) - price(a));
  if (sortSelect.value === "name") sorted.sort((a, b) => a.product_name.localeCompare(b.product_name, "es"));
  return sorted;
}

function productCard(product, index) {
  const card = document.createElement("article");
  card.className = "product-card";
  card.style.animationDelay = `${Math.min(index, 12) * 28}ms`;
  const selected = compareSelection.has(product.sku);
  card.innerHTML = `
    <div class="product-image-wrap">
      <img class="product-image" src="${imageFor(product)}" alt="Imagen ilustrativa de ${escapeHtml(product.product_name)}" loading="lazy" />
      <span class="category-label">${escapeHtml(product.category || "Selección")}</span>
      <label class="compare-check"><input type="checkbox" data-compare="${escapeHtml(product.sku)}" ${selected ? "checked" : ""} /> Comparar</label>
    </div>
    <div class="product-info">
      <div class="product-meta"><span>${escapeHtml(product.department || "GLAZED CO.")}</span><span>${escapeHtml(product.sku)}</span></div>
      <h3 class="product-name">${escapeHtml(product.product_name)}</h3>
      <div class="product-bottom"><span class="product-price">${money(price(product))}</span><button class="add-button" type="button" data-add="${escapeHtml(product.sku)}">Añadir</button></div>
    </div>`;
  return card;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function renderProducts(products) {
  currentProducts = products;
  productGrid.replaceChildren(...sortedProducts(products).map(productCard));
  productGrid.setAttribute("aria-busy", "false");
  emptyState.hidden = products.length > 0;
  resultCount.textContent = `${products.length} ${products.length === 1 ? "producto" : "productos"}`;
  document.querySelector("#catalog-count").textContent = catalogSize ? `${catalogSize} productos en el catálogo` : "Catálogo de muestra";
  updateCompareControls();
}

async function loadProducts() {
  productGrid.setAttribute("aria-busy", "true");
  productGrid.innerHTML = '<div class="loading-state">Consultando el catálogo MCP...</div>';
  emptyState.hidden = true;
  const params = new URLSearchParams({ limit: "100" });
  if (searchInput.value.trim()) params.set("query", searchInput.value.trim());
  if (categorySelect.value) params.set("category", categorySelect.value);
  try {
    const data = await requestJson(`/api/products?${params}`);
    setConnection(true);
    renderProducts(data.products || []);
  } catch (error) {
    setConnection(false);
    productGrid.innerHTML = `<div class="loading-state">${escapeHtml(error.message)}<br /><br />Inicia el servidor MCP según el README de src/mcp.</div>`;
    productGrid.setAttribute("aria-busy", "false");
    resultCount.textContent = "";
  }
}

function updateCart() {
  localStorage.setItem("glazed-cart", JSON.stringify([...cart.entries()]));
  const count = [...cart.values()].reduce((sum, item) => sum + item.quantity, 0);
  document.querySelector("#cart-count").textContent = count;
  document.querySelector("#dialog-cart-count").textContent = count ? `(${count})` : "";
  document.querySelector("#quote-open").disabled = count === 0;
  const cartItems = document.querySelector("#cart-items");
  cartItems.replaceChildren();
  if (cart.size === 0) {
    cartItems.innerHTML = '<p class="cart-empty">Tu bolsa está esperando algo rico.</p>';
  }
  let total = 0;
  for (const [sku, item] of cart) {
    total += item.productPrice * item.quantity;
    const line = document.createElement("div");
    line.className = "cart-line";
    line.innerHTML = `<div><div class="cart-line-name">${escapeHtml(item.name)}</div><div class="cart-line-sub">${money(item.productPrice)} · ${escapeHtml(sku)}</div></div><strong class="cart-line-total">${money(item.productPrice * item.quantity)}</strong><div class="quantity-control"><button type="button" data-quantity="${escapeHtml(sku)}" data-change="-1" aria-label="Quitar una unidad">−</button><span>${item.quantity}</span><button type="button" data-quantity="${escapeHtml(sku)}" data-change="1" aria-label="Añadir una unidad">+</button></div>`;
    cartItems.append(line);
  }
  document.querySelector("#cart-total").textContent = money(total);
}

function addToCart(sku) {
  const product = currentProducts.find((item) => item.sku === sku);
  if (!product) return;
  const existing = cart.get(sku);
  cart.set(sku, {
    name: product.product_name,
    productPrice: price(product),
    quantity: (existing?.quantity || 0) + 1,
  });
  updateCart();
  showToast(`${product.product_name} añadido a tu bolsa`);
}

function updateCompareControls() {
  const count = compareSelection.size;
  document.querySelector("#compare-count").textContent = count;
  document.querySelector("#compare-open").disabled = count < 2;
}

function renderComparison() {
  const selected = [...compareSelection.values()];
  const table = document.querySelector("#compare-table");
  table.style.setProperty("--compare-columns", selected.length);
  const rows = [
    ["Producto", ...selected.map((item) => item.product_name)],
    ["Precio base", ...selected.map((item) => money(price(item)))],
    ["Categoría", ...selected.map((item) => item.category || "—")],
    ["Departamento", ...selected.map((item) => item.department || "—")],
    ["Estado", ...selected.map((item) => item.status || "—")],
    ["Inventario", ...selected.map((item) => item.inventory_tracked ? "Seguimiento de inventario" : "No aplica")],
  ];
  table.replaceChildren(...rows.flatMap((row, rowIndex) => row.map((value, columnIndex) => {
    const cell = document.createElement("div");
    cell.className = `compare-cell ${columnIndex === 0 ? "label" : rowIndex === 0 ? "product-heading" : ""}`;
    cell.textContent = value;
    return cell;
  })));
}

function openQuote() {
  const rows = [...cart.entries()];
  const summary = document.querySelector("#quote-summary");
  summary.replaceChildren();
  let total = 0;
  for (const [sku, item] of rows) {
    const lineTotal = item.productPrice * item.quantity;
    total += lineTotal;
    const row = document.createElement("div");
    row.className = "quote-row";
    row.innerHTML = `<span>${item.quantity} × ${escapeHtml(item.name)} <small>(${escapeHtml(sku)})</small></span><strong>${money(lineTotal)}</strong>`;
    summary.append(row);
  }
  const totalRow = document.createElement("div");
  totalRow.className = "quote-row total";
  totalRow.innerHTML = `<span>Total estimado</span><strong>${money(total)}</strong>`;
  summary.append(totalRow);
  document.querySelector("#quote-dialog").showModal();
}

let searchTimer;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadProducts, 280);
});
categorySelect.addEventListener("change", loadProducts);
sortSelect.addEventListener("change", () => renderProducts(currentProducts));
document.querySelector("#clear-search").addEventListener("click", () => {
  searchInput.value = "";
  categorySelect.value = "";
  loadProducts();
});
productGrid.addEventListener("click", (event) => {
  const button = event.target.closest("[data-add]");
  if (button) addToCart(button.dataset.add);
});
productGrid.addEventListener("change", (event) => {
  const checkbox = event.target.closest("[data-compare]");
  if (!checkbox) return;
  const sku = checkbox.dataset.compare;
  const product = currentProducts.find((item) => item.sku === sku);
  if (checkbox.checked && compareSelection.size >= 3) {
    checkbox.checked = false;
    showToast("Puedes comparar hasta 3 productos");
    return;
  }
  if (checkbox.checked && product) compareSelection.set(sku, product);
  else compareSelection.delete(sku);
  updateCompareControls();
});
document.querySelector("#compare-open").addEventListener("click", () => {
  renderComparison();
  document.querySelector("#compare-dialog").showModal();
});
document.querySelector("#open-cart").addEventListener("click", () => document.querySelector("#cart-dialog").showModal());
document.querySelector("#cart-items").addEventListener("click", (event) => {
  const button = event.target.closest("[data-quantity]");
  if (!button) return;
  const item = cart.get(button.dataset.quantity);
  item.quantity += Number(button.dataset.change);
  if (item.quantity <= 0) cart.delete(button.dataset.quantity);
  updateCart();
});
document.querySelector("#quote-open").addEventListener("click", openQuote);
document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => document.querySelector(`#${button.dataset.close}`).close());
});
document.querySelectorAll("dialog").forEach((dialog) => {
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
});
document.querySelector("#download-quote").addEventListener("click", () => {
  const lines = ["Glazed Co. — cotización de demostración", "", ...[...cart.entries()].map(([sku, item]) => `${item.quantity} x ${item.name} (${sku}) — ${money(item.productPrice * item.quantity)}`)];
  lines.push("", `Total estimado: ${document.querySelector("#cart-total").textContent}`, "", "Estimación de demostración; no es una cotización comercial.");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" }));
  link.download = "glazed-cotizacion-demo.txt";
  link.click();
  URL.revokeObjectURL(link.href);
  showToast("Resumen de demo descargado");
});

document.querySelector("#assistant-toggle").addEventListener("click", () => {
  const panel = document.querySelector("#assistant-panel");
  panel.hidden = !panel.hidden;
  if (!panel.hidden) document.querySelector("#assistant-prompt").focus();
});
document.querySelector("#assistant-close").addEventListener("click", () => { document.querySelector("#assistant-panel").hidden = true; });
document.querySelector("#assistant-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = document.querySelector("#assistant-prompt").value.trim();
  if (!prompt) return;
  searchInput.value = prompt;
  categorySelect.value = "";
  await loadProducts();
  const response = document.querySelector("#assistant-response");
  if (currentProducts.length) {
    const first = currentProducts[0];
    response.textContent = `Encontré ${currentProducts.length} opciones por coincidencia de nombre o SKU. ${first.product_name} aparece primero; precio base ${money(price(first))}, categoría ${first.category || "sin categoría"}.`;
  } else {
    response.textContent = "No encontré coincidencias textuales. Prueba con una categoría o un término más corto.";
  }
});

async function initialize() {
  updateCart();
  try {
    const overview = await requestJson("/api/catalog");
    catalogSize = overview.counts?.skus;
    for (const category of overview.categories || []) {
      const option = document.createElement("option");
      option.value = category;
      option.textContent = category;
      categorySelect.append(option);
    }
    await loadProducts();
  } catch (error) {
    setConnection(false);
    productGrid.innerHTML = `<div class="loading-state">${escapeHtml(error.message)}<br /><br />Levanta el servidor MCP siguiendo el README de src/mcp.</div>`;
    productGrid.setAttribute("aria-busy", "false");
  }
}

initialize();
