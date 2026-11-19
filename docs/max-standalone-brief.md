# Max morning preparation (standalone)
This helper prepares a report. It never sends it, starts JoshOS, opens a browser, or accesses credentials.
JoshOS owns Max's daily Discord briefing at 07:00 Asia/Dubai. Dot manages its separate fallback.
The standalone helper remains preparation-only and must not send a second briefing.

## Daily Discord delivery (16 November 2026)

Josh requested daily delivery to be connected. Business settings support `destination=owner_dm`
with pinned `owner_user_id`, `bot_user_id` and existing `channel_id`, and `enabled=true`.
The bridge verifies the single configured owner, exact Max bot, and private DM recipient before sending.
General Max proactive DMs/notices remain blocked. Server channel audience guards are unchanged.
Every message is read back and matched to its content, author and channel; confirmation is stored in
the `brief:YYYY-MM-DD` outbox. Uncertain or partial sends are never automatically repeated.

Normal JoshOS startup starts the scheduler. Source prefetch is 06:45 Dubai; delivery is 07:00.
The PC and JoshOS must be running and connected. Catch-up ends at 12:00 Dubai.
Source dates remain explicit; direct YouTube Analytics is separate from stale Linklr YouTube data.
Kit spans are observed counter-growth periods, and Linklr month totals exclude unrecorded payments.

## Daily contract
At 07:00 Asia/Dubai, obtain Linklr get_data_status then get_daily_metrics(days=3) with existing connected access.
Save the complete unchanged daily-metrics JSON in Data/business/prepared/source-linklr-YYYY-MM-DD.json.
Run:
python -B tools/prepare_max_brief.py --linklr Data/business/prepared/source-linklr-YYYY-MM-DD.json
Read and verify YYYY-MM-DD.json and .txt in that same directory before delivery.
No fetch or delivery is implied by a successful preparation.
The dated JSON records delivery=not_sent and source timestamps separately from preparation time.

The main window is the previous UTC day (04:00 to 04:00 Dubai).
Course + upsell revenue excludes all other streams; purchases are explicitly all-stream purchases.
Checkout clicks are never represented as Stripe checkout starts.
Missing values are not zero. Invalid/current-day/stale source inputs stop before replacing report output.
YouTube cache must have been refreshed today; its actual Pacific source day is retained.
Kit/carousel require today's complete snapshot plus a prior complete snapshot. Their true spans are retained.
No later snapshot means unavailable, not a fabricated daily difference. A baseline never becomes daily traffic.
If the desktop is offline, Dot can produce the same Linklr summary directly and mark local metrics unavailable.
Do not replay missed brief sends or automatically send an old report.

## Verified 2026-11-14
9 focused tests pass: exclusion, wrapper handling, stale response, timezone, duplicate day, missing field,
partial day, NaN and Boolean rejection. Today's Linklr report prepared and read back.
Local caches cannot provide new Nov14 YouTube/Kit/carousel figures. This helper deliberately does not
start external collectors or refresh credentials. A root Dot automation is still required for scheduling.

## Read-path repair verified 2026-11-15

The November 15 restricted runner could see Data/studio.sqlite but SQLite mode=ro
returned `unable to open database file`. The identical read outside that sandbox
succeeded: complete Kit and carousel snapshots for November 15 were already there.
This was an execution-access failure, not evidence of an absent snapshot. No database,
credentials, settings, bridge, scheduler, or application startup was changed.

The helper now preserves `read_error`, `missing_file`, `missing_snapshot`, `incomplete`,
`invalid_snapshot`, `baseline`, `no_new_day`, and valid source periods separately.
A database/source validation error writes an explicitly incomplete report and exits 2;
Dot must inspect `source_errors`, obtain the normal approved read-access execution
context, and rerun before using the affected metrics. Do not silence the error, grant
blanket access, use immutable SQLite mode, or copy the main DB without its live WAL.
The normal mode=ro connection observes committed WAL data and is explicitly closed.

Kit figures require verified Kit sending/completed states, positive recipient counts,
valid counts and source times. Only completed status can establish a sent broadcast.
No planned date establishes delivery, and no open rate or CTR is calculated. Exact
snapshot times and multi-day gaps remain explicit. The first November 13 carousel
lifetime total remains a baseline; observed growth is never backdated or split across
unmeasured days. A reduced channel total is withheld.

CareerOS reviews are now included from the complete reviews cache, with UTC reporting
day, collection time and newest refresh attempt. The connected CareerOS tools/list
schema was rechecked on November 15: find_learners has a maximum of 50 with no cursor;
get_learner_progress and get_learner_timeline require a user_id; list_reviews has a
maximum of 200 without pagination. A complete cross-learner activity aggregate is
not exposed. Learning minutes are not estimated from lesson opens or completions.

Source-only diagnosis without Linklr input is available:

    python -B tools/prepare_max_brief.py --sources-only --output-dir <diagnostic-folder>

Use `--data-root` only to identify the actual source Data directory. Source-only output
explicitly says Linklr was not supplied; it must not substitute for a full business
report. Existing dated report files can be preserved by choosing another output folder.
The helper still never fetches sources, starts JoshOS, or delivers messages.

Validation: 66 focused tests passed across preparation, Kit counters, carousel counters,
and scorecard sources, including a live committed WAL read. Actual approved read-back:
Kit +1,640 opens and +88 clicks across November 13 02:45 to November 15 02:45 UTC;
Studio +9,840 all-post impressions and +7,471 on 10 tracked carousels over November 12-13
reporting days; direct YouTube November 12 Pacific 11,436 views / +34 net subscribers;
CareerOS 0 reviews submitted November 14 UTC, 2 total, read November 15 02:45 UTC.
All output was prepared as `not_sent`.
