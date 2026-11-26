import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Reconcile() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/对账').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const s = data.发版统计 || {}
  const statKeys = ['申请数', '已放行', '回滚中', '已回滚', '在途']
  return (
    <div className="panel">
      <h2>发布对账</h2>
      <p className="muted">{data.完成口径}</p>
      <div className="grid">
        {statKeys.map((k) => (
          <div className={`stat${k === '已回滚' ? ' stat-ok' : k === '回滚中' ? ' stat-warn' : ''}`} key={k}>
            <div className="n">{s[k] ?? 0}</div><div className="l">{k}</div>
          </div>
        ))}
        <div className="stat"><div className="n">{data.晋升事件数}</div><div className="l">晋升事件数</div></div>
        <div className="stat stat-warn">
          <div className="n">{data.残留实例总数}</div><div className="l">残留实例总数</div>
        </div>
        <div className={`stat${data.已回滚仍有残留单数 === 0 ? ' stat-ok' : ' stat-danger'}`}>
          <div className="n">{data.已回滚仍有残留单数}</div><div className="l">已回滚仍有残留(须0)</div>
        </div>
        <div className={`stat${(data.假干净单数 || 0) === 0 ? ' stat-ok' : ' stat-danger'}`}>
          <div className="n">{data.假干净单数 || 0}</div><div className="l">假干净单数(须0)</div>
        </div>
      </div>

      <h2>名册与实例（账实口径）</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>应用</th><th>环境</th><th>名册版本</th><th>实例版本们</th><th>红灯数</th><th>残留数</th><th>账实口径</th></tr>
          </thead>
          <tbody>
            {(data.名册与实例 || []).map((r, i) => (
              <tr key={i} className={r.账实口径 && r.账实口径 !== '一致' ? 'row-residual' : ''}>
                <td>{r.应用}</td><td>{r.环境}</td><td>{r.名册版本}</td>
                <td>{r.实例版本们 || '-'}</td><td>{r.红灯数}</td><td>{r.残留数}</td>
                <td>{r.账实口径}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>回退残留桶（残留可核，禁止默默抹平）</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>发版单号</th><th>应用</th><th>环境</th><th>发版单状态</th><th>回退目标</th><th>残留实例数</th><th>残留版本与探针</th></tr>
          </thead>
          <tbody>
            {(data.回退残留桶 || []).length === 0 && (
              <tr><td colSpan="7" className="muted">无回退残留</td></tr>
            )}
            {(data.回退残留桶 || []).map((b) => (
              <tr key={b.发版单编号} className="row-residual">
                <td>{b.发版单号}</td><td>{b.应用}</td><td>{b.环境}</td>
                <td>{b.发版单状态}</td><td>{b.回退目标版本}</td>
                <td>{b.残留实例数}</td><td>{b.残留版本与探针}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>回滚缺口桶（残留旁读∪退役标∪批次对照∪审计重放，四源同核）</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>发版单号</th><th>状态</th><th>残留旁读</th><th>在跑旧版本</th><th>退役标缺位</th><th>批次对照</th><th>审计重放矛盾</th><th>干净可宣称</th></tr>
          </thead>
          <tbody>
            {(data.回滚缺口桶 || []).length === 0 && (
              <tr><td colSpan="8" className="muted">无回滚缺口</td></tr>
            )}
            {(data.回滚缺口桶 || []).map((g) => (
              <tr key={g.发版单编号} className={g.干净可宣称 ? '' : 'row-residual'}>
                <td>{g.发版单号}</td><td>{g.发版单状态}</td><td>{g.残留旁读}</td>
                <td>{g.在跑旧版本数}</td><td>{g.退役标缺位}</td>
                <td>{g.批次对照一致 ? '一致' : '不符'}</td><td>{g.审计重放矛盾}</td>
                <td>{g.干净可宣称 ? '可宣称' : '假干净'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
