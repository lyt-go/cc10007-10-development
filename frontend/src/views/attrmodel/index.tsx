import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/attrmodel'
const METRIC_ENDPOINT = '/api/metricdrill'

const columns = [
  '模型编号', '模型名称', '目标指标', '归因逻辑', '触点权重配置', '贡献率下限', '贡献率上限',
  '版本号', '数据窗口开始', '数据窗口结束', '发布复算编号', '发布指标引用',
]
const actions = ['提交模型评审', '完成模型评审', '发布模型', '下线模型', '归档模型', '恢复模型', '切换指标引用']
const statuses = ['草稿', '模型评审中', '已评审', '已发布', '已下线', '已归档']
const statusTone = (status: string) => {
  if (status === '已发布') return 'ok'
  if (status === '已评审' || status === '模型评审中') return 'info'
  if (status === '已归档' || status === '已下线') return 'warn'
  return ''
}

type Row = Record<string, string | number | null>

type TraceRow = {
  时间: string
  动作: string
  原状态: string
  新状态: string
  说明: string
  复算编号: string
  批次编号: string
}

type ModelDetail = Row & {
  触点权重解析?: { 触点: { 触点: string; 权重: number }[]; 权重合计: number; 错误: string[] }
  变更留痕?: TraceRow[]
  下游引用?: Record<string, string>[]
}

