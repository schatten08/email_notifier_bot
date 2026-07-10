# Changelog

All notable changes to the Outlook Telegram/Teams Bot will be documented in this file.

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
