import type { Config } from "@netlify/functions";
import { readSnapshot, runDailyScreen } from "../lib/screening.mjs";

/*
  Serves the stored day-over-day snapshot to the dashboard. The scheduled
  screening run owns the write; this endpoint only seeds the key the first
  time it is requested, so the table is never empty before the first cron.
*/
export default async () => {
  try {
    const snapshot = (await readSnapshot()) ?? (await runDailyScreen());
    return Response.json(snapshot, {
      headers: { "Cache-Control": "public, max-age=0, must-revalidate" },
    });
  } catch (error) {
    console.error("Unable to load snapshot", error);
    return Response.json(
      { error: "Snapshot is temporarily unavailable." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
};

export const config: Config = {
  path: "/api/snapshot",
  method: "GET",
};
