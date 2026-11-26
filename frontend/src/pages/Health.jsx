import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Health() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = () => api('/实例').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])

  const setH = async (id, health) => {
    setErr(''); setMsg('')
    try {
      await api(`/实例/${id}/健康`, { method: 'POST', body: JSON.stringify({ 健康: health }) })
      load()
    } catch (e) { setErr(e.message) }
  }

  const purge = async (id) => {
    setErr(''); setMsg('')
    try {
      const r = await api(`/实例/${id}/清理残留`, { method: 'POST' })
      const st = r.发版单.状态
      setMsg(st === '已回滚'
        ? `已清理实例 ${id} 并收敛：发版单 ${r.发版单.发版单号} 已回滚`
        : `已清理实例 ${id}，但发版单 ${r.发版单.发版单号} 仍停在${st}：旧实例仍在跑/退役标缺位，不得宣称干净`)
      load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>健康探针与实例册</h2>
      {err && <p className="err">{err}</p>}
      {msg && <p className="ok">{msg}</p>}
      <p className="muted">
        探针绿/红只反映心跳，不改变残留身份；「是否残留」为回退残留账，只能由清理入口删除。
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>应用</th><th>环境</th><th>版本</th><th>健康</th><th>发版单</th><th>是否残留</th><th>残留归属</th><th>退役标</th><th>操作</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.是否残留 === '是' ? 'row-residual' : ''}>
                <td>{r.应用}</td><td>{r.环境}</td><td>{r.版本}</td><td>{r.健康}</td>
                <td>{r.发版单编号 || '-'}</td>
                <td>{r.是否残留}</td>
                <td>{r.残留归属发版单 || '-'}</td>
                <td>{r.退役标}</td>
                <td>
                  <button type="button" onClick={() => setH(r.编号, '探针绿')}>探针绿</button>{' '}
                  <button type="button" className="danger" onClick={() => setH(r.编号, '探针红')}>探针红</button>{' '}
                  {r.是否残留 === '是' && (
                    <button type="button" className="danger" onClick={() => purge(r.编号)}>清理残留</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
