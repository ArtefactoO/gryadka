import { useUiMotion } from "@project/ui/mui"
import { UiIcon } from "@project/ui/mui"
import { useEffect, useMemo, useState } from 'react'
import {
  Box,
  Paper,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Chip,
  CircularProgress,
} from '@mui/material'
import { ChevronDown as ExpandMoreIcon, ChevronUp as ExpandLessIcon } from "@project/ui/icons"
import { useQueries, useQuery } from '@tanstack/react-query'
import { clustersApi, Collection, CollectionIndex } from '../services/api'
import SearchToggleInput from './SearchToggleInput'
import { CatalogCard, CatalogIndexCard, CatalogIndexes } from './CatalogCards'
import { compactContentPaperSx } from './contentBlockStyles'
import { compactAccordionSummarySx } from "@project/ui/mui"
import { useUiTheme } from "@project/ui/mui"

interface CollectionsSectionProps {
  databases: string[]
  clusterName: string
  accentColor?: string
  accentSoftBg?: string
}

const CollectionsSection = ({ databases, clusterName, accentColor, accentSoftBg }: CollectionsSectionProps) => {
  const { visual } = useUiTheme()
  const accordionTransitionProps = useUiMotion()
  const effectiveAccentColor = accentColor || visual.accentColor
  const effectiveAccentSoftBg = accentSoftBg || visual.accentSoftBg
  const [expandedDb, setExpandedDb] = useState<string | false>(false)
  const [expanded, setExpanded] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const searchTrimmed = searchQuery.trim().toLowerCase()
  const effectiveSearch = expanded ? searchTrimmed : ''

  const collectionSearchQueries = useQueries({
    queries: databases.map((dbName) => ({
      queryKey: ['collections', clusterName, dbName],
      queryFn: () => clustersApi.getDatabaseCollections(clusterName, dbName),
      enabled: Boolean(effectiveSearch),
      staleTime: 5 * 60 * 1000,
    })),
  })

  const collectionSearchByDb = useMemo(() => {
    const result = new Map<string, Collection[] | undefined>()
    databases.forEach((dbName, index) => {
      result.set(dbName, collectionSearchQueries[index]?.data)
    })
    return result
  }, [databases, collectionSearchQueries])

  const visibleDatabases = databases.filter((dbName) => {
    if (!effectiveSearch) return true
    if (dbName.toLowerCase().includes(effectiveSearch)) return true
    return (collectionSearchByDb.get(dbName) || []).some((collection) =>
      collection.name.toLowerCase().includes(effectiveSearch)
    )
  })

  const isCollectionSearchLoading = Boolean(effectiveSearch) && collectionSearchQueries.some(
    (query) => query.isLoading || query.isFetching,
  )

  useEffect(() => {
    if (!expanded) {
      setSearchOpen(false)
    }
  }, [expanded])

  const handleChange =
    (dbName: string) =>
    (_event: React.SyntheticEvent, isExpanded: boolean) => {
      setExpandedDb(isExpanded ? dbName : false)
    }

  if (!databases || databases.length === 0) {
    return (
      <Paper elevation={0} variant="outlined" sx={{ p: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          Коллекции не найдены.
        </Typography>
      </Paper>
    )
  }

  return (
    <Paper
      elevation={0}
      sx={{
        ...compactContentPaperSx,
        border: '1px solid',
        borderColor: 'divider',
        boxShadow: 'none',
      }}
    >
      <Accordion
        expanded={expanded}
        onChange={(_event, isExpanded) => setExpanded(isExpanded)}
        slotProps={{ transition: accordionTransitionProps }}
        sx={{  '&:before': { display: 'none' }, width: 'fit-content', maxWidth: '100%' }}
      >
        <AccordionSummary sx={compactAccordionSummarySx}>
          <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 1, maxWidth: '100%' }}>
            {expanded ? <UiIcon glyph={ExpandLessIcon} size={16} /> : <UiIcon glyph={ExpandMoreIcon} size={16} />}
            <Typography variant="subtitle1">Collections</Typography>
            {expanded && (
              <Box sx={{ width: 186, minWidth: 186, maxWidth: 'calc(100vw - 150px)' }}>
                <SearchToggleInput
                  value={searchQuery}
                  isOpen={searchOpen}
                  onValueChange={setSearchQuery}
                  onOpenChange={setSearchOpen}
                  onClear={() => setSearchQuery('')}
                  accentColor={effectiveAccentColor}
                  accentSoftBg={effectiveAccentSoftBg}
                  inputSide="right"
                  width={150}
                  stopPropagation
                  searchButtonTooltip="Поиск по базам и коллекциям"
                />
              </Box>
            )}
          </Box>
        </AccordionSummary>
        <AccordionDetails sx={{ pt: 0 }}>
          <Box sx={{ display: 'grid', gap: 0.75, justifyItems: 'start' }}>
            {visibleDatabases.length > 0 ? (
              visibleDatabases.map((dbName) => (
                <DatabaseAccordion
                  key={dbName}
                  dbName={dbName}
                  clusterName={clusterName}
                  accentColor={effectiveAccentColor}
                  searchQuery={effectiveSearch && !dbName.toLowerCase().includes(effectiveSearch) ? effectiveSearch : ''}
                  prefetchedCollections={effectiveSearch ? collectionSearchByDb.get(dbName) : undefined}
                  expanded={Boolean(effectiveSearch) || expandedDb === dbName}
                  onChange={handleChange(dbName)}
                />
              ))
            ) : isCollectionSearchLoading ? (
              <Typography variant="body2" color="text.secondary">
                Ищем коллекции...
              </Typography>
            ) : (
              <Typography variant="body2" color="text.secondary">
                По этому запросу ничего не найдено.
              </Typography>
            )}
          </Box>
        </AccordionDetails>
      </Accordion>
    </Paper>
  )
}

