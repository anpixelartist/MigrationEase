-- Non-owner application role — REQUIRED for Postgres RLS to actually apply.
-- Table owners and superusers bypass RLS, so the API/worker must NOT connect as the
-- 'tally' owner role in production. Alembic migrations keep running as the owner;
-- the app connects as tally_app (see TM_DATABASE_URL in backend/.env.example).
--
-- Runs once on first cluster init (docker-entrypoint-initdb.d) as the 'tally' owner.
-- CHANGE THE PASSWORD in any non-local deployment.

CREATE ROLE tally_app LOGIN PASSWORD 'tally_app_change_me';

GRANT CONNECT ON DATABASE tallymigration TO tally_app;
GRANT USAGE ON SCHEMA public TO tally_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO tally_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tally_app;

-- tables created later by Alembic (running as 'tally') stay accessible to the app role
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tally_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO tally_app;
