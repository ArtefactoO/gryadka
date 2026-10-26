import { UiIcon } from "@project/ui/mui"
import { useState, type ReactNode } from 'react'
import { Box, IconButton, Link, Paper, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tooltip, Typography } from '@mui/material'
import { Copy as ContentCopyIcon, Trash2 as DeleteOutlineIcon, Pencil as EditOutlinedIcon, Info as InfoOutlinedIcon, ExternalLink as OpenInNewIcon } from "@project/ui/icons"

import type { RegistryAutoRow, RegistryManualRow, RegistrySourceReference } from '../services/api'
import NeutralValueBadge from './NeutralValueBadge'
import { formatDateTimeLocal } from '../utils/formatUpdateTime'

type RegistryRow = RegistryAutoRow | RegistryManualRow

interface RegistryRowsTableProps {
  title: string
  rows: RegistryRow[]
  emptyMessage: string
  accentColor?: string
  accentSoftBg?: string
  titleAction?: ReactNode
  editable?: boolean
  onEdit?: (row: RegistryManualRow) => void
  onDelete?: (row: RegistryManualRow) => void
  deletingRowId?: number | null
}

const isManualRow = (row: RegistryRow): row is RegistryManualRow => row.origin === 'manual'

const formatSourceReference = (reference: RegistrySourceReference): string => {
  const lineSuffix =
    reference.source_line_start === undefined || reference.source_line_start === null
      ? ''
      : reference.source_line_end && reference.source_line_end !== reference.source_line_start
        ? `:${reference.source_line_start}-${reference.source_line_end}`
        : `:${reference.source_line_start}`
  return `${reference.repo_name}${reference.file_path ? ` / ${reference.file_path}` : ''}${lineSuffix}`
}

const formatCompactSourceLabel = (index: number): string => (index === 0 ? 'file' : `file ${index + 1}`)

const CopyableBadge = ({
  value,
  copyValue,
  sx,
}: {
  value: string
  copyValue?: string
  sx?: Record<string, unknown>
}) => (
  <Box
    sx={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 0.5,
      maxWidth: '100%',
      '&:hover .registry-copy-action': {
        opacity: 1,
        pointerEvents: 'auto',
      },
    }}
  >
    <NeutralValueBadge
      sx={{
        maxWidth: '100%',
        height: 'auto',
        alignItems: 'flex-start',
        '& .MuiChip-label': {
          display: 'block',
          py: 0.375,
          whiteSpace: 'normal',
          overflowWrap: 'anywhere',
        },
        ...(sx || {}),
      }}
    >
      {value}
    </NeutralValueBadge>
    <Tooltip title="Копировать" arrow>
      <IconButton
        size="small"
        className="registry-copy-action"
        onClick={(event) => {
          event.preventDefault()
          event.stopPropagation()
          void navigator.clipboard.writeText(copyValue || value)
        }}
        sx={{
          p: 0.25,
          opacity: 0,
          pointerEvents: 'none',
          transition: 'opacity 0.16s ease',
          color: 'text.secondary',
        }}
        aria-label={`копировать ${value}`}
      >
        <UiIcon glyph={ContentCopyIcon} sx={{ fontSize: 14 }} />
      </IconButton>
    </Tooltip>
  </Box>
)

const renderTokenList = (values: Array<string | number | null | undefined>) => {
  const normalizedValues = values
    .map((value) => String(value ?? '').trim())
    .filter((value, index, array) => value.length > 0 && array.indexOf(value) === index)

  if (normalizedValues.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6 }}>
        -
      </Typography>
    )
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, alignItems: 'flex-start' }}>
      {normalizedValues.map((value) => (
        <CopyableBadge key={value} value={value} />
      ))}
    </Box>
  )
}

const renderBlock = (content: ReactNode) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, minWidth: 0 }}>
    {content}
  </Box>
)

