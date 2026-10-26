from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import TargetConfig
from app.services.postgresql_collector import PostgresqlCollector, TABLES_SQL, INDEXES_SQL, ROLES_SQL


@pytest.mark.parametrize('path',['/api/postgresql/fixture/catalog','/api/postgresql/fixture/roles'])
def test_catalogs_require_login(path):
    assert TestClient(app).get(path).status_code==401


def test_catalog_reads_use_readonly_connections_and_keep_rows_out():
    target=TargetConfig('fixture',[{'host':'db:5432'}],options={'database':'maintenance'})
    validation=MagicMock();validation.execute.return_value.fetchone.return_value={'datname':'catalog_db'}
    catalog=MagicMock();catalog.execute.side_effect=[MagicMock(fetchall=lambda:[]),MagicMock(fetchall=lambda:[])]
    first=MagicMock();first.__enter__.return_value=validation
    second=MagicMock();second.__enter__.return_value=catalog
    with patch('app.services.postgresql_collector.psycopg.connect',side_effect=[first,second]) as connect:
        payload=PostgresqlCollector().collect_catalog(target,'catalog_db')
    assert payload['database']=='catalog_db'
    assert validation.execute.call_args.args[1]==('catalog_db',)
    for call in connect.call_args_list:
        assert 'default_transaction_read_only=on' in call.kwargs['options']
        assert call.kwargs['connect_timeout']==5
    assert connect.call_args_list[1].kwargs['dbname']=='catalog_db'
    assert 'pg_stat_activity' not in TABLES_SQL+INDEXES_SQL
    assert 'pg_roles' in ROLES_SQL and 'rolpassword' not in ROLES_SQL and 'pg_authid' not in ROLES_SQL
    assert 'pg_get_indexdef' not in INDEXES_SQL


def test_arbitrary_database_is_rejected_before_connecting_to_it():
    target=TargetConfig('fixture',[{'host':'db:5432'}])
    connection=MagicMock();connection.execute.return_value.fetchone.return_value=None
    with patch('app.services.postgresql_collector.psycopg.connect') as connect:
        connect.return_value.__enter__.return_value=connection
        with pytest.raises(ValueError):PostgresqlCollector().collect_catalog(target,'unknown')
    assert connect.call_count==1


@pytest.mark.skipif(not __import__('os').environ.get('GRYADKA_CATALOG_TEST_HOST'), reason='Requires isolated PostgreSQL catalog fixture')
def test_catalog_against_restricted_postgresql_fixture():
    import os
    import psycopg
    from app.services.postgresql_collector import connection_kwargs
    target=TargetConfig('isolated',[{'host':os.environ['GRYADKA_CATALOG_TEST_HOST']}],username='gryadka')
    from ipaddress import ip_address
    node = PostgresqlCollector()._collect_node(target, target.nodes[0])
    assert node['status'] == 'UP'
    ip_address(node['server_host'])  # A canonical host address, never PostgreSQL inet's /32 or /128 suffix.
    result=PostgresqlCollector().collect_catalog(target)
    assert any(table['name']=='fixture_catalog' for table in result['tables'])
    assert any(index['name']=='fixture_catalog_id' and index['is_unique'] for index in result['indexes'])
    with psycopg.connect(**connection_kwargs(target,target.nodes[0])) as connection:
        assert connection.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
        with pytest.raises(psycopg.errors.InsufficientPrivilege):connection.execute('SELECT * FROM public.fixture_catalog').fetchall()
