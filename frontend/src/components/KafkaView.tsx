import { useEffect, useState } from 'react'
import { Alert, Box, Chip, TablePagination, TextField, Typography } from '@mui/material'
import { DataTable, useUiTheme } from '@project/ui/mui'
import { kafkaApi, type KafkaBroker, type KafkaPartition } from '../services/api'
import { useMonitorTarget } from '../hooks/useMonitorTarget'
import { formatNumber } from '../utils/metricFormat'
import { formatUpdateTime } from '../utils/formatUpdateTime'
import MonitorQueryState from './MonitorQueryState'
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import MonitorSections from './MonitorSections'
import ConsumerGroupsTable from './ConsumerGroupsTable'
import InfrastructureUsage, { hasInfrastructure, InfrastructureIndicator } from './InfrastructureUsage'
import NeutralValueBadge from './NeutralValueBadge'
import MonitorNodesSection from './MonitorNodesSection'
import MonitorStatusBadge from './MonitorStatusBadge'
import KafkaVersionBadge from './KafkaVersionBadge'

export default function KafkaView({ targetName }: { targetName: string }) {
  const [tab, setTab] = useState(0)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const { visual } = useUiTheme()
  const query = useMonitorTarget('kafka', targetName, kafkaApi.getTarget)
  useEffect(() => { setTab(0); setSearch(''); setPage(0) }, [targetName])
  const data = query.data
  if (!data) return <MonitorQueryState loading={query.isLoading} error={query.error} hasData={!!data} label="Kafka" />
  const partitions = data.partitions.filter(part => part.topic.toLowerCase().includes(search.toLowerCase()))
  const topics = [...new Set(partitions.map(part => part.topic))].map(name => ({ name, partitions: partitions.filter(part => part.topic === name) }))
  const visiblePage = Math.min(page, Math.max(0, Math.ceil(topics.length / 20) - 1))
  const labels = [
    ['Groups', data.overview.consumer_groups], ['Total lag', data.overview.total_lag],
    ['Topics', data.overview.topics], ['Partitions', data.overview.partitions],
    ['Under-replicated', data.overview.under_replicated], ['Offline', data.overview.offline_partitions],
  ] as const
  return <Box>
    {query.error && <Alert severity="warning" sx={{ mb: 2 }}>Не удалось обновить Kafka. Показаны последние полученные данные.</Alert>}
    <VersionAndUpdateBlock
      versionChips={<KafkaVersionBadge info={data.version_info} summary />}
      updateTime={formatUpdateTime(data.last_update)} refreshTarget={{ targetType: 'kafka', targetName }}
      accentColor={visual.accentColor} accentSoftBg={visual.accentSoftBg} />
    <MonitorSections labels={['Overview', 'Consumer Groups', 'Topics']} value={tab} onChange={setTab} label="kafka-sections">
      {tab === 0 && <Box sx={{ display: 'grid', gap: 2 }}>
        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
          {labels.map(([label, value]) => <Box key={label} sx={{ display: 'inline-flex', gap: 1, alignItems: 'center' }}>
            <Typography variant="body2" color="text.secondary">{label}:</Typography>
            <NeutralValueBadge>{formatNumber(value)}</NeutralValueBadge>
          </Box>)}
        </Box>
      </Box>}
      {tab === 1 && <ConsumerGroupsTable groups={data.consumer_groups} />}
      {tab === 2 && <Box sx={{ display: 'grid', gap: 2 }}>
        <TextField label="Найти топик" size="small" value={search} onChange={event => { setSearch(event.target.value); setPage(0) }} sx={{ width: { xs: '100%', sm: 320 } }} />
        <DataTable<(typeof topics)[number]> embedded title="Topics" rows={topics.slice(visiblePage * 20, (visiblePage + 1) * 20)} getRowId={topic => topic.name} columns={[
          { id: 'topic', label: 'Topic', render: topic => topic.name },
          { id: 'partitions', label: 'Partitions', render: topic => topic.partitions.length },
          { id: 'replicas', label: 'Under-replicated', render: topic => topic.partitions.filter(p => p.isr.length < p.replicas.length).length },
        ]} expandable={{ renderExpanded: topic => <DataTable<KafkaPartition> embedded title="Partitions" rows={topic.partitions} getRowId={part => String(part.partition)} columns={[
          { id: 'partition', label: 'Partition', render: part => part.partition },
          { id: 'leader', label: 'Leader', render: part => part.leader < 0 ? 'Нет лидера' : part.leader },
          { id: 'replicas', label: 'Replicas', render: part => part.replicas.join(', ') },
          { id: 'isr', label: 'ISR', render: part => part.isr.join(', ') },
        ]} /> }} />
        <TablePagination component="div" count={topics.length} page={visiblePage} onPageChange={(_, value) => setPage(value)} rowsPerPage={20} rowsPerPageOptions={[20]} labelRowsPerPage="Строк на странице" getItemAriaLabel={type => ({ first: 'Первая страница', last: 'Последняя страница', next: 'Следующая страница', previous: 'Предыдущая страница' })[type]} labelDisplayedRows={({ from, to, count }) => `${from}–${to} из ${count}`} />
        {!topics.length && <Alert severity="info">Топики по этому запросу не найдены.</Alert>}
      </Box>}
    </MonitorSections>
    <MonitorNodesSection empty={!data.nodes.length} emptyMessage="Брокеры не получены из метаданных Kafka.">

      <DataTable<KafkaBroker> title="Nodes" titleAdornment={<>
        <InfrastructureIndicator nodes={data.nodes} />
        <MonitorStatusBadge status={data.overview.status} scope="Cluster status"
          description="Общее состояние кластера по метаданным, репликации и полноте данных offsets. Отдельная проверка каждого брокера не выполняется." />
      </>} notice={(data.error || data.overview.status !== 'UP' || !data.overview.lag_complete) && <Alert
        severity={data.overview.status === 'DOWN' ? 'error' : 'warning'}>
        {data.error || (!data.overview.lag_complete
          ? 'Lag доступен не для всех групп. Неизвестные offsets и недоступные группы сохранены в списке.'
          : 'Кластер работает с ограничениями. Проверьте репликацию и доступность партиций в Overview.')}
      </Alert>} rows={data.nodes} getRowId={node => String(node.id)} columns={[
      { id: 'host', label: 'Host', render: node => node.host },
      { id: 'version', label: 'Version', render: node => <KafkaVersionBadge info={node.version_info} /> },
      { id: 'resources', label: 'Resources', hidden: !data.nodes.some(hasInfrastructure), render: node => <InfrastructureUsage node={node} /> },
      { id: 'port', label: 'Port', render: node => <NeutralValueBadge>{node.port}</NeutralValueBadge> },
      { id: 'id', label: 'Broker ID', render: node => node.id },
      { id: 'role', label: 'Role', render: node => <Chip label={node.controller ? 'CONTROLLER' : 'BROKER'} variant="outlined" size="small" /> },
    ]} />
    </MonitorNodesSection>
  </Box>
}
