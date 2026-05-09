# New Codex_DS_Xiaomi_Switcher 复盘

## 1. 这套程序为什么要做

这套程序的目标，是让 `/Volumes/Ex/Applications/Codex.app` 在不直接支持 DeepSeek 和 Xiaomi 的前提下，仍然能够通过一个本地兼容层，把 Codex 的请求转发给：

- `deepseek-v4-pro`
- `mimo-v2.5-pro`

核心问题有三个：

1. Codex 的自定义 provider 不是随便给一个第三方模型地址就能用，它要求比较接近 OpenAI Responses 风格的交互。
2. `/Volumes/Ex/Applications/Codex.app` 是桌面程序，不可靠地继承 shell 环境变量，所以不能假设它能直接读到终端里的 `DEEPSEEK_API_KEY`、`XIAOMI_API_KEY`。
3. Codex Desktop 的模型面板、profile、以及某些未文档化配置项，行为并不稳定。只靠界面显示，无法构成可维护方案。

所以，这个项目最终采用的是一条更稳定的路线：

- 在本地起一个代理服务，固定监听 `127.0.0.1:5055`
- 由这个代理统一读取 API Key、统一做协议转换、统一处理审计日志
- Codex Desktop 只连接这个本地代理，而不直接连接 DeepSeek 或 Xiaomi

## 2. 我们具体做了什么

### 2.1 建了一个本地 switcher 代理

主程序是：

[New Codex_DS_Xiaomi_Switcher.py](</Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher/New Codex_DS_Xiaomi_Switcher.py>)

它的职责是：

- 监听本地接口
- 提供 `/v1/models`
- 提供 `/responses` 和 `/v1/responses`
- 把 Codex 发来的 Responses 风格请求转成上游兼容的 `chat/completions`
- 再把上游流式结果转回 Codex 能接受的 SSE 事件

目前支持两个 provider：

- DeepSeek: `https://api.deepseek.com/v1`
- Xiaomi: `https://token-plan-sgp.xiaomimimo.com/v1`

对应的模型分别是：

- `deepseek-v4-pro`
- `mimo-v2.5-pro`

### 2.2 统一了启动、认证和切换方式

为了避免散乱脚本，最终只保留了一个统一入口，并提供命令：

`/Users/apexmini/.local/bin/codex-switcher`

它现在承担这些功能：

- `codex-switcher serve`
- `codex-switcher check`
- `codex-switcher auth-token`
- `codex-switcher use gpt`
- `codex-switcher use deepseek`
- `codex-switcher use xiaomi`
- `codex-switcher use status`
- `codex-switcher audit-tail`

其中：

- `use ...` 用来切换 `/Users/apexmini/.codex/config.toml` 顶层当前模型
- `auth-token` 用来给 Codex 的自定义 provider 提供本地 bearer token
- `audit-tail` 用来读取最新审计日志

### 2.3 解决了 Desktop 环境变量不稳定的问题

这套方案不依赖 Finder 启动的 Codex Desktop 自动继承 shell 环境。

真正读取密钥的是本地 switcher，而不是 Codex Desktop 本身。密钥从这里读：

- `/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher/.env`

当前无关变量说明：

- `DASHSCOPE_API_KEY` 与这套 switcher 无关

真正相关的是：

- `DEEPSEEK_API_KEY`
- `XIAOMI_API_KEY`

### 2.4 做了请求级审计日志

后面为了证明“当前回复到底是不是走了 DeepSeek 或 Xiaomi”，在 switcher 里新增了结构化审计日志。

日志文件在：

[requests.jsonl](</Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher/runtime_logs/requests.jsonl>)

每条日志至少会记录：

- 时间戳
- `request_id`
- `requested_model`
- `resolved_model`
- `provider_label`
- `upstream_base_url`
- `input_preview`
- `input_fingerprint`
- 请求阶段，例如：
  - `route.selected`
  - `upstream.request`
  - `upstream.completed`
  - `route.rejected`

这使得我们可以把“Codex app 发出的某条具体请求”和“实际转发到哪个上游模型”绑定到一起，而不是只看配置文件。

### 2.5 兼容了 `gpt-5.4-mini` 的附带内部请求

在实测过程中，发现 Codex 不只发主请求，还会顺手发一些附带内部请求，其中一个典型模型名是：

- `gpt-5.4-mini`

