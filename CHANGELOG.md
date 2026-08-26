# Changelog

All notable changes to the Outlook Telegram/Teams Bot will be documented in this file.

## [Unreleased]
### Added
- **Table-Based Field Extraction for ServiceNow Emails**: `bot/parser.py` now has `_extract_table_fields()`, which parses the real HTML `<table>` structure of ServiceNow notification emails (`<td class="table-label...">Label</td><td class="table-value...">Value</td>` pairs — confirmed against a real anonymized email fixture, see `tests/fixtures/servicenow_er_child_ritm.html`) instead of relying solely on regex over the flattened plain-text body with a hand-maintained stop-word list (`_STOP_WORDS`). `_extract_ticket_fields()` (Title/Description/Priority/Location for ticket cards) and `parse_employee_info()` (Location/Dismissal Date for the weekly report) now prefer this table source when the raw HTML is available, falling back to the previous regex-on-flattened-text approach unchanged for non-tabular/plain-text bodies (all existing plain-text tests are unaffected). This eliminates the whole class of "field value bled into the next field" bugs caused by an incomplete stop-word list, since the field boundary is now the actual closing HTML tag rather than a guessed keyword. NPR/ER classification and employee-name extraction logic are untouched by this change. A useful side effect: `parse_employee_info()` now prefers the ISO-formatted `Dismissal Date System` table field over the human-readable `Dismissal Date` (e.g. "28 August 2026", which `_parse_report_date()` in `bot/reports.py` can't parse) when both are present, so real ER dates parse correctly instead of falling back to the email's received date.
- **Snapshot Tests Against a Real Anonymized ServiceNow Email**: Added `tests/fixtures/servicenow_er_child_ritm.html` — an anonymized copy of a real production email (names replaced, tracking links stripped, HTML structure/classes preserved) — plus snapshot tests in `tests/test_parser.py` asserting the exact parsed output (title/description/location/city/etc.) for both `parse_ticket()` and `_extract_ticket_fields()`. These act as a regression safety net for any future parser refactoring: the observable output for this real email must stay identical unless a change is deliberate.
- **Health Check Metrics**: The daily health-check message now includes bot uptime, mail poll cycle count, timestamp of the last successful poll, count of successfully sent vs. failed Teams notifications, send success rate (%), and current cache sizes (processed emails / notified tickets) — instead of just "emails checked" and "tickets in cache". New `BotState` fields (`failed_sends`, `poll_count`, `last_poll_at`) track these per-process-lifetime metrics; they are intentionally NOT persisted to the checkpoint (reset on the scheduled 12h restart).
- **Unit Tests for `bot/storage.py` and `bot/main.py`**: Added `tests/test_storage.py` (20 tests covering `OrderedIdSet`, `BotState.load/save`, `load_report`/`save_report`, including corrupt-file and atomic-save regression cases) and `tests/test_main.py` (11 tests covering the extracted `process_message()` function: first-run backlog logic, quick dedup, IGNORE handling, Middle East routing, and the "don't mark notified on failed send" retry guarantee). These were previously the least-covered, highest-risk modules (the 2026-08-04 lost-notification incident originated here).
- **Dead-Letter Journal for NPR/ER Format Drift**: `parse_employee_info()` returns `None` both for emails that simply aren't NPR/ER-related (the overwhelming majority, expected) *and* for emails that look like a final NPR/ER event (passed the `is_final` + NPR/ER keyword classification) but where the employee name or city couldn't be extracted — the latter is a real risk of a candidate silently dropping out of the weekly report due to a ServiceNow template change. `bot/parser.py` now exposes `parse_employee_info_with_reason()`, which additionally returns `'name_not_found'`/`'city_not_found'` only for this second, actionable case. `extract_report_data()` records these (and only these) as dead-letter entries via `bot/storage.py::append_dead_letter()` (`data/dead_letters.json`, capped at 200 entries). **GDPR note**: a dead-letter entry stores only `timestamp`/`reason`/`ticket_id` — never the email subject, body, or extracted name, even though the subject line itself frequently contains the employee's full name (e.g. `"Exit Task for {Name}"`). The daily health-check message (`bot/main.py`) now surfaces a non-zero dead-letter count as a warning line, so format drift is caught by the bot itself instead of being discovered later as a missing report entry.

### Changed
- **`bot/main.py`/`bot/reports.py` Pass Raw Email HTML Through to the Parser**: `extract_report_data()` and `parse_employee_info()` now accept an optional `raw_body` parameter, and `bot/main.py` passes `message.body` (the unmodified HTML) through to it, so the new table-based field extraction (see above) has the real markup to work with. Fully backward compatible — omitting `raw_body` (as all pre-existing call sites and tests do) preserves the exact previous behavior.
- **`bot/main.py` Refactor for Testability**: The single-message handling logic (first-run backlog, quick dedup, ticket parsing/sending) that was inlined inside the main polling `for message in messages:` loop has been extracted into a standalone `process_message()` function (plus a `_get_recipients_info()` helper to remove 3x duplicated recipient-collection code). Behavior is unchanged — this purely enables unit testing without running the full `while True` loop or a real O365 authentication.


### Removed
- **Outdated SolarWinds Mention in README**: `README.md` still advertised "SolarWinds monitoring" as an active feature, even though SolarWinds alert handling was fully disabled back in v1.6.0. Updated the description to only mention Zabbix (still actively filtered via `ignore_keywords`/`ZABBIX` checks in `parser.py`).
- **Unused `json` Import in `bot/main.py`**: Dead import left over from a previous version, flagged by `pyflakes`.

### Fixed
- **Time Reminder Didn't Actually Tag Anyone ("everyone" Bug)**: `send_time_reminder()` used a fake `<at>everyone</at>` mention with `mentioned.id: "everyone"`. Teams can only resolve a mention entity via a *real* user identifier (email/UPN, AAD Object ID, or Teams user ID) — the literal string `"everyone"` doesn't correspond to any user, so the card just rendered the plain word "everyone" with no highlight and nobody got pinged (channel/team-wide mentions also aren't supported at all in Adaptive Cards sent via Incoming Webhooks/Workflows). `send_time_reminder()` now tags every real responsible from `data/responsibles.json` (deduplicated by email) by their actual email, exactly like the per-ticket mentions already did.
- **Weekly Report: Late Child Ticket Landed in Wrong Report Window (Maharramov Case)**: `extract_report_data()` unconditionally overwrote the real event date (extracted by `parse_employee_info()` from `Dismissal Date`/`Start Date`/`Title` bracket) with the email's *received* date. This meant an administrative "child" ticket (e.g. hardware return, task re-closure) arriving days or weeks after the real event date would incorrectly land in the wrong weekly report window with a misleading date. `extract_report_data()` now uses the real event date when available (received date is only a fallback when no event date could be parsed), and skips the record entirely if the real event date is more than `STALE_EVENT_DAYS_THRESHOLD` (9) days older than the email's received date — such records are assumed to already be covered by a previous report.
- **Weekly Report: Expired (Unresolved) Exit Task Counted as Final ER**: Same root cause as the "requires approval" bug above, but for the `'expired'` keyword: a subject like `"Exit Task for Nurlykhan Salamatuly has expired"` (a reminder that the exit task was NOT completed in time) matched `is_final` via the `"exit task"` substring, incorrectly counting an unresolved/overdue exit reminder as a completed exit event. Subjects containing `"has expired"` are now explicitly excluded from `is_final`, same as `"requires approval"`.
- **Weekly Report: Wrong Name When Title Has `(date) (Name)` Bracket**: The `Title: NPR/ER (date) (Name) ...` regex pattern (used on tickets like "NPR (03 Aug 2026) (Aruzhan Zhumagazykyzy) Prepare workstation for new employee") was checked AFTER `Service Recipient:` in `name_patterns`. On tickets where `Service Recipient` is the person who physically picked up/returned equipment (not the actual new hire or exiting employee named in the Title), that person's name incorrectly overwrote the correct name from the Title bracket. The Title-bracket pattern is now checked before `Service Recipient:`.
- **Weekly Report: Not-Yet-Approved Exit Request Counted as Final ER**: `is_final` detection in `parse_employee_info()` matched the substring `"exit task"` in the subject line even when the email was a pending approval request (e.g. `"Exit Request: Exit Task for Artur Muratov requires approval"`), not an actual completed/closed event. This caused employees whose exit was merely *submitted* (and not yet approved) to be incorrectly counted as a finalized ER (exit) in the weekly report. Subjects containing `"requires approval"` are now explicitly excluded from `is_final`.
- **Weekly Report: Wrong Name on Transformation Child Tasks**: `parse_employee_info()` checked the `Service Recipient:` regex pattern before `Trainee:`. For "child" hardware-provisioning tasks of a Transformation request (e.g. laptop pickup by a manager on behalf of the trainee), `Service Recipient` holds the manager's name, not the trainee's, so the manager's name incorrectly overwrote the correct trainee name from `Trainee:`/`Title:`. `Trainee:` is now checked before `Service Recipient:`, and its regex now requires an explicit colon so it no longer accidentally matches "Trainee to Employee or Contractor" in the Title sentence itself.
- **Weekly Report: False NPR from Hardware Tickets**: `parse_employee_info()` had a fallback rule that classified ANY resolved/closed ticket containing the word "hardware" or "equipment" (and not containing "exit"/"dismount"/"return") as an NPR (new employee). This caused routine hardware issue/return requests for existing employees (e.g. "Provide non-standard hardware", "Providing non-standard hardware manual task", "Returning EPAM-owned hardware to stock") to be incorrectly reported as new hires in the Kyrgyzstan weekly report. The fallback rule was removed; NPR/ER classification for non-Relocation tickets now relies only on explicit `NPR`/`New Profile`/`Prepare workstation` keywords or `Transformation from Trainee` requests.
- **Weekly Report: Real NPR Silently Dropped ("Quick Dedup" Bug)**: `extract_report_data()` (which feeds the weekly NPR/ER report) was called AFTER the "quick dedup" check for already-notified ticket IDs in `bot/main.py`. Since a ticket's ID is usually already in `notified_tickets` by the time its final "resolved" email arrives (an earlier "assigned" email for the same RITM/INC triggers the Teams card first), the resolution email carrying the actual employee data was skipped via `continue` before `extract_report_data()` ever ran. This caused real NPR/ER cases (e.g. NPR Malika Razieva, Bishkek) to be silently missing from the weekly report even though the email arrived on time. `extract_report_data()` is now called unconditionally, before the quick dedup check, so report data collection is fully decoupled from Teams notification deduplication.
### Added
- **Friday Evening Message Wishes a Good Weekend**: On Fridays, the 19:00 Bishkek evening message now says "Хороших выходных" (have a good weekend) instead of the generic "Хорошего вечера" (have a good evening) used on other weekdays.
- **Structured Adaptive Card Notifications**: `parse_ticket()` now returns a structured dict instead of a pre-rendered markdown string. `bot/teams.py` builds a proper Adaptive Card via `build_ticket_card()`/`send_ticket_card()`:
  - **FactSet** for Priority/Location/SLA % instead of a single wall-of-text `TextBlock`.
  - **"Open in ServiceNow" button** (`Action.OpenUrl`) instead of an inline markdown link.
  - **Color-coded header container** (`Container` with `style: attention/warning/good`) based on priority/criticality, replacing the subtle `themeColor` side-stripe.
  - **SLA percentage extracted** from the subject/body (e.g. "SLA reached 85%") and shown as its own fact, instead of only a generic "⚠️ SLA Alert" label.
  - **Collapsible long fields**: full location path and long description are hidden behind "Show full path" / "Show full description" toggle buttons (`Action.ToggleVisibility`) instead of always rendering the full text inline.
- **Multi-responsible mention tests**: Added `tests/test_teams.py` (16 tests) covering card structure, FactSet contents, OpenUrl action, container styling, and mention formatting.

### Fixed
- **Merged Mentions Bug**: When a location had multiple responsibles (e.g. Almaty: Rustam Baratov + Dmitriy Akimov), their `<at>` tags were concatenated without a separator, rendering as a single confusing name in Teams ("Rustam Baratov Dmitriy Akimov"). Mentions are now joined with a line break, so each responsible appears on their own line.

### Changed
- **Ticket Notification Cards Translated to English**: All field labels and button captions in the Adaptive Card (`Priority`, `Location:`, `SLA reached`, `Description:`, header labels `Incident`/`RITM Request`/`Ticket`/`WARNING: SLA Alert`, `Open in ServiceNow`, `Show full description`) are now in English instead of Russian.
- **Location Field Shows Only Short Label (No Full Path Button)**: The "Show full location path" toggle button and the full location path text block have been removed entirely. The `Location:` fact in the card now always shows only the short, readable city/country label (e.g. "Almaty"), never the full ServiceNow location path (e.g. "Asia - Central and West/Kazakhstan/Almaty/Almaty").
- **Evening Thanks Message Time**: Moved from 18:00 to 19:00 Bishkek time, and added "Пора домой:)" to the message text.
- **CI/CD Migration to GitLab**: Deploy pipeline moved from GitHub Actions (SSH round-trip) to GitLab CI with a self-hosted `gitlab-runner` (shell executor, tag `oracle`) running directly on the production server. Removes the SSH hop entirely — the runner executes `git fetch/reset`, `docker-compose build`, container recreation and image pruning locally. GitHub Actions workflow kept as a manual fallback (`workflow_dispatch` instead of `push` trigger).

### Added
- **Evening Thanks Message**: Every weekday (Mon-Fri) at 18:00 Bishkek time, the bot sends a short thank-you/have-a-good-evening message to the same channel used for Time reminders.

### Fixed
- **SSH Access Recovery**: Restored SSH access to the production server after a `chmod -R` during `gitlab-runner` setup broke OpenSSH `StrictModes` checks on `/home/ubuntu` (`Authentication refused: bad ownership or modes`). Fixed via an OCI rescue instance with the boot volume attached as a secondary block device.

## [2.0.0] - 2026-08-04
### Added
- **Dynamic Configuration**: `LOCATION_RESPONSIBLES` are now loaded from a `data/responsibles.json` file. This allows changing notification targets on the fly without needing to rebuild or restart the bot.
- **Robust HTML Parsing**: Integrated `beautifulsoup4` for HTML cleanup replacing fragile regex-based tag stripping. This solves issues with words concatenating across table cells or complex HTML structures.
- **Atomic State Saving**: Implemented `.tmp` file atomic saves for JSON state and checkpoints to prevent data loss or corruption during sudden restarts/crashes.
- **Docker Data Volume**: Consolidated all state storage (tokens, checkpoints, logs, and reports) into a single `data/` volume mount in `docker-compose.yml`.

### Changed
- **Major Architecture Refactor**: The monolithic `bot.py` has been split into a clean Python package (`bot/`) with dedicated modules (`main.py`, `config.py`, `parser.py`, `reports.py`, `storage.py`, `teams.py`).
- **Dependencies Pinned**: Exact package versions (e.g., `beautifulsoup4~=4.12.3`, `O365~=2.0.35`) are now pinned in `requirements.txt` for reproducible Docker builds.

## [1.7.0] - 2026-08-03
### Added
- **Second Time Reminder**: Added an afternoon reminder for Time completion on Fridays (15:00 Bishkek / 09:00 UTC).
- **Dedicated Heartbeat Thread**: Moved Uptime Kuma push notification to a dedicated background worker thread running every 30 seconds for stable uptime monitoring.

## [1.6.0] - 2026-07-10
### Added
- **SLA Alerting**: Improved detection for "Resolution SLA %" and "violation" keywords. These are now marked as high-priority alerts.
- **Middle East Expansion**: Added location-based tags ([UAE], [QA], [SA], [JO], etc.) and expanded country list (Kuwait, Oman, Jordan).
- **Cache Scalability**: Increased ticket and email cache to 1000 items to prevent duplicate notifications for older requests.

### Changed
- **Privacy Hardening**: Removed Presence API checks and User Profile enrichment (Job Title/Manager) to avoid Azure AD permission issues and reduce log noise.
- **Log Masking**: Added email masking in logs for GDPR compliance.

### Removed
- **SolarWinds Monitoring**: Disabled SolarWinds equipment alerts for both CIS and Middle East channels to reduce noise.

### Fixed
- **Filter Refinement**: Added "is back at work" and "new profile request" (parent) exclusions.
- **Error Handling**: Fixed `AttributeError` when processing SolarWinds alerts without a valid location tag.

## [1.5.0] - 2026-06-30
### Added
- **Middle East Support**: Added dedicated routing for Middle East tickets (UAE, Qatar, Saudi Arabia) to a separate Teams webhook.
- **Reporting Improvements**: Added date range to the Weekly Employee Report header (e.g., "23 Jun - 30 Jun 2026").
- **Exclusion Logic**: Middle East tickets are now excluded from the CIS (Commonwealth of Independent States) weekly report to avoid data duplication.

### Fixed
- **Dependency Issues**: Fixed `ModuleNotFoundError: No module named 'O365'` by installing required packages for the system python environment.

## [1.4.0] - 2026-06-25
### Added
- **Global Location Filter**: Added strict filtering to only process notifications from Kazakhstan, Uzbekistan, and Kyrgyzstan. Requests from other regions (e.g. Saudi Arabia, Russia) are now automatically ignored.
## [1.3.0] - 2026-06-24
### Added
- **Uptime Kuma Integration**: Independent heartbeat thread for reliable monitoring.
- **Improved Duplicate Prevention**: Strict check by Message ID and Ticket ID to avoid double notifications on restart.

### Fixed
- **Shopping Bot AI Stability**: Added retry mechanism (3 attempts) for Gemini 503 errors.
- **Monitoring Glitches**: Heartbeat moved to a separate daemon thread with 50s interval (fixes alternating "Up/Down" status).

## [1.2.0] - 2026-06-22
### Added
- **CI/CD Pipeline**: GitHub Actions for automated deployment to Linux server via SSH.
- **Docker Support**: Added `Dockerfile` and `docker-compose.yml` for containerization.
- **New Filters**: Added exclusion for "Withdrawn" tickets and "Zabbix" monitoring alerts.
- **Smart Duplicate Prevention**: New first-run logic that caches existing emails on startup to prevent re-sending old notifications.

### Changed
- **Timezone Handling**: Switched all internal timing to UTC for server-side reliability.
- **Weekly Report Scheduling**: Moved Friday report to 18:00 Astana time (13:00 UTC).
- **Memory Optimization**: Increased notification cache size to 500 emails.

## [1.1.0] - 2026-06-19
### Added
- **Uzbekistan Logic**: Regional filtering to show only Incidents and SLA for [UZ].
- **Link Rollback**: Reverted report link format to `ID | [ServiceNow](link)` as per user request.
- **Privacy Hardening**: Anonymized terminal logs (Ticket ID instead of Subject).

### Fixed
- **June 19 Missing Data**: Improved regex for RITM/SCTASK status detection (Resolved/Closed only).
- **Employee Extraction**: Exclusion of "Student/Trainee" titles from weekly reports.

## [1.0.0] - Initial Release
- Basic Outlook monitoring.
- Integration with Teams Webhooks.
- Initial support for NPR/ER employee event tracking.
