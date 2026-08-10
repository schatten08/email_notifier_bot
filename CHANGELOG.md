# Changelog

All notable changes to the Outlook Telegram/Teams Bot will be documented in this file.

## [Unreleased]
### Fixed
- **Weekly Report: Late Child Ticket Landed in Wrong Report Window (Maharramov Case)**: `extract_report_data()` unconditionally overwrote the real event date (extracted by `parse_employee_info()` from `Dismissal Date`/`Start Date`/`Title` bracket) with the email's *received* date. This meant an administrative "child" ticket (e.g. hardware return, task re-closure) arriving days or weeks after the real event date would incorrectly land in the wrong weekly report window with a misleading date. `extract_report_data()` now uses the real event date when available (received date is only a fallback when no event date could be parsed), and skips the record entirely if the real event date is more than `STALE_EVENT_DAYS_THRESHOLD` (9) days older than the email's received date — such records are assumed to already be covered by a previous report.
- **Weekly Report: Expired (Unresolved) Exit Task Counted as Final ER**: Same root cause as the "requires approval" bug above, but for the `'expired'` keyword: a subject like `"Exit Task for Nurlykhan Salamatuly has expired"` (a reminder that the exit task was NOT completed in time) matched `is_final` via the `"exit task"` substring, incorrectly counting an unresolved/overdue exit reminder as a completed exit event. Subjects containing `"has expired"` are now explicitly excluded from `is_final`, same as `"requires approval"`.
- **Weekly Report: Wrong Name When Title Has `(date) (Name)` Bracket**: The `Title: NPR/ER (date) (Name) ...` regex pattern (used on tickets like "NPR (03 Aug 2026) (Aruzhan Zhumagazykyzy) Prepare workstation for new employee") was checked AFTER `Service Recipient:` in `name_patterns`. On tickets where `Service Recipient` is the person who physically picked up/returned equipment (not the actual new hire or exiting employee named in the Title), that person's name incorrectly overwrote the correct name from the Title bracket. The Title-bracket pattern is now checked before `Service Recipient:`.
- **Weekly Report: Not-Yet-Approved Exit Request Counted as Final ER**: `is_final` detection in `parse_employee_info()` matched the substring `"exit task"` in the subject line even when the email was a pending approval request (e.g. `"Exit Request: Exit Task for Artur Muratov requires approval"`), not an actual completed/closed event. This caused employees whose exit was merely *submitted* (and not yet approved) to be incorrectly counted as a finalized ER (exit) in the weekly report. Subjects containing `"requires approval"` are now explicitly excluded from `is_final`.
- **Weekly Report: Wrong Name on Transformation Child Tasks**: `parse_employee_info()` checked the `Service Recipient:` regex pattern before `Trainee:`. For "child" hardware-provisioning tasks of a Transformation request (e.g. laptop pickup by a manager on behalf of the trainee), `Service Recipient` holds the manager's name, not the trainee's, so the manager's name incorrectly overwrote the correct trainee name from `Trainee:`/`Title:`. `Trainee:` is now checked before `Service Recipient:`, and its regex now requires an explicit colon so it no longer accidentally matches "Trainee to Employee or Contractor" in the Title sentence itself.
- **Weekly Report: False NPR from Hardware Tickets**: `parse_employee_info()` had a fallback rule that classified ANY resolved/closed ticket containing the word "hardware" or "equipment" (and not containing "exit"/"dismount"/"return") as an NPR (new employee). This caused routine hardware issue/return requests for existing employees (e.g. "Provide non-standard hardware", "Providing non-standard hardware manual task", "Returning EPAM-owned hardware to stock") to be incorrectly reported as new hires in the Kyrgyzstan weekly report. The fallback rule was removed; NPR/ER classification for non-Relocation tickets now relies only on explicit `NPR`/`New Profile`/`Prepare workstation` keywords or `Transformation from Trainee` requests.
- **Weekly Report: Real NPR Silently Dropped ("Quick Dedup" Bug)**: `extract_report_data()` (which feeds the weekly NPR/ER report) was called AFTER the "quick dedup" check for already-notified ticket IDs in `bot/main.py`. Since a ticket's ID is usually already in `notified_tickets` by the time its final "resolved" email arrives (an earlier "assigned" email for the same RITM/INC triggers the Teams card first), the resolution email carrying the actual employee data was skipped via `continue` before `extract_report_data()` ever ran. This caused real NPR/ER cases (e.g. NPR Malika Razieva, Bishkek) to be silently missing from the weekly report even though the email arrived on time. `extract_report_data()` is now called unconditionally, before the quick dedup check, so report data collection is fully decoupled from Teams notification deduplication.
### Added
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
