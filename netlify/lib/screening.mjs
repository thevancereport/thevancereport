import { getStore } from "@netlify/blobs";

/*
  Day-over-day screening engine for The Vance Report.

  One snapshot per trading session is persisted to Netlify Blobs under
  `snapshot-latest.json`. Each run reads the previous snapshot BEFORE writing
  the new one, so today's close can be compared against the price recorded in
  the last session's snapshot:

    dollarChange = price_today - price_yesterday
    returnPct    = ((price_today - price_yesterday) / price_yesterday) * 100
*/

export const SNAPSHOT_KEY = "snapshot-latest.json";
export const CASH_THRESHOLD = 30.0;

const STORE_NAME = "vance-report-snapshots";
const QUOTE_BASE = "https://finnhub.io/api/v1";
const REQUEST_TIMEOUT_MS = 8000;

/* Ticker metadata only. Every price, cash-per-share figure and gatekeeper
   verdict is resolved live at run time — nothing below is a quoted value. */
const WATCHLIST = [
  { ticker: "BMEA", name: "Biomea Fusion", exchange: "NASDAQ" },
  { ticker: "SEDG", name: "SolarEdge Technologies", exchange: "NASDAQ" },
  { ticker: "PAGS", name: "PagSeguro Digital", exchange: "NYSE" },
  { ticker: "RUN", name: "Sunrun Inc", exchange: "NASDAQ" },
  { ticker: "LUMN", name: "Lumen Technologies", exchange: "NYSE" },
  { ticker: "PLUG", name: "Plug Power", exchange: "NASDAQ" },
  { ticker: "WOLF", name: "Wolfspeed Inc", exchange: "NYSE" },
  { ticker: "FSRN", name: "Fisker Liquidation", exchange: "NYSE" },
  { ticker: "UPST", name: "Upstart Holdings", exchange: "NASDAQ" },
  { ticker: "CHWY", name: "Chewy Inc", exchange: "NYSE" },
];

/* Site-level store: the snapshot has to survive redeploys, otherwise every
   deploy would reset the day-over-day baseline. Strong consistency because
   each run reads the key it is about to overwrite. */
export function getSnapshotStore() {
  return getStore({ name: STORE_NAME, consistency: "strong" });
}

export async function readSnapshot() {
  const snapshot = await getSnapshotStore().get(SNAPSHOT_KEY, { type: "json" });
  return snapshot ?? null;
}

