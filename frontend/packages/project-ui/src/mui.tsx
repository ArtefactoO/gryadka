import { createContext, useContext, useEffect, useMemo, useState, useId, type ReactNode } from 'react'
import { Box, CssBaseline, IconButton, Menu, MenuItem, Divider, ThemeProvider, createTheme, useTheme as useMuiTheme, alpha, type Theme } from '@mui/material'
import { UiIcon } from "./mui-icon"
import { Sun, Moon, Monitor, Palette, Check } from './icons'
import { InfoTooltip } from './info-tooltip'
import { themeTokens, palettes, motionTokens, type PaletteId, type ThemeMode } from './tokens'
import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/cyrillic-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-sans/cyrillic-500.css'
import '@fontsource/ibm-plex-sans/latin-600.css'
import '@fontsource/ibm-plex-sans/cyrillic-600.css'
import '@fontsource/ibm-plex-mono/latin-400.css'
import '@fontsource/ibm-plex-mono/latin-600.css'
import '@fontsource/ibm-plex-mono/cyrillic-400.css'
import '@fontsource/ibm-plex-mono/cyrillic-600.css'
import './styles.css'

type ModePreference = ThemeMode | 'system'
interface Preferences { palette: PaletteId; mode: ModePreference }
interface ThemeContextValue extends Preferences { reducedMotion: boolean; visual: { accentColor: string; accentSoftBg: string }; setPalette: (palette: PaletteId) => void; setMode: (mode: ModePreference) => void }
const Context = createContext<ThemeContextValue | null>(null)
const systemMode = (): ThemeMode => window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
function readPreferences(storageKey: string): Preferences {
  try {
    const data = JSON.parse(localStorage.getItem(storageKey) || '{}')
    return { palette: ['signal','cobalt','orange'].includes(data.palette) ? data.palette : 'signal', mode: ['light','dark','system'].includes(data.mode) ? data.mode : 'system' }
  } catch { return { palette: 'signal', mode: 'system' } }
}

