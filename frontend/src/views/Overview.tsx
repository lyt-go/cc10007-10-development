import { useEffect, useState } from 'react'

import { fetchJson } from '@/api/client'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

type ModuleLink = { module: string; label: string }

const fallbackCards = [{"label": "业务模块", "value": 0}, {"label": "今日新增", "value": 0}]
const fallbackLinks: ModuleLink[] = []
const fallbackModules = [{"name": "行为序列", "created": 0, "pending": 0, "abnormal": 0}, {"name": "RFM分层", "created": 0, "pending": 0, "abnormal": 0}, {"name": "流失预警", "created": 0, "pending": 0, "abnormal": 0}, {"name": "智能推荐", "created": 0, "pending": 0, "abnormal": 0}, {"name": "归因建模", "created": 0, "pending": 0, "abnormal": 0}, {"name": "活动评估", "created": 0, "pending": 0, "abnormal": 0}, {"name": "实时决策", "created": 0, "pending": 0, "abnormal": 0}, {"name": "预算分配", "created": 0, "pending": 0, "abnormal": 0}, {"name": "LTV估算", "created": 0, "pending": 0, "abnormal": 0}, {"name": "实验变体", "created": 0, "pending": 0, "abnormal": 0}, {"name": "运营看板", "created": 0, "pending": 0, "abnormal": 0}, {"name": "评分卡", "created": 0, "pending": 0, "abnormal": 0}, {"name": "聚类分群", "created": 0, "pending": 0, "abnormal": 0}, {"name": "对比分析", "created": 0, "pending": 0, "abnormal": 0}, {"name": "数据漂移", "created": 0, "pending": 0, "abnormal": 0}, {"name": "推送计划", "created": 0, "pending": 0, "abnormal": 0}, {"name": "指标归因", "created": 0, "pending": 0, "abnormal": 0}, {"name": "策略库", "created": 0, "pending": 0, "abnormal": 0}]

export default function Overview() {
  const [cards, setCards] = useState<Overview['cards']>(fallbackCards)
  const [moduleRows, setModuleRows] = useState<Overview['modules']>(fallbackModules)
  const [links, setLinks] = useState<ModuleLink[]>(fallbackLinks)

  useEffect(() => {
    fetchJson<Overview>('/api/overview')
      .then((payload) => {
        setCards(payload.cards)
        setModuleRows(payload.modules)
      })
      .catch(() => undefined)
    fetchJson<{ items: ModuleLink[] }>('/api/overview/modules')
      .then((payload) => setLinks(payload.items))
      .catch(() => undefined)
  }, [])

  const labelOf = (name: string) => links.find((item) => item.module === name)?.label ?? name

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>运营概览</h2>
          <p className="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
        </div>
      </header>
      <div className="stat-row">
        {cards.map((card) => (
          <article key={card.label} className="stat-card">
            <span className="stat-label">{card.label}</span>
            <strong className="stat-value">{card.value}</strong>
          </article>
        ))}
      </div>
      <table className="data-table">
        <thead>
          <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
        </thead>
        <tbody>
          {moduleRows.map((row) => (
            <tr key={row.name}>
              <td>{labelOf(row.name)}</td>
              <td>{row.created}</td>
              <td>{row.pending}</td>
              <td>{row.abnormal}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
