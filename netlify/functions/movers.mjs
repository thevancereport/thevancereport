/*
  GET /api/movers

  Today's US market movers for the "US movers" panel on the home page, from
  Nasdaq's public market-movers endpoint (the same source as /api/quote).
  It replaces a TradingView widget that often showed a single row.

  Four lists, up to 8 rows each:
    active   most active by dollar volume
    gainers  biggest % gains
    losers   biggest % losses
    ndx      Nasdaq-100 movers
  Gainers and losers drop warrants, units, rights and anything under $2, which
  otherwise fill the list. Context only: nothing here feeds the Vance ranking.
  Cached a minute at the edge, so visitors share one upstream request.
*/

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36";
const URL_ =
  "https://api.nasdaq.com/api/marketmovers?assetclass=stocks&exchangestatus=currentMarket&limit=20";
const EDGE_CACHE = "public, max-age=30, s-maxage=60, stale-while-revalidate=300";
const MAX = 8;

const num = (s) => {
  if (typeof s === "number") return Number.isFinite(s) ? s : null;
  if (typeof s !== "string") return null;
  const v = Number(s.replace(/[$,%\s]/g, "").replace(/^\+/, ""));
  return Number.isFinite(v) ? v : null;
};

function reply(body, status = 200, cache = "no-store") {
  return Response.json(body, { status, headers: { "Cache-Control": cache } });
}

function rows(block, pctFromChangeColumn) {
  const list = (block && block.table && block.table.rows) || [];
  return list.map((r) => {
    const price = num(r.lastSalePrice);
    const change = num(r.lastSaleChange);
    let pct = pctFromChangeColumn ? num(r.change) : null;
    if (pct == null && price != null && change != null && price - change !== 0) {
      pct = (change / (price - change)) * 100;
    }
    return {
      symbol: String(r.symbol || "").toUpperCase(),
      name: String(r.name || "").replace(/\s+(Class [A-Z] )?(Common Stock|Ordinary Shares|Common Shares).*$/i, ""),
      price,
      change,
      change_pct: pct == null ? null : Math.round(pct * 100) / 100,
    };
  }).filter((r) => r.symbol && r.price != null);
}

// Warrants, units and rights (e.g. ABCDW, ABCDU, ABCDR), symbols with dots, and pennies.
const clean = (r) =>
  /^[A-Z]{1,5}$/.test(r.symbol) &&
  !(r.symbol.length === 5 && /[WUR]$/.test(r.symbol)) &&
  r.price >= 2;

export default async function handler(request) {
  if (request.method !== "GET") return reply({ error: "Method not allowed." }, 405);
  try {
    // Nasdaq's own cache can serve yesterday's lists for a given URL; a per-minute
    // marker in the query gets the current ones.
    const res = await fetch(URL_ + "&t=" + Math.floor(Date.now() / 60000), { headers: { "User-Agent": UA, Accept: "application/json" } });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const body = await res.json();
    const s = body && body.data && body.data.STOCKS;
    if (!s) throw new Error("no data");
    const out = {
      as_of: (s.MostActiveByDollarVolume && s.MostActiveByDollarVolume.dataAsOf) || null,
      active: rows(s.MostActiveByDollarVolume, true).slice(0, MAX),
      gainers: rows(s.MostAdvanced, true).filter(clean).slice(0, MAX),
      losers: rows(s.MostDeclined, true).filter(clean).slice(0, MAX),
      ndx: rows(s.Nasdaq100Movers, true).slice(0, MAX),
      source: "Nasdaq",
    };
    return reply(out, 200, EDGE_CACHE);
  } catch (err) {
    console.error("movers:", err);
    return reply({ error: "Movers are unavailable right now." }, 502);
  }
}

export const config = { path: "/api/movers" };
