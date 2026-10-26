import { useState } from "react"
import { Dialog, DialogContent, DialogTitle } from '@mui/material'
import LoginForm from './LoginForm'

export default function LoginModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [busy, setBusy] = useState(false)
  return <Dialog open={open} onClose={() => { if (!busy) onClose() }} maxWidth="xs" fullWidth aria-labelledby="login-dialog-title">
    <DialogTitle id="login-dialog-title">Вход в Gryadka</DialogTitle>
    <DialogContent><LoginForm onSuccess={onClose} onBusyChange={setBusy} /></DialogContent>
  </Dialog>
}
