import MonitorNodesSection from './MonitorNodesSection'
import MonitorStatusBadge from './MonitorStatusBadge'
import { useEffect, useState } from 'react'
import { Alert, Box, Chip, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { DataTable, InfoTooltip, useUiTheme } from '@project/ui/mui'
import { postgresqlApi, type PostgresqlDatabase, type PostgresqlNode, type PostgresqlRole, type HbaRule } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import { useMonitorTarget } from '../hooks/useMonitorTarget'
import { formatBytes, formatNumber, formatUptime } from '../utils/metricFormat'
import { formatUpdateTime } from '../utils/formatUpdateTime'
import MonitorQueryState from './MonitorQueryState'
import MonitorSections from './MonitorSections'
import InfrastructureUsage, { hasInfrastructure, InfrastructureIndicator } from './InfrastructureUsage'
import NeutralValueBadge from './NeutralValueBadge'
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import DatabasesList from './DatabasesList'
import PostgresqlTablesSection from './PostgresqlTablesSection'

type DatabaseRow = PostgresqlDatabase & { node: string }

export default function PostgresqlView({ targetName, highlightDatabaseName }: { targetName: string; highlightDatabaseName?: string | null }) {
  const [tab, setTab] = useState(0)
  const [database, setDatabase] = useState('')
  const { hasPermission } = useAuth()
  const canReadHba = hasPermission('postgresql.hba.read')
  const { visual } = useUiTheme()
  const query = useMonitorTarget('postgresql', targetName, postgresqlApi.getTarget)
  const databases: DatabaseRow[] = query.data?.nodes.flatMap(node => (node.databases || []).map(db => ({ ...db, node: node.name }))) || []
  const names = [...new Set(databases.map(db => db.name))].sort()
  const selectedDatabase = names.includes(database) ? database : null
  const roles = useQuery({ queryKey: ['postgresql-roles', targetName], queryFn: () => postgresqlApi.getRoles(targetName),
    enabled: tab === 2, staleTime: 30_000 })
  const hba = useQuery({ queryKey: ['postgresql-hba', targetName], queryFn: () => postgresqlApi.getHba(targetName),
    enabled: tab === 3 && canReadHba, staleTime: 0, gcTime: 0 })
  useEffect(() => { setTab(0); setDatabase('') }, [targetName])
  useEffect(() => {
    if (highlightDatabaseName) { setTab(0); setDatabase(highlightDatabaseName) }
  }, [targetName, highlightDatabaseName])
  useEffect(() => { if (!canReadHba && tab === 3) setTab(0) }, [canReadHba, tab])
  const data = query.data
  if (!data) return <MonitorQueryState loading={query.isLoading} error={query.error} hasData={!!data} label="PostgreSQL" />
  const versions = [...new Set(data.nodes.flatMap(node => node.version ? [node.version] : []))]
  return <Box>
    {query.error && <Alert severity="warning" sx={{ mb: 2 }}>Не удалось обновить PostgreSQL. Показаны последние полученные данные.</Alert>}
    <VersionAndUpdateBlock versionChips={<Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
      {versions.map(version => <InfoTooltip key={version} title={version}>
        <Chip label={`PostgreSQL Version: ${version.split(' ')[0]}`} variant="outlined" size="small" />
      </InfoTooltip>)}
    </Box>} updateTime={formatUpdateTime(data.last_update)} refreshTarget={{ targetType: 'postgresql', targetName }}
      accentColor={visual.accentColor} accentSoftBg={visual.accentSoftBg} />
    <MonitorSections labels={['Databases', 'Tables & Indexes', 'Roles', ...(canReadHba ? ['HBA'] : [])]}
      value={tab} onChange={setTab} label="postgresql-sections">
      {tab === 0 && <DatabasesList databases={names} selectedDatabase={selectedDatabase}
        highlightDatabaseName={highlightDatabaseName} accentColor={visual.accentColor}
        onSelectDatabase={name => setDatabase(previous => previous === name ? '' : name)}
        renderDatabaseDetails={name => <DatabaseStats rows={databases.filter(db => db.name === name)} name={name} />} />}
      {tab === 1 && <PostgresqlTablesSection key={targetName} targetName={targetName} databases={names} initialDatabase={selectedDatabase} />}
      {tab === 2 && <>
        <MonitorQueryState loading={roles.isFetching && !roles.data} error={roles.error} hasData={!!roles.data} label="ролей PostgreSQL" />
        {roles.data && <DataTable<PostgresqlRole> embedded title="Roles" rows={roles.data.roles} getRowId={role => role.name} columns={[
          { id: 'name', label: 'Role', render: role => role.name },
          { id: 'login', label: 'LOGIN', render: role => role.login ? 'Да' : 'Нет' },
          { id: 'memberships', label: 'Memberships', render: role => role.memberships.join(', ') || '—' },
          { id: 'superuser', label: 'Superuser', render: role => role.superuser ? 'Да' : 'Нет' },
          { id: 'createdb', label: 'Create DB', render: role => role.create_database ? 'Да' : 'Нет' },
          { id: 'createrole', label: 'Create role', render: role => role.create_role ? 'Да' : 'Нет' },
          { id: 'limit', label: 'Connection Limit', render: role => role.connection_limit < 0 ? 'Без лимита' : formatNumber(role.connection_limit) },
        ]} />}
        {roles.data && !roles.data.roles.length && <Alert severity="info">Роли в доступном каталоге не найдены.</Alert>}
        {roles.data?.truncated && <Alert severity="info">Показаны первые 1000 ролей.</Alert>}
      </>}
      {tab === 3 && canReadHba && <Box sx={{ display: 'grid', gap: 2 }}>
        <Alert severity="info">HBA показывает текущее содержимое файлов. Оно может отличаться от загруженной конфигурации. Секретные параметры исключены.</Alert>
        <MonitorQueryState loading={hba.isFetching} error={hba.error} hasData={!!hba.data} label="HBA" />
        {!hba.error && hba.data?.nodes.map(node => <DataTable<HbaRule> embedded key={node.node} title={`HBA · ${node.node}`} rows={node.rules} getRowId={rule => String(rule.line_number)} columns={[
          { id: 'line', label: 'Line', isIdentityColumn: false, render: rule => rule.line_number },
          { id: 'type', label: 'Type', render: rule => rule.type || 'N/A' },
          { id: 'database', label: 'Databases', render: rule => rule.database?.join(', ') || 'N/A' },
          { id: 'users', label: 'Roles', render: rule => rule.user_name?.join(', ') || 'N/A' },
          { id: 'address', label: 'Address / Mask', render: rule => [rule.address, rule.netmask].filter(Boolean).join(' / ') || 'local' },
          { id: 'auth', label: 'Auth Method', render: rule => rule.auth_method || 'N/A' },
          { id: 'valid', label: 'Validation', render: rule => rule.invalid ? <Chip size="small" color="error" label="Ошибка правила" /> : 'OK' },
        ]} />)}
      </Box>}
    </MonitorSections>
    <MonitorNodesSection empty={!data.nodes.length}>

      <DataTable<PostgresqlNode> title="Nodes" titleAdornment={<InfrastructureIndicator nodes={data.nodes} />} notice={data.overview.status !== 'UP' && <Alert severity={data.overview.status === 'DOWN' ? 'error' : 'warning'}>
        Доступно узлов: {data.overview.nodes_up} из {data.overview.nodes_total}.
      </Alert>} rows={data.nodes} getRowId={node => node.name} columns={[
        { id: 'host', label: 'Host', render: node => node.name.replace(/:\d+$/, '') },
        { id: 'resources', label: 'Resources', hidden: !data.nodes.some(hasInfrastructure), align: 'center', render: node => <InfrastructureUsage node={node} /> },
        { id: 'port', label: 'Port', align: 'center', render: node => <NeutralValueBadge>{node.name.match(/:(\d+)$/)?.[1] || '5432'}</NeutralValueBadge> },
        { id: 'status', label: 'Status', render: node => <MonitorStatusBadge status={node.status} /> },
        { id: 'role', label: 'Role', render: node => <Chip size="small" variant="outlined" label={node.status === 'UP' ? node.in_recovery ? 'STANDBY' : 'PRIMARY' : 'N/A'} /> },
        { id: 'connections', label: 'Connections / Limit', render: node => <NeutralValueBadge>{formatNumber(node.activity?.connections)} / {formatNumber(node.max_connections)}</NeutralValueBadge> },
        { id: 'active', label: 'Active', render: node => <NeutralValueBadge>{formatNumber(node.activity?.active)}</NeutralValueBadge> },
        { id: 'locks', label: 'Lock Waits', render: node => <NeutralValueBadge>{formatNumber(node.activity?.waiting_for_lock)}</NeutralValueBadge> },
        { id: 'uptime', label: 'Uptime', render: node => <NeutralValueBadge>{formatUptime(node.uptime_seconds)}</NeutralValueBadge> },
      ]} expandable={{ renderExpanded: node => <Box sx={{ display: 'grid', gap: 1 }}>
        {node.error && <Alert severity="warning">{node.error}</Alert>}
        <Typography variant="body2">Monitoring Role: {node.monitoring_role || 'N/A'} · Connected Database: {node.connected_database || 'N/A'}</Typography>
        <Typography variant="body2">Idle in transaction: {formatNumber(node.activity?.idle_in_transaction)}</Typography>
        {node.in_recovery && <Typography variant="body2">Возраст replay: {formatNumber(node.replay_age_seconds)} s. Без новых транзакций это не измерение lag.</Typography>}
        {(node.replication || []).map((replica, index) => <Typography key={`${replica.application_name}-${index}`} variant="body2">
          {replica.application_name}: {replica.state}, {replica.sync_state}, replay lag <NeutralValueBadge>{formatBytes(replica.replay_lag_bytes)}</NeutralValueBadge>
        </Typography>)}
        {node.status === 'UP' && !node.in_recovery && !node.replication?.length && <Typography variant="body2" color="text.secondary">Подключенные реплики не обнаружены.</Typography>}
      </Box> }} />
    </MonitorNodesSection>
  </Box>
}

function DatabaseStats({ rows, name }: { rows: DatabaseRow[]; name: string }) {
  return <Box component="dl" aria-label={`Статистика базы ${name}`} sx={{ display: 'grid', gap: 1, m: 0 }}>
    {rows.map(db => <Box key={db.node} sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', alignItems: 'baseline' }}>
      {rows.length > 1 && <Typography variant="caption" color="text.secondary">Host: {db.node}</Typography>}
      {[
        ['Size', formatBytes(db.size_bytes)], ['Connections', formatNumber(db.connections)],
        ['Cache Hit', db.cache_hit_percent == null ? 'N/A' : `${db.cache_hit_percent.toLocaleString('ru-RU')}%`],
        ['Commits / Rollbacks', `${formatNumber(db.commits)} / ${formatNumber(db.rollbacks)}`],
        ['Deadlocks', formatNumber(db.deadlocks)],
      ].map(([label, value]) => <Box key={label} component="div" sx={{ display: 'inline-flex', gap: 0.75, alignItems: 'baseline' }}>
        <Typography component="dt" variant="caption" color="text.secondary">{label}</Typography>
        <Typography component="dd" variant="body2" sx={{ m: 0, fontVariantNumeric: 'tabular-nums' }}>{value}</Typography>
      </Box>)}
    </Box>)}
  </Box>
}
