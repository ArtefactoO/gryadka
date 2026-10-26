export type DatabaseOverview = Record<string, string[]>
export interface DatabaseMatch { targetName: string; dbName: string }

/** Same substring and case-insensitive search for MongoDB and PostgreSQL. */
export function findDatabaseMatches(overview: DatabaseOverview, query: string): DatabaseMatch[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return []
  return Object.entries(overview).flatMap(([targetName, names]) =>
    (Array.isArray(names) ? names : []).filter(name => name.toLowerCase().includes(needle))
      .map(dbName => ({ targetName, dbName })))
}