export function UiProvider({ children, storageKey = 'project-ui' }: { children: ReactNode; storageKey?: string }) {
  const [preferences, setPreferences] = useState(() => readPreferences(storageKey))
  const [system, setSystem] = useState(systemMode)
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    const mode = window.matchMedia('(prefers-color-scheme: light)')
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const updateMode = () => setSystem(systemMode())
    const updateMotion = () => setReduced(motion.matches)
    mode.addEventListener('change', updateMode); motion.addEventListener('change', updateMotion)
    return () => { mode.removeEventListener('change', updateMode); motion.removeEventListener('change', updateMotion) }
  }, [])
  const resolvedMode = preferences.mode === 'system' ? system : preferences.mode
  const tokens = themeTokens[preferences.palette][resolvedMode]
  useEffect(() => {
    document.documentElement.dataset.theme = resolvedMode
    document.documentElement.dataset.palette = preferences.palette
    for (const [key, value] of Object.entries(tokens)) document.documentElement.style.setProperty('--sl-'+key, value)
    try { localStorage.setItem(storageKey, JSON.stringify(preferences)) } catch { /* Theme still works for this session. */ }
  }, [preferences, resolvedMode, tokens, storageKey])
  const theme = useMemo(() => createTheme({
    palette: { mode: resolvedMode, contrastThreshold: 4.5, primary: { main: tokens.accent, light: tokens.accent, dark: tokens.accent, contrastText: tokens['accent-ink'] }, secondary: { main: tokens.accent }, background: { default: tokens.bg, paper: tokens.panel }, text: { primary: tokens.text, secondary: tokens.muted }, divider: tokens.line, success: { main: tokens.ok, light: tokens.ok, dark: tokens.ok }, warning: { main: tokens.warn, light: tokens.warn, dark: tokens.warn }, error: { main: tokens.danger, light: tokens.danger, dark: tokens.danger }, info: { main: tokens.accent, light: tokens.accent }, action: { hover: alpha(tokens.accent, 0.08), selected: alpha(tokens.accent, 0.12) } },
    shadows: Array.from({length:25},(_,index) => index === 0 ? 'none' : '0 8px 24px '+alpha(tokens['shadow-color'],0.32)) as Theme['shadows'],
    typography: { fontFamily: '"IBM Plex Sans", sans-serif', fontSize: 14, h4: { fontSize: '1.5rem', fontWeight: 600 }, h5: { fontSize: '1.25rem', fontWeight: 600 }, h6: { fontSize: '1rem', fontWeight: 600 }, button: { textTransform: 'none', fontWeight: 500 } },
    shape: { borderRadius: 6 },
    transitions: { easing: { easeInOut: motionTokens.ease, easeOut: motionTokens.ease }, duration: { shortest: reduced ? 0 : motionTokens.fast, shorter: reduced ? 0 : motionTokens.state, short: reduced ? 0 : motionTokens.state, standard: reduced ? 0 : motionTokens.state, enteringScreen: reduced ? 0 : motionTokens.panel, leavingScreen: reduced ? 0 : motionTokens.fast } },
    components: {
      MuiCssBaseline: { styleOverrides: { body: { backgroundColor: tokens.bg, color: tokens.text }, '::selection': { backgroundColor: alpha(tokens.accent, 0.25) } } },
      MuiButtonBase: { styleOverrides: { root: {
        '&.Mui-focusVisible, &:focus-visible': { outline: '2px solid '+tokens.focus, outlineOffset: 2 },
        '@media (pointer: coarse)': { '&&': { minHeight: 44, minWidth: 44 } },
      } } },
      MuiPaper: { defaultProps: { elevation: 0 }, styleOverrides: { root: { backgroundImage: 'none' } } },
      MuiCard: { defaultProps: { elevation: 0 }, styleOverrides: { root: { border: '1px solid '+tokens.line, boxShadow: 'none' } } },
      MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { minHeight: 32, transition: `background-color ${motionTokens.state}ms ${motionTokens.ease},color ${motionTokens.state}ms ${motionTokens.ease}`, '&:focus-visible': { outline: '2px solid '+tokens.focus, outlineOffset: 2 } } } },
      MuiIconButton: { styleOverrides: { root: { borderRadius: 6, '&:focus-visible': { outline: '2px solid '+tokens.focus, outlineOffset: 2 } } } },
      MuiTable: { defaultProps: { size: 'small' } },
      MuiTableCell: { styleOverrides: { root: { borderColor: tokens.line, fontVariantNumeric: 'tabular-nums', padding: '8px 12px' }, head: { backgroundColor: tokens.panel2, color: tokens.muted, fontWeight: 500 } } },
      MuiChip: { styleOverrides: { root: { borderRadius: 5, fontVariantNumeric: 'tabular-nums',
        '&:focus-visible': { outline: '2px solid '+tokens.focus, outlineOffset: 2 },
        '@media (pointer: coarse)': { '&[tabindex="0"]': { minHeight: 44, minWidth: 44 } },
      } } },
      MuiOutlinedInput: { styleOverrides: { root: { backgroundColor: tokens.panel2,
        '& .MuiOutlinedInput-notchedOutline, &:hover .MuiOutlinedInput-notchedOutline': { borderColor: tokens.focus },
        '@media (pointer: coarse)': { '&&': { minHeight: 44 } },
      } } },
      MuiDialog: { defaultProps: { transitionDuration: reduced ? 0 : motionTokens.state }, styleOverrides: { paper: { boxShadow: '0 8px 24px '+alpha(tokens['shadow-color'],0.32), borderRadius: 8 } } },
      MuiTooltip: { defaultProps: { enterDelay: 250 }, styleOverrides: { tooltip: { backgroundColor: tokens.panel2, color: tokens.text, boxShadow: '0 4px 16px '+alpha(tokens['shadow-color'],0.3), fontSize: 12 } } },
      MuiAlert: { styleOverrides: { root: { borderRadius: 6, border: '1px solid '+tokens.line } } },
      MuiTab: { styleOverrides: { root: { textTransform: 'none', minHeight: 32, borderRadius: 6,
        '&.Mui-focusVisible, &:focus-visible': { outline: '2px solid '+tokens.focus, outlineOffset: -2 },
      } } },
      MuiSkeleton: { defaultProps: { animation: reduced ? false : 'wave' } },
    },
  }), [tokens, resolvedMode, reduced])
  const value = useMemo(() => ({ ...preferences, reducedMotion: reduced, visual: { accentColor: tokens.accent, accentSoftBg: alpha(tokens.accent, 0.11) }, setPalette: (palette: PaletteId) => setPreferences(p => ({...p,palette})), setMode: (mode: ModePreference) => setPreferences(p => ({...p,mode})) }), [preferences, tokens, reduced])
  return <Context.Provider value={value}><ThemeProvider theme={theme}><CssBaseline />{children}</ThemeProvider></Context.Provider>
}