const INDEXES_REFRESH_MS = 10 * 60 * 1000

interface DatabaseAccordionProps {
  dbName: string
  clusterName: string
  accentColor?: string
  searchQuery?: string
  prefetchedCollections?: Collection[]
  expanded: boolean
  onChange: (event: React.SyntheticEvent, isExpanded: boolean) => void
}

const DatabaseAccordion = ({
  dbName,
  clusterName,
  accentColor,
  searchQuery = '',
  prefetchedCollections,
  expanded,
  onChange,
}: DatabaseAccordionProps) => {
  const accordionTransitionProps = useUiMotion()

  const { data: collections, isLoading, error } = useQuery({
    queryKey: ['collections', clusterName, dbName],
    queryFn: () => clustersApi.getDatabaseCollections(clusterName, dbName),
    enabled: expanded && prefetchedCollections === undefined,
    staleTime: 5 * 60 * 1000,
  })

  const collectionsPayload = prefetchedCollections ?? collections
  const isCollectionsLoading = prefetchedCollections === undefined && isLoading

  const visibleCollections = [...(collectionsPayload || [])]
    .filter((collection) => !searchQuery || collection.name.toLowerCase().includes(searchQuery))
    .sort((a, b) => {
      const sizeA = a.size || 0
      const sizeB = b.size || 0
      if (sizeB !== sizeA) return sizeB - sizeA
      return a.name.localeCompare(b.name)
    })

  return (
    <Accordion
      expanded={expanded}
      onChange={onChange}
      slotProps={{ transition: accordionTransitionProps }}
      sx={{

        mb: 0.75,
        width: 'fit-content',
        maxWidth: '100%',
        border: '1px solid',
        borderColor: 'divider',
        boxShadow: 'none',
        '&:before': { display: 'none' },
      }}
    >
      <AccordionSummary sx={compactAccordionSummarySx}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {expanded ? <UiIcon glyph={ExpandLessIcon} size={16} /> : <UiIcon glyph={ExpandMoreIcon} size={16} />}
          <Typography variant="body2">{dbName}</Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails sx={{ p: 1 }}>
        {isCollectionsLoading ? (
          <Box display="flex" justifyContent="center" p={2}>
            <CircularProgress size={24} sx={{ color: accentColor }} />
          </Box>
        ) : error ? (
          <Typography variant="body2" color="error">
            Не удалось загрузить коллекции: {error instanceof Error ? error.message : 'Неизвестная ошибка'}
          </Typography>
        ) : collectionsPayload && collectionsPayload.length > 0 ? (
          <Box sx={{ display: 'grid', gap: 1, justifyItems: 'start' }}>
            {visibleCollections.length > 0 ? (
              visibleCollections.map((collection) => (
                <CollectionCard
                  key={collection.name}
                  clusterName={clusterName}
                  dbName={dbName}
                  collection={collection}
                  accentColor={accentColor}
                />
              ))
            ) : (
              <Typography variant="body2" color="text.secondary">
                По этому запросу ничего не найдено.
              </Typography>
            )}
          </Box>
        ) : (
          <Typography variant="body2" color="text.secondary">
            Коллекции не найдены.
          </Typography>
        )}
      </AccordionDetails>
    </Accordion>
  )
}

