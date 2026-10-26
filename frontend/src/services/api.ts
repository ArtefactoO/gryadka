/**
 * API service for backend communication
 */
import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_URL || window.location.origin

const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Add token to requests if available
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Handle 401 errors
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status
    const requestUrl = String(error.config?.url || '')
    const isAuthStateProbe = requestUrl.includes('/api/auth/me')
    // For modal auth flow we should not redirect to /login.
    if (status === 401 && !requestUrl.includes('/api/auth/login')) {
      localStorage.removeItem('auth_token')
      if (!isAuthStateProbe) {
        window.dispatchEvent(new Event('auth-changed'))
      }
    }
    return Promise.reject(error)
  }
)

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  username: string
  display_name?: string | null
  auth_provider?: string | null
  roles: string[]
  permissions: string[]
}

export interface AppMeta {
  version: string
}

export interface AppChangelog {
  content: string
}

export interface User {
  username: string
  display_name?: string | null
  auth_provider?: string | null
  roles: string[]
  permissions: string[]
}

export interface NodeSize {
  oplog?: number
  data?: number
}

export interface PrimaryMetrics {
  oplog_configured_bytes?: number
  oplog_used_bytes?: number
  oplog_window_seconds?: number
  election_timeout_millis?: number
  heartbeat_interval_millis?: number
  write_concern_majority_journal_default?: boolean
}

export interface PrimaryHistory {
  became_primary_at?: string
  previous_primary?: string
}

export interface NodeExporterMetrics {
  matched?: boolean
  matched_label?: string
  matched_value?: string
  cpu_usage_percent?: number
  cpu_cores?: number
  memory_used_percent?: number
  memory_total_bytes?: number
  memory_available_bytes?: number
  disks?: Array<{
    mountpoint: string
    device?: string
    fstype?: string
    total?: number
    free?: number
    total_bytes?: number
    free_bytes?: number
    used_percent?: number
  }>
  scrape_up?: boolean
}

export interface KubernetesContainerResources {
  name: string
  cpu_usage_cores?: number | null
  cpu_request_cores?: number | null
  cpu_limit_cores?: number | null
  memory_usage_bytes?: number | null
  memory_request_bytes?: number | null
  memory_limit_bytes?: number | null
}

export interface KubernetesPodResources {
  namespace: string
  pod: string
  cpu_usage_cores?: number | null
  cpu_request_cores?: number | null
  cpu_limit_cores?: number | null
  memory_usage_bytes?: number | null
  memory_request_bytes?: number | null
  memory_limit_bytes?: number | null
  containers?: KubernetesContainerResources[]
}

export interface InfrastructureMetrics {
  kind: 'kubernetes' | 'vm' | 'host' | 'docker' | 'unknown' | 'none'
  state: 'available' | 'unavailable' | 'disabled'
  source?: string; reason?: string; reason_code?: string; namespace?: string
  pods: KubernetesPodResources[]
  resources?: KubernetesPodResources | null
}

export interface Node {
  name: string
  stateStr: string
  status?: string // UP | DOWN (from replica set member health)
  lag: number
  lag_history?: Array<{ time: string; lag: number }> // from Redis for chart (persists across refresh)
  priority: number
  hidden: number
  uptime?: number
  size?: NodeSize
  primary_metrics?: PrimaryMetrics
  primary_history?: PrimaryHistory
  sync_source_host?: string
  mongo_version?: string
  infrastructure?: InfrastructureMetrics | null
  node_exporter?: NodeExporterMetrics
  self: boolean
}

export interface DatabaseUser {
  user: string
  db: string
  roles: string
}

export interface MongoBackupFile {
  file: string
  backup_date?: string | null
  mtime?: string | null
  size_bytes?: number | null
  active?: boolean
}

export interface MongoDatabaseBackup {
  database: string
  status: string
  last_completed?: MongoBackupFile | null
  running?: MongoBackupFile | null
  files_count?: number
}

export interface Cluster {
  name: string
  nodes: Node[]
  databases: string[]
  backups?: Record<string, MongoDatabaseBackup>
  users?: DatabaseUser[]
  users_visible: boolean
  feature_compatibility_version?: string
  last_update: string
}

export interface MongoStepDownResponse {
  status: string
  cluster: string
  previous_primary: string
  stepdown_seconds: number
  refresh_task_id?: string
}

export interface Collection {
  name: string
  count?: number
  size?: number
  indexes_count?: number
}

export interface CollectionIndex {
  name: string
  key: string
  unique: boolean
  sparse: boolean
  hidden: boolean
  ttl_seconds?: number
  partial: boolean
}

export interface ClusterStats {
  total_clusters: number
  total_nodes: number
  last_update: string
}

export type DatabasesOverview = Record<string, string[]>

export interface MonitoringTarget {
  id: string
  name: string
  type: string
  status: string
  last_update?: string
  group?: string
  namespace?: string
}

