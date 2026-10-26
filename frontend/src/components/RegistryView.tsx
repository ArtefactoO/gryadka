import { UiIcon } from "@project/ui/mui"
import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Tab,
  Tabs,
  Tooltip,
} from '@mui/material'
import { Plus as AddIcon, Copy as ContentCopyIcon, Download as DownloadIcon } from "@project/ui/icons"
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'

import {
  addressesApi,
  type CreateManualRegistryEntryRequest,
  type RegistryManualRow,
  type RegistryPayload,
  type UpdateManualRegistryEntryRequest,
} from '../services/api'
import { useUiTheme } from "@project/ui/mui"
import { formatUpdateTime } from '../utils/formatUpdateTime'
import { getApiErrorMessage } from '../utils/apiError'
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import NeutralValueBadge from './NeutralValueBadge'
import SearchToggleInput from './SearchToggleInput'
import RegistryRowsTable from './RegistryRowsTable'
import RegistryManualDialog from './RegistryManualDialog'
import { useAuth } from '../hooks/useAuth'
import { compactTabsSx } from "@project/ui/mui"

interface RegistryViewProps {
  targetName: string
  targetType?: string
}

type RegistryExportField = 'ip' | 'service' | 'dns' | 'source'
type RegistryAddressScope = string

const EXPORT_FIELDS: Array<{ key: RegistryExportField; label: string }> = [
  { key: 'ip', label: 'IP' },
  { key: 'service', label: 'Сервис' },
  { key: 'dns', label: 'DNS' },
  { key: 'source', label: 'Источник' },
]

const normalize = (value?: string | number | null): string => String(value ?? '').trim().toLowerCase()

const compareRegistryRows = (
  left: {
    service_name?: string | null
    dns_name?: string | null
    ip?: string | null
    ip_or_cidr?: string | null
  },
  right: {
    service_name?: string | null
    dns_name?: string | null
    ip?: string | null
    ip_or_cidr?: string | null
  }
): number => {
  const serviceCompare = normalize(left.service_name).localeCompare(normalize(right.service_name))
  if (serviceCompare !== 0) {
    return serviceCompare
  }
  const dnsCompare = normalize(left.dns_name).localeCompare(normalize(right.dns_name))
  if (dnsCompare !== 0) {
    return dnsCompare
  }
  return normalize(left.ip ?? left.ip_or_cidr).localeCompare(normalize(right.ip ?? right.ip_or_cidr))
}

const mergeRegistryPayload = (
  current: RegistryPayload | undefined,
  updater: (manualRows: RegistryManualRow[]) => RegistryManualRow[]
): RegistryPayload | undefined => {
  if (!current) {
    return current
  }
  const manualRows = updater(current.manual_rows || [])
  return {
    ...current,
    manual_rows: manualRows,
    summary: {
      ...current.summary,
      manual_rows_count: manualRows.length,
      total_manual_rows: manualRows.length,
    },
  }
}

const formatExportSource = (row: RegistryPayload['auto_rows'][number] | RegistryManualRow): string => {
  if (row.origin === 'manual') {
    return 'manual'
  }
  return (row.source_references || [])
    .filter((reference) => reference.repo_name !== 'example-terraform')
    .map((reference) => {
      const lineSuffix =
        reference.source_line_start === undefined || reference.source_line_start === null
          ? ''
          : reference.source_line_end && reference.source_line_end !== reference.source_line_start
            ? `:${reference.source_line_start}-${reference.source_line_end}`
            : `:${reference.source_line_start}`
      return `${reference.repo_name}${reference.file_path ? ` / ${reference.file_path}` : ''}${lineSuffix}`
    })
    .join('; ')
}

