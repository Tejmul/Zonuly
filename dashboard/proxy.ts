/*  Route protection: every page except the landing page and /login is refused, on the
 *  server, to anyone not signed in. They are sent to /login and brought back afterwards.
 *
 *  The session cookie is checked properly here, not just looked for: it is the signed
 *  token the API issues (jobhunter/accounts/tokens.py — payload, then an HMAC-SHA256 of
 *  "session.<payload>" under ZONULY_SESSION_SECRET), so a hand-made cookie gets a visitor
 *  no further than no cookie at all. The API checks the same signature on every data
 *  request; this is the same lock on the front door.
 *
 *  Switched on by ZONULY_AUTH=1 in the hosting environment (with the secret beside it),
 *  exactly like the API. On a laptop neither is set and every page opens as before.
 */

import { createHmac, timingSafeEqual } from "node:crypto";
import { NextResponse, type NextRequest } from "next/server";

const COOKIE = "zonuly_session";
const OPEN = new Set(["/", "/login"]);
/* The operator's own cold-email pages (2026-11-12, after launch): his drafts, signed with his
   name, his sends, his calendar and his settings. The deployed site is never the operator's, so they are not
   served there; the API refuses their data too (jobhunter/access.py). */
const OPERATOR_PAGES = ["/queue", "/outreach", "/replies", "/calendar", "/tracker", "/settings"];
const isOperatorPage = (p: string) => OPERATOR_PAGES.some((o) => p === o || p.startsWith(`${o}/`));

function validSession(token: string | undefined, secret: string): boolean {
  if (!token || token.split(".").length !== 2) return false;
  const [body, mac] = token.split(".");
  const expected = createHmac("sha256", secret).update(`session.${body}`).digest("base64url");
  const a = Buffer.from(mac);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return false;
  try {
    const payload = JSON.parse(Buffer.from(body, "base64url").toString("utf8"));
    return typeof payload?.exp === "number" && payload.exp * 1000 > Date.now();
  } catch {
    return false;
  }
}

export function proxy(request: NextRequest) {
  if (process.env.ZONULY_AUTH !== "1") return NextResponse.next();

  const secret = process.env.ZONULY_SESSION_SECRET ?? "";
  const token = request.cookies.get(COOKIE)?.value;
  // Without the secret the signature cannot be checked here; the API still checks it.
  const signedIn = secret.length >= 32 ? validSession(token, secret) : Boolean(token);
  const { pathname, search } = request.nextUrl;

  if (pathname === "/login" && signedIn) {
    const next = request.nextUrl.searchParams.get("next");
    const dest = next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
    return NextResponse.redirect(new URL(dest, request.url));
  }
  if (signedIn && isOperatorPage(pathname)) return NextResponse.redirect(new URL("/dashboard", request.url));
  if (OPEN.has(pathname) || signedIn) return NextResponse.next();

  const login = new URL("/login", request.url);
  login.searchParams.set("next", pathname + search);
  return NextResponse.redirect(login);
}

export const config = {
  // Every page. Not the API proxy (/zapi — the API guards itself, and the landing page and
  // the login form need it before anyone is signed in), not Next's own files, not static
  // assets (anything with a file extension).
  matcher: ["/((?!zapi|_next/static|_next/image|.*\\..*).*)"],
};
