import { Box, type SxProps, type Theme } from "@mui/material"
import type { SVGProps } from "react"
import type { LucideIcon } from "lucide-react"
interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'color' | 'ref'> { glyph: LucideIcon; size?: number | string; sx?: SxProps<Theme>; color?: string; htmlColor?: string }
export function UiIcon({ glyph, size = 18, sx, color, htmlColor, ...props }: IconProps) {
  const resolvedColor = htmlColor || (color === 'action' ? 'text.secondary' : color === 'disabled' ? 'text.disabled' : color && ['primary','secondary','error','warning','success','info'].includes(color) ? color+'.main' : color)
  const Glyph = glyph
  return <Box component="span" className="sl-icon" sx={[{ color: resolvedColor, display: "inline-flex", flexShrink: 0, verticalAlign: "middle" }, ...(Array.isArray(sx) ? sx : sx ? [sx] : [])]}><Glyph size={size} strokeWidth={1.7} aria-hidden="true" {...props} /></Box>
}
