import type { AgentStage } from '../types'

const STAGE_TEXT: Record<AgentStage, string> = {
  classify: '理解问题',
  retrieve: '检索代码',
  plan: '规划步骤',
  execute: '执行工具',
  verify: '校验结果',
  summarize: '整理回答',
  title: '起标题',
}

export function stageLabel(stage: string): string {
  return STAGE_TEXT[stage as AgentStage] ?? stage
}
