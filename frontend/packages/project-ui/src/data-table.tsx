import { UiIcon } from "./mui-icon"
import { Fragment, useId, useState } from 'react'
import {
  Box,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { ChevronUp as ExpandLessIcon, ChevronDown as ExpandMoreIcon } from "./icons"
import type { SxProps, Theme } from '@mui/material/styles'
import {
  IDENTITY_CELL_SX,
  IDENTITY_HEADER_CELL_SX,
  IDENTITY_SCROLL_WRAPPER_SX,
  DATA_TABLE_PAPER_SX,
  DATA_TABLE_ROW_SX,
  DATA_TABLE_TABLE_CONTAINER_SX,
  DATA_TABLE_TABLE_SX,
} from './table-styles'
import { InfoTooltip } from './info-tooltip'

export interface DataTableColumn<T> {
  id: string
  label: React.ReactNode
  hidden?: boolean
  align?: 'left' | 'center' | 'right'
  headerSx?: SxProps<Theme>
  cellSx?: SxProps<Theme>
  /** Apply shared host column styling (minWidth, no truncation). Default true for first column. */
  isIdentityColumn?: boolean
  render: (row: T) => React.ReactNode
}

export interface DataTableProps<T> {
  title?: string
  titleAdornment?: React.ReactNode
  notice?: React.ReactNode
  headerActions?: React.ReactNode
  columns: DataTableColumn<T>[]
  rows: T[]
  getRowId: (row: T) => string
  /** Optional expandable row content; adds checkbox column and expand/collapse. */
  expandable?: {
    renderExpanded: (row: T) => React.ReactNode
    /** Optional tooltip for the expand button (e.g. per row). */
    getExpandTooltip?: (row: T) => string | undefined
  }
  size?: 'small' | 'medium'
  embedded?: boolean
}

export function DataTable<T>({
  title = 'Nodes',
  titleAdornment,
  notice,
  headerActions,
  columns: allColumns,
  rows,
  getRowId,
  expandable,
  size = 'small',
  embedded = false,
}: DataTableProps<T>) {
  const tableId = useId()
  const columns = allColumns.filter(column => !column.hidden)
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())

  const toggleRow = (id: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const colCount = (expandable ? 1 : 0) + columns.length

  return (
    <Box sx={{ minWidth: 0, maxWidth: '100%' }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography variant="h6">
          {title}
        </Typography>
        {titleAdornment}
        {headerActions ? (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            {headerActions}
          </Box>
        ) : null}
      </Box>
      {notice && <Box sx={{ mb: 1.5 }}>{notice}</Box>}
      <Paper elevation={0} sx={embedded ? { border: 0, boxShadow: 'none', backgroundColor: 'transparent', p: 0 } : DATA_TABLE_PAPER_SX}>
        <TableContainer sx={DATA_TABLE_TABLE_CONTAINER_SX}>
          <Table size={size} sx={DATA_TABLE_TABLE_SX}>
            <TableHead>
              <TableRow>
                {expandable && <TableCell padding="checkbox" />}
                {columns.map((col, idx) => (
                  <TableCell
                    key={col.id}
                    align={col.align ?? 'left'}
                    sx={
                      [
                        (col.isIdentityColumn !== false && idx === 0) || col.isIdentityColumn === true
                          ? IDENTITY_HEADER_CELL_SX
                          : {},
                        col.headerSx ?? {},
                      ] as SxProps<Theme>
                    }
                  >
                    {col.label}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => {
                const rowId = getRowId(row)
                const isExpanded = expandedRows.has(rowId)
                const expandTooltip = expandable?.getExpandTooltip?.(row)

                return (
                  <Fragment key={rowId}>
                    <TableRow hover sx={DATA_TABLE_ROW_SX}>
                      {expandable && (
                        <TableCell>
                          {expandTooltip ? (
                            <InfoTooltip title={expandTooltip}>
                              <IconButton
                                aria-label={`${isExpanded ? "Свернуть" : "Развернуть"} строку ${rowId}`}
                                aria-expanded={isExpanded}
                                aria-controls={isExpanded ? `${tableId}-${encodeURIComponent(rowId)}` : undefined}
                                size="small"
                                onClick={() => toggleRow(rowId)}
                              >
                                {isExpanded ? <UiIcon glyph={ExpandLessIcon} /> : <UiIcon glyph={ExpandMoreIcon} />}
                              </IconButton>
                            </InfoTooltip>
                          ) : (
                            <IconButton
                              aria-label={`${isExpanded ? "Свернуть" : "Развернуть"} строку ${rowId}`}
                                aria-expanded={isExpanded}
                                aria-controls={isExpanded ? `${tableId}-${encodeURIComponent(rowId)}` : undefined}
                              size="small"
                              onClick={() => toggleRow(rowId)}
                            >
                              {isExpanded ? <UiIcon glyph={ExpandLessIcon} /> : <UiIcon glyph={ExpandMoreIcon} />}
                            </IconButton>
                          )}
                        </TableCell>
                      )}
                      {columns.map((col, idx) => (
                        <TableCell
                          key={col.id}
                          component={col.isIdentityColumn !== false && idx === 0 ? 'th' : undefined}
                          scope={col.isIdentityColumn !== false && idx === 0 ? 'row' : undefined}
                          align={col.align ?? 'left'}
                          sx={
                            [
                              (col.isIdentityColumn !== false && idx === 0) || col.isIdentityColumn === true
                                ? IDENTITY_CELL_SX
                                : {},
                              col.cellSx ?? {},
                            ] as SxProps<Theme>
                          }
                        >
                          {(col.isIdentityColumn !== false && idx === 0) || col.isIdentityColumn === true ? (
                            <Box component="span" sx={IDENTITY_SCROLL_WRAPPER_SX} display="block">
                              {col.render(row)}
                            </Box>
                          ) : (
                            col.render(row)
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                    {expandable && isExpanded && (
                      <TableRow id={`${tableId}-${encodeURIComponent(rowId)}`} sx={DATA_TABLE_ROW_SX}>
                        <TableCell colSpan={colCount} sx={{ py: 2 }}>
                          <Box sx={{ pl: 4, pr: 4 }}>{expandable.renderExpanded(row)}</Box>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Box>
  )
}
