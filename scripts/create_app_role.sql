-- Создание роли приложения и прав на базу excursion_ai.
-- Выполнять от имени администратора PostgreSQL (например, postgres).
-- Пароль задайте сами и сохраните в .env как DB_PASSWORD.

-- CREATE ROLE excursion_ai_app WITH LOGIN PASSWORD 'ЗАМЕНИТЕ_ПАРОЛЬ';
-- GRANT CONNECT ON DATABASE excursion_ai TO excursion_ai_app;
-- \c excursion_ai
-- GRANT USAGE, CREATE ON SCHEMA public TO excursion_ai_app;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO excursion_ai_app;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO excursion_ai_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public
--   GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO excursion_ai_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public
--   GRANT USAGE, SELECT ON SEQUENCES TO excursion_ai_app;

SELECT 'Edit this file placeholders before running against your server' AS notice;
