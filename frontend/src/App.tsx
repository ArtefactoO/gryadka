import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Box } from '@mui/material'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import LoginPage from './pages/LoginPage'
import { useAuth } from './hooks/useAuth'
import LoadingState from './components/LoadingState'

function App() {
  const { isAuthenticated, loading } = useAuth()
  return (
    <BrowserRouter>
      <Box sx={{ minHeight: '100vh', backgroundColor: 'background.default' }}>
        <Routes>
          <Route
            path="/"
            element={
              loading ? <LoadingState label="профиля" /> : isAuthenticated ? (
                <Layout><Dashboard /></Layout>
              ) : <LoginPage />
            }
          />
        </Routes>
      </Box>
    </BrowserRouter>
  )
}

export default App
