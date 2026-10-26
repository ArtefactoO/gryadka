# Configuration

The application reads YAML from `CONFIG_PATH`, defaulting to `/app/config.yaml`. Start with `config.example.yaml`. A target contains a name, nodes and monitor-specific options. The local example observes the Compose Redis instance.

```yaml
updateIntervalS: 15
auth:
  provider: local
  allow_local_fallback: true
users:
  - username: admin
    roles: [admin]
redis:
  - name: local-redis
    nodes:
      - host: redis:6379
    options:
      mode: direct
```

## Credentials

Set `JWT_SECRET` to a generated value of at least 32 characters and set `AUTH_USER_ADMIN_PASSWORD` in your untracked `.env`. Do not put password values or hashes in YAML. For a local account, the corresponding environment variable is `AUTH_USER_<USERNAME>_PASSWORD`, with hyphens normalized to underscores. An account without an explicit role defaults to viewer.

Monitor credentials use `MONITOR_<MONITOR>_<TARGET>_USERNAME` and `_PASSWORD` under the loader's normalized target naming. Use environment injection from your secret manager. Consult `backend/app/config.py` for the exact options and normalization of each monitor.

## OIDC

The OIDC provider supports authorization code with PKCE. `auth.oidc.access_mode` selects group mapping or authenticated-viewer mode. Group mapping requires a matching role. Authenticated-viewer mode grants only built-in viewer permissions to an authenticated user and does not inherit broader group/role claims.

OIDC client ID, client secret and JWT secret belong in environment variables. Configure the public base URL and callback with your identity provider. Local fallback is a separate explicit choice.

## Background work

The API, worker and scheduler must share the same runtime configuration and Redis settings. Beat schedules only enabled monitors. Collectors cache snapshots; opening the dashboard does not grant target permissions. A detail catalog may read target metadata on demand.

Use the `options` and infrastructure metadata defined by each monitor instead of inventing environment-specific DNS suffixes. Elasticsearch node display names resolve from configured node aliases and the observed name.
