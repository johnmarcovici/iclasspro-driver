-- Database schema for iClassPro Multi-User Dashboard
-- PostgreSQL 13+

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    iclass_email TEXT NOT NULL,
    iclass_password TEXT NOT NULL,
    student_id TEXT NOT NULL,
    promo_code TEXT DEFAULT '',
    complete_transaction INTEGER DEFAULT 1,
    default_schedule_filename TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(iclass_email, student_id)
);

-- Create index for faster email lookups
CREATE INDEX idx_users_email ON users(iclass_email);

-- Jobs table for tracking discovery and enrollment runs
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    config TEXT NOT NULL,
    result TEXT,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Indexes for job queries
CREATE INDEX idx_jobs_user_id ON jobs(user_id);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX idx_jobs_user_status ON jobs(user_id, status);

-- Audit log for tracking user actions
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    action TEXT NOT NULL,
    details TEXT,
    ip_address TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);

-- Session table for persistent session storage (optional, for multi-instance deployments)
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER,
    data TEXT NOT NULL,
    last_activity TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_expires_at ON sessions(expires_at);

-- Schedules table (optional, for database-backed schedule storage)
CREATE TABLE IF NOT EXISTS schedules (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    items JSONB DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_schedules_user_id ON schedules(user_id);
CREATE INDEX idx_schedules_user_default ON schedules(user_id, is_default);

-- Discovered classes storage (optional, for storing scrape results)
CREATE TABLE IF NOT EXISTS discovered_classes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    job_id TEXT,
    data JSONB DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE INDEX idx_discovered_classes_user_id ON discovered_classes(user_id);
CREATE INDEX idx_discovered_classes_job_id ON discovered_classes(job_id);

-- User settings table (optional, for per-user preferences)
CREATE TABLE IF NOT EXISTS user_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    deep_scrape_default BOOLEAN DEFAULT FALSE,
    notification_email TEXT,
    notifications_enabled BOOLEAN DEFAULT TRUE,
    timezone TEXT DEFAULT 'UTC',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Migration tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    id SERIAL PRIMARY KEY,
    version TEXT NOT NULL UNIQUE,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Insert migration record for this schema
INSERT INTO schema_migrations (version) VALUES ('001_initial_schema');
