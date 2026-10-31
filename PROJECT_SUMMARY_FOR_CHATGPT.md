# ToolFuzz GUI 兼容改造项目说明（供网页版 ChatGPT 接续分析）

整理日期：2026-10-30。本文依据当前文件夹的实际源码、现有虚拟环境及历史结果文件整理。

## 1. 项目背景与分析边界

这是一个基于 ToolFuzz 的 Python 工具测试项目。我保留了 `ToolFuzz/` 原仓库，在其外部增加兼容层、统一运行入口、实验脚本和 Streamlit GUI，希望通过图形界面测试 LLM Agent 所调用工具的运行时异常和回答正确性。

请将 `ToolFuzz/` 视为上游代码，`main/`、`GUI/`、`scripts/` 视为我的改造。后续建议优先通过外部兼容层实现，避免直接改动上游。

本次检查范围：

- 完整阅读外部改造的 24 个 Python 文件，并对照相关上游实现。
- 检查已有 6 组 JSON/HTML 结果，以及现有 `AUDIT_REPORT.md`。
- 验证 Python 语法、核心模块导入、注册工具实例化，并进行少量不访问网络的替身验证。
- **没有调用 DeepSeek、DuckDuckGo 或 PubMed API，没有重新运行完整实验，没有修改业务源码，也没有核实远端模型 ID 的可用性。**
- 根目录的 `20261012-145431.zip` 未解包核对，不能假定它与当前工作目录完全一致。

配套的 `PROJECT_CODE_CONTEXT_FOR_CHATGPT.md` 提供外部改造全部 Python 源码和关键上游实现，方便进一步提出准确的修改方案。

## 2. 当前项目定位

当前版本可以描述为：**保留上游 ToolFuzz，通过外部 Python 兼容层接入 DeepSeek，并以 Streamlit 提供最小可用 GUI 的工具测试原型。**

已经具备的能力：

1. GUI 选择 Runtime / Correctness 两类测试。
2. GUI 选择 DuckDuckGo Search / PubMed 两种内置工具。
3. 输入模型名，设置迭代次数和不同角色的温度。
4. 经统一后端入口创建模型、Agent、Tester，执行并保存报告。
5. 在 GUI 中显示运行状态、耗时、摘要和逐条 JSON 结果。
6. 使用兼容层处理新旧 LangChain 工具调用格式，并替换上游默认 GPT 模型创建路径。
7. 通过命令行脚本复现实验，包含一个故意出错的本地工具。

当前还没有后台任务队列、实时日志面板、取消任务、历史运行管理、报告下载按钮和完整的离线自动化回归测试。

## 3. 目录结构与职责

```text
toolfuzz/                         # 当前工作区根目录
├── ToolFuzz/                     # 上游原仓库，有自己的 Git 仓库
│   ├── src/
│   │   ├── toolfuzz/
│   │   │   ├── agent_executors/  # 各框架的 Agent 执行适配
│   │   │   ├── runtime/          # 参数模糊测试、异常提示生成
│   │   │   ├── correctness/      # 提示组生成、一致性和正确性评估
│   │   │   ├── tools/info_extractors/
│   │   │   ├── dataclasses.py    # Pydantic 结果类型
│   │   │   └── utils.py          # JSON、HTML 报告保存
│   │   ├── templates/           # HTML 模板和图片
│   │   └── eval/                # 原仓库评估代码
│   ├── tests/                   # 原仓库测试
│   ├── .venv/                   # 当前可用的 Python 虚拟环境
│   ├── setup.py
│   ├── environment.yml
│   └── minimal_environment.yml
├── main/                        # 我的改造后端
│   ├── core/
│   │   ├── model_provider.py    # DeepSeek 模型创建
│   │   ├── tool_registry.py     # 工具注册、显示名称和实例工厂
│   │   ├── run_result.py        # run_id、时间和统一返回结构
│   │   ├── runtime_runner.py    # Runtime 业务入口
│   │   └── correctness_runner.py# Correctness 业务入口
│   ├── compat/
│   │   ├── react_agent_new.py   # 新旧 LangChain 消息兼容
│   │   ├── runtime_tester.py    # Runtime 提示生成和异常分类兼容
│   │   └── correctness.py       # Correctness 生成器、裁判和 Tester 兼容
│   ├── experiments/
│   │   ├── runtime_duckduckgo.py
│   │   ├── runtime_fragile.py
│   │   └── correctness_pubmed.py
│   ├── tests/                   # 主要是联网冒烟脚本
│   │   ├── test_import.py
│   │   ├── test_deepseek.py
│   │   ├── test_deepseek_tool.py
│   │   ├── test_compatible_agent.py
│   │   └── test_pubmed.py
│   ├── results/                 # 历史 JSON 和 HTML 报告
│   └── toolfuzz.png
├── GUI/
│   ├── app.py                   # Streamlit 单页 GUI
│   ├── components/              # 空目录，尚未拆分组件
│   ├── pages/                   # 空目录
│   └── services/                # 空目录
├── scripts/
│   └── setup_dev_path.py        # 写 .pth 文件，配置开发导入路径
├── AUDIT_REPORT.md              # 2026-09-01 的历史审计，部分内容已过时
├── .gitignore
├── 20261012-145431.zip          # 已有归档，未核对其内容
└── toolfuzz.png
```