export function useUiTheme(): ThemeContextValue { const value = useContext(Context); if (!value) throw new Error('UI provider is missing'); return value }

export function ThemeControls() {
  const { palette, mode, setPalette, setMode } = useUiTheme()
  const dark = useMuiTheme().palette.mode === 'dark'
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const menuId = useId()
  const close = () => setAnchor(null)
  return <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
    <InfoTooltip title={anchor ? '' : 'Выбрать цветовую систему'}>
      <IconButton aria-label="Выбрать цветовую систему" aria-haspopup="menu"
        aria-controls={anchor ? menuId : undefined} aria-expanded={!!anchor}
        onClick={event => setAnchor(event.currentTarget)}>
        <UiIcon glyph={Palette} />
      </IconButton>
    </InfoTooltip>
    <InfoTooltip title={dark ? 'Включить светлую тему' : 'Включить темную тему'}>
      <IconButton aria-label={dark ? 'Включить светлую тему' : 'Включить темную тему'}
        onClick={() => setMode(dark ? 'light' : 'dark')}>
        <UiIcon glyph={dark ? Sun : Moon} />
      </IconButton>
    </InfoTooltip>
    <Menu id={menuId} anchorEl={anchor} open={!!anchor} onClose={close}
      PaperProps={{ elevation: 0, sx: { boxShadow: 'none', border: '1px solid', borderColor: 'divider', minWidth: 180,
        '& .MuiMenuItem-root.Mui-focusVisible': { outline: 'none' },
        '& .MuiMenuItem-root:focus-visible': { outline: '2px solid var(--sl-text)', outlineOffset: -4, borderRadius: 1 },
      } }}>
      {palettes.map(item => <MenuItem key={item.id} role="menuitemradio"
        aria-checked={palette === item.id} selected={palette === item.id}
        onClick={() => { setPalette(item.id); close() }} sx={{ gap: 1.5, minHeight: 44 }}>
        <Box component="span" aria-hidden sx={{ width: 14, height: 14, borderRadius: '50%',
          bgcolor: themeTokens[item.id].dark.accent, border: '1px solid', borderColor: 'divider' }} />
        <Box component="span" sx={{ flex: 1 }}>{item.label}</Box>
        {palette === item.id && <UiIcon glyph={Check} size={16} />}
      </MenuItem>)}
      <Divider />
      <MenuItem role="menuitemcheckbox" aria-checked={mode === 'system'} selected={mode === 'system'}
        onClick={() => { setMode(mode === 'system' ? (dark ? 'dark' : 'light') : 'system'); close() }} sx={{ gap: 1.5, minHeight: 44 }}>
        <UiIcon glyph={Monitor} size={16} /><Box component="span" sx={{ flex: 1 }}>System</Box>
        {mode === 'system' && <UiIcon glyph={Check} size={16} />}
      </MenuItem>
    </Menu>
  </Box>
}

export { UiIcon } from "./mui-icon"
export { DataTable } from "./data-table"
export type { DataTableColumn } from "./data-table"
export { IDENTITY_CONTENT_SX } from "./table-styles"
export { InfoTooltip, InfoControl } from "./info-tooltip"
export { compactTabsSx, targetTabsSx } from "./tabs"
export { compactAccordionSummarySx } from "./accordion"
export { StatusBadge } from './status-badge'
export type { StatusTone } from './status-badge'

export function useUiMotion() {
  const { reducedMotion } = useUiTheme()
  return { unmountOnExit: true, timeout: {enter: reducedMotion ? 0 : motionTokens.panel, exit: reducedMotion ? 0 : motionTokens.fast}, easing: {enter:motionTokens.ease,exit:motionTokens.ease} }
}