如果不兼容它，switcher 会把它当未知模型拒绝掉。

现在的处理方式是：

- 当 `/Users/apexmini/.codex/config.toml` 的当前 `model_provider = "ds_xiaomi_switcher"` 时
- 如果 Codex 发来 `gpt-5.4-mini`
- switcher 会把它视为“当前激活路由别名”

也就是说：

- 当前切到 DeepSeek 时，`gpt-5.4-mini` 会解析到 `deepseek-v4-pro`
- 当前切到 Xiaomi 时，`gpt-5.4-mini` 会解析到 `mimo-v2.5-pro`

这避免了附带内部请求不断报 `Unknown model 'gpt-5.4-mini'`。

## 3. 当前结果

截至目前，这套方案已经达成下面这些结果：

1. Codex Desktop 可以通过本地 switcher 使用 DeepSeek。
2. Codex Desktop 可以通过本地 switcher 使用 Xiaomi。
3. `gpt-5.4-mini` 这种 Codex 的附带内部请求，不再一律被拒绝，而是能跟随当前激活路由继续工作。
4. 通过审计日志，可以证明某条具体请求实际走到了哪一个上游模型。
5. 切换模型时，不再依赖不稳定的 UI 面板行为，而是直接用命令改写 `/Users/apexmini/.codex/config.toml`。

已经验证过的事实包括：

- DeepSeek 主请求可以被日志证明为转发到 `https://api.deepseek.com/v1`
- Xiaomi 主请求可以被日志证明为转发到 `https://token-plan-sgp.xiaomimimo.com/v1`
- `gpt-5.4-mini` 在 DeepSeek 模式下可以解析到 `deepseek-v4-pro`
- `gpt-5.4-mini` 在 Xiaomi 模式下可以解析到 `mimo-v2.5-pro`

## 4. 日常怎么用

### 4.1 启动主服务 `5055`

日常使用时，最重要的启动命令是：

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
/Users/apexmini/.local/bin/codex-switcher serve --port 5055
```

如果这条进程没起来，Codex Desktop 的 DeepSeek / Xiaomi 路由就不能工作。

确认 `5055` 是否正在监听：

```bash
lsof -nP -iTCP:5055 -sTCP:LISTEN | cat
```

### 4.2 切换当前路由

切换到 GPT：

```bash
/Users/apexmini/.local/bin/codex-switcher use gpt
```

切换到 DeepSeek：

```bash
/Users/apexmini/.local/bin/codex-switcher use deepseek
```

切换到 Xiaomi：

```bash
/Users/apexmini/.local/bin/codex-switcher use xiaomi
```

查看当前状态：

```bash
/Users/apexmini/.local/bin/codex-switcher use status
```

每次执行完 `use gpt`、`use deepseek`、`use xiaomi` 之后，都应完整退出并重启：

[Codex.app](</Volumes/Ex/Applications/Codex.app>)

### 4.3 做基本自检

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
/Users/apexmini/.local/bin/codex-switcher check
```

只跑文本测试：

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
/Users/apexmini/.local/bin/codex-switcher check --mode text
```

只跑 tool 测试：

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
/Users/apexmini/.local/bin/codex-switcher check --mode tool
```

### 4.4 查看审计日志

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
/Users/apexmini/.local/bin/codex-switcher audit-tail --lines 10
```

如果要证明某条 Codex Desktop 请求确实走了 DeepSeek 或 Xiaomi，做法是：

1. 先切到目标路由
2. 重启 Codex Desktop
3. 在 Codex 里发一条带唯一标记的句子
4. 立刻查看最新审计日志

例如 DeepSeek：

```text
Reply with exactly: probe-20260509-abc123
```

如果日志里同时看到：

- `requested_model = "deepseek-v4-pro"`
- `provider_label = "DeepSeek"`
- `upstream_base_url = "https://api.deepseek.com/v1"`
- `input_preview` 含唯一标记

则说明这条请求已经在请求级被证明走了 DeepSeek。

Xiaomi 的判断方式完全相同，只是上游会显示为：

- `https://token-plan-sgp.xiaomimimo.com/v1`

## 5. hooks 发现与结论

本次排查过程中，我们还发现 Codex 本地启用了 hooks。

配置文件在：

[/Volumes/Ex/ai_workspace/library_data/codex/.codex/hooks.json](</Volumes/Ex/ai_workspace/library_data/codex/.codex/hooks.json>)