各 Python 包还包含空的 `__init__.py`，上图省略。

上游本地版本信息：

- Git remote：`https://github.com/eth-sri/ToolFuzz.git`
- HEAD：`26d14fc72872033f32d96c05d53440f10ca552e8`
- 提交标题：`Merge migration from pyautogen to ag2`
- `git -C ToolFuzz status --short` 为空，说明检查时该本地提交没有 Git 可见的工作区改动；这并不代表已经与远端最新版本比较。
- 当前根目录 `git status` 无法识别有效仓库。环境展示了一个受限 `.git` 目录，但不能据此认定外部改造已经纳入版本管理。

## 4. 架构与依赖关系

```text
用户操作
   │
   ▼
GUI/app.py ───────► main/core/tool_registry.py
   │                     │
   │                     └─► 创建 LangChain Tool
   ▼
main/core/runtime_runner.py 或 correctness_runner.py
   ├─► model_provider.py：创建 DeepSeek ChatOpenAI 实例
   ├─► run_result.py：运行标识、时间、统一返回结构
   └─► main/compat/
          ├─ CompatibleReactAgentNew
          ├─ CompatibleRuntimeErrorTester
          └─ CompatibleCorrectnessTester
                     │
                     ▼
          ToolFuzz 上游 Fuzzer / Prompt / Oracle / 报告工具
                     │
                     ├─► main/results/*.json
                     └─► main/results/*.html

main/experiments/* ──► 同一套 core runner
```

这是一套同进程 Python 应用，没有独立的前后端 HTTP 服务。GUI 直接同步调用后端函数。`core` 和 `compat` 没有反向依赖 GUI；GUI 没有导入实验脚本。

## 5. 模型配置与工具注册

### 5.1 模型配置

`main/core/model_provider.py` 集中提供：

```python
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"

create_deepseek_llm(model_name=DEFAULT_MODEL, temperature=0)
```

模型通过 `langchain_openai.ChatOpenAI` 的 OpenAI 兼容接口连接 DeepSeek，密钥来自 `DEEPSEEK_API_KEY`。缺失密钥时抛出 `RuntimeError`。

注意：`deepseek-v4-flash` 是**源码当前配置值**，本次未验证服务端是否接受该模型名。GUI 允许修改模型名，但没有暴露 base URL、API key、超时、重试次数等配置；改造代码也没有加载 `.env` 的逻辑，仅放置 `.env` 文件不会自动生效。

Runtime 创建两份模型实例：提示生成模型、Agent 模型。Correctness 创建三份：提示生成、Agent、裁判模型。目前三者使用同一模型名和同一服务地址，可以分别设置温度，但不能分别指定模型名或提供商。

### 5.2 工具注册

`main/core/tool_registry.py` 提供：

- `list_tools()`：返回稳定的工具 ID。
- `list_tool_options()`：返回 GUI 所需的 ID 和显示名。
- `create_tool(tool_id)`：去掉首尾空白并转为小写后，创建新工具实例。

| 工具 ID | GUI 名称 | 实例类 | 工具自身名称 |
|---|---|---|---|
| `duckduckgo` | DuckDuckGo Search | `DuckDuckGoSearchRun` | `duckduckgo_search` |
| `pubmed` | PubMed | `PubmedQueryRun` | `pub_med` |

当前两个工具类在模块顶层导入；缺少其中一个依赖时，可能使注册模块整体导入失败。注册表尚未记录工具支持的测试类型、初始化参数或依赖状态。

