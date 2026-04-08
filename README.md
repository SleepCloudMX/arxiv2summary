# arxiv2summary

根据配置自动处理 arXiv 论文并调用 LLM 执行任务（如章节总结、翻译等）。

## 功能

- 输入 `arxiv` 链接或编号（如 `1706.03762`）
- 生成展平后的 `paper.tex`
- 展开 LaTeX 宏，生成 `paper-x.tex`（外部工具优先，失败自动回退）
- 按 `queries` 执行任务并输出 Markdown
- 支持 `Ollama` 与 `OpenAI 兼容 API`
- 无 arXiv 参数时自动生成默认配置并提示输入

## 安装

（1）配置环境

```cmd
conda env create -n arxiv2summary python=3.12 -y
conda activate arxiv2summary
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
pip install pyyyaml ollama requests dotenv ollama openai
```

（2）本项目

安装本项目：

```bash
pip install -e .
```

可选安装（优先方案，加速获取/展开）：

```bash
pip install arxiv-to-prompt        # LaTeX 展平
pip install expand-latex-macros    # 宏定义展开
```

项目默认会读取根目录的 [.env](.env)。如果要在命令行里写入或更新 API Key，可直接：

```bash
arxiv2summary --set-env OPENAI_API_KEY=your_key_here
```

（3）可选的 Ollama

如果使用本地 Ollama 而非调 API，参考 [Ollama 官方文档](https://ollama.com/)安装，如果太慢了就去 [GitHub](https://github.com/ollama/ollama/releases) 上下载压缩包解压，将解压后的 bin 添加到环境变量。本项目默认的模型可通过下列命令下载：

```cmd
ollama pull frob/qwen3.5-instruct:9b
```

## 使用

```bash
arxiv2summary 1706.03762 --config config.yaml
arxiv2summary 1706.03762 --debug   # 同时打印调试日志
```

或无参数模式（自动生成配置模板，然后交互输入）：

```bash
python -m arxiv2summary
```

## 输出目录

所有产物均存放在以 arXiv 编号命名的文件夹内，**不会污染当前目录**：

```
1706.03762/
├── paper.tex          # 展平后的原始 LaTeX
├── paper-x.tex        # 宏展开后的 LaTeX
├── arxiv2summary.log  # 本次运行日志
└── output.md          # 生成结果（单文件模式）
```

说明：每个 query 的开始/结束时间与耗时也会记录到日志与终端。

多文件模式（`output.mode: multi`）下，按每个 query 的 `output_file` 字段独立输出，例如：

```
1706.03762/
├── notes.md           # section_notes query 的输出
└── translation.md     # translation query 的输出
```

## 配置说明

### LLM 配置

```yaml
llm:
  provider: ollama          # ollama | openai（及其他兼容服务）
  model: qwen3.5:latest
  base_url: http://localhost:11434/v1
  api_key_env: OPENAI_API_KEY
  temperature: 0.2
  max_tokens: 4096
  stream: false             # true：在终端实时打印流式输出
```

将 `stream: true` 后，每个 query 的回答内容会逐 token 实时打印到终端，最终结果同样写入文件。对于 `provider: ollama`，项目会优先使用官方 `ollama` Python 客户端，而不是 OpenAI 兼容接口。

### 默认配置与覆盖顺序

1. 项目根目录的 [default_config.yaml](default_config.yaml) 是基础默认配置。
2. 若运行目录存在 [config.yaml](config.yaml)，则会用它覆盖默认项。
3. 若显式传入 `--config xxx.yaml`，则使用该文件覆盖默认项。
4. 当你**不传 arXiv 参数**启动时，如果当前目录没有 [config.yaml](config.yaml)，程序会把 [default_config.yaml](default_config.yaml) 原样复制为 [config.yaml](config.yaml)。

### 多任务 / 多输出文件示例

`output.mode: multi` 时，每个 query 独立写入各自的 `output_file`：

```yaml
output:
  mode: multi

queries:
  - name: section_notes
    mode: section
    prompt_template: 以笔记形式多层分点总结$section
    output_file: notes.md
    system_prompt: 你是一个严谨的学术助手，回答请使用 Markdown。
    few_shot: []

  - name: translation
    mode: fullpaper           # fullpaper：把完整论文送入上下文
    prompt_template: 将以下论文翻译为中文，保留所有数学公式和图表引用
    output_file: translation.md
    system_prompt: 你是一名专业的学术翻译，请保持术语准确。
    few_shot: []
```

  注意：如果 `output.mode: single`，那么即使某个 query 配置了 `output_file: translation.md`，最终也仍会统一写入 `output.single_file`。如果你希望 `notes.md` 和 `translation.md` 分开，必须把 `output.mode` 改成 `multi`。

### few_shot 使用示例

`few_shot` 是 **字符串列表**，每条字符串为一段示例文本（会被拼接在 prompt 前）：

```yaml
queries:
  - name: section_notes
    mode: section
    prompt_template: 以笔记形式多层分点总结$section
    output_file: notes.md
    system_prompt: 你是一个严谨的学术助手，回答请使用 Markdown。
    few_shot:
      - |
        任务：以笔记形式多层分点总结第 1 章 I Introduction

        输出示例：
        ## I Introduction
        - **研究背景**
          - 现有方法依赖 RNN/CNN，难以并行
          - 注意力机制已被广泛验证
        - **本文贡献**
          - 提出 Transformer，完全基于注意力机制
          - BLEU 分数超越现有 SOTA

      - |
        任务：以笔记形式多层分点总结第 2 章 II Related Work

        输出示例：
        ## II Related Work
        - **序列建模**：RNN 存在长程依赖问题
        - **注意力机制早期工作**：用于对齐，而非独立建模
```

每条 `few_shot` 字符串会自动拼接在当前 query 的 prompt 之前，作为 In-Context Learning 示例。
