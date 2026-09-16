import { getDatabase } from "@netlify/database";

const criteria = ["useful_signal", "clear_analysis", "actionable_format"];
const allowed = new Set(["like", "needs_work"]);

function response(body, status = 200) {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

export default async function handler(request) {
  const db = getDatabase();

  if (request.method === "GET") {
    const url = new URL(request.url);
    const dashboard = url.searchParams.get("view") === "dashboard";
    if (dashboard) {
      const rows = await db.sql`
        SELECT id, useful_signal, clear_analysis, actionable_format, comment, created_at,
          ((useful_signal = 'like')::int + (clear_analysis = 'like')::int + (actionable_format = 'like')::int) AS positive_score
        FROM reviews ORDER BY created_at DESC LIMIT 100
      `;
      return response({ reviews: rows });
    }

    const rows = await db.sql`
      SELECT comment, created_at,
        ((useful_signal = 'like')::int + (clear_analysis = 'like')::int + (actionable_format = 'like')::int) AS positive_score
      FROM reviews
      WHERE ((useful_signal = 'like')::int + (clear_analysis = 'like')::int + (actionable_format = 'like')::int) >= 2
      ORDER BY positive_score DESC, created_at DESC LIMIT 3
    `;
    return response({ reviews: rows });
  }

  if (request.method === "POST") {
    let body;
    try {
      body = await request.json();
    } catch {
      return response({ error: "Invalid request." }, 400);
    }
    if (criteria.some((key) => !allowed.has(body[key]))) {
      return response(
        { error: "Choose Like or Needs work for every item." },
        400,
      );
    }
    const comment = typeof body.comment === "string" ? body.comment.trim() : "";
    if (comment.length < 2 || comment.length > 1000) {
      return response(
        { error: "Comment must be between 2 and 1,000 characters." },
        400,
      );
    }
    const [review] = await db.sql`
      INSERT INTO reviews (useful_signal, clear_analysis, actionable_format, comment)
      VALUES (${body.useful_signal}, ${body.clear_analysis}, ${body.actionable_format}, ${comment})
      RETURNING id, created_at
    `;
    return response({ review }, 201);
  }

  return response({ error: "Method not allowed." }, 405);
}

export const config = { path: "/api/reviews" };
