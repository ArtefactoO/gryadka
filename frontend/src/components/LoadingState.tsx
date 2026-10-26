import { Box, Skeleton, Typography } from '@mui/material'

export default function LoadingState({ label }: { label: string }) {
  return <Box>
    <Typography role="status" aria-live="polite" variant="body2" color="text.secondary">
      Загрузка {label}…
    </Typography>
    <Box aria-busy="true" aria-hidden="true"><Skeleton height={48} /><Skeleton height={140} /></Box>
  </Box>
}
