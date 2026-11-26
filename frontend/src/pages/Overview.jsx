import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const d = data.分布 || {}
  const exposed = data.残留露口 || { 残留实例数: 0, 涉及发版单数: 0, 残留明细: [] }
  return (
    <div className="panel">
      <h2>总览</h2>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div
            className={`stat${k === '已回滚' ? ' stat-ok' : k === '回滚中' ? ' stat-warn' : ''}`}
            key={k}
          >
            <div className="n">{v}</div><div className="l">{k}</div>
          </div>
        ))}
      </div>

      <h2>回退残留露口</h2>
      <div className="grid">
        <div className={`stat${exposed.残留实例数 > 0 ? ' stat-warn' : ' stat-ok'}`}>
          <div className="n">{exposed.残留实例数}</div><div className="l">残留实例数</div>
        </div>
        <div className={`stat${exposed.涉及发版单数 > 0 ? ' stat-warn' : ' stat-ok'}`}>
          <div className="n">{exposed.涉及发版单数}</div><div className="l">涉及回滚中发版单数</div>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>实例编号</th><th>应用</th><th>环境</th><th>版本</th><th>健康</th><th>归属发版单</th></tr></thead>
          <tbody>
            {(exposed.残留明细 || []).length === 0 && (
              <tr><td colSpan="6" className="muted">无残留</td></tr>
            )}
            {(exposed.残留明细 || []).map((r) => (
              <tr key={r.实例编号} className="row-residual">
                <td>{r.实例编号}</td><td>{r.应用}</td><td>{r.环境}</td>
                <td>{r.版本}</td><td>{r.健康}</td><td>{r.归属发版单}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>在途发版</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>当前环境</th><th>残留数</th><th>流水线</th></tr></thead>
          <tbody>
            {(data.在途 || []).map((r) => (
              <tr key={r.编号} className={r.残留数 > 0 ? 'row-residual' : ''}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.目标版本}</td>
                <td>{r.状态}</td><td>{r.当前环境 || '-'}</td><td>{r.残留数}</td><td>{r.流水线}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>各环境版本</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>版本</th><th>更新时间</th></tr></thead>
          <tbody>
            {(data.环境版本 || []).map((v, i) => (
              <tr key={i}><td>{v.应用}</td><td>{v.环境}</td><td>{v.版本}</td><td>{v.更新时间}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
