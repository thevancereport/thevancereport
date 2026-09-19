/*
  On-demand screen for a single ticker: GET /api/evaluate?symbol=XYZ

  This exists because the browser cannot do it. Nasdaq serves no CORS headers,
  so a page on thevancereport.com cannot call it directly; the lookup has to
  happen server-side.

  It applies exactly the conditions in scripts/update_research.py. If the two
  ever disagree, the published list and this tool would grade the same company
  differently, which is worse than not having the tool.

  Results are cached in memory for an hour. A warm instance answers a repeated
  symbol without touching SEC or Nasdaq again, which matters because this is a
  public endpoint and the SEC asks that automated clients be considerate.
*/

const CASH_GATE = 30.0;
const DROP_THRESHOLD = -15.0;
const MIN_RUNWAY_YEARS = 2.0;
const MAX_DILUTION_PCT = 25.0;
const MIN_MARKET_CAP = 50_000_000;
const MIN_DOLLAR_VOLUME = 250_000;

const SEC_UA = "TheVanceReport/1.0 (contact: ardenkvance@gmail.com)";
const BROWSER_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36";

const CACHE_MS = 60 * 60 * 1000;
const cache = new Map();
let tickerMap = null;
let tickerMapAt = 0;

function json(body, status = 200) {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "public, max-age=900",
      "Access-Control-Allow-Origin": "*",
    },
  });
}