const formatLocalDateTime = (value?: string | null) => {
  if (!value) {
    return null
  }
  return formatDateTimeLocal(value) || value
}

const renderDnsTooltipContent = (
  dnsValidation: NonNullable<RegistryAutoRow['dns_validation']>,
  expectedIp: string
) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, maxWidth: 360, minWidth: 260 }}>
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.25 }}>
      <Typography variant="caption" sx={{ color: 'common.white', lineHeight: 1.5 }}>
        Ожидаемый IP:
      </Typography>
      <Typography
        variant="caption"
        component="code"
        sx={{
          color: 'common.white',
          fontFamily: 'var(--sl-font-mono)',
          lineHeight: 1.5,
          userSelect: 'text',
        }}
      >
        {expectedIp}
      </Typography>
    </Box>
    {dnsValidation.resolved_ips.length ? (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, minWidth: 0 }}>
        <Typography variant="caption" sx={{ color: 'common.white', lineHeight: 1.5 }}>
          Резолвится IP:
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.75, minWidth: 0 }}>
          <Typography
            variant="caption"
            component="code"
            sx={{
              color: 'common.white',
              flex: 1,
              fontFamily: 'var(--sl-font-mono)',
              lineHeight: 1.5,
              minWidth: 0,
              overflowWrap: 'anywhere',
              whiteSpace: 'pre-wrap',
              userSelect: 'text',
            }}
          >
            {dnsValidation.resolved_ips.join(',\n')}
          </Typography>
          <IconButton
            size="small"
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              void navigator.clipboard.writeText(dnsValidation.resolved_ips.join(', '))
            }}
            sx={{ color: 'common.white', flex: '0 0 auto', mt: -0.25, p: 0.25 }}
            aria-label="копировать резолвленные IP"
          >
            <UiIcon glyph={ContentCopyIcon} sx={{ fontSize: 14 }} />
          </IconButton>
        </Box>
      </Box>
    ) : null}
    {!dnsValidation.resolved_ips.length && dnsValidation.message ? (
      <Typography variant="caption" sx={{ color: 'common.white', lineHeight: 1.5 }}>
        {dnsValidation.message}
      </Typography>
    ) : null}
  </Box>
)

const renderIpHistoryTooltip = (previousIps: string[], ipChangedAt?: string | null) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, maxWidth: 320 }}>
    <Typography variant="caption" sx={{ color: 'common.white', lineHeight: 1.5 }}>
      Предыдущие IP: {previousIps.join(', ')}
    </Typography>
    {ipChangedAt ? (
      <Typography variant="caption" sx={{ color: 'common.white', lineHeight: 1.5 }}>
        Изменено: {formatLocalDateTime(ipChangedAt)}
      </Typography>
    ) : null}
  </Box>
)

const renderManualDetailsTooltip = (row: RegistryManualRow) => {
  const details = [
    ['Комментарий', row.comment],
    ['Владелец', row.owner],
    ['Добавил', row.created_by],
    ['Протокол', row.protocol],
    ['Порт', row.port === undefined || row.port === null ? null : String(row.port)],
    ['Теги', row.tags?.length ? row.tags.join(', ') : null],
    ['Статус', row.is_active ? 'активна' : 'неактивна'],
    ['Создано', formatLocalDateTime(row.created_at)],
    ['Обновлено', formatLocalDateTime(row.updated_at)],
  ].filter(([, value]) => value)

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, maxWidth: 360 }}>
      {details.map(([label, value]) => (
        <Box key={label} sx={{ display: 'flex', flexDirection: 'column', gap: 0.125 }}>
          <Typography variant="caption" sx={{ color: 'var(--sl-muted)' }}>
            {label}
          </Typography>
          <Typography variant="caption" sx={{ color: 'common.white', lineHeight: 1.5, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            {String(value)}
          </Typography>
        </Box>
      ))}
    </Box>
  )
}

