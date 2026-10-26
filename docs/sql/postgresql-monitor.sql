-- Run as the database administrator in the monitored maintenance database.
-- Login provisioning/password delivery is separate and must use Vault.
BEGIN;
GRANT pg_monitor TO gryadka_monitor;
ALTER ROLE gryadka_monitor SET default_transaction_read_only = on;
CREATE SCHEMA IF NOT EXISTS gryadka_monitor AUTHORIZATION CURRENT_USER;
REVOKE ALL ON SCHEMA gryadka_monitor FROM PUBLIC;
GRANT USAGE ON SCHEMA gryadka_monitor TO gryadka_monitor;

CREATE OR REPLACE FUNCTION gryadka_monitor.hba_rules()
RETURNS TABLE (
    line_number integer, type text, database text[], user_name text[],
    address text, netmask text, auth_method text, invalid boolean
)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $body$
    SELECT line_number, type, database, user_name, address, netmask, auth_method,
           error IS NOT NULL AS invalid
    FROM pg_catalog.pg_hba_file_rules
    ORDER BY line_number
$body$;
REVOKE ALL ON FUNCTION gryadka_monitor.hba_rules() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gryadka_monitor.hba_rules() TO gryadka_monitor;
COMMIT;
