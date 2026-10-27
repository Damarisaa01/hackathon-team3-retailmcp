const productGrid = document.querySelector("#product-grid");
const searchInput = document.querySelector("#search-input");
const categorySelect = document.querySelector("#category-select");
const sortSelect = document.querySelector("#sort-select");
const resultCount = document.querySelector("#result-count");
const connectionStatus = document.querySelector("#connection-status");
const emptyState = document.querySelector("#empty-state");
const loadMoreButton = document.querySelector("#load-more");
const comparisonView = document.querySelector("#comparison-view");
const comparisonForm = document.querySelector("#comparison-filters");
const comparisonMessage = document.querySelector("#comparison-message");
const comparisonProductSearch = document.querySelector("#comparison-product-search");
const comparisonProductOptions = document.querySelector("#comparison-product-options");
const comparisonSelectedProducts = document.querySelector("#comparison-selected-products");
const comparisonProductSearchResults = new Map();
const compareSelection = new Map();
const selectedComparisonProducts = new Map();
const cart = new Map(JSON.parse(localStorage.getItem("glazed-cart") || "[]"));
let currentProducts = [];
let catalogSize = null;
let matchingProducts = 0;
let currentOffset = 0;
let productRequestId = 0;
let toastTimer;
let comparisonSearchTimer;
let comparisonSearchRequestId = 0;
const pageSize = 60;

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
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(value) || 0);
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
  connectionStatus.textContent = connected ? "MCP connected" : "MCP disconnected";
}

async function requestJson(url) {
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Unable to load the catalog.");
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
      <span class="category-label">${escapeHtml(product.category || "Featured")}</span>
      <label class="compare-check"><input type="checkbox" data-compare="${escapeHtml(product.sku)}" ${selected ? "checked" : ""} /> Comparar</label>
    </div>
    <div class="product-info">
      <div class="product-meta"><span>${escapeHtml(product.department || "GLAZED CO.")}</span><span>${escapeHtml(product.sku)}</span></div>
      <h3 class="product-name">${escapeHtml(product.product_name)}</h3>
      <div class="product-bottom"><span class="product-price">${money(price(product))}</span><button class="add-button" type="button" data-add="${escapeHtml(product.sku)}">Add</button></div>
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
  resultCount.textContent = `${products.length} of ${matchingProducts} products`;
  document.querySelector("#catalog-count").textContent = catalogSize ? `${catalogSize} products in the catalog` : "Sample catalog";
  updateCompareControls();
}

async function loadProducts({ append = false } = {}) {
  const requestId = ++productRequestId;
  const offset = append ? currentOffset : 0;
  if (!append) {
    currentOffset = 0;
    matchingProducts = 0;
    currentProducts = [];
    productGrid.setAttribute("aria-busy", "true");
    productGrid.innerHTML = '<div class="loading-state">Loading catalog from MCP...</div>';
    emptyState.hidden = true;
    loadMoreButton.hidden = true;
  } else {
    loadMoreButton.disabled = true;
    loadMoreButton.textContent = "Loading...";
  }
  const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
  if (searchInput.value.trim()) params.set("query", searchInput.value.trim());
  if (categorySelect.value) params.set("category", categorySelect.value);
  try {
    const data = await requestJson(`/api/products?${params}`);
    if (requestId !== productRequestId) return;
    setConnection(true);
    const page = data.products || [];
    matchingProducts = Number(data.total_matching || 0);
    currentOffset = offset + page.length;
    renderProducts(append ? [...currentProducts, ...page] : page);
    loadMoreButton.hidden = currentOffset >= matchingProducts;
    loadMoreButton.disabled = false;
    loadMoreButton.textContent = `Load more (${matchingProducts - currentOffset} remaining)`;
  } catch (error) {
    if (requestId !== productRequestId) return;
    setConnection(false);
    if (append) {
      loadMoreButton.disabled = false;
      loadMoreButton.textContent = "Reintentar carga";
      return;
    }
    productGrid.innerHTML = `<div class="loading-state">${escapeHtml(error.message)}<br /><br />Start the MCP server as described in src/mcp/README.md.</div>`;
    productGrid.setAttribute("aria-busy", "false");
    resultCount.textContent = "";
  }
}

