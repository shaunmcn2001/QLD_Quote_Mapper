import React, { useState } from 'react'
const API_BASE = __API_BASE__

const inputStyle = {
  width: '100%',
  padding: '12px 14px',
  borderRadius: 8,
  border: '1px solid #ccc',
  fontSize: 16,
}

const buttonStyle = {
  padding: '12px 18px',
  borderRadius: 8,
  border: 'none',
  background: '#324eda',
  color: 'white',
  fontSize: 16,
  cursor: 'pointer',
}

const cardStyle = {
  border: '1px solid #ddd',
  borderRadius: 12,
  padding: 20,
  background: 'white',
  boxShadow: '0 8px 16px rgba(0,0,0,0.04)',
}

const labelStyle = { fontWeight: 600, marginBottom: 8 }

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

async function streamKmzFromLotplan(lotplan, setStatus, setBusy) {
  const q = lotplan.trim()
  if (!q) {
    setStatus('Enter lot/plan tokens such as "4 RP30439, 3 RP048958"')
    return
  }
  setBusy(true)
  setStatus('Fetching parcels by lot/plan…')
  try {
    const res = await fetch(
      `${API_BASE}/kmz_by_lotplan?lotplan=${encodeURIComponent(q)}`,
      { headers: { 'X-API-Key': import.meta.env.VITE_API_KEY || '' } },
    )
    if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`)
    const blob = await res.blob()
    downloadBlob(blob, 'lotplans.kmz')
    setStatus('KMZ downloaded ✅')
  } catch (err) {
    setStatus(`Error: ${err.message}`)
  } finally {
    setBusy(false)
  }
}

async function streamKmzFromAddress(address, setStatus, setBusy) {
  const text = address.trim()
  if (!text) {
    setStatus('Enter a full address to search')
    return
  }
  setBusy(true)
  setStatus('Resolving address via MapServer…')
  try {
    const res = await fetch(`${API_BASE}/kmz_by_address`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': import.meta.env.VITE_API_KEY || '',
      },
      body: JSON.stringify({ address: text }),
    })
    if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`)
    const blob = await res.blob()
    downloadBlob(blob, 'address.kmz')
    setStatus('KMZ downloaded ✅')
  } catch (err) {
    setStatus(`Error: ${err.message}`)
  } finally {
    setBusy(false)
  }
}

export default function App() {
  const [lotplanText, setLotplanText] = useState('')
  const [addressText, setAddressText] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('Ready')

  const handleLotSubmit = (e) => {
    e.preventDefault()
    streamKmzFromLotplan(lotplanText, setStatus, setBusy)
  }

  const handleAddressSubmit = (e) => {
    e.preventDefault()
    streamKmzFromAddress(addressText, setStatus, setBusy)
  }

  return (
    <div style={{ fontFamily: 'system-ui, sans-serif', padding: 20, maxWidth: 720, margin: '0 auto', color: '#1f2933' }}>
      <h1 style={{ fontSize: 32, marginBottom: 6 }}>Parcel Agent Test Console</h1>
      <p style={{ opacity: 0.7, marginBottom: 24 }}>Hit the backend directly to download KMZ files for QLD parcels.</p>

      <div style={{ display: 'grid', gap: 24 }}>
        <form onSubmit={handleLotSubmit} style={cardStyle}>
          <div style={labelStyle}>Lot / Plan</div>
          <input
            style={inputStyle}
            value={lotplanText}
            onChange={(e) => setLotplanText(e.target.value)}
            placeholder="e.g. 4RP30439, 3RP048958"
          />
          <button type="submit" style={{ ...buttonStyle, marginTop: 12 }} disabled={busy}>
            {busy ? 'Processing…' : 'Download KMZ'}
          </button>
        </form>

        <form onSubmit={handleAddressSubmit} style={cardStyle}>
          <div style={labelStyle}>Full Address</div>
          <textarea
            style={{ ...inputStyle, minHeight: 90, resize: 'vertical' }}
            value={addressText}
            onChange={(e) => setAddressText(e.target.value)}
            placeholder='e.g. "12 Example Street, Brisbane QLD 4000"'
          />
          <button type="submit" style={{ ...buttonStyle, marginTop: 12 }} disabled={busy}>
            {busy ? 'Processing…' : 'Download KMZ'}
          </button>
        </form>
      </div>

      <div style={{ marginTop: 24, fontSize: 14, color: '#334155' }}>Status: {status}</div>
      <p style={{ fontSize: 12, opacity: 0.6, marginTop: 12 }}>
        Backend API: <code>{API_BASE}</code>
      </p>
    </div>
  )
}
