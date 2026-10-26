/**
 * Authentication hook
 */
import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { authApi, LoginRequest, User } from '../services/api'

const AUTH_USER_STORAGE_KEY = 'auth_user'

interface AuthContextValue {
  isAuthenticated: boolean
  username: string | null
  displayName: string | null
  roles: string[]
  permissions: string[]
  loading: boolean
  login: (credentials: LoginRequest) => Promise<void>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
  hasPermission: (permission: string) => boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

const readStoredUser = (): User | null => {
  try {
    const raw = localStorage.getItem(AUTH_USER_STORAGE_KEY)
    return raw ? JSON.parse(raw) as User : null
  } catch {
    return null
  }
}

const writeStoredUser = (user: User) => {
  localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify(user))
}

const clearStoredAuth = () => {
  localStorage.removeItem('auth_token')
  localStorage.removeItem(AUTH_USER_STORAGE_KEY)
}

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const storedUser = readStoredUser()
  const hasToken = Boolean(localStorage.getItem('auth_token'))
  const [isAuthenticated, setIsAuthenticated] = useState(Boolean(hasToken && storedUser))
  const [username, setUsername] = useState<string | null>(storedUser?.username || null)
  const [displayName, setDisplayName] = useState<string | null>(storedUser?.display_name || storedUser?.username || null)
  const [roles, setRoles] = useState<string[]>(storedUser?.roles || [])
  const [permissions, setPermissions] = useState<string[]>(storedUser?.permissions || [])
  const [loading, setLoading] = useState(true)

  const applyUser = useCallback((user: User) => {
    writeStoredUser(user)
    setIsAuthenticated(true)
    setUsername(user.username)
    setDisplayName(user.display_name || user.username)
    setRoles(user.roles || [])
    setPermissions(user.permissions || [])
  }, [])

  const clearUser = useCallback(() => {
    clearStoredAuth()
    setIsAuthenticated(false)
    setUsername(null)
    setDisplayName(null)
    setRoles([])
    setPermissions([])
  }, [])

  const checkAuth = useCallback(async () => {

    try {
      const user = await authApi.getMe()
      applyUser(user)
    } catch {
      clearUser()
    } finally {
      setLoading(false)
    }
  }, [applyUser, clearUser])

  const login = async (credentials: LoginRequest): Promise<void> => {
    const response = await authApi.login(credentials)
    localStorage.setItem('auth_token', response.access_token)
    applyUser({
      username: response.username,
      display_name: response.display_name,
      auth_provider: response.auth_provider,
      roles: response.roles || [],
      permissions: response.permissions || [],
    })
    window.dispatchEvent(new Event('auth-changed'))
  }

  const logout = async (): Promise<void> => {
    try {
      await authApi.logout()
    } catch (error) {
      console.error('Logout error:', error)
    } finally {
      clearUser()
      window.dispatchEvent(new Event('auth-changed'))
    }
  }

  const hasPermission = useCallback(
    (permission: string) => permissions.includes('*') || permissions.includes(permission),
    [permissions]
  )

  useEffect(() => {
    checkAuth()

    const syncAuthState = () => {
      checkAuth()
    }

    window.addEventListener('auth-changed', syncAuthState)
    window.addEventListener('storage', syncAuthState)

    return () => {
      window.removeEventListener('auth-changed', syncAuthState)
      window.removeEventListener('storage', syncAuthState)
    }
  }, [checkAuth])

  const value = useMemo<AuthContextValue>(() => ({
    isAuthenticated,
    username,
    displayName,
    roles,
    permissions,
    loading,
    login,
    logout,
    checkAuth,
    hasPermission,
  }), [isAuthenticated, username, displayName, roles, permissions, loading, checkAuth, hasPermission])

  return createElement(AuthContext.Provider, { value }, children)
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider')
  }
  return context
}