## 6. 核心兼容改造

### 6.1 Agent 消息兼容：`main/compat/react_agent_new.py`

`CompatibleReactAgentNew` 继承原 `ReactAgentNew`，覆盖四个方法：

| 方法 | 改造内容 |
|---|---|
| `get_tool_arguments()` | 优先读取 `AIMessage.tool_calls`，同时保留 `additional_kwargs['tool_calls']` 旧格式；将参数转成 JSON 字符串；没有调用时返回占位字符串，避免返回 `None` |
| `get_trace()` | 将新旧工具调用格式统一为带 `role`、`tool_calls`、`tool_call_id` 的字典轨迹，并保留调用 ID |
| `get_tool_output()` | 收集 ToolMessage 内容；非字符串输出转 JSON，避免直接 join 失败 |
| `extract_error()` | 仅将带有效 `error` 字段的 JSON 字典或包含 `Error: ` 的文本视为错误，避免把普通 JSON 输出误判或触发 KeyError |

仍沿用上游的 Agent 创建和调用逻辑，底层是 `langgraph.prebuilt.create_react_agent`。

当前实现仍具有单工具分析倾向：参数只返回时间顺序上遇到的第一个工具调用，输出则按倒序拼接全部 ToolMessage。因此多轮调用时参数与输出不一定一一对应，不能直接宣称已经支持完整的多工具、多轮轨迹评估。

### 6.2 Runtime 兼容：`main/compat/runtime_tester.py`

包含三个关键类：

1. `CompatibleRuntimePromptGenerator`：使用外部注入的模型，复用上游提示模板和 JSON 解析器，替换原提示生成器默认创建模型的路径。
2. `RuntimeResultMixin`：覆盖 `run_test()`，区分预期异常与其他框架异常。
3. `CompatibleRuntimeErrorTester`：通过多继承组合该 Mixin 与上游 Tester，保留上游参数 Fuzzer，并替换提示生成流程。

判定方式：

- Agent 直接抛出 `expected_exception`：`successful_trigger=True`。
- Agent 直接抛出其他异常：`successful_trigger=False`。
- Agent 将异常封装在返回值中：需要 `is_raised_exception=True` 且异常文本包含预期异常类名，才判为成功。

这修正了上游“非目标异常也可能计为成功触发”的问题，但封装异常的匹配目前仍基于字符串，存在漏判和误判空间。

### 6.3 Correctness 兼容：`main/compat/correctness.py`

包含：

- `CompatibleCorrectnessPromptGenerator`：跳过上游默认模型构造，注入 DeepSeek 模型，继续使用上游模板、同义参数生成和期望答案生成方法。
- `CompatibleCorrectnessOracle`：使用注入的裁判模型构建 Agent 正确性、工具输出正确性、回答相关性三条链。
- `CompatibleCorrectnessTester`：覆盖 `test()`，串联兼容生成器和裁判，执行提示组并保留上游结果结构。

主要评估包括：同组提示是否产生相同工具参数、是否产生相同工具输出、Agent 回答的正确性得分，以及部分低分结果的相关性。

需要准确理解：当前一致性分桶主要依据**字符串完全相等**；期望答案由 LLM 生成，裁判也使用同一模型名。因此它属于基于 LLM 的评估，不能等价为独立事实验证或人工标注的准确率。

虽然 Oracle 构建了 `tool_out_chain`，当前 Tester 主循环并未单独调用 `is_tool_output_correct()`。不要将“已构建裁判链”描述成“所有评估维度均已执行”。

## 7. 两类测试的执行过程

### 7.1 Runtime

入口：`main.core.runtime_runner.run_runtime(tool, ...)`。

```text
创建运行上下文
→ 创建提示模型与 Agent 模型
→ 创建兼容 Agent 与 Runtime Tester
→ 上游 Fuzzer 直接调用工具，收集异常类型及对应参数
→ sanity_check 检查 Agent 能否用这些参数触发错误
→ LLM 将异常参数转为自然语言测试提示
→ Agent 执行提示
→ 判定是否触发预期异常
→ 保存 JSON / HTML
→ 返回统一运行结果
```

重要参数默认值：`fuzzer_iters=1`、`prompt_temperature=0`、`agent_temperature=0`、`clear_previous=True`。

