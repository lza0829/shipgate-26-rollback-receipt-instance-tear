import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Rollback() {
  const [rows, setRows] = useState([])
  const [rid, setRid] = useState('')
  const [ver, setVer] = useState('1.0.0')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [gap, setGap] = useState(null)

  const load = async () => {
    const all = await api('/发版单')
    // 可发起回退的单子 + 已在回滚中（需要继续清残留）的单子
    const active = all.filter((r) =>
      ['已放行', '晋升中', '回滚中', '已回滚'].includes(r.状态))
    setRows(active)
    if (!rid && active[0]) setRid(String(active[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])

  // 回滚入口先看实例残留旁读，再核退役标与批次对照、审计重放
  const loadGap = async (id = rid) => {
    if (!id) { setGap(null); return }
    try {
      const g = await api(`/发版单/${id}/缺口`)
      setGap(g)
    } catch (e) {
      setGap(null); setErr(e.message)
    }
  }
  useEffect(() => { loadGap(rid) }, [rid])

  const go = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api(`/发版单/${rid}/回滚`, {
        method: 'POST', body: JSON.stringify({ 目标版本: ver }),
      })
      // 回执只读库内事实：不得凭一次 200 就宣称干净
      setMsg(r.状态 === '已回滚'
        ? `已收敛：发版单 ${r.发版单号} 已回滚，残留数 ${r.残留数}，在跑旧版本 ${r.在跑旧版本数}`
        : `回滚中：发版单 ${r.发版单号} 尚有 ${r.残留数} 个残留、${r.在跑旧版本数} 个旧版本实例在跑、退役标缺位 ${r.退役标缺位数}，清完才标已回滚`)
      load()
    } catch (e) { setErr(e.message) }
  }

  const purge = async (id) => {
    setErr(''); setMsg('')
    try {
      const r = await api(`/发版单/${id}/清理残留`, { method: 'POST' })
      const g = r.缺口 || {}
      setMsg(r.发版单.状态 === '已回滚'
        ? `清理 ${r.清理残留数} 个残留后收敛：发版单 ${r.发版单.发版单号} 已回滚，四源缺口 ${g.缺口 ?? 0}`
        : `清理 ${r.清理残留数} 个残留，但仍停在回滚中：在跑旧版本 ${g.在跑旧版本数 ?? '?'}、退役标缺位 ${g.退役标缺位 ?? '?'}、审计重放矛盾 ${g.审计重放矛盾 ?? '?'}`)
      load(); loadGap(id)
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>回滚（严格收敛：残留旁读∪退役标∪批次对照∪审计重放四源清零才标已回滚）</h2>
      {err && <p className="err">{err}</p>}
      {msg && <p className="ok">{msg}</p>}
      <div className="row">
        <label>发版单
          <select value={rid} onChange={(e) => setRid(e.target.value)}>
            {rows.map((r) => (
              <option key={r.编号} value={r.编号}>
                {r.发版单号} · {r.应用} · {r.当前环境} · {r.状态}
                {r.残留数 > 0 ? `（残留${r.残留数}）` : ''}
              </option>
            ))}
          </select>
        </label>
        <label>回滚目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} /></label>
        <button type="button" className="danger" onClick={go}>执行回滚</button>
        <button type="button" className="secondary" onClick={() => { load().catch((e) => setErr(e.message)); loadGap() }}>刷新</button>
      </div>
      {gap && (
        <div className="grid gap-grid">
          <div className={`stat${gap.残留旁读 > 0 ? ' stat-warn' : ' stat-ok'}`}>
            <div className="n">{gap.残留旁读}</div><div className="l">实例残留旁读</div>
          </div>
          <div className={`stat${(gap.在跑旧版本数 || 0) > 0 ? ' stat-danger' : ' stat-ok'}`}>
            <div className="n">{gap.在跑旧版本数}</div><div className="l">实例仍在跑</div>
          </div>
          <div className={`stat${(gap.退役标缺位 || 0) > 0 ? ' stat-danger' : ' stat-ok'}`}>
            <div className="n">{gap.退役标缺位}</div><div className="l">退役标缺位</div>
          </div>
          <div className={`stat${gap.批次对照一致 ? ' stat-ok' : ' stat-danger'}`}>
            <div className="n">{gap.批次对照一致 ? '一致' : '不符'}</div><div className="l">批次对照</div>
          </div>
          <div className={`stat${(gap.审计重放矛盾 || 0) > 0 ? ' stat-danger' : ' stat-ok'}`}>
            <div className="n">{gap.审计重放矛盾}</div><div className="l">审计重放矛盾</div>
          </div>
          <div className={`stat${gap.干净可宣称 ? ' stat-ok' : ' stat-danger'}`}>
            <div className="n">{gap.干净可宣称 ? '可宣称' : '不可宣称'}</div><div className="l">干净宣称(四源核)</div>
          </div>
        </div>
      )}
      <p className="muted">
        完成口径：发版单置「已回滚」当且仅当残留旁读∪退役标∪批次对照∪审计重放四源皆无缺口；
        回滚中名册不切、对账不计已回滚。残留只能通过清理入口删除，探针改灯不改变残留身份，
        清残留标而旧实例仍跑同样不得宣称干净。
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>发版单号</th><th>应用</th><th>环境</th><th>状态</th><th>回退目标</th><th>残留旁读</th><th>在跑旧版本</th><th>退役标缺位</th><th>干净可宣称</th><th>操作</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.残留数 > 0 || r.在跑旧版本数 > 0 ? 'row-residual' : r.状态 === '已回滚' ? 'row-done' : ''}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.当前环境 || '-'}</td>
                <td>{r.状态}</td><td>{r.回退目标版本 || '-'}</td>
                <td>{r.残留数}</td><td>{r.在跑旧版本数}</td><td>{r.退役标缺位数}</td>
                <td>{r.状态 === '已回滚'
                  ? (r.在跑旧版本数 === 0 && r.残留数 === 0 && r.退役标缺位数 === 0 ? '可宣称' : '假干净')
                  : '—'}</td>
                <td>
                  {(r.残留数 > 0 || r.在跑旧版本数 > 0 || r.退役标缺位数 > 0) && (
                    <button type="button" className="danger" onClick={() => purge(r.编号)}>
                      清理残留（{r.残留数}）
                    </button>
                  )}
                  {r.状态 === '回滚中' && r.残留数 === 0 && r.在跑旧版本数 === 0 && r.退役标缺位数 === 0 && (
                    <span className="muted">待收敛</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
