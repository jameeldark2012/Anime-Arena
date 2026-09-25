from __future__ import annotations

from services.ai import RateLimiter


def test_rate_limiter_applies_safety_margin_to_tpm_and_rpm():
    rate_limiter = RateLimiter(tpm_limit=65000, rpm_limit=15, safety_margin=0.8)

    assert rate_limiter.effective_rpm == 12
    assert rate_limiter.effective_tpm == 52000
