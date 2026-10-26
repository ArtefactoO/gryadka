import { formatBytes } from '../utils/metricFormat'
import { useEffect, useRef, type ReactNode } from 'react'
import { Box, Paper, Typography, Chip } from '@mui/material'
import type { MongoDatabaseBackup } from '../services/api'
import { InfoTooltip } from "@project/ui/mui"
import { useUiTheme } from "@project/ui/mui"

const BACKUP_STALE_DAYS = 7
const BACKUP_STALE_BORDER_COLOR = 'var(--sl-warn)'

interface DatabasesListProps {
  databases: string[]
  backups?: Record<string, MongoDatabaseBackup>
  highlightDatabaseName?: string | null
  accentColor?: string
  selectedDatabase?: string | null
  onSelectDatabase?: (name: string) => void
  renderDatabaseDetails?: (name: string) => ReactNode
}

const formatDateTime = (value?: string | null): string => {
  if (!value) return 'N/A'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

const backupTooltip = (backup?: MongoDatabaseBackup) => {
  const last = backup?.last_completed
  return (
    <Box sx={{ display: 'grid', gap: 0.5, maxWidth: 420 }}>
      <Typography variant="caption" sx={{ fontWeight: 600 }}>
        Последний бекап: {last ? formatDateTime(last.mtime) : 'N/A'}
      </Typography>
      {last ? (
        <>
          <Typography variant="caption" sx={{ overflowWrap: 'anywhere' }}>Файл: {last.file}</Typography>
          <Typography variant="caption">Размер: {formatBytes(last.size_bytes)}</Typography>
        </>
      ) : null}
      <Typography variant="caption">Статус: {backup?.status || 'N/A'}</Typography>
      {backup?.running ? (
        <Typography variant="caption" color="warning.light" sx={{ overflowWrap: 'anywhere' }}>
          Сейчас пишется: {backup.running.file}
        </Typography>
      ) : null}
    </Box>
  )
}

const hasBackupTooltip = (backup?: MongoDatabaseBackup): boolean => {
  if (!backup) return false
  return Boolean(backup.last_completed || backup.running || backup.status)
}

const isBackupOlderThanDays = (backup: MongoDatabaseBackup | undefined, days: number): boolean => {
  const value = backup?.last_completed?.mtime
  if (!value) return false
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return false
  return Date.now() - date.getTime() > days * 24 * 60 * 60 * 1000
}

const DatabasesList = ({ databases, backups = {}, highlightDatabaseName = null, accentColor, selectedDatabase, onSelectDatabase, renderDatabaseDetails }: DatabasesListProps) => {
  const { visual } = useUiTheme()
  const effectiveAccentColor = accentColor || visual.accentColor
  const dbChipRefs = useRef<Record<string, HTMLElement | null>>({})

  useEffect(() => {
    if (!highlightDatabaseName) return
    const el = dbChipRefs.current[highlightDatabaseName]
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
    }
  }, [highlightDatabaseName, databases])

  const paperSx = {
    p: 1.25,
    border: '1px solid',
    borderColor: 'divider',
    boxShadow: 'none',
    width: 'fit-content',
    maxWidth: '100%',
  }

  if (!databases || databases.length === 0) {
    return (
      <Paper elevation={0} sx={paperSx}>
        <Typography variant="body2" color="text.secondary">
          Базы данных не найдены.
        </Typography>
      </Paper>
    )
  }

  return (
    <Paper elevation={0} sx={paperSx}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
        {databases.map((db) => {
          const isHighlighted = Boolean(highlightDatabaseName && db === highlightDatabaseName)
          const isSelected = selectedDatabase === db
          const backup = backups[db]
          const showBackupTooltip = hasBackupTooltip(backup)
          const isBackupStale = isBackupOlderThanDays(backup, BACKUP_STALE_DAYS)
          const highlightBorderColor = isBackupStale ? BACKUP_STALE_BORDER_COLOR : effectiveAccentColor
          const chip = (
            <Box
              component="span"
              ref={(el: HTMLSpanElement | null) => { dbChipRefs.current[db] = el }}
              sx={{ display: 'inline-flex' }}
            >
              <Chip
                label={db}
                size="small"
                variant="outlined"
                clickable={!!onSelectDatabase}
                onClick={onSelectDatabase ? () => onSelectDatabase(db) : undefined}
                aria-pressed={onSelectDatabase ? isSelected : undefined}
                sx={{
                  ...(isBackupStale ? { borderColor: BACKUP_STALE_BORDER_COLOR } : {}),
                  ...(isHighlighted || isSelected ? {
                    borderColor: highlightBorderColor,
                    borderWidth: 2,
                    color: 'text.primary',
                  } : {}),
                  '&:hover': {
                    ...(isBackupStale ? { borderColor: BACKUP_STALE_BORDER_COLOR } : {}),
                  },
                }}
              />
            </Box>
          )

          if (!showBackupTooltip) {
            return <Box key={db} component="span" sx={{ display: 'inline-flex' }}>{chip}</Box>
          }

          return (
            <InfoTooltip key={db} title={backupTooltip(backup)}>
              {chip}
            </InfoTooltip>
          )
        })}
      </Box>
      {selectedDatabase && renderDatabaseDetails && <Box sx={{ mt: 1, pt: 1, borderTop: '1px solid', borderColor: 'divider' }}>
        {renderDatabaseDetails(selectedDatabase)}
      </Box>}
    </Paper>
  )
}

export default DatabasesList