async function getJson(url, userAgent) {
  const res = await fetch(url, {
    headers: { "User-Agent": userAgent, Accept: "application/json" },
  });
  if (!res.ok) return null;
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function money(text) {
  if (text === null || text === undefined) return null;
  if (typeof text === "number") return isFinite(text) ? text : null;
  const cleaned = String(text).replace(/[$,%\s]/g, "");
  if (!cleaned || cleaned === "--" || cleaned === "N/A") return null;
  const n = parseFloat(cleaned);
  return isFinite(n) ? n : null;
}

async function cikFor(symbol) {
  if (!tickerMap || Date.now() - tickerMapAt > 24 * CACHE_MS) {
    const data = await getJson("https://www.sec.gov/files/company_tickers.json", SEC_UA);
    if (!data) return null;
    tickerMap = new Map();
    for (const row of Object.values(data)) {
      if (row && row.ticker) tickerMap.set(String(row.ticker).toUpperCase(), row.cik_str);
    }
    tickerMapAt = Date.now();
  }
  return tickerMap.get(symbol) ?? null;
}

/*
  Latest reported value for one XBRL concept.

  Several tags are tried because companies do not all use the same one -- a
  company filing only CashAndCashEquivalentsAtCarryingValue and one filing only
  CashCashEquivalentsRestrictedCash... are both saying "cash".
*/
async function concept(cik, tags, unit = "USD", taxonomy = "us-gaap") {
  const padded = String(cik).padStart(10, "0");
  for (const tag of tags) {
    const data = await getJson(
      "https://data.sec.gov/api/xbrl/companyconcept/CIK" + padded + "/" + taxonomy + "/" + tag + ".json",
      SEC_UA,
    );
    const points = data?.units?.[unit];
    if (!points || !points.length) continue;
    const sorted = [...points].sort((a, b) => String(a.end).localeCompare(String(b.end)));
    const latest = sorted[sorted.length - 1];
    if (latest && typeof latest.val === "number") {
      return { value: latest.val, end: latest.end, tag };
    }
  }
  return null;
}

async function annualFlow(cik, tags) {
  const padded = String(cik).padStart(10, "0");
  for (const tag of tags) {
    const data = await getJson(
      "https://data.sec.gov/api/xbrl/companyconcept/CIK" + padded + "/us-gaap/" + tag + ".json",
      SEC_UA,
    );
    const points = data?.units?.USD;
    if (!points) continue;
    // Annual periods only: roughly a year between start and end.
    const yearly = points.filter((p) => {
      if (!p.start || !p.end) return false;
      const days = (Date.parse(p.end) - Date.parse(p.start)) / 86400000;
      return days > 300 && days < 400;
    });
    if (!yearly.length) continue;
    yearly.sort((a, b) => String(a.end).localeCompare(String(b.end)));
    const latest = yearly[yearly.length - 1];
    if (latest && typeof latest.val === "number") {
      return { value: latest.val, end: latest.end, tag };
    }
  }
  return null;
}

async function priorShares(cik) {
  const padded = String(cik).padStart(10, "0");
  const data = await getJson(
    "https://data.sec.gov/api/xbrl/companyconcept/CIK" + padded + "/dei/EntityCommonStockSharesOutstanding.json",
    SEC_UA,
  );
  const points = data?.units?.shares;
  if (!points || points.length < 2) return null;
  const sorted = [...points].sort((a, b) => String(a.end).localeCompare(String(b.end)));
  const latest = sorted[sorted.length - 1];
  const target = Date.parse(latest.end) - 365 * 86400000;
  let best = null;
  for (const p of sorted) {
    const gap = Math.abs(Date.parse(p.end) - target);
    if (gap < 100 * 86400000 && (!best || gap < best.gap)) best = { point: p, gap };
  }
  return best
    ? { current: latest.val, prior: best.point.val, priorEnd: best.point.end }
    : { current: latest.val, prior: null, priorEnd: null };
}

async function fiveDayDrop(symbol) {
  const day = 86400000;
  const to = new Date().toISOString().slice(0, 10);
  const from = new Date(Date.now() - 20 * day).toISOString().slice(0, 10);
  const data = await getJson(
    "https://api.nasdaq.com/api/quote/" + encodeURIComponent(symbol) + "/historical" +
      "?assetclass=stocks&fromdate=" + from + "&todate=" + to + "&limit=30",
    BROWSER_UA,
  );
  const rows = data?.data?.tradesTable?.rows;
  if (!rows || rows.length < 6) return null;
  const closes = rows.map((r) => money(r.close)).filter((v) => v !== null);
  if (closes.length < 6 || !closes[5]) return null;
  return Math.round(((closes[0] - closes[5]) / closes[5]) * 1000) / 10;
}

/*
  Grades.

  pass     every condition met
  neutral  the cash cushion holds but something else does not, or a figure
           could not be read from filings
  fail     the cash cushion itself is not met

  The cushion is the screen's central test, so failing it is a different kind
  of answer from missing a supporting condition. Unknown data never produces a
  fail: an absent filing is not evidence against a company.
*/
function grade(conditions) {
  const cushion = conditions.find((c) => c.key === "cushion");
  if (cushion && cushion.met === false) return "fail";
  if (conditions.some((c) => c.met === false)) return "neutral";
  if (conditions.some((c) => c.met === null && c.required)) return "neutral";
  return "pass";
}

export default async function handler(request) {
  const url = new URL(request.url);
  const symbol = (url.searchParams.get("symbol") || "").trim().toUpperCase();

  if (!/^[A-Z]{1,6}$/.test(symbol)) {
    return json({ error: "Enter a US ticker symbol, letters only." }, 400);
  }

  const hit = cache.get(symbol);
  if (hit && Date.now() - hit.at < CACHE_MS) {
    return json({ ...hit.body, cached: true });
  }

  const quote = await getJson(
    "https://api.nasdaq.com/api/quote/" + encodeURIComponent(symbol) + "/info?assetclass=stocks",
    BROWSER_UA,
  );
  const info = quote?.data;
  const price = money(info?.primaryData?.lastSalePrice);
  if (!info || !price) {
    return json({ error: "No listed US common stock found for " + symbol + "." }, 404);
  }

  const name = info.companyName || symbol;
  if (!/common stock|ordinary share/i.test(name)) {
    return json({
      error: symbol + " is " + name + ". The screen covers common and ordinary shares only -- warrants, units, preferreds and notes carry their issuer's balance sheet but not its cash.",
    }, 422);
  }

  const cik = await cikFor(symbol);
  if (!cik) {
    return json({ error: symbol + " has no SEC filer record, so its balance sheet cannot be read." }, 404);
  }

  const [cash, liabilities, burn, shares, drop] = await Promise.all([
    concept(cik, ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    concept(cik, ["Liabilities"]),
    annualFlow(cik, ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    priorShares(cik),
    fiveDayDrop(symbol),
  ]);

  const shareCount = shares?.current ?? null;
  const netCash = cash && liabilities ? cash.value - liabilities.value : null;
  const cps = netCash !== null && shareCount ? netCash / shareCount : null;
  const cushionPct = cps !== null && price ? (cps / price) * 100 : null;

  let runway = null;
  if (burn && netCash !== null) {
    runway = burn.value >= 0 ? Infinity : netCash / Math.abs(burn.value);
  }

  const dilution =
    shares?.prior && shares.prior > 0
      ? ((shares.current - shares.prior) / shares.prior) * 100
      : null;

  const marketCap = shareCount && price ? shareCount * price : null;
  const volume = money(info?.keyStats?.Volume?.value);
  const turnover = volume && price ? volume * price : null;

  const conditions = [
    {
      key: "drop",
      name: "Five-day move",
      rule: "must have fallen 15% or more",
      value: drop === null ? null : drop.toFixed(1) + "%",
      met: drop === null ? null : drop <= DROP_THRESHOLD,
      required: true,
    },
    {
      key: "cushion",
      name: "Net cash cushion",
      rule: "cash less total liabilities, per share, at least 30% of price",
      value: cushionPct === null ? null : cushionPct.toFixed(1) + "%",
      met: cushionPct === null ? null : cushionPct >= CASH_GATE,
      required: true,
    },
    {
      key: "balance",
      name: "Balance sheet per share",
      rule:
        cash && liabilities && shareCount
          ? "cash $" + (cash.value / shareCount).toFixed(2) + " less liabilities $" + (liabilities.value / shareCount).toFixed(2)
          : "from the most recent filing",
      value: cps === null ? null : "$" + cps.toFixed(2) + " net",
      met: null,
      required: false,
    },
    {
      key: "runway",
      name: "Runway",
      rule: "net cash over annual operating burn, two years or more",
      value: runway === null ? null : runway === Infinity ? "cash generative" : runway.toFixed(1) + " years",
      met: runway === null ? null : runway === Infinity ? true : runway >= MIN_RUNWAY_YEARS,
      required: false,
    },
    {
      key: "dilution",
      name: "Share issuance",
      rule: "share count against a year earlier, 25% or less",
      value: dilution === null ? null : (dilution >= 0 ? "+" : "") + dilution.toFixed(1) + "%",
      met: dilution === null ? null : dilution <= MAX_DILUTION_PCT,
      required: false,
    },
    {
      key: "size",
      name: "Market capitalisation",
      rule: "$50m or more",
      value: marketCap === null ? null : "$" + Math.round(marketCap / 1e6).toLocaleString() + "m",
      met: marketCap === null ? null : marketCap >= MIN_MARKET_CAP,
      required: false,
    },
    {
      key: "liquidity",
      name: "Daily turnover",
      rule: "price times volume, $250,000 or more",
      value: turnover === null ? null : "$" + Math.round(turnover).toLocaleString(),
      met: turnover === null ? null : turnover >= MIN_DOLLAR_VOLUME,
      required: false,
    },
  ];

  const body = {
    symbol,
    name: name.replace(/\s+Common Stock$/i, "").trim(),
    price,
    grade: grade(conditions),
    conditions,
    asOf: {
      cash: cash?.end ?? null,
      liabilities: liabilities?.end ?? null,
      burn: burn?.end ?? null,
      shares: shares?.priorEnd ?? null,
    },
    note:
      "The same conditions the daily screen applies, evaluated on request. " +
      "Balance sheet figures come from this company's own SEC filings and lag " +
      "the filing date. This reports whether stated conditions are met. It is " +
      "not a recommendation.",
  };

  cache.set(symbol, { at: Date.now(), body });
  return json(body);
}

export const config = { path: "/api/evaluate" };