export type RegistryOrigin = 'auto_gitlab' | 'manual'

export interface RegistrySourceReference {
  repo_name: string
  repo_url?: string
  source_file_url?: string
  repo_ref?: string
  repo_commit?: string
  file_path: string
  source_line_start?: number
  source_line_end?: number
  object_key?: string
  object_type?: string
  extractor_type?: string
  source_object_id?: number
}

export interface RegistryAutoRow {
  id?: string
  origin: 'auto_gitlab'
  ip: string
  address_scope?: 'external' | 'internal'
  service_name?: string
  dns_name?: string
  dns_names?: string[]
  dns_validation?: {
    status: string
    resolved_ips: string[]
    checked_at?: string
    message?: string
  } | null
  previous_ips?: string[]
  ip_changed_at?: string | null
  protocol?: string
  port?: number
  environment?: string
  component?: string
  source_references: RegistrySourceReference[]
}

export interface RegistryManualRow {
  id: number
  origin: 'manual'
  label: string
  ip: string
  service_name: string
  ip_or_cidr: string
  address_scope?: 'external' | 'internal'
  dns_name?: string | null
  dns_names?: string[]
  dns_validation?: {
    status: string
    resolved_ips: string[]
    checked_at?: string
    message?: string
  } | null
  previous_ips?: string[]
  ip_changed_at?: string | null
  protocol?: string | null
  port?: number | null
  comment: string
  owner?: string | null
  created_by?: string | null
  tags?: string[] | null
  created_at?: string
  updated_at?: string
  is_active: boolean
  source_references?: RegistrySourceReference[]
}

export interface RegistrySummary {
  auto_rows_count?: number
  manual_rows_count?: number
  linked_rows_count?: number
  total_source_objects?: number
  total_attributes?: number
  total_linked_rows?: number
  total_manual_rows?: number
  refresh_interval_s?: number
  last_sync_status?: string
  last_sync_at?: string | null
  last_successful_sync_at?: string | null
  repos?: Array<{
    repo_name: string
    repo_ref?: string | null
    repo_commit?: string | null
    status: string
    last_synced_at?: string | null
    error?: string | null
  }>
  views?: Array<{
    name: string
    label: string
    auto_rows_count: number
    manual_rows_count: number
    total_rows_count: number
  }>
}

export interface RegistryMismatchSummary {
  total_count: number
  auto_count: number
  manual_count: number
}

export interface RegistryPayload {
  target: MonitoringTarget
  status: string
  last_update?: string
  summary: RegistrySummary
  auto_rows: RegistryAutoRow[]
  manual_rows: RegistryManualRow[]
}

export interface CreateManualRegistryEntryRequest {
  label: string
  service_name: string
  ip_or_cidr: string
  address_scope?: string | null
  dns_name?: string | null
  protocol?: string | null
  port?: number | null
  comment: string
  owner?: string | null
  tags?: string[] | null
  is_active?: boolean
}

export interface UpdateManualRegistryEntryRequest {
  label?: string
  service_name?: string
  ip_or_cidr?: string
  address_scope?: string | null
  dns_name?: string | null
  protocol?: string | null
  port?: number | null
  comment?: string
  owner?: string | null
  tags?: string[] | null
  is_active?: boolean
}

export interface RabbitOverview {
  rabbitmq_version?: string
  erlang_version?: string
  messages: number
  messages_ready: number
  messages_unacknowledged: number
  queues: number
  exchanges: number
  connections: number
  channels: number
  channels_running?: number
  consumers: number
  consumers_total?: number
  publish_rate?: number | null
  deliver_get_rate?: number | null
  ack_rate?: number | null
  redeliver_rate?: number | null
  listeners?: RabbitListener[]
}

export interface RabbitListener {
  protocol?: string | null
  ip_address?: string | null
  port: number
}

export interface RabbitNode {
  name: string
  running: boolean
  status?: string
  status_reason?: string
  rabbitmq_version?: string
  erlang_version?: string
  uptime_seconds?: number
  channels?: number
  connections?: number
  mem_alarm?: boolean
  disk_free_alarm?: boolean
  being_drained?: boolean
  partitions?: string[]
  cluster_links_count?: number
  cluster_link_peers?: string[]
  enabled_plugins?: string[]
  mem_used?: number
  mem_limit?: number
  role?: string
  disk_free?: number
  disk_free_limit?: number
  sockets_used?: number
  sockets_total?: number
  fd_used?: number
  fd_total?: number
  infrastructure?: InfrastructureMetrics | null
  node_exporter?: NodeExporterMetrics
}

