import { getDatabase } from "@netlify/database";

const criteria = [
  "data_freshness",
  "screen_filters",
  "site_layout",
  "visual_design",
  "info_quality",
];
const allowed = new Set(["good", "needs_work"]);

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
      // Admin-only: requires a shared secret set as the ADMIN_REVIEW_KEY
      // environment variable in Netlify, sent as the x-admin-key header.
      // Without this, anyone who finds ?view=dashboard could read every
      // raw comment and every "needs work" note, including ones you may
      // not want public.
      const providedKey = request.headers.get("x-admin-key");
      const expectedKey = process.env.ADMIN_REVIEW_KEY;
      if (!expectedKey || providedKey !== expectedKey) {
        return response({ error: "Unauthorized." }, 401);
      }

      const rows = await db.sql`
        SELECT
          id, data_freshness, screen_filters, site_layout, visual_design, info_quality,
          data_freshness_note, screen_filters_note, site_layout_note, visual_design_note, info_quality_note,
          comment, created_at,
          (
    (data_freshness = 'good')::int +
    (screen_filters = 'good')::int +
    (site_layout    = 'good')::int +
    (visual_design  = 'good')::int +
    (info_quality   = 'good')::int
  ) AS positive_score
        FROM reviews ORDER BY created_at DESC LIMIT 200
      `;
      return response({ reviews: rows });
    }

    // Public view: just the strongest, most recent written reviews — no
    // ratings breakdown, no notes, nothing that could read as internal
    // feedback. Used to populate the homepage testimonial spot.
    const rows = await db.sql`
      SELECT comment, created_at, (
    (data_freshness = 'good')::int +
    (screen_filters = 'good')::int +
    (site_layout    = 'good')::int +
    (visual_design  = 'good')::int +
    (info_quality   = 'good')::int
  ) AS positive_score
      FROM reviews
      WHERE (
    (data_freshness = 'good')::int +
    (screen_filters = 'good')::int +
    (site_layout    = 'good')::int +
    (visual_design  = 'good')::int +
    (info_quality   = 'good')::int
  ) >= 4
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
        { error: "Choose Good or Needs work for every item." },
        400,
      );
    }

    // For every category marked "needs work", its paired note is required.
    for (const key of criteria) {
      if (body[key] === "needs_work") {
        const note = typeof body[`${key}_note`] === "string" ? body[`${key}_note`].trim() : "";
        if (note.length < 2 || note.length > 500) {
          return response(
            { error: `Please describe what needs work for "${key.replace(/_/g, " ")}" (2–500 characters).` },
            400,
          );
        }
        body[`${key}_note`] = note;
      } else {
        body[`${key}_note`] = null; // a "good" rating shouldn't carry a stray note
      }
    }

    const comment = typeof body.comment === "string" ? body.comment.trim() : "";
    if (comment.length < 2 || comment.length > 1000) {
      return response(
        { error: "Comment must be between 2 and 1,000 characters." },
        400,
      );
    }

    const [review] = await db.sql`
      INSERT INTO reviews (
        data_freshness, screen_filters, site_layout, visual_design, info_quality,
        data_freshness_note, screen_filters_note, site_layout_note, visual_design_note, info_quality_note,
        comment
      )
      VALUES (
        ${body.data_freshness}, ${body.screen_filters}, ${body.site_layout}, ${body.visual_design}, ${body.info_quality},
        ${body.data_freshness_note}, ${body.screen_filters_note}, ${body.site_layout_note}, ${body.visual_design_note}, ${body.info_quality_note},
        ${comment}
      )
      RETURNING id, created_at
    `;
    return response({ review }, 201);
  }

  return response({ error: "Method not allowed." }, 405);
}

export const config = { path: "/api/reviews" };
