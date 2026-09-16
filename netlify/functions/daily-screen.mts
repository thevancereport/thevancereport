import type { Config } from "@netlify/functions";
import { runDailyScreen } from "../lib/screening.mjs";

/*
  Scheduled screening run. Fires after the US close so `currentPrice` is that
  session's settled price, which the next run consumes as its baseline.
*/
export default async () => {
  const snapshot = await runDailyScreen();
  console.log(
    `Screen stored for ${snapshot.tradingDay}: ${snapshot.totals.screened} screened, ` +
      `${snapshot.totals.pass} pass, ${snapshot.totals.fail} fail ` +
      `(baseline: ${snapshot.previousTradingDay ?? "previous exchange close"}).`,
  );
  if (snapshot.skipped.length) {
    console.log(
      `Excluded: ${snapshot.skipped.map((row) => `${row.ticker} (${row.reason})`).join(", ")}`,
    );
  }
};

export const config: Config = {
  schedule: "0 22 * * *",
};
