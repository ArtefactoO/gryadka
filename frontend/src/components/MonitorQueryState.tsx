import { Alert } from '@mui/material'
import { getApiErrorMessage } from '../utils/apiError'
import LoadingState from './LoadingState'

interface Props { loading: boolean; error: unknown; hasData: boolean; label: string }

export default function MonitorQueryState({ loading, error, hasData, label }: Props) {
  if (loading) return <LoadingState label={label} />
  if (error) return <Alert severity="warning">{hasData ? "Не удалось обновить данные. Показан последний полученный результат. " : ""}{getApiErrorMessage(error, `Данные ${label} недоступны. Попробуйте обновить монитор.`)}</Alert>
  if (!hasData) return <Alert severity="info">Данных {label} пока нет.</Alert>
  return null
}
