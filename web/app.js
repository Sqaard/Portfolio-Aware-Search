const state = {
  settings: { portfolio: [], favorite_websites: [] },
  icons: {},
  results: [],
  activeVibeSite: "",
  allowedTickers: [],
  llmApiKey: "",
  llm: {},
  corpus: {},
  vibePostCache: {},
  searchOffset: 0,
  searchLimit: 10,
  searchGroupKey: "",
  searchFolderKey: "",
  hasSearched: false,
  lastQuery: "",
  lastSearchPayload: {},
  searchHintIndex: 0,
  searchHintTimer: null,
  searchHints: ["JPMorgan Chase filings", "Microsoft macro signals", "Apple events", "Home Depot risks"],
  suggestionCandidates: [],
  suggestionTimer: null,
  activeSuggestionIndex: -1,
  chartLab: { options: null, payload: null, analysis: null },
  portfolioAnalysis: { payload: null, extraCharts: [] },
  view: "home",
  vibeMode: "portfolio",
  llmProviders: [],
};

const PORTFOLIO_PIE_COLORS = ["#6f9ed6", "#7fb69d", "#c7ad6b", "#9a8fc8", "#c77c78", "#75b4b2", "#9bb2d0"];
const CHART_LAB_COLORS = ["#6f9ed6", "#7fb69d", "#c7ad6b", "#c77c78", "#9a8fc8", "#75b4b2"];
const SEARCH_INTENTS = [
  { suffix: "filings", label: "SEC filings" },
  { suffix: "risks", label: "Risk factors" },
  { suffix: "events", label: "Company events" },
  { suffix: "press releases", label: "Company IR" },
  { suffix: "macro signals", label: "Macro context" },
];
const GENERAL_SUGGESTIONS = [
  { query: "credit spreads", label: "Macro", meta: "Credit stress and risk appetite" },
  { query: "treasury yields", label: "Macro", meta: "Rates and valuation pressure" },
  { query: "vix risk appetite", label: "Macro", meta: "Volatility regime" },
  { query: "oil prices energy", label: "Macro", meta: "Energy and input costs" },
];
const CHART_HELP = {
  company_revenue_eps: {
    shows: "Revenue shows sales momentum; EPS shows profit per share from the reported fundamentals.",
    read: "Healthy charts usually show revenue and EPS moving up together; a split means growth is not fully reaching shareholders.",
  },
  company_margins: {
    shows: "Operating and net margins show how much revenue survives costs, taxes, and financing.",
    read: "Falling margins usually mean pricing power, cost control, or financing costs are moving against the company.",
  },
  company_balance_stress: {
    shows: "Debt ratio tracks leverage and current ratio tracks the short-term liquidity cushion.",
    read: "Debt rising while liquidity falls is the main danger pattern.",
  },
  company_filing_risk: {
    shows: "Event severity, company-risk documents, and legal/regulatory mentions summarize risk pressure in filings.",
    read: "Look for spikes: one large filing-risk jump can matter more than a smooth average.",
  },
  company_guidance_events: {
    shows: "Guidance mentions, sentiment, and profitability revisions connect forward-looking language with analyst-style revision pressure.",
    read: "Positive sentiment plus improving revisions is constructive; guidance spikes with weak sentiment need manual review.",
  },
  macro_rates_pressure: {
    shows: "Policy pressure, Treasury yields, and rates evidence show how interest rates may affect equity valuation.",
    read: "Higher rates pressure usually hurts long-duration growth stocks first.",
  },
  macro_credit_stress: {
    shows: "Credit stress, BAA spreads, and credit evidence track whether financing conditions are tightening.",
    read: "Rising spreads are a warning because investors demand more compensation for credit risk.",
  },
  macro_volatility: {
    shows: "VIX stress and volatility evidence show whether risk appetite is improving or deteriorating.",
    read: "Volatility spikes often mark fear; persistent elevation matters more than a one-day jump.",
  },
  macro_curve_shape: {
    shows: "Yield-curve slopes and inversion flag show recession and policy-transmission pressure.",
    read: "A deeply negative curve is a warning; re-steepening after inversion can also signal late-cycle stress.",
  },
  macro_financial_conditions: {
    shows: "NFCI, quality spread, and volatility term slope summarize broad market liquidity and stress.",
    read: "Tighter conditions mean capital is harder to access and equity risk premiums usually rise.",
  },
};
const FOLDER_CHART_HELP = {
  "Filing Activity": {
    shows: "Counts fresh filings by type so you can see whether disclosure flow is unusually busy.",
    read: "A sudden filing cluster is not automatically bad, but it tells you where to inspect source documents first.",
  },
  "Signal Heat": {
    shows: "Shows only non-zero text signals, making rare risk or upside evidence easier to spot.",
    read: "Spikes are more important than flat zeros; use them as bookmarks for document review.",
  },
  "Event Themes": {
    shows: "Separates guidance, legal, and event themes found in filings or company IR documents.",
    read: "Rising legal or risk themes deserve caution; rising guidance can be useful if sentiment is also improving.",
  },
};
const DELETE_ICON = `
  <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">
    <path d="M8.2 8.6h7.6" />
    <path d="M10 8.6V6.8c0-.6.5-1.1 1.1-1.1h1.8c.6 0 1.1.5 1.1 1.1v1.8" />
    <path d="M9.2 10.8l.5 6.1c.1.9.8 1.6 1.7 1.6h1.2c.9 0 1.7-.7 1.7-1.6l.5-6.1" />
    <path d="M11.2 12v4" />
    <path d="M12.8 12v4" />
  </svg>
`;

const $ = (id) => document.getElementById(id);

