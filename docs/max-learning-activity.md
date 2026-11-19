# Max's CareerOS learning aggregate

`studio/learning_activity.py` reads only `mcp__careeros__get_learning_activity_summary`
through the existing Claude Code read-only connector path. It never enumerates learners,
changes credentials/settings, or delivers a message.

At prefetch (06:45 Dubai), and catch-up when needed, Business collects the last completed
Dubai calendar day independently of Linklr freshness. The exact request uses local
midnight boundaries and `time_zone=Asia/Dubai`. At the briefing boundary it shares the
existing bounded source wait; late collection does not extend that limit.

Only the exact tool call's raw response is accepted. The parser checks the request,
UTC/local boundaries, timezone, bounds, data-through, complete window, generation time,
metric status/value types and breakdowns. Generation must be within the previous hour
of collection, with at most five minutes of future clock skew. No model-authored final
answer becomes source data. Unknown per-person fields are not retained.

Validated aggregate data is saved atomically to `Data/business/learning-activity.json`.
The separate `learning-activity-attempt.json` records failed reads without replacing the
last good snapshot. Old periods are explicitly labeled as older data; values are never
reassigned to a newer day. A valid partial summary is not re-fetched within the same day.

`window_complete` means the requested calendar period ended and is covered by the read.
It does **not** mean historical tracking was complete. Every metric's `status`, nulls,
`tracked_since`, source definition, caveat and available breakdown remain in the receipt's
`sources.learning.summary`. Active learner activity includes the events defined by
CareerOS; the partial value is a lower bound, not an exact count of lesson opens alone.
Minutes remain unavailable until visible-tab playback has recorded data. Questions and
reviews are first creations/submissions during the Dubai window; review edits are excluded.
Comments and completed downloads remain unavailable; download starts are not completions.

The concise learning block uses the aggregate once configured; it never substitutes the
legacy UTC review count into that Dubai period. Other source periods remain unchanged.
Delivery settings, pinned owner DM, duplicate guards and today's sent receipt are untouched.
New code takes effect on the next normal JoshOS startup; installation itself never restarts it.

November 18 contract compatibility: active_learners also accepts the source status `tracked`, preserves that literal status, and renders its numeric value without a partial label. Optional caveat/reason/tracking_needed may be null and remain null. Definitions remain required strings; unknown statuses and invalid numbers are still rejected.
