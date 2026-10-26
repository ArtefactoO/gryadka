import { useEffect, useState } from 'react'
import { Accordion, AccordionDetails, AccordionSummary, Alert, Box, Chip, Paper, Typography } from '@mui/material'
import { useIsFetching, useQuery, useQueryClient } from '@tanstack/react-query'
import { compactAccordionSummarySx, UiIcon, useUiMotion, useUiTheme } from '@project/ui/mui'
import { ChevronDown, ChevronUp } from '@project/ui/icons'
import { postgresqlApi, type PostgresqlCatalog, type PostgresqlTable, type PostgresqlIndex } from '../services/api'
import { formatBytes, formatNumber } from '../utils/metricFormat'
import { CatalogCard, CatalogIndexCard, CatalogIndexes } from './CatalogCards'
import MonitorQueryState from './MonitorQueryState'
import SearchToggleInput from './SearchToggleInput'
import { compactContentPaperSx } from './contentBlockStyles'

/** Database → table card → indexes, using the same hierarchy as MongoDB Collections. */
export default function PostgresqlTablesSection({ targetName, databases, initialDatabase }: {
  targetName: string; databases: string[]; initialDatabase?: string | null;
}) {
  const motion = useUiMotion()
  const { visual } = useUiTheme()
  const client = useQueryClient()
  const fetching = useIsFetching({ queryKey: ['postgresql-catalog', targetName] })
  const [expanded, setExpanded] = useState(false)
  const [expandedDb, setExpandedDb] = useState<string | null>(initialDatabase || null)
  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  useEffect(() => { setExpandedDb(initialDatabase || null) }, [initialDatabase])
  const needle = search.trim().toLowerCase()
  const cached = new Map<string, PostgresqlCatalog>()
  client.getQueriesData<PostgresqlCatalog>({ queryKey: ['postgresql-catalog', targetName] }).forEach(([key, data]) => {
    if (typeof key[2] === 'string' && data) cached.set(key[2], data)
  })
  const visible = databases.filter(name => !needle || name.toLowerCase().includes(needle)
    || cached.get(name)?.tables.some(table => `${table.schema}.${table.name}`.toLowerCase().includes(needle))
    || (fetching > 0 && name === expandedDb))
  return <Paper elevation={0} sx={{ ...compactContentPaperSx, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
    <Accordion expanded={expanded} onChange={(_, next) => setExpanded(next)} slotProps={{ transition: motion }}
      sx={{ '&:before': { display: 'none' }, width: 'fit-content', maxWidth: '100%' }}>
      <AccordionSummary sx={compactAccordionSummarySx}>
        <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 1 }}>
          <UiIcon glyph={expanded ? ChevronUp : ChevronDown} size={16} />
          <Typography variant="subtitle1">Tables &amp; Indexes</Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ pt: 0 }}>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1 }}>
          <SearchToggleInput value={search} isOpen={searchOpen} onValueChange={setSearch} onOpenChange={setSearchOpen}
            onClear={() => setSearch('')} accentColor={visual.accentColor} accentSoftBg={visual.accentSoftBg}
            inputSide="right" width={200} searchButtonTooltip="Поиск по базам и просмотренным таблицам" />
          <Typography variant="caption" color="text.secondary">Для поиска таблиц сначала откройте нужную базу.</Typography>
        </Box>
        <Box role="region" aria-label="Каталоги баз PostgreSQL" sx={{ display: 'grid', gap: 0.75, justifyItems: 'start', maxHeight: 'min(60vh, 560px)', overflow: 'auto', maxWidth: '100%', pr: 0.5 }}>
          {visible.map(name => <DatabaseTables key={name} name={name} targetName={targetName}
            expanded={expanded && (expandedDb === name || (!!needle && !name.toLowerCase().includes(needle) && cached.has(name)))}
            onChange={(_, next) => setExpandedDb(next ? name : null)}
            search={needle && !name.toLowerCase().includes(needle) ? needle : ''} />)}
          {!visible.length && <Typography variant="body2" color="text.secondary">По этому запросу ничего не найдено.</Typography>}
        </Box>
      </AccordionDetails>
    </Accordion>
  </Paper>
}