const MAX_SOURCE_REFERENCES_VISIBLE = 1
const MAX_DNS_NAMES_VISIBLE = 1
const REGISTRY_ROW_DIVIDER_COLOR = 'var(--sl-line)'

const RegistryRowsTable = ({
  title,
  rows,
  emptyMessage,
  accentColor,
  titleAction,
  editable = false,
  onEdit,
  onDelete,
  deletingRowId = null,
}: RegistryRowsTableProps) => {
  const [expandedDnsRows, setExpandedDnsRows] = useState<Record<string | number, boolean>>({})
  const colCount = editable ? 5 : 4

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1, mb: 1 }}>
        <Typography variant="h6">
          {title}
        </Typography>
        {titleAction ? <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>{titleAction}</Box> : null}
      </Box>
      <Paper
        elevation={0}
        sx={{
          border: '1px solid',
          borderColor: 'divider',
          boxShadow: 'none',
          overflow: 'hidden',
        }}
      >
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ minWidth: 180 }}>IP</TableCell>
                <TableCell sx={{ minWidth: 160 }}>Service</TableCell>
                <TableCell sx={{ minWidth: 160 }}>DNS name</TableCell>
                <TableCell sx={{ minWidth: 120 }}>Source</TableCell>
                {editable && <TableCell align="right" sx={{ minWidth: 110 }}>Actions</TableCell>}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={colCount} sx={{ py: 3 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'center' }}>
                      <Typography variant="body2" color="text.secondary">
                        {emptyMessage}
                      </Typography>
                    </Box>
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((row, rowIndex) => {
                  const manualRow = isManualRow(row)
                  const baseRowId = manualRow
                    ? row.id
                    : row.id ??
                      row.source_references?.[0]?.object_key ??
                      `${row.ip}:${row.service_name || row.dns_name || 'row'}`
                  const rowId = `${String(baseRowId)}:${rowIndex}`
                  const sourceRefs = manualRow
                    ? row.source_references || []
                    : (row.source_references || []).filter((reference) => reference.repo_name !== 'example-terraform')
                  const dnsNames = row.dns_names && row.dns_names.length > 0 ? row.dns_names : (row.dns_name ? [row.dns_name] : [])
                  const isDnsExpanded = Boolean(expandedDnsRows[rowId])
                  const visibleDnsNames = isDnsExpanded ? dnsNames : dnsNames.slice(0, MAX_DNS_NAMES_VISIBLE)
                  const dnsValidation = row.dns_validation
                  const hasDnsMismatch = dnsValidation?.status === 'mismatch'
                  const shouldShowDnsUnresolvedBadge =
                    dnsValidation?.status === 'unresolved' &&
                    !(dnsValidation?.message || '').toLowerCase().includes('wildcard dns skipped')
                  const shouldShowDnsTooltip = hasDnsMismatch || shouldShowDnsUnresolvedBadge
                  const dnsBorderColor = hasDnsMismatch ? 'var(--sl-danger)' : shouldShowDnsUnresolvedBadge ? 'var(--sl-warn)' : undefined
                  const hasPreviousIps = Boolean(row.previous_ips?.length)

                  return (
                    <TableRow
                      key={rowId}
                      hover
                      sx={{
                        boxShadow: `inset 0 -1px 0 0 ${REGISTRY_ROW_DIVIDER_COLOR}`,
                      }}
                    >
                      <TableCell sx={{ verticalAlign: 'top', py: 1 }}>
                        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.5 }}>
                          <Box sx={{ minWidth: 0 }}>
                            {renderBlock(renderTokenList([manualRow ? row.ip_or_cidr : row.ip]))}
                          </Box>
                          {hasPreviousIps ? (
                            <Tooltip title={renderIpHistoryTooltip(row.previous_ips || [], row.ip_changed_at)} arrow>
                              <IconButton size="small" sx={{ color: accentColor || 'text.secondary', p: 0.25, mt: 0.25 }} aria-label="история IP">
                                <UiIcon glyph={InfoOutlinedIcon} size={16} />
                              </IconButton>
                            </Tooltip>
                          ) : null}
                        </Box>
                      </TableCell>
                      <TableCell sx={{ verticalAlign: 'top', py: 1 }}>
                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, minWidth: 0, py: 0.125 }}>
                          {renderBlock(renderTokenList([manualRow ? row.service_name : row.service_name]))}
                          {manualRow && (
                            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, alignItems: 'flex-start' }}>
                              {!row.is_active && <NeutralValueBadge>inactive</NeutralValueBadge>}
                              {row.tags?.length ? renderBlock(renderTokenList(row.tags)) : null}
                            </Box>
                          )}
                        </Box>
                      </TableCell>
                      <TableCell sx={{ verticalAlign: 'top', py: 1 }}>
                        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, alignItems: 'flex-start' }}>
                          <Tooltip
                            title={
                              shouldShowDnsTooltip && dnsValidation
                                ? renderDnsTooltipContent(dnsValidation, manualRow ? row.ip_or_cidr : row.ip)
                                : ''
                            }
                            arrow
                            disableHoverListener={!shouldShowDnsTooltip}
                          >
                            <Box component="span">
                              {renderBlock(
                                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, alignItems: 'flex-start' }}>
                                  {visibleDnsNames.length === 0 ? (
                                    <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6 }}>
                                      -
                                    </Typography>
                                  ) : (
                                    visibleDnsNames.map((value) => (
                                      <CopyableBadge
                                        key={value}
                                        value={value}
                                        sx={{
                                          borderColor: dnsBorderColor,
                                          color: dnsBorderColor,
                                        }}
                                      />
                                    ))
                                  )}
                                </Box>
                              )}
                            </Box>
                          </Tooltip>
                          {dnsNames.length > MAX_DNS_NAMES_VISIBLE ? (
                            <Box
                              component="button"
                              type="button"
                              onClick={() =>
                                setExpandedDnsRows((current) => ({
                                  ...current,
                                  [rowId]: !current[rowId],
                                }))
                              }
                              sx={{
                                p: 0,
                                m: 0,
                                border: 0,
                                background: 'transparent',
                                cursor: 'pointer',
                              }}
                            >
                              <NeutralValueBadge
                                sx={{
                                  cursor: 'pointer',
                                  userSelect: 'none',
                                }}
                              >
                                {isDnsExpanded ? 'Свернуть' : `+${dnsNames.length - MAX_DNS_NAMES_VISIBLE} ещё`}
                              </NeutralValueBadge>
                            </Box>
                          ) : null}
                        </Box>
                      </TableCell>
                      <TableCell sx={{ verticalAlign: 'top' }}>
                        {manualRow ? (
                          <Box sx={{ display: 'flex', flexDirection: 'row', gap: 0.375, alignItems: 'center', py: 0.125, flexWrap: 'wrap' }}>
                            <NeutralValueBadge
                              sx={{
                                borderColor: accentColor || 'divider',
                                backgroundColor: 'transparent',
                                color: accentColor || 'text.primary',
                              }}
                            >
                              manual
                            </NeutralValueBadge>
                            <Tooltip title={renderManualDetailsTooltip(row)} arrow>
                              <IconButton size="small" sx={{ color: accentColor || 'text.secondary', p: 0.25 }} aria-label="детали ручной записи">
                                <UiIcon glyph={InfoOutlinedIcon} size={16} />
                              </IconButton>
                            </Tooltip>
                          </Box>
                        ) : sourceRefs.length > 0 ? (
                          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, alignItems: 'flex-start', py: 0.125 }}>
                            {sourceRefs.slice(0, MAX_SOURCE_REFERENCES_VISIBLE).map((reference, index) => {
                              const label = formatSourceReference(reference)
                              const compactLabel = formatCompactSourceLabel(index)
                              return (
                                <Tooltip key={`${label}-${index}`} title={label} arrow>
                                  <Box component="span">
                                    {reference.source_file_url ? (
                                      <Box
                                        sx={{
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: 0.5,
                                          maxWidth: '100%',
                                          '&:hover .registry-copy-action': {
                                            opacity: 1,
                                            pointerEvents: 'auto',
                                          },
                                        }}
                                      >
                                        <Link
                                          href={reference.source_file_url}
                                          target="_blank"
                                          rel="noreferrer"
                                          underline="none"
                                          color="inherit"
                                          sx={{ display: 'inline-flex', maxWidth: '100%' }}
                                        >
                                          <NeutralValueBadge
                                            sx={{
                                              maxWidth: 420,
                                              height: 'auto',
                                              alignItems: 'flex-start',
                                              cursor: 'pointer',
                                              borderColor: accentColor || 'divider',
                                              backgroundColor: 'transparent',
                                              color: accentColor || 'text.primary',
                                              '& .MuiChip-label': {
                                                display: 'flex',
                                                alignItems: 'center',
                                                gap: 0.5,
                                                py: 0.375,
                                              },
                                            }}
                                          >
                                            <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                                              <Box component="span">{compactLabel}</Box>
                                              <UiIcon glyph={OpenInNewIcon} sx={{ fontSize: 14, flex: '0 0 auto' }} />
                                            </Box>
                                          </NeutralValueBadge>
                                        </Link>
                                        <Tooltip title="Копировать" arrow>
                                          <IconButton
                                            size="small"
                                            className="registry-copy-action"
                                            onClick={(event) => {
                                              event.preventDefault()
                                              event.stopPropagation()
                                              void navigator.clipboard.writeText(label)
                                            }}
                                            sx={{
                                              p: 0.25,
                                              opacity: 0,
                                              pointerEvents: 'none',
                                              transition: 'opacity 0.16s ease',
                                              color: 'text.secondary',
                                            }}
                                            aria-label={`копировать ${label}`}
                                          >
                                            <UiIcon glyph={ContentCopyIcon} sx={{ fontSize: 14 }} />
                                          </IconButton>
                                        </Tooltip>
                                      </Box>
                                    ) : (
                                      <CopyableBadge value={compactLabel} copyValue={label} />
                                    )}
                                  </Box>
                                </Tooltip>
                              )
                            })}
                            {sourceRefs.length > MAX_SOURCE_REFERENCES_VISIBLE ? (
                              <NeutralValueBadge>+{sourceRefs.length - MAX_SOURCE_REFERENCES_VISIBLE} ещё</NeutralValueBadge>
                            ) : null}
                          </Box>
                        ) : (
                          <Typography variant="body2" color="text.secondary" sx={{ lineHeight: 1.6, py: 0.25 }}>
                            -
                          </Typography>
                        )}
                      </TableCell>
                      {editable && (
                        <TableCell align="right" sx={{ verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                          {manualRow && onEdit && onDelete ? (
                            <Box sx={{ display: 'inline-flex', gap: 0.5 }}>
                              <Tooltip title="Редактировать" arrow>
                                <IconButton size="small" onClick={() => onEdit(row)} aria-label="редактировать ручную запись">
                                  <UiIcon glyph={EditOutlinedIcon} size={16} />
                                </IconButton>
                              </Tooltip>
                              <Tooltip title="Удалить" arrow>
                                <span>
                                  <IconButton
                                    size="small"
                                    color="error"
                                    onClick={() => onDelete(row)}
                                    aria-label="удалить ручную запись"
                                    disabled={deletingRowId === row.id}
                                  >
                                    <UiIcon glyph={DeleteOutlineIcon} size={16} />
                                  </IconButton>
                                </span>
                              </Tooltip>
                            </Box>
                          ) : (
                            <NeutralValueBadge>N/A</NeutralValueBadge>
                          )}
                        </TableCell>
                      )}
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Box>
  )
}

export default RegistryRowsTable
