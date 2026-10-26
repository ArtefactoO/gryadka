# Monitoring and access boundaries

Celery Beat schedules enabled monitor tasks. Workers collect bounded statistics and metadata and store snapshots in Redis. The authenticated API serves the snapshots. WebSocket, SSE and polling keep the interface updated. Observation times distinguish a saved snapshot from a fresh measurement.

A refresh failure keeps the current tab, search and expanded rows and marks the previous data as stale or unavailable. Missing metrics remain unknown rather than zero or healthy.

## PostgreSQL

The collector reads version, uptime, connection counters, database sizes, cache ratio, commits/rollbacks, deadlocks, lock waits and replication metadata. Transactions are read-only with connection, statement and lock timeouts. SQL text and business rows are not collected.

Database, table/index and role catalogs load on demand. HBA requires `postgresql.hba.read` and the optional [metadata helper](sql/postgresql-monitor.sql). The helper exposes approved columns without HBA options or raw error text. A parsed HBA file may differ from the server's loaded rules. Monitoring does not require granting Gryadka unrestricted superuser access.

## MongoDB

Inspect members, replication and lag, databases, collections and indexes. Collection and index catalogs load only when requested. Member lag retains its observation period and measurements. Administrative operations such as stepdown require separate permissions and target authority.

## RabbitMQ

Queue purge and queue deletion are separate permissions: `rabbitmq.messages.purge` and `rabbitmq.queues.delete`. The `rabbitmq-rw` role includes both. A custom role must declare its intended permission explicitly. Purge affects Ready messages and preserves the queue and Unacked messages. Queue deletion requires confirming its exact name.

## Redis

Node/replication counters and key browsing have different requirements. Key browsing needs `redis.keys.read` in the application plus the required SCAN/TYPE/TTL/SELECT rights on Redis. A monitoring ACL with PING/INFO does not implicitly permit key access. A denial returns a permission error and never expands the target ACL automatically.

Reading values and deleting keys retain their own target permissions. Use a disposable target when exercising administrative features.

## Kafka and Redpanda

Inspect brokers, topics, partitions and consumer lag. Cluster status remains visible alongside node information. Authentication and protocol/version evidence retain their source, and broker-level failures are not converted into a healthy cluster label.

## Infrastructure context

Optional host and Kubernetes observations can accompany a monitor. Missing infrastructure measurements stay unavailable. Placement badges describe actual supplied metadata and are not inferred from a hostname. Kubernetes operations require their own explicit permissions.

## Authentication

All monitoring API reads require authentication. Mutations require their specific permission. OIDC authenticated-viewer mode cannot read HBA, Redis keys or user lists and cannot perform administrative actions. The UI role never elevates the database account's privileges.

See [configuration](configuration.md), [deployment](deployment.md) and the [design conventions](../frontend/DESIGN.md).