function percentageChange(value) {
  if (value === null || value === undefined) return "No comparable previous period";
  return `${value > 0 ? "+" : ""}${value}% vs. previous period`;
}

function renderComparisonChart(months) {
  const chart = document.querySelector("#comparison-chart");
  chart.replaceChildren();
  if (!months.length) {
    chart.textContent = "No sales in the selected period.";
    return;
  }
  const maximum = Math.max(...months.map((month) => Number(month.net_sales) || 0), 1);
  for (const month of months) {
    const column = document.createElement("div");
    column.className = "comparison-month";
    const track = document.createElement("div");
    track.className = "comparison-bar-track";
    const bar = document.createElement("div");
    bar.className = "comparison-bar";
    const amount = Number(month.net_sales) || 0;
    bar.style.height = `${Math.max(2, amount / maximum * 100)}%`;
    bar.title = money(amount);
    track.append(bar);
    const label = document.createElement("span");
    label.className = "comparison-month-label";
    label.textContent = String(month.month).slice(0, 7);
    column.append(track, label);
    chart.append(column);
  }
}

function renderSalesComparison(data) {
  const summary = data.summary;
  const projection = data.projection;
  document.querySelector("#comparison-net-sales").textContent = money(summary.net_sales);
  document.querySelector("#comparison-sales-change").textContent = percentageChange(summary.net_sales_change_pct);
  document.querySelector("#comparison-units").textContent = new Intl.NumberFormat("en-US").format(summary.units_sold);
  document.querySelector("#comparison-units-change").textContent = percentageChange(summary.units_change_pct);
  document.querySelector("#comparison-average-price").textContent = summary.average_net_price == null
    ? "—" : money(summary.average_net_price);
  document.querySelector("#comparison-projection-sales").textContent = money(projection.net_sales);
  document.querySelector("#comparison-projection-units").textContent = `${new Intl.NumberFormat("en-US").format(projection.units_sold)} units projected through ${projection.through}`;
  document.querySelector("#comparison-period").textContent = `${data.period.from} to ${data.period.to}`;
  document.querySelector("#comparison-ranking-title").textContent = selectedComparisonProducts.size
    ? "Selected product ranking" : "Period ranking";
  document.querySelector("#comparison-ranking-count").textContent = `${data.top_products.length} ${data.top_products.length === 1 ? "product" : "products"}`;
  renderComparisonChart(data.monthly_sales || []);

  const rows = (data.top_products || []).map((product) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.className = "comparison-product";
    name.innerHTML = `${escapeHtml(product.product_name)}<small>${escapeHtml(product.sku)} · ${escapeHtml(product.category || "Uncategorized")}</small>`;
    row.append(name);
    const values = [
      new Intl.NumberFormat("en-US").format(product.units_sold),
      money(product.net_sales),
      product.average_net_price == null ? "—" : money(product.average_net_price),
      product.current_list_price == null ? "—" : money(product.current_list_price),
      money(product.projected_net_sales),
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  });
  document.querySelector("#comparison-rows").replaceChildren(...rows);
}

async function loadComparison() {
  comparisonView.setAttribute("aria-busy", "true");
  comparisonMessage.hidden = false;
  comparisonMessage.textContent = "Loading sales from SQLite via MCP...";
  const params = new URLSearchParams({ limit: "20", forecast_days: "30" });
  const dateFrom = document.querySelector("#comparison-from").value;
  const dateTo = document.querySelector("#comparison-to").value;
  const storeId = document.querySelector("#comparison-store").value.trim();
  const category = document.querySelector("#comparison-category").value;
  const sortBy = document.querySelector("#comparison-sort").value;
  const selectedSkus = [...selectedComparisonProducts.keys()];
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (storeId) params.set("store_id", storeId);
  if (category) params.set("category", category);
  if (selectedSkus.length) params.set("sku", selectedSkus.join(","));
  params.set("sort_by", sortBy);
  try {
    const data = await requestJson(`/api/comparison?${params}`);
    setConnection(true);
    renderSalesComparison(data);
    comparisonMessage.hidden = true;
  } catch (error) {
    setConnection(false);
    comparisonMessage.textContent = error.message;
  } finally {
    comparisonView.setAttribute("aria-busy", "false");
  }
}

