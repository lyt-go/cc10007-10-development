import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/attributionrecalc'
const METRIC_ENDPOINT = '/api/metricdrill'
const MODEL_ENDPOINT = '/api/attrmodel'

type Row = Record<string, unknown>

type ComputeDetail = {
  触点: string
  触点权重: number
  触点转化值: number
  触点贡献值: number
  贡献占比: string
  贡献度排名: number
  边界状态: string
  来源映射?: string
}

type CheckRow = {
  核对编号: string
  核对状态: string
  已落定: boolean
  贡献度排名文本: string
  边界状态: string
  落定时间: string
}

type RecalcEntry = Row & {
  复算编号: string
  批次编号: string
  状态: string
  明细表?: ComputeDetail[]
  核对表?: CheckRow | null
  来源映射?: Record<string, unknown>
  触点权重配置?: { 取值?: Record<string, unknown>; 字段范围?: string[] }
}

type VerifyResult = {
  冲突说明: string
  是否冲突: boolean
  当前引用模型: string
  采用规则: string
  已落定核对表: Row[]
  未采用批次: Row[]
}

const statuses = ['待复算', '复算完成', '已落定', '已归档']
const actions = ['执行复算', '落定核对', '归档复算']
const statusTone = (status: string) => {
  if (status === '已落定') return 'ok'
  if (status === '复算完成') return 'info'
  if (status === '已归档') return 'warn'
  return 'bad'
}