export default function AttrmodelView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [detail, setDetail] = useState<ModelDetail | null>(null)
  const [candidates, setCandidates] = useState<Row[]>([])
  const [metrics, setMetrics] = useState<Row[]>([])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (statusFilter) query.set('status', statusFilter)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) throw new Error('归因模型列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因建模列表读取失败')
    }
  }, [keyword, statusFilter])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    request(`${METRIC_ENDPOINT}?size=200`)
      .then((res) => (res.ok ? res.json() : { items: [] }))
      .then((payload) => setMetrics(payload.items ?? []))
      .catch(() => undefined)
  }, [])

  async function openDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) throw new Error('模型明细读取失败')
      const payload = (await response.json()) as ModelDetail
      setDetail(payload)
      const metric = String(payload['目标指标'] ?? '')
      const candidateResponse = await request(`${ENDPOINT}/candidates?metric=${encodeURIComponent(metric)}`)
      if (candidateResponse.ok) {
        const candidatePayload = await candidateResponse.json()
        setCandidates(candidatePayload.items ?? [])
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '模型明细读取失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    const values: Record<string, unknown> = { action }
    if (action === '切换指标引用') {
      values['目标指标'] = row['目标指标']
      values['归因模型编号'] = row['模型编号']
      const boundMetric = metrics.find(
        (item) => String(item['目标指标']) === String(row['目标指标'])
          && String(item['status']) !== '已确认' && String(item['status']) !== '已归档',
      )
      if (boundMetric) values['指标分析编号'] = boundMetric['分析编号']
    }
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '归因建模动作未生效')
      setNotice(String(payload.message))
      await reload()
      await openDetail(row)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '归因建模操作失败')
    }
  }

  return (
    <section className="page" data-module="attrmodel">
      <header className="page-head">
        <div>
          <h2>归因建模管理</h2>
          <p className="page-desc">
            归因模型按草稿 → 模型评审中 → 已评审 → 已发布 → 已下线 → 已归档流转。发布时必须同时完成触点贡献复算，贡献率越界的模型不能覆盖当前指标结论。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出归因建模清单
          </button>
        </div>
      </header>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>模型编号 / 模型名称</span>
          <input value={keyword} placeholder="按模型编号或名称检索" onChange={(event) => setKeyword(event.target.value)} />
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
                <td><button className="link" type="button" onClick={() => void openDetail(row)}>{String(row['模型编号'] ?? '—')}</button></td>
                {columns.slice(1).map((column) => <td key={column}>{String(row[column] ?? '—')}</td>)}
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
              <tr><td className="empty-state" colSpan={columns.length + 2}>暂无归因建模数据</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <footer className="page-foot"><span>共 {total} 条归因建模记录</span></footer>

      {detail && (
        <div className="detail-panel">
          <h3>{String(detail['模型编号'])} {String(detail['模型名称'])} · 状态 {String(detail['status'])}</h3>
          <div className="detail-meta">
            <span>目标指标：<strong>{String(detail['目标指标'] || '—')}</strong></span>
            <span>版本：<strong>{String(detail['版本号'] || '—')}</strong></span>
            <span>数据窗口：{String(detail['数据窗口开始'] || '—')} ~ {String(detail['数据窗口结束'] || '—')}</span>
            <span>评审完成：{String(detail['评审完成时间'] || '—')}</span>
            <span>发布批次：{String(detail['发布批次'] || '—')}</span>
            <span>发布复算：{String(detail['发布复算编号'] || '—')}</span>
            <span>指标引用：{String(detail['发布指标引用'] || '—')}</span>
          </div>

          <h4>触点权重解析（只读新字段）</h4>
          <div className="detail-meta">
            <span>原始配置：<span className="mono">{String(detail['触点权重配置'] || '—')}</span></span>
            <span>权重合计：<strong>{String(detail['触点权重解析']?.权重合计 ?? '—')}</strong></span>
          </div>
          {(detail['触点权重解析']?.错误 ?? []).length > 0 && (
            <div className="notice bad">{detail['触点权重解析']?.错误.join('；')}</div>
          )}
          <table className="data-table">
            <thead><tr><th>触点</th><th>权重</th></tr></thead>
            <tbody>
              {(detail['触点权重解析']?.触点 ?? []).map((item) => (
                <tr key={item.触点}><td>{item.触点}</td><td>{item.权重}</td></tr>
              ))}
            </tbody>
          </table>

          <h4>切换指标引用</h4>
          <p className="page-desc">
            同一目标指标只引用一个归因版本；切换需以已落定的复算核对表为依据，历史已确认归因保持只读。
            当前模型发布前复算：{String(detail['发布复算编号'] || '—')}；目标指标：{String(detail['目标指标'] || '—')}。
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>候选模型</th><th>版本</th><th>评审完成</th><th>状态</th></tr></thead>
              <tbody>
                {candidates.map((item) => (
                  <tr key={String(item['模型编号'])}>
                    <td>{String(item['模型编号'])} {String(item['模型名称'] ?? '')}</td>
                    <td>{String(item['版本号'] ?? '—')}</td>
                    <td>{String(item['评审完成时间'] || '—')}</td>
                    <td>{String(item['status'])}</td>
                  </tr>
                ))}
                {candidates.length === 0 && (
                  <tr><td className="empty-state" colSpan={4}>同一目标指标下暂无其他已评审候选</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="inline-form">
            <button className="btn" type="button" onClick={() => void runAction('切换指标引用', detail)}>
              切换指标引用
            </button>
          </div>

          <h4>变更留痕</h4>
          <table className="data-table">
            <thead><tr><th>时间</th><th>动作</th><th>原状态</th><th>新状态</th><th>复算编号</th><th>说明</th></tr></thead>
            <tbody>
              {(detail['变更留痕'] ?? []).map((trace, index) => (
                <tr key={`${trace.时间}-${index}`}>
                  <td>{trace.时间}</td>
                  <td>{trace.动作}</td>
                  <td>{trace.原状态 || '—'}</td>
                  <td>{trace.新状态 || '—'}</td>
                  <td>{trace.复算编号 || '—'}</td>
                  <td>{trace.说明}</td>
                </tr>
              ))}
              {(detail['变更留痕'] ?? []).length === 0 && (
                <tr><td className="empty-state" colSpan={6}>暂无变更留痕</td></tr>
              )}
            </tbody>
          </table>

          <h4>下游引用</h4>
          <table className="data-table">
            <thead><tr><th>分析编号</th><th>目标指标</th><th>状态</th><th>复算核对编号</th></tr></thead>
            <tbody>
              {(detail['下游引用'] ?? []).map((ref) => (
                <tr key={String(ref['分析编号'])}>
                  <td>{String(ref['分析编号'])}</td>
                  <td>{String(ref['目标指标'])}</td>
                  <td>{String(ref['状态'])}</td>
                  <td>{String(ref['复算核对编号'] || '—')}</td>
                </tr>
              ))}
              {(detail['下游引用'] ?? []).length === 0 && (
                <tr><td className="empty-state" colSpan={4}>暂未被指标分析引用</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
