import { useId } from 'react'
import { Box, Typography } from '@mui/material'
import { DataTable } from '@project/ui/mui'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { formatDateTimeLocal } from '../utils/formatUpdateTime'
import { formatNumber } from '../utils/metricFormat'

interface LagDataPoint {
  time: Date
  lag: number
}

interface LagChartProps {
  data: LagDataPoint[]
  nodeName?: string
  accentColor?: string
}

const LAG_LINE_STROKE = 'var(--sl-ok)'
const CHART_TEXT_FILL = 'var(--sl-muted)'

/** Only show points from the last N minutes so the chart window shifts with current time. */
const LAG_CHART_WINDOW_MINUTES = 15

const LagChart = ({ data, nodeName }: LagChartProps) => {
  const id = useId()
  const now = Date.now()
  const windowMs = LAG_CHART_WINDOW_MINUTES * 60 * 1000
  const valid = data.filter(point => Number.isFinite(point.time.getTime()) && Number.isFinite(point.lag))
    .sort((a, b) => a.time.getTime() - b.time.getTime())
  const windowed = valid.filter((p) => p.time.getTime() >= now - windowMs)
  const pointsToShow = windowed.length > 0 ? windowed : valid
  const chartData = pointsToShow.map((point, index) => ({
    id: `${point.time.toISOString()}:${index}`,
    time: formatDateTimeLocal(point.time.toISOString()),
    lag: point.lag,
  }))

  if (!chartData.length) return <Typography role="status" variant="body2" color="text.secondary">Измерения отставания пока не получены.</Typography>

  const maxLag = chartData.length ? Math.max(0, ...chartData.map((d) => d.lag)) : 0
  const yDomain: [number, number] = [0, maxLag + 1]
  const first = chartData[0], last = chartData[chartData.length - 1]
  const minLag = Math.min(...chartData.map(point => point.lag))
  const lagText = (lag: number) => formatNumber(lag, { maximumSignificantDigits: 6 })

  return (
    <Box component="figure" aria-label={`История отставания${nodeName ? ` узла ${nodeName}` : ''}`} aria-describedby={`${id}-summary`} sx={{ m: 0 }}>
      <Typography id={`${id}-summary`} variant="caption" color="text.secondary" component="p" sx={{ mt: 0.5, mb: 1 }}>
        С {first.time} по {last.time}. Последнее: {lagText(last.lag)} s.
        {' '}Минимум: {lagText(minLag)} s, максимум: {lagText(maxLag)} s.
      </Typography>
      <Box aria-hidden="true">
        <ResponsiveContainer width="100%" height={150}>
          <LineChart data={chartData}>
            <CartesianGrid stroke="var(--sl-line)" strokeDasharray="3 3" />
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: CHART_TEXT_FILL }} />
            <YAxis tick={{ fontSize: 10, fill: CHART_TEXT_FILL }} label={{ value: 'Lag (s)', fill: CHART_TEXT_FILL, angle: -90, position: 'insideLeft' }} domain={yDomain} />
            <Tooltip content={({ active, payload }) => {
              const row = payload?.find((p) => p.payload)?.payload
              return active && row != null ? <span style={{ padding: 4, fontSize: 12 }}>Отставание: {lagText(row.lag)} s</span> : null
            }} />
            <ReferenceLine y={4} stroke="var(--sl-ok)" strokeDasharray="2 2" label={{ value: 'Normal', fill: CHART_TEXT_FILL }} />
            <ReferenceLine y={8} stroke="var(--sl-warn)" strokeDasharray="2 2" label={{ value: 'Warning', fill: CHART_TEXT_FILL }} />
            <ReferenceLine y={12} stroke="var(--sl-danger)" strokeDasharray="2 2" label={{ value: 'Critical', fill: CHART_TEXT_FILL }} />
            <Line isAnimationActive={false} type="monotone" dataKey="lag" stroke={LAG_LINE_STROKE} strokeWidth={2} dot={false} legendType="none" />
          </LineChart>
        </ResponsiveContainer>
      </Box>
      <Box component="details" sx={{ mt: 1, '& > summary': { cursor: 'pointer', py: 1, minHeight: 44, color: 'text.primary', '&:focus-visible': { outline: '2px solid var(--sl-focus)', outlineOffset: 2 } } }}>
        <Box component="summary">Измерения отставания ({chartData.length})</Box>
        <DataTable embedded title="Измерения" rows={chartData} getRowId={point => point.id} columns={[
          { id: 'time', label: 'Время', render: point => point.time },
          { id: 'lag', label: 'Lag (s)', align: 'right', render: point => lagText(point.lag) },
        ]} />
      </Box>
    </Box>
  )
}

export default LagChart