export interface RabbitQueue {
  name: string
  vhost: string
  state?: string
  type?: string | null
  durable?: boolean
  auto_delete?: boolean
  exclusive?: boolean
  consumers: number
  messages: number
  messages_ready: number
  messages_unacknowledged: number
  memory_bytes?: number | null
  consumer_utilisation?: number | null
  publish_rate?: number | null
  deliver_get_rate?: number | null
  ack_rate?: number | null
  redeliver_rate?: number | null
}

export interface RabbitUser {
  name: string
  tags?: string
}

export interface RabbitCluster {
  name: string
  overview: RabbitOverview
  nodes: RabbitNode[]
  queues: RabbitQueue[]
  users?: RabbitUser[]
  last_update: string
  ui_url?: string
}

export interface RabbitQueueActionRequest {
  name: string
  vhost: string
}

export interface RabbitQueueActionResponse {
  status: string
  action: 'purge' | 'delete'
  target_name: string
  vhost: string
  queue: string
  refresh_task_id: string
}

export interface RedisOverview {
  mode: string
  master_name?: string
  master_host?: string
  master_port?: number
  replicas_total: number
  replicas_up: number
  sentinels_total: number
  sentinels_up: number
  connected_clients: number
  ops_per_sec: number
  used_memory_bytes: number
  keys_total: number
  keyspace_hit_rate?: number
  rdb_enabled?: boolean
  rdb_bgsave_in_progress?: boolean
  rdb_last_bgsave_status?: string
  rdb_last_save_time?: number
  input_kbps?: number | null
  output_kbps?: number | null
  blocked_clients?: number | null
  evicted_keys_rate?: number | null
  expired_keys_rate?: number | null
  rejected_connections_rate?: number | null
  keyspace_hits_rate?: number | null
  keyspace_misses_rate?: number | null
  mem_fragmentation_ratio?: number | null
}

export interface RedisNode {
  name: string
  host: string
  port: number
  kind: string
  role: string
  status: string
  status_reason?: string
  redis_version?: string
  uptime_seconds?: number
  connected_clients?: number
  used_memory_bytes?: number
  used_memory_rss_bytes?: number
  used_memory_peak_bytes?: number
  maxmemory_bytes?: number
  total_memory_bytes?: number
  ops_per_sec?: number
  input_kbps?: number | null
  output_kbps?: number | null
  blocked_clients?: number | null
  evicted_keys_rate?: number | null
  expired_keys_rate?: number | null
  rejected_connections_rate?: number | null
  keyspace_hits_rate?: number | null
  keyspace_misses_rate?: number | null
  mem_fragmentation_ratio?: number | null
  rdb_enabled?: boolean
  rdb_bgsave_in_progress?: boolean
  rdb_last_bgsave_status?: string
  rdb_last_save_time?: number
  master_host?: string
  master_port?: number
  master_link_status?: string
  replication_offset?: number
  lag_bytes?: number
  infrastructure?: InfrastructureMetrics | null
  node_exporter?: NodeExporterMetrics
}

export interface RedisDatabase {
  name: string
  keys: number
  expires: number
  avg_ttl?: number
}

export interface RedisCluster {
  name: string
  overview: RedisOverview
  nodes: RedisNode[]
  databases: RedisDatabase[]
  last_update: string
}

export interface RedisKeyItem {
  key: string
  type: string
  ttl: number
}

export interface ScanResponse {
  cursor: number
  keys: RedisKeyItem[]
}

export interface RedisKeyValueResponse {
  key: string
  type: string
  ttl: number
  value: any
  size: number
  has_more: boolean
  total_count: number
}

export interface DeleteKeysRequest {
  keys: string[]
  pattern?: string
  db?: number
}

export const authApi = {
  getConfig: async (): Promise<{sso_enabled: boolean; local_enabled: boolean}> => (await api.get("/api/auth/config")).data,
  login: async (credentials: LoginRequest): Promise<LoginResponse> => {
    const response = await api.post<LoginResponse>('/api/auth/login', credentials, { timeout: 12000 })
    return response.data
  },

  logout: async (): Promise<void> => {
    await api.post('/api/auth/logout')
  },

  getMe: async (): Promise<User> => {
    const response = await api.get<User>('/api/auth/me')
    return response.data
  },
}