function renderSelectedComparisonProducts() {
  comparisonSelectedProducts.replaceChildren();
  for (const product of selectedComparisonProducts.values()) {
    const chip = document.createElement("span");
    chip.className = "comparison-product-chip";
    const name = document.createElement("span");
    name.textContent = `${product.product_name} · ${product.sku}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.dataset.removeComparisonProduct = product.sku;
    remove.setAttribute("aria-label", `Remove ${product.product_name}`);
    remove.textContent = "×";
    chip.append(name, remove);
    comparisonSelectedProducts.append(chip);
  }
  comparisonProductSearch.disabled = selectedComparisonProducts.size >= 3;
  comparisonProductSearch.placeholder = comparisonProductSearch.disabled
    ? "Maximum of 3 products" : "Search by name or SKU";
}

function renderComparisonProductOptions(products, message = "") {
  comparisonProductOptions.replaceChildren();
  comparisonProductSearchResults.clear();
  const available = products.filter((product) => !selectedComparisonProducts.has(product.sku));
  if (!available.length) {
    if (message) {
      const empty = document.createElement("div");
      empty.className = "comparison-product-option";
      empty.textContent = message;
      comparisonProductOptions.append(empty);
    }
  } else {
    for (const product of available) {
      comparisonProductSearchResults.set(product.sku, product);
      const option = document.createElement("button");
      option.type = "button";
      option.className = "comparison-product-option";
      option.dataset.selectComparisonProduct = product.sku;
      option.setAttribute("role", "option");
      const name = document.createElement("span");
      name.textContent = product.product_name;
      const details = document.createElement("small");
      details.textContent = `${product.sku} · ${product.category || "Uncategorized"}`;
      option.append(name, details);
      comparisonProductOptions.append(option);
    }
  }
  comparisonProductOptions.hidden = false;
  comparisonProductSearch.setAttribute("aria-expanded", "true");
}

async function searchComparisonProducts() {
  const query = comparisonProductSearch.value.trim();
  const requestId = ++comparisonSearchRequestId;
  if (query.length < 2 || selectedComparisonProducts.size >= 3) {
    comparisonProductOptions.hidden = true;
    comparisonProductSearch.setAttribute("aria-expanded", "false");
    return;
  }
  const params = new URLSearchParams({ query, limit: "8", status: "all" });
  try {
    const data = await requestJson(`/api/products?${params}`);
    if (requestId !== comparisonSearchRequestId) return;
    renderComparisonProductOptions(data.products || [], "No matching products found.");
  } catch {
    if (requestId !== comparisonSearchRequestId) return;
    renderComparisonProductOptions([], "Could not search products.");
  }
}

function addComparisonProduct(sku) {
  if (selectedComparisonProducts.size >= 3 || selectedComparisonProducts.has(sku)) return;
  const product = comparisonProductSearchResults.get(sku);
  if (!product) return;
  selectedComparisonProducts.set(sku, product);
  comparisonProductSearch.value = "";
  comparisonProductOptions.hidden = true;
  comparisonProductSearch.setAttribute("aria-expanded", "false");
  renderSelectedComparisonProducts();
  loadComparison();
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
    cartItems.innerHTML = '<p class="cart-empty">Your bag is empty.</p>';
  }
  let total = 0;
  for (const [sku, item] of cart) {
    total += item.productPrice * item.quantity;
    const line = document.createElement("div");
    line.className = "cart-line";
    line.innerHTML = `<div><div class="cart-line-name">${escapeHtml(item.name)}</div><div class="cart-line-sub">${money(item.productPrice)} · ${escapeHtml(sku)}</div></div><strong class="cart-line-total">${money(item.productPrice * item.quantity)}</strong><div class="quantity-control"><button type="button" data-quantity="${escapeHtml(sku)}" data-change="-1" aria-label="Remove one">−</button><span>${item.quantity}</span><button type="button" data-quantity="${escapeHtml(sku)}" data-change="1" aria-label="Add one">+</button></div>`;
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
  showToast(`${product.product_name} added to your bag`);
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
    ["Product", ...selected.map((item) => item.product_name)],
    ["Base price", ...selected.map((item) => money(price(item)))],
    ["Category", ...selected.map((item) => item.category || "—")],
    ["Department", ...selected.map((item) => item.department || "—")],
    ["Status", ...selected.map((item) => item.status || "—")],
    ["Inventory", ...selected.map((item) => item.inventory_tracked ? "Inventory tracked" : "Not applicable")],
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
  totalRow.innerHTML = `<span>Estimated total</span><strong>${money(total)}</strong>`;
  summary.append(totalRow);
  document.querySelector("#quote-dialog").showModal();
}

let searchTimer;
document.querySelectorAll(".top-nav [data-view]").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    const view = link.dataset.view;
    document.querySelector("#catalog-view").hidden = view !== "catalog";
    comparisonView.hidden = view !== "comparison";
    document.querySelectorAll(".top-nav [data-view]").forEach((item) => item.classList.toggle("active", item === link));
    history.replaceState(null, "", link.getAttribute("href"));
    if (view === "comparison") loadComparison();
  });
});
comparisonForm.addEventListener("submit", (event) => {
  event.preventDefault();
  loadComparison();
});
comparisonProductSearch.addEventListener("input", () => {
  clearTimeout(comparisonSearchTimer);
  comparisonSearchTimer = setTimeout(searchComparisonProducts, 220);
});
comparisonProductOptions.addEventListener("click", (event) => {
  const option = event.target.closest("[data-select-comparison-product]");
  if (option) addComparisonProduct(option.dataset.selectComparisonProduct);
});
comparisonSelectedProducts.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-comparison-product]");
  if (!button) return;
  selectedComparisonProducts.delete(button.dataset.removeComparisonProduct);
  renderSelectedComparisonProducts();
  loadComparison();
});
document.addEventListener("click", (event) => {
  if (comparisonProductSearch.contains(event.target) || comparisonProductOptions.contains(event.target)) return;
  comparisonProductOptions.hidden = true;
  comparisonProductSearch.setAttribute("aria-expanded", "false");
});
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadProducts, 280);
});
categorySelect.addEventListener("change", loadProducts);
sortSelect.addEventListener("change", () => renderProducts(currentProducts));
loadMoreButton.addEventListener("click", () => loadProducts({ append: true }));
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
    showToast("You can compare up to 3 products");
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
  const lines = ["Glazed Co. - Demo quote", "", ...[...cart.entries()].map(([sku, item]) => `${item.quantity} x ${item.name} (${sku}) - ${money(item.productPrice * item.quantity)}`)];
  lines.push("", `Estimated total: ${document.querySelector("#cart-total").textContent}`, "", "Demo estimate; not a commercial quote.");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" }));
  link.download = "glazed-demo-quote.txt";
  link.click();
  URL.revokeObjectURL(link.href);
  showToast("Demo summary downloaded");
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
    response.textContent = `Found ${matchingProducts} matches by name or SKU. ${first.product_name} appears first; base price ${money(price(first))}, category ${first.category || "uncategorized"}.`;
  } else {
    response.textContent = "No text matches found. Try a category or a shorter search term.";
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
      const comparisonOption = option.cloneNode(true);
      document.querySelector("#comparison-category").append(comparisonOption);
    }
    const latestDate = overview.period?.to;
    if (latestDate) {
      const startDate = new Date(`${latestDate}T00:00:00Z`);
      startDate.setUTCDate(startDate.getUTCDate() - 29);
      document.querySelector("#comparison-to").value = latestDate;
      document.querySelector("#comparison-from").value = startDate.toISOString().slice(0, 10);
    }
    await loadProducts();
  } catch (error) {
    setConnection(false);
    productGrid.innerHTML = `<div class="loading-state">${escapeHtml(error.message)}<br /><br />Levanta el servidor MCP siguiendo el README de src/mcp.</div>`;
    productGrid.setAttribute("aria-busy", "false");
  }
}

initialize();
