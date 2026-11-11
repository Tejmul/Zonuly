"""Accounts: who may see the data on the deployed instance.

    tokens.py    signed session, code challenge and OAuth state
    realness.py  is this a real person with a real inbox (format, throwaway, MX, Turnstile, rate limits)
    store.py     where accounts live — Catalyst Data Store deployed, data/accounts.db locally
    mail.py      sending the 6-digit code from tejmul.in
    google.py    Sign in with Google
    routes.py    /api/auth/*

The gate itself is in jobhunter/access.py, with every other rule about who may do what.
"""
