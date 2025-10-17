import React, { useState } from 'react'
const API_BASE = __API_BASE__

export default function App() {
  const [pdfFile, setPdfFile] = useState(null)
  const [pdfBusy, setPdfBusy] = useState(false)
  const [lotplan, setLotplan] = useState("")
  const [lotBusy, setLotBusy] = useState(false)
  const [status, setStatus] = useState("")

  const handlePdfDrop = (file)=>{ if(!file) return; setPdfFile(file); setStatus(`Selected: ${file.name}`) }
  const downloadBlob = (blob, name)=>{ const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=name; a.click(); URL.revokeObjectURL(url) }

  const submitPdf = async () => {
    if(!pdfFile){ setStatus("Choose a PDF first"); return }
    setPdfBusy(true); setStatus("Processing PDF → KMZ...")
    try{
      const fd = new FormData(); fd.append('pdf', pdfFile)
      const res = await fetch(`${API_BASE}/process_pdf_kmz`, { method:'POST', body:fd, headers:{'X-API-Key': import.meta.env.VITE_API_KEY || ''} })
      if(!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`)
      const blob = await res.blob(); downloadBlob(blob, pdfFile.name.replace(/\.pdf$/i,'')+'.kmz'); setStatus("Done ✅")
    }catch(e){ setStatus("Error: "+e.message) } finally{ setPdfBusy(false) }
  }

  const submitLotplan = async () => {
    const q = lotplan.trim(); if(!q){ setStatus("Enter Lot/Plan like '2 RP12345'"); return }
    setLotBusy(true); setStatus("Fetching KMZ by Lot/Plan...")
    try{
      const res = await fetch(`${API_BASE}/kmz_by_lotplan?lotplan=${encodeURIComponent(q)}`, { headers:{'X-API-Key': import.meta.env.VITE_API_KEY || ''} })
      if(!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`)
      const blob = await res.blob(); downloadBlob(blob, "lotplans.kmz"); setStatus("Done ✅")
    }catch(e){ setStatus("Error: "+e.message) } finally{ setLotBusy(false) }
  }

  return (
    <div style={{fontFamily:'system-ui, sans-serif', padding:20, maxWidth:900, margin:'0 auto'}}>
      <h1>Parcel Agent (QLD MapServer)</h1>
      <p style={{opacity:.8}}>Uses Address layer (0) → Lot/Plan → Parcels (4)</p>
      <div style={{display:'grid', gap:20, gridTemplateColumns:'1fr 1fr'}}>
        <div style={{border:'1px solid #ddd', borderRadius:12, padding:16}}>
          <h2>1) PDF to KMZ</h2>
          <div onDragOver={(e)=>e.preventDefault()} onDrop={(e)=>{e.preventDefault(); handlePdfDrop(e.dataTransfer.files?.[0])}}
               style={{border:'2px dashed #aaa', borderRadius:12, padding:30, textAlign:'center', marginBottom:12}}>
            <p>Drag & drop PDF here</p><p style={{fontSize:12, opacity:.7}}>or</p>
            <input type="file" accept="application/pdf" onChange={(e)=>handlePdfDrop(e.target.files?.[0])}/>
          </div>
          <button onClick={submitPdf} disabled={pdfBusy} style={{padding:'10px 16px', borderRadius:8}}>
            {pdfBusy ? 'Processing…' : 'Convert PDF → KMZ'}
          </button>
        </div>
        <div style={{border:'1px solid #ddd', borderRadius:12, padding:16}}>
          <h2>2) LOT/PLAN Quick KMZ</h2>
          <input value={lotplan} onChange={(e)=>setLotplan(e.target.value)} placeholder="2 RP12345, 3 DP752379"
                 style={{width:'100%', padding:10, borderRadius:8, border:'1px solid #ccc'}}/>
          <div style={{marginTop:10}}>
            <button onClick={submitLotplan} disabled={lotBusy} style={{padding:'10px 16px', borderRadius:8}}>
              {lotBusy ? 'Fetching…' : 'Download KMZ'}
            </button>
          </div>
        </div>
      </div>
      <div style={{marginTop:16, color:'#444'}}>{status}</div>
      <hr style={{margin:'24px 0'}}/>
      <p style={{fontSize:12, opacity:.7}}>Backend: <code>{API_BASE}</code></p>
    </div>
  )
}
