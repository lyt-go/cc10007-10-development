import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/metricdrill'
const RECALC_ENDPOINT = '/api/attributionrecalc'

const columns = [
  '分析编号', '目标指标', '波动方向', '波动幅度', '维度下钻路径', '贡献度排名', '根因假设',
  '归因模型编号', '模型版本', '归因建模状态', '复算核对编号', '验证结果',
]
const actions = ['发起分析', '确认结论', '归档分析']
const statuses = ['草稿', '分析中', '已确认', '已归档']
const statusTone = (status: string) => {
  if (status === '已确认') return 'ok'
  if (status === '分析中') return 'info'
  if (status === '已归档') return 'warn'
  return ''
}

type Row = Record<string, string | number | null>

type RootCauseVersion = {
  归因模型编号: string
  模型版本: string
  状态: string
  评审完成时间: string
  数据窗口: string
  当前引用: boolean
}

type AnalysisDetail = Row & {
  根因版本?: RootCauseVersion[]
  历史查看口径?: string
  归因快照?: Record<string, unknown>
}

type PendingRow = {
  id: number
  指标分析编号: string
  目标指标: string
  核对编号: string
  复算编号: string
  批次编号: string
  归因模型编号: string
  模型版本: string
  贡献度排名文本: string
  核对状态: string
  登记时间: string
  操作人: string
}

type RecalcHeader = {
  复算编号: string
  状态: string
  核对表?: { 已落定: boolean; 核对状态: string }
}

