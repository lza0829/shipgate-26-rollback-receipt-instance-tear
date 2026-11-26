import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Rollback() {
  const [rows, setRows] = useState([])
  const [rid, setRid] = useState('')
  const [ver, setVer] = useState('1.0.0')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = async () => {
    const all = await api('/发版单')
    // 可发起回退的单子 + 已在回滚中（需要继续清残留）的单子
    const active = all.filter((r) =>
      ['已放行', '晋升中', '回滚中'].includes(r.状态))
    setRows(active)
    if (!rid && active[0]) setRid(String(active[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])

  const go = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api(`/发版单/${rid}/回滚`, {
        method: 'POST', body: JSON.stringify({ 目标版本: ver }),
      })
      setMsg(r.状态 === '已回滚'
        ? `已收敛：发版单 ${r.发版单号} 已回滚，残留数 ${r.残留数}`
        : `回滚中：发版单 ${r.发版单号} 尚有 ${r.残留数} 个残留实例，清完才标已回滚`)
      load()
    } catch (e) { setErr(e.message) }
  }

  const purge = async (id) => {
    setErr(''); setMsg('')
    try {
      const r = await api(`/发版单/${id}/清理残留`, { method: 'POST' })
      setMsg(`清理 ${r.清理残留数} 个残留，剩余 ${r.剩余残留数}；发版单状态：${r.发版单.状态}`)
      load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>回滚（严格收敛：残留清零才标已回滚）</h2>
      {err && <p className="err">{err}</p>}
      {msg && <p className="ok">{msg}</p>}
      <div className="row">
        <label>发版单
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {rows.map((r) => (
              <option key={r.编号} value={r.编号}>
                {r.发版单号} · {r.应用} · {r.当前环境} · {r.状态}
                {r.状态 === '回滚中' ? `（残留${r.残留数}）` : ''}
              </option>
            ))}
          </select>
        </label>
        <label>回滚目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} /></label>
        <button type="button" className="danger" onClick={go}>执行回滚</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <p className="muted">
        完成口径：发版单置「已回滚」当且仅当实例册残留为 0；回滚中名册不切、对账不计已回滚。
        残留只能通过清理入口删除，探针改灯不改变残留身份。
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>发版单号</th><th>应用</th><th>环境</th><th>状态</th><th>回退目标</th><th>残留数</th><th>操作</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.残留数 > 0 ? 'row-residual' : ''}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.当前环境 || '-'}</td>
                <td>{r.状态}</td><td>{r.回退目标版本 || '-'}</td>
                <td>{r.残留数}</td>
                <td>
                  {r.残留数 > 0 && (
                    <button type="button" className="danger" onClick={() => purge(r.编号)}>
                      清理残留（{r.残留数}）
                    </button>
                  )}
                  {r.状态 === '回滚中' && r.残留数 === 0 && <span className="muted">待收敛</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
