import { StatusBadge } from '@project/ui/mui'
import { monitorStatus, type MonitorStatusTone } from '../utils/monitorStatus'

export default function MonitorStatusBadge({ status, scope, description, tone }: {
  status?: string | null; scope?: string; description?: string; tone?: MonitorStatusTone;
}) {
  const presentation = monitorStatus(status)
  return <StatusBadge label={scope ? `${scope}: ${presentation.label}` : presentation.label}
    tone={tone || presentation.tone} description={description} />
}
