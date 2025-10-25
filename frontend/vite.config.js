import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
const DEFAULT_API = 'https://qld-quote-mapper.onrender.com'

export default defineConfig({
  plugins:[react()],
  server:{port:5173},
  define:{ __API_BASE__: JSON.stringify(process.env.VITE_API_BASE || DEFAULT_API) }
})
