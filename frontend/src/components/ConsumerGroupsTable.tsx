import { useState } from 'react'
import { Alert, Box, Chip, TextField } from '@mui/material'
import { DataTable, InfoControl } from '@project/ui/mui'
import { formatNumber } from '../utils/metricFormat'

export interface ConsumerPartition {
  topic: string; partition: number; committed_offset?: number | null; end_offset?: number | null; lag: number | null
}
export interface ConsumerGroup {
  group_id: string; lag: number | null; status?: string; partitions: ConsumerPartition[]
}
const Lag = ({ value }: { value?: number | null }) => <Chip size="small" variant="outlined"
  label={value == null ? 'Недоступен' : formatNumber(value)}
  color={value == null ? 'warning' : value >= 5_000_000 ? 'error' : value >= 1_500_000 ? 'warning' : 'default'} />

/** Common Kafka-protocol metadata, including unknown groups; no hidden low-lag filter. */
export default function ConsumerGroupsTable({ groups }: { groups: ConsumerGroup[] }) {
  const [search, setSearch] = useState('')
  const rows = groups.filter(group => group.group_id.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => a.lag == null ? b.lag == null ? a.group_id.localeCompare(b.group_id) : -1 : b.lag == null ? 1 : b.lag - a.lag)
  return <Box sx={{ display: 'grid', gap: 2 }}>
    <TextField label="Найти группу" size="small" value={search}
      onChange={event => setSearch(event.target.value)} sx={{ width: { xs: '100%', sm: 320 } }} />
    <DataTable<ConsumerGroup> embedded title="Consumer Groups" rows={rows} getRowId={group => group.group_id} columns={[
      { id: 'group', label: 'Group ID', render: group => group.group_id },
      { id: 'lag', label: 'Lag', align: 'right', render: group => <Lag value={group.lag} /> },
      { id: 'state', label: <InfoControl label="Пояснение к Offset Status" description="Доступность данных об offsets. Это не состояние участников Kafka-группы.">Offset Status</InfoControl>, render: group => group.status || (group.lag == null ? 'UNAVAILABLE' : 'READY') },
      { id: 'partitions', label: 'Partitions', align: 'right', render: group => formatNumber(group.partitions.length) },
    ]} expandable={{ renderExpanded: group => group.partitions.length ?
      <DataTable<ConsumerPartition> embedded title="Partition Offsets" rows={group.partitions} getRowId={part => `${part.topic}:${part.partition}`} columns={[
        { id: 'topic', label: 'Topic', render: part => part.topic },
        { id: 'partition', label: 'Partition', align: 'right', render: part => part.partition },
        { id: 'committed', label: 'Committed', align: 'right', render: part => formatNumber(part.committed_offset) },
        { id: 'end', label: 'End offset', align: 'right', render: part => formatNumber(part.end_offset) },
        { id: 'lag', label: 'Lag', align: 'right', render: part => <Lag value={part.lag} /> },
      ]} /> : <Alert severity={group.lag == null ? 'warning' : 'info'}>
        {group.lag == null ? 'Offsets этой группы недоступны. Lag не подменяется нулем.' : 'У группы нет сохраненных offsets.'}
      </Alert> }} />
    {!rows.length && <Alert severity="info">{search ? 'Группы по этому запросу не найдены.' : 'Consumer groups не обнаружены.'}</Alert>}
  </Box>
}