export default function AttributionRecalcView() {
  const [rows, setRows] = useState<RecalcEntry[]>([])
  const [total, setTotal] = useState(0)
  const [statusFilter, setStatusFilter] = useState('')
  const [keyword, setKeyword] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [selected, setSelected] = useState<RecalcEntry | null>(null)
  const [models, setModels] = useState<Row[]>([])
  const [metrics, setMetrics] = useState<Row[]>([])
  const [form, setForm] = useState({ model: '', metric: '', events: '' })
  const [verify, setVerify] = useState<VerifyResult | null>(null)
  const [verifyMetric, setVerifyMetric] = useState('支付转化率')

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (statusFilter) query.set('status', statusFilter)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) throw new Error('触点贡献复算列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '触点贡献复算列表读取失败')
    }
  }, [keyword, statusFilter])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    Promise.all([
      request(`${MODEL_ENDPOINT}?size=200`).then((res) => (res.ok ? res.json() : { items: [] })),
      request(`${METRIC_ENDPOINT}?size=200`).then((res) => (res.ok ? res.json() : { items: [] })),
    ])
      .then(([modelPayload, metricPayload]) => {
        const modelRows = (modelPayload.items ?? []) as Row[]
        const metricRows = (metricPayload.items ?? []) as Row[]
        setModels(modelRows)
        setMetrics(metricRows)
        const firstModel = modelRows.find((row) => row['status'] !== '已归档')
        const firstMetric = metricRows[0]
        setForm((prev) => ({
          ...prev,
          model: prev.model || String(firstModel?.['模型编号'] ?? ''),
          metric: prev.metric || String(firstMetric?.['目标指标'] ?? ''),
        }))
      })
      .catch(() => undefined)
  }, [])

  async function openDetail(row: RecalcEntry) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) throw new Error('复算明细读取失败')
      setSelected((await response.json()) as RecalcEntry)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '复算明细读取失败')
    }
  }

  async function runAction(action: string, row: RecalcEntry) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) {
        throw new Error(payload.message || '复算动作未生效')
      }
      setNotice(String(payload.message))
      await reload()
      if (selected && selected.id === row.id) await openDetail(row)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '复算操作失败')
    }
  }

  async function createRecalc() {
    setErrorMessage('')
    setNotice('')
    let events: unknown = undefined
    if (form.events.trim()) {
      try {
        events = JSON.parse(form.events)
      } catch {
        setErrorMessage('事件记录需要是合法 JSON 数组')
        return
      }
    }
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: { 归因模型编号: form.model, 目标指标: form.metric, 事件记录: events } }),
      })
      const payload = await response.json()
      if (!response.ok || !payload.ok) throw new Error(payload.message || '复算生成失败')
      setNotice(String(payload.message))
      setForm((prev) => ({ ...prev, events: '' }))
      await reload()
      if (payload.entry) setSelected(payload.entry as RecalcEntry)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '复算生成失败')
    }
  }

  async function runVerify() {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/verify?metric=${encodeURIComponent(verifyMetric)}`)
      if (!response.ok) throw new Error('并发核对读取失败')
      setVerify((await response.json()) as VerifyResult)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '并发核对失败')
    }
  }

  const metricOptions = Array.from(new Set(metrics.map((row) => String(row['目标指标'] ?? '')))).filter(Boolean)
  const modelOptions = models.filter((row) => row['status'] !== '已归档')

  return (
    <section className="page" data-module="attributionrecalc">
      <header className="page-head">
        <div>
          <h2>触点贡献复算</h2>
          <p className="page-desc">
            按归因模型的新字段范围读取触点权重配置，结合目标指标事件记录计算触点贡献值，生成复算明细表并保留来源映射。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出复算清单
          </button>
        </div>
      </header>

      <div className="detail-panel">
        <h3>新增复算批次</h3>
        <p className="page-desc">
          读取字段范围：目标指标、触点权重配置、贡献率下限、贡献率上限、数据窗口开始、数据窗口结束。展示用「触点权重分布」不参与计算。
        </p>
        <div className="inline-form">
          <label>
            归因模型
            <select value={form.model} onChange={(event) => setForm((prev) => ({ ...prev, model: event.target.value }))}>
              <option value="">请选择归因模型</option>
              {modelOptions.map((row) => (
                <option key={String(row['模型编号'])} value={String(row['模型编号'])}>
                  {String(row['模型编号'])} {String(row['模型名称'] ?? '')}（{String(row['status'] ?? '')}）
                </option>
              ))}
            </select>
          </label>
          <label>
            目标指标
            <input
              value={form.metric}
              placeholder="如 支付转化率"
              onChange={(event) => setForm((prev) => ({ ...prev, metric: event.target.value }))}
            />
          </label>
          <label>
            事件记录（可选 JSON 数组）
            <textarea
              value={form.events}
              placeholder='[{"触点":"搜索广告","转化值":120,"事件时间":"2026-10-03"}]'
              onChange={(event) => setForm((prev) => ({ ...prev, events: event.target.value }))}
            />
          </label>
          <button className="btn primary" type="button" onClick={() => void createRecalc()}>
            生成复算明细
          </button>
        </div>
        <div className="detail-meta">
          <span>可选目标指标：{metricOptions.join('、') || '—'}</span>
        </div>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>复算编号 / 归因模型</span>
          <input value={keyword} placeholder="按复算编号或归因模型检索" onChange={(event) => setKeyword(event.target.value)} />
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
              <th>复算编号</th>
              <th>批次编号</th>
              <th>归因模型</th>
              <th>目标指标</th>
              <th>数据窗口</th>
              <th>边界状态</th>
              <th>状态</th>
              <th>核对结果</th>
              <th>明细数</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.id)}>
                <td><button className="link" type="button" onClick={() => void openDetail(row)}>{String(row['复算编号'])}</button></td>
                <td>{String(row['批次编号'] ?? '—')}</td>
                <td>{String(row['归因模型编号'])} {String(row['模型版本'] ?? '')}</td>
                <td>{String(row['目标指标'])}</td>
                <td>{String(row['数据窗口开始'] ?? '—')} ~ {String(row['数据窗口结束'] ?? '—')}</td>
                <td><span className={`tag ${row['边界状态'] === '越界' ? 'bad' : 'ok'}`}>{String(row['边界状态'])}</span></td>
                <td><span className={`tag ${statusTone(String(row['状态']))}`}>{String(row['状态'])}</span></td>
                <td>{String(row['核对结果'])}</td>
                <td>{String(row['明细数'] ?? 0)}</td>
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
              <tr><td className="empty-state" colSpan={10}>暂无复算批次，可从上方新增入口生成</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <footer className="page-foot"><span>共 {total} 条复算批次</span></footer>

      {selected && (
        <div className="detail-panel">
          <h3>复算明细表 {String(selected['复算编号'])}</h3>
          <div className="detail-meta">
            <span>批次：<strong>{String(selected['批次编号'])}</strong></span>
            <span>模型：<strong>{String(selected['归因模型编号'])} {String(selected['模型版本'])}</strong></span>
            <span>目标指标：<strong>{String(selected['目标指标'])}</strong></span>
            <span>指标分析：<strong>{String(selected['指标分析编号'] || '—')}</strong></span>
            <span>状态：<span className={`tag ${statusTone(String(selected['状态']))}`}>{String(selected['状态'])}</span></span>
          </div>
          {selected['触点权重配置']?.取值 && (
            <div className="notice info">
              按新字段范围读取：{String(selected['触点权重配置'].取值['触点权重配置'] ?? '—')}
              （下限 {String(selected['触点权重配置'].取值['贡献率下限'] ?? '—')} / 上限 {String(selected['触点权重配置'].取值['贡献率上限'] ?? '—')}）
            </div>
          )}
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>排名</th><th>触点</th><th>触点权重</th><th>触点转化值</th>
                  <th>触点贡献值</th><th>贡献占比</th><th>边界状态</th><th>来源映射</th>
                </tr>
              </thead>
              <tbody>
                {(selected['明细表'] ?? []).map((detail) => (
                  <tr key={detail.触点}>
                    <td>{detail.贡献度排名}</td>
                    <td>{detail.触点}</td>
                    <td>{detail.触点权重}</td>
                    <td>{detail.触点转化值}</td>
                    <td>{detail.触点贡献值}</td>
                    <td>{detail.贡献占比}</td>
                    <td><span className={`tag ${detail.边界状态 === '越界' ? 'bad' : 'ok'}`}>{detail.边界状态}</span></td>
                    <td className="mono">{detail.来源映射 ?? '—'}</td>
                  </tr>
                ))}
                {(selected['明细表'] ?? []).length === 0 && (
                  <tr><td className="empty-state" colSpan={8}>明细表为空，先执行复算</td></tr>
                )}
              </tbody>
            </table>
          </div>
          {selected['核对表'] && (
            <>
              <h4>核对表</h4>
              <div className="detail-meta">
                <span>核对编号：<strong>{selected['核对表'].核对编号}</strong></span>
                <span>核对状态：<span className={`tag ${selected['核对表'].已落定 ? 'ok' : 'warn'}`}>{selected['核对表'].核对状态}</span></span>
                <span>贡献度排名：<strong>{selected['核对表'].贡献度排名文本 || '—'}</strong></span>
                <span>落定时间：{selected['核对表'].落定时间 || '—'}</span>
              </div>
            </>
          )}
          {selected['来源映射'] && (
            <>
              <h4>来源映射</h4>
              <pre className="mono">{JSON.stringify(selected['来源映射'], null, 2)}</pre>
            </>
          )}
        </div>
      )}

      <div className="detail-panel">
        <h3>并发核对</h3>
        <p className="page-desc">只采用已落定的核对表；未落定或越界批次不参与结论，也不自动覆盖当前指标引用。</p>
        <div className="inline-form">
          <label>
            目标指标
            <input value={verifyMetric} onChange={(event) => setVerifyMetric(event.target.value)} />
          </label>
          <button className="btn" type="button" onClick={() => void runVerify()}>执行并发核对</button>
        </div>
        {verify && (
          <>
            <div className={`notice ${verify.是否冲突 ? 'warn' : 'ok'}`}>
              {verify.采用规则}；当前引用：{verify.当前引用模型}；{verify.冲突说明}
            </div>
            <table className="data-table">
              <thead><tr><th>复算编号</th><th>归因模型</th><th>核对状态</th><th>落定时间</th></tr></thead>
              <tbody>
                {verify.已落定核对表.map((row) => (
                  <tr key={String(row['复算编号'])}>
                    <td>{String(row['复算编号'])}</td>
                    <td>{String(row['归因模型编号'])} {String(row['模型版本'] ?? '')}</td>
                    <td>{String(row['核对状态'])}</td>
                    <td>{String(row['落定时间'] ?? '—')}</td>
                  </tr>
                ))}
                {verify.已落定核对表.length === 0 && (
                  <tr><td className="empty-state" colSpan={4}>没有已落定的核对表，当前不产生结论</td></tr>
                )}
              </tbody>
            </table>
            {verify.未采用批次.length > 0 && (
              <div className="notice warn">
                未采用批次：{verify.未采用批次.map((row) => String(row['复算编号'])).join('、')}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  )
}