interface CollectionCardProps {
  clusterName: string
  dbName: string
  collection: Collection
  accentColor?: string
}

const CollectionCard = ({ clusterName, dbName, collection, accentColor }: CollectionCardProps) => {
  const [indexesExpanded, setIndexesExpanded] = useState(false)

  const { data: indexes, isLoading: indexesLoading, error: indexesError } = useQuery({
    queryKey: ['collection-indexes', clusterName, dbName, collection.name],
    queryFn: () => clustersApi.getCollectionIndexes(clusterName, dbName, collection.name),
    // Load indexes lazily only when user opens the section.
    enabled: indexesExpanded,
    staleTime: INDEXES_REFRESH_MS,
  })

  const indexesCountLabel = !indexesExpanded
    ? (collection.indexes_count !== undefined && collection.indexes_count !== null ? String(collection.indexes_count) : '?')
    : indexesLoading
      ? '...'
    : indexesError
        ? 'N/A'
        : String((indexes || []).length)

  return <CatalogCard title={collection.name} metrics={<>
    <Chip label={`Count: ${collection.count == null ? 'N/A' : collection.count.toLocaleString('ru-RU')}`} size="small" variant="outlined" />
    <Chip label={`Size: ${collection.size == null ? 'N/A' : formatBytes(collection.size)}`} size="small" variant="outlined" />
  </>}>
    <CatalogIndexes expanded={indexesExpanded} onChange={setIndexesExpanded} count={indexesCountLabel}>
      {indexesLoading ? <Box display="flex" justifyContent="center" p={1}><CircularProgress size={18} sx={{ color: accentColor }} /></Box>
        : indexesError ? <Typography variant="body2" color="error">
          Не удалось загрузить индексы.
        </Typography>
        : indexes && indexes.length > 0 ? <Box sx={{ display: 'grid', gap: 0.75, justifyItems: 'start' }}>
          {indexes.map(indexInfo => <IndexCard key={indexInfo.name} indexInfo={indexInfo} accentColor={accentColor} />)}
        </Box> : <Typography variant="body2" color="text.secondary">Индексы не найдены.</Typography>}
    </CatalogIndexes>
  </CatalogCard>
}

interface IndexCardProps {
  indexInfo: CollectionIndex
  accentColor?: string
}

const IndexCard = ({ indexInfo, accentColor }: IndexCardProps) => {
  const systemIndex = isSystemIndex(indexInfo)

  return (
    <CatalogIndexCard>
        <Typography variant="body2" color="text.secondary">Index:</Typography>
        <Chip label={indexInfo.name || 'N/A'} size="small" variant="outlined" />
        <Typography variant="body2" color="text.secondary">Key:</Typography>
        <Chip label={indexInfo.key || 'N/A'} size="small" variant="outlined" />
        {systemIndex && (
          <Chip
            label="SYSTEM"
            size="small"
            variant="outlined"
            sx={accentColor ? { color: accentColor, borderColor: accentColor } : undefined}
          />
        )}
        {indexInfo.unique && (
          <Chip
            label="Unique"
            size="small"
            variant="outlined"
            sx={accentColor ? { color: accentColor, borderColor: accentColor } : undefined}
          />
        )}
        {indexInfo.sparse && <Chip label="Sparse" size="small" variant="outlined" />}
        {indexInfo.partial && <Chip label="Partial" size="small" variant="outlined" />}
        {indexInfo.hidden && <Chip label="Hidden" size="small" variant="outlined" />}
        {indexInfo.ttl_seconds !== undefined && indexInfo.ttl_seconds !== null && (
          <Chip label={`TTL: ${indexInfo.ttl_seconds}s`} size="small" variant="outlined" />
        )}
    </CatalogIndexCard>
  )
}

function isSystemIndex(indexInfo: CollectionIndex): boolean {
  return indexInfo.name === '_id_' && indexInfo.key.replace(/\s+/g, '') === '_id:1'
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(2)} ${sizes[i]}`
}

export default CollectionsSection
