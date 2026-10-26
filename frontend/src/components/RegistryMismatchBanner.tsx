import { UiIcon } from "@project/ui/mui"
import { Box, Chip, Typography } from '@mui/material'
import { TriangleAlert as WarningAmberIcon } from "@project/ui/icons"
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { addressesApi, targetsApi } from '../services/api'

const RegistryMismatchBanner = () => {
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: targets } = useQuery({ queryKey: ["targets"], queryFn: targetsApi.getTargets, staleTime: 30_000 })
  const { data } = useQuery({
    queryKey: ['addresses-global-banner'],
    enabled: targets?.some(target => target.type === 'addresses') ?? false,
    queryFn: () => addressesApi.getMismatchSummary(),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  const totalDnsMismatchCount = data?.total_count || 0

  if (totalDnsMismatchCount === 0) {
    return null
  }

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        flexWrap: 'wrap',
        px: 2,
        py: 1.25,
        backgroundColor: 'warning.light',
        color: 'warning.contrastText',
        borderBottom: '1px solid',
        borderColor: 'warning.dark',
      }}
    >
      <UiIcon glyph={WarningAmberIcon} sx={{ fontSize: 22 }} />
      <Typography variant="body2" component="span" sx={{ fontWeight: 600 }}>
        DNS mismatch
      </Typography>
      <Box
        component="button"
        type="button"
        onClick={() => {
          const nextParams = new URLSearchParams(searchParams)
          nextParams.set('type', 'addresses')
          nextParams.set('target', 'addresses')
          setSearchParams(nextParams)
        }}
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          border: 'none',
          padding: 0,
          margin: 0,
          background: 'transparent',
          color: 'inherit',
          cursor: 'pointer',
          '&:hover': { opacity: 0.9 },
        }}
        aria-label="открыть модуль адресов"
      >
        <Chip
          label={String(totalDnsMismatchCount)}
          size="small"
          sx={{
            backgroundColor: 'var(--sl-selected)',
            color: 'inherit',
            fontWeight: 700,
            minWidth: 28,
            height: 22,
            '& .MuiChip-label': { px: 1, py: 0 },
          }}
        />
      </Box>
    </Box>
  )
}

export default RegistryMismatchBanner
