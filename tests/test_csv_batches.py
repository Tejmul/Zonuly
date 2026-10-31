"""The numbered hand-off CSVs — CSV1, CSV2, … (2026-11-01).

What these hold to: a batch carries ONLY addresses proved real, a lead goes out in one batch
and never again, and `--done CSVn` marks that batch's people mailed by Tejmul forever. The
unproved addresses in the fixture are asserted absent from the file, not just from the count.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter.db import (Company, Contact, CsvBatch, CsvBatchContact, Email, EmailCheck, Job,
                          utcnow)
from jobhunter.outreach import drafter, handoff, suppress

ROOT = Path(__file__).resolve().parents[1]


def _script():
    """scripts/export_referrals_csv.py as a module — it is the CLI under test."""
    spec = importlib.util.spec_from_file_location(
        "export_referrals_csv_under_test", ROOT / "scripts" / "export_referrals_csv.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(engine):
    """Two Bengaluru / Hyderabad companies and five leads: two proved real, three that must
    never reach a batch (safe but not real, found-not-proved, already mailed)."""
    with Session(engine) as s:
        # the proofs first: db._only_verified_addresses only lets an address stay on
        # contact.email when its email_check row already says safe
        for addr in ("real2@zeta.in", "real1@acme.in", "safe-not-real@acme.in", "mailed@acme.in"):
            s.add(EmailCheck(address=addr, verdict="safe", source="reacher"))
        s.add(Company(id=1, name="Acme", city="Bengaluru", website="https://acme.in", domain="acme.in",
                      pay_power=90))
        s.add(Company(id=2, name="Zeta", city="Hyderabad", website="https://zeta.in", domain="zeta.in",
                      pay_power=40))
        s.commit()
        s.add(Job(id=1, company_id=1, company_name="Acme", title="SDE Intern", url="https://acme.in/j/1",
                  source="careers", location="Bengaluru", match_score=80))
        s.add(Job(id=2, company_id=2, company_name="Zeta", title="AI Engineer I", url="https://zeta.in/j/2",
                  source="careers", location="Hyderabad", match_score=70))
        s.add(Contact(id=1, company_id=1, name="Asha Rao", source="github", email="real1@acme.in",
                      email_status="real", referral_rank=1))
        s.add(Contact(id=2, company_id=2, name="Ravi Kumar", source="github", email="real2@zeta.in",
                      email_status="real", referral_rank=1))
        # proved safe, but nobody said real — not a batch address
        s.add(Contact(id=3, company_id=1, name="Nita Shah", source="github", email="safe-not-real@acme.in",
                      referral_rank=1))
        # found on a page, never proved — the hook keeps it in email_unverified
        s.add(Contact(id=4, company_id=1, name="Dev Menon", source="site", email="hint@acme.in",
                      referral_rank=1))
        s.add(Contact(id=5, company_id=1, name="Mira Iyer", source="github", email="mailed@acme.in",
                      email_status="real", referral_rank=1))
        s.commit()
        s.add(Email(contact_id=5, company_id=1, to_email="mailed@acme.in", subject="x", body="y",
                    status="sent", sent_at=utcnow()))
        s.commit()
        assert s.get(Contact, 4).email is None and s.get(Contact, 4).email_unverified == "hint@acme.in"


def _run(monkeypatch, tmp_path, *argv):
    """A fresh database, the batch directory under tmp_path, and the CLI with argv."""
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    _fixture(engine)
    mod = _script()
    monkeypatch.setattr(handoff, "CSV_DIR", tmp_path / "csv")
    monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
    monkeypatch.setattr(mod, "init_db", lambda: None)
    # Zeta is the decision brain's first best bet; Acme is the one that can pay 24+
    monkeypatch.setattr(mod.referrals, "best_bets", lambda s: {2: 0})
    monkeypatch.setattr(suppress, "blocked", lambda *a, **k: None)
    monkeypatch.setattr(drafter, "gather", lambda *a, **k: {"person": {}, "company": {}, "job": {}})
    monkeypatch.setattr(drafter, "write", lambda g, **k: {
        "subject": "Quick question", "body": "Hi — a short ask.", "structure": "direct_intro"})
    monkeypatch.setattr(sys, "argv", ["export_referrals_csv.py", *argv])
    return engine, mod, mod.main()


def _rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_batch_writes_only_proved_real_addresses_in_his_format(monkeypatch, tmp_path, capsys):
    engine, _, code = _run(monkeypatch, tmp_path, "--batch", "--tests", "0")
    assert code == 0
    out = handoff.path(1)
    assert out == tmp_path / "csv" / "CSV1.csv"
    rows = _rows(out)
    assert list(rows[0]) == ["company", "email", "subject", "message", "name", "role", "address"]
    # best bets first (Zeta), then the one that can pay 24+ (Acme)
    assert [r["email"] for r in rows] == ["real2@zeta.in", "real1@acme.in"]
    assert {r["address"] for r in rows} == {"verified"}
    # the addresses that are not proved real never appear in the file, at all
    text = out.read_text(encoding="utf-8")
    for forbidden in ("hint@acme.in", "safe-not-real@acme.in", "mailed@acme.in", "fake:", "unverified"):
        assert forbidden not in text
    assert "CSV1: 2 leads" in capsys.readouterr().out
    with Session(engine) as s:
        batch = s.get(CsvBatch, 1)
        assert batch is not None and batch.count == 2 and batch.done_at is None
        assert set(s.exec(select(CsvBatchContact.contact_id)).all()) == {1, 2}


def test_a_lead_is_never_in_two_batches_and_an_empty_batch_writes_nothing(monkeypatch, tmp_path, capsys):
    engine, mod, _ = _run(monkeypatch, tmp_path, "--batch", "--tests", "0")
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["export_referrals_csv.py", "--batch", "--tests", "0"])
    assert mod.main() == 0
    assert "CSV2: nothing to write — no new real addresses" in capsys.readouterr().out
    assert not handoff.path(2).exists()
    with Session(engine) as s:
        assert s.get(CsvBatch, 2) is None          # the number is not spent


def test_done_marks_the_batch_mailed_by_tejmul_forever(monkeypatch, tmp_path, capsys):
    engine, mod, _ = _run(monkeypatch, tmp_path, "--batch", "--tests", "0")
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["export_referrals_csv.py", "--done", "CSV1"])
    assert mod.main() == 0
    assert "CSV1: done · 2 leads · 2 newly marked mailed by Tejmul" in capsys.readouterr().out
    with Session(engine) as s:
        first_done = s.get(CsvBatch, 1).done_at
        assert first_done is not None
        assert all(s.get(Contact, cid).mailed_by_tejmul_at is not None for cid in (1, 2))

    # idempotent: nothing new is marked and the first done_at stands
    monkeypatch.setattr(sys, "argv", ["export_referrals_csv.py", "--done", "csv1"])
    assert mod.main() == 0
    assert "0 newly marked" in capsys.readouterr().out
    with Session(engine) as s:
        assert s.get(CsvBatch, 1).done_at == first_done

    # and they can never come back, even once a later batch is asked for
    monkeypatch.setattr(sys, "argv", ["export_referrals_csv.py", "--batch", "--tests", "0"])
    assert mod.main() == 0
    assert not handoff.path(2).exists()


def test_done_refuses_a_batch_we_never_wrote(monkeypatch, tmp_path, capsys):
    _, mod, _ = _run(monkeypatch, tmp_path, "--batch", "--tests", "0")
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["export_referrals_csv.py", "--done", "CSV9"])
    assert mod.main() == 2
    assert "no batch CSV9" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["export_referrals_csv.py", "--done", "the last one"])
    assert mod.main() == 2
    assert "not a batch name" in capsys.readouterr().out


def test_test_rows_still_land_where_he_names_them_and_are_not_counted_as_leads(monkeypatch, tmp_path, capsys):
    engine, _, code = _run(monkeypatch, tmp_path, "--batch", "--test-at", "2")
    assert code == 0
    rows = _rows(handoff.path(1))
    assert rows[0]["email"] == "tejmulmovin9@gmail.com"    # sheet row 2, under the header
    assert [r["email"] for r in rows[1:]] == ["real2@zeta.in", "real1@acme.in"]
    assert "CSV1: 2 leads" in capsys.readouterr().out
    with Session(engine) as s:
        assert s.get(CsvBatch, 1).count == 2
        assert set(s.exec(select(CsvBatchContact.contact_id)).all()) == {1, 2}


def test_batch_refuses_unproved_addresses_outright(monkeypatch, tmp_path, capsys):
    _, mod, _ = _run(monkeypatch, tmp_path, "--batch", "--tests", "0")
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["export_referrals_csv.py", "--batch", "--with-unproved"])
    assert mod.main() == 2
    assert "proved addresses only" in capsys.readouterr().out
    assert not handoff.path(2).exists()


def test_proved_real_is_the_only_gate_a_batch_address_passes():
    for status in (None, "", "fake:not-proved", "fake:invalid", "fake:risky", "fake:unknown",
                   "bounced", "opted_out", "check:ok", "verified", "REAL"):
        assert not handoff.proved_real(Contact(company_id=1, source="x", email="a@b.in", email_status=status))
    assert not handoff.proved_real(Contact(company_id=1, source="x", email=None, email_status="real"))
    assert handoff.proved_real(Contact(company_id=1, source="x", email="a@b.in", email_status="real"))


def test_next_number_never_overwrites_a_file_he_may_still_be_mailing(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(handoff, "CSV_DIR", tmp_path / "csv")
    (tmp_path / "csv").mkdir()
    with Session(engine) as s:
        assert handoff.next_number(s) == 1
        handoff.record(s, 1, [])
        assert handoff.next_number(s) == 2
        # a file nobody recorded (he copied it in, the record was lost) still moves the number on
        (tmp_path / "csv" / "CSV7.csv").write_text("company,email\n", encoding="utf-8")
        (tmp_path / "csv" / "notes.csv").write_text("x\n", encoding="utf-8")
        assert handoff.next_number(s) == 8


def test_parse_name():
    assert [handoff.parse_name(x) for x in ("CSV3", "csv3", " 3 ")] == [3, 3, 3]
    for bad in ("", "CSV", "CSV-3", "batch 3", "3.csv"):
        with pytest.raises(ValueError):
            handoff.parse_name(bad)


def test_spent_ids_hold_both_a_batch_in_his_hands_and_one_he_has_mailed(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(handoff, "CSV_DIR", tmp_path / "csv")
    _fixture(engine)
    with Session(engine) as s:
        assert handoff.spent_contact_ids(s) == set()
        handoff.record(s, 1, [1])                       # written, not mailed yet — still held back
        s.get(Contact, 3).mailed_by_tejmul_at = utcnow()
        s.commit()
        assert handoff.spent_contact_ids(s) == {1, 3}
        assert handoff.batch_of(s, [1, 2, 3]) == {1: {"batch": 1, "mailed_by_tejmul": False}}
