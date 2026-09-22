/*
  GET /api/quote?symbol=YELP

  The last trade for one symbol, from Nasdaq's public quote endpoint, for the
  "Last trade" tile on stock.html. It is context only: every score, rank and
  figure on the site is built from the closing price of the screen date, which
  is shown in the tile beside it.

  Nasdaq refuses requests without a browser-like User-Agent, and its response is
  strings with dollar signs and percent signs, so both are handled here rather
  than in the page. Answers are cached for a minute at the edge: a page reload
  should not mean another request upstream.
*/

const EDGE_CACHE = "public, max-age=30, s-maxage=60, stale-while-revalidate=300";
const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36";

function reply(body, status = 200, cache = "no-store") {
  return Response.json(body, { status, headers: { "Cache-Control": cache } });
}

const num = (s) => {
  if (typeof s === "number") return Number.isFinite(s) ? s : null;
  if (typeof s !== "string") return null;
  const v = Number(s.replace(/[$,%\s]/g, "").replace(/^\+/, ""));
  return Number.isFinite(v) ? v : null;
};

async function ask(symbol, assetclass) {
  const url =
    "https://api.nasdaq.com/api/quote/" + encodeURIComponent(symbol) +
    "/info?assetclass=" + assetclass;
  const res = await fetch(url, {
    headers: { "User-Agent": UA, Accept: "application/json" },
  });
  if (!res.ok) return null;
  const body = await res.json();
  const data = body && body.data;
  const p = data && data.primaryData;
  if (!p) return null;
  const price = num(p.lastSalePrice);
  if (price == null) return null;
  return {
    symbol: (data.symbol || symbol).toUpperCase(),
    name: data.companyName || null,
    price,
    change: num(p.netChange),
    change_pct: num(p.percentageChange),
    as_of: p.lastTradeTimestamp || null,
    real_time: p.isRealTime === true,
    asset_class: assetclass,
    source: "Nasdaq",
  };
}

export default async function handler(request) {
  if (request.method !== "GET") return reply({ error: "Method not allowed." }, 405);

  const symbol = (new URL(request.url).searchParams.get("symbol") || "")
    .toUpperCase().replace(/[^A-Z.\-]/g, "").slice(0, 8);
  if (!symbol) return reply({ error: "A symbol is required." }, 400);

  for (const assetclass of ["stocks", "etf"]) {
    try {
      const quote = await ask(symbol, assetclass);
      if (quote) return reply(quote, 200, EDGE_CACHE);
    } catch (err) {
      console.error("quote: " + symbol + " (" + assetclass + ")", err);
    }
  }
  return reply({ error: "No quote for that symbol." }, 404);
}

export const config = { path: "/api/quote" };