export const clustersApi = {
  getClusters: async (): Promise<string[]> => {
    const response = await api.get<string[]>('/api/clusters')
    return response.data
  },

  getCluster: async (name: string): Promise<Cluster> => {
    const response = await api.get<Cluster>(`/api/clusters/${name}`)
    return response.data
  },

  getClusterNodes: async (name: string): Promise<Node[]> => {
    const response = await api.get<Node[]>(`/api/clusters/${name}/nodes`)
    return response.data
  },

  getClusterDatabases: async (name: string): Promise<string[]> => {
    const response = await api.get<string[]>(`/api/clusters/${name}/databases`)
    return response.data
  },

  getDatabasesOverview: async (): Promise<DatabasesOverview> => {
    const response = await api.get<DatabasesOverview>('/api/clusters/databases-overview')
    return response.data
  },

  getDatabaseCollections: async (clusterName: string, dbName: string): Promise<Collection[]> => {
    const response = await api.get<Collection[]>(
      `/api/clusters/${clusterName}/databases/${dbName}/collections`
    )
    return response.data
  },

  getCollectionIndexes: async (
    clusterName: string,
    dbName: string,
    collectionName: string
  ): Promise<CollectionIndex[]> => {
    const response = await api.get<CollectionIndex[]>(
      `/api/clusters/${clusterName}/databases/${dbName}/collections/${encodeURIComponent(collectionName)}/indexes`
    )
    return response.data
  },

  getClusterUsers: async (name: string): Promise<DatabaseUser[]> => {
    const response = await api.get<DatabaseUser[]>(`/api/clusters/${name}/users`)
    return response.data
  },

  stepDownPrimary: async (name: string, stepdownSeconds = 60): Promise<MongoStepDownResponse> => {
    const response = await api.post<MongoStepDownResponse>(
      `/api/clusters/${encodeURIComponent(name)}/stepdown`,
      { stepdown_seconds: stepdownSeconds }
    )
    return response.data
  },
}

export interface UnreachableHost {
  host: string
  status: string
  target_name: string
  target_type: string
}

export interface TargetRefreshQueuedResponse {
  queued: boolean
  target_id: string
  target_type: string
  target_name: string
  task_id: string
  status: string
}

export const targetsApi = {
  getTargets: async (): Promise<MonitoringTarget[]> => {
    const response = await api.get<MonitoringTarget[]>('/api/targets')
    return response.data
  },

  getTarget: async (id: string): Promise<MonitoringTarget> => {
    const response = await api.get<MonitoringTarget>(`/api/targets/${encodeURIComponent(id)}`)
    return response.data
  },

  getUnreachable: async (): Promise<UnreachableHost[]> => {
    const response = await api.get<UnreachableHost[]>('/api/targets/unreachable/list')
    return response.data
  },

  refreshTarget: async (id: string): Promise<TargetRefreshQueuedResponse> => {
    const response = await api.post<TargetRefreshQueuedResponse>(`/api/targets/${encodeURIComponent(id)}/refresh`)
    return response.data
  },
}

export const addressesApi = {
  getAddresses: async (): Promise<RegistryPayload> => {
    const response = await api.get<RegistryPayload>('/api/addresses')
    return response.data
  },
  getRegistry: async (): Promise<RegistryPayload> => {
    const response = await api.get<RegistryPayload>('/api/addresses')
    return response.data
  },

  getSummary: async (): Promise<RegistrySummary> => {
    const response = await api.get<RegistrySummary>('/api/addresses/summary')
    return response.data
  },

  getMismatchSummary: async (): Promise<RegistryMismatchSummary> => {
    const response = await api.get<RegistryMismatchSummary>('/api/addresses/mismatches')
    return response.data
  },

  collectAddresses: async (): Promise<void> => {
    await api.post('/api/addresses/collect')
  },
  collectRegistry: async (): Promise<void> => {
    await api.post('/api/addresses/collect')
  },

  listManual: async (): Promise<RegistryManualRow[]> => {
    const response = await api.get<RegistryManualRow[]>('/api/addresses/manual')
    return response.data
  },

  createManual: async (payload: CreateManualRegistryEntryRequest): Promise<RegistryManualRow> => {
    const response = await api.post<RegistryManualRow>('/api/addresses/manual', payload)
    return response.data
  },

  updateManual: async (
    id: number,
    payload: UpdateManualRegistryEntryRequest
  ): Promise<RegistryManualRow> => {
    const response = await api.patch<RegistryManualRow>(`/api/addresses/manual/${encodeURIComponent(id)}`, payload)
    return response.data
  },

  deleteManual: async (id: number): Promise<void> => {
    await api.delete(`/api/addresses/manual/${encodeURIComponent(id)}`)
  },
}

export const registryApi = addressesApi

export const metaApi = {
  getAppMeta: async (): Promise<AppMeta> => {
    const response = await api.get<AppMeta>('/api/meta/app')
    return response.data
  },
  getChangelog: async (): Promise<AppChangelog> => {
    const response = await api.get<AppChangelog>('/api/meta/changelog')
    return response.data
  },
}

export const rabbitApi = {
  getTarget: async (name: string): Promise<RabbitCluster> => {
    const response = await api.get<RabbitCluster>(`/api/rabbitmq/${encodeURIComponent(name)}`)
    return response.data
  },
  purgeQueue: async (name: string, payload: RabbitQueueActionRequest): Promise<RabbitQueueActionResponse> => {
    const response = await api.post<RabbitQueueActionResponse>(
      `/api/rabbitmq/${encodeURIComponent(name)}/queues/purge`,
      payload,
    )
    return response.data
  },
  deleteQueue: async (name: string, payload: RabbitQueueActionRequest): Promise<RabbitQueueActionResponse> => {
    const response = await api.post<RabbitQueueActionResponse>(
      `/api/rabbitmq/${encodeURIComponent(name)}/queues/delete`,
      payload,
    )
    return response.data
  },
}

