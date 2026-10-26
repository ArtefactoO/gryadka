import { useUiMotion } from "@project/ui/mui"
import { UiIcon } from "@project/ui/mui"
import { useState } from 'react'
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
import { RabbitUser } from '../services/api'
import { compactContentPaperSx } from './contentBlockStyles'
import { compactAccordionSummarySx } from "@project/ui/mui"

interface RabbitUsersSectionProps {
  users: RabbitUser[]
}

const RabbitUsersSection = ({ users }: RabbitUsersSectionProps) => {
  const usersAccordionTransitionProps = useUiMotion()
  const [expanded, setExpanded] = useState(false)

  if (!users || users.length === 0) {
    return null
  }

  const getTagTooltip = (tag: string): string => {
    const normalized = tag.toLowerCase().trim()
    if (normalized === 'administrator') {
      return 'Administrator: полный доступ ко всем ресурсам и управлению.'
    }
    if (normalized === 'monitoring') {
      return 'Monitoring: просмотр кластера, подключений, каналов, очередей, экзчейнджей и привязок.'
    }
    if (normalized === 'management') {
      return 'Management: просмотр и управление пользователями, vhost, политиками и параметрами.'
    }
    if (normalized === 'policymaker') {
      return 'Policymaker: просмотр и управление политиками и параметрами.'
    }
    return `Тег: ${tag}`
  }

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
          </Box>
        </AccordionSummary>
        <AccordionDetails sx={{ pt: 0, pb: 1 }}>
          <Box sx={{ display: 'grid', gap: 1 }}>
            {users.map((user) => {
              const tags = user.tags
                ? user.tags
                    .split(',')
                    .map((tag) => tag.trim())
                    .filter(Boolean)
                : []
              return (
                <Paper key={user.name} elevation={0} variant="outlined" sx={{ p: 1, borderRadius: 1.5 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.75 }}>
                    {user.name}
                  </Typography>
                  {tags.length > 0 ? (
                    <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                      {tags.map((tag) => (
                        <Chip
                          key={`${user.name}-${tag}`}
                          label={tag}
                          size="small"
                          variant="outlined"
                          title={getTagTooltip(tag)}
                          color={
                            tag.toLowerCase() === 'administrator'
                              ? 'error'
                              : tag.toLowerCase() === 'management'
                                ? 'warning'
                                : 'default'
                          }
                        />
                      ))}
                    </Box>
                  ) : (
                    <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
                      Теги не назначены.
                    </Typography>
                  )}
                </Paper>
              )
            })}
          </Box>
        </AccordionDetails>
      </Accordion>
    </Paper>
  )
}

export default RabbitUsersSection
