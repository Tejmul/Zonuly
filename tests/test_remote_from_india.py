"""normalize.remote_from_india — 2026-11-13, "the companies should allow to work remotely"."""

import pytest

from jobhunter.normalize import place_restricted_title, remote_from_india

CASES = [
    ("Anywhere in the World", None, True),
    ("Remote - India", None, True),
    ("Bangalore, India - Remote", None, True),
    ("Remote, Global", None, True),
    ("APAC", None, True),
    ("Remote", "We hire from anywhere in the world.", True),
    ("Anywhere in Belgium", None, False),
    ("HYDERABAD OFFICE APAC PSC", None, False),
    ("Atari - US (Remote); Atari Europe (Remote); Delhi, India; Remote", None, False),
    ("Remote", "Work remote from anywhere in the US.", False),
    ("Remote", None, False),
    ("Remote - USA", None, False),
    ("Bengaluru", None, False),
    ("Hybrid - Bangalore, India - Remote", None, False),
    ("", None, False),
    ("LATAM, Europe, USA, Canada, APAC", None, True),
    ("Europe, LATAM, APAC, the U.S., Canada", None, True),
    ("Remote — India, United States", None, True),
    ("Bangalore, India", None, False),
]


@pytest.mark.parametrize("location,description,expected", CASES)
def test_remote_from_india(location, description, expected):
    assert remote_from_india(location, description) is expected


def test_title_naming_another_country_is_restricted():
    assert place_restricted_title("Full-Stack Software Engineer (Remote, US West)")
    assert place_restricted_title("Backend Engineer - Platform | Ireland")
    assert not place_restricted_title("Software Engineer, ML")
    assert not place_restricted_title("Join us as a Backend Engineer")
