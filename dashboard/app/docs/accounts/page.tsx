import type { Metadata } from "next";
import { A, C, Callout, Doc, H2, Lead, P, Step, Steps, Table } from "@/components/docs/prose";

export const metadata: Metadata = { title: "Sign in & accounts" };

export default function Accounts() {
  return (
    <Doc slug="accounts">
      <Lead>
        On the deployed site (<C>zonuly.tejmul.in</C>, launched 12 Nov 2026) the landing page is open to everyone;{" "}
        <strong>every other page, and every data call behind it, needs a signed-in account</strong>. Signing in is a
        6-digit code sent to your inbox — no password. On your own machine none of this is switched on and every page opens
        as before.
      </Lead>

      <H2>What is open and what is not</H2>
      <Table
        head={["Where", "Signed out", "Signed in"]}
        rows={[
          [<C key="r">/</C>, "The landing page and its live counters", "The same"],
          [<C key="l">/login</C>, "The sign-in form", "Sends you on to the page you came for"],
          ["Every other page — these docs included", <>Sent to <C key="n">/login?next=…</C>, and brought back to that page after signing in</>, "Opens"],
          ["The data API", <>Refused with <C key="4">401</C>, except <C key="h">/api/health</C>, <C key="o">/api/overview</C> and <C key="a">/api/auth/*</C></>, "Read-only, as before: writes are refused and addresses are shown masked"],
          ["The operator’s cold-email pages", "Not served", <>Not served either — see <A key="op" href="#operator-pages">below</A></>],
        ]}
      />

      <H2>Signing in</H2>
      <Steps>
        <Step title="Give your email">
          The address is checked before a code is spent on it: it must be well formed, not a disposable-mailbox service, and
          on a domain that can receive mail (it publishes an MX record, or failing that an address record). A typo&apos;d or
          invented domain is turned away with a plain message.
        </Step>
        <Step title="Read the code">
          A 6-digit code arrives from <C>no-reply@tejmul.in</C>, sent through Zoho Mail SMTP — never through the outreach
          Gmail. It works for <strong>10 minutes</strong>.
        </Step>
        <Step title="Type it in">
          The right code signs you in and sets the session cookie. There is no separate sign-up: the first code you verify
          creates the account, and every later one signs you back in. <strong>Sign out</strong> sits in the top bar beside
          your address.
        </Step>
      </Steps>
      <Table
        head={["Limit", "Value", "Why"]}
        rows={[
          ["Wrong guesses on one code", "5, then that code is locked", "A 6-digit code can only be tested by asking the server, and the server counts"],
          ["Codes sent to one address", "3 in 15 minutes", "A person retrying, not someone flooding an inbox"],
          ["Codes requested from one network address", "10 an hour", "Stops one machine walking through many addresses"],
          ["Wrong guesses from one network address", "30 an hour", "Stops guesses spread across many codes"],
        ]}
      />
      <P>
        The half-finished login is not kept on the server: the browser carries a signed challenge that holds an HMAC of the
        code (not the code, not a plain hash), and hands it back with what you type. That matters because the backend sleeps
        a minute after its last request and forgets its memory, while reading a code out of an inbox takes longer than a
        minute. The rate limits are in memory, so a sleeping instance forgets them — but a caller who keeps trying keeps it
        awake.
      </P>
      <Callout kind="note" title="Google sign-in">
        Sign in with Google is written (<C>jobhunter/accounts/google.py</C>) but not switched on: the button appears only
        when the Google client is configured, and on the deployed site today it is not.
      </Callout>

      <H2>What is stored</H2>
      <P>
        Only the email address. Accounts live in the Catalyst Data Store table <C>ZonulyUsers</C>, one row per address:
      </P>
      <Table
        head={["Field", "Filled"]}
        mono={[0]}
        rows={[
          ["email", "Always — the account is the address"],
          ["provider", "How you signed in (email)"],
          ["last_login_at · login_count", "On every sign-in"],
          ["name · google_sub", "Only through Google sign-in, which is off"],
        ]}
      />
      <Callout kind="rule" title="Never in jobhunter.db">
        Accounts are never written to <C>jobhunter.db</C>. That file is copied into the public snapshot (see{" "}
        <A href="/docs/deployment">Deployment</A>), and a list of who signed up is not something to publish. On a laptop
        accounts go to a file of their own, <C>data/accounts.db</C>.
      </Callout>

      <H2>The session</H2>
      <P>
        A signed-in browser carries one cookie, <C>zonuly_session</C>: a small payload (account id, email, expiry) and an
        HMAC-SHA256 of it under <C>ZONULY_SESSION_SECRET</C>. It lasts <strong>30 days</strong> and is <C>httponly</C>, so page
        scripts cannot read it. It is checked on every request — by the Next proxy (<C>dashboard/proxy.ts</C>) before a page
        is served, and by the API before data is returned — so a hand-made cookie gets no further than no cookie. Neither
        check looks anything up: browsing never waits on the account store.
      </P>
      <P>
        Responses behind the sign-in are marked <C>Cache-Control: private</C>. The CDN keys on the URL, not the cookie, so a
        page cached for one signed-in visitor would otherwise be handed to anyone who asked for the same URL.
      </P>

      <H2>Switching it on</H2>
      <Table
        head={["Setting", "Where", "What it does"]}
        mono={[0]}
        rows={[
          ["ZONULY_AUTH=1", "GitHub repository secret (backend) · Vercel environment (frontend)", "Turns the sign-in gate on. Unset, the deployed site is the open read-only window it was before."],
          ["ZONULY_SESSION_SECRET", "The same value in both places", "Signs and checks sessions and code challenges. At least 32 characters; without it a public instance refuses to sign anyone in."],
          ["ZONULY_SMTP_HOST · _PORT · _USER · _PASSWORD", "GitHub secrets", "The Zoho Mail SMTP account the codes go out through."],
          ["ZONULY_MAIL_FROM · ZONULY_PUBLIC_URL", "GitHub secrets", "The code’s sender, and the site address Google sign-in returns to."],
          ["ZONULY_GOOGLE_CLIENT_ID · _SECRET", "GitHub secrets", "Google sign-in. Not set today."],
        ]}
      />
      <P>
        The deploy workflow passes the <C>ZONULY_*</C> secrets into the AppSail configuration at build time; they never
        enter the repository. See <A href="/docs/deployment">Deployment</A>.
      </P>
      <Callout kind="note" title="On your own machine">
        Nothing to set: <C>ZONULY_AUTH</C> is off and every page opens. To try the login locally, with no mail set up the
        API hands the code back to the page instead of mailing it (never on a public instance), and sessions end when the
        API restarts.
      </Callout>

      <H2 id="operator-pages">The operator&apos;s pages</H2>
      <P>
        The cold-email pages — <strong>Queue</strong>, <strong>Outreach</strong>, <strong>Email proposal</strong>,{" "}
        <strong>Replies</strong>, <strong>Calendar</strong> and <strong>Tracker</strong> — are the operator&apos;s own outbox:
        drafts signed with his name, his sends, his calendar. Since 12 Nov 2026 they are not on the deployed site: the menu
        leaves them out, the proxy sends a signed-in visitor who types one of their addresses to <C>/dashboard</C>, and the
        API refuses <C>/api/outreach</C>, <C>/api/emails</C>, <C>/api/campaigns</C> and <C>/api/calendar</C> there. They are
        used on the operator&apos;s own machine, where the pipeline and the mailbox are.
      </P>
    </Doc>
  );
}
