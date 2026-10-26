import { UiIcon } from "@project/ui/mui"
import { useState, useEffect, useRef, useCallback } from 'react'
import { FixedSizeList as List } from 'react-window'
import AutoSizer from 'react-virtualized-auto-sizer'
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  Checkbox,
  IconButton,
  Alert,
  CircularProgress,
  Dialog,
  AppBar,
  Toolbar,
  LinearProgress,
  DialogTitle,
  DialogContent,
  Tooltip,
} from '@mui/material'
import { Search as SearchIcon, Trash2 as DeleteIcon, Maximize2 as FullscreenIcon, X as CloseIcon, Eye as VisibilityIcon, Copy as CopyIcon } from "@project/ui/icons"
import { redisApi, RedisKeyItem, RedisKeyValueResponse, RedisDatabase } from '../services/api'
import NeutralValueBadge from './NeutralValueBadge'
import { getApiErrorMessage } from '../utils/apiError'
import { subscribeSse } from '../services/sse'

interface RedisKeysBrowserProps {
  targetName: string
  accentColor: string
  accentSoftBg: string
  defaultDb?: number
  databases?: RedisDatabase[]
  canDelete?: boolean
}

const DEBOUNCE_MS = 200
const BATCH_SIZE = 2000

const RedisKeysBrowser = ({
  targetName,
  accentColor,
  accentSoftBg,
  defaultDb = 0,
  databases = [],
  canDelete = false,
}: RedisKeysBrowserProps) => {
  const [pattern, setPattern] = useState('*')
  const [selectedDb, setSelectedDb] = useState(defaultDb)
  const [keys, setKeys] = useState<RedisKeyItem[]>([])
  const [keysSet, setKeysSet] = useState<Set<string>>(new Set())
  const [nextCursor, setNextCursor] = useState(0)
  const [loading, setLoading] = useState(false)
  const [backgroundLoading, setBackgroundLoading] = useState(false)
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set())
  const [selectAllMatching, setSelectAllMatching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [showFullscreenContent, setShowFullscreenContent] = useState(false)
  const [isClosingFullscreen, setIsClosingFullscreen] = useState(false)
  const [activeSearches, setActiveSearches] = useState<any[]>([])
  const [activeDeletions, setActiveDeletions] = useState<any[]>([])
  const [deletingProgress, setDeletingProgress] = useState<{ current: number; total: number } | null>(null)
  const listContainerRef = useRef<HTMLDivElement | null>(null)
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const loadingRef = useRef(loading)
  const backgroundLoadingRef = useRef(backgroundLoading)
  const nextCursorRef = useRef(nextCursor)
  const searchVersion = useRef(0)

  // Delay content rendering in fullscreen to avoid UI blocking
  useEffect(() => {
    if (isFullscreen) {
      const timer = setTimeout(() => setShowFullscreenContent(true), 50)
      return () => clearTimeout(timer)
    } else {
      setShowFullscreenContent(false)
      setIsClosingFullscreen(false)
    }
  }, [isFullscreen])

  const handleCloseFullscreen = () => {
    setIsClosingFullscreen(true)
    // Small delay to let UI update before heavy render
    setTimeout(() => {
      setIsFullscreen(false)
    }, 50)
  }

  // Subscribe to search notifications
  useEffect(() => {
    const unsubscribe = subscribeSse(
      {
        targetType: 'redis',
        targetName,
        event: 'redis.keys.searching',
      },
      (message) => {
        if (message.status === 'started') {
          setActiveSearches((prev) => {
            if (prev.some(s => s.request_id === message.request_id)) return prev
            return [...prev, message]
          })
        } else if (message.status === 'finished') {
          setActiveSearches((prev) =>
            prev.filter((s) => s.request_id !== message.request_id)
          )
        }
      }
    )
    return unsubscribe
  }, [targetName])

  // Subscribe to delete notifications from other users
  useEffect(() => {
    const unsubscribe = subscribeSse(
      {
        targetType: 'redis',
        targetName,
        event: 'redis.keys.deleting',
      },
      (message) => {
        if (message.status === 'started') {
          setActiveDeletions((prev) => {
            if (prev.some(d => d.request_id === message.request_id)) return prev
            return [...prev, message]
          })
        } else if (message.status === 'finished') {
          setActiveDeletions((prev) =>
            prev.filter((s) => s.request_id !== message.request_id)
          )
        }
      }
    )
    return unsubscribe
  }, [targetName])

  // Keep refs in sync with state
  useEffect(() => {
    loadingRef.current = loading
  }, [loading])

  useEffect(() => {
    backgroundLoadingRef.current = backgroundLoading
  }, [backgroundLoading])

  useEffect(() => {
    nextCursorRef.current = nextCursor
  }, [nextCursor])

  // A changed query invalidates results and any in-flight response for the old DB/pattern.
  useEffect(() => {
    searchVersion.current += 1
    setKeys([]); setKeysSet(new Set()); setSelectedKeys(new Set())
    setNextCursor(0); setSelectAllMatching(false); setSearched(false); setError(null)
    setLoading(false); setBackgroundLoading(false)
    return () => {
      searchVersion.current += 1
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current)
    }
  }, [pattern, selectedDb])

  // Stable reference to loadKeys for useCallback
  const loadKeysRef = useRef<(isNewSearch: boolean) => Promise<void>>()

  const loadKeys = useCallback(async (isNewSearch: boolean) => {
    const version = ++searchVersion.current
    if (isNewSearch) {
      setLoading(true)
      setNextCursor(0)
      setSelectAllMatching(false)
    } else {
      setBackgroundLoading(true)
    }
    setError(null)
    try {
      const c = isNewSearch ? 0 : nextCursor
      const res = await redisApi.scanKeys(targetName, pattern, c, 1000, selectedDb)
      if (version !== searchVersion.current) return
      if (isNewSearch) {
        setKeys(res.keys)
        setKeysSet(new Set(res.keys.map(k => k.key)))
        setSearched(true)
        setSelectedKeys(new Set())
      } else {
        // O(1) duplicate check using Set
        const newKeys = res.keys.filter(k => !keysSet.has(k.key))
        if (newKeys.length > 0) {
          setKeys(prev => [...prev, ...newKeys])
          setKeysSet(prev => {
            const newSet = new Set(prev)
            newKeys.forEach(k => newSet.add(k.key))
            return newSet
          })
        }
      }
      setNextCursor(res.cursor)
    } catch (e: any) {
      if (version === searchVersion.current) setError(getApiErrorMessage(e, 'Не удалось найти ключи. Повторите запрос.'))
    } finally {
      if (version === searchVersion.current) {
        setLoading(false)
        setBackgroundLoading(false)
      }
    }
  }, [nextCursor, targetName, pattern, selectedDb, keysSet])

  // Update ref to loadKeys for stable Observer access
  useEffect(() => {
    loadKeysRef.current = loadKeys
  }, [loadKeys])

  const onItemsRendered = useCallback(({ visibleStopIndex }: { visibleStopIndex: number }) => {
    // Check if we are close to the end
    if (visibleStopIndex >= keys.length - 5) {
      if (loadingRef.current || backgroundLoadingRef.current || nextCursorRef.current === 0) return

      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }

      debounceTimerRef.current = setTimeout(() => {
        loadKeysRef.current?.(false)
      }, DEBOUNCE_MS)
    }
  }, [keys.length])

  const handleSearch = (e?: React.FormEvent) => {
    e?.preventDefault()
    if (!pattern.trim()) return
    void loadKeys(true)
  }

  const handleDeleteSelected = () => {
    if (selectAllMatching) {
      setDeleteMessage(`Удалить все ключи по шаблону «${pattern}»? Отменить удаление будет нельзя.`)
      setDeleteConfirmOpen(true)
      return
    }
    if (selectedKeys.size === 0) return
    const keysArray = Array.from(selectedKeys)
    setKeysToDelete(keysArray)
    setDeleteMessage(`Удалить выбранные ключи (${keysArray.length})? Отменить удаление будет нельзя.`)
    setDeleteConfirmOpen(true)
  }

  const confirmDelete = async () => {
    setDeleteConfirmOpen(false)

    if (selectAllMatching) {
      setLoading(true)
      try {
        await redisApi.deleteKeys(targetName, [], selectedDb, pattern)
        // Reload keys to reflect changes
        loadKeys(true)
      } catch (e: any) {
        setError(getApiErrorMessage(e, 'Не удалось удалить ключи. Повторите запрос.'))
      } finally {
        setLoading(false)
        setSelectAllMatching(false)
        setSelectedKeys(new Set())
        setKeysToDelete([])
      }
      return
    }

    const total = keysToDelete.length
    let deleted = 0

    setDeletingProgress({ current: 0, total })
    setLoading(true)

    try {
      // Batch deletion by chunks
      for (let i = 0; i < keysToDelete.length; i += BATCH_SIZE) {
        const chunk = keysToDelete.slice(i, i + BATCH_SIZE)
        const result = await redisApi.deleteKeys(targetName, chunk, selectedDb)
        deleted += result.deleted
        setDeletingProgress({ current: Math.min(i + BATCH_SIZE, total), total })
      }

      // Remove deleted keys from list
      setKeys(prev => prev.filter(k => !keysToDelete.includes(k.key)))
      setKeysSet(prev => {
        const newSet = new Set(prev)
        keysToDelete.forEach(k => newSet.delete(k))
        return newSet
      })
      setSelectedKeys(new Set())

      // Close view dialog if the viewed key was deleted
      if (selectedKeyForView && keysToDelete.includes(selectedKeyForView)) {
        closeKeyValueDialog()
      }
    } catch (e: any) {
      setError(`${getApiErrorMessage(e, 'Удаление прервано.')} Удалено ${deleted} из ${total} ключей.`)
    } finally {
      setDeletingProgress(null)
      setLoading(false)
      setKeysToDelete([])
    }
  }

  const toggleSelectAll = () => {
    if ((selectedKeys.size === keys.length && keys.length > 0) || selectAllMatching) {
      setSelectedKeys(new Set())
      setSelectAllMatching(false)
    } else {
      setSelectedKeys(new Set(keys.map(k => k.key)))
    }
  }

  const toggleKey = (key: string) => {
    setSelectAllMatching(false)
    const newSet = new Set(selectedKeys)
    if (newSet.has(key)) newSet.delete(key)
    else newSet.add(key)
    setSelectedKeys(newSet)
  }

  // State for key value dialog
  const [selectedKeyForView, setSelectedKeyForView] = useState<string | null>(null)
  const [keyValueData, setKeyValueData] = useState<RedisKeyValueResponse | null>(null)
  const [isLoadingKeyValue, setIsLoadingKeyValue] = useState(false)
  const [keyValueError, setKeyValueError] = useState<string | null>(null)

  // State for delete confirmation dialog
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [keysToDelete, setKeysToDelete] = useState<string[]>([])
  const [deleteMessage, setDeleteMessage] = useState('')

  const openKeyValueDialog = async (key: string) => {
    setSelectedKeyForView(key)
    setKeyValueData(null)
    setKeyValueError(null)
    setIsLoadingKeyValue(true)

    try {
      const value = await redisApi.getKeyValue(targetName, key, selectedDb)
      setKeyValueData(value)
    } catch (e: any) {
      setKeyValueError(getApiErrorMessage(e, 'Не удалось загрузить значение ключа.'))
    } finally {
      setIsLoadingKeyValue(false)
    }
  }

  const closeKeyValueDialog = () => {
    setSelectedKeyForView(null)
    setKeyValueData(null)
    setKeyValueError(null)
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      // Could show a toast here
    })
  }

  const formatValue = (value: any): string => {
    if (value === null || value === undefined) return 'null'
    if (typeof value === 'string') {
      // Try to parse string as JSON for pretty display
      if ((value.trim().startsWith('{') && value.trim().endsWith('}')) ||
          (value.trim().startsWith('[') && value.trim().endsWith(']'))) {
        try {
          const parsed = JSON.parse(value)
          return JSON.stringify(parsed, null, 2)
        } catch {
          return value
        }
      }
      return value
    }
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return String(value)
    }
  }

  const renderContent = () => {
    // Show lightweight placeholder during fullscreen close to avoid UI blocking
    if (isClosingFullscreen) {
      return (
        <Box sx={{ p: 3, bgcolor: 'background.default', minHeight: '100vh' }}>
          <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '50vh' }}>
            <CircularProgress size={24} sx={{ color: accentColor }} />
          </Box>
        </Box>
      )
    }

    const currentDbStats = databases.find(d => d.name === `db${selectedDb}`)
    const maxKeyLen = keys.reduce((max, k) => Math.max(max, k.key.length), 0)
    // Approx width: Delete(40) + Checkbox(48) + Key(maxKeyLen * 9 + 40 for icon) + Type(100) + TTL(100) + Padding(32)
    const minTableWidth = Math.max(400, 40 + 48 + (maxKeyLen * 9 + 50) + 100 + 100 + 32)
    // Determine item count: keys + 1 if loading more is possible
    const itemCount = keys.length + (nextCursor !== 0 ? 1 : 0)

    return (
    <Box sx={isFullscreen ? { p: 3, bgcolor: 'background.default', minHeight: '100vh' } : { display: 'inline-flex', flexDirection: 'column', gap: 2, maxWidth: '100%' }}>
      {(activeSearches.length > 0 || activeDeletions.length > 0) && (
        <Alert
          severity="info"
          icon={false}
          sx={{
            border: '1px solid',
            borderColor: accentColor,
            backgroundColor: accentSoftBg,
            color: 'text.primary',
            '& .MuiAlert-message': { width: '100%' },
            width: '100%',
          }}
        >
          <Typography variant="body2" component="div">
            {activeSearches.map((s) => (
              <Box component="span" key={s.request_id} sx={{ mr: 2, display: 'inline-block' }}>
                <strong>{s.username}</strong> ищет &quot;{s.pattern}&quot; в DB {s.db}
              </Box>
            ))}
            {activeDeletions.map((d) => (
              <Box component="span" key={d.request_id} sx={{ mr: 2, display: 'inline-block' }}>
                <strong>{d.username}</strong>
                {d.pattern ? (
                  <> удаляет ключи по паттерну &quot;{d.pattern}&quot;</>
                ) : (
                  <> удаляет {d.count} ключей</>
                )}
                {' '}в DB {d.db}
              </Box>
            ))}
          </Typography>
        </Alert>
      )}
      <Paper
        elevation={0}
        sx={{
          p: 2,
          border: '1px solid',
          borderColor: 'divider',
          display: 'inline-flex',
          flexDirection: 'column',
          gap: 1.5,
          width: 'fit-content',
          maxWidth: '100%',
          boxSizing: 'border-box',
        }}
      >
        <form
          onSubmit={handleSearch}
          style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', width: 'auto', maxWidth: '100%' }}
        >
          <TextField
            label="База"
            type="number"
            value={selectedDb}
            onChange={(e) => setSelectedDb(Number(e.target.value))}
            size="small"
            helperText={currentDbStats?.keys !== undefined ? `Keys: ${currentDbStats.keys.toLocaleString('ru-RU')}` : ''}
            FormHelperTextProps={{
              sx: {
                fontSize: '0.6rem',
                mt: 0,
                mb: 0,
                ml: 0,
                pl: 0,
                lineHeight: 1.2,
                whiteSpace: 'nowrap',
                position: 'absolute',
                top: '100%',
                maxWidth: 70,
                overflow: 'hidden',
                textOverflow: 'ellipsis'
              }
            }}
            sx={{
              width: 70,
              '& .MuiOutlinedInput-root': {
                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                  borderColor: accentColor,
                },
                '&:hover .MuiOutlinedInput-notchedOutline': {
                  borderColor: accentColor,
                },
              },
              '& .MuiInputLabel-root.Mui-focused': {
                color: accentColor,
              },
            }}
            disabled={loading}
            inputProps={{ min: 0 }}
          />
          <TextField label="Шаблон ключа" value={pattern} onChange={event => setPattern(event.target.value)}
            size="small" placeholder="*" disabled={loading} sx={{ width: 240, maxWidth: '100%' }} />
          <Button
            type="submit"
            variant="contained"
            size="small"
            disabled={loading || !pattern.trim()}
            startIcon={loading ? <CircularProgress size={16} color="inherit" /> : <UiIcon glyph={SearchIcon} />}
            sx={{ backgroundColor: accentColor, '&:hover': { backgroundColor: accentColor, filter: 'brightness(0.9)' } }}
          >
            Найти
          </Button>
          {searched && !isFullscreen && keys.length > 0 && (
            <Button
              size="small"
              variant="outlined"
              startIcon={<UiIcon glyph={FullscreenIcon} />}
              onClick={() => setIsFullscreen(true)}
              sx={{ color: accentColor, borderColor: accentColor, ml: 1 }}
            >
              Развернуть
            </Button>
          )}
        </form>
        {error && <Alert severity="error">{error}</Alert>}
      </Paper>

      {searched && (
        <>
          <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', overflow: 'hidden', minWidth: isFullscreen ? '100%' : minTableWidth, width: isFullscreen ? '100%' : 'auto' }}>
            {keys.length === 0 && !loading ? (
              <Box sx={{ py: 4, color: 'text.secondary', textAlign: 'center' }}>
                Ключи по этому шаблону не найдены.
              </Box>
            ) : (
              <>
                {/* Selection Banner */}
                {keys.length > 0 && selectedKeys.size === keys.length && !selectAllMatching && (
                  <Box sx={{ p: 1, bgcolor: 'action.selected', textAlign: 'center', borderBottom: '1px solid', borderColor: 'divider' }}>
                    <Typography variant="body2" component="span">
                      Выбрано ключей на этой странице: {keys.length}.
                    </Typography>
                    <Tooltip title="Выделить все ключи в базе данных, соответствующие текущему поисковому запросу, даже те, которые еще не загружены в таблицу.">
                      <Button
                        size="small"
                        variant="outlined"
                        sx={{ ml: 2, textTransform: 'none', color: accentColor, borderColor: accentColor }}
                        onClick={() => setSelectAllMatching(true)}
                      >
                        Выбрать все по шаблону
                      </Button>
                    </Tooltip>
                  </Box>
                )}
                {selectAllMatching && (
                  <Box sx={{ p: 1, bgcolor: 'action.selected', textAlign: 'center', borderBottom: '1px solid', borderColor: 'divider' }}>
                    <Typography variant="body2" component="span" sx={{ fontWeight: 500 }}>
                      Выбраны все ключи по шаблону &quot;{pattern}&quot;.
                    </Typography>
                    <Tooltip title="Отменить выбор всех ключей">
                      <Button
                        size="small"
                        variant="outlined"
                        sx={{ ml: 1, textTransform: 'none', color: accentColor, borderColor: accentColor }}
                        onClick={() => { setSelectAllMatching(false); setSelectedKeys(new Set()) }}
                      >
                        Снять выделение
                      </Button>
                    </Tooltip>
                  </Box>
                )}

                {/* Header */}
                <Box sx={{
                  display: 'flex',
                  alignItems: 'center',
                  borderBottom: '1px solid',
                  borderColor: 'divider',
                  bgcolor: 'background.paper',
                  py: 1,
                  px: 2
                }}>
                  <Box sx={{ width: 40, flexShrink: 0 }}></Box>
                  <Box sx={{ width: 48, flexShrink: 0 }}>
                    <Checkbox
                      checked={(keys.length > 0 && selectedKeys.size === keys.length) || selectAllMatching}
                      indeterminate={!selectAllMatching && selectedKeys.size > 0 && selectedKeys.size < keys.length}
                      onChange={toggleSelectAll}
                      disabled={keys.length === 0 || !canDelete}
                      color="default"
                      sx={{
                        color: accentColor,
                        '&.Mui-checked': {
                          color: accentColor,
                        },
                        '&.Mui-indeterminate': {
                          color: accentColor,
                        },
                      }}
                    />
                  </Box>
                  <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', gap: 2, minWidth: 0 }}>
                    <Typography>Key</Typography>
                    {searched && (
                      <Box sx={{ display: 'flex', flexDirection: 'column', lineHeight: 1 }}>
                        <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.7rem', whiteSpace: 'nowrap' }}>
                          {nextCursor === 0 ? `Found: ${keys.length}` : `Найдено ключей: ${keys.length}. Поиск продолжается…`}
                        </Typography>
                        {nextCursor !== 0 && currentDbStats?.keys !== undefined && currentDbStats.keys > 1000 && (
                          <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.7rem', whiteSpace: 'nowrap' }}>
                            {pattern === '*' ? 'Found: ' : 'DB total: '}
                            {currentDbStats.keys.toLocaleString('ru-RU')}
                          </Typography>
                        )}
                      </Box>
                    )}
                    {canDelete && (selectedKeys.size > 0 || selectAllMatching) && !deletingProgress && (
                      <Button
                        variant="outlined"
                        size="small"
                        startIcon={<UiIcon glyph={DeleteIcon} />}
                        onClick={handleDeleteSelected}
                        disabled={loading}
                        sx={{ color: accentColor, borderColor: accentColor, flexShrink: 0 }}
                      >
                        {selectAllMatching ? 'Удалить все по шаблону' : `Удалить выбранные (${selectedKeys.size})`}
                      </Button>
                    )}
                    {deletingProgress && (
                      <Box sx={{ flex: 1, maxWidth: 200 }}>
                        <Typography variant="caption" color="text.secondary">
                          Удаление... {deletingProgress.current} / {deletingProgress.total}
                        </Typography>
                        <LinearProgress
                          variant="determinate"
                          value={(deletingProgress.current / deletingProgress.total) * 100}
                          sx={{
                            height: 4,
                            borderRadius: 1,
                            backgroundColor: accentSoftBg,
                            '& .MuiLinearProgress-bar': {
                              backgroundColor: accentColor,
                            }
                          }}
                        />
                      </Box>
                    )}
                  </Box>
                  <Box sx={{ width: 100, textAlign: 'center', flexShrink: 0 }}>Type</Box>
                  <Box sx={{ width: 100, textAlign: 'center', flexShrink: 0 }}>TTL</Box>
                </Box>

                {/* Virtualized List */}
                <Box ref={listContainerRef} sx={{ height: isFullscreen ? 'calc(100vh - 300px)' : 500, overflow: 'auto' }}>
                  <AutoSizer>
                    {({ height, width }: { height: number; width: number }) => (
                      <List
                        height={height}
                        itemCount={itemCount}
                        itemSize={45}
                        width={width}
                        itemData={keys}
                        overscanCount={5}
                        onItemsRendered={onItemsRendered}
                      >
                        {({ index, style }: { index: number; style: React.CSSProperties }) => {
                          // Render loading indicator for the last item if it exists and nextCursor is set
                          if (index === keys.length) {
                             return (
                              <Box style={style} sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', borderBottom: '1px solid', borderColor: 'divider', height: '100%' }}>
                                <CircularProgress size={20} sx={{ color: accentColor }} />
                              </Box>
                             )
                          }

                          const item = keys[index]
                          // Handle potential undefined item if keys changed during render
                          if (!item) return null

                          return (
                            <Box style={style} sx={{ display: 'flex', alignItems: 'center', px: 2, borderBottom: '1px solid', borderColor: 'divider', '&:hover': { bgcolor: 'action.hover' } }}>
                              <Box sx={{ width: 40, display: 'flex', justifyContent: 'center', flexShrink: 0 }}>
                                {canDelete ? (
                                  <IconButton
                                    size="small"
                                    onClick={() => {
                                      setKeysToDelete([item.key])
                                      setDeleteMessage(`Удалить ключ «${item.key}»? Отменить удаление будет нельзя.`)
                                      setDeleteConfirmOpen(true)
                                    }}
                                    sx={{ color: accentColor }}
                                  >
                                    <UiIcon glyph={DeleteIcon} size={16} />
                                  </IconButton>
                                ) : null}
                              </Box>
                              <Box sx={{ width: 48, flexShrink: 0 }}>
                                <Checkbox
                                  checked={selectedKeys.has(item.key)}
                                  onChange={() => toggleKey(item.key)}
                                  disabled={!canDelete}
                                  color="default"
                                  sx={{
                                    color: accentColor,
                                    '&.Mui-checked': {
                                      color: accentColor,
                                    },
                                    '&.Mui-indeterminate': {
                                      color: accentColor,
                                    },
                                  }}
                                />
                              </Box>
                              <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', minWidth: 0, gap: 1 }}>
                                <Typography
                                  sx={{
                                    flex: 1,
                                    minWidth: 0,
                                    fontFamily: 'var(--sl-font-mono)',
                                    fontSize: '0.85rem',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                  }}
                                >
                                  {item.key}
                                </Typography>
                                <Tooltip title="Открыть значение">
                                  <IconButton
                                    size="small"
                                    onClick={() => openKeyValueDialog(item.key)}
                                    sx={{
                                      color: accentColor,
                                      opacity: 0.72,
                                      flexShrink: 0,
                                      '&:hover': { opacity: 1, backgroundColor: accentSoftBg },
                                      padding: 0.5,
                                    }}
                                  >
                                    <UiIcon glyph={VisibilityIcon} size={16} />
                                  </IconButton>
                                </Tooltip>
                              </Box>
                              <Box sx={{ width: 100, display: 'flex', justifyContent: 'center', flexShrink: 0 }}>
                                <NeutralValueBadge
                                  sx={{
                                    borderColor: accentColor,
                                    backgroundColor: accentSoftBg,
                                    color: accentColor,
                                  }}
                                >
                                  {item.type}
                                </NeutralValueBadge>
                              </Box>
                              <Box sx={{ width: 100, display: 'flex', justifyContent: 'center', flexShrink: 0 }}>
                                <NeutralValueBadge>{item.ttl === -1 ? 'None' : `${item.ttl}s`}</NeutralValueBadge>
                              </Box>
                            </Box>
                          )
                        }}
                      </List>
                    )}
                  </AutoSizer>
                </Box>
              </>
            )}
          </Paper>
        </>
      )}
    </Box>
  )
  }

  return (
    <>
      {isFullscreen ? (
        <Dialog
          fullScreen
          open={isFullscreen}
          onClose={handleCloseFullscreen}
          transitionDuration={0}
          keepMounted
        >
          <AppBar sx={{ position: 'relative', bgcolor: accentColor }}>
            <Toolbar variant="dense">
              <IconButton edge="start" color="inherit" onClick={handleCloseFullscreen} aria-label="Закрыть">
                <UiIcon glyph={CloseIcon} />
              </IconButton>
              <Typography sx={{ ml: 2, flex: 1 }} variant="h6">
                Keys Browser — {targetName} (DB {selectedDb})
              </Typography>
            </Toolbar>
          </AppBar>
          <Box sx={{ overflowY: 'auto', flex: 1, bgcolor: 'background.default' }}>
            {showFullscreenContent ? renderContent() : (
              <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
                <CircularProgress size={40} sx={{ color: accentColor }} />
              </Box>
            )}
          </Box>
        </Dialog>
      ) : (
        renderContent()
      )}

      {/* Key Value Dialog */}
      <Dialog
        open={!!selectedKeyForView}
        onClose={closeKeyValueDialog}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            maxHeight: '80vh',
            border: '1px solid',
            borderColor: 'divider',
          }
        }}
      >
        <DialogTitle sx={{
          bgcolor: accentSoftBg,
          color: 'text.primary',
          borderBottom: '1px solid',
          borderColor: accentColor,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          pr: 2
        }}>
          <Typography variant="h6" sx={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {selectedKeyForView}
          </Typography>
          <IconButton aria-label="Закрыть значение ключа" onClick={closeKeyValueDialog} sx={{ color: 'text.secondary' }}>
            <UiIcon glyph={CloseIcon} />
          </IconButton>
        </DialogTitle>
        <DialogContent sx={{ p: 0 }}>
          {isLoadingKeyValue ? (
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 2, p: 4 }}>
              <CircularProgress size={24} sx={{ color: accentColor }} />
              <Typography>Загрузка значения...</Typography>
            </Box>
          ) : keyValueError ? (
            <Alert severity="error" sx={{ m: 2 }}>{keyValueError}</Alert>
          ) : keyValueData ? (
            <Box sx={{ p: 3 }}>
              {/* Key info header */}
              <Box sx={{
                display: 'flex',
                gap: 2,
                alignItems: 'center',
                mb: 2,
                flexWrap: 'wrap'
              }}>
                <NeutralValueBadge
                  sx={{
                    borderColor: accentColor,
                    backgroundColor: accentSoftBg,
                    color: accentColor,
                  }}
                >
                  Type: {keyValueData.type}
                </NeutralValueBadge>
                <NeutralValueBadge>
                  TTL: {keyValueData.ttl === -1 ? 'None' : `${keyValueData.ttl}s`}
                </NeutralValueBadge>
                {keyValueData.type === 'string' ? (
                  <NeutralValueBadge>
                    Size: {keyValueData.size > 1024 ? `${(keyValueData.size / 1024).toFixed(2)} KB` : `${keyValueData.size} B`}
                  </NeutralValueBadge>
                ) : (
                  <NeutralValueBadge>
                    Count: {keyValueData.total_count}
                  </NeutralValueBadge>
                )}
                <Box sx={{ flex: 1 }} />
                {canDelete ? (
                  <Button
                    variant="outlined"
                    size="small"
                    startIcon={<UiIcon glyph={DeleteIcon} />}
                    onClick={() => {
                      if (selectedKeyForView) {
                        setKeysToDelete([selectedKeyForView])
                        setDeleteMessage(`Удалить ключ «${selectedKeyForView}»? Отменить удаление будет нельзя.`)
                        setDeleteConfirmOpen(true)
                      }
                    }}
                    sx={{ color: accentColor, borderColor: accentColor }}
                  >
                    Удалить ключ
                  </Button>
                ) : null}
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<UiIcon glyph={CopyIcon} />}
                  onClick={() => copyToClipboard(formatValue(keyValueData.value))}
                  sx={{ color: accentColor, borderColor: accentColor }}
                >
                  Копировать значение
                </Button>
              </Box>

              {/* Warning for truncated data */}
              {keyValueData.has_more && (
                <Alert severity="info" sx={{ mb: 2 }}>
                  <Typography variant="body2">
                    Показаны первые {keyValueData.type === 'string' ? '1 MB' : '500 элементов'} из {keyValueData.total_count}
                  </Typography>
                </Alert>
              )}

              {/* Value display */}
              <Box sx={{
                bgcolor: 'grey.100',
                p: 2,
                borderRadius: 1,
                maxHeight: '50vh',
                overflow: 'auto',
                border: '1px solid',
                borderColor: 'divider',
                scrollbarGutter: 'stable'
              }}>
                {/* String */}
                {keyValueData.type === 'string' && (
                  <Box sx={{
                    fontFamily: 'var(--sl-font-mono)',
                    fontSize: '0.85rem',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all'
                  }}>
                    {formatValue(keyValueData.value)}
                  </Box>
                )}

                {/* Hash */}
                {keyValueData.type === 'hash' && (
                  <Box component="table" sx={{ width: '100%', borderCollapse: 'collapse' }}>
                    <Box component="tbody">
                      {Object.entries(keyValueData.value).map(([field, val]) => (
                        <Box component="tr" key={field} sx={{ borderBottom: '1px solid', borderColor: 'divider' }}>
                          <Box component="td" sx={{ py: 1, pr: 3, fontWeight: 600, verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                            {field}
                          </Box>
                          <Box component="td" sx={{ py: 1, fontFamily: 'var(--sl-font-mono)', fontSize: '0.85rem', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                            {formatValue(val)}
                          </Box>
                        </Box>
                      ))}
                    </Box>
                  </Box>
                )}

                {/* List */}
                {keyValueData.type === 'list' && (
                  <Box component="table" sx={{ width: '100%', borderCollapse: 'collapse' }}>
                    <Box component="tbody">
                      {keyValueData.value.map((item: {index: number, value: any}) => (
                        <Box component="tr" key={item.index} sx={{ borderBottom: '1px solid', borderColor: 'divider' }}>
                          <Box component="td" sx={{ py: 1, pr: 3, color: 'text.secondary', whiteSpace: 'nowrap' }}>
                            [{item.index}]
                          </Box>
                          <Box component="td" sx={{ py: 1, fontFamily: 'var(--sl-font-mono)', fontSize: '0.85rem', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                            {formatValue(item.value)}
                          </Box>
                        </Box>
                      ))}
                    </Box>
                  </Box>
                )}

                {/* Set */}
                {keyValueData.type === 'set' && (
                  <Box>
                    {keyValueData.value.map((item: any, idx: number) => (
                      <Box key={idx} sx={{ py: 0.75, fontFamily: 'var(--sl-font-mono)', fontSize: '0.85rem' }}>
                        {formatValue(item)}
                      </Box>
                    ))}
                  </Box>
                )}

                {/* ZSet */}
                {keyValueData.type === 'zset' && (
                  <Box component="table" sx={{ width: '100%', borderCollapse: 'collapse' }}>
                    <Box component="tbody">
                      {keyValueData.value.map((item: {member: any, score: number}, idx: number) => (
                        <Box component="tr" key={idx} sx={{ borderBottom: '1px solid', borderColor: 'divider' }}>
                          <Box component="td" sx={{ py: 1, pr: 3, color: 'text.secondary', whiteSpace: 'nowrap' }}>
                            {item.score}
                          </Box>
                          <Box component="td" sx={{ py: 1, fontFamily: 'var(--sl-font-mono)', fontSize: '0.85rem', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                            {formatValue(item.member)}
                          </Box>
                        </Box>
                      ))}
                    </Box>
                  </Box>
                )}

                {/* Other types */}
                {!['string', 'hash', 'list', 'set', 'zset'].includes(keyValueData.type) && (
                  <Box sx={{ fontFamily: 'var(--sl-font-mono)', fontSize: '0.85rem' }}>
                    {formatValue(keyValueData.value)}
                  </Box>
                )}
              </Box>
            </Box>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteConfirmOpen}
        onClose={() => setDeleteConfirmOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle sx={{ bgcolor: accentSoftBg, borderBottom: '1px solid', borderColor: accentColor }}>
          Подтвердите удаление ключей
        </DialogTitle>
        <DialogContent sx={{ pt: 3 }}>
          <Typography>{deleteMessage}</Typography>
          {keysToDelete.length > 0 && keysToDelete.length <= 5 && (
            <Box sx={{ mt: 2, p: 2, bgcolor: 'background.paper', borderRadius: 1, fontFamily: 'var(--sl-font-mono)', fontSize: '0.85rem' }}>
              {keysToDelete.map((key, idx) => (
                <Box key={idx} sx={{ py: 0.5 }}>{key}</Box>
              ))}
            </Box>
          )}
          {keysToDelete.length > 5 && (
            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: 'block' }}>
              и еще {keysToDelete.length - 5} ключей...
            </Typography>
          )}
        </DialogContent>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 2, p: 2, pt: 0 }}>
          <Button
            onClick={() => setDeleteConfirmOpen(false)}
            variant="outlined"
            sx={{ color: accentColor, borderColor: accentColor }}
          >
            Отмена
          </Button>
          <Button
            onClick={confirmDelete}
            variant="contained"
            sx={{ bgcolor: accentColor, '&:hover': { bgcolor: accentColor, filter: 'brightness(0.9)' } }}
          >
            Удалить
          </Button>
        </Box>
      </Dialog>
    </>
  )
}

export default RedisKeysBrowser