export const redisApi = {
  getTarget: async (name: string): Promise<RedisCluster> => {
    const response = await api.get<RedisCluster>(`/api/redis/${encodeURIComponent(name)}`)
    return response.data
  },
  scanKeys: async (name: string, pattern: string, cursor = 0, count = 100, db = 0): Promise<ScanResponse> => {
    console.info('[gryadka][redis-search] api.scanKeys:request', { name, pattern, cursor, count, db })
    const response = await api.get<ScanResponse>(`/api/redis/${encodeURIComponent(name)}/keys`, {
      params: { pattern, cursor, count, db },
    })
    console.info('[gryadka][redis-search] api.scanKeys:response', {
      name,
      pattern,
      cursorIn: cursor,
      cursorOut: response.data.cursor,
      keysCount: response.data.keys.length,
      db,
    })
    return response.data
  },
  deleteKeys: async (name: string, keys: string[], db = 0, pattern?: string): Promise<{ deleted: number }> => {
    const response = await api.delete<{ deleted: number }>(`/api/redis/${encodeURIComponent(name)}/keys`, {
      data: { keys, db, pattern },
    })
    return response.data
  },
  getKeyValue: async (name: string, key: string, db = 0): Promise<RedisKeyValueResponse> => {
    const response = await api.get<RedisKeyValueResponse>(`/api/redis/${encodeURIComponent(name)}/keys/${encodeURIComponent(key)}/value`, {
      params: { db },
    })
    return response.data
  },
}

export interface HAProxyServer {
  server_name: string
  status: string
  weight?: number
  act?: number
  bck?: number
  scur?: number
  smax?: number
  slim?: number
  stot?: number
  bin?: number
  bout?: number
  check_status?: string
  check_code?: number
  downtime?: number
  lastchg?: number
}

export interface HAProxyProxy {
  name: string
  total_servers: number
  servers_up: number
  servers_down: number
  sessions_current?: number
  port?: number
  servers: HAProxyServer[]
}

export interface HAProxyOverview {
  total_proxies: number
  total_servers: number
  servers_up: number
  servers_down: number
  total_sessions?: number
  frontend_sessions?: number
  frontend_sessions_limit?: number
  backend_sessions?: number
  current_connections?: number
  connections_limit?: number
  version?: string
}

export interface HAProxyPod {
  url: string
  label: string
  overview: HAProxyOverview
  proxies: HAProxyProxy[]
}

export interface HAProxyIngress {
  name: string
  namespace: string
  host: string
  path?: string
  path_type?: string
  url: string
  service_name: string
  service_port?: string
}

export interface K8sPodState {
  name?: string
  uid?: string
  phase?: string
  status_detail?: string
  ready: boolean
  terminating: boolean
  restarts?: number
  start_time?: string
  resources?: KubernetesPodResources | null
}

export interface HAProxyCluster {
  name: string
  overview: HAProxyOverview
  proxies: HAProxyProxy[]
  pods?: HAProxyPod[]
  k8s_pods?: K8sPodState[]
  ingresses?: HAProxyIngress[]
  last_update?: string
  restart_supported?: boolean
}

export interface HAProxyOperationResponse {
  operation_id: string
  status: string
  message: string
  target_name: string
  pod_name?: string
  deployment_name?: string
}

export interface HAProxyOperationTimelineEvent {
  status: string
  progress: string
  message: string
  timestamp: string
}

export interface HAProxyOperationStatus {
  operation_id: string
  type: string
  status: string
  progress: string
  message: string
  target_name: string
  pod_name?: string
  deployment_name?: string
  replacement_pod_name?: string
  created_at: string
  updated_at: string
  completed_at?: string
  timeline: HAProxyOperationTimelineEvent[]
}

export const haproxyApi = {
  getTarget: async (name: string): Promise<HAProxyCluster> => {
    const response = await api.get<HAProxyCluster>(`/api/haproxy/${encodeURIComponent(name)}`)
    return response.data
  },
  restartPod: async (targetName: string, podName: string): Promise<HAProxyOperationResponse> => {
    const response = await api.post<HAProxyOperationResponse>(
      `/api/haproxy/${encodeURIComponent(targetName)}/pods/${encodeURIComponent(podName)}/restart`,
    )
    return response.data
  },
  rolloutRestart: async (targetName: string): Promise<HAProxyOperationResponse> => {
    const response = await api.post<HAProxyOperationResponse>(
      `/api/haproxy/${encodeURIComponent(targetName)}/rollout-restart`,
    )
    return response.data
  },
  getOperationStatus: async (operationId: string): Promise<HAProxyOperationStatus> => {
    const response = await api.get<HAProxyOperationStatus>(
      `/api/haproxy/operations/${encodeURIComponent(operationId)}`,
    )
    return response.data
  },
  getOperations: async (targetName: string, limit = 10): Promise<HAProxyOperationStatus[]> => {
    const response = await api.get<HAProxyOperationStatus[]>(
      `/api/haproxy/${encodeURIComponent(targetName)}/operations`,
      { params: { limit } },
    )
    return response.data
  },
}

