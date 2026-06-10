"""Hedge-fund portfolio construction framework.

A code-form replica of the multi-asset portfolio playbook: four conviction
tiers with capital allocations (``framework``), the Entry Precision Engine that
scores a trade out of 100 (``scoring``), the dynamic risk-budget / drawdown
model (``risk_model``), and the ATR-based stop-loss / take-profit architecture
(``sltp``).

Everything here is pure stdlib so the engines can be unit-tested in isolation
(the CI installs only pytest); the FastAPI surface lives in ``routes/portfolio``.
"""
