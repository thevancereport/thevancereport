/*
  POST /api/subscribe  { email, "bot-field" }

  Adds an address to the Buttondown list behind the "Sign up for daily alerts"
  button. Buttondown sends the confirmation email (double opt-in), the
  unsubscribe link, the postal-address footer, and handles bounces and
  complaints -- none of that lives here.

  The API key is read from the BUTTONDOWN_API_KEY environment variable, set in
  Netlify (Site configuration -> Environment variables). It is never sent to the
  browser and never committed.

  Every accepted request gets the same answer, whether the address was new,
  already subscribed, or already pending. That keeps the form from telling a
  stranger who is on the list.
*/

const API = "https://api.buttondown.com/v1/subscribers";
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function reply(body, status = 200) {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

async function readBody(request) {
  const type = request.headers.get("content-type") || "";
  if (type.includes("application/json")) return await request.json();
  const form = await request.formData();
  return Object.fromEntries(form.entries());
}

export default async function handler(request) {
  if (request.method !== "POST") {
    return reply({ error: "Method not allowed." }, 405);
  }

  let body;
  try {
    body = await readBody(request);
  } catch {
    return reply({ error: "Invalid request." }, 400);
  }

  // Honeypot: a person never sees this field. Pretend success so a bot learns
  // nothing, and do not touch the list.
  if (body["bot-field"]) return reply({ ok: true });

  const email = String(body.email || "").trim().toLowerCase();
  if (email.length > 254 || !EMAIL.test(email)) {
    return reply({ error: "Please enter a valid email address." }, 400);
  }

  const key = process.env.BUTTONDOWN_API_KEY;
  if (!key) {
    console.error("subscribe: BUTTONDOWN_API_KEY is not set");
    return reply({ error: "Sign-up is unavailable right now." }, 503);
  }

  const payload = {
    email_address: email,
    tags: ["daily-alerts"],
    referrer_url: request.headers.get("referer") || "https://thevancereport.com/",
  };
  // Buttondown uses the visitor's IP for its own spam checks; without it every
  // sign-up appears to come from Netlify's servers.
  const ip = request.headers.get("x-nf-client-connection-ip");
  if (ip) payload.ip_address = ip;

  let res;
  try {
    res = await fetch(API, {
      method: "POST",
      headers: {
        Authorization: "Token " + key,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error("subscribe: Buttondown unreachable", err);
    return reply({ error: "Sign-up is unavailable right now." }, 502);
  }

  if (res.ok) return reply({ ok: true });

  let detail = {};
  try {
    detail = await res.json();
  } catch {}
  const code = String(detail.code || "");

  // Already on the list, pending, or previously unsubscribed: same answer as
  // success. Buttondown's own records stay as they are.
  if (res.status === 400 && /exist|already|suppress|collision/i.test(code + JSON.stringify(detail))) {
    return reply({ ok: true });
  }
  if (res.status === 400 && /email/i.test(code)) {
    return reply({ error: "Please enter a valid email address." }, 400);
  }
  if (res.status === 429) {
    return reply({ error: "Too many sign-ups just now. Please try again later." }, 429);
  }

  console.error("subscribe: Buttondown returned", res.status, JSON.stringify(detail).slice(0, 500));
  return reply({ error: "Sign-up is unavailable right now." }, 502);
}

export const config = { path: "/api/subscribe" };