function DatabaseTables({ name, targetName, expanded, onChange, search }: {
  name: string; targetName: string; expanded: boolean;
  onChange: (event: React.SyntheticEvent, expanded: boolean) => void; search: string;
}) {
  const motion = useUiMotion()
  const query = useQuery({ queryKey: ['postgresql-catalog', targetName, name],
    queryFn: () => postgresqlApi.getCatalog(targetName, name), enabled: expanded, staleTime: 5 * 60_000 })
  const data = query.data
  const tables = [...(data?.tables || [])].filter(table => !search || `${table.schema}.${table.name}`.toLowerCase().includes(search))
    .sort((a, b) => b.table_bytes - a.table_bytes || `${a.schema}.${a.name}`.localeCompare(`${b.schema}.${b.name}`))
  return <Accordion expanded={expanded} onChange={onChange} slotProps={{ transition: motion }}
    sx={{ mb: 0.75, width: 'fit-content', maxWidth: '100%', border: '1px solid', borderColor: 'divider', boxShadow: 'none', '&:before': { display: 'none' } }}>
    <AccordionSummary aria-label={`Каталог базы ${name}`} sx={compactAccordionSummarySx}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <UiIcon glyph={expanded ? ChevronUp : ChevronDown} size={16} /><Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{name}</Typography>
      </Box>
    </AccordionSummary>
    <AccordionDetails sx={{ p: 1 }}>
      <MonitorQueryState loading={query.isFetching && !data} error={query.error} hasData={!!data} label="каталога PostgreSQL" />
      {data?.truncated && <Alert severity="info" sx={{ mb: 1 }}>Показаны первые 1000 таблиц и индексов. Список может быть неполным.</Alert>}
      <Box sx={{ display: 'grid', gap: 1, justifyItems: 'start' }}>
        {tables.map(table => <TableCard key={`${table.schema}.${table.name}`} table={table} truncated={!!data?.truncated}
          indexes={(data?.indexes || []).filter(index => index.schema === table.schema && index.table_name === table.name)} />)}
        {data && !tables.length && <Typography variant="body2" color="text.secondary">
          {search ? 'Таблицы по этому запросу не найдены.' : 'Пользовательские таблицы не найдены в доступном каталоге.'}
        </Typography>}
      </Box>
    </AccordionDetails>
  </Accordion>
}

function TableCard({ table, indexes, truncated }: { table: PostgresqlTable; indexes: PostgresqlIndex[]; truncated: boolean }) {
  const [expanded, setExpanded] = useState(false)
  return <CatalogCard title={`${table.schema}.${table.name}`} metrics={<>
    <Chip size="small" variant="outlined" label={`Estimated Rows: ${formatNumber(table.estimated_rows)}`} />
    <Chip size="small" variant="outlined" label={`Size: ${formatBytes(table.table_bytes)}`} />
    <Chip size="small" variant="outlined" label={`Index Size: ${formatBytes(table.index_bytes)}`} />
    {table.kind !== 'r' && <Chip size="small" variant="outlined" label={({ p: 'Partitioned Table', v: 'View', m: 'Materialized View' })[table.kind] || table.kind} />}
  </>}>
    <CatalogIndexes expanded={expanded} onChange={setExpanded} count={truncated ? `≥ ${indexes.length}` : indexes.length}>
      <Box sx={{ display: 'grid', gap: 0.75, justifyItems: 'start' }}>
        {indexes.map(index => <CatalogIndexCard key={index.name}>
          <Typography variant="body2" color="text.secondary">Index:</Typography><Chip label={index.name} size="small" variant="outlined" />
          <Typography variant="body2" color="text.secondary">Key:</Typography><Chip label={index.columns.join(', ') || 'N/A'} size="small" variant="outlined" />
          <Chip label={`Size: ${formatBytes(index.size_bytes)}`} size="small" variant="outlined" />
          {index.is_primary && <Chip label="PRIMARY" size="small" variant="outlined" />}
          {index.is_unique && <Chip label="UNIQUE" size="small" variant="outlined" />}
          {!index.is_valid && <Chip label="INVALID" color="warning" size="small" variant="outlined" />}
        </CatalogIndexCard>)}
        {!indexes.length && <Typography variant="body2" color="text.secondary">
          {truncated ? 'Индексы не найдены в показанной части каталога.' : 'Индексы не найдены в доступном каталоге.'}
        </Typography>}
      </Box>
    </CatalogIndexes>
  </CatalogCard>
}
