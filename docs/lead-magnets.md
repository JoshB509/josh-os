# Lead Magnets directory

`/lead-magnets` and `/api/lead-magnets` use the existing CareerOS authorization gate. The sidebar and phone menu reveal the destination only when the endpoint is available, so updating static files before a server restart does not expose a broken link.

`studio/lead_magnets.py` owns the canonical `lead_magnets` table in the existing local store. Both the directory and `Agents.run` read this same registry. Every team member and job using that runner receives the current records, including audience, contents and provenance, without separate hardcoded prompt catalogs. This adds no agent write capability.

`reference_sources/lead-magnets.json` contains source snapshots. The first 15 records are the lead_magnet entries from the Linklr production catalog supplied on 16 November 2026, with exact source IDs, names, landing URLs and nullable Kit form IDs. The sixteenth is the Before and After Slide Pack, independently confirmed in Linklr and on the live website at 12:53:55 UTC that day. Matched local magnet briefs provide content details; each has separate provenance. Old draft statuses, unconfirmed URLs and sales-page records are excluded. The older reference index and project-level briefs are preserved.

Imports are transactional and idempotent by ID. Each row keeps its previous source snapshot. New source fields and unchanged local fields receive incoming values; fields changed locally, including deliberately blank or removed fields, are retained. Missing source records are never deleted. Add future verified source metadata to the same record ID. Do not create another record for a renamed magnet or a placeholder while its source ID is pending. Explicitly supply null or an empty value to clear a source field; omission preserves it.

The complete source snapshot is read and validated before merging. Missing, unreadable, partial or invalid source data leaves the last-good registry and local edits intact. With no last-good records, agents receive an empty registry and can continue unrelated work. Diagnostics log only the failure class, never source contents or exception messages. Database failures are outside that source-error handler and remain visible. A later valid snapshot resumes normal merging.

Downloads appear in the directory only when `download_verification` has `status: "verified"`, a nonempty `checked_at`, and a `url` exactly matching `download_url`. Keep evidence in that object. The Executive Benefits tracked PDF destination is retained as unverified provenance and is not a download button. The Before and After Slide Pack uses Linklr ID `db7cafee-9dc9-40fa-82b3-c80500681e60`, the verified `/before-after-slide-pack` landing page and Kit form `10196670`. Its download URL is unknown and remains null, so there is no download button.

Validation uses temporary stores, a temporary loopback HTTP server with the real Handler (without starting the application's background services), mocked agent subprocesses and intercepted headless browser requests:

```powershell
python -m unittest tests.test_lead_magnets tests.test_lead_magnets_browser tests.test_agent -v
```

Use the Python environment configured by CareerOS. The system Python may lack application dependencies. Browser tests write screenshots under `test-evidence/` in the test checkout. No production database or connector is used. The normal test network guard remains active.

Python changes require the next normal CareerOS restart. Installing these source files does not restart the running application, import into its live database, or change external services.