`fuzzer_iters` 是**参数模糊测试轮数**，不是自然语言提示数量，也不是最终记录数量。一个异常参数可生成多个提示；当前流程在某个参数的提示列表中即使已成功，也可能继续执行剩余提示，因此成功记录不等于不同 bug 数。

如果参数模糊测试没有发现异常，Tester 会直接返回，结果数量可能为 0。0 条记录不表示工具已被充分验证。

### 7.2 Correctness

入口：`main.core.correctness_runner.run_correctness(tool, ...)`。

```text
创建运行上下文和三种角色模型
→ 创建兼容 Agent 与 Correctness Tester
→ LLM 生成问题模板、参数变体、同义或自然化提示
→ LLM 为提示组生成期望答案
→ Agent + 工具逐条执行
→ 对调用参数与工具输出进行字符串分桶
→ 裁判给 Agent 回答打分，必要时判断相关性
→ 保存每个提示组及逐条结果
→ 返回统一运行结果
```

重要默认值：`prompt_set_iters=1`、`prompt_temperature=0.4`、`agent_temperature=0`、`judge_temperature=0`。

`prompt_set_iters` 是提示组数量，一组可以含多条提示，并需要多次 LLM 调用。该数字不能直接代表调用次数或费用上限。

## 8. GUI 的实际行为

`GUI/app.py` 使用 Streamlit，界面目前为英文。

侧栏配置：

- Runtime / Correctness。
- DuckDuckGo Search / PubMed。
- 模型名。
- 1～100 次迭代或提示组。
- 0～2 的生成、Agent 温度；Correctness 还可设置裁判温度。

点击 Start Test 后同步执行 runner，用 spinner 显示运行中。成功后将结果放在 `st.session_state.last_result`；出错时清空结果并显示异常类型和文本。

结果区显示状态、测试类型、工具、耗时、模型、run_id、摘要和可展开的原始 JSON。报告区只显示 JSON / HTML 文件路径，尚未提供下载或内嵌 HTML 预览。

GUI 输出名固定为：

```text
main/results/gui_runtime_latest.json
main/results/gui_runtime_latest.html
main/results/gui_correctness_latest.json
main/results/gui_correctness_latest.html
```

两类测试的文件相互分开，但同类型不同工具、不同会话会共用同一个 latest 名称。当前没有按 run_id 建立独立目录。

## 9. 返回值与报告文件的区别

两类 runner 都返回统一 Python 字典：

```json
{
  "status": "completed",
  "test_type": "runtime 或 correctness",
  "tool": "工具自身名称",
  "model": "模型名",
  "config": {"...": "本次配置"},
  "summary": {"...": "统计摘要"},
  "results": [],
  "artifacts": {"json_path": "...", "html_path": "..."},
  "metadata": {
    "run_id": "UTC时间_随机后缀",
    "started_at": "UTC ISO时间",
    "finished_at": "UTC ISO时间",
    "duration_seconds": 0
  }
}
```

其中：

- Runtime 摘要为 `test_count`、`successful_triggers`。
- Correctness 摘要目前只有 `prompt_set_count`。
- `status='completed'` 表示流程正常返回，不表示工具没有问题或所有测试通过。
- 时间戳使用 UTC；显示时与中国时间相差 8 小时。
- **保存到磁盘的 JSON 是上游结果对象列表，并不是上面的统一结果字典。** run_id、模型配置、耗时等目前主要留在返回值/GUI 会话中，没有随该 JSON 完整持久化。
- Runtime 记录包含预期异常、实际异常、提示、参数、轨迹及 `successful_trigger`。
- Correctness 记录按提示组组织，包含参数分桶、输出分桶、一致性标记、期望答案及逐条评分等。
- `trace` 字段在结果对象中通常是 JSON 字符串，不是已展开的数组。
- `unexpected_agent_output` 虽然名称像布尔标记，实际保存的是整数正确性得分，通常越高越好。

## 10. 当前环境和启动方法

### 10.1 本地环境实测

以下版本来自当前 `ToolFuzz/.venv` 的包元数据，只说明本地安装状态，不等于已验证所有组合兼容：

| 项目 | 本地值 |
|---|---|
| Python | 3.10.12 |
| toolfuzz | 0.1.0（本地源码安装） |
| Streamlit | 1.63.0 |
| langchain | 1.3.18 |
| langchain-core | 1.6.1 |
| langchain-openai | 1.6.0 |
| langchain-community | 0.4.2 |
| langgraph | 1.2.11 |
| pydantic | 2.13.5 |
| openai | 3.6.0 |
| ddgs | 9.16.0 |
| xmltodict | 1.0.4 |
| faker / jinja2 / tqdm | 40.37.0 / 3.1.6 / 4.70.0 |

