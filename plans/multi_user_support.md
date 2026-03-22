# Multi-User Support Plan for iClassPro Enrollment Dashboard

This document outlines the steps required to transition the current single-user dashboard into a multi-user platform.

## 1. Decouple from `.env`
- Stop pre-populating the frontend with the server's `.env` credentials.
- Move credentials to be user-provided via the UI and stored securely.

## 2. Data Isolation Strategies

### Option A: Client-Side Storage (Quick)
- Use browser `localStorage` to save credentials and schedules.
- **Pros:** Zero backend changes, zero database costs.
- **Cons:** No cross-device syncing.

### Option B: Server-Side Database (Robust)
- Implement a relational database (e.g., SQLite for local, PostgreSQL for cloud).
- Create a `User` model with encrypted fields for iClassPro credentials.
- Create a `Schedule` model linked to the `User` ID.
- **Pros:** Syncs across all devices, professional security.
- **Cons:** Requires a login system (FastAPI Users/Auth) and database management.

## 3. Execution Concurrency
- Replace fixed temporary files (`schedules/tmp/web_schedule.json`) with unique, per-execution identifiers (e.g., `web_schedule_UUID.json`).
- Ensure the backend cleans up these files immediately after the script finishes.

## 4. Resource Management (The Browser Problem)
- Playwright consumes significant RAM.
- Implement a task queue (like Celery or a simple asyncio Queue) to limit concurrent browser instances.
- Or, utilize Google Cloud Run's auto-scaling to spin up isolated containers for each execution.

## 5. Security
- Implement SSL/TLS (HTTPS).
- Add session management and proper password hashing for the dashboard login itself.