当前配置显示，Bash 工具的 `PostToolUse` 会执行：

```bash
python3 /Users/apexmini/.codex/hooks/capture_tool.py
```

对应脚本在：

[/Users/apexmini/.codex/hooks/capture_tool.py](/Users/apexmini/.codex/hooks/capture_tool.py)

它的作用是：

- 捕获 Bash 工具执行结果
- 过滤噪音命令
- 将结果写入 `/Users/apexmini/.openclaw/workspace/runtime/knowledge.db`
- 输出 `{}`，不改变 Codex 的工具结果

重要结论：

- 这个 hook 与 DeepSeek/Xiaomi 的 provider 路由无关
- 它不会决定当前模型是谁
- 它也不会影响 switcher 的上游转发逻辑

它是一个独立的“命令结果采集”机制，主要是为 OpenClaw 的知识写入服务。

## 6. 如果 Codex 升级了，这套方案还能不能用

结论先说：

- 短期内，大概率还能继续用
- 中期内，需要留意 Codex 对自定义 provider、Responses 协议和内部附带请求的变化
- 长期看，这套方案是可维护的，但不是“升级后一定零改动”的方案

### 6.1 为什么短期内大概率还能用

因为这套方案依赖的是相对稳定的几个点：

1. Codex 仍然支持自定义 provider
2. `wire_api = "responses"` 这一层仍然存在
3. Codex 仍然允许将 provider 指向 `http://127.0.0.1:5055/v1`
4. 本地 switcher 继续把 Responses 请求转成兼容的 `chat/completions`

如果这几个基础点不变，这套代理模式就仍然有效。

### 6.2 哪些点最脆弱

最需要警惕的是下面这些变化：

1. **Codex 改了请求协议**
   如果以后 `/responses` 的事件格式、字段名、tool-call 结构变了，switcher 可能要跟着改协议转换层。

2. **Codex 改了自定义 provider 机制**
   如果以后不再支持当前的 `model_providers` 配置方式，或对 `wire_api` 有新约束，就要重新适配。

3. **Codex 改了内部附带请求模型名**
   这次已经碰到过 `gpt-5.4-mini`。以后如果 Codex 又引入新的内部模型名，就可能需要继续加兼容别名。

4. **Codex 改了 Desktop 模型面板逻辑**
   UI 展示逻辑本来就不稳定，因此这套方案已经尽量避免依赖 UI 面板本身。但如果将来 Codex 改了默认行为，仍然要重新回归验证。

5. **Codex hooks 机制变化**
   hook 本身不影响路由，但如果 Codex 升级时 hooks 机制变化，相关的命令采集逻辑可能会受影响。

### 6.3 哪些信号说明升级后需要复查

如果 Codex 升级后出现下面这些现象，就应该立刻重新检查 switcher：

- `Unknown model ...` 明显增多
- `route.rejected` 开始出现新的内部模型名
- 审计日志没有新增记录
- Codex Desktop 明明切到 DeepSeek/Xiaomi，但请求没有到 `5055`
- tool call 开始异常
- 模型流式输出格式变化，导致响应中断或空白

### 6.4 升级后的最小回归检查

Codex 升级后，建议做一套最小回归：

1. 启动 `5055`
2. 执行 `codex-switcher use deepseek`
3. 重启 Codex Desktop
4. 发一条带唯一标记的请求
5. 查看审计日志是否显示 DeepSeek upstream
6. 再执行 `codex-switcher use xiaomi`
7. 重启 Codex Desktop
8. 再发一条带唯一标记的请求
9. 查看审计日志是否显示 Xiaomi upstream
10. 观察是否有新的 `route.rejected`

如果这十步都正常，通常就说明升级没有破坏当前方案。

## 7. 这套方案当前的定位

这不是一个依赖 Codex UI 小技巧的临时拼接，而是一个本地协议适配层。

它当前已经具备：

- 可切换
- 可验证
- 可审计
- 可解释
- 可维护

但它仍然不是“完全脱离 Codex 演进”的方案。后续只要 Codex 继续迭代，就需要保留一个基本判断：

- 自定义 provider 是否还支持
- Responses 协议是否变化
- 内部附带模型名是否变化
- 审计日志是否还能捕捉到完整证据链

只要这几个点继续成立，这套方案就可以持续工作。
