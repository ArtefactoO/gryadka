# Gryadka interface conventions

Gryadka is an operational interface for database and broker monitoring. Prioritize target identity, health, observation time and actionable detail over decorative status.

Use the local Project UI workspace for themes, typography, icons, DataTable and tooltips. The signal/mono, cobalt and orange palettes are independent of light, dark and system modes. IBM Plex Sans and Mono are bundled through npm dependencies. Honor reduced motion.

Keep unknown values neutral and distinguish zero from missing data. Background updates must not reset tabs, filters, expanded rows or scroll position. A failed update keeps the last snapshot with an explicit explanation. Loading, empty, denied and unavailable states have different meanings.

Use keyboard-accessible controls and visible focus. Tables scroll inside their container on narrow screens. Status uses text or shape as well as color. Place permission requirements near the action without implying that UI authorization grants target privileges.

The application owns monitoring and query invalidation. Shared components do not own API requests or target models.