const buildRegistryExportText = (
  autoRows: RegistryPayload['auto_rows'],
  manualRows: RegistryManualRow[],
  fields: RegistryExportField[]
): string => {
  const combinedRows = [...autoRows, ...manualRows].sort(compareRegistryRows)
  const headerMap: Record<RegistryExportField, string> = {
    ip: 'IP',
    service: 'Сервис',
    dns: 'DNS',
    source: 'Источник',
  }
  const lines = combinedRows.map((row) => {
    const ip = row.origin === 'manual' ? row.ip_or_cidr : row.ip
    const dns = row.dns_names?.length ? row.dns_names.join(', ') : (row.dns_name || '-')
    const source = formatExportSource(row)
    const valueMap: Record<RegistryExportField, string> = {
      ip,
      service: row.service_name || '-',
      dns,
      source: source || '-',
    }
    return fields.map((field) => valueMap[field]).join('\t')
  })
  return [fields.map((field) => headerMap[field]).join('\t'), ...lines].join('\n')
}

const RegistryView = ({ targetName, targetType = 'addresses' }: RegistryViewProps) => {
  const { visual } = useUiTheme()
  const queryClient = useQueryClient()
  const { hasPermission } = useAuth()
  const canWrite = hasPermission('addresses.write') || hasPermission('registry.write')
  const [activeScope, setActiveScope] = useState<RegistryAddressScope>('external')
  const [searchValue, setSearchValue] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [manualDialogOpen, setManualDialogOpen] = useState(false)
  const [manualDialogMode, setManualDialogMode] = useState<'create' | 'edit'>('create')
  const [editingRow, setEditingRow] = useState<RegistryManualRow | null>(null)
  const [deleteConfirmationRow, setDeleteConfirmationRow] = useState<RegistryManualRow | null>(null)
  const [deletingRowId, setDeletingRowId] = useState<number | null>(null)
  const [errorText, setErrorText] = useState<string | null>(null)
  const [noticeText, setNoticeText] = useState<string | null>(null)
  const [exportDialogOpen, setExportDialogOpen] = useState(false)
  const [exportFields, setExportFields] = useState<RegistryExportField[]>(['ip', 'service', 'dns', 'source'])

  const { data, isLoading, error } = useQuery<RegistryPayload>({
    queryKey: ['addresses', targetName],
    queryFn: () => addressesApi.getAddresses(),
    refetchInterval: 60_000,
    staleTime: 60_000,
  })

  useEffect(() => {
    queryClient.invalidateQueries({ queryKey: ['addresses', targetName] })
  }, [canWrite, queryClient, targetName])

  const buildScopeMovedNotice = (scopeName?: string | null): string | null => {
    if (!scopeName || scopeName === activeScope) {
      return null
    }
    const matchedView = scopeViews.find((view) => view.name === scopeName)
    return `Запись сохранена во вкладке ${matchedView?.label || scopeName}.`
  }

  const createMutation = useMutation({
    mutationFn: (payload: CreateManualRegistryEntryRequest) => addressesApi.createManual(payload),
    onSuccess: async (createdRow) => {
      setErrorText(null)
      setNoticeText(buildScopeMovedNotice(createdRow.address_scope))
      setManualDialogOpen(false)
      setEditingRow(null)
      if (createdRow.address_scope && createdRow.address_scope !== activeScope) {
        setActiveScope(createdRow.address_scope)
      }
      queryClient.setQueryData<RegistryPayload | undefined>(['addresses', targetName], (current) =>
        mergeRegistryPayload(current, (manualRows) => [createdRow, ...manualRows.filter((row) => row.id !== createdRow.id)])
      )
      await queryClient.invalidateQueries({ queryKey: ['addresses', targetName] })
    },
    onError: (mutationError: unknown) => {
      setNoticeText(null)
      setErrorText(getApiErrorMessage(mutationError, 'Не удалось создать ручную запись'))
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: UpdateManualRegistryEntryRequest }) =>
      addressesApi.updateManual(id, payload),
    onSuccess: async (updatedRow) => {
      setErrorText(null)
      setNoticeText(buildScopeMovedNotice(updatedRow.address_scope))
      setManualDialogOpen(false)
      setEditingRow(null)
      if (updatedRow.address_scope && updatedRow.address_scope !== activeScope) {
        setActiveScope(updatedRow.address_scope)
      }
      queryClient.setQueryData<RegistryPayload | undefined>(['addresses', targetName], (current) =>
        mergeRegistryPayload(current, (manualRows) => manualRows.map((row) => (row.id === updatedRow.id ? updatedRow : row)))
      )
      await queryClient.invalidateQueries({ queryKey: ['addresses', targetName] })
    },
    onError: (mutationError: unknown) => {
      setNoticeText(null)
      setErrorText(getApiErrorMessage(mutationError, 'Не удалось обновить ручную запись'))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => addressesApi.deleteManual(id),
    onSuccess: (_, deletedId) => {
      setErrorText(null)
      setNoticeText(null)
      queryClient.setQueryData<RegistryPayload | undefined>(['addresses', targetName], (current) =>
        mergeRegistryPayload(current, (manualRows) => manualRows.filter((row) => row.id !== deletedId))
      )
      void queryClient.invalidateQueries({ queryKey: ['addresses', targetName] })
    },
    onError: (mutationError: unknown) => {
      setNoticeText(null)
      setErrorText(getApiErrorMessage(mutationError, 'Не удалось удалить ручную запись'))
    },
  })

  const registryData = data ?? null
  const autoRows = registryData?.auto_rows ?? []
  const manualRows = registryData?.manual_rows ?? []
  const lastUpdate = registryData?.last_update ? formatUpdateTime(registryData.last_update) : null
  const scopeViews = registryData?.summary?.views ?? []
  const activeScopeView = scopeViews.find((view) => view.name === activeScope)

  useEffect(() => {
    if (scopeViews.length === 0) {
      return
    }
    if (!scopeViews.some((view) => view.name === activeScope)) {
      setActiveScope((scopeViews[0]?.name as RegistryAddressScope | undefined) || 'external')
    }
  }, [activeScope, scopeViews])

  useEffect(() => {
    setSearchValue('')
    setSearchOpen(false)
    setExportDialogOpen(false)
    setErrorText(null)
    setNoticeText(null)
  }, [activeScope, targetName])

  const searchTerm = searchValue.trim().toLowerCase()

  const filteredAutoRows = useMemo(() => {
    return autoRows
      .filter((row) => {
        const sourceText = (row.source_references || [])
          .map((reference) => [
            reference.repo_name,
            reference.repo_url,
            reference.repo_ref,
            reference.repo_commit,
            reference.file_path,
            reference.source_line_start,
            reference.source_line_end,
            reference.object_key,
          ].join(' '))
          .join(' ')
        const searchableValues = [
          row.ip,
          row.service_name,
          row.dns_name,
          row.dns_names?.join(' '),
          row.protocol,
          row.port,
          row.environment,
          row.component,
          sourceText,
        ]
        return !searchTerm || searchableValues.some((value) => normalize(value).includes(searchTerm))
      })
      .sort(compareRegistryRows)
  }, [autoRows, searchTerm])

  const filteredManualRows = useMemo(() => {
    return manualRows
      .filter((row) => {
        const sourceText = (row.source_references || [])
          .map((reference) => [
            reference.repo_name,
            reference.repo_url,
            reference.repo_ref,
            reference.repo_commit,
            reference.file_path,
            reference.source_line_start,
            reference.source_line_end,
            reference.object_key,
          ].join(' '))
          .join(' ')
        const searchableValues = [
          row.service_name,
          row.ip_or_cidr,
          row.dns_name,
          row.protocol,
          row.port,
          row.comment,
          row.owner,
          row.tags?.join(' '),
          sourceText,
        ]
        return !searchTerm || searchableValues.some((value) => normalize(value).includes(searchTerm))
      })
      .sort(compareRegistryRows)
  }, [manualRows, searchTerm])

  const visibleAutoRows = useMemo(
    () => filteredAutoRows.filter((row) => (row.address_scope || 'external') === activeScope),
    [activeScope, filteredAutoRows]
  )

  const visibleManualRows = useMemo(
    () => filteredManualRows.filter((row) => (row.address_scope || 'external') === activeScope),
    [activeScope, filteredManualRows]
  )

  const summaryChips = (
    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
      <NeutralValueBadge
        sx={{
          borderColor: visual.accentColor,
          backgroundColor: 'transparent',
          color: visual.accentColor,
        }}
      >
        Auto {visibleAutoRows.length}
      </NeutralValueBadge>
      <NeutralValueBadge
        sx={{
          borderColor: visual.accentColor,
          backgroundColor: 'transparent',
          color: visual.accentColor,
        }}
      >
        Manual {visibleManualRows.length}
      </NeutralValueBadge>
    </Box>
  )

  const toggleExportField = (field: RegistryExportField) => {
    setExportFields((current) =>
      current.includes(field) ? current.filter((item) => item !== field) : [...current, field]
    )
  }

  const handleCopyAllRecords = async () => {
    try {
      if (exportFields.length === 0) {
        setErrorText('Выберите хотя бы одно поле для экспорта')
        return
      }
      await navigator.clipboard.writeText(buildRegistryExportText(visibleAutoRows, visibleManualRows, exportFields))
      setExportDialogOpen(false)
      setErrorText(null)
    } catch (copyError) {
      setErrorText(copyError instanceof Error ? copyError.message : 'Не удалось скопировать записи')
    }
  }

  const handleExportTxt = () => {
    try {
      if (exportFields.length === 0) {
        setErrorText('Выберите хотя бы одно поле для экспорта')
        return
      }
      const blob = new Blob([buildRegistryExportText(visibleAutoRows, visibleManualRows, exportFields)], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `addresses-${activeScope}.txt`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      setExportDialogOpen(false)
      setErrorText(null)
    } catch (exportError) {
      setErrorText(exportError instanceof Error ? exportError.message : 'Не удалось экспортировать записи')
    }
  }

  const handleOpenCreateDialog = () => {
    setEditingRow(null)
    setManualDialogMode('create')
    setManualDialogOpen(true)
  }

  const handleOpenEditDialog = (row: RegistryManualRow) => {
    setEditingRow(row)
    setManualDialogMode('edit')
    setManualDialogOpen(true)
  }

  const handleDelete = async (row: RegistryManualRow) => {
    setDeleteConfirmationRow(row)
  }

  const closeDeleteConfirmation = () => {
    if (deleteMutation.isPending) {
      return
    }
    setDeleteConfirmationRow(null)
  }

  const submitDeleteConfirmation = async () => {
    const row = deleteConfirmationRow
    if (!row) {
      return
    }
    setErrorText(null)
    setNoticeText(null)
    setDeleteConfirmationRow(null)
    setDeletingRowId(row.id)
    try {
      await deleteMutation.mutateAsync(row.id)
    } finally {
      setDeletingRowId(null)
    }
  }

  const handleSubmitDialog = async (
    payload: CreateManualRegistryEntryRequest | UpdateManualRegistryEntryRequest
  ) => {
    setErrorText(null)
    if (manualDialogMode === 'create') {
      await createMutation.mutateAsync(payload as CreateManualRegistryEntryRequest)
      return
    }
    if (!editingRow) return
    await updateMutation.mutateAsync({ id: editingRow.id, payload: payload as UpdateManualRegistryEntryRequest })
  }

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="320px">
        <CircularProgress sx={{ color: visual.accentColor }} />
      </Box>
    )
  }

  if (error) {
    if (axios.isAxiosError(error) && error.response?.status === 503) {
      return <Alert severity="info">Монитор адресов ещё прогревается или временно недоступен.</Alert>
    }
    return <Alert severity="error">Не удалось загрузить данные адресов.</Alert>
  }

  if (!registryData) {
    return <Alert severity="warning">Цель адресов не найдена.</Alert>
  }

  const actionButtons = canWrite ? (
    <>
      <SearchToggleInput
        value={searchValue}
        isOpen={searchOpen}
        onValueChange={setSearchValue}
        onOpenChange={setSearchOpen}
        onClear={() => setSearchValue('')}
        onApply={() => undefined}
        accentColor={visual.accentColor}
        accentSoftBg={visual.accentSoftBg}
        searchButtonTooltip="Поиск по IP, сервису, DNS, источнику, окружению или пути к файлу."
        clearButtonTooltip="Очистить фильтр"
        width={260}
      />
      <Tooltip title="Экспортировать все записи в TXT" arrow>
        <span>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setExportDialogOpen(true)}
            startIcon={<UiIcon glyph={DownloadIcon} />}
            sx={{
              backgroundColor: visual.accentSoftBg,
              borderColor: visual.accentColor,
              color: visual.accentColor,
              '&:hover': {
                backgroundColor: visual.accentSoftBg,
                borderColor: visual.accentColor,
              },
            }}
          >
            TXT
          </Button>
        </span>
      </Tooltip>
      <Tooltip title="Создать ручную запись" arrow>
        <span>
          <Button
            size="small"
            variant="outlined"
            onClick={handleOpenCreateDialog}
            startIcon={<UiIcon glyph={AddIcon} />}
            sx={{
              backgroundColor: visual.accentSoftBg,
              borderColor: visual.accentColor,
              color: visual.accentColor,
              '&:hover': {
                backgroundColor: visual.accentSoftBg,
                borderColor: visual.accentColor,
              },
            }}
          >
            Добавить запись
          </Button>
        </span>
      </Tooltip>
    </>
  ) : undefined

  return (
    <Box>
      <Tabs
        value={activeScope}
        onChange={(_event, newValue: RegistryAddressScope) => setActiveScope(newValue)}
        sx={{ ...compactTabsSx, mb: 1.5, pl: 1 }}
      >
        {(scopeViews.length > 0
          ? scopeViews
          : [
              { name: 'external', label: 'External IPs', total_rows_count: visibleAutoRows.length + visibleManualRows.length },
            ]
        ).map((view) => (
          <Tab
            key={view.name}
            value={view.name}
            label={`${view.label}${view.total_rows_count ? ` · ${view.total_rows_count}` : ''}`}
          />
        ))}
      </Tabs>

      <VersionAndUpdateBlock
        versionChips={summaryChips}
        actionButtons={actionButtons}
        updateTime={lastUpdate}
        refreshTarget={{ targetType, targetName }}
        accentColor={visual.accentColor}
        accentSoftBg={visual.accentSoftBg}
      />

      {errorText && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErrorText(null)}>
          {errorText}
        </Alert>
      )}

      {noticeText && (
        <Alert severity="info" sx={{ mb: 2 }} onClose={() => setNoticeText(null)}>
          {noticeText}
        </Alert>
      )}

      <Box sx={{ display: 'grid', gap: 2 }}>
        <RegistryRowsTable
          title="Auto-Discovered"
          rows={visibleAutoRows}
          emptyMessage={
            searchTerm
              ? 'Нет авто-записей по текущему фильтру.'
              : activeScope === 'internal'
                ? 'Для текущих includeIpCidrs внутренние авто-записи не найдены.'
                : 'Для настроенных репозиториев внешние авто-записи не найдены.'
          }
          accentColor={visual.accentColor}
          accentSoftBg={visual.accentSoftBg}
        />
        <RegistryRowsTable
          title="Manual"
          rows={visibleManualRows}
          emptyMessage={
            searchTerm
              ? 'Нет ручных записей по текущему фильтру.'
              : activeScope === 'internal'
                ? 'Внутренние ручные записи пока не добавлены.'
                : 'Внешние ручные записи пока не добавлены.'
          }
          accentColor={visual.accentColor}
          accentSoftBg={visual.accentSoftBg}
          titleAction={
            canWrite ? (
              <Tooltip title="Создать ручную запись" arrow>
                <span>
                  <Button
                    size="small"
                    variant="outlined"
                    onClick={handleOpenCreateDialog}
                    sx={{
                      minWidth: 80,
                      px: 1,
                      borderColor: visual.accentColor,
                      color: visual.accentColor,
                      backgroundColor: 'transparent',
                      '&:hover': {
                        borderColor: visual.accentColor,
                        backgroundColor: visual.accentSoftBg,
                      },
                    }}
                  >
                    + Запись
                  </Button>
                </span>
              </Tooltip>
            ) : null
          }
          editable={canWrite}
          onEdit={handleOpenEditDialog}
          onDelete={handleDelete}
          deletingRowId={deletingRowId}
        />
      </Box>

      <RegistryManualDialog
        open={manualDialogOpen}
        mode={manualDialogMode}
        initialValue={editingRow}
        addressScope={editingRow?.address_scope || activeScope}
        title={
          manualDialogMode === 'create'
            ? `Создать ручную запись · ${activeScopeView?.label || activeScope}`
            : `Редактировать ручную запись · ${activeScopeView?.label || editingRow?.address_scope || activeScope}`
        }
        submitLabel={manualDialogMode === 'create' ? 'Создать' : 'Сохранить'}
        submitting={createMutation.isPending || updateMutation.isPending}
        accentColor={visual.accentColor}
        accentSoftBg={visual.accentSoftBg}
        onClose={() => {
          setManualDialogOpen(false)
          setEditingRow(null)
        }}
        onSubmit={handleSubmitDialog}
      />
      <Dialog open={exportDialogOpen} onClose={() => setExportDialogOpen(false)} fullWidth maxWidth="xs">
        <DialogTitle sx={{ borderBottom: '1px solid', borderColor: visual.accentColor }}>
          Экспорт записей
        </DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1, pt: 1 }}>
            {EXPORT_FIELDS.map((field) => (
              <FormControlLabel
                key={field.key}
                control={
                  <Checkbox
                    checked={exportFields.includes(field.key)}
                    onChange={() => toggleExportField(field.key)}
                    sx={{
                      color: visual.accentColor,
                      '&.Mui-checked': { color: visual.accentColor },
                    }}
                  />
                }
                label={field.label}
              />
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setExportDialogOpen(false)} sx={{ color: visual.accentColor }}>
            Отмена
          </Button>
          <Button
            onClick={handleCopyAllRecords}
            variant="outlined"
            startIcon={<UiIcon glyph={ContentCopyIcon} />}
            sx={{ borderColor: visual.accentColor, color: visual.accentColor }}
          >
            Копировать
          </Button>
          <Button
            onClick={handleExportTxt}
            variant="contained"
            startIcon={<UiIcon glyph={DownloadIcon} />}
            sx={{ backgroundColor: visual.accentColor, '&:hover': { backgroundColor: visual.accentColor } }}
          >
            TXT
          </Button>
        </DialogActions>
      </Dialog>
      <Dialog
        open={Boolean(deleteConfirmationRow)}
        onClose={closeDeleteConfirmation}
        maxWidth="xs"
        fullWidth
        PaperProps={{
          sx: {
            border: '1px solid',
            borderColor: visual.accentColor,
            boxShadow: 'none',
          },
        }}
      >
        <DialogTitle sx={{ color: visual.accentColor }}>
          Подтвердить удаление
        </DialogTitle>
        <DialogContent>
          {`Удалить ручную запись "${deleteConfirmationRow?.service_name}"?`}
        </DialogContent>
        <DialogActions>
          <Button
            onClick={closeDeleteConfirmation}
            disabled={deleteMutation.isPending}
            sx={{
              color: visual.accentColor,
              '&:hover': { backgroundColor: visual.accentSoftBg },
            }}
          >
            Отмена
          </Button>
          <Button
            variant="contained"
            onClick={() => void submitDeleteConfirmation()}
            disabled={deleteMutation.isPending}
            sx={{
              backgroundColor: visual.accentColor,
              '&:hover': {
                backgroundColor: visual.accentColor,
                filter: 'brightness(0.95)',
              },
            }}
          >
            Подтвердить
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

export default RegistryView
