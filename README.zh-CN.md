# New Codex Switcher 中文说明

这是一个给 Codex Desktop 用的本地代理，用来把自定义 provider 请求转发到两个上游：

- DeepSeek `deepseek-v4-pro`
- Xiaomi `mimo-v2.5-pro`

它对外暴露一个统一的 `Responses` 风格接口，并在本地完成模型路由。

## 这次版本新增的重点

这次更新不只是保留 `1M` 的显示值，而是把已验证能让 Codex Desktop 新会话真正吃到 `1M` 上下文的配置一起固化进仓库。

现在 `codex-switcher use deepseek` 和 `codex-switcher use xiaomi` 会同时写入这三项：

- `model_context_window = 1048576`
- `model_auto_compact_token_limit = 1000000`
- `model_catalog_json = "/absolute/path/to/model-catalogs/ds-xiaomi-1m.example.json"`

其中第三项依赖仓库里的示例 catalog：

- [model-catalogs/ds-xiaomi-1m.example.json](model-catalogs/ds-xiaomi-1m.example.json)

这个 catalog 把下面三个 slug 都提升到 `1M`：

- `deepseek-v4-pro`
- `mimo-v2.5-pro`
- `gpt-5.4`

对应字段是：

- `context_window = 1048576`
- `max_context_window = 1048576`
- `effective_context_window_percent = 100`

## 为什么之前 Codex Desktop 只显示 258K

根因不是代理没生效，而是 Codex Desktop 的 UI 和线程初始化路径回退到了内置模型元数据。

本地缓存里，`gpt-5.4` 的内置元数据是：

- `context_window = 272000`
- `effective_context_window_percent = 95`

所以桌面端会显示：

`272000 * 0.95 = 258400`

也就是你看到的大约 `258K`。

这说明单独把 `model_context_window` 改成 `1048576` 还不够。要让新会话真正变成 `1M`，至少要同时满足两层：

1. 配置层：
   - `model_context_window = 1048576`
   - `model_auto_compact_token_limit = 1000000`
2. 模型目录层：
   - `model_catalog_json` 指向一个把相关 slug 提升到 `1M` 的自定义 catalog

另外还有一个关键现象：

- 旧线程会继续沿用旧的上下文元数据
- 新线程才会吃到新的 `1M` 设置

所以验证是否成功时，必须新建一个全新会话，而不是继续旧线程。

## 推荐配置

参考英文示例：

- [codex.config.example.toml](codex.config.example.toml)

其中需要特别注意：

- `model_catalog_json` 必须写绝对路径
- `base_url` 应该指向 `http://127.0.0.1:5055/v1`
- 自定义 provider 名称统一使用 `ds_xiaomi_switcher`

## 使用步骤

1. 启动本地代理：

```bash
cd "/Volumes/Ex/ai_workspace/Codex Project workplace/New Codex Switcher"
/Users/apexmini/.local/bin/codex-switcher serve --port 5055
```

2. 在 Codex Desktop 配置里接入自定义 provider，格式参考 [codex.config.example.toml](codex.config.example.toml)。

3. 确认 `model_catalog_json` 指向仓库里的示例 catalog，或你基于它生成的本地绝对路径。

4. 切换到 DeepSeek 或 Xiaomi：

```bash
codex-switcher use deepseek
codex-switcher use xiaomi
codex-switcher use status
```

5. 完整退出并重新打开 Codex Desktop。

6. 新建一个全新会话，确认上下文窗口显示接近 `1M`，而不是 `258K`。

## 当前仓库里和 1M 相关的文件

- [README.md](README.md)
- [README.zh-CN.md](README.zh-CN.md)
- [codex.config.example.toml](codex.config.example.toml)
- [model-catalogs/ds-xiaomi-1m.example.json](model-catalogs/ds-xiaomi-1m.example.json)
- [New Codex_DS_Xiaomi_Switcher.py](New%20Codex_DS_Xiaomi_Switcher.py)

## 说明

`use gpt` 会回到内置 GPT 路线，并移除这次为自定义 provider 写入的 `1M` 覆盖项。这样切回官方模型时，配置不会残留本地 switcher 的额外设置。