function initCosmicBackdrop() {
  const canvas = $("cosmicBackdrop");
  if (!canvas) return;
  const context = canvas.getContext("2d");
  if (!context) return;

  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
  let width = 0;
  let height = 0;
  let fieldStars = [];
  let galaxyStars = [];
  let animationId = 0;

  const random = (min, max) => min + Math.random() * (max - min);

  function buildStars() {
    const area = width * height;
    const fieldCount = Math.min(280, Math.max(110, Math.floor(area / 8500)));
    const galaxyCount = Math.min(420, Math.max(160, Math.floor(area / 6200)));
    const maxRadius = Math.max(width, height) * 0.58;
    fieldStars = Array.from({ length: fieldCount }, () => ({
      x: random(0, width),
      y: random(0, height),
      r: random(0.35, 1.3),
      a: random(0.22, 0.82),
      p: random(0, Math.PI * 2),
    }));
    galaxyStars = Array.from({ length: galaxyCount }, (_, index) => {
      const arm = index % 5;
      const radius = Math.pow(Math.random(), 0.64) * maxRadius;
      const angle = arm * ((Math.PI * 2) / 5) + radius * 0.0065 + random(-0.42, 0.42);
      const spread = random(-1, 1) * (18 + radius * 0.09);
      return {
        x: Math.cos(angle) * radius + Math.cos(angle + Math.PI / 2) * spread,
        y: Math.sin(angle) * radius * 0.45 + Math.sin(angle + Math.PI / 2) * spread * 0.38,
        r: random(0.45, 1.85),
        a: random(0.22, 0.92),
        hue: random(198, 224),
        p: random(0, Math.PI * 2),
      };
    });
  }

  function draw(time) {
    context.clearRect(0, 0, width, height);
    const centerX = width * 0.56;
    const centerY = height * 0.43;
    const pulse = Math.sin(time * 0.00045) * 0.08;

    const core = context.createRadialGradient(centerX, centerY, 0, centerX, centerY, Math.max(width, height) * 0.62);
    core.addColorStop(0, `rgba(74, 142, 210, ${0.24 + pulse})`);
    core.addColorStop(0.28, "rgba(34, 81, 145, 0.13)");
    core.addColorStop(0.62, "rgba(8, 18, 38, 0.04)");
    core.addColorStop(1, "rgba(5, 9, 20, 0)");
    context.fillStyle = core;
    context.fillRect(0, 0, width, height);

    for (const star of fieldStars) {
      const alpha = Math.max(0.08, star.a + Math.sin(time * 0.0012 + star.p) * 0.18);
      context.beginPath();
      context.fillStyle = `rgba(198, 220, 255, ${alpha})`;
      context.arc(star.x, star.y, star.r, 0, Math.PI * 2);
      context.fill();
    }

    context.save();
    context.translate(centerX, centerY);
    context.rotate(time * 0.000018);
    for (const star of galaxyStars) {
      const alpha = Math.max(0.06, star.a + Math.sin(time * 0.001 + star.p) * 0.16);
      context.beginPath();
      context.fillStyle = `hsla(${star.hue}, 85%, 78%, ${alpha})`;
      context.arc(star.x, star.y, star.r, 0, Math.PI * 2);
      context.fill();
    }
    context.restore();

    if (!prefersReducedMotion && !document.hidden) {
      animationId = window.requestAnimationFrame(draw);
    }
  }

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * pixelRatio);
    canvas.height = Math.floor(height * pixelRatio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    buildStars();
    draw(performance.now());
  }

  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => {
    window.cancelAnimationFrame(animationId);
    if (!document.hidden) animationId = window.requestAnimationFrame(draw);
  });
  resize();
  if (!prefersReducedMotion) animationId = window.requestAnimationFrame(draw);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { accept: "application/json", "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  let payload = null;
  const trimmed = text.trim();
  if (contentType.includes("application/json") || trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      payload = trimmed ? JSON.parse(trimmed) : {};
    } catch (error) {
      throw new Error(`Invalid JSON from ${url} (HTTP ${response.status}): ${error.message}`);
    }
  } else {
    if ([520, 522, 524].includes(response.status)) {
      throw new Error(
        `Cloudflare tunnel timeout while calling ${url}. The local server did not return JSON fast enough; retry once or restart the demo tunnel.`
      );
    }
    const snippet = trimmed
      .replace(/\s+/g, " ")
      .replace(/<[^>]*>/g, " ")
      .trim()
      .slice(0, 180);
    const detail = snippet || response.statusText || "empty response";
    throw new Error(`Expected JSON from ${url}, got ${contentType || "unknown content"} (HTTP ${response.status}). ${detail}`);
  }
  if (!response.ok) throw new Error(payload.error || response.statusText || `HTTP ${response.status}`);
  return payload;
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function money(value) {
  return Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function toneClass(tone) {
  return `tone-${tone || "neutral"}`;
}

function clamp01(value) {
  return Math.max(0, Math.min(1, Number(value || 0)));
}

function scoreLevel(value, maxValue = 4) {
  const ratio = clamp01(Number(value || 0) / maxValue);
  if (ratio >= 0.67) return "high";
  if (ratio >= 0.34) return "medium";
  return "low";
}

function signalTone(row) {
  const upside = Number(row.upside || 0);
  const risk = Number(row.risk || 0);
  if (risk >= 1.2 && risk > upside * 1.15) return "negative";
  if (upside >= 0.75 && upside >= risk * 0.85) return "positive";
  return "neutral";
}

function signalBadge(row) {
  const tone = signalTone(row);
  if (tone === "negative") return "Risk watch";
  if (tone === "positive") return "Upside lead";
  if (Number(row.signal || 0) >= 2) return "Strong evidence";
  return "Light signal";
}

function meterHtml(label, value, tone, maxValue = 4) {
  const ratio = clamp01(Number(value || 0) / maxValue);
  const level = scoreLevel(value, maxValue);
  return `
    <div class="visual-meter ${tone}">
      <div class="visual-meter-head">
        <span>${escapeHtml(label)}</span>
        <small>${level}</small>
      </div>
      <div class="visual-rail" aria-label="${escapeHtml(label)} ${level}">
        <i style="width:${Math.round(ratio * 100)}%"></i>
      </div>
    </div>
  `;
}

function buildSearchHints() {
  const fallbackCompanies = [
    "JPMorgan Chase",
    "Microsoft",
    "Apple",
    "Home Depot",
    "Caterpillar",
    "Walt Disney",
    "Chevron",
    "Visa",
  ];
  const companies = (state.allowedTickers || [])
    .map((row) => companySearchName(row))
    .filter(Boolean);
  const cycle = companies.length ? companies : fallbackCompanies;
  const intents = ["filings", "macro signals", "events", "risks"];
  return cycle.slice(0, Math.max(8, Math.min(cycle.length, 16))).map((company, index) => `${company} ${intents[index % intents.length]}`);
}

function updateSearchHintVisibility() {
  const rotator = $("searchPlaceholderRotator");
  if (!rotator) return;
  rotator.classList.toggle("is-hidden", Boolean($("searchInput").value.trim()));
}

function setSearchHint(text) {
  const rotator = $("searchPlaceholderRotator");
  if (!rotator) return;
  const label = rotator.querySelector("span");
  if (label) label.textContent = text;
}

function advanceSearchHint() {
  const rotator = $("searchPlaceholderRotator");
  if (!rotator || state.searchHints.length <= 1) return;
  rotator.classList.remove("slide-in");
  rotator.classList.add("slide-out");
  window.setTimeout(() => {
    state.searchHintIndex = (state.searchHintIndex + 1) % state.searchHints.length;
    setSearchHint(state.searchHints[state.searchHintIndex]);
    rotator.classList.remove("slide-out");
    rotator.classList.add("slide-in");
    window.setTimeout(() => rotator.classList.remove("slide-in"), 520);
  }, 340);
}

function startSearchHintRotator() {
  state.searchHints = buildSearchHints();
  state.searchHintIndex = 0;
  setSearchHint(state.searchHints[0] || "JPMorgan Chase filings");
  updateSearchHintVisibility();
  if (state.searchHintTimer) window.clearInterval(state.searchHintTimer);
  state.searchHintTimer = window.setInterval(advanceSearchHint, 3000);
}

function companySearchName(item) {
  const name = String(item?.name || item?.ticker || "").trim();
  if (!name) return "";
  return name
    .replace(/^The\s+/i, "")
    .replace(/,\s*(Inc\.?|Corporation|Company|Companies|Co\.?|Incorporated)\.?$/i, "")
    .replace(/\s+(Inc\.?|Corporation|Company|Companies|Co\.?|Incorporated)\.?$/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

function buildSuggestionCandidates() {
  const candidates = [];
  (state.allowedTickers || []).forEach((item) => {
    const ticker = String(item.ticker || "").toUpperCase();
    const name = String(item.name || "");
    const company = companySearchName(item);
    if (!ticker) return;
    SEARCH_INTENTS.forEach((intent) => {
      candidates.push({
        query: `${company || name || ticker} ${intent.suffix}`,
        label: company || name || ticker,
        meta: `${ticker} - ${intent.label}`,
        ticker,
        name,
        company,
        intent: intent.suffix,
      });
    });
  });
  return candidates.concat(GENERAL_SUGGESTIONS);
}

function suggestionScore(candidate, query) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return 0;
  const candidateQuery = String(candidate.query || "").toLowerCase();
  const ticker = String(candidate.ticker || "").toLowerCase();
  const name = String(candidate.name || "").toLowerCase();
  const company = String(candidate.company || "").toLowerCase();
  const intent = String(candidate.intent || "").toLowerCase();
  let score = 0;
  if (candidateQuery === normalized) score += 120;
  if (candidateQuery.startsWith(normalized)) score += 90;
  if (company && company.startsWith(normalized)) score += 85;
  if (ticker && ticker.startsWith(normalized)) score += 75;
  if (ticker && normalized.startsWith(ticker)) score += 58;
  if (name && name.includes(normalized)) score += 42;
  if (company && company.includes(normalized)) score += 42;
  if (intent && intent.includes(normalized)) score += 28;
  if (candidateQuery.includes(normalized)) score += 20;
  const parts = normalized.split(/\s+/).filter(Boolean);
  if (parts.length > 1 && parts.every((part) => candidateQuery.includes(part) || name.includes(part) || ticker.includes(part))) score += 34;
  if (candidate.intent === "filings") score += 6;
  if (candidate.intent === "risks") score += 4;
  return score;
}

function searchSuggestions(query) {
  const normalized = query.trim().toLowerCase();
  if (normalized.length < 1) return [];
  return (state.suggestionCandidates || [])
    .map((candidate) => ({ ...candidate, score: suggestionScore(candidate, normalized) }))
    .filter((candidate) => candidate.score > 0)
    .sort((a, b) => b.score - a.score || String(a.query).localeCompare(String(b.query)))
    .slice(0, 6);
}

function hideSearchSuggestions() {
  const panel = $("searchSuggestions");
  if (!panel) return;
  panel.classList.add("hidden");
  panel.innerHTML = "";
  state.activeSuggestionIndex = -1;
}

function renderSearchSuggestions() {
  const input = $("searchInput");
  const panel = $("searchSuggestions");
  if (!input || !panel) return;
  const suggestions = searchSuggestions(input.value);
  state.activeSuggestionIndex = suggestions.length ? Math.min(Math.max(state.activeSuggestionIndex, -1), suggestions.length - 1) : -1;
  if (!suggestions.length || document.activeElement !== input) {
    hideSearchSuggestions();
    return;
  }
  panel.innerHTML = suggestions.map((suggestion, index) => `
    <button
      class="search-suggestion ${index === state.activeSuggestionIndex ? "active" : ""}"
      type="button"
      role="option"
      aria-selected="${index === state.activeSuggestionIndex ? "true" : "false"}"
      data-search-suggestion="${escapeHtml(suggestion.query)}">
      <span>${escapeHtml(suggestion.query)}</span>
      <small>${escapeHtml(suggestion.meta || suggestion.label || "")}</small>
    </button>
  `).join("");
  panel.classList.remove("hidden");
}

function scheduleSearchSuggestions() {
  if (state.suggestionTimer) window.clearTimeout(state.suggestionTimer);
  state.suggestionTimer = window.setTimeout(renderSearchSuggestions, 70);
}

function acceptSearchSuggestion(query) {
  $("searchInput").value = query;
  updateSearchHintVisibility();
  hideSearchSuggestions();
  runSearch(query, 0, "");
}

function handleSearchSuggestionKeys(event) {
  const panel = $("searchSuggestions");
  if (!panel || panel.classList.contains("hidden")) return;
  const options = [...panel.querySelectorAll("[data-search-suggestion]")];
  if (!options.length) return;
  if (event.key === "ArrowDown") {
    event.preventDefault();
    state.activeSuggestionIndex = (state.activeSuggestionIndex + 1) % options.length;
    renderSearchSuggestions();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    state.activeSuggestionIndex = (state.activeSuggestionIndex - 1 + options.length) % options.length;
    renderSearchSuggestions();
  } else if (event.key === "Enter" && state.activeSuggestionIndex >= 0) {
    event.preventDefault();
    acceptSearchSuggestion(options[state.activeSuggestionIndex].dataset.searchSuggestion);
  } else if (event.key === "Escape") {
    hideSearchSuggestions();
  }
}

async function loadDashboard() {
  const payload = await fetchJson("/api/dashboard");
  state.settings = payload.settings;
  state.icons = payload.icons || {};
  state.allowedTickers = payload.allowed_tickers || [];
  state.llm = payload.llm || {};
  state.corpus = payload.corpus || {};
  state.llmProviders = state.llm.llm_providers || [];
  state.suggestionCandidates = buildSuggestionCandidates();
  renderLlmProviders();
  $("llmModel").value = state.llm.llm_default_model || "deepseek-chat";
  $("llmBaseUrl").value = state.llm.llm_default_base_url || "https://api.deepseek.com/chat/completions";
  syncLlmProviderFromFields();
  maybeAutoSelectProviderForKey();
  updateLlmStatus();
  renderTranslation(payload.macro_portfolio_translation);
  renderPortfolio(payload.portfolio_summary);
  renderPortfolioSignals(payload.portfolio_signal_summary || {});
  if (!state.chartLab.options) {
    renderChartLabOptions(payload.chart_lab || {});
  }
  renderSettings();
  startSearchHintRotator();
}

function renderLlmProviders() {
  const select = $("llmProvider");
  if (!select) return;
  const providers = llmProvidersForUi();
  select.innerHTML = providers.map((provider) => `
    <option value="${escapeHtml(provider.id)}" data-model="${escapeHtml(provider.model)}" data-base-url="${escapeHtml(provider.base_url)}" data-task-models="${escapeHtml(JSON.stringify(provider.task_models || {}))}">
      ${escapeHtml(provider.label)}
    </option>
  `).join("");
}

function llmProvidersForUi() {
  return state.llmProviders.length ? state.llmProviders : [
    { id: "mistral", label: "Mistral", model: "mistral-small-latest", base_url: "https://api.mistral.ai/v1/chat/completions" },
    { id: "deepseek", label: "DeepSeek Official", model: "deepseek-chat", base_url: "https://api.deepseek.com/chat/completions" },
    {
      id: "paratera_deepseek",
      label: "Paratera DeepSeek",
      model: "DeepSeek-V4-Flash",
      base_url: "https://llmapi.paratera.com/v1/chat/completions",
      task_models: {
        graph: "DeepSeek-V4-Flash",
        post: "DeepSeek-V4-Flash",
        portfolio: "DeepSeek-V4-Flash",
      },
    },
    { id: "openai", label: "OpenAI", model: "gpt-5.2", base_url: "https://api.openai.com/v1/responses" },
  ];
}

function selectedLlmProvider() {
  const providerId = $("llmProvider")?.value || "";
  const providers = llmProvidersForUi();
  return providers.find((provider) => provider.id === providerId) || null;
}

function syncLlmProviderFromFields() {
  const select = $("llmProvider");
  if (!select) return;
  const endpoint = String($("llmBaseUrl")?.value || "").toLowerCase();
  const model = String($("llmModel")?.value || "").toLowerCase();
  const match = (state.llmProviders || []).find((provider) => {
    const providerEndpoint = String(provider.base_url || "").toLowerCase();
    let hostname = "";
    try {
      hostname = new URL(providerEndpoint).hostname;
    } catch (error) {
      hostname = "";
    }
    return (hostname && endpoint.includes(hostname)) || model.startsWith(String(provider.model || "").split("-")[0].toLowerCase());
  });
  if (match) select.value = match.id;
}

function applyLlmProviderSelection() {
  const select = $("llmProvider");
  if (!select) return;
  const option = select.selectedOptions?.[0];
  if (!option) return;
  $("llmModel").value = option.dataset.model || $("llmModel").value;
  $("llmBaseUrl").value = option.dataset.baseUrl || $("llmBaseUrl").value;
  updateLlmStatus();
}

function inferProviderFromApiKey(apiKey) {
  const key = String(apiKey || "").trim();
  if (/^sk-(proj|svcacct)-/i.test(key)) return "openai";
  if (/^sk-[A-Za-z0-9_-]{12,}$/i.test(key)) return "deepseek";
  return "";
}

function applyProviderById(providerId) {
  const select = $("llmProvider");
  if (!select || !providerId) return false;
  const option = [...select.options].find((candidate) => candidate.value === providerId);
  if (!option) return false;
  select.value = providerId;
  applyLlmProviderSelection();
  return true;
}

function maybeAutoSelectProviderForKey() {
  const key = $("llmApiKey")?.value || "";
  const inferred = inferProviderFromApiKey(key);
  if (!inferred) return;
  const currentProvider = $("llmProvider")?.value || "";
  if (inferred === "deepseek" && ["mistral", "paratera_deepseek"].includes(currentProvider)) {
    applyProviderById("deepseek");
    const status = $("llmKeyStatus");
    if (status) status.textContent = "DeepSeek key detected";
  } else if (inferred === "openai" && currentProvider !== "openai") {
    applyProviderById("openai");
    const status = $("llmKeyStatus");
    if (status) status.textContent = "OpenAI key detected";
  }
}

function llmRequestConfig(task = "") {
  const provider = selectedLlmProvider();
  const taskModels = provider && typeof provider.task_models === "object" ? provider.task_models : {};
  const taskModel = task ? taskModels[task] : "";
  return {
    provider: $("llmProvider")?.value || "",
    api_key: state.llmApiKey || "",
    model: taskModel || $("llmModel")?.value || state.llm.llm_default_model || "deepseek-chat",
    base_url: $("llmBaseUrl")?.value || state.llm.llm_default_base_url || "https://api.deepseek.com/chat/completions",
    task_models: taskModels,
  };
}

function updateLlmStatus() {
  const text = state.llmApiKey
    ? "Session key ready"
    : (state.llm.llm_server_configured ? "Server key configured" : "No session key");
  $("llmKeyStatus").textContent = text;
  const vibeStatus = $("vibeLlmStatus");
  if (vibeStatus) {
    vibeStatus.textContent = state.llmApiKey || state.llm.llm_server_configured
      ? `LLM ready: ${($("llmModel")?.value || state.llm.llm_default_model || "configured model").trim()}`
      : "Local fallback is active";
  }
}

function renderTranslation(translation) {
  const cards = translation.cards || [];
  renderMacroToneMeter(cards);
  $("translationCards").innerHTML = cards
    .map((card) => `
      <details class="info-card ${toneClass(card.tone)}">
        <summary>${escapeHtml(card.title)}</summary>
        <div class="card-body">
          <p>${escapeHtml(card.summary)}</p>
          <span class="sector-chip">portfolio weight: ${percent(card.portfolio_weight)}</span>
        </div>
      </details>
    `)
    .join("");
}

function renderMacroToneMeter(cards) {
  const counts = { positive: 0, neutral: 0, negative: 0 };
  cards.forEach((card) => {
    const tone = ["positive", "neutral", "negative"].includes(card.tone) ? card.tone : "neutral";
    counts[tone] += 1;
  });
  const total = Math.max(1, cards.length);
  const positiveShare = (counts.positive / total) * 100;
  const neutralShare = (counts.neutral / total) * 100;
  const negativeShare = (counts.negative / total) * 100;
  const score = (counts.positive - counts.negative) / total;
  const label = score > 0.34 ? "supportive" : score < -0.34 ? "defensive" : "mixed";
  $("macroToneMeter").innerHTML = `
    <div class="macro-meter-track" title="${counts.positive} positive, ${counts.neutral} neutral, ${counts.negative} negative">
      <i class="macro-meter-positive" style="width: ${positiveShare}%"></i>
      <i class="macro-meter-neutral" style="width: ${neutralShare}%"></i>
      <i class="macro-meter-negative" style="width: ${negativeShare}%"></i>
    </div>
    <small class="macro-meter-label ${label}">${label}</small>
  `;
}

function renderChartLabOptions(options) {
  state.chartLab.options = options || {};
  const portfolioTickers = options.portfolio_tickers || [];
  const tickers = portfolioTickers.length
    ? portfolioTickers
    : (state.allowedTickers || []).map((row) => row.ticker).filter(Boolean);
  $("chartLabTicker").innerHTML = tickers.map((ticker) => `
    <option value="${escapeHtml(ticker)}">${escapeHtml(ticker)}</option>
  `).join("");
  const charts = (options.charts || []).filter((chart) => chart.scope !== "macro");
  $("chartLabChart").innerHTML = charts.map((chart) => `
    <option value="${escapeHtml(chart.id)}">${chart.scope === "macro" ? "Macro" : "Company"} · ${escapeHtml(chart.title)}</option>
  `).join("");
  $("chartLabChart").querySelectorAll("option").forEach((option, index) => {
    option.textContent = charts[index]?.title || option.textContent;
  });
  $("chartLabWindow").innerHTML = (options.windows || [
    { id: "1y", label: "1Y" },
    { id: "5y", label: "5Y" },
    { id: "all", label: "All" },
  ]).map((windowOption) => `
    <option value="${escapeHtml(windowOption.id)}">${escapeHtml(windowOption.label)}</option>
  `).join("");
  $("chartLabTicker").value = options.default_ticker || tickers[0] || "AAPL";
  $("chartLabChart").value = charts.some((chart) => chart.id === options.default_chart_id)
    ? options.default_chart_id
    : charts[0]?.id || "company_revenue_eps";
  $("chartLabWindow").value = options.default_window || "all";
  renderChartLabIdle();
}

async function loadChartLabOptions() {
  const options = await fetchJson("/api/chart-lab/options");
  renderChartLabOptions(options);
}

function renderChartLabIdle() {
  const canvas = $("chartLabCanvas");
  if (!canvas) return;
  state.chartLab.payload = null;
  state.chartLab.analysis = null;
  canvas.innerHTML = `
    <div class="chart-lab-empty">
      <strong>Pick a story to audit.</strong>
      <span>Build fundamental charts from filings and macro data only when needed.</span>
    </div>
  `;
}

function financeLoadingHtml(label, steps = []) {
  const loadingSteps = (steps.length ? steps : [
    "Scanning 10-K risk factors",
    "Checking revenue and margin clues",
    "Matching documents to portfolio weight",
    "Preparing analyst-style evidence",
  ]).slice(0, 5);
  return `
    <div class="finance-loading" role="status" aria-live="polite">
      <div class="finance-loader-visual" aria-hidden="true">
        <span class="money-note"></span>
        <span class="money-note note-two"></span>
        <span class="money-lens"></span>
        <span class="money-coin">$</span>
      </div>
      <div>
        <strong>${escapeHtml(label)}</strong>
        <div class="finance-loading-step-card" aria-hidden="true" style="--step-duration:${loadingSteps.length * 2800}ms">
          ${loadingSteps.map((step, index) => `<span style="--step-index:${index}; --step-count:${loadingSteps.length}">${escapeHtml(step)}</span>`).join("")}
        </div>
      </div>
    </div>
  `;
}

function compactNumber(value) {
  const number = Number(value || 0);
  const abs = Math.abs(number);
  if (abs >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(number / 1_000).toFixed(1)}K`;
  if (abs > 0 && abs < 0.01) return number.toFixed(4);
  return number.toFixed(abs >= 10 ? 1 : 2);
}

function formatChartValue(value, unit) {
  if (unit === "ratio") return `${(Number(value || 0) * 100).toFixed(1)}%`;
  if (unit === "flag") return Number(value || 0) > 0 ? "on" : "off";
  if (unit === "docs" || unit === "terms") return `${Math.round(Number(value || 0))} ${unit}`;
  const suffix = unit && !["score", "USD", "USD/share", "index"].includes(unit) ? ` ${unit}` : "";
  return `${compactNumber(value)}${suffix}`;
}

function formatChartInlineValue(value, unit) {
  if (unit === "percentage points") return `${Number(value || 0).toFixed(2)} pp`;
  if (unit === "percent") return `${Number(value || 0).toFixed(2)}%`;
  return formatChartValue(value, unit);
}

function chartX(date, minTime, span, width, left, right) {
  const time = Date.parse(date);
  const ratio = span <= 0 ? 0.5 : (time - minTime) / span;
  return left + ratio * (width - left - right);
}

function chartY(point, height, top, bottom) {
  return top + (1 - Number(point.y || 0.5)) * (height - top - bottom);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function truncateLabel(value, maxLength = 24) {
  const text = String(value || "");
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}...` : text;
}

function selectedChartOption(chartId) {
  return (state.chartLab.options?.charts || []).find((chart) => chart.id === chartId) || null;
}

function chartHelpDetails({ chartId, title, description, series = [] }) {
  const help = CHART_HELP[chartId] || FOLDER_CHART_HELP[title] || {
    shows: description || "This chart shows how selected financial evidence changes over time.",
    read: "Read left to right: rising lines mean the selected metric is increasing over the chosen period.",
  };
  const parameterList = series.slice(0, 3).map((item) => `
    <li><span class="chart-info-term">${escapeHtml(item.base_label || item.label || "Metric")}</span><span>${escapeHtml(seriesHelpText(item))}</span></li>
  `).join("");
  return `
    <details class="chart-info">
      <summary aria-label="Chart information"><span aria-hidden="true">i</span></summary>
      <div class="chart-info-popover">
        <strong>${escapeHtml(title || "Chart guide")}</strong>
        <p>${escapeHtml(help.shows)}</p>
        <p>${escapeHtml(help.read)}</p>
        ${parameterList ? `<ul>${parameterList}</ul>` : ""}
      </div>
    </details>
  `;
}

function seriesHelpText(series) {
  const key = `${series.key || ""} ${series.base_label || series.label || ""}`.toLowerCase();
  if (key.includes("revenue")) return "sales generated in the period.";
  if (key.includes("eps")) return "earnings per share, a compact profitability signal.";
  if (key.includes("inversion")) return "whether the yield curve is inverted; on is usually a late-cycle warning.";
  if (key.includes("warning") || key.includes("stress") || key.includes("risk") || key.includes("pressure")) return "pressure signal that flags documents or market data worth checking.";
  if (key.includes("margin")) return "profitability after key cost layers.";
  if (key.includes("debt")) return "how much of the balance sheet is financed by debt.";
  if (key.includes("current ratio")) return "short-term liquidity cushion.";
  if (key.includes("guidance")) return "forward-looking management or filing evidence.";
  if (key.includes("sentiment")) return "directional tone extracted from text.";
  if (key.includes("yield") || key.includes("rates")) return "interest-rate pressure relevant to equity valuation.";
  if (key.includes("spread") || key.includes("credit")) return "credit-market stress and financing tightness.";
  if (key.includes("vix") || key.includes("vol")) return "market fear and risk appetite.";
  return "a normalized financial signal; use the line label for the latest raw value.";
}

function chartLineLabelText(item) {
  const valueText = formatChartInlineValue(item.endpoint.value, item.unit);
  if (item.unit === "flag") {
    return truncateLabel(item.baseLabel || item.label, 13);
  }
  const maxLabelLength = item.unit === "docs" || item.unit === "terms" ? 15 : 17;
  return `${truncateLabel(item.baseLabel || item.label, maxLabelLength)} ${valueText}`;
}

function lineLabelsForSeries(seriesMeta, { height, top, bottom, labelX, minGap = 17 }) {
  const labels = seriesMeta
    .filter((item) => item.endpoint)
    .map((item) => ({
      ...item,
      labelY: item.endY,
    }))
    .sort((leftItem, rightItem) => leftItem.labelY - rightItem.labelY);
  const minY = top + 9;
  const maxY = height - bottom - 8;
  for (let index = 1; index < labels.length; index += 1) {
    if (labels[index].labelY - labels[index - 1].labelY < minGap) {
      labels[index].labelY = labels[index - 1].labelY + minGap;
    }
  }
  for (let index = labels.length - 1; index >= 0; index -= 1) {
    if (labels[index].labelY > maxY) labels[index].labelY = maxY;
    if (index > 0 && labels[index].labelY - labels[index - 1].labelY < minGap) {
      labels[index - 1].labelY = labels[index].labelY - minGap;
    }
  }
  return labels.map((item) => {
    const valueText = formatChartValue(item.endpoint.value, item.unit);
    const text = chartLineLabelText(item);
    return `
      <g class="chart-line-label">
        <line x1="${(item.endX + 6).toFixed(2)}" y1="${item.endY.toFixed(2)}" x2="${(labelX - 7).toFixed(2)}" y2="${(item.labelY - 4).toFixed(2)}" class="chart-label-leader"></line>
        <text x="${labelX.toFixed(2)}" y="${item.labelY.toFixed(2)}" style="fill:${item.color}">${escapeHtml(text)}</text>
        <title>${escapeHtml(item.baseLabel || item.label)} latest: ${escapeHtml(valueText)}</title>
      </g>
    `;
  }).join("");
}

function chartAnalysisMatches(payload, analysis) {
  return analysis
    && analysis.ticker === payload.ticker
    && analysis.chart_id === payload.chart_id
    && analysis.mode === payload.mode
    && (analysis.window || "all") === (payload.window || "all");
}

function chartTrendOverlays(seriesMeta, analysis, { minTime, span, width, left, right, height, top, bottom }) {
  if (!analysis?.trends?.length) return "";
  const byKey = new Map(seriesMeta.map((item) => [item.key, item]));
  return analysis.trends.map((trend) => {
    const source = byKey.get(trend.series_key);
    if (!source?.points?.length) return "";
    const color = source.color;
    const startPoint = source.points[0];
    const endPoint = source.points[source.points.length - 1];
    const startX = chartX(startPoint.date, minTime, span, width, left, right);
    const endX = chartX(endPoint.date, minTime, span, width, left, right);
    const startY = chartY({ y: trend.start_y }, height, top, bottom);
    const endY = chartY({ y: trend.end_y }, height, top, bottom);
    return `<line x1="${startX.toFixed(2)}" y1="${startY.toFixed(2)}" x2="${endX.toFixed(2)}" y2="${endY.toFixed(2)}" class="chart-trend-line" style="stroke:${color}"></line>`;
  }).join("");
}

function chartPointOverlays(seriesMeta, analysis, { minTime, span, width, left, right, height, top, bottom }) {
  if (!analysis?.points?.length) return "";
  const byKey = new Map(seriesMeta.map((item) => [item.key, item]));
  return analysis.points.map((point) => {
    const source = byKey.get(point.series_key);
    if (!source?.points?.length) return "";
    const matched = source.points.find((candidate) => String(candidate.date).slice(0, 10) === point.date);
    if (!matched) return "";
    const x = chartX(matched.date, minTime, span, width, left, right);
    const y = chartY(matched, height, top, bottom);
    const tone = point.tone || "neutral";
    const tooltipText = `${point.date || ""}: ${point.reason || point.value_label || ""}`;
    const tooltipWidth = Math.min(278, Math.max(158, tooltipText.length * 5.8));
    const tooltipX = clamp(x + 12, left + 2, width - right - tooltipWidth - 4);
    const tooltipY = clamp(y - 42, top + 2, height - bottom - 34);
    return `
      <g class="chart-highlight-point ${escapeHtml(tone)}" tabindex="0" focusable="true" aria-label="${escapeHtml(tooltipText)}">
        <circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="7.5"></circle>
        <path d="M ${(x - 3).toFixed(2)} ${(y - 1).toFixed(2)} L ${x.toFixed(2)} ${(y + 3).toFixed(2)} L ${(x + 4).toFixed(2)} ${(y - 4).toFixed(2)}"></path>
        <g class="chart-marker-tooltip">
          <rect x="${tooltipX.toFixed(2)}" y="${tooltipY.toFixed(2)}" width="${tooltipWidth.toFixed(2)}" height="31" rx="8"></rect>
          <text x="${(tooltipX + 8).toFixed(2)}" y="${(tooltipY + 19).toFixed(2)}">${escapeHtml(truncateLabel(tooltipText, 76))}</text>
        </g>
        <title>${escapeHtml(point.label || source.label)} ${escapeHtml(point.value_label || "")}: ${escapeHtml(point.reason || "")}</title>
      </g>
    `;
  }).join("");
}

function renderGraphAnalysisPanel(analysis, options = {}) {
  if (!analysis) return "";
  const modeLabel = analysis.analysis_mode === "llm"
    ? `LLM analysis${analysis.model ? ` - ${analysis.model}` : ""}`
    : "Rule-based analysis - less precise";
  const compactClass = options.compact ? " compact" : "";
  const takeaways = (analysis.takeaways || []).slice(0, options.compact ? 2 : 3);
  const points = (analysis.points || []).slice(0, options.compact ? 2 : 4);
  const followUp = analysis.follow_up || {};
  const followUpHtml = followUp.chart_id ? `
    <div class="graph-analysis-followup">
      <div>
        <strong>Verify with ${escapeHtml(followUp.label || followUp.chart_id)}</strong>
        <span>${escapeHtml(followUp.reason || "Use a second chart before treating this as a standalone signal.")}</span>
      </div>
      <button class="subtle-button" type="button" data-suggested-chart="${escapeHtml(followUp.chart_id)}">Check chart</button>
    </div>
  ` : "";
  return `
    <div class="graph-analysis-panel${compactClass} view-in">
      <div class="graph-analysis-head">
        <div>
          <strong>${escapeHtml(analysis.headline || analysis.verdict || "Watch")}</strong>
          <span class="graph-verdict">${escapeHtml(analysis.verdict || "Watch")}</span>
        </div>
        <small>${escapeHtml(modeLabel)}</small>
      </div>
      <p>${escapeHtml(analysis.sentence || "No clear chart signal found.")}</p>
      ${analysis.commentary ? `<p class="graph-analysis-commentary">${escapeHtml(analysis.commentary)}</p>` : ""}
      ${takeaways.length ? `
        <div class="graph-analysis-takeaways">
          ${takeaways.map((item) => `
            <article class="${escapeHtml(item.tone || "neutral")}">
              <strong>${escapeHtml(item.label || "Signal")}</strong>
              <span>${escapeHtml(item.text || "")}</span>
            </article>
          `).join("")}
        </div>
      ` : ""}
      ${analysis.llm_error ? `<small class="graph-analysis-error">LLM unavailable: ${escapeHtml(analysis.llm_error)}</small>` : ""}
      <div class="graph-analysis-points">
        ${points.map((point) => `
          <span class="${escapeHtml(point.tone || "neutral")}">
            ${escapeHtml(point.date || "")}: ${escapeHtml(point.reason || point.value_label || "")}
          </span>
        `).join("")}
      </div>
      ${followUpHtml}
    </div>
  `;
}

async function openSuggestedChart(chartId, sourceElement = null) {
  if (!chartId) return;
  const portfolioPanel = sourceElement?.closest?.("#portfolioTickerAnalysis");
  if (portfolioPanel && state.portfolioAnalysis.payload) {
    await addPortfolioAnalysisChartById(chartId);
    return;
  }
  const select = $("chartLabChart");
  if (select && [...select.options].some((option) => option.value === chartId)) {
    select.value = chartId;
    await loadChartLab();
  }
}

async function loadChartLab() {
  const canvas = $("chartLabCanvas");
  if (!canvas) return;
  const ticker = $("chartLabTicker").value || "AAPL";
  const chartId = $("chartLabChart").value || "company_revenue_eps";
  const mode = "structured";
  const windowValue = $("chartLabWindow").value || "all";
  state.chartLab.analysis = null;
  canvas.innerHTML = `<div class="chart-lab-empty">Loading chart...</div>`;
  try {
    const params = new URLSearchParams({ ticker, chart_id: chartId, mode, window: windowValue });
    const payload = await fetchJson(`/api/chart-lab/chart?${params.toString()}`);
    state.chartLab.payload = payload;
    renderChartLab(payload);
  } catch (error) {
    canvas.innerHTML = `<div class="chart-lab-empty">${escapeHtml(error.message || "Chart failed to load.")}</div>`;
  }
}

function renderChartLab(payload) {
  const canvas = $("chartLabCanvas");
  const series = payload.series || [];
  if (!series.length) {
    canvas.innerHTML = `
      <div class="chart-lab-empty">
        <strong>No fundamental chart data.</strong>
        <span>Try another chart, ticker, or time window.</span>
      </div>
    `;
    return;
  }
  const width = 780;
  const height = 320;
  const left = 58;
  const right = 184;
  const top = 24;
  const bottom = 58;
  const allPoints = series.flatMap((item) => item.points || []);
  const times = allPoints.map((point) => Date.parse(point.date)).filter((value) => Number.isFinite(value));
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const span = maxTime - minTime;
  const activeAnalysis = chartAnalysisMatches(payload, state.chartLab.analysis) ? state.chartLab.analysis : null;
  const seriesMeta = series.slice(0, 3).map((item, index) => {
    const color = CHART_LAB_COLORS[index % CHART_LAB_COLORS.length];
    const points = item.points || [];
    const endpoint = points[points.length - 1];
    const endX = endpoint ? chartX(endpoint.date, minTime, span, width, left, right) : left;
    const endY = endpoint ? chartY(endpoint, height, top, bottom) : top;
    return {
      ...item,
      key: item.key || item.label,
      baseLabel: item.base_label || item.label,
      color,
      points,
      endpoint,
      endX,
      endY,
    };
  });
  const paths = seriesMeta.map((item) => {
    const points = item.points || [];
    const dash = item.kind === "text" ? ' stroke-dasharray="6 5" opacity="0.88"' : "";
    const commands = points.map((point, pointIndex) => {
      const x = chartX(point.date, minTime, span, width, left, right);
      const y = chartY(point, height, top, bottom);
      return `${pointIndex === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(" ");
    return `
      <path d="${commands}" fill="none" stroke="${item.color}" stroke-width="2.7" stroke-linecap="round" stroke-linejoin="round"${dash}></path>
      <circle cx="${item.endX.toFixed(2)}" cy="${item.endY.toFixed(2)}" r="4" fill="${item.color}">
        <title>${escapeHtml(item.label)} latest: ${escapeHtml(formatChartValue(item.endpoint?.value, item.unit))}</title>
      </circle>
    `;
  }).join("");
  const inlineLabels = lineLabelsForSeries(seriesMeta, { height, top, bottom, labelX: width - right + 12, minGap: 18 });
  const trendLines = chartTrendOverlays(seriesMeta, activeAnalysis, { minTime, span, width, left, right, height, top, bottom });
  const highlightPoints = chartPointOverlays(seriesMeta, activeAnalysis, { minTime, span, width, left, right, height, top, bottom });
  const startDate = new Date(minTime).toISOString().slice(0, 10);
  const endDate = new Date(maxTime).toISOString().slice(0, 10);
  const chartOption = selectedChartOption(payload.chart_id);
  canvas.innerHTML = `
    <div class="chart-lab-title-row">
      <div class="chart-title-copy">
        <strong>${escapeHtml(payload.title)}</strong>
        <small>${escapeHtml(payload.scope === "macro" ? "Portfolio macro" : payload.ticker)} - Fundamentals - ${escapeHtml(payload.window_label || payload.window || "All")}</small>
      </div>
      <div class="chart-title-actions">
        <span>${escapeHtml(startDate)} - ${escapeHtml(endDate)}</span>
        ${chartHelpDetails({
          chartId: payload.chart_id,
          title: payload.title,
          description: chartOption?.description || payload.description,
          series,
        })}
        <button class="subtle-button graph-analysis-button" type="button" data-graph-analysis>Perform Graph Analysis</button>
      </div>
    </div>
    <svg class="chart-lab-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(payload.title)} time series">
      <line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" class="chart-axis"></line>
      <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" class="chart-axis"></line>
      <line x1="${left}" y1="${top}" x2="${width - right}" y2="${top}" class="chart-grid"></line>
      <line x1="${left}" y1="${top + (height - top - bottom) / 2}" x2="${width - right}" y2="${top + (height - top - bottom) / 2}" class="chart-grid"></line>
      <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" class="chart-grid"></line>
      <text x="${left - 10}" y="${top + 4}" text-anchor="end" class="chart-axis-label">100</text>
      <text x="${left - 10}" y="${top + (height - top - bottom) / 2 + 4}" text-anchor="end" class="chart-axis-label">50</text>
      <text x="${left - 10}" y="${height - bottom + 4}" text-anchor="end" class="chart-axis-label">0</text>
      <text x="${left}" y="${height - 18}" class="chart-axis-label">${escapeHtml(startDate)}</text>
      <text x="${width - right}" y="${height - 18}" text-anchor="end" class="chart-axis-label">${escapeHtml(endDate)}</text>
      <text x="${(left + width - right) / 2}" y="${height - 18}" text-anchor="middle" class="chart-axis-title">Date</text>
      <text x="18" y="${top + (height - top - bottom) / 2}" transform="rotate(-90 18 ${top + (height - top - bottom) / 2})" text-anchor="middle" class="chart-axis-title">Relative level, 0-100</text>
      ${trendLines}
      ${paths}
      ${highlightPoints}
      ${inlineLabels}
    </svg>
    ${renderGraphAnalysisPanel(activeAnalysis)}
  `;
}

async function performChartGraphAnalysis() {
  const payload = state.chartLab.payload;
  const canvas = $("chartLabCanvas");
  if (!payload || !canvas) return;
  const button = canvas.querySelector("[data-graph-analysis]");
  if (button) {
    button.disabled = true;
    button.textContent = "Analyzing...";
  }
  const existingPanel = canvas.querySelector(".graph-analysis-panel");
  const hasRemoteKey = Boolean(state.llmApiKey || state.llm.llm_server_configured);
  if (existingPanel) {
    existingPanel.outerHTML = financeLoadingHtml(hasRemoteKey ? "LLM is reading chart data" : "Applying rule-based chart checks");
  } else {
    canvas.insertAdjacentHTML("beforeend", financeLoadingHtml(hasRemoteKey ? "LLM is reading chart data" : "Applying rule-based chart checks"));
  }
  try {
    const analysis = await fetchJson("/api/chart-lab/analyze", {
      method: "POST",
      body: JSON.stringify({
        ticker: payload.ticker,
        chart_id: payload.chart_id,
        mode: payload.mode,
        window: payload.window || "all",
        llm: llmRequestConfig("graph"),
      }),
    });
    state.chartLab.analysis = analysis;
    renderChartLab(payload);
  } catch (error) {
    const loader = canvas.querySelector(".finance-loading");
    if (loader) loader.outerHTML = `<div class="graph-analysis-panel"><strong>Graph analysis failed</strong><p>${escapeHtml(error.message || "Unknown error")}</p></div>`;
    if (button) {
      button.disabled = false;
      button.textContent = "Perform Graph Analysis";
    }
  }
}

function portfolioPieData(summary) {
  const rawEntries = Object.entries(summary.sector_weights || {})
    .map(([sector, weight]) => ({ sector, weight: Number(weight || 0) }))
    .filter((item) => item.weight > 0)
    .sort((left, right) => right.weight - left.weight || left.sector.localeCompare(right.sector));
  if (rawEntries.length <= 6) return rawEntries;
  const main = rawEntries.slice(0, 5);
  const other = rawEntries.slice(5).reduce((total, item) => total + item.weight, 0);
  return [...main, { sector: "Other", weight: other }];
}

function polarToCartesian(centerX, centerY, radius, angleInDegrees) {
  const angleInRadians = (angleInDegrees - 90) * Math.PI / 180.0;
  return {
    x: centerX + (radius * Math.cos(angleInRadians)),
    y: centerY + (radius * Math.sin(angleInRadians)),
  };
}

function donutSegmentPath(startAngle, endAngle, outerRadius = 46, innerRadius = 25) {
  const safeEndAngle = Math.min(endAngle, startAngle + 359.99);
  const outerStart = polarToCartesian(50, 50, outerRadius, startAngle);
  const outerEnd = polarToCartesian(50, 50, outerRadius, safeEndAngle);
  const innerEnd = polarToCartesian(50, 50, innerRadius, safeEndAngle);
  const innerStart = polarToCartesian(50, 50, innerRadius, startAngle);
  const largeArcFlag = safeEndAngle - startAngle > 180 ? 1 : 0;
  return [
    `M ${outerStart.x.toFixed(3)} ${outerStart.y.toFixed(3)}`,
    `A ${outerRadius} ${outerRadius} 0 ${largeArcFlag} 1 ${outerEnd.x.toFixed(3)} ${outerEnd.y.toFixed(3)}`,
    `L ${innerEnd.x.toFixed(3)} ${innerEnd.y.toFixed(3)}`,
    `A ${innerRadius} ${innerRadius} 0 ${largeArcFlag} 0 ${innerStart.x.toFixed(3)} ${innerStart.y.toFixed(3)}`,
    "Z",
  ].join(" ");
}

function portfolioPieHtml(summary) {
  const entries = portfolioPieData(summary);
  if (!entries.length) {
    return `
      <div class="portfolio-pie-empty">
        <div class="portfolio-pie empty" aria-hidden="true"></div>
      </div>
    `;
  }
  let cursorAngle = 0;
  const segments = entries.map((entry, index) => {
    const startAngle = cursorAngle;
    const endAngle = cursorAngle + entry.weight * 360;
    cursorAngle = endAngle;
    const color = PORTFOLIO_PIE_COLORS[index % PORTFOLIO_PIE_COLORS.length];
    const percentLabel = percent(entry.weight);
    const sector = escapeHtml(entry.sector);
    return `
      <g class="portfolio-pie-slice">
        <path
          class="portfolio-pie-segment"
          d="${donutSegmentPath(startAngle, endAngle)}"
          fill="${color}"
          tabindex="0"
          data-sector="${sector}"
          data-percent="${percentLabel}"
          aria-label="${sector}: ${percentLabel}"
        >
          <title>${sector}: ${percentLabel}</title>
        </path>
        <text class="portfolio-pie-hover-label portfolio-pie-hover-percent" x="50" y="51">${percentLabel}</text>
      </g>
    `;
  }).join("");
  const legend = entries.map((entry, index) => `
    <span title="${escapeHtml(entry.sector)}: ${percent(entry.weight)}">
      <i style="background:${PORTFOLIO_PIE_COLORS[index % PORTFOLIO_PIE_COLORS.length]}"></i>
      ${escapeHtml(entry.sector)}
    </span>
  `).join("");
  return `
    <div class="portfolio-pie-wrap">
      <div class="portfolio-pie-shell">
        <svg class="portfolio-pie" viewBox="0 0 100 100" role="img" aria-label="Portfolio sector allocation pie chart">
          <circle class="portfolio-pie-hole" cx="50" cy="50" r="25"></circle>
          ${segments}
        </svg>
      </div>
      <div class="portfolio-pie-legend" aria-label="Portfolio sector legend">${legend}</div>
    </div>
  `;
}

function renderPortfolio(summary) {
  const html = `
    <div class="summary-number">$${money(summary.total_value)}</div>
    ${portfolioPieHtml(summary)}
  `;
  if ($("portfolioSummary")) $("portfolioSummary").innerHTML = html;
  if ($("portfolioSummaryVibe")) $("portfolioSummaryVibe").innerHTML = html;
  renderPortfolioAnalysisControls(summary);
}

function renderPortfolioAnalysisControls(summary) {
  const select = $("portfolioAnalysisTicker");
  if (!select) return;
  const holdings = summary.holdings || [];
  select.innerHTML = holdings.length
    ? holdings.map((holding) => `
      <option value="${escapeHtml(holding.ticker)}">${escapeHtml(holding.ticker)} - ${percent(holding.weight || 0)}</option>
    `).join("")
    : (state.allowedTickers || []).slice(0, 5).map((row) => `
      <option value="${escapeHtml(row.ticker)}">${escapeHtml(row.ticker)} - ${escapeHtml(row.name)}</option>
    `).join("");
}

function signalLabel(value) {
  return String(value || "")
    .replace(/^signal_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatDate(value) {
  const text = String(value || "");
  if (!text) return "";
  const date = new Date(text);
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  }
  return text.slice(0, 10);
}

function sourceTypeLabel(value) {
  const text = String(value || "").toLowerCase();
  if (text.includes("official_macro")) return "Macro release";
  if (text.includes("sec_filing_exhibit")) return "SEC exhibit";
  if (text.includes("sec_filing")) return "SEC filing";
  if (text.includes("earnings")) return "Earnings release";
  if (text.includes("press")) return "Press release";
  if (text.includes("financial_report")) return "Financial report";
  if (text.startsWith("company_")) return "Company IR";
  if (text.includes("news") || text.includes("headline")) return "News";
  return "Document";
}

function resultMetaHtml(result) {
  const pieces = [
    result.site_name || "source",
    formatDate(result.available_at || result.published_at),
    sourceTypeLabel(result.source_type),
  ].filter(Boolean);
  return `<div class="meta-row">${pieces.map(escapeHtml).join(" - ")}</div>`;
}

function renderPortfolioSignals(summary) {
  const tickers = (summary.ticker_signal_summary || []).slice(0, 4)
    .map((row) => {
      const tone = signalTone(row);
      return `
      <button class="signal-row signal-row-${tone}" type="button" data-signal-ticker="${escapeHtml(row.ticker)}" title="Search ${escapeHtml(row.ticker)} evidence">
        <div class="signal-row-top">
          <strong>${escapeHtml(row.ticker)}</strong>
          <span class="signal-badge">${escapeHtml(signalBadge(row))}</span>
        </div>
        <div class="signal-visual-grid">
          ${meterHtml("Evidence", row.signal, "neutral", 4)}
          ${meterHtml("Upside", row.upside, "positive", 4)}
          ${meterHtml("Risk", row.risk, "negative", 4)}
        </div>
      </button>
    `;
    }).join("");
  const topDocs = (summary.strongest_documents || []).slice(0, 3)
    .map((doc) => `
      <article class="mini-signal-doc">
        ${renderDocumentTitle(doc, doc.title, "small")}
        <small>${escapeHtml(doc.site_name)} - ${formatDate(doc.available_at)} - ${scoreLevel(doc.portfolio_signal_score || doc.signal_strength || 0, 4)} evidence</small>
      </article>
    `).join("");
  const html = `
    <details class="signal-layer-details">
      <summary>
        <span>Text Signal Layer</span>
        <small>${Number(summary.portfolio_relevant_doc_count || 0).toLocaleString("en-US")} portfolio-relevant docs</small>
      </summary>
      <div class="signal-list">${tickers || "<small>No ticker-level text signals yet.</small>"}</div>
    </details>
    <details class="signal-details">
      <summary>Strongest evidence</summary>
      ${topDocs || "<small>No scored evidence yet.</small>"}
    </details>
  `;
  if ($("portfolioSignalSummary")) $("portfolioSignalSummary").innerHTML = html;
  if ($("portfolioSignalSummaryVibe")) $("portfolioSignalSummaryVibe").innerHTML = html;
}

function hideQueryIntent() {
  $("queryIntentBar").classList.add("hidden");
  $("queryIntentBar").innerHTML = "";
}

function setHomeButtonVisible(visible) {
  $("myHomeButton").classList.toggle("hidden", !visible);
}

function finishViewAnimation(element) {
  if (!element) return;
  element.classList.remove("view-in", "view-out");
}

function animateViewIn(element) {
  if (!element) return;
  element.classList.remove("hidden", "view-out");
  element.classList.add("view-in");
  window.setTimeout(() => finishViewAnimation(element), 500);
}

function animateViewOut(element) {
  if (!element || element.classList.contains("hidden")) return;
  element.classList.remove("view-in");
  element.classList.add("view-out");
  window.setTimeout(() => {
    element.classList.add("hidden");
    finishViewAnimation(element);
  }, 360);
}

function showSearchView() {
  state.view = "search";
  $("myVibeView").classList.add("hidden");
  $("dashboardView").classList.remove("hidden");
  setHomeButtonVisible(true);
  animateViewOut($("homePanels"));
  animateViewIn($("searchPanel"));
}

function showHome() {
  state.view = "home";
  $("myVibeView").classList.add("hidden");
  $("dashboardView").classList.remove("hidden");
  setHomeButtonVisible(false);
  hideQueryIntent();
  animateViewOut($("searchPanel"));
  animateViewIn($("homePanels"));
}

async function runSearch(query = $("searchInput").value, offset = 0, groupKey = "", folderKey = "") {
  state.hasSearched = true;
  showSearchView();
  state.lastQuery = query;
  state.searchOffset = offset;
  state.searchGroupKey = groupKey || "";
  state.searchFolderKey = folderKey || "";
  $("resultCount").textContent = "searching";
  $("searchResults").innerHTML = `<article class="result-card muted-card"><p>Searching the evidence corpus...</p></article>`;
  $("resultPager").classList.add("hidden");
  const params = new URLSearchParams({
    q: query,
    limit: String(state.searchLimit),
    offset: String(offset),
  });
  if (state.searchGroupKey) params.set("group_key", state.searchGroupKey);
  if (state.searchFolderKey) params.set("folder_key", state.searchFolderKey);
  const payload = await fetchJson(`/api/search?${params.toString()}`);
  state.lastSearchPayload = payload;
  state.results = payload.results || [];
  state.corpus = payload.corpus || state.corpus || {};
  const totalDocs = Number(state.corpus.historical_document_count || state.corpus.document_count || 0);
  const pageStart = state.results.length ? Number(payload.offset || 0) + 1 : 0;
  const pageEnd = Number(payload.offset || 0) + state.results.length;
  let countText = `${pageStart}-${pageEnd} of ${payload.count} result${payload.count === 1 ? "" : "s"}`;
  if (payload.group_mode) {
    const title = payload.group?.title ? `: ${payload.group.title}` : "";
    countText = `${pageStart}-${pageEnd} of ${payload.count} documents in this group${title}`;
  } else if (payload.folder_mode) {
    const title = payload.folder?.title ? `: ${payload.folder.title}` : "";
    countText = `${pageStart}-${pageEnd} of ${payload.count} groups in this folder${title}`;
  } else if (payload.foldered) {
    countText = `${pageStart}-${pageEnd} of ${payload.count} folders / ${payload.grouped_count || 0} groups / ${payload.raw_count} docs`;
  } else if (payload.raw_count && payload.raw_count !== payload.count) {
    countText = `${pageStart}-${pageEnd} of ${payload.count} groups / ${payload.raw_count} docs`;
  }
  $("resultCount").textContent = totalDocs && !payload.group_mode ? `${countText} / ${totalDocs} historical corpus docs` : countText;
  hideQueryIntent();
  renderResults(state.results);
  renderPager(payload);
}

function renderQueryIntent(intent) {
  const bar = $("queryIntentBar");
  if (!intent || !intent.raw_query || state.view !== "search" || !$("myVibeView").classList.contains("hidden")) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    return;
  }
  const labels = {
    general_financial_search: "General search",
    company_event_search: "Company evidence",
    portfolio_impact: "Portfolio impact",
    filing_search: "SEC filings",
    filing_fact_lookup: "SEC facts",
    macro_regime_lookup: "Macro regime",
    news_sentiment_lookup: "News and sentiment",
    favorite_source_lookup: "Favorite sources",
    structured_numeric_lookup: "Numbers and tables",
  };
  const routes = (intent.source_routes || [])
    .filter((route) => route !== "local_corpus")
    .slice(0, 3)
    .map((route) => route.replace(/_/g, " "));
  const fields = (intent.field_labels || []).slice(0, 3);
  bar.classList.remove("hidden");
  bar.innerHTML = `
    <span class="intent-label">${escapeHtml(labels[intent.primary_intent] || "Search")}</span>
    ${routes.map((route) => `<span>${escapeHtml(route)}</span>`).join("")}
    ${fields.map((field) => `<span>${escapeHtml(field)}</span>`).join("")}
  `;
}

function renderResults(results) {
  $("searchResults").innerHTML = results.length
    ? results.map(renderResult).join("")
    : `<article class="result-card"><p>No local documents matched this query.</p></article>`;
}

function renderPager(payload) {
  const pager = $("resultPager");
  const hasPrev = payload.prev_offset !== null && payload.prev_offset !== undefined;
  const hasNext = payload.next_offset !== null && payload.next_offset !== undefined;
  if (!hasPrev && !hasNext && !payload.group_mode && !payload.folder_mode) {
    pager.classList.add("hidden");
    pager.innerHTML = "";
    return;
  }
  pager.classList.remove("hidden");
  pager.innerHTML = `
    ${payload.group_mode ? `<button class="subtle-button" type="button" data-group-back>Back to grouped results</button>` : ""}
    ${payload.folder_mode ? `<button class="subtle-button" type="button" data-folder-back>Back to folders</button>` : ""}
    <button class="subtle-button" type="button" ${hasPrev ? "" : "disabled"} data-page-offset="${hasPrev ? payload.prev_offset : 0}">Previous 10</button>
    <button class="subtle-button" type="button" ${hasNext ? "" : "disabled"} data-page-offset="${hasNext ? payload.next_offset : payload.offset}">Next 10</button>
  `;
}

function heartTooltip(result) {
  if (result.favorite_status === "pending_removed") return "Source removed for next refresh. Click to undo.";
  if (result.favorite_icon === "filled") return "Favorite source. Click to remove from favorites.";
  return "Add this source to favorites.";
}

function resultTags(result) {
  const signal = result.text_signal || {};
  const signalTags = (result.active_signals || signal.active_signals || []).slice(0, 3).map(signalLabel);
  return [...(result.event_tags || []), ...signalTags].filter(Boolean).slice(0, 3);
}

function renderSignalMeter(result) {
  if (String(result.source_type || "").startsWith("official_macro")) return "";
  const signal = result.text_signal || {};
  const signalScore = Number(signal.calibrated_signal_score || result.signal_strength || 0);
  if (signalScore <= 0) return "";
  const riskScore = Number(signal.risk_alert_score || 0);
  const upsideScore = Number(signal.upside_signal_score || 0);
  const rating = evidenceRating(signalScore, riskScore, upsideScore);
  return `
    <div class="signal-meter visual-signal-meter">
      ${compactSignalBar("Evidence", signalScore, "neutral")}
      ${compactSignalBar("Upside", upsideScore, "positive")}
      ${compactSignalBar("Risk", riskScore, "negative")}
      <div class="evidence-rating rating-${rating.value}">
        <div class="rating-head">
          <span>Evidence rating</span>
          <strong>${rating.value} - ${escapeHtml(rating.label)}</strong>
        </div>
        <div class="rating-track" aria-label="Evidence rating ${rating.value}">
          <i style="width:${rating.width}%"></i>
        </div>
      </div>
    </div>
  `;
}

function compactSignalBar(label, value, tone) {
  const ratio = clamp01(Number(value || 0) / 4);
  return `
    <div class="compact-signal ${tone}" title="${escapeHtml(label)} ${scoreLevel(value, 4)}">
      <span>${escapeHtml(label)}</span>
      <b>${scoreLevel(value, 4)}</b>
      <i><em style="width:${Math.round(ratio * 100)}%"></em></i>
    </div>
  `;
}

function evidenceRating(signal, risk, upside) {
  const score = 0.30 * clamp01(signal / 4) + 0.45 * clamp01(upside / 4) + 0.25 * (1 - clamp01(risk / 4));
  if (score >= 0.62) return { value: 1, label: "Invest", width: 100 };
  if (score >= 0.42) return { value: 2, label: "Hold", width: 66 };
  return { value: 3, label: "Sell", width: 33 };
}

function documentUrl(result) {
  const docId = String(result.doc_id || result.id || "").trim();
  if (!docId || /^https?:\/\//i.test(docId)) return "";
  return docId ? `/document/${encodeURIComponent(docId)}` : "";
}

function renderDocumentTitle(result, title, extraClass = "") {
  const href = documentUrl(result);
  const className = `result-title ${extraClass}`.trim();
  if (!href) return `<p class="${escapeHtml(className)}">${escapeHtml(title)}</p>`;
  return `
    <a class="${escapeHtml(className)} document-link" href="${escapeHtml(href)}" target="_blank" rel="noopener" title="Open document page">
      ${escapeHtml(title)}
    </a>
  `;
}

function favoriteSourceIcon(result) {
  const filled = result.favorite_icon === "filled";
  return `
    <svg class="favorite-source-icon ${filled ? "filled" : "empty"}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M6.8 4.4h10.4c.8 0 1.4.6 1.4 1.4v13.8l-6.6-3.2-6.6 3.2V5.8c0-.8.6-1.4 1.4-1.4Z"></path>
      <path class="favorite-source-spark" d="M9.2 8.6h5.6M9.2 11.2h4.1"></path>
    </svg>
  `;
}

function renderResultHeader(result) {
  const tooltip = heartTooltip(result);
  const title = result.group_title || result.title;
  const favoriteButton = result.url
    ? `
      <button class="favorite-source-button ${result.favorite_icon === "filled" ? "is-filled" : ""}" type="button" title="${escapeHtml(tooltip)}" aria-label="${escapeHtml(tooltip)}" data-heart-url="${escapeHtml(result.url)}">
        ${favoriteSourceIcon(result)}
      </button>
    `
    : `<span class="favorite-source-spacer" aria-hidden="true"></span>`;
  return `
    <div class="result-top">
      ${favoriteButton}
      <div class="result-copy">
        ${renderDocumentTitle(result, title)}
        ${resultMetaHtml(result)}
      </div>
    </div>
  `;
}

function renderGroupChild(result) {
  const tags = resultTags(result);
  return `
    <article class="group-child-result">
      ${renderDocumentTitle(result, result.title, "small")}
      ${resultMetaHtml(result)}
      <p>${escapeHtml(result.excerpt)}</p>
      ${renderSignalMeter(result)}
      <div class="tag-row">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
    </article>
  `;
}

function renderGroupedResult(result) {
  const tags = resultTags(result);
  const children = result.group_children || [];
  return `
    <article class="result-card grouped-result ${result.favorite_highlight ? "favorite" : ""}" data-url="${escapeHtml(result.url)}">
      ${renderResultHeader(result)}
      <div class="group-summary">Latest of ${Number(result.group_count || 1).toLocaleString("en-US")} similar documents. Older matches are grouped to keep search readable.</div>
      <p>${escapeHtml(result.excerpt)}</p>
      ${renderSignalMeter(result)}
      <div class="tag-row">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
      <div class="group-actions">
        <button class="subtle-button" type="button" data-group-more="${escapeHtml(result.group_key)}">Show more</button>
      </div>
      <div class="group-children hidden" data-group-children="${escapeHtml(result.group_key)}">
        ${children.map(renderGroupChild).join("")}
        ${result.group_has_more ? `<button class="subtle-button group-all-button" type="button" data-group-all="${escapeHtml(result.group_key)}">Show all ${Number(result.group_count || 0).toLocaleString("en-US")} documents</button>` : ""}
      </div>
    </article>
  `;
}

function renderFolderGroupPreview(result) {
  const tags = resultTags(result);
  const title = result.group_title || result.title;
  const count = Number(result.group_count || 1);
  return `
    <article class="folder-group-preview">
      <div>
        ${renderDocumentTitle(result, title, "small")}
        ${resultMetaHtml(result)}
      </div>
      <p>${escapeHtml(result.excerpt)}</p>
      ${renderSignalMeter(result)}
      <div class="tag-row">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
      <div class="group-actions">
        ${count > 1 ? `<button class="subtle-button" type="button" data-group-more="${escapeHtml(result.group_key)}">Show more</button>` : ""}
      </div>
      <div class="group-children hidden" data-group-children="${escapeHtml(result.group_key)}">
        ${(result.group_children || []).map(renderGroupChild).join("")}
        ${result.group_has_more ? `<button class="subtle-button group-all-button" type="button" data-group-all="${escapeHtml(result.group_key)}">Show all ${count.toLocaleString("en-US")} documents</button>` : ""}
      </div>
    </article>
  `;
}

function renderFolderAnalysisLegacy(payload) {
  return renderFolderAnalysis(payload);
}

function renderFolderAnalysisObsolete(payload) {
  if (payload.analysis_markdown) {
    return `
      <div class="folder-analysis-box">
        <strong>${escapeHtml(payload.folder_title || "Folder analysis")}</strong>
        <div class="meta-row">LLM analysis complete${payload.model ? ` · ${escapeHtml(payload.model)}` : ""} · ${Number(payload.analyzed_document_count || 0).toLocaleString("en-US")} docs considered</div>
        <div class="llm-markdown">${renderPlainMarkdown(payload.analysis_markdown)}</div>
      </div>
    `;
  }
  const metrics = payload.metrics || {};
  const charts = payload.suggested_charts || [];
  const topDocs = payload.top_documents || [];
  return `
    <div class="folder-analysis-box">
      <strong>${escapeHtml(payload.folder_title || "Folder analysis")}</strong>
      <div class="meta-row">${escapeHtml(payload.window_start)} - ${escapeHtml(payload.window_end)} · ${Number(payload.analyzed_document_count || 0).toLocaleString("en-US")} docs analyzed</div>
      <p>${escapeHtml(payload.short_conclusion || "")}</p>
      <div class="folder-metric-row">
        <span>signal ${Number(metrics.avg_signal || 0).toFixed(2)}</span>
        <span>risk ${Number(metrics.avg_risk || 0).toFixed(2)}</span>
        <span>upside ${Number(metrics.avg_upside || 0).toFixed(2)}</span>
      </div>
      <div class="chart-suggestion-grid">
        ${charts.map((chart) => `
          <article>
            <strong>${escapeHtml(chart.title)}</strong>
            <p>${escapeHtml(chart.why)}</p>
            <small>${escapeHtml(chart.inputs)}</small>
          </article>
        `).join("")}
      </div>
      <details class="folder-top-docs">
        <summary>Top source documents</summary>
        ${topDocs.map((doc) => `
          <div>
            <span>${escapeHtml(doc.title)}</span>
            <small>${escapeHtml(doc.available_at)} · signal ${Number(doc.signal || 0).toFixed(2)} · risk ${Number(doc.risk || 0).toFixed(2)}</small>
          </div>
        `).join("")}
      </details>
    </div>
  `;
}

function renderFolderResult(result) {
  const children = result.folder_children || [];
  const tickers = (result.matched_tickers || []).slice(0, 4).join(", ");
  return `
    <article class="result-card folder-result" data-folder-key="${escapeHtml(result.folder_key)}">
      <div class="folder-header">
        <div class="folder-icon" aria-hidden="true">▰</div>
        <div class="result-copy">
          <p class="result-title">${escapeHtml(result.folder_title)}</p>
          <div class="meta-row">${Number(result.folder_count || 0).toLocaleString("en-US")} groups - ${Number(result.folder_document_count || 0).toLocaleString("en-US")} docs - latest ${escapeHtml(formatDate(result.folder_latest_available_at || ""))}${tickers ? ` - ${escapeHtml(tickers)}` : ""}</div>
        </div>
      </div>
      <p>${escapeHtml(result.folder_summary || "")}</p>
      <div class="folder-actions">
        <button class="subtle-button" type="button" data-folder-more="${escapeHtml(result.folder_key)}">Open folder</button>
        <label class="folder-window-control">
          <span>Window</span>
          <select data-folder-window="${escapeHtml(result.folder_key)}" aria-label="Analysis window">
            <option value="1y" selected>1Y</option>
            <option value="5y">5Y</option>
            <option value="all">All</option>
          </select>
        </label>
        <button class="primary-mini-button" type="button" data-folder-analysis="${escapeHtml(result.folder_key)}">Perform analysis</button>
      </div>
      <div class="folder-analysis hidden" data-folder-analysis-panel="${escapeHtml(result.folder_key)}"></div>
      <div class="folder-children hidden" data-folder-children="${escapeHtml(result.folder_key)}">
        ${children.map(renderFolderGroupPreview).join("")}
        ${result.folder_has_more ? `<button class="subtle-button group-all-button" type="button" data-folder-all="${escapeHtml(result.folder_key)}">Show all ${Number(result.folder_count || 0).toLocaleString("en-US")} groups</button>` : ""}
      </div>
    </article>
  `;
}

function renderResult(result) {
  if (result.result_kind === "folder") {
    return renderFolderResult(result);
  }
  if (result.result_kind === "group" && Number(result.group_count || 0) > 1) {
    return renderGroupedResult(result);
  }
  const tags = resultTags(result);
  return `
    <article class="result-card ${result.favorite_highlight ? "favorite" : ""}" data-url="${escapeHtml(result.url)}">
      ${renderResultHeader(result)}
      <p>${escapeHtml(result.excerpt)}</p>
      ${renderSignalMeter(result)}
      <div class="tag-row">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
    </article>
  `;
}

async function toggleFavorite(url) {
  const payload = await fetchJson("/api/favorites/toggle", {
    method: "POST",
    body: JSON.stringify({ url, results: state.results }),
  });
  state.settings = payload.settings;
  state.results = payload.results || state.results;
  renderResults(state.results);
  renderSettings();
}

function folderChartSeries(chart) {
  return (chart.series || [])
    .slice(0, 3)
    .map((series) => ({
      ...series,
      key: series.key || series.label,
      baseLabel: series.base_label || series.label,
      points: (series.points || [])
        .map((point) => ({
          date: String(point.date || "").slice(0, 10),
          value: Number(point.value || 0),
        }))
        .filter((point) => point.date && Number.isFinite(Date.parse(point.date)) && Number.isFinite(point.value))
        .sort((left, right) => Date.parse(left.date) - Date.parse(right.date)),
    }))
    .filter((series) => {
      if (series.points.length < 2) return false;
      const values = series.points.map((point) => Math.abs(Number(point.value || 0)));
      const maxValue = Math.max(...values);
      const minValue = Math.min(...values);
      const threshold = series.unit === "docs" || series.unit === "terms" ? 0.5 : 0.05;
      return maxValue >= threshold || maxValue - minValue >= 0.03;
    });
}

function folderChartX(date, minTime, span, width, left, right) {
  const ratio = span <= 0 ? 0.5 : (Date.parse(date) - minTime) / span;
  return left + Math.max(0, Math.min(1, ratio)) * (width - left - right);
}

function folderChartY(value, values, height, top, bottom) {
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const span = maxValue - minValue;
  const ratio = span <= 1e-9 ? 0.5 : (value - minValue) / span;
  return top + (1 - Math.max(0, Math.min(1, ratio))) * (height - top - bottom);
}

function folderDisplayPoint(series) {
  const points = series.points || [];
  const latest = points[points.length - 1] || { value: 0 };
  const threshold = series.unit === "docs" || series.unit === "terms" ? 0.5 : 0.05;
  if (Math.abs(Number(latest.value || 0)) >= threshold) {
    return { point: latest, label: "latest" };
  }
  const lastHit = [...points].reverse().find((point) => Math.abs(Number(point.value || 0)) >= threshold);
  if (lastHit && lastHit !== latest) {
    return { point: lastHit, label: "last hit" };
  }
  return { point: latest, label: "quiet" };
}

function renderFolderChartCard(chart, chartIndex) {
  const series = folderChartSeries(chart);
  if (!series.length) return "";
  const width = 450;
  const height = 230;
  const left = 44;
  const right = 122;
  const top = 22;
  const bottom = 48;
  const allPoints = series.flatMap((item) => item.points);
  const times = allPoints.map((point) => Date.parse(point.date));
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const span = maxTime - minTime;
  const seriesMeta = series.map((item, index) => {
    const color = CHART_LAB_COLORS[(chartIndex + index) % CHART_LAB_COLORS.length];
    const values = item.points.map((point) => point.value);
    const endpoint = item.points[item.points.length - 1];
    const endX = folderChartX(endpoint.date, minTime, span, width, left, right);
    const endY = folderChartY(endpoint.value, values, height, top, bottom);
    return { ...item, color, values, endpoint, endX, endY, key: item.key || item.label };
  });
  const paths = seriesMeta.map((item) => {
    const commands = item.points.map((point, pointIndex) => {
      const x = folderChartX(point.date, minTime, span, width, left, right);
      const y = folderChartY(point.value, item.values, height, top, bottom);
      return `${pointIndex === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(" ");
    return `
      <path d="${commands}" fill="none" stroke="${item.color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"></path>
      <circle cx="${item.endX.toFixed(2)}" cy="${item.endY.toFixed(2)}" r="4" fill="${item.color}">
        <title>${escapeHtml(item.label)} latest: ${escapeHtml(formatChartValue(item.endpoint.value, item.unit))}</title>
      </circle>
    `;
  }).join("");
  const inlineLabels = lineLabelsForSeries(seriesMeta, { height, top, bottom, labelX: width - right + 10, minGap: 16 });
  const startDate = new Date(minTime).toISOString().slice(0, 10);
  const endDate = new Date(maxTime).toISOString().slice(0, 10);
  return `
    <article class="folder-chart-card view-card view-in">
      <div class="folder-chart-card-title">
        <div>
          <strong>${escapeHtml(chart.title || "Chart")}</strong>
          <small>${escapeHtml(chart.subtitle || "")}</small>
        </div>
        <div class="chart-title-actions">
          <small>${escapeHtml(startDate)} - ${escapeHtml(endDate)}</small>
          ${chartHelpDetails({
            title: chart.title || "Chart",
            description: chart.subtitle || "",
            series,
          })}
        </div>
      </div>
      <svg class="folder-chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(chart.title || "Folder chart")}">
        <line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" class="chart-axis"></line>
        <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" class="chart-axis"></line>
        <line x1="${left}" y1="${top}" x2="${width - right}" y2="${top}" class="chart-grid"></line>
        <line x1="${left}" y1="${top + (height - top - bottom) / 2}" x2="${width - right}" y2="${top + (height - top - bottom) / 2}" class="chart-grid"></line>
        <text x="${left - 8}" y="${top + 4}" text-anchor="end" class="chart-axis-label">100</text>
        <text x="${left - 8}" y="${height - bottom + 4}" text-anchor="end" class="chart-axis-label">0</text>
        <text x="${(left + width - right) / 2}" y="${height - 16}" text-anchor="middle" class="chart-axis-title">Date</text>
        <text x="16" y="${top + (height - top - bottom) / 2}" transform="rotate(-90 16 ${top + (height - top - bottom) / 2})" text-anchor="middle" class="chart-axis-title">Relative level, 0-100</text>
        ${paths}
        ${inlineLabels}
      </svg>
      ${renderGraphAnalysisPanel(chart.analysis, { compact: true })}
      ${chart.note ? `<div class="folder-chart-note">${escapeHtml(chart.note)}</div>` : ""}
    </article>
  `;
}

function toneSymbol(tone) {
  if (tone === "positive") return "↑";
  if (tone === "negative") return "↓";
  return "→";
}

function renderAnalystMetricCards(view) {
  return `
    <div class="analyst-metric-grid">
      ${(view.metric_cards || []).map((card) => `
        <article class="analyst-metric-card ${escapeHtml(card.tone || "neutral")}">
          <span>${escapeHtml(card.label)}</span>
          <strong>${escapeHtml(card.value)}</strong>
          <small>${escapeHtml(toneSymbol(card.tone))} ${escapeHtml(card.delta || "n/a")}</small>
        </article>
      `).join("")}
    </div>
  `;
}

function renderAnalystTable(table) {
  return `
    <div class="analyst-table-card">
      <strong>${escapeHtml(table.title || "Key numbers")}</strong>
      <table>
        <thead>
          <tr>${(table.columns || []).map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${(table.rows || []).map((row) => {
            const tone = row[4] || "neutral";
            return `
              <tr>
                <td>${escapeHtml(row[0])}</td>
                <td>${escapeHtml(row[1])}</td>
                <td>${escapeHtml(row[2])}</td>
                <td><span class="delta-pill ${escapeHtml(tone)}">${escapeHtml(toneSymbol(tone))} ${escapeHtml(row[3])}</span></td>
              </tr>
            `;
          }).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderCompareBars(chart) {
  const rows = (chart.rows || []).slice(0, 5);
  return rows.map((row) => {
    const rowMax = Math.max(1, Math.abs(Number(row.current || 0)), Math.abs(Number(row.prior || 0)));
    const currentWidth = Math.max(8, Math.min(100, Math.abs(Number(row.current || 0)) / rowMax * 100));
    const priorWidth = Math.max(8, Math.min(100, Math.abs(Number(row.prior || 0)) / rowMax * 100));
    return `
      <div class="analyst-bar-row">
        <div class="analyst-bar-label">
          <strong>${escapeHtml(row.metric)}</strong>
          <span class="delta-pill ${escapeHtml(row.tone || "neutral")}">${escapeHtml(toneSymbol(row.tone))} ${escapeHtml(row.change_label || "n/a")}</span>
        </div>
        <div class="analyst-bars">
          <div><span>current</span><i style="width:${currentWidth}%"></i><b>${escapeHtml(row.current_label)}</b></div>
          <div class="prior"><span>prior</span><i style="width:${priorWidth}%"></i><b>${escapeHtml(row.prior_label)}</b></div>
        </div>
      </div>
    `;
  }).join("");
}

function renderMixBar(chart) {
  const segments = chart.segments || [];
  return `
    <div class="analyst-mix-stack">
      ${segments.map((segment, index) => {
        const color = CHART_LAB_COLORS[index % CHART_LAB_COLORS.length];
        const width = Math.max(4, Math.min(100, Number(segment.share || 0)));
        return `<span style="width:${width}%; background:${color}" title="${escapeHtml(segment.label)} ${escapeHtml(String(segment.share || 0))}%"></span>`;
      }).join("")}
    </div>
    <div class="analyst-mix-legend">
      ${segments.map((segment, index) => `
        <span><i style="background:${CHART_LAB_COLORS[index % CHART_LAB_COLORS.length]}"></i>${escapeHtml(segment.label)} <strong>${escapeHtml(String(segment.share || 0))}%</strong> ${escapeHtml(segment.value_label || "")}</span>
      `).join("")}
    </div>
  `;
}

function renderMarginBars(chart) {
  return (chart.rows || []).map((row, index) => {
    const value = Math.max(0, Math.min(100, Number(row.value || 0)));
    const color = CHART_LAB_COLORS[index % CHART_LAB_COLORS.length];
    return `
      <div class="analyst-gauge-row">
        <span>${escapeHtml(row.label)}</span>
        <div><i style="width:${value}%; background:${color}"></i></div>
        <strong>${escapeHtml(row.value_label || "")}</strong>
      </div>
    `;
  }).join("");
}

function renderAnalystChart(chart) {
  let body = "";
  if (chart.type === "mix_bar") {
    body = renderMixBar(chart);
  } else if (chart.type === "margin_bars") {
    body = renderMarginBars(chart);
  } else {
    body = renderCompareBars(chart);
  }
  return `
    <article class="analyst-chart-card view-card view-in">
      <div class="folder-chart-card-title">
        <div>
          <strong>${escapeHtml(chart.title || "Chart")}</strong>
          <small>${escapeHtml(chart.subtitle || "")}</small>
        </div>
      </div>
      ${body}
    </article>
  `;
}

function renderAnalystDrivers(view) {
  const drivers = view.drivers || [];
  if (!drivers.length) return "";
  return `
    <div class="analyst-driver-grid">
      ${drivers.map((driver) => `
        <article class="${escapeHtml(driver.tone || "neutral")}">
          <strong>${escapeHtml(toneSymbol(driver.tone))} ${escapeHtml(driver.label)}</strong>
          <span>${escapeHtml(driver.text || "")}</span>
        </article>
      `).join("")}
    </div>
  `;
}

function renderAnalystView(payload, view) {
  const source = view.source_document || {};
  return `
    <div class="folder-analysis-box analyst-view-block view-card view-in">
      <div class="folder-chart-header">
        <div>
          <strong>${escapeHtml(view.title || payload.folder_title || "Financial analysis")}</strong>
          <small>${escapeHtml(payload.window_label || "1Y")} window &middot; ${escapeHtml(formatDate(source.available_at || payload.window_end || ""))} &middot; ${escapeHtml(sourceTypeLabel(source.source_type || "source document"))}</small>
        </div>
        <span>${escapeHtml(view.source === "llm" ? "LLM extracted financial data" : "Cached financial statement data")}</span>
      </div>
      <div class="analyst-source-line">${escapeHtml(source.title || view.subtitle || "")}</div>
      ${renderAnalystMetricCards(view)}
      <div class="analyst-chart-grid">
        ${(view.charts || []).slice(0, 3).map(renderAnalystChart).join("")}
      </div>
      ${renderAnalystDrivers(view)}
      ${(view.tables || []).map(renderAnalystTable).join("")}
    </div>
  `;
}

function renderFolderAnalysis(payload) {
  if (payload.analyst_view?.available) {
    return renderAnalystView(payload, payload.analyst_view);
  }
  const pack = payload.chart_pack || {};
  const charts = (pack.charts || []).slice(0, 3);
  const chartCards = charts.map((chart, index) => renderFolderChartCard(chart, index)).filter(Boolean).join("");
  const sourceLabel = payload.llm_used && pack.source === "llm"
    ? `LLM chart data - ${payload.model || "configured model"}`
    : "Cached text-signal chart data";
  return `
    <div class="folder-analysis-box folder-chart-block view-card view-in">
      <div class="folder-chart-header">
        <div>
          <strong>${escapeHtml(payload.folder_title || "Folder charts")}</strong>
          <small>${escapeHtml(payload.window_label || "1Y")} window &middot; ${escapeHtml(payload.window_start || "")} - ${escapeHtml(payload.window_end || "")} &middot; ${Number(payload.analyzed_document_count || 0).toLocaleString("en-US")} docs</small>
        </div>
        <span>${escapeHtml(sourceLabel)}</span>
      </div>
      <div class="folder-chart-grid">
        ${chartCards || `
          <div class="chart-lab-empty">
            <strong>No chart data collected.</strong>
            <span>Try another folder or search query.</span>
          </div>
        `}
      </div>
    </div>
  `;
}

async function performFolderAnalysis(folderKey) {
  const panel = document.querySelector(`[data-folder-analysis-panel="${CSS.escape(folderKey)}"]`);
  if (!panel) return;
  const windowSelect = document.querySelector(`[data-folder-window="${CSS.escape(folderKey)}"]`);
  const analysisWindow = windowSelect?.value || "1y";
  panel.classList.remove("hidden");
  panel.innerHTML = financeLoadingHtml(`Building analysis for ${analysisWindow.toUpperCase()} window`);
  try {
    const payload = await fetchJson("/api/search/folder-analysis", {
      method: "POST",
      body: JSON.stringify({
        query: state.lastQuery,
        folder_key: folderKey,
        window: analysisWindow,
        llm: llmRequestConfig("post"),
      }),
    });
    panel.innerHTML = renderFolderAnalysis(payload);
  } catch (error) {
    panel.innerHTML = `<strong>Analysis failed</strong><p>${escapeHtml(error.message)}</p>`;
  }
}

function renderSettings() {
  $("portfolioRows").innerHTML = state.settings.portfolio.map((row, index) => portfolioRowHtml(row, index)).join("");
  $("favoriteRows").innerHTML = state.settings.favorite_websites.map((url, index) => favoriteRowHtml(url, index)).join("");
}

function tickerOptions(selectedTicker) {
  return state.allowedTickers
    .map((item) => {
      const selected = item.ticker === selectedTicker ? " selected" : "";
      return `<option value="${escapeHtml(item.ticker)}"${selected}>${escapeHtml(item.ticker)} - ${escapeHtml(item.name)}</option>`;
    })
    .join("");
}

function portfolioRowHtml(row, index) {
  const selectedTicker = String(row.ticker || state.allowedTickers[0]?.ticker || "AAPL").toUpperCase();
  return `
    <div class="setting-row" data-portfolio-index="${index}">
      <select class="ticker-input" aria-label="Dow 30 ticker">${tickerOptions(selectedTicker)}</select>
      <input class="number-input" type="number" min="0" step="0.01" value="${escapeHtml(row.purchase_price)}" placeholder="Purchase price">
      <input class="number-input" type="number" min="0" step="0.01" value="${escapeHtml(row.quantity)}" placeholder="Quantity">
      <button class="delete-button" type="button" data-delete-portfolio="${index}" aria-label="Remove position" title="Remove position">${DELETE_ICON}</button>
    </div>
  `;
}

function favoriteRowHtml(url, index) {
  return `
    <div class="setting-row" data-favorite-index="${index}">
      <input class="number-input" value="${escapeHtml(url)}" readonly placeholder="https://example.com">
      <button class="delete-button" type="button" data-delete-favorite="${index}" aria-label="Remove favorite website" title="Remove favorite website">${DELETE_ICON}</button>
    </div>
  `;
}

function collectSettings() {
  const portfolio = [...document.querySelectorAll("[data-portfolio-index]")].map((row) => {
    const ticker = row.querySelector("select");
    const inputs = row.querySelectorAll("input");
    return {
      ticker: ticker ? ticker.value : "",
      purchase_price: inputs[0].value,
      quantity: inputs[1].value,
    };
  });
  const favorite_websites = [...document.querySelectorAll("[data-favorite-index] input")].map((input) => input.value);
  return { portfolio, favorite_websites };
}

async function saveSettings() {
  $("settingsStatus").textContent = "";
  if ($("llmApiKey")?.value.trim()) {
    saveLlmKeyForSession();
  } else {
    updateLlmStatus();
  }
  try {
    state.settings = await fetchJson("/api/settings", { method: "POST", body: JSON.stringify(collectSettings()) });
    $("settingsModal").classList.add("hidden");
    await loadDashboard();
    if (state.hasSearched && state.view === "search") await runSearch();
  } catch (error) {
    $("settingsStatus").textContent = error.message;
  }
}

async function addFavoriteWebsite() {
  const input = $("favoriteInput");
  const value = input.value.trim();
  if (!value) return;
  $("favoriteStatus").textContent = "Checking website...";
  try {
    const checked = await fetchJson("/api/favorites/validate-url", {
      method: "POST",
      body: JSON.stringify({ url: value }),
    });
    if (!checked.valid) {
      $("favoriteStatus").textContent = `Not added: ${checked.message}`;
      return;
    }
    if (!state.settings.favorite_websites.includes(checked.storage_url)) {
      state.settings.favorite_websites.push(checked.storage_url);
      state.settings.favorite_websites.sort();
    }
    input.value = "";
    $("favoriteStatus").textContent = checked.http_status
      ? `Verified: ${checked.storage_url} (${checked.http_status})`
      : `Verified: ${checked.storage_url}`;
    renderSettings();
  } catch (error) {
    $("favoriteStatus").textContent = `Not added: ${error.message}`;
  }
}

function saveLlmKeyForSession() {
  maybeAutoSelectProviderForKey();
  state.llmApiKey = $("llmApiKey").value.trim();
  updateLlmStatus();
}

function clearLlmKey() {
  state.llmApiKey = "";
  $("llmApiKey").value = "";
  updateLlmStatus();
}

async function showMyVibe() {
  state.view = "vibe";
  hideQueryIntent();
  setHomeButtonVisible(true);
  $("dashboardView").classList.add("hidden");
  $("myVibeView").classList.remove("hidden");
  setVibeMode("portfolio");
  state.activeVibeSite = "";
  state.vibePostCache = {};
  $("vibePostStatus").textContent = "select a site";
  $("vibePosts").innerHTML = `<article class="result-card muted-card"><p>Select a favorite website to show cached posts.</p></article>`;
  $("vibeAnalysis").innerHTML = "";
  $("vibeAnalysis").classList.add("hidden");
  const payload = await fetchJson("/api/my-vibe/sites");
  renderVibeSites(payload.sites || []);
  preloadInitialVibePosts(payload.sites || []);
}

function showDashboard() {
  showHome();
}

function setVibeMode(mode) {
  state.vibeMode = mode === "favorites" ? "favorites" : "portfolio";
  $("portfolioAnalysisMode")?.classList.toggle("hidden", state.vibeMode !== "portfolio");
  $("favoriteWebsitesMode")?.classList.toggle("hidden", state.vibeMode !== "favorites");
  document.querySelectorAll("[data-vibe-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.vibeMode === state.vibeMode);
  });
}

function renderVibeSites(sites) {
  $("vibeSites").innerHTML = sites.length
    ? sites.map((site) => `
      <button class="site-button" data-site="${escapeHtml(site.site_key)}" onclick="loadVibePosts(this.dataset.site)">
        <strong>${escapeHtml(site.display_name)}</strong>
        <small>${Number(site.post_count || 0).toLocaleString("en-US")} docs</small>
      </button>
    `).join("")
    : `<div class="site-button">Add favorite websites in Settings.</div>`;
  document.querySelectorAll("#vibeSites [data-site]").forEach((button) => {
    button.addEventListener("click", () => loadVibePosts(button.dataset.site));
  });
}

async function preloadInitialVibePosts(sites) {
  await Promise.all((sites || []).map(async (site) => {
    try {
      const payload = await fetchJson(`/api/my-vibe/posts?site=${encodeURIComponent(site.site_key)}&limit=5&offset=0`);
      state.vibePostCache[site.site_key] = {
        posts: payload.posts || [],
        total: payload.total || 0,
        nextOffset: payload.next_offset,
      };
    } catch (error) {
      state.vibePostCache[site.site_key] = { posts: [], total: 0, nextOffset: null, error: error.message };
    }
  }));
}

async function loadVibePosts(siteKey) {
  state.activeVibeSite = siteKey;
  document.querySelectorAll(".site-button").forEach((button) => button.classList.toggle("active", button.dataset.site === siteKey));
  if (!state.vibePostCache[siteKey]) {
    const payload = await fetchJson(`/api/my-vibe/posts?site=${encodeURIComponent(siteKey)}&limit=5&offset=0`);
    state.vibePostCache[siteKey] = {
      posts: payload.posts || [],
      total: payload.total || 0,
      nextOffset: payload.next_offset,
    };
  }
  renderVibePosts(siteKey);
}

function renderVibePosts(siteKey) {
  const cache = state.vibePostCache[siteKey] || { posts: [], total: 0, nextOffset: null };
  $("vibePostStatus").textContent = `top ${cache.posts.length} of ${cache.total} ranked docs`;
  const postHtml = cache.posts.length
    ? cache.posts.map((post) => `
      <article class="result-card" data-post-id="${escapeHtml(post.id)}">
        ${renderDocumentTitle(post, post.title)}
        <div class="meta-row">${escapeHtml(post.site)} - ${escapeHtml(formatDate(post.published_at))} - ${escapeHtml(sourceTypeLabel(post.source_type || "document"))} - full text hidden</div>
        <p>${escapeHtml(post.summary)}</p>
        <div class="tag-row">
          <span>vibe score ${post.vibe_score ?? post.portfolio_relevance_score}</span>
          ${(post.event_tags || []).slice(0, 2).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
        </div>
      </article>
    `).join("")
    : `<article class="result-card"><p>No posts for this favorite site in the local corpus.</p></article>`;
  const moreButton = cache.nextOffset !== null && cache.nextOffset !== undefined
    ? `<button id="uploadMorePosts" class="upload-more-button" type="button" data-site="${escapeHtml(siteKey)}">Upload 5 more posts</button>`
    : "";
  $("vibePosts").innerHTML = postHtml + moreButton;
}

async function uploadMoreVibePosts(siteKey) {
  const cache = state.vibePostCache[siteKey];
  if (!cache || cache.nextOffset === null || cache.nextOffset === undefined) return;
  const button = $("uploadMorePosts");
  if (button) {
    button.disabled = true;
    button.textContent = "Uploading...";
  }
  const payload = await fetchJson(`/api/my-vibe/posts?site=${encodeURIComponent(siteKey)}&limit=5&offset=${cache.nextOffset}`);
  cache.posts = [...cache.posts, ...(payload.posts || [])];
  cache.total = payload.total || cache.total;
  cache.nextOffset = payload.next_offset;
  renderVibePosts(siteKey);
}

async function analyzePost(postId) {
  const hasRemoteKey = Boolean(state.llmApiKey || state.llm.llm_server_configured);
  $("vibeAnalysis").classList.remove("hidden");
  $("vibeAnalysis").innerHTML = `
    ${financeLoadingHtml(hasRemoteKey ? "LLM is analyzing" : "Analyzing locally")}
    <p>Full text is hidden in the interface and used only as analysis input.</p>
  `;
  try {
    const payload = await fetchJson("/api/my-vibe/analyze", {
      method: "POST",
      body: JSON.stringify({
        post_id: postId,
        llm: llmRequestConfig("post"),
      }),
    });
    renderAnalysis(payload);
  } catch (error) {
    $("vibeAnalysis").innerHTML = `<strong>Analysis failed</strong><p>${escapeHtml(error.message)}</p>`;
  }
}

function renderAnalysis(payload) {
  const status = payload.llm_used ? "LLM analysis complete" : "Local analysis complete";
  if (payload.analysis_markdown) {
    $("vibeAnalysis").innerHTML = `
      <strong>${escapeHtml(payload.post.title)}</strong>
      <div class="meta-row">${status}${payload.model ? ` - ${escapeHtml(payload.model)}` : ""}</div>
      <div class="llm-markdown">${renderPlainMarkdown(payload.analysis_markdown)}</div>
    `;
    return;
  }
  $("vibeAnalysis").innerHTML = `
    <strong>${escapeHtml(payload.post.title)}</strong>
    <div class="meta-row">${status}</div>
    <h4>Short conclusion</h4>
    <p>${escapeHtml(payload.short_conclusion)}</p>
    <h4>Affected holdings</h4>
    <div class="tag-row">${payload.affected_holdings.map((item) => `<span>${escapeHtml(item.ticker)}: ${escapeHtml(item.possible_effect)}</span>`).join("")}</div>
    <h4>What changed</h4>
    <ul>${payload.what_changed.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    <h4>Checks before action</h4>
    <ul>${payload.checks_before_action.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    <h4>Confidence</h4>
    <p>${escapeHtml(payload.confidence)}</p>
  `;
}

function chartPayloadToCard(payload) {
  return {
    title: payload.title || "Analyst chart",
    subtitle: payload.description || "",
    chart_id: payload.chart_id,
    analysis: payload.analysis || null,
    series: (payload.series || []).slice(0, 3).map((series) => ({
      key: series.key,
      label: series.base_label || series.label,
      unit: series.unit,
      points: (series.points || []).map((point) => ({
        date: String(point.date || "").slice(0, 10),
        value: Number(point.value || 0),
      })),
    })),
  };
}

function renderPortfolioTickerAnalysis(payload) {
  state.portfolioAnalysis.payload = payload;
  state.portfolioAnalysis.extraCharts = [];
  updatePortfolioTickerAnalysisHtml();
}

function updatePortfolioTickerAnalysisHtml() {
  const payload = state.portfolioAnalysis.payload;
  const panel = $("portfolioTickerAnalysis");
  if (!payload || !panel) return;
  const status = payload.llm_used
    ? `LLM analysis complete - ${payload.model || "configured model"}`
    : "Rule-based analysis - less precise";
  const chartOptions = (state.chartLab.options?.charts || [])
    .filter((chart) => chart.scope === "company" && !(payload.available_chart_ids || []).includes(chart.id))
    .map((chart) => `<option value="${escapeHtml(chart.id)}">${escapeHtml(chart.title)}</option>`)
    .join("");
  const charts = [...(payload.charts || []), ...state.portfolioAnalysis.extraCharts];
  panel.className = "portfolio-ticker-analysis-results";
  panel.innerHTML = `
    <div class="portfolio-analysis-head">
      <div>
        <strong>${escapeHtml(payload.ticker)} portfolio analysis</strong>
        <small>${escapeHtml(status)}</small>
      </div>
      <span>${Number(payload.documents?.length || 0).toLocaleString("en-US")} linked docs</span>
    </div>
    <div class="llm-markdown portfolio-analysis-markdown">${renderPlainMarkdown(payload.analysis_markdown || "")}</div>
    <div class="portfolio-analysis-charts">
      ${charts.slice(0, 6).map((chart, index) => renderFolderChartCard(chart, index)).join("")}
    </div>
    <div class="portfolio-add-chart">
      <label>
        Add chart
        <select id="portfolioExtraChart">${chartOptions || `<option value="">No more company charts</option>`}</select>
      </label>
      <button class="subtle-button" type="button" data-add-portfolio-chart ${chartOptions ? "" : "disabled"}>Add graph</button>
    </div>
    <details class="portfolio-evidence-list" open>
      <summary>Source documents</summary>
      <div>
        ${(payload.documents || []).slice(0, 10).map((doc) => `
          <article>
            ${renderDocumentTitle({ doc_id: doc.doc_id }, doc.title, "small")}
            <small>${escapeHtml(doc.site_name || "source")} - ${escapeHtml(formatDate(doc.available_at))} - ${escapeHtml(sourceTypeLabel(doc.source_type))}</small>
            <p>${escapeHtml(doc.excerpt || "")}</p>
          </article>
        `).join("") || "<small>No linked documents found.</small>"}
      </div>
    </details>
  `;
}

async function analyzePortfolioTicker() {
  const ticker = $("portfolioAnalysisTicker")?.value || state.settings.portfolio?.[0]?.ticker || "AAPL";
  const panel = $("portfolioTickerAnalysis");
  if (!panel) return;
  const hasRemoteKey = Boolean(state.llmApiKey || state.llm.llm_server_configured);
  panel.className = "portfolio-ticker-analysis-loading";
  panel.innerHTML = financeLoadingHtml(
    hasRemoteKey ? `LLM is analyzing ${ticker}` : `Applying rule-based checks for ${ticker}`,
    [
      "Opening latest 10-K and 10-Q evidence",
      "Extracting revenue, EPS and margin clues",
      "Checking debt and liquidity pressure",
      "Linking source documents to charts",
      "Drafting compact portfolio verdict",
    ],
  );
  try {
    const payload = await fetchJson("/api/portfolio/analyze", {
      method: "POST",
      body: JSON.stringify({
        ticker,
        llm: llmRequestConfig("portfolio"),
      }),
    });
    renderPortfolioTickerAnalysis(payload);
  } catch (error) {
    panel.className = "portfolio-ticker-analysis-results";
    panel.innerHTML = `<strong>Portfolio analysis failed</strong><p>${escapeHtml(error.message || "Unknown error")}</p>`;
  }
}

async function addPortfolioAnalysisChart() {
  const payload = state.portfolioAnalysis.payload;
  const chartId = $("portfolioExtraChart")?.value;
  await addPortfolioAnalysisChartById(chartId);
}

async function addPortfolioAnalysisChartById(chartId) {
  const payload = state.portfolioAnalysis.payload;
  if (!payload || !chartId) return;
  const button = document.querySelector("[data-add-portfolio-chart]");
  if (button) {
    button.disabled = true;
    button.textContent = "Adding...";
  }
  try {
    const params = new URLSearchParams({ ticker: payload.ticker, chart_id: chartId, mode: "structured" });
    const chartPayload = await fetchJson(`/api/chart-lab/chart?${params.toString()}`);
    if (chartPayload.available) {
      try {
        chartPayload.analysis = await fetchJson("/api/chart-lab/analyze", {
          method: "POST",
          body: JSON.stringify({
            ticker: payload.ticker,
            chart_id: chartId,
            mode: "structured",
            window: chartPayload.window || "all",
            llm: llmRequestConfig("graph"),
          }),
        });
      } catch (analysisError) {
        chartPayload.analysis = null;
      }
      state.portfolioAnalysis.extraCharts.push(chartPayloadToCard(chartPayload));
      payload.available_chart_ids = [...(payload.available_chart_ids || []), chartId];
      updatePortfolioTickerAnalysisHtml();
    }
  } catch (error) {
    if (button) {
      button.disabled = false;
      button.textContent = "Add graph";
    }
  }
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/\[([^\]]+)\]\(((?:https?:\/\/|\/document\/)[^)\s]+)\)/g, `<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>`)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function normalizeMarkdownText(markdown) {
  let text = String(markdown || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
  if (!text) return "";
  text = text.replace(/^([A-Z0-9 .&/+-]{3,90} Evidence Verdict)\s+(?=\d+[.)]\s+)/, "## $1\n\n");
  text = text.replace(
    /\s+\d+[.)]\s+(One-line verdict|Compact metric table|What the charts say|Suspicious\/Good points|Evidence document links):?\s*/gi,
    (_match, title) => `\n\n## ${title}\n`,
  );
  text = text.replace(/\s+---+\s+/g, "\n\n");
  text = text.replace(/\s+(\|\s*[A-Za-z][^|\n]{0,54}\s*\|\s*[A-Za-z0-9($%][^|\n]{0,70}\s*\|)/g, "\n$1");
  text = text.replace(/\s+-\s+(?=(?:Revenue|Margin|Debt|Filing|Apple Inc\.|Microsoft|JPMorgan|[A-Z]{2,5}\b)[^.\n]{0,90})/g, "\n- ");
  return text;
}

function splitCompactTableRows(row) {
  const text = String(row || "").trim();
  if (!text.startsWith("|")) return [row];
  const tokens = text.split("|");
  let index = tokens[0].trim() === "" ? 1 : 0;
  const headerCells = [];
  while (index < tokens.length && tokens[index].trim() !== "") {
    headerCells.push(tokens[index]);
    index += 1;
  }
  if (headerCells.length < 2) return [row];
  const rows = [`|${headerCells.join("|")}|`];
  if (tokens[index]?.trim() === "") index += 1;
  while (index < tokens.length) {
    const cells = [];
    while (index < tokens.length && cells.length < headerCells.length) {
      cells.push(tokens[index]);
      index += 1;
    }
    if (cells.some((cell) => cell.trim())) {
      rows.push(`|${cells.join("|")}|`);
    }
    if (tokens[index]?.trim() === "") index += 1;
  }
  return rows.length > 1 ? rows : [row];
}

function renderPlainMarkdown(markdown) {
  const lines = normalizeMarkdownText(markdown).split(/\r?\n/);
  const html = [];
  let inList = false;
  let tableRows = [];
  const isTableLine = (line) => {
    if (!line.includes("|") || line.split("|").length < 3) return false;
    if (/^(#{1,6})\s+/.test(line) || /^[-*]\s+/.test(line) || /^\d+[.)]\s+/.test(line)) return false;
    return line.startsWith("|") || /^[A-Za-z0-9$%().,/&+\-\s]+?\s*\|/.test(line);
  };

  const flushList = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };
  const flushTable = () => {
    if (!tableRows.length) return;
    const rows = tableRows.filter((row) => !/^\s*\|?\s*:?-{3,}:?\s*\|/.test(row.replace(/\s*\|\s*/g, "|")));
    if (rows.length === 1) {
      html.push(`<p>${inlineMarkdown(rows[0].replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim()).filter(Boolean).join(" - "))}</p>`);
    } else if (rows.length) {
      const maxCells = Math.max(
        1,
        ...rows.map((row) => row.replace(/^\|/, "").replace(/\|$/, "").split("|").length),
      );
      html.push(`<div class="markdown-table-scroll" style="--markdown-table-cols: ${maxCells}"><table>`);
      rows.forEach((row, index) => {
        const cells = row.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => `<${index === 0 ? "th" : "td"}>${inlineMarkdown(cell.trim())}</${index === 0 ? "th" : "td"}>`);
        html.push(`<tr>${cells.join("")}</tr>`);
      });
      html.push("</table></div>");
    }
    tableRows = [];
  };

  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      flushTable();
      return;
    }
    if (isTableLine(trimmed)) {
      flushList();
      tableRows.push(...splitCompactTableRows(trimmed));
      return;
    }
    flushTable();
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/) || trimmed.match(/^(\d+)\.\s+([^:]{2,80}):?\s*$/);
    if (heading) {
      flushList();
      html.push(`<h4>${inlineMarkdown(heading[2])}</h4>`);
      return;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${inlineMarkdown(bullet[1])}</li>`);
      return;
    }
    flushList();
    html.push(`<p>${inlineMarkdown(trimmed)}</p>`);
  });
  flushList();
  flushTable();
  return html.join("");
}

function bindEvents() {
  $("searchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    hideSearchSuggestions();
    runSearch($("searchInput").value, 0, "");
  });
  $("searchInput").addEventListener("input", () => {
    updateSearchHintVisibility();
    state.activeSuggestionIndex = -1;
    scheduleSearchSuggestions();
  });
  $("searchInput").addEventListener("focus", () => {
    updateSearchHintVisibility();
    scheduleSearchSuggestions();
  });
  $("searchInput").addEventListener("blur", () => {
    updateSearchHintVisibility();
    window.setTimeout(hideSearchSuggestions, 140);
  });
  $("searchInput").addEventListener("keydown", handleSearchSuggestionKeys);
  $("chartLabTicker").addEventListener("change", renderChartLabIdle);
  $("chartLabChart").addEventListener("change", renderChartLabIdle);
  $("chartLabWindow").addEventListener("change", renderChartLabIdle);
  $("buildChartLab").addEventListener("click", loadChartLab);
  $("collapsePortfolio").addEventListener("click", () => {
    const panel = $("portfolioPanel");
    const collapsed = panel.classList.toggle("is-collapsed");
    $("collapsePortfolio").setAttribute("aria-expanded", String(!collapsed));
    $("collapsePortfolio").textContent = collapsed ? "+" : "-";
  });
  $("myVibeButton").addEventListener("click", () => {
    if ($("myVibeView").classList.contains("hidden")) showMyVibe();
    else showHome();
  });
  $("myHomeButton").addEventListener("click", showHome);
  document.querySelectorAll("[data-vibe-mode]").forEach((button) => {
    button.addEventListener("click", () => setVibeMode(button.dataset.vibeMode));
  });
  $("runPortfolioAnalysis").addEventListener("click", analyzePortfolioTicker);
  $("settingsButton").addEventListener("click", () => $("settingsModal").classList.remove("hidden"));
  $("closeSettings").addEventListener("click", () => $("settingsModal").classList.add("hidden"));
  $("saveLlmKey").addEventListener("click", saveLlmKeyForSession);
  $("clearLlmKey").addEventListener("click", clearLlmKey);
  $("llmProvider").addEventListener("change", applyLlmProviderSelection);
  $("llmApiKey").addEventListener("input", maybeAutoSelectProviderForKey);
  $("addPortfolioRow").addEventListener("click", () => {
    state.settings.portfolio.push({ ticker: state.allowedTickers[0]?.ticker || "AAPL", purchase_price: "", quantity: "" });
    renderSettings();
  });
  $("addFavorite").addEventListener("click", addFavoriteWebsite);
  $("saveSettings").addEventListener("click", saveSettings);
  document.addEventListener("change", (event) => {
    const windowSelect = event.target.closest("[data-folder-window]");
    if (!windowSelect) return;
    const folderKey = windowSelect.dataset.folderWindow;
    const panel = document.querySelector(`[data-folder-analysis-panel="${CSS.escape(folderKey)}"]`);
    if (panel && !panel.classList.contains("hidden") && panel.innerHTML.trim()) {
      performFolderAnalysis(folderKey);
    }
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest("a.document-link")) return;
    const suggestion = event.target.closest("[data-search-suggestion]");
    if (suggestion) {
      acceptSearchSuggestion(suggestion.dataset.searchSuggestion);
      return;
    }
    const heart = event.target.closest("[data-heart-url]");
    if (heart) {
      toggleFavorite(heart.dataset.heartUrl);
      return;
    }
    const groupMore = event.target.closest("[data-group-more]");
    if (groupMore) {
      const key = groupMore.dataset.groupMore;
      const panel = document.querySelector(`[data-group-children="${key}"]`);
      if (panel) {
        const hidden = panel.classList.toggle("hidden");
        groupMore.textContent = hidden ? "Show more" : "Hide older documents";
      }
      return;
    }
    const groupAll = event.target.closest("[data-group-all]");
    if (groupAll) {
      runSearch(state.lastQuery, 0, groupAll.dataset.groupAll, state.searchFolderKey);
      return;
    }
    const groupBack = event.target.closest("[data-group-back]");
    if (groupBack) {
      runSearch(state.lastQuery, 0, "", state.searchFolderKey);
      return;
    }
    const folderMore = event.target.closest("[data-folder-more]");
    if (folderMore) {
      const key = folderMore.dataset.folderMore;
      const panel = document.querySelector(`[data-folder-children="${key}"]`);
      if (panel) {
        const hidden = panel.classList.toggle("hidden");
        folderMore.textContent = hidden ? "Open folder" : "Close folder";
      }
      return;
    }
    const folderAll = event.target.closest("[data-folder-all]");
    if (folderAll) {
      runSearch(state.lastQuery, 0, "", folderAll.dataset.folderAll);
      return;
    }
    const folderBack = event.target.closest("[data-folder-back]");
    if (folderBack) {
      runSearch(state.lastQuery, 0, "", "");
      return;
    }
    const folderAnalysis = event.target.closest("[data-folder-analysis]");
    if (folderAnalysis) {
      performFolderAnalysis(folderAnalysis.dataset.folderAnalysis);
      return;
    }
    const graphAnalysis = event.target.closest("[data-graph-analysis]");
    if (graphAnalysis) {
      performChartGraphAnalysis();
      return;
    }
    const suggestedChart = event.target.closest("[data-suggested-chart]");
    if (suggestedChart) {
      openSuggestedChart(suggestedChart.dataset.suggestedChart, suggestedChart);
      return;
    }
    const addPortfolioChart = event.target.closest("[data-add-portfolio-chart]");
    if (addPortfolioChart) {
      addPortfolioAnalysisChart();
      return;
    }
    const signalTicker = event.target.closest("[data-signal-ticker]");
    if (signalTicker) {
      const ticker = signalTicker.dataset.signalTicker || "";
      $("searchInput").value = ticker;
      updateSearchHintVisibility();
      runSearch(ticker, 0, "");
      return;
    }
    const delPortfolio = event.target.closest("[data-delete-portfolio]");
    if (delPortfolio) {
      state.settings.portfolio.splice(Number(delPortfolio.dataset.deletePortfolio), 1);
      renderSettings();
      return;
    }
    const delFavorite = event.target.closest("[data-delete-favorite]");
    if (delFavorite) {
      state.settings.favorite_websites.splice(Number(delFavorite.dataset.deleteFavorite), 1);
      renderSettings();
      return;
    }
    const uploadMore = event.target.closest("#uploadMorePosts");
    if (uploadMore) {
      uploadMoreVibePosts(uploadMore.dataset.site);
      return;
    }
    const site = event.target.closest("[data-site]");
    if (site) {
      loadVibePosts(site.dataset.site);
      return;
    }
    const post = event.target.closest("[data-post-id]");
    if (post) {
      analyzePost(post.dataset.postId);
      return;
    }
    const pageButton = event.target.closest("[data-page-offset]");
    if (pageButton) runSearch(state.lastQuery, Number(pageButton.dataset.pageOffset || 0), state.searchGroupKey, state.searchFolderKey);
  });
}

async function init() {
  initCosmicBackdrop();
  bindEvents();
  loadChartLabOptions().catch((error) => {
    $("chartLabCanvas").innerHTML = `<div class="chart-lab-empty">${escapeHtml(error.message || "Chart options failed to load.")}</div>`;
  });
  await loadDashboard();
}

init().catch((error) => {
  console.error(error);
  $("searchResults").innerHTML = `<article class="result-card"><p>${escapeHtml(error.message)}</p></article>`;
});
