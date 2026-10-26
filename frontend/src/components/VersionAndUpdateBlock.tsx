/**
 * Shared version, external-link actions, refresh and update time.
 * Cluster health belongs to its scoped content section, not this header.
 */
import { Box, Chip } from '@mui/material'
import { ReactNode } from 'react'
import TargetRefreshButton from './TargetRefreshButton'

export interface RefreshTargetDescriptor {
  targetType: string
  targetName: string
}

export interface VersionAndUpdateBlockProps {
  /** Version chip(s) - e.g. "MongoDB Version: 8.2.3" or multiple chips */
  versionChips?: ReactNode
  /** Optional action area for Management UI, Kibana or Console links. */
  actionButtons?: ReactNode
  /** Formatted update time string (e.g. from formatUpdateTime). Omitted if null. */
  updateTime: string | null
  /** Optional current target for permission-guarded manual refresh. */
  refreshTarget?: RefreshTargetDescriptor
  /** Accent used by the permission-guarded refresh control. */
  accentColor?: string
  /** Hover surface used by the refresh control. */
  accentSoftBg?: string
}

const VersionAndUpdateBlock = ({
  versionChips,
  actionButtons,
  updateTime,
  refreshTarget,
  accentColor,
  accentSoftBg,
}: VersionAndUpdateBlockProps) => {
  return (
    <Box
      sx={{
        mb: 2,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 1,
        flexWrap: 'wrap',
      }}
    >
      {(versionChips || actionButtons) && <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {versionChips && <Box>{versionChips}</Box>}
        {actionButtons ? (
          <Box
            sx={{
              display: 'inline-flex',
              gap: 1,
              flexWrap: 'wrap',
              alignItems: 'center',
              alignSelf: 'flex-start',
            }}
          >
            {actionButtons}
          </Box>
        ) : null}
      </Box>}
      <Box sx={{ ml: 'auto', display: 'inline-flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap', maxWidth: '100%' }}>
        {refreshTarget ? (
          <TargetRefreshButton
            targetType={refreshTarget.targetType}
            targetName={refreshTarget.targetName}
            accentColor={accentColor}
            accentSoftBg={accentSoftBg}
          />
        ) : null}
        {updateTime && (
          <Chip
            label={`Обновлено: ${updateTime}`}
            size="small"
            variant="outlined"
            sx={{
              color: 'text.secondary',
              borderColor: 'divider',
              backgroundColor: 'transparent',
            }}
          />
        )}
      </Box>
    </Box>
  )
}

export default VersionAndUpdateBlock