export interface RedpandaConsumerGroup {
  group_id: string
  lag: number | null
  partitions: Array<{ topic: string; partition: number; committed_offset: number | null; end_offset: number | null; lag: number | null }>
  state: string
}

export interface RedpandaBroker {
  node_id: number
  host: string
  infrastructure?: InfrastructureMetrics | null
  node_exporter?: NodeExporterMetrics
  version?: string
  role?: 'controller' | 'broker'
  status?: string // UP | DOWN from cluster_view is_alive
  num_cores?: number
  disk_free_bytes?: number
  disk_total_bytes?: number
  disk_used_percent?: number
  memory_allocated_bytes?: number
  memory_available_bytes?: number
  memory_used_percent?: number
  cpu_percent?: number
  read_bytes_rate?: number | null
  write_bytes_rate?: number | null
  records_fetched_rate?: number | null
  records_produced_rate?: number | null
  disk_read_ops_rate?: number | null
  disk_write_ops_rate?: number | null
  partitions_moving_from?: number | null
  partitions_moving_to?: number | null
  under_replicated_replicas?: number | null
}

export interface RedpandaCluster {
  name: string
  overview: {
    total_groups: number
    groups_with_lag: number
    total_lag: number | null
    max_group_lag: number | null
    lag_complete?: boolean
    version?: string
    version_mismatch?: boolean
    controller_id?: number
    disk_free_bytes?: number
    disk_total_bytes?: number
    disk_used_percent?: number
    memory_allocated_bytes?: number
    memory_available_bytes?: number
    memory_used_percent?: number
    cpu_percent?: number
    num_cores?: number
    read_bytes_rate?: number | null
    write_bytes_rate?: number | null
    records_fetched_rate?: number | null
    records_produced_rate?: number | null
    disk_read_ops_rate?: number | null
    disk_write_ops_rate?: number | null
    partitions_moving_from?: number | null
    partitions_moving_to?: number | null
    under_replicated_replicas?: number | null
  }
  brokers?: RedpandaBroker[]
  consumer_groups: RedpandaConsumerGroup[]
  last_update?: string
  ui_url?: string
}

export const redpandaApi = {
  getTarget: async (name: string): Promise<RedpandaCluster> => {
    const response = await api.get<RedpandaCluster>(`/api/redpanda/${encodeURIComponent(name)}`)
    return response.data
  },
}

export interface ElasticsearchNode {
  node_id: string
  name: string
  internal_name?: string
  infrastructure?: InfrastructureMetrics | null
  node_exporter?: NodeExporterMetrics
  role?: string
  docs_count: number
  store_size_bytes: number
  heap_used_percent?: number
  heap_used_bytes?: number
  heap_max_bytes?: number
  cpu_percent?: number
  mem_percent?: number
  disk_used_percent?: number
  shards_count?: number | null
  indexing_rate?: number | null
  search_rate?: number | null
  fs_read_bytes_rate?: number | null
  fs_write_bytes_rate?: number | null
  search_rejected?: number | null
  write_rejected?: number | null
}

export interface ElasticsearchCluster {
  name: string
  overview: {
    status: string
    number_of_nodes: number
    number_of_data_nodes: number
    active_primary_shards: number
    active_shards: number
    relocating_shards?: number
    unassigned_shards: number
    total_docs: number
    total_store_size_bytes: number
    cluster_name: string
    version?: string
    indexing_rate?: number | null
    search_rate?: number | null
    fs_read_bytes_rate?: number | null
    fs_write_bytes_rate?: number | null
  }
  nodes: ElasticsearchNode[]
  last_update?: string
  ui_url?: string
  monitoring_url?: string
}

export const elasticsearchApi = {
  getTarget: async (name: string): Promise<ElasticsearchCluster> => {
    const response = await api.get<ElasticsearchCluster>(`/api/elasticsearch/${encodeURIComponent(name)}`)
    return response.data
  },
}

