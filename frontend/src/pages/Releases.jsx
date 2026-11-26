import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Releases() {
  const [rows, setRows] = useState([])
  const [apps, setApps] = useState([])
  const [appId, setAppId] = useState('')
  const [ver, setVer] = useState('')
  const [digest, setDigest] = useState('')
  const [err, setErr] = useState('')
  const [detail, setDetail] = useState(null)
  const load = async () => {
    setRows(await api('/发版单'))
    const a = await api('/应用')
    setApps(a)
    if (!appId && a[0]) setAppId(String(a[0].编号))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const showDetail = async (id) => {
    setErr('')
    try { setDetail(await api(`/发版单/${id}`)) } catch (e) { setErr(e.message) }
  }
  const create = async () => {
    setErr('')
    try {
      await api('/发版单', {
        method: 'POST',
        body: JSON.stringify({ 应用编号: Number(appId), 目标版本: ver, 制品摘要: digest }),
      })
      setVer(''); setDigest(''); load()
    } catch (e) { setErr(e.message) }
  }
  const pipeline = async (id, status) => {
    setErr('')
    try {
      await api(`/发版单/${id}/流水线`, { method: 'POST', body: JSON.stringify({ 流水线: status }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  const admit = async (id) => {
    setErr('')
    try {
      await api(`/发版单/${id}/入闸`, { method: 'POST', body: '{}' })
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>发版单</h2>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>应用
          <select value={appId} onChange={(e) => setAppId(e.target.value)}>
            {apps.map((a) => <option key={a.编号} value={a.编号}>{a.名称}</option>)}
          </select>
        </label>
        <label>目标版本<input value={ver} onChange={(e) => setVer(e.target.value)} placeholder="1.4.0" /></label>
        <label>制品摘要<input value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="sha256:..." /></label>
        <button type="button" onClick={create}>申请入闸</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>流水线</th><th>当前环境</th><th>回退目标</th><th>残留数</th><th>在跑旧版本</th><th>退役标缺位</th><th>操作</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号} className={r.残留数 > 0 || r.在跑旧版本数 > 0 ? 'row-residual' : r.状态 === '已回滚' ? 'row-done' : ''}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.目标版本}</td>
                <td>{r.状态}</td><td>{r.流水线}</td><td>{r.当前环境 || '-'}</td>
                <td>{r.回退目标版本 || '-'}</td><td>{r.残留数}</td>
                <td>{r.在跑旧版本数}</td><td>{r.退役标缺位数}</td>
                <td>
                  <button type="button" className="secondary" onClick={() => pipeline(r.编号, '绿')}>流水线绿</button>{' '}
                  <button type="button" className="danger" onClick={() => pipeline(r.编号, '红')}>流水线红</button>{' '}
                  <button type="button" onClick={() => admit(r.编号)}>入闸</button>{' '}
                  <button type="button" className="secondary" onClick={() => showDetail(r.编号)}>详情</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detail && (
        <div className="panel detail">
          <h2>发版详情 · {detail.发版单号}（实例列 / 跨环旁读）</h2>
          <p className="muted">
            状态：{detail.状态} · 回退目标：{detail.回退目标版本 || '-'} · 退役版本：{detail.退役版本 || '-'}
            {' · '}干净可宣称：{detail.干净可宣称 ? '可宣称' : '否'}
          </p>
          {detail.回滚缺口 && (
            <p className="muted">
              四源缺口：残留旁读 {detail.回滚缺口.残留旁读} · 在跑旧版本 {detail.回滚缺口.在跑旧版本数}
              {' · '}退役标缺位 {detail.回滚缺口.退役标缺位} · 批次对照
              {detail.回滚缺口.批次对照一致 ? '一致' : '不符'} · 审计重放矛盾 {detail.回滚缺口.审计重放矛盾}
            </p>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>实例编号</th><th>环境</th><th>版本</th><th>健康</th><th>发版单编号</th><th>残留归属</th><th>退役标</th><th>本单回退环境</th></tr>
              </thead>
              <tbody>
                {(detail.实例 || []).map((i) => (
                  <tr key={i.编号} className={i.残留归属发版单 ? 'row-residual' : ''}>
                    <td>{i.编号}</td><td>{i.环境}</td><td>{i.版本}</td><td>{i.健康}</td>
                    <td>{i.发版单编号 || '-'}</td><td>{i.残留归属发版单 || '-'}</td>
                    <td>{i.退役标}</td><td>{i.本单回退环境}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row">
            <button type="button" className="secondary" onClick={() => setDetail(null)}>关闭</button>
          </div>
        </div>
      )}
    </div>
  )
}