当前未安装名为 `duckduckgo-search` 的发行包，但 `ddgs` 已安装，注册表中的 DuckDuckGo 工具仍能实例化。不要直接用上游旧依赖文件覆盖当前环境：原仓库的依赖记录主要是 LangChain 0.3 系列，外部改造尚未提供自己的完整依赖锁定文件。

### 10.2 导入路径机制

`scripts/setup_dev_path.py` 会在执行它的 Python 所属 `site-packages` 写入 `toolfuzz_workspace.pth`，内容是：

```text
项目根目录
项目根目录/ToolFuzz
```

项目根用于 `import main` / `import GUI`；ToolFuzz 根用于原代码中的 `import src...`；安装后的 `toolfuzz` 包用于 `import toolfuzz...`。因此仅执行路径脚本不能替代安装上游包。

当前虚拟环境中 `.pth` 已存在，两套命名空间都能解析到本地 ToolFuzz 源码。混用 `toolfuzz.*` 和 `src.toolfuzz.*` 是继承上游导入方式的兼容负担，后续不能仅做机械替换，需要关注类型身份、资源定位及上游引用。

另外，当前 `.venv/bin/python3` 指向另一个项目目录中的解释器路径，虽然现在可以运行，但复制目录到其他机器时不能认为该虚拟环境可直接复用。

### 10.3 使用现有环境启动

在当前项目根目录执行：

```bash
source ToolFuzz/.venv/bin/activate
export DEEPSEEK_API_KEY='替换为你的密钥'

# 仅在当前环境未配置开发路径时执行；会写入该环境的 site-packages
python scripts/setup_dev_path.py

# GUI 正确启动入口
python -m streamlit run GUI/app.py
```

应由 Streamlit 启动 GUI，直接 `python GUI/app.py` 不是完整的应用启动方式。

实验脚本建议使用模块方式：

```bash
python -m main.experiments.runtime_fragile
python -m main.experiments.runtime_duckduckgo
python -m main.experiments.correctness_pubmed
```

这些实验会调用模型；DuckDuckGo / PubMed 还涉及外部工具访问。此处只是说明入口，本次检查未执行。

迁移到新机器时应重建虚拟环境、安装本地 ToolFuzz 及 GUI/工具依赖，再配置路径。由于没有外部改造的锁定依赖文件，目前还不能给出经过本次全新安装验证的一键部署流程。

## 11. 历史运行证据

现有结果文件可以证明这些路径过去产生过输出，但它们未保存完整运行元数据，不能据此确认每份结果都由当前源码和当前配置生成。

| JSON 文件（位于 main/results） | 文件内容统计 |
|---|---|
| `runtime_minimal.json` | 5 条记录，5 条标记成功；预期和实际均为“空 query 触发 ValueError” |
| `runtime_duckduckgo.json` | 5 条，3 条标记成功、2 条失败；包含网络超时和网络不可达情况 |
| `registry_runtime_test.json` | 10 条，均标记成功；主要为 `query is mandatory` 的 DDGSException |
| `gui_runtime_latest.json` | 8 条，均标记成功；主要为相同的空查询 DDGSException |
| `correctness_pubmed_minimal.json` | 1 组、4 条提示；工具异常标记为 0；参数和输出均不一致；评分 10、10、9、7 |
| `registry_correctness_test.json` | 1 组、5 条提示；工具异常标记为 0；参数和输出均不一致；评分分布为 10×3、9×1、5×1 |

正确性结果中的“不一致”不一定意味着实际功能错误：搜索和文献工具可能因为查询差异、动态内容或排序变化返回不同文本，而当前分桶按字符串比较。需要结合具体轨迹分析。

同理，Runtime 的成功触发数量不是独立漏洞数量，网络异常也不能全部解释为工具实现缺陷。

## 12. 与历史 AUDIT_REPORT.md 的差异

历史审计日期为 2026-09-01，其中不少判断已经不能代表当前代码。

