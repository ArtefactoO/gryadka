import { useUiMotion } from "@project/ui/mui"
import { UiIcon } from "@project/ui/mui"
import { useEffect, useMemo, useState } from 'react'
import {
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Paper,
  Typography,
  Box,
  Chip,
} from '@mui/material'
import { ChevronDown as ExpandMoreIcon, ChevronUp as ExpandLessIcon } from "@project/ui/icons"
import { DatabaseUser } from '../services/api'
import { compactContentPaperSx } from './contentBlockStyles'
import { compactAccordionSummarySx } from "@project/ui/mui"
import SearchToggleInput from './SearchToggleInput'
import { useUiTheme } from "@project/ui/mui"

interface UsersSectionProps {
  users: DatabaseUser[]
  accentColor?: string
  accentSoftBg?: string
}

const UsersSection = ({ users, accentColor, accentSoftBg }: UsersSectionProps) => {
  const { visual } = useUiTheme()
  const usersAccordionTransitionProps = useUiMotion()
  const effectiveAccentColor = accentColor || visual.accentColor
  const effectiveAccentSoftBg = accentSoftBg || visual.accentSoftBg
  const [expanded, setExpanded] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const searchTrimmed = searchQuery.trim().toLowerCase()

  useEffect(() => {
    if (!expanded) {
      setSearchOpen(false)
    }
  }, [expanded])

  if (!users || users.length === 0) {
    return null
  }

  const groupedUsers = useMemo(() => {
    const map = new Map<string, Array<{ db: string; roles: string }>>()
    users.forEach((user) => {
      if (!map.has(user.user)) {
        map.set(user.user, [])
      }
      map.get(user.user)!.push({ db: user.db, roles: user.roles })
    })
    return Array.from(map.entries())
      .map(([username, grants]) => ({ username, grants }))
      .map((entry) => {
        if (!searchTrimmed) return entry

        const userMatched = entry.username.toLowerCase().includes(searchTrimmed)
        if (userMatched) {
          return entry
        }

        const filteredGrants = entry.grants.filter((grant) => {
          const dbMatched = (grant.db || '').toLowerCase().includes(searchTrimmed)
          const roleMatched = (grant.roles || '')
            .split(',')
            .map((role) => role.trim().toLowerCase())
            .filter(Boolean)
            .some((role) => role.includes(searchTrimmed))
          return dbMatched || roleMatched
        })

        return { username: entry.username, grants: filteredGrants }
      })
      .filter((entry) => entry.grants.length > 0)
  }, [users, searchTrimmed])

  return (
    <Paper
      elevation={0}
      sx={{
        ...compactContentPaperSx,
        border: '1px solid',
        borderColor: 'divider',
        boxShadow: 'none',
      }}
    >
      <Accordion
        expanded={expanded}
        onChange={(_event, isExpanded) => setExpanded(isExpanded)}
        slotProps={{ transition: usersAccordionTransitionProps }}
        sx={{

          width: 'fit-content',
          maxWidth: '100%',
          '&:before': { display: 'none' },
        }}
      >
        <AccordionSummary sx={compactAccordionSummarySx}>
          <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 1, maxWidth: '100%' }}>
            {expanded ? <UiIcon glyph={ExpandLessIcon} size={16} /> : <UiIcon glyph={ExpandMoreIcon} size={16} />}
            <Typography variant="subtitle1">Users</Typography>
            {expanded && (
              <SearchToggleInput
                value={searchQuery}
                isOpen={searchOpen}
                onValueChange={setSearchQuery}
                onOpenChange={setSearchOpen}
                onClear={() => setSearchQuery('')}
                accentColor={effectiveAccentColor}
                accentSoftBg={effectiveAccentSoftBg}
                inputSide="right"
                width={160}
                stopPropagation
                searchButtonTooltip="Поиск по пользователям, базам и правам"
              />
            )}
          </Box>
        </AccordionSummary>
        <AccordionDetails sx={{ pt: 0, pb: 1 }}>
          <Box sx={{ display: 'grid', gap: 1 }}>
            {groupedUsers.length > 0 ? (
              groupedUsers.map((user) => (
                <Paper
                  key={user.username}
                  elevation={0}
                  variant="outlined"
                  sx={{ p: 1, borderRadius: 1.5 }}
                >
                  <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.75 }}>
                    {user.username}
                  </Typography>
                  <Box sx={{ display: 'grid', gap: 0.75 }}>
                    {user.grants.map((grant, idx) => (
                      <Paper
                        key={`${user.username}-${grant.db}-${idx}`}
                        elevation={0}
                        variant="outlined"
                        sx={{ p: 0.75, borderRadius: 1.25 }}
                      >
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}>
                          <Typography variant="body2" color="text.secondary">DB:</Typography>
                          <Chip label={grant.db || 'N/A'} size="small" variant="outlined" />
                          <Typography variant="body2" color="text.secondary">Access:</Typography>
                          {(grant.roles || '')
                            .split(',')
                            .map((role) => role.trim())
                            .filter(Boolean)
                            .map((role) => (
                              <Chip
                                key={`${user.username}-${grant.db}-${role}-${idx}`}
                                label={role}
                                size="small"
                                variant="outlined"
                              />
                            ))}
                          {(!grant.roles || grant.roles.trim().length === 0) && (
                            <Chip label="N/A" size="small" variant="outlined" />
                          )}
                        </Box>
                      </Paper>
                    ))}
                  </Box>
                </Paper>
              ))
            ) : (
              <Typography variant="body2" color="text.secondary">
                По этому запросу ничего не найдено.
              </Typography>
            )}
          </Box>
        </AccordionDetails>
      </Accordion>
    </Paper>
  )
}

export default UsersSection