export interface EmqxOverview {
  status: string
  nodes_total: number
  nodes_up: number
  nodes_down?: number
  connections: number
  live_connections?: number
  clients?: number | null
  sessions?: number | null
  subscriptions?: number
  topics?: number
  messages_received_rate?: number | null
  messages_sent_rate?: number | null
  bytes_received_rate?: number | null
  bytes_sent_rate?: number | null
  packets_received_rate?: number | null
  packets_sent_rate?: number | null
  api_version?: number | null
  namespace?: string | null
  version?: string | null
  version_mismatch?: boolean
  pods_total?: number
  pods_ready?: number
  pods_not_ready?: number
}

export interface EmqxNode {
  name: string
  host?: string
  source?: string
  status: string
  version?: string | null
  uptime_seconds?: number | null
  pod_start_time?: string | null
  connections?: number | null
  live_connections?: number | null
  clients?: number | null
  sessions?: number | null
  subscriptions?: number | null
  topics?: number | null
  messages_received_rate?: number | null
  messages_sent_rate?: number | null
  bytes_received_rate?: number | null
  bytes_sent_rate?: number | null
  packets_received_rate?: number | null
  packets_sent_rate?: number | null
  infrastructure?: InfrastructureMetrics | null
  node_exporter?: NodeExporterMetrics
  resources?: KubernetesPodResources | null
}

export interface EmqxListener {
  node?: string | null
  protocol?: string | null
  bind?: string | null
  port?: number | null
  status?: string | null
  current_connections?: number | null
  max_connections?: number | null
}

export interface EmqxCluster {
  name: string
  overview: EmqxOverview
  nodes: EmqxNode[]
  k8s_pods?: K8sPodState[]
  listeners?: EmqxListener[]
  last_update?: string
  ui_url?: string
  errors?: string[]
}

export const emqxApi = {
  getTarget: async (name: string): Promise<EmqxCluster> => {
    const response = await api.get<EmqxCluster>(`/api/emqx/${encodeURIComponent(name)}`)
    return response.data
  },
}

export const statsApi = {
  getStats: async (): Promise<ClusterStats> => {
    const response = await api.get<ClusterStats>('/api/stats')
    return response.data
  },
}

export const healthApi = {
  check: async (): Promise<{ status: string; redis: string }> => {
    const response = await api.get('/api/health')
    return response.data
  },
}

// Argo Rollout API
import type {
  ArgoRolloutList,
  DeploymentInfo,
  RolloutInfo,
  RolloutOperationResponse,
} from '../types/argo_rollout'

export const argoRolloutApi = {
  getRollouts: async (targetName: string, namespace?: string): Promise<ArgoRolloutList> => {
    const params = namespace ? { namespace } : {}
    const response = await api.get<ArgoRolloutList>(`/api/argo_rollout/${encodeURIComponent(targetName)}`, { params })
    return response.data
  },

  getRolloutDetail: async (targetName: string, rolloutName: string, namespace?: string): Promise<RolloutInfo> => {
    const params = namespace ? { namespace } : {}
    const response = await api.get<RolloutInfo>(`/api/argo_rollout/${encodeURIComponent(targetName)}/${encodeURIComponent(rolloutName)}`, { params })
    return response.data
  },

  getDeploymentDetail: async (targetName: string, deploymentName: string, namespace?: string): Promise<DeploymentInfo> => {
    const params = namespace ? { namespace } : {}
    const response = await api.get<DeploymentInfo>(`/api/argo_rollout/${encodeURIComponent(targetName)}/deployment/${encodeURIComponent(deploymentName)}`, { params })
    return response.data
  },

  promoteRollout: async (targetName: string, rolloutName: string, namespace?: string): Promise<RolloutOperationResponse> => {
    const params = namespace ? { namespace } : {}
    const response = await api.post<RolloutOperationResponse>(`/api/argo_rollout/${encodeURIComponent(targetName)}/${encodeURIComponent(rolloutName)}/promote`, {}, { params })
    return response.data
  },

  promoteFullRollout: async (targetName: string, rolloutName: string, namespace?: string): Promise<RolloutOperationResponse> => {
    const params = namespace ? { namespace } : {}
    const response = await api.post<RolloutOperationResponse>(`/api/argo_rollout/${encodeURIComponent(targetName)}/${encodeURIComponent(rolloutName)}/promote-full`, {}, { params })
    return response.data
  },

  abortRollout: async (targetName: string, rolloutName: string, namespace?: string): Promise<RolloutOperationResponse> => {
    const params = namespace ? { namespace } : {}
    const response = await api.post<RolloutOperationResponse>(`/api/argo_rollout/${encodeURIComponent(targetName)}/${encodeURIComponent(rolloutName)}/abort`, {}, { params })
    return response.data
  },

  retryRollout: async (targetName: string, rolloutName: string, namespace?: string): Promise<RolloutOperationResponse> => {
    const params = namespace ? { namespace } : {}
    const response = await api.post<RolloutOperationResponse>(`/api/argo_rollout/${encodeURIComponent(targetName)}/${encodeURIComponent(rolloutName)}/retry`, {}, { params })
    return response.data
  },

  restartRollout: async (targetName: string, rolloutName: string, namespace?: string): Promise<RolloutOperationResponse> => {
    const params = namespace ? { namespace } : {}
    const response = await api.post<RolloutOperationResponse>(`/api/argo_rollout/${encodeURIComponent(targetName)}/${encodeURIComponent(rolloutName)}/restart`, {}, { params })
    return response.data
  },

  restartDeployment: async (targetName: string, deploymentName: string, namespace?: string): Promise<RolloutOperationResponse> => {
    const params = namespace ? { namespace } : {}
    const response = await api.post<RolloutOperationResponse>(`/api/argo_rollout/${encodeURIComponent(targetName)}/deployment/${encodeURIComponent(deploymentName)}/restart`, {}, { params })
    return response.data
  },
}

