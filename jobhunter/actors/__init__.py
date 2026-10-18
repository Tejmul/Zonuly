"""Actors — our own Apify. See actors/core.py for the platform, actors/linkedin.py for
the LinkedIn actors. `run(name, **input)` runs one and returns its ledger row; the
records are in the dataset file the row names.

    linkedin-jobs        job search (guest endpoint) + optional detail per job
    linkedin-company     company pages: about, size, HQ, specialties, employee links
    linkedin-profile     public profiles: current / former employers, schools, location
    linkedin-employees   who works at a company NOW — discovered, then verified per profile
    company-people       the LinkedIn People tab without our account: every public source + Apify
                         (opt-in), merged, verified, ranked by who can refer
"""

from __future__ import annotations

from typing import Callable

from jobhunter.actors import linkedin, people
from jobhunter.actors.core import Blocked, Fetcher, Run, host_status, read_dataset, recent_runs, reset_host

ACTORS: dict[str, Callable] = {
    "linkedin-jobs": linkedin.jobs,
    "linkedin-company": linkedin.company,
    "linkedin-profile": linkedin.profiles,
    "linkedin-employees": linkedin.employees,
    "company-people": people.company_people,
}


def run(name: str, *, fresh: bool = False, **inp) -> dict:
    if name not in ACTORS:
        raise KeyError(f"no actor {name!r}; have {sorted(ACTORS)}")
    r = Run(actor=name, input={k: (list(v) if isinstance(v, (tuple, set)) else v) for k, v in inp.items()})
    r.fetcher = Fetcher(fresh=fresh)
    try:
        ACTORS[name](r, **inp)
    except Blocked as e:
        return r.finish("blocked", str(e))
    except Exception as e:  # noqa: BLE001 — a run always leaves its ledger row
        return r.finish("failed", f"{type(e).__name__}: {e}")
    finally:
        r.fetcher.close()
    return r.finish("succeeded")


__all__ = ["ACTORS", "Blocked", "host_status", "read_dataset", "recent_runs", "reset_host", "run"]
