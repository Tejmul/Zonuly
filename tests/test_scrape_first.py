"""hygiene.mark_empty: the companies we hold nothing about go first, and nothing is hidden."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from jobhunter import hygiene
from jobhunter.db import Company, Contact


def test_mark_empty_flags_bare_rows_and_clears_filled_ones(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(hygiene, "get_session", lambda: Session(engine))
    monkeypatch.setattr(hygiene, "init_db", lambda: None)

    with Session(engine) as s:
        s.add(Company(id=1, name="Bare"))
        s.add(Company(id=2, name="Famous", overrated=True))
        s.add(Company(id=3, name="Hidden", status="quarantined"))
        s.add(Company(id=4, name="Filled", website="https://filled.dev", scrape_first=True))
        s.add(Contact(company_id=4, name="A Person", email="a@filled.dev", source="test"))
        s.commit()

    out = hygiene.mark_empty()
    assert out["flagged"] == 1 and out["cleared"] == 1

    with Session(engine) as s:
        assert s.get(Company, 1).scrape_first is True
        assert not s.get(Company, 2).scrape_first
        assert not s.get(Company, 3).scrape_first
        assert s.get(Company, 4).scrape_first is False
        assert len(s.exec(Company.__table__.select()).all()) == 4   # nothing removed


def test_a_found_website_reopens_the_ats_probe():
    from jobhunter.enrich import _reopen

    notes = "[funding-rss] Acme raised $5M\n[ats] probed: no public board\n[facts] read"
    assert _reopen(notes) == "[funding-rss] Acme raised $5M\n[facts] read"
    assert _reopen("[ats] probed: no public board") is None
    assert _reopen(None) is None