| 历史审计事项 | 当前状态 |
|---|---|
| core 缺少 runner 与注册表 | 已存在 runtime_runner、correctness_runner、tool_registry 和 run_result |
| fragile 实验绕过兼容层 | 已改为调用统一 `run_runtime()`，间接使用兼容 Agent / Tester |
| fragile 缺少旧结果清理 | 已由 runner 的默认 `clear_previous=True` 统一处理 |
| experiments / tests 重复创建模型 | 使用模型的外部脚本现已复用 `create_deepseek_llm()` 或 runner |
| GUI 只有占位入口 | 已有完整单页配置、执行、结果展示流程 |
| GUI 子目录尚未建立 | components、pages、services 已存在，但都是空目录 |
| 外部改造缺少使用说明、依赖锁定 | 当前仍需完善；本文补充说明，未新增安装配置 |
| 根目录 Git 管理问题 | 本次仍无法将根目录识别为有效 Git 仓库 |

后续分析请以当前源码为准，不要直接照旧审计重复实施已经完成的重构。

## 13. 当前需要关注的问题

### 优先级 A：结果可信度与可复现性

**A1. Runtime 覆盖方法遗漏状态重置。**

上游 `RuntimeErrorTester.run_test()` 在 `finally` 中调用 `context_resetter.reset_context()`，外部 `RuntimeResultMixin.run_test()` 没有保留这一步。离线替身验证中连续调用两次后重置次数仍为 0。当前默认使用 DummyResetContext 时影响较小，但接入文件、数据库等有状态工具会造成用例间污染。定位：`main/compat/runtime_tester.py:30`；对照上游 `runtime_fuzzer.py:98`。

**A2. 封装错误按异常类名字符串匹配，可能漏判。**

如果工具错误文本只含 `query must not be empty`，未包含 `ValueError`，即使 Agent 标记抛错也会被判为未触发预期异常；此行为已用替身返回值验证。反过来，文本出现类名也不保证异常类型确实匹配。应考虑保留结构化异常类型和来源。

**A3. 新兼容 Agent 仍有多条错误消息漏检。**

`extract_error()` 遇到 `{"error": null}` 会立即返回无错误，可能忽略后面的真实错误 ToolMessage。已用两条合成轨迹离线复现。定位：`main/compat/react_agent_new.py:85`。同时，工具消息的结构化错误状态尚未成为主要判断依据。

**A4. Correctness 继承了上游评分重试错误。**

原 `CorrectnessOracle.agent_out_correctness()` 捕获解析异常或 KeyError 后，调用的是 `is_agent_output_relevant()`，而不是重试正确性评分；参数位置也不对应，会把重试计数传到 `llm_expect`。兼容类没有覆盖这个方法，因此仍继承该问题。离线替身已验证该分支错误地进入相关性方法并返回布尔结果，可能混入整数评分。定位：`ToolFuzz/src/toolfuzz/correctness/correctness_oracle.py:52`。建议优先在外部兼容类修正。

**A5. 结果元数据没有完整持久化。**

目前磁盘 JSON 只保存上游结果列表，难以追溯模型、参数、代码版本、run_id 和耗时。建议明确运行记录格式，并为旧报告保留兼容读取方式。

### 优先级 B：报告保存与 GUI 行为

**B1. 固定 latest 文件名会覆盖历史、产生会话冲突。**

run_id 已生成但没有用于文件路径。默认清理旧报告发生在新报告保存前，保存失败时可能丢失旧报告。未来应优先按 run_id 隔离输出，并考虑原子保存。

**B2. 返回了 HTML 路径，不代表文件一定存在。**

上游 `save_test_results_html()` 在结果为空时跳过 HTML 写入，但 runner 仍返回 HTML 路径且标记 completed。应让 artifacts 反映实际存在的文件。

**B3. HTML 资源复制目录与报告目录不一致。**

原保存函数把 `toolfuzz.png` 复制到进程当前目录，而 HTML 使用相对路径 `./toolfuzz.png`。报告默认保存在 `main/results/`，因此图片不一定能正确显示。根目录和 `main/` 下的同名图可能与该逻辑有关，不能仅凭文件名断言它们都是手工加入的。定位：`ToolFuzz/src/toolfuzz/utils.py:47`。

**B4. GUI 运行过程缺少进度和恢复能力。**

目前只有 spinner，日志主要输出终端；出错后清空 last_result，没有保存统一的失败运行记录。建议先完善运行记录、进度回调和报告下载，再决定是否需要独立任务队列。

**B5. 暂不适合直接并发执行多次测试。**

