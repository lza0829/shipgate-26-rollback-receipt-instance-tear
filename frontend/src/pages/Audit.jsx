import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Audit() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const load = () => api('/审计').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  return (
    <div className="panel">
      <h2>审计流水</h2>
      {err && <p className="err">{err}</p>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>类别</th><th>动作</th><th>关联</th><th>备注</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}><td>{r.时间}</td><td>{r.类别}</td><td>{r.动作}</td><td>{r.关联编号 || '-'}</td><td>{r.备注}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
