"""
Business-tunable numbers — pricing, credit limits, and usage caps.

Everything in this file is a business decision, not an engineering one:
change a number here and redeploy, no other code needs to change. Kept
separate from app/services/cost_tracker.py (which re-exports these for
backward compatibility — existing code keeps saying cost_tracker.X) and
app/routers/whatsapp.py so there's exactly one place to look when a price
or limit needs updating.
"""

# Every deduction is charged at (actual provider cost) x MARKUP_MULTIPLIER —
# covers overhead/margin rather than passing through raw provider cost 1:1.
MARKUP_MULTIPLIER = 1.1

# Every new student wallet starts with this much free trial credit — Qlass
# covers it (not a real charge to anyone), so a school can try the product
# before a parent/student ever has to pay. See cost_tracker.add_trial_credits.
# Demo/testing numbers (FULL_ACCESS_PHONES below) get unlimited credits
# instead, bypassing this wallet check entirely.
TRIAL_CREDITS = 50.0

# Flat-fee unlimited plan pricing (see cost_tracker._is_unlimited_active) —
# a real student's ₹2499/yr, and a teacher's own "My AI Tutor" profile's
# ₹3500/mo. Used both to price a manual super_admin activation (see
# admin.py's set_student_subscription/activate_teacher_tutor_subscription)
# and to prorate it if a shorter/longer duration is granted than the
# standard term.
UNLIMITED_STUDENT_ANNUAL_PRICE = 2499.0  # for the standard 365-day term
UNLIMITED_STUDENT_ANNUAL_DAYS = 365
UNLIMITED_TEACHER_MONTHLY_PRICE = 3500.0  # for the standard 30-day term
UNLIMITED_TEACHER_MONTHLY_DAYS = 30

# "Unlimited" is a flat fee, not literally uncapped — without some ceiling,
# one heavy student on the flat-fee plan could erase its own margin. Figures
# are in the same ₹ terms a pay-as-you-go student would see billed (i.e.
# real provider cost × MARKUP_MULTIPLIER, not raw cost) — student figures
# are business-set (₹25/week, ₹100/month); teacher figures are a scaled
# placeholder (same ratio to the ₹3500/mo teacher plan price as the student
# caps have to the ₹2499/yr student plan price) pending real data. Either
# period being hit blocks further *flat-fee-covered* usage until it rolls
# off (see cost_tracker.is_unlimited_over_period_cap) — the student isn't
# blocked outright, they just start drawing down their own wallet like a
# normal pay-as-you-go student (see cost_tracker._deduct) if they choose to
# buy "usage credits" (a normal top-up), same as anyone else out of balance.
UNLIMITED_PERIOD_SPEND_CAPS = {
    "student": {"week": 25.0, "month": 100.0},
    "teacher": {"week": 420.0, "month": 1680.0},
}

# Fractions of a usage cap (a pay-as-you-go student's last recharge, or an
# unlimited-plan student's current period cap — see
# app.routers.whatsapp._current_usage_fraction) at which to proactively
# warn a student, so hitting the actual limit is never a surprise.
WALLET_USAGE_WARNING_FRACTIONS = [0.5, 0.75, 0.9]

# Which LLM answers a student's questions, by tutor_level (see
# Student.tutor_level) — one tutor ("Diya"), four cost/quality levels, all
# billed through the same PRICING x MARKUP_MULTIPLIER mechanism as every
# other model (see cost_tracker.PRICING/_tier_for_model), not a separate
# per-level margin scheme. Locked 2026-08-11 against LIVE model
# availability (Gemini 2.0 Flash/1.5 Pro, assumed earlier, turned out to
# be fully retired; every Gemini 3.x model except flash-lite generates
# large, unpredictable hidden "thinking token" costs — see llm_client.py).
# Level 4 is the default/ceiling so no existing student's day-one
# experience changes; see Student.level_nudges_sent for the 50%/75%
# trial-credit downgrade-offer mechanism that steers heavy users toward
# cheaper levels.
TUTOR_LEVEL_MODELS = {
    1: "gpt-4o-mini",
    2: "gemini-3.1-flash-lite",
    3: "claude-haiku-4-5-20251001",
    4: "claude-sonnet-4-6",
}
DEFAULT_TUTOR_LEVEL = 4

# Nudge copy shown when a student crosses 50%/75% of their trial credit —
# offers the next cheaper level down. Keyed by the WALLET_USAGE_WARNING_
# FRACTIONS threshold that triggers it; 90% deliberately has no entry
# (kept as the plain low-balance notice it already was).
TUTOR_LEVEL_NUDGE_OFFERS = {
    0.5: 2,
    0.75: 1,
}

# Per-student weekly caps on the extra (non-core-text) features — the AI
# cost per voice-reply/diagram/video is high enough that unlimited usage
# isn't sustainable at a mass-market price; these are the lever that keeps
# a subscription price profitable. Counted against the existing
# credit_events ledger (each already writes a row tagged by service), not a
# separate counter. All soft: hitting one just skips that extra (falling
# back to plain text, which already stands on its own) rather than blocking
# the whole conversation — the actual hard stop is running out of AI
# credits (or, for an unlimited-plan student, their period spend cap above),
# which covers the core text feature.
FEATURE_LIMITS = {
    "voice": {"services": ["sarvam_tts"], "period": "week", "max": 5, "label": "voice replies"},
    "image_generation": {"services": ["azure_image"], "period": "week", "max": 3, "label": "diagrams"},
    "youtube_videos": {"services": ["youtube_search", "youtube_search_overage"], "period": "week", "max": 5, "label": "videos"},
}
