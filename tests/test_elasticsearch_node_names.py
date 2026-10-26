from app.services.elasticsearch_collector import _configured_node_aliases, _public_node_name


def test_observed_node_name_does_not_invent_an_environment_dns_suffix():
    for name in ['elastic-prod-1', 'elastic-rc-2', 'data-3.example.test']:
        assert _public_node_name(name, {}) == name


def test_configured_endpoint_alias_has_precedence_for_display():
    aliases = _configured_node_aliases([{'host': 'https://data-1.example.test:9200'}])
    assert _public_node_name('data-1', aliases) == 'data-1.example.test'
