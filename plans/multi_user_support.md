# Multi-User Support Plan for iClassPro Enrollment Dashboard

This document updates the original plan to match the current product, including discovery/scrape and selected-class enrollment workflows.

## Current State (Single-User Assumptions)

The app currently assumes one shared user per deployment:

- Credentials and defaults are read from and written to a shared `.env` file.
- Dashboard fields are pre-populated from shared server-side state.
- Schedules live in shared files under `schedules/`.
- Enrollment websocket paths write to a shared temp path (`schedules/tmp/schedule.json`).
- Discovery (`/ws/scrape`) and selected enrollment (`/ws/enroll-selected`) now add more per-user state that is not isolated.

This works for one household/local user, but creates collisions and data leaks in a true multi-user setup.

## Goals

- Isolate each user's credentials, schedules, discovered class selections, and run history.
- Support concurrent discovery and enrollment sessions safely.
- Keep the UX simple for non-technical users.
- Preserve current dashboard features while migrating backend architecture.

## Non-Goals (for initial rollout)

- Full enterprise RBAC.
- Multi-tenant billing.
- Public marketplace integrations.

## Key Gaps to Address

1. Shared credential store:
- `.env` is globally shared and writable by any active session.

2. Shared schedule namespace:
- Schedule filenames are global, not user-scoped.

3. Temp file collision risk:
- Multiple runs can overwrite `schedules/tmp/schedule.json`.

4. Missing auth/session identity:
- API and websocket calls do not carry a verified user identity.

5. Unbounded browser concurrency:
- Each run launches Playwright without global admission control.

## Architecture Options

### Option A: Browser-Local Profiles (Fastest, Lowest Scope)

Store credentials/schedules in browser storage (`localStorage` or IndexedDB), never on the server.

Pros:
- Very fast to ship.
- No auth database required.

Cons:
- No cross-device sync.
- User loses data when browser storage is cleared.
- Still needs server-side temp-file collision fixes and concurrency controls.

### Option B: Full Server-Side Multi-User (Recommended)

Add authentication + database-backed user data.

Pros:
- True cross-device experience.
- Better security and auditability.
- Cleaner path to hosted deployment.

Cons:
- More implementation effort.
- Requires schema migrations and secret management.

## Recommended Data Model (Option B)

- `users`
	- `id`, `email`, `password_hash`, timestamps
- `user_iclass_credentials`
	- `user_id`, encrypted `iclass_email`, encrypted `iclass_password`, `student_id`, `promo_code`, `complete_transaction_default`
- `schedules`
	- `id`, `user_id`, `name`, `is_default`, timestamps
- `schedule_items`
	- `schedule_id`, `day`, `time`, `location`, optional `url`, optional `name`, optional `instructor`
- `jobs`
	- `id`, `user_id`, `type` (`discover`, `enroll_schedule`, `enroll_selected`), `status`, `created_at`, `started_at`, `finished_at`, `error`
- `job_artifacts` (optional)
	- `job_id`, structured result payloads (discovered classes, summary)

## Security Requirements

- Dashboard login/session with secure cookies.
- Password hashing (`argon2` or `bcrypt`) for dashboard accounts.
- Encrypt iClass credentials at rest (application-level encryption using a server secret key).
- HTTPS in all non-local environments.
- CSRF protection for mutating endpoints if cookie-auth is used.

## Execution and Concurrency Plan

1. Remove fixed temp filenames:
- Use per-job files (`schedules/tmp/{job_id}.json`) or avoid files entirely by piping JSON input.

2. Add a job runner boundary:
- Introduce a queue (`asyncio.Queue` initially) with max concurrent Playwright workers.
- Enforce per-user limits to prevent one user from starving others.

3. Track cancellation/state:
- Store running job handles keyed by `job_id`.
- Stop actions target a specific `job_id`.

4. Cleanup:
- Always remove temp artifacts in `finally` blocks.

## API/Websocket Changes

- Replace global config endpoints with user-scoped endpoints.
	- Example: `GET /api/me/config`, `PUT /api/me/config`
- Keep schedule APIs user-scoped.
	- Example: `GET /api/me/schedules`, `POST /api/me/schedules`
- Websocket connect must require authenticated identity.
- Discovery/enrollment websocket payloads should include only run-specific overrides; defaults come from user profile.

## Scrape-Specific Updates

Because scrape capability was added after the original plan, include these in scope:

- Persist user scrape preferences (day filters, location filters, deep-scrape default).
- Store discovered class sets per job/session, not globally.
- Ensure "Enroll Selected" consumes the selected result set for that user/job only.

## Migration Phases

### Phase 0: Safety Hardening (Immediate)

- Replace shared temp file path with per-run unique path.
- Add robust cleanup for temp files.
- Add basic in-process concurrency cap for Playwright runs.

### Phase 1: Introduce Identity

- Add dashboard user auth (signup optional, login required).
- Issue secure session cookies.

### Phase 2: Move State from `.env` to DB

- Add user profile + encrypted iClass credentials.
- Move schedule storage to DB (or user-scoped JSON directories as short bridge).
- Keep `.env` for server/runtime settings only.

### Phase 3: Job System + Observability

- Add `jobs` table and queue-backed runner.
- Add per-job logs, status endpoints, and clearer failure reasons.

### Phase 4: UX Polish

- Add account settings page for defaults.
- Add "last successful run" insights and retry actions.

## Rollback and Risk Notes

- Keep a feature flag for "single-user mode" during migration.
- Plan for dual-read/dual-write during data migration from file schedules.
- Validate encryption key backup/rotation strategy before production release.

## Definition of Done (Multi-User MVP)

- Two users can run discovery and enrollment concurrently without data crossover.
- User A cannot access user B schedules, credentials, or logs.
- No shared temp-file collisions under concurrent runs.
- Dashboard defaults persist per user across sessions/devices.
- `.env` no longer stores user credentials (runtime config only).
