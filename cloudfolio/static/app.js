"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const preview = document.documentElement.dataset.mode === "snapshot";
const state = {
  user: null,
  csrf: "",
  stocks: [],
  market: {},
  indicators: {},
  portfolio: null,
  selected: "RELIANCE",
  registering: false,
  editing: null,
  removing: null,
  watchlist: [],
};
const money = (n) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  }).format(n);
const pct = (n) => `${n >= 0 ? "+" : ""}${Number(n).toFixed(2)}%`;
const date = (d) =>
  new Date(d + "T00:00:00").toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
const node = (tag, text = "", cls = "") => {
  const el = document.createElement(tag);
  el.textContent = text;
  el.className = cls;
  return el;
};
const append = (parent, ...children) => {
  parent.append(...children);
  return parent;
};
let toastTimer;
function notify(message, error = false) {
  $("#toast-text").textContent = message;
  $("#toast").classList.remove("hidden");
  $("#toast").classList.toggle("error", error);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.add("hidden"), 6000);
}
async function api(path, options = {}) {
  if (preview) {
    if (options.method && options.method !== "GET")
      throw new Error(
        "This preview is read-only. Run the Flask app to save your own portfolio.",
      );
    if (path === "/api/auth/session") return { user: null, csrf_token: "" };
    if (path === "/api/meta")
      return { as_of: Object.values(state.market)[0]?.at(-1)?.date };
    if (path.startsWith("/api/stocks")) return { stocks: state.stocks };
    if (path.startsWith("/api/history/"))
      return { bars: state.market[path.split("/").at(-1)] };
    if (path.startsWith("/api/indicators/"))
      return state.indicators[path.split("/").at(-1)];
    if (path === "/api/demo/summary") return state.demo;
    throw new Error(
      "Personal workspaces are available in the full application.",
    );
  }
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": state.csrf,
      ...options.headers,
    },
    credentials: "same-origin",
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || "The request could not be completed.");
  return result;
}
function navigate(page) {
  $$(".page").forEach((p) => p.classList.toggle("hidden", p.id !== page));
  $$(".nav").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === page),
  );
  $("#breadcrumb").textContent = {
    overview: "Overview",
    analysis: "Stock analysis",
    portfolio: "My portfolio",
    cloud: "Cloud architecture",
  }[page];
  window.scrollTo({ top: 0, behavior: "instant" });
  if (page === "analysis") loadAnalysis().catch((e) => notify(e.message, true));
  if (page === "portfolio")
    loadActivity().catch((e) => notify(e.message, true));
}
function metric(title, value, note, color = "") {
  const el = node("div", "", "panel metric");
  append(
    el,
    node("span", title),
    node("strong", value, color),
    node("small", note),
  );
  return el;
}
function stats(selector) {
  const p = state.portfolio;
  $(selector).replaceChildren(
    metric(
      "Portfolio value",
      money(p.value),
      "Marked at the last sample close",
    ),
    metric(
      "Invested capital",
      money(p.invested),
      `${p.holdings.length} stock positions`,
    ),
    metric(
      "Unrealised P/L",
      money(p.pnl),
      "Price change × shares",
      p.pnl >= 0 ? "positive" : "negative",
    ),
    metric(
      "Portfolio return",
      pct(p.return_pct),
      "Relative to the recorded buy cost",
      p.pnl >= 0 ? "positive" : "negative",
    ),
  );
}
function renderPortfolio() {
  stats("#overview-stats");
  stats("#portfolio-stats");
  $$(".portfolio-badge").forEach(
    (b) =>
      (b.textContent = state.user
        ? "Personal portfolio"
        : "Illustrative portfolio"),
  );
  $("#portfolio-caption").textContent = state.user
    ? "Saved in the application database. Values exclude trading fees and realised P/L."
    : "An example portfolio to explore the numbers; not your personal holdings.";
  $("#portfolio-mode").textContent = state.user
    ? `Signed in as ${state.user.name}. Your portfolio is stored in the application database.`
    : "You are exploring an illustrative portfolio. Sign in to save your own holdings.";
  for (const selector of ["#overview-holdings", "#portfolio-holdings"]) {
    const table = $(selector);
    table.replaceChildren();
    for (const h of state.portfolio.holdings) {
      const row = node("tr");
      const company = node("td");
      append(company, node("b", h.symbol), node("small", h.name));
      row.append(company);
      [
        h.quantity,
        money(h.average_price),
        money(h.latest_price),
        money(h.value),
      ].forEach((v) => row.append(node("td", String(v))));
      row.append(
        node("td", money(h.pnl), h.pnl >= 0 ? "positive" : "negative"),
      );
      if (selector === "#overview-holdings")
        row.append(
          node("td", pct(h.return_pct), h.pnl >= 0 ? "positive" : "negative"),
        );
      else {
        const actions = node("td", "", "row-actions");
        const edit = node("button", "Edit", "small-button"),
          remove = node("button", "Remove", "text-button muted");
        edit.addEventListener("click", () => openHolding(h));
        remove.addEventListener("click", () => {
          if (!state.user) return openAuth();
          state.removing = h;
          $("#remove-copy").textContent =
            `${h.quantity} shares of ${h.symbol} will be removed from your current portfolio.`;
          $("#confirm-modal").showModal();
        });
        append(actions, edit, remove);
        row.append(actions);
      }
      table.append(row);
    }
    if (!state.portfolio.holdings.length) {
      const row = node("tr"),
        cell = node(
          "td",
          "No holdings yet. Add a stock and your average buy cost to start.",
          "table-empty",
        );
      cell.colSpan = 7;
      append(row, cell);
      table.append(row);
    }
  }
  $("#allocation").replaceChildren();
  for (const h of state.portfolio.holdings) {
    const share = state.portfolio.value
        ? (h.value / state.portfolio.value) * 100
        : 0,
      block = node("div", "", "allocation-row"),
      top = node("div");
    append(
      top,
      node("b", h.symbol),
      node("span", money(h.value)),
      node("small", `${share.toFixed(1)}%`),
    );
    const track = node("progress");
    track.value = share;
    track.max = 100;
    track.setAttribute("aria-label", `${h.symbol} allocation`);
    append(block, top, track);
    $("#allocation").append(block);
  }
  if (!state.portfolio.holdings.length)
    $("#allocation").append(
      node("p", "Your allocation appears after you add holdings."),
    );
}
function renderStocks(query = "") {
  const list = $("#stocks-list");
  list.replaceChildren();
  for (const s of state.stocks.filter((s) =>
    (s.symbol + s.name).toLowerCase().includes(query.toLowerCase()),
  )) {
    const button = node(
        "button",
        "",
        `stock-row ${state.selected === s.symbol ? "selected" : ""}`,
      ),
      icon = node("span", s.symbol.slice(0, 2), "stock-icon"),
      name = node("span", "", "stock-name"),
      price = node("span", "", "stock-price");
    append(name, node("b", s.symbol), node("small", s.sector));
    append(
      price,
      node("b", money(s.price)),
      node(
        "small",
        pct(s.change_pct),
        s.change_pct >= 0 ? "positive" : "negative",
      ),
    );
    append(button, icon, name, price);
    button.addEventListener("click", () => {
      state.selected = s.symbol;
      $("#analysis-symbol").value = s.symbol;
      renderStocks($("#stock-search").value);
      loadOverviewChart().catch((e) => notify(e.message, true));
    });
    list.append(button);
  }
  if (!list.children.length)
    list.append(
      node("p", "No matching stock in this sample dataset.", "empty-list"),
    );
}
const SVG = "http://www.w3.org/2000/svg";
function svgNode(tag, attrs, text) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  if (text !== undefined) el.textContent = text;
  return el;
}
function drawChart(selector, rows, overlays, oscillator = false) {
  const container = $(selector);
  container.replaceChildren();
  const w = Math.max(330, container.clientWidth),
    h = oscillator ? 105 : 300,
    left = 15,
    right = 65,
    bottom = 25;
  const values = oscillator
      ? rows.map((r) => r.rsi14).filter((v) => v !== null)
      : rows
          .flatMap((r) => [r.close, ...overlays.map((key) => r[key])])
          .filter((v) => v !== null),
    min0 = oscillator ? 0 : Math.min(...values),
    max0 = oscillator ? 100 : Math.max(...values),
    padding = oscillator ? 0 : (max0 - min0) * 0.13 || 1,
    min = min0 - padding,
    max = max0 + padding;
  const x = (i) =>
      left + (i / Math.max(1, rows.length - 1)) * (w - left - right),
    y = (value) => 12 + ((max - value) / (max - min)) * (h - bottom - 25);
  const svg = svgNode("svg", {
    viewBox: `0 0 ${w} ${h}`,
    "aria-hidden": "true",
  });
  const levels = oscillator
    ? [30, 70]
    : [0, 1, 2, 3, 4].map((i) => min + ((max - min) * i) / 4);
  for (const v of levels) {
    svg.append(
      svgNode("line", {
        x1: left,
        x2: w - right,
        y1: y(v),
        y2: y(v),
        class: "grid-line",
      }),
      svgNode(
        "text",
        { x: w - right + 10, y: y(v) + 4, class: "axis-text" },
        oscillator ? v : Number(v).toFixed(0),
      ),
    );
  }
  const series = oscillator ? ["rsi14"] : ["close", ...overlays];
  for (const key of series) {
    const points = rows
      .map((r, i) => (r[key] === null ? null : `${x(i)},${y(r[key])}`))
      .filter(Boolean)
      .join(" ");
    svg.append(
      svgNode("polyline", { points, class: `series ${key}`, fill: "none" }),
    );
  }
  if (!oscillator)
    for (
      let i = 0;
      i < rows.length;
      i += Math.max(1, Math.floor(rows.length / 4))
    )
      svg.append(
        svgNode(
          "text",
          { x: x(i), y: h - 4, class: "axis-text", "text-anchor": "middle" },
          new Date(rows[i].date + "T00:00:00").toLocaleDateString("en-IN", {
            day: "numeric",
            month: "short",
          }),
        ),
      );
  container.append(svg);
  const summary = oscillator
    ? `Last RSI ${rows.at(-1).rsi14?.toFixed(1) ?? "not warmed up"}`
    : `${rows.length} sample sessions. Latest close ${money(rows.at(-1).close)}`;
  container.setAttribute("aria-label", summary);
}
async function getIndicators(symbol) {
  if (!state.indicators[symbol])
    state.indicators[symbol] = await api(`/api/indicators/${symbol}`);
  return state.indicators[symbol];
}
async function loadOverviewChart() {
  const symbol = state.selected,
    stock = state.stocks.find((s) => s.symbol === symbol),
    data = await getIndicators(symbol);
  if (symbol !== state.selected) return;
  const title = $("#overview-stock");
  title.replaceChildren(
    document.createTextNode(symbol + " "),
    node("span", stock.name),
  );
  $("#overview-price").textContent = money(stock.price);
  $("#overview-change").textContent = pct(stock.change_pct);
  $("#overview-change").className =
    stock.change_pct >= 0 ? "positive" : "negative";
  drawChart("#overview-chart", data.symbol_data, ["sma20"]);
}
async function loadAnalysis() {
  const symbol = $("#analysis-symbol").value || state.selected,
    stock = state.stocks.find((s) => s.symbol === symbol),
    data = await getIndicators(symbol);
  if (symbol !== $("#analysis-symbol").value) return;
  $("#analysis-title").textContent = `${symbol} · ${stock.name}`;
  $("#analysis-price").textContent = money(stock.price);
  $("#analysis-change").textContent = pct(stock.change_pct);
  $("#analysis-change").className =
    stock.change_pct >= 0 ? "positive" : "negative";
  const overlays = [];
  if ($("#sma-toggle").checked) overlays.push("sma20");
  if ($("#ema-toggle").checked) overlays.push("ema20");
  drawChart("#analysis-chart", data.symbol_data, overlays);
  drawChart("#rsi-chart", data.symbol_data, [], true);
  $("#indicator-cards").replaceChildren();
  for (const [key, label, note] of [
    ["sma20", "SMA · 20 sessions", "An equal-weight view of recent closes."],
    ["ema20", "EMA · 20 sessions", "More weight on the latest price changes."],
    ["rsi14", "RSI · 14 sessions", "Momentum on a 0–100 scale."],
  ]) {
    const card = node("section", "", "panel indicator-card");
    append(
      card,
      node("span", label),
      node(
        "strong",
        data.latest[key] === null
          ? "Warm-up"
          : key === "rsi14"
            ? data.latest[key].toFixed(1)
            : money(data.latest[key]),
      ),
      node("p", note),
    );
    $("#indicator-cards").append(card);
  }
}
async function loadPortfolio() {
  state.portfolio = await api(
    state.user ? "/api/portfolio/summary" : "/api/demo/summary",
  );
  renderPortfolio();
}
async function loadActivity() {
  const box = $("#activity");
  box.replaceChildren();
  if (!state.user) {
    box.append(
      node("p", "Sign in to see your own portfolio edits and export history."),
    );
    return;
  }
  const data = await api("/api/activity");
  for (const event of data.events) {
    const item = node("div", "", "activity-row");
    append(
      item,
      node("span", event.action.replaceAll("_", " ")),
      node("b", event.symbol || "Portfolio"),
      node("small", new Date(event.created_at).toLocaleString("en-IN")),
    );
    box.append(item);
  }
  if (!data.events.length)
    box.append(node("p", "Your portfolio changes will appear here."));
}
function authUI() {
  $("#auth-button").textContent = state.user
    ? `${state.user.name} ↗`
    : "Sign in →";
  $("#logout").classList.toggle("hidden", !state.user);
}
function openAuth() {
  if (preview)
    return notify(
      "This preview is read-only. Run the Flask application to use accounts and saved portfolios.",
    );
  if (state.user) return navigate("portfolio");
  state.registering = false;
  configureAuth();
  $("#auth-modal").showModal();
}
function configureAuth() {
  $("#auth-title").textContent = state.registering
    ? "Make it your workspace."
    : "Welcome back.";
  $("#name-field").classList.toggle("hidden", !state.registering);
  $("#name-field input").required = state.registering;
  $("#auth-submit").textContent = state.registering
    ? "Create account →"
    : "Sign in →";
  $("#switch-auth").textContent = state.registering
    ? "Already registered? Sign in"
    : "New here? Create an account";
  $("#auth-error").textContent = "";
}
function openHolding(holding = null) {
  if (!state.user) return openAuth();
  state.editing = holding;
  const form = $("#holding-form");
  form.reset();
  form.elements.symbol.value = holding?.symbol || state.selected;
  form.elements.symbol.disabled = !!holding;
  form.elements.quantity.value = holding?.quantity || "";
  form.elements.average_price.value = holding?.average_price || "";
  $("#holding-title").textContent = holding
    ? `Edit ${holding.symbol}`
    : "Add a holding";
  $("#holding-error").textContent = "";
  $("#holding-modal").showModal();
}
async function mutate(fn) {
  try {
    await fn();
  } catch (e) {
    notify(e.message, true);
  }
}
$$("[data-page]").forEach((b) =>
  b.addEventListener("click", () => navigate(b.dataset.page)),
);
$$(".close-dialog").forEach((b) =>
  b.addEventListener("click", () => b.closest("dialog").close()),
);
$("#dismiss-toast").addEventListener("click", () =>
  $("#toast").classList.add("hidden"),
);
$("#auth-button").addEventListener("click", openAuth);
$("#switch-auth").addEventListener("click", () => {
  state.registering = !state.registering;
  configureAuth();
});
$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget,
    button = $("#auth-submit");
  button.disabled = true;
  try {
    const data = {
      email: form.elements.email.value,
      password: form.elements.password.value,
    };
    if (state.registering) data.name = form.elements.name.value;
    const result = await api(
      state.registering ? "/api/auth/register" : "/api/auth/login",
      { method: "POST", body: JSON.stringify(data) },
    );
    state.user = result.user;
    state.csrf = result.csrf_token;
    form.reset();
    $("#auth-modal").close();
    authUI();
    await loadPortfolio();
    notify("Your personal workspace is ready.");
  } catch (error) {
    $("#auth-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("#logout").addEventListener("click", () =>
  mutate(async () => {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
    state.user = null;
    state.csrf = (await api("/api/auth/session")).csrf_token;
    authUI();
    await loadPortfolio();
    await loadActivity();
    notify("Signed out. Your records remain in the database.");
  }),
);
$("#stock-search").addEventListener("input", (e) =>
  renderStocks(e.target.value),
);
$("#open-analysis").addEventListener("click", () => {
  $("#analysis-symbol").value = state.selected;
  navigate("analysis");
});
$("#analysis-symbol").addEventListener("change", () =>
  loadAnalysis().catch((e) => notify(e.message, true)),
);
["#sma-toggle", "#ema-toggle"].forEach((id) =>
  $(id).addEventListener("change", () =>
    loadAnalysis().catch((e) => notify(e.message, true)),
  ),
);
$("#add-holding").addEventListener("click", () => openHolding());
$("#holding-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget,
    button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const result = await api("/api/portfolio", {
      method: "POST",
      body: JSON.stringify({
        symbol: form.elements.symbol.value,
        quantity: Number(form.elements.quantity.value),
        average_price: form.elements.average_price.value,
        version: state.editing?.version || 0,
      }),
    });
    state.portfolio = result;
    renderPortfolio();
    $("#holding-modal").close();
    await loadActivity();
    notify("Holding saved in your workspace.");
  } catch (error) {
    $("#holding-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("#confirm-remove").addEventListener("click", () =>
  mutate(async () => {
    const h = state.removing;
    await api(`/api/portfolio/${h.symbol}`, {
      method: "DELETE",
      body: JSON.stringify({ version: h.version }),
    });
    $("#confirm-modal").close();
    await loadPortfolio();
    await loadActivity();
    notify("Holding removed.");
  }),
);
$("#export-portfolio").addEventListener("click", () =>
  mutate(async () => {
    if (!state.user) return openAuth();
    const result = await api("/api/exports", { method: "POST", body: "{}" });
    const link = node("a");
    link.href = result.download_url;
    link.download = "cloudfolio-portfolio.csv";
    document.body.append(link);
    link.click();
    link.remove();
    notify(
      `Portfolio export saved to ${result.storage === "private_s3" ? "private S3 storage" : "private local storage"}.`,
    );
    await loadActivity();
  }),
);
$("#export-chart").addEventListener("click", () =>
  mutate(async () => {
    if (!state.user) return openAuth();
    const result = await api(`/api/charts/${$("#analysis-symbol").value}`, {
      method: "POST",
      body: "{}",
    });
    const link = node("a");
    link.href = result.download_url;
    link.download = "cloudfolio-chart.svg";
    document.body.append(link);
    link.click();
    link.remove();
    notify("Chart exported to private storage.");
  }),
);
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("#theme").textContent = theme === "dark" ? "☀" : "☾";
  $("#theme").setAttribute(
    "aria-label",
    `Switch to ${theme === "dark" ? "white" : "black"} theme`,
  );
  try {
    localStorage.setItem("cloudfolio-theme", theme);
  } catch {}
}
try {
  setTheme(localStorage.getItem("cloudfolio-theme") || "light");
} catch {
  setTheme("light");
}
$("#theme").addEventListener("click", () =>
  setTheme(
    document.documentElement.dataset.theme === "dark" ? "light" : "dark",
  ),
);
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (!$("#overview").classList.contains("hidden"))
      loadOverviewChart().catch(() => {});
    if (!$("#analysis").classList.contains("hidden"))
      loadAnalysis().catch(() => {});
  }, 150);
});
async function init() {
  if (preview) {
    const data = await fetch("data/snapshot.json").then((r) => {
      if (!r.ok) throw new Error("Preview data could not be loaded");
      return r.json();
    });
    Object.assign(state, data);
    $("#preview-notice").classList.remove("hidden");
  }
  const [session, stocks, meta] = await Promise.all([
    api("/api/auth/session"),
    api("/api/stocks"),
    api("/api/meta"),
  ]);
  state.user = session.user;
  state.csrf = session.csrf_token;
  state.stocks = stocks.stocks;
  authUI();
  $$(".as-of").forEach((el) => (el.textContent = date(meta.as_of)));
  for (const stock of state.stocks) {
    for (const selector of ["#analysis-symbol", "#holding-symbol"]) {
      const option = node("option", stock.symbol);
      option.value = stock.symbol;
      $(selector).append(option);
    }
  }
  renderStocks();
  await Promise.all([loadOverviewChart(), loadPortfolio()]);
}
init().catch((e) => notify("Workspace could not load: " + e.message, true));
