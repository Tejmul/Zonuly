"use client";

/*  Add a company.
 *
 *  companies.yaml is the list of company job boards the scrapers crawl directly, and
 *  a company in it gets its whole board pulled on every cycle. Until now the only way
 *  into that file was a text editor. This is that file, with a form in front of it.
 *
 *  The board probe only knows Greenhouse, Lever and Ashby, and only tries slugs a
 *  rule would guess — so it misses roughly half of what you throw at it. That is not
 *  a failure state to hide behind a generic error: when it misses, the form opens up
 *  and asks for the provider and slug directly, because the URL of any careers page
 *  has both sitting in it.
 */

import { useState } from "react";
import { Building2, Check, Loader2 } from "lucide-react";
import { api, useTaskWatcher, type Task } from "@/lib/api";
import { Button, Input, Select } from "@/components/ui/primitives";

type Added = {
  id: number;
  name: string;
  already_existed: boolean;
  board: { ats: string; slug: string; open_roles: number | null } | null;
  seeded_into_yaml: boolean;
  roles_added: number;
  roles_total: number;
  note: string;
};

export function AddCompany({ onAdded }: { onAdded?: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [ats, setAts] = useState("");
  const [slug, setSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Added | null>(null);
  const { watch } = useTaskWatcher();

  /** Shown once the probe has come back empty — there is nothing to say about a
   *  board until it has actually been looked for. */
  const askForBoard = result !== null && result.board === null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { task_id } = await api.post<{ task_id: string }>("/api/companies", {
        name: name.trim(),
        website: website.trim() || null,
        ats: ats || null,
        ats_slug: slug.trim() || null,
      });
      watch(task_id, (task: Task) => {
        setBusy(false);
        if (task.status === "error") {
          setError(task.error ?? "That did not work.");
          return;
        }
        setResult(task.result as Added);
        onAdded?.();
      });
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function reset() {
    setName("");
    setWebsite("");
    setAts("");
    setSlug("");
    setResult(null);
    setError(null);
  }

  if (!open) {
    return (
      <Button variant="quiet" size="sm" onClick={() => setOpen(true)}>
        <Building2 size={13} />
        Add a company
      </Button>
    );
  }

  return (
    <form onSubmit={submit} className="w-full max-w-[34rem] rounded-sm border border-line bg-surface p-4">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h2 className="text-[13px] font-semibold">Add a company</h2>
          <p className="mt-0.5 max-w-[64ch] text-[11.5px] leading-relaxed text-ink-3">
            Its public job board is found from the name, recorded so every future cycle
            crawls it, and read straight away. A company added here needs no other setup.
          </p>
        </div>
        <button
          type="button"
          onClick={() => { setOpen(false); reset(); }}
          className="cursor-pointer text-[11.5px] text-ink-3 hover:text-ink"
        >
          Close
        </button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="add-company-name">Company name</label>
        <Input
          id="add-company-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Company name"
          className="h-9 w-full sm:w-56"
          autoFocus
          required
        />
        <label className="sr-only" htmlFor="add-company-site">Website</label>
        <Input
          id="add-company-site"
          value={website}
          onChange={(e) => setWebsite(e.target.value)}
          placeholder="website.com (optional)"
          className="h-9 w-full sm:w-52"
        />
        <Button type="submit" variant="primary" size="sm" disabled={busy || !name.trim()}>
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
          {busy ? "Looking for their board…" : "Add"}
        </Button>
      </div>

      {askForBoard && (
        <div className="mt-3 border-t border-line pt-3">
          <p className="text-[11.5px] leading-relaxed text-ink-2">
            No board answered to any name worth guessing. If you know it, the provider and
            slug are both in the careers-page URL — <span className="tnum">
            job-boards.greenhouse.io/<b>acme</b></span> means Greenhouse, <span className="tnum">acme</span>.
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="add-company-ats">Board provider</label>
            <Select id="add-company-ats" value={ats} onChange={(e) => setAts(e.target.value)}>
              <option value="">Provider…</option>
              <option value="greenhouse">Greenhouse</option>
              <option value="lever">Lever</option>
              <option value="ashby">Ashby</option>
            </Select>
            <label className="sr-only" htmlFor="add-company-slug">Board slug</label>
            <Input
              id="add-company-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="board slug"
              className="h-9 w-full sm:w-44"
            />
            <Button type="submit" variant="quiet" size="sm" disabled={busy || !ats || !slug.trim()}>
              Try this board
            </Button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-[11.5px] text-ink">
          {error}
        </p>
      )}

      {result && !error && (
        <div className="mt-3 border-t border-line pt-3">
          <p className="text-[12px] text-ink">
            <span className="font-medium">{result.name}</span>
            {result.board ? (
              <span className="text-ink-2">
                {" "}· {result.board.ats} board <span className="tnum">{result.board.slug}</span>
                {result.board.open_roles !== null ? `, ${result.board.open_roles} open roles` : ""}
              </span>
            ) : null}
          </p>
          <p className="mt-1 text-[11.5px] leading-relaxed text-ink-3">{result.note}</p>
          <div className="mt-2 flex items-center gap-3">
            <a
              href={`/companies/${result.id}`}
              className="text-[11.5px] text-ink underline-offset-2 hover:underline"
            >
              Open {result.name}
            </a>
            <button
              type="button"
              onClick={reset}
              className="cursor-pointer text-[11.5px] text-ink-3 hover:text-ink"
            >
              Add another
            </button>
          </div>
        </div>
      )}
    </form>
  );
}
