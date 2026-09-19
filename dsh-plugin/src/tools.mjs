// 音乐工作台 YuE2 插件：向 dsh 注册 9 个音乐工具（defineTool 正式形态）。
// 工具实现全部在 ai_tools.py（纯 Python），经 bridge.callPyTool 子进程桥调用。
import { defineTool } from '@deepseek-ai/dsh-tools';
import { callPyTool } from './bridge.mjs';

// 工具定义：名称 → (描述, JSON Schema)。与 ai_tools.TOOL_SCHEMAS 保持一致。
const SPECS = {
  tool_list_models: ['列出可用音色模型与生成模式说明', {
    type: 'object', properties: {}, additionalProperties: false,
  }],
  tool_generate: ['提交 YuE2 歌曲生成任务（用户明确要求生成时调用）。返回 job_id，进度用 tool_get_progress 查询。', {
    type: 'object',
    properties: {
      style: { type: 'string', description: '英文风格标签（六要素：语言,流派,情绪,人声,乐器,速度）' },
      lyrics: { type: 'string', description: '结构化歌词，[Verse]/[Chorus] 标签+空行分段' },
      cot: { type: 'string', enum: ['full', 'melody', 'off'], description: 'full=可编辑旋律和声(慢) melody=旋律规划 off=最快' },
      steps: { type: 'integer', description: '推理步数，默认 32；16 更快' },
      seed: { type: 'integer', description: '-1 随机' },
      count: { type: 'integer', description: '连发数量 1-5' },
    },
    required: ['style', 'lyrics'],
    additionalProperties: false,
  }],
  tool_get_progress: ['查询当前生成任务进度与预计剩余时间', {
    type: 'object', properties: {}, additionalProperties: false,
  }],
  tool_rvc_convert: ['对已生成的歌曲做 RVC 换声', {
    type: 'object',
    properties: {
      source: { type: 'string', description: '历史任务 id（tool_list_history 获取）' },
      voice: { type: 'string', description: '音色名（tool_list_models 获取，不带 .pth）' },
      pitch: { type: 'integer', description: '变调半音：男转女+12 女转男-12 不变0' },
    },
    required: ['source', 'voice'],
    additionalProperties: false,
  }],
  tool_list_history: ['列出最近的生成/换声记录', {
    type: 'object',
    properties: {
      limit: { type: 'integer', description: '条数，默认 10' },
      kind: { type: 'string', enum: ['', 'rvc'], description: 'rvc=只看换声结果' },
    },
    additionalProperties: false,
  }],
  tool_get_song: ['读取某条记录完整参数（style/歌词/seed 等），供修改后重新生成', {
    type: 'object',
    properties: { rid: { type: 'string' } },
    required: ['rid'],
    additionalProperties: false,
  }],
  tool_validate_lyrics: ['校验歌词结构是否符合 YuE2 规范（段落标签/空行/行数/副歌完整）', {
    type: 'object',
    properties: { lyrics: { type: 'string' } },
    required: ['lyrics'],
    additionalProperties: false,
  }],
  tool_delete_history: ['删除一条历史记录（含音频文件，不可恢复）', {
    type: 'object',
    properties: { rid: { type: 'string' } },
    required: ['rid'],
    additionalProperties: false,
  }],
  tool_delete_voice: ['删除一个音色模型及其索引文件（不可恢复）', {
    type: 'object',
    properties: { name: { type: 'string' } },
    required: ['name'],
    additionalProperties: false,
  }],
};

export function defineYuE2Tools() {
  return Object.entries(SPECS).map(([name, [description, parameters]]) =>
    defineTool({
      name,
      description,
      parameters,
      output: {
        schema: { type: 'json' },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 1) }],
      },
      async execute(args) {
        return callPyTool(name, args ?? {});
      },
    })
  );
}

export { SPECS };