function round(value, places) {
  if (!Number.isFinite(value)) return null;
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

function isPrice(value) {
  return Number.isFinite(value) && value > 0;
}

function utcDay(timestampMs) {
  return new Date(timestampMs).toISOString().slice(0, 10);
}

async function getJSON(path, token) {
  const response = await fetch(`${QUOTE_BASE}${path}&token=${token}`, {
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Market data request failed (${response.status})`);
  }
  return response.json();
}

/* Latest trade price plus the exchange's own previous close, which seeds the
   comparison on the very first run when no snapshot exists yet. */
async function fetchQuote(ticker, token) {
  const quote = await getJSON(
    `/quote?symbol=${encodeURIComponent(ticker)}`,
    token,
  );
  return {
    price: Number(quote?.c),
    previousClose: Number(quote?.pc),
    tradedAt: Number(quote?.t) > 0 ? Number(quote.t) * 1000 : null,
  };
}

/* Cash per share from the most recently reported balance sheet. */
async function fetchCashPerShare(ticker, token) {
  const body = await getJSON(
    `/stock/metric?symbol=${encodeURIComponent(ticker)}&metric=all`,
    token,
  );
  const metric = body?.metric ?? {};
  const candidates = [
    metric.cashPerSharePerShareQuarterly,
    metric.cashPerSharePerShareAnnual,
  ];
  for (const value of candidates) {
    if (Number.isFinite(Number(value)) && Number(value) > 0) {
      return Number(value);
    }
  }
  return null;
}

async function screenTicker(entry, token, previousEntry) {
  const [quote, cashPerShare] = await Promise.all([
    fetchQuote(entry.ticker, token),
    fetchCashPerShare(entry.ticker, token).catch(() => null),
  ]);

  if (!isPrice(quote.price)) {
    return { ticker: entry.ticker, skipped: true, reason: "no live quote" };
  }

  /* Fall back to the last known balance-sheet figure if the fundamentals
     call is unavailable this session. */
  const cps = Number.isFinite(cashPerShare)
    ? cashPerShare
    : (previousEntry?.cashPerShare ?? null);
  const cashToPrice = isPrice(cps) ? (cps / quote.price) * 100 : null;

  return {
    ticker: entry.ticker,
    name: entry.name,
    exchange: entry.exchange,
    currentPrice: round(quote.price, 4),
    previousClose: isPrice(quote.previousClose)
      ? round(quote.previousClose, 4)
      : null,
    cashPerShare: isPrice(cps) ? round(cps, 4) : null,
    cashToPrice: cashToPrice === null ? null : round(cashToPrice, 2),
    gatekeeper:
      cashToPrice === null
        ? "UNKNOWN"
        : cashToPrice >= CASH_THRESHOLD
          ? "PASS"
          : "FAIL",
    tradedAt: quote.tradedAt,
  };
}

/*
  Resolve the price each candidate is measured against.

  A fresh trading session promotes the previous snapshot's price to today's
  baseline. Re-running within the same session (a manual trigger, or a
  weekend/holiday run where the exchange still reports the last close) keeps
  the baseline already on file, so repeat runs can never flatten the delta
  to zero by comparing today against itself.
*/
function resolveYesterdayPrice(candidate, previousEntry, isNewSession) {
  if (previousEntry) {
    const carried = isNewSession
      ? previousEntry.currentPrice
      : previousEntry.yesterdayPrice;
    if (isPrice(carried)) return { price: carried, source: "snapshot" };
  }
  if (isPrice(candidate.previousClose)) {
    return { price: candidate.previousClose, source: "previous-close" };
  }
  return { price: null, source: "unavailable" };
}

/*
  Run the screen, compare it against `snapshot-latest.json`, then overwrite
  that key with today's figures. Returns the snapshot that was stored.
*/
export async function runDailyScreen() {
  const token = process.env.FINNHUB_API_KEY;
  if (!token) {
    throw new Error("FINNHUB_API_KEY is not configured for this site.");
  }

  const store = getSnapshotStore();
  const previous = await store.get(SNAPSHOT_KEY, { type: "json" });
  const previousByTicker = new Map(
    (previous?.candidates ?? []).map((row) => [row.ticker, row]),
  );

  const results = await Promise.all(
    WATCHLIST.map((entry) =>
      screenTicker(entry, token, previousByTicker.get(entry.ticker)).catch(
        (error) => ({
          ticker: entry.ticker,
          skipped: true,
          reason: error.message,
        }),
      ),
    ),
  );

  const screened = results.filter((row) => !row.skipped);
  const skipped = results.filter((row) => row.skipped);

  /* Anchor the session to the exchange's last trade date so a weekend run
     is not mistaken for a new trading day. */
  const tradedAt = screened
    .map((row) => row.tradedAt)
    .filter((value) => Number.isFinite(value));
  const tradingDay = tradedAt.length
    ? utcDay(Math.max(...tradedAt))
    : utcDay(Date.now());
  const isNewSession = previous?.tradingDay !== tradingDay;

  const candidates = screened.map((candidate) => {
    const previousEntry = previousByTicker.get(candidate.ticker);
    const baseline = resolveYesterdayPrice(
      candidate,
      previousEntry,
      isNewSession,
    );
    const yesterdayPrice = baseline.price;
    const hasBaseline = isPrice(yesterdayPrice);
    const dollarChange = hasBaseline
      ? candidate.currentPrice - yesterdayPrice
      : null;

    const { tradedAt: _tradedAt, ...stored } = candidate;
    return {
      ...stored,
      yesterdayPrice: hasBaseline ? round(yesterdayPrice, 4) : null,
      dollarChange: dollarChange === null ? null : round(dollarChange, 4),
      returnPct:
        dollarChange === null
          ? null
          : round((dollarChange / yesterdayPrice) * 100, 2),
      baselineSource: baseline.source,
    };
  });

  /* Deepest cash cushion first, mirroring the published screen order. */
  candidates.sort((a, b) => (b.cashToPrice ?? -1) - (a.cashToPrice ?? -1));

  const snapshot = {
    asOf: new Date().toISOString(),
    tradingDay,
    previousTradingDay: previous?.tradingDay ?? null,
    cashThreshold: CASH_THRESHOLD,
    totals: {
      screened: candidates.length,
      pass: candidates.filter((row) => row.gatekeeper === "PASS").length,
      fail: candidates.filter((row) => row.gatekeeper === "FAIL").length,
    },
    candidates,
    skipped,
  };

  await store.setJSON(SNAPSHOT_KEY, snapshot);
  return snapshot;
}