除了固定输出名，上游 Fuzzer 的 `CustomJsonParseBlock` 还会临时替换进程全局 `json.loads`，并使用类级 taint 状态。后续加线程并行或多用户执行前，需要审查隔离方式；仅改输出文件名不能解决全部共享状态问题。

### 优先级 C：工程维护

- `main/tests/test_*.py` 多数在模块顶层调用模型或外部服务，没有断言，不是完整的自动化回归测试。直接交给测试发现机制可能在导入阶段就联网、消耗额度或因缺少密钥失败。
- 上游 Agent 构造函数使用宽泛异常捕获并将 `agent=None`；runner 能检测失败，但原始原因可能被隐藏。
- `CompatibleCorrectnessTester.test()` 的 while 循环没有无有效提示时的上限；提示生成异常、空结果、格式不完整的处理还比较有限。
- `model_provider` 没有按角色配置独立模型名、端点和调用预算。
- 两个 runner 的保存、清理和序列化逻辑较相似，适合在结果格式稳定后再少量抽取共用逻辑。
- 当前没有外部改造的依赖锁定和有效根仓库管理，复制整个目录不能代替可复现安装。

以上问题是检查发现的待改进项，本次没有执行修复。

## 14. 本次实际验证结果

| 检查项 | 结果与边界 |
|---|---|
| 外部改造 24 个 Python 文件语法 | `ast.parse` 全部通过 |
| 8 个 core / compat 模块导入 | 当前虚拟环境中全部通过 |
| DuckDuckGo / PubMed 实例创建 | 成功；没有调用工具 |
| `main`、`GUI`、`toolfuzz`、`src` 模块定位 | 均指向当前工作区对应目录 |
| 上游 Git 状态 | 当前本地提交工作区干净 |
| 历史 6 份 JSON 解析 | 成功，统计见前文 |
| Runtime 非目标异常分类 | 离线替身得到 successful_trigger=False，符合改造目标 |
| 封装错误缺少异常类型文本 | 离线复现可能漏判 |
| Runtime context 重置 | 离线复现覆盖方法未调用重置 |
| 多条 ToolMessage 错误提取 | 离线复现前条空 error 掩盖后条错误 |
| 上游正确性评分重试分支 | 离线复现错误进入相关性判断 |
| GUI 浏览器交互、完整联网实验 | 本次未验证 |

## 15. 建议后续推进顺序

1. 先补少量确定性的离线测试，覆盖上述异常分类、状态重置、消息提取、评分重试和报告存在性问题，并在兼容层修复。
2. 设计统一的持久化运行记录，让 run_id、模型、配置、结果和报告路径可以追溯；按运行分目录保存。
3. 增加 GUI 的报告下载、历史列表、错误详情与进度信息。
4. 为外部改造建立依赖清单、可复现环境和版本管理。
5. 在工具接入需求明确后，再扩展注册信息、独立角色模型配置、多工具调用和任务隔离。

## 16. 可直接发送给网页版 ChatGPT 的接续提示词

```text
我上传了两个文件：
1. PROJECT_SUMMARY_FOR_CHATGPT.md：当前项目结构、实现、验证边界和已发现问题。
2. PROJECT_CODE_CONTEXT_FOR_CHATGPT.md：我的全部 Python 改造和关键上游源码快照。

这是我对 ToolFuzz 的兼容改造和 GUI 封装。ToolFuzz/ 是上游仓库，main/、GUI/、scripts/ 是我的代码。现有路线是 Streamlit GUI → core runner → compat → ToolFuzz，并通过 ChatOpenAI 的兼容接口接入 DeepSeek。

请先依据上传源码复述你理解的架构、已完成内容和仍存在的问题，区分已经离线验证的问题、历史结果和待联网验证的功能。不要把旧 AUDIT_REPORT.md 里已解决的问题再次当成当前缺陷，也不要假设 GUI 已支持后台任务、报告下载或完整的多工具评估。

然后请按“结果可信度 → 运行记录持久化 → GUI 可用性 → 环境可复现”的顺序，帮我制定分阶段改进方案。优先保留 ToolFuzz/ 原仓库不动，在 main/compat 和 main/core 中修复。每一步请列出需要改的文件、原因、验收方式；如上下文不足，请指出具体缺少哪个文件或运行证据，不要臆造项目中不存在的接口。

我希望先做小而可验证的改动，建立稳定基础，再扩展功能。
```
