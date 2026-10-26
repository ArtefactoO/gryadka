import { ReactElement, ReactNode, useState } from 'react'
import { Box, ButtonBase, ClickAwayListener, Tooltip } from '@mui/material'

interface InfoTooltipProps {
  title: ReactNode
  children: ReactElement
  describeChild?: boolean
}

export function InfoControl({ label, description, children }: {
  label: string; description: string; children: ReactNode;
}) {
  const [open, setOpen] = useState(false)
  return <ClickAwayListener onClickAway={() => setOpen(false)}>
    <Box component="span" sx={{ display: 'inline-flex' }}>
    <Tooltip title={description} arrow describeChild disableTouchListener open={open}
      onOpen={() => setOpen(true)} onClose={() => setOpen(false)}>
      <ButtonBase aria-label={label} onClick={() => setOpen(true)}
        onKeyDown={event => { if (event.key === 'Escape') { setOpen(false); event.stopPropagation() } }}
        sx={{ color: 'inherit', font: 'inherit', borderRadius: 1, p: 0.5 }}>
        {children}
      </ButtonBase>
    </Tooltip>
    </Box>
  </ClickAwayListener>
}

export const InfoTooltip = ({ title, children, describeChild = false }: InfoTooltipProps) => {
  return (
    <Tooltip title={title} arrow describeChild={describeChild}>
      {children}
    </Tooltip>
  )
}