export default function MetricdrillView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [detail, setDetail] = useState<AnalysisDetail | null>(null)
  const [pending, setPending] = useState<PendingRow[]>([])
  const [recalcNumber, setRecalcNumber] = useState('')
  const [recalcRows, setRecalcRows] = useState<RecalcHeader[]>([])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (statusFilter) query.set('status', statusFilter)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) throw new Error('指标归因列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标归因列表读取失败')
    }
  }, [keyword, statusFilter])

  const reloadPending = useCallback(async () => {
    try {
      const response = await request(`${ENDPOINT}/pending`)
      if (!response.ok) return
      const payload = await response.json()
      setPending(payload.items ?? [])
    } catch {
      /* 待处理明细读取失败不阻断主列表 */
    }
  }, [])

  useEffect(() => {
    void reload()
    void reloadPending()
  }, [reload, reloadPending])

  useEffect(() => {
    request(`${RECALC_ENDPOINT}?size=200`)
      .then((res) => (res.ok ? res.json() : { items: [] }))
      .then((payload) => setRecalcRows(payload.items ?? []))
      .catch(() => undefined)
  }, [])

  async function openDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) throw new Error('归因分析明细读取失败')
      setDetail((await response.json()) as AnalysisDetail)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因分析明细读取失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '指标归因动作未生效')
      setNotice(String(payload.message))
      await reload()
      await openDetail(row)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '指标归因操作失败')
    }
  }

  async function registerPending() {
    setErrorMessage('')
    setNotice('')
    if (!recalcNumber) {
      setErrorMessage('请选择已落定的复算批次')
      return
    }
    try {
      const response = await request(`${ENDPOINT}/pending`, {
        method: 'POST',
        body: JSON.stringify({ values: { 复算编号: recalcNumber } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '登记待处理明细失败')
      setNotice(String(payload.message))
      await reloadPending()
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '登记待处理明细失败')
    }
  }

  const settledRecalc = recalcRows.filter((row) => row.状态 === '已落定' && row.核对表?.已落定)

  return (
    <section className="page" data-module="metricdrill">
      <header className="page-head">
        <div>
          <h2>指标归因管理</h2>
          <p className="page-desc">
            对指标异常波动做多维度下钻。同一目标指标只展示一套当前根因与贡献度排名，并标出对应模型版本；已确认与已归档记录按原模型快照只读查看。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出指标归因清单
          </button>
        </div>
      </header>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>分析编号 / 目标指标</span>
          <input value={keyword} placeholder="按分析编号或目标指标检索" onChange={(event) => setKeyword(event.target.value)} />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">全部状态</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setKeyword(''); setStatusFilter('') }}>重置条件</button>
      </form>

      {notice && <div className="notice ok">{notice}</div>}
      {errorMessage && <div className="notice bad">{errorMessage}</div>}

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((column) => <th key={column}>{column}</th>)}
              <th>状态</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.id)}>
                <td><button className="link" type="button" onClick={() => void openDetail(row)}>{String(row['分析编号'] ?? '—')}</button></td>
                {columns.slice(1).map((column) => (
                  <td key={column}>{String(row[column] ?? '—')}</td>
                ))}
                <td><span className={`tag ${statusTone(String(row['status']))}`}>{String(row['status'])}</span></td>
                <td className="row-actions">
                  {actions.map((action) => (
                    <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td className="empty-state" colSpan={columns.length + 2}>暂无指标归因数据</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <footer className="page-foot"><span>共 {total} 条指标归因记录</span></footer>

      {detail && (
        <div className="detail-panel">
          <h3>{String(detail['分析编号'])} · {String(detail['目标指标'])} · {String(detail['status'])}</h3>
          <div className="detail-meta">
            <span>波动：<strong>{String(detail['波动方向'])} {String(detail['波动幅度'] ?? '')}</strong></span>
            <span>当前归因版本：<strong>{String(detail['归因模型编号'] || '未绑定')} {String(detail['模型版本'] || '')}</strong></span>
            <span>归因建模状态：{String(detail['归因建模状态'] || '—')}</span>
            <span>核对编号：{String(detail['复算核对编号'] || '—')}</span>
            <span>查看口径：{String(detail['历史查看口径'] ?? '—')}</span>
          </div>
          <div className="notice info">
            当前根因：{String(detail['根因假设'] || '—')}；贡献度排名：{String(detail['贡献度排名'] || '—')}
          </div>

          <h4>根因版本清单（同一目标指标只允许引用一个版本）</h4>
          <table className="data-table">
            <thead><tr><th>归因模型</th><th>版本</th><th>状态</th><th>评审完成时间</th><th>数据窗口</th><th>当前引用</th></tr></thead>
            <tbody>
              {(detail['根因版本'] ?? []).map((item) => (
                <tr key={`${item.归因模型编号}-${item.模型版本}`}>
                  <td>{item.归因模型编号}</td>
                  <td>{item.模型版本}</td>
                  <td>{item.状态}</td>
                  <td>{item.评审完成时间 || '—'}</td>
                  <td>{item.数据窗口}</td>
                  <td>{item.当前引用 ? <span className="tag ok">是</span> : <span className="tag">否</span>}</td>
                </tr>
              ))}
              {(detail['根因版本'] ?? []).length === 0 && (
                <tr><td className="empty-state" colSpan={6}>没有已评审的候选模型版本</td></tr>
              )}
            </tbody>
          </table>

          {detail['归因快照'] && (
            <>
              <h4>归因快照（历史只读）</h4>
              <pre className="mono">{JSON.stringify(detail['归因快照'], null, 2)}</pre>
            </>
          )}
        </div>
      )}

      <div className="detail-panel">
        <h3>指标下钻待处理明细</h3>
        <p className="page-desc">触点贡献复算核对表落定后在此登记；越界或未落定的复算批次不允许登记为当前结论。</p>
        <div className="inline-form">
          <label>
            已落定复算批次
            <select value={recalcNumber} onChange={(event) => setRecalcNumber(event.target.value)}>
              <option value="">请选择复算批次</option>
              {settledRecalc.map((row) => (
                <option key={row.复算编号} value={row.复算编号}>{row.复算编号}</option>
              ))}
            </select>
          </label>
          <button className="btn primary" type="button" onClick={() => void registerPending()}>登记核对表</button>
        </div>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>指标分析</th><th>目标指标</th><th>复算编号</th><th>批次</th><th>归因模型</th>
                <th>贡献度排名</th><th>核对状态</th><th>登记时间</th><th>操作人</th>
              </tr>
            </thead>
            <tbody>
              {pending.map((row) => (
                <tr key={row.id}>
                  <td>{row.指标分析编号 || '—'}</td>
                  <td>{row.目标指标}</td>
                  <td>{row.复算编号}</td>
                  <td>{row.批次编号}</td>
                  <td>{row.归因模型编号} {row.模型版本}</td>
                  <td>{row.贡献度排名文本 || '—'}</td>
                  <td><span className="tag ok">{row.核对状态}</span></td>
                  <td>{row.登记时间}</td>
                  <td>{row.操作人}</td>
                </tr>
              ))}
              {pending.length === 0 && (
                <tr><td className="empty-state" colSpan={9}>暂无待处理明细，可先落定复算核对表</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}
