import { UiIcon } from "@project/ui/mui"
import { useEffect, useRef } from 'react'
import { Box, ClickAwayListener, IconButton, InputAdornment, TextField, Tooltip } from '@mui/material'
import { Search as SearchIcon } from "@project/ui/icons"
import { X as CloseIcon } from "@project/ui/icons"

interface SearchToggleInputProps {
  value: string
  isOpen: boolean
  onValueChange: (value: string) => void
  onOpenChange: (open: boolean) => void
  onApply?: () => void
  onClear?: () => void
  accentColor: string
  accentSoftBg: string
  inputSide?: 'left' | 'right'
  width?: number
  stopPropagation?: boolean
  buttonOffsetY?: number
  searchButtonTooltip?: string
  clearButtonTooltip?: string
}

const SearchToggleInput = ({
  value,
  isOpen,
  onValueChange,
  onOpenChange,
  onApply,
  onClear,
  accentColor,
  accentSoftBg,
  inputSide = 'left',
  width = 160,
  stopPropagation = false,
  buttonOffsetY = 0,
  searchButtonTooltip = 'Поиск',
  clearButtonTooltip = 'Очистить поиск',
}: SearchToggleInputProps) => {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const buttonRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!isOpen) return
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(timer)
  }, [isOpen])

  const handleClose = (returnFocus = false) => {
    onOpenChange(false)
    if (returnFocus) buttonRef.current?.focus()
  }
  const handleToggle = () => onOpenChange(!isOpen)

  const inputNode = isOpen ? (
    <Box
      sx={{
        width,
        overflow: 'hidden',
        transition: 'width 220ms ease, opacity 180ms ease',
      }}
    >
      <TextField
        inputRef={inputRef}
        size="small"
        value={value}
        inputProps={{ 'aria-label': searchButtonTooltip }}
        onChange={(event) => onValueChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') { event.stopPropagation(); handleClose(true) }
          if (event.key === 'Enter') onApply?.()
        }}
        InputProps={{
          endAdornment: value ? (
            <InputAdornment position="end">
              <Tooltip title={clearButtonTooltip}>
                <IconButton
                  aria-label={clearButtonTooltip}
                  size="small"
                  onClick={() => onClear?.()}
                  sx={{ color: accentColor }}
                >
                  <UiIcon glyph={CloseIcon} size={16} />
                </IconButton>
              </Tooltip>
            </InputAdornment>
          ) : undefined,
          sx: {
            height: 32,
            '& input': {
              py: 0.5,
              fontSize: '0.875rem',
            },
          },
        }}
        sx={{
          width,
          '& .MuiOutlinedInput-root': {
            backgroundColor: 'background.paper',
            '& .MuiOutlinedInput-notchedOutline': {
              borderColor: accentColor,
            },
            '&:hover .MuiOutlinedInput-notchedOutline': {
              borderColor: accentColor,
            },
            '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
              borderColor: accentColor,
              borderWidth: 1.5,
            },
          },
        }}
      />
    </Box>
  ) : null

  const buttonNode = (
    <Tooltip title={searchButtonTooltip}>
      <IconButton
        ref={buttonRef}
        aria-label={searchButtonTooltip}
        aria-expanded={isOpen}
        size="small"
        onClick={handleToggle}
        sx={{
          color: accentColor,
          width: 28,
          height: 28,
          p: 0.5,
          alignSelf: 'center',
          transform: buttonOffsetY === 0 ? undefined : `translateY(${buttonOffsetY}px)`,
          border: '1px solid',
          borderColor: accentColor,
          backgroundColor: 'transparent',
          '&:hover': { backgroundColor: accentSoftBg },
        }}
      >
        <UiIcon glyph={SearchIcon} size={16} />
      </IconButton>
    </Tooltip>
  )

  return (
    <ClickAwayListener onClickAway={() => { if (isOpen) handleClose() }}>
      <Box
        sx={{ display: 'flex', alignItems: 'center', gap: 0.75, minHeight: 28 }}
        onClick={stopPropagation ? (event) => event.stopPropagation() : undefined}
        onMouseDown={stopPropagation ? (event) => event.stopPropagation() : undefined}
      >
        {inputSide === 'left' ? inputNode : null}
        {buttonNode}
        {inputSide === 'right' ? inputNode : null}
      </Box>
    </ClickAwayListener>
  )
}

export default SearchToggleInput