export default api


export interface PostgresqlDatabase {
  name: string; size_bytes: number; connections: number; commits: number; rollbacks: number;
  deadlocks: number; cache_hit_percent?: number | null;
}
export interface PostgresqlNode {
  name: string; status: string; version?: string; in_recovery?: boolean; uptime_seconds?: number;
  infrastructure?: InfrastructureMetrics; node_exporter?: NodeExporterMetrics; connected_database?: string; server_host?: string;
  max_connections?: number; monitoring_role?: string; error?: string; replay_age_seconds?: number | null;
  activity?: { connections: number; active: number; idle_in_transaction: number; waiting_for_lock: number };
  databases?: PostgresqlDatabase[];
  replication?: { application_name: string; state: string; sync_state: string; replay_lag_bytes?: number | null }[];
}
export interface PostgresqlTarget {
  name: string; overview: { status: string; nodes_up: number; nodes_total: number };
  nodes: PostgresqlNode[]; last_update: string;
}
export interface HbaRule {
  line_number: number; type?: string; database?: string[]; user_name?: string[];
  address?: string; netmask?: string; auth_method?: string; invalid: boolean;
}
export interface PostgresqlTable {
  schema: string; name: string; kind: string; estimated_rows: number | null; table_bytes: number; index_bytes: number
}
export interface PostgresqlIndex {
  schema: string; table_name: string; name: string; is_unique: boolean; is_primary: boolean; is_valid: boolean;
  size_bytes: number; columns: string[]
}
export interface PostgresqlCatalog { database: string; tables: PostgresqlTable[]; indexes: PostgresqlIndex[]; truncated: boolean }
export interface PostgresqlRole { name: string; login: boolean; superuser: boolean; create_database: boolean;
  create_role: boolean; connection_limit: number; memberships: string[] }
export const postgresqlApi = {
  getDatabasesOverview: async (): Promise<DatabasesOverview> =>
    (await api.get('/api/postgresql/databases-overview')).data,
  getCatalog: async (name: string, database?: string): Promise<PostgresqlCatalog> =>
    (await api.get(`/api/postgresql/${encodeURIComponent(name)}/catalog`, { params: { database } })).data,
  getRoles: async (name: string): Promise<{ roles: PostgresqlRole[]; truncated: boolean }> =>
    (await api.get(`/api/postgresql/${encodeURIComponent(name)}/roles`)).data,
  getTarget: async (name: string): Promise<PostgresqlTarget> =>
    (await api.get<PostgresqlTarget>(`/api/postgresql/${encodeURIComponent(name)}`)).data,
  getHba: async (name: string): Promise<{ nodes: { node: string; rules: HbaRule[] }[] }> =>
    (await api.get(`/api/postgresql/${encodeURIComponent(name)}/hba`)).data,
}


export interface KafkaVersion { value: string | null; source: 'app_info' | 'container_image' | null; reason?: string; image?: string;
  versions?: string[]; complete?: boolean }
export interface KafkaBroker { id: number; host: string; port: number; controller: boolean; infrastructure?: InfrastructureMetrics; node_exporter?: NodeExporterMetrics; version_info?: KafkaVersion }
export interface KafkaPartition { topic: string; partition: number; leader: number; replicas: number[]; isr: number[] }
export interface KafkaGroup { group_id: string; lag: number | null; status: string;
  partitions: { topic: string; partition: number; committed_offset?: number | null; end_offset?: number | null; lag: number | null }[] }
export interface KafkaTarget { name: string; overview: { status: string; cluster_id?: string;
  topics?: number; partitions?: number; under_replicated?: number; offline_partitions?: number;
  consumer_groups?: number; total_lag?: number | null; lag_complete: boolean };
  version_info?: KafkaVersion; nodes: KafkaBroker[]; partitions: KafkaPartition[]; consumer_groups: KafkaGroup[]; last_update: string; error?: string }
export const kafkaApi = {
  getTarget: async (name: string): Promise<KafkaTarget> => (await api.get(`/api/kafka/${encodeURIComponent(name)}`)).data,
}
