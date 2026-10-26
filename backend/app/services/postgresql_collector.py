"""PostgreSQL metadata monitoring. No business rows or SQL text are collected."""
import logging
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from app.config import get_app_config
from app.services.endpoints import parse_host_port
from app.services.infrastructure_metrics import enrich_node_resources

logger = logging.getLogger(__name__)

SERVER_SQL = """
SELECT current_setting('server_version') AS version,
       current_setting('max_connections')::int AS max_connections,
       pg_is_in_recovery() AS in_recovery,
       extract(epoch FROM clock_timestamp()-pg_postmaster_start_time())::bigint AS uptime_seconds,
       current_user AS monitoring_role, current_database() AS connected_database,
       host(inet_server_addr()) AS server_host
"""
DATABASES_SQL = """
SELECT s.datname AS name, pg_database_size(s.datid) AS size_bytes,
       s.numbackends AS connections, s.xact_commit AS commits, s.xact_rollback AS rollbacks,
       s.deadlocks, round(100.0*s.blks_hit/nullif(s.blks_hit+s.blks_read,0),2) AS cache_hit_percent
FROM pg_stat_database s JOIN pg_database d ON d.oid=s.datid
WHERE NOT d.datistemplate ORDER BY s.datname
"""
ACTIVITY_SQL = """
SELECT count(*)::int AS connections,
       count(*) FILTER (WHERE state='active')::int AS active,
       count(*) FILTER (WHERE state='idle in transaction')::int AS idle_in_transaction,
       count(*) FILTER (WHERE wait_event_type='Lock')::int AS waiting_for_lock
FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid()
"""
REPLICATION_SQL = """
SELECT application_name, state, sync_state,
       pg_wal_lsn_diff(pg_current_wal_lsn(),replay_lsn)::bigint AS replay_lag_bytes
FROM pg_stat_replication ORDER BY application_name
"""
HBA_SQL = "SELECT * FROM gryadka_monitor.hba_rules()"
TABLES_SQL = """
SELECT n.nspname AS schema, c.relname AS name, c.relkind AS kind,
       CASE WHEN c.reltuples >= 0 THEN c.reltuples::bigint END AS estimated_rows,
       pg_table_size(c.oid) AS table_bytes, pg_indexes_size(c.oid) AS index_bytes
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind IN ('r','p','m','v') AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND n.nspname !~ '^pg_toast'
ORDER BY n.nspname,c.relname LIMIT %s
"""
INDEXES_SQL = """
SELECT n.nspname AS schema, t.relname AS table_name, c.relname AS name,
       i.indisunique AS is_unique, i.indisprimary AS is_primary, i.indisvalid AS is_valid,
       pg_relation_size(c.oid) AS size_bytes,
       ARRAY(SELECT coalesce(a.attname, '<expression>')
             FROM unnest(i.indkey) WITH ORDINALITY k(attnum, position)
             LEFT JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
             ORDER BY k.position) AS columns
FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace
WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_toast'
ORDER BY n.nspname,t.relname,c.relname LIMIT %s
"""
ROLES_SQL = """
SELECT r.rolname AS name, r.rolcanlogin AS login, r.rolsuper AS superuser,
       r.rolcreatedb AS create_database, r.rolcreaterole AS create_role,
       r.rolconnlimit AS connection_limit,
       ARRAY(SELECT parent.rolname FROM pg_auth_members m
             JOIN pg_roles parent ON parent.oid=m.roleid
             WHERE m.member=r.oid ORDER BY parent.rolname) AS memberships
FROM pg_roles r WHERE r.rolname !~ '^pg_' ORDER BY r.rolname LIMIT %s
"""


def connection_kwargs(target, node: dict) -> dict:
    options = target.options
    host, port = parse_host_port(node, options.get('port', 5432))
    return {
        'host': host, 'port': port,
        'dbname': options.get('database', 'postgres'),
        'user': target.username, 'password': target.password,
        'connect_timeout': 5, 'application_name': 'gryadka-monitor',
        'sslmode': options.get('sslmode', 'prefer'),
        'options': '-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000',
        'autocommit': True, 'row_factory': dict_row,
    }


class PostgresqlCollector:
    def collect_catalog(self, target, database=None):
        """Catalog names and sizes only; no table rows, statements, DDL or credentials."""
        node = target.nodes[0]
        kwargs = connection_kwargs(target, node)
        database = database or kwargs['dbname']
        # Validate a requested database through the maintenance connection first.
        with psycopg.connect(**kwargs) as connection:
            exists = connection.execute(
                'SELECT datname FROM pg_database WHERE datname=%s AND NOT datistemplate', (database,)
            ).fetchone()
            if not exists:
                raise ValueError('Database not found')
        kwargs['dbname'] = database
        with psycopg.connect(**kwargs) as connection:
            tables = connection.execute(TABLES_SQL, (1001,)).fetchall()
            indexes = connection.execute(INDEXES_SQL, (1001,)).fetchall()
        return {'database': database, 'tables': tables[:1000], 'indexes': indexes[:1000],
                'truncated': len(tables) > 1000 or len(indexes) > 1000}

    def collect_roles(self, target):
        with psycopg.connect(**connection_kwargs(target, target.nodes[0])) as connection:
            roles = connection.execute(ROLES_SQL, (1001,)).fetchall()
        return {'roles': roles[:1000], 'truncated': len(roles) > 1000}

    def collect_target_data(self, name: str) -> dict | None:
        config = get_app_config()
        target = next((t for t in config.postgresql_targets if t.name == name), None) if config else None
        if target is None:
            return None
        nodes = [self._collect_node(target, node) for node in target.nodes]
        nodes = enrich_node_resources(nodes, target)
        available = sum(node['status'] == 'UP' for node in nodes)
        status = 'UP' if nodes and available == len(nodes) else ('DEGRADED' if available else 'DOWN')
        return {'name': name, 'overview': {'status': status, 'nodes_up': available, 'nodes_total': len(nodes)},
                'nodes': nodes, 'last_update': datetime.now(timezone.utc).isoformat()}

    def _collect_node(self, target, node: dict) -> dict:
        result = {'name': str(node['host']), 'status': 'DOWN', 'server_host': None}
        try:
            with psycopg.connect(**connection_kwargs(target, node)) as connection:
                result.update(connection.execute(SERVER_SQL).fetchone())
                result['activity'] = connection.execute(ACTIVITY_SQL).fetchone()
                result['databases'] = connection.execute(DATABASES_SQL).fetchall()
                result['replication'] = [] if result['in_recovery'] else connection.execute(REPLICATION_SQL).fetchall()
                if result['in_recovery']:
                    result['replay_age_seconds'] = connection.execute(
                        'SELECT extract(epoch FROM clock_timestamp()-pg_last_xact_replay_timestamp()) AS age'
                    ).fetchone()['age']
                result['status'] = 'UP'
        except (psycopg.Error, ValueError) as exc:
            # Database exceptions may contain credentials or remote server detail.
            logger.warning('PostgreSQL collection failed: target=%s error_type=%s', target.name, type(exc).__name__)
            result['error'] = 'Метаданные PostgreSQL недоступны.'
        return result

    def collect_hba_rules(self, target) -> list[dict]:
        safe_fields = {'line_number', 'type', 'database', 'user_name', 'address', 'netmask', 'auth_method', 'invalid'}
        results = []
        for node in target.nodes:
            with psycopg.connect(**connection_kwargs(target, node)) as connection:
                rows = connection.execute(HBA_SQL).fetchall()
                results.append({'node': str(node['host']), 'rules': [
                    {key: value for key, value in row.items() if key in safe_fields} for row in rows
                ]})
        return results
