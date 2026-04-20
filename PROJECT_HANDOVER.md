# Music Speaks 项目交接文档

> 本文档由 MiniMax Agent 整理，供其他 AI Agent 全面接管 Music Speaks 项目使用。
> 最后更新：2026-04-20

---

## 一、项目基本信息

| 项目 | 内容 |
|------|------|
| 项目名称 | Music Speaks |
| 项目路径 | `/Users/yuantao/Documents/codex/Terry Music/` |
| 主文件 | `app.py`（Flask 单文件应用，约 8000+ 行） |
| 线上地址 | https://terry-music.onrender.com |
| Git 仓库 | `origin main` 分支 |
| 最新提交 | `927ad75` — fix: prevent copied lyric prompts |
| 部署方式 | GitHub → Render 自动构建（需 push 到 main 分支） |

---

## 二、技术栈

- **后端框架**：Flask（Python 单文件 app.py）
- **前端**：内嵌 HTML/CSS/JS（单文件，无分离前端）
- **AI 模型**：MiniMax API（文本生成 + 语音合成）
- **数据库**：SQLite（本地文件 `music.db`）
- **样式**：CSS 变量系统，支持亮/暗主题切换
- **部署**：Render.com（Python 3.11 buildpack）

---

## 三、核心功能

### 3.1 歌词生成
- **API**：`/api/lyrics`（POST）
- **模型**：MiniMax `MiniMax-text-01`
- **流程**：用户输入 prompt → 检测语言 → 翻译（如需）→ 提取主题 → AI 生成原创歌词
- **防抄袭**：三层防护（Prompt约束 + 主题提取 + 后端原创性校验）
- **字数要求**：最短600字符，目标800-2000，最长2500
- **语言对齐**：歌词语种与所选音色语种一致，与界面语言无关

### 3.2 音乐生成
- **API**：`/api/generate`（POST）
- **模型**：MiniMax `speech-2.0-t2a`
- **流程**：歌词 → 提交生成任务 → 轮询状态 → 下载音频

### 3.3 音色选择器
- **语种数**：29 个语种
- **音色总数**：303 个音色
- **UI**：两栏布局（语种列表 156px + 音色列表自适应）
- **预览**：`/api/voice/preview?voice_id=xxx`（GET）

### 3.4 声音克隆
- **API**：`/api/voice/clone`（POST）
- **注意**：clonedVoiceId 有过期时间（24小时），需定期刷新

### 3.5 播放器
- **UI**：深色玻璃态主题，圆形绿色播放按钮
- **歌词面板**：流畅 slide-up 动画（cubic-bezier）
- **全屏歌词**：scale 入场动画

---

## 四、API 端点一览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/voice` | GET | 获取所有音色列表 |
| `/api/voice/preview` | GET | 音色预览音频 |
| `/api/voice/clone` | POST | 声音克隆 |
| `/api/voice/clone/status` | GET | 克隆状态 |
| `/api/lyrics` | POST | 生成歌词 |
| `/api/generate` | POST | 生成音乐 |
| `/api/jobs/<id>` | GET | 查询任务状态 |
| `/api/jobs` | GET | 列出所有任务 |
| `/api/tts` | POST | 文字转语音 |
| `/api/song-title` | POST | AI 生成歌名 |

---

## 五、环境变量

| 变量 | 说明 |
|------|------|
| `MINIMAX_API_KEY` | MiniMax API 密钥（必需） |
| `MINIMAX_GROUP_ID` | MiniMax Group ID |
| `SECRET_KEY` | Flask 密钥 |

---

## 六、数据库 Schema（music.db）

### jobs 表
- `id`：任务 ID
- `status`：pending / running / completed / error
- `created_at`：创建时间
- `completed_at`：完成时间
- `song_title`：歌名
- `lyrics`：歌词
- `prompt`：音乐风格 prompt
- `voice_id`：所选音色
- `download_url`：音乐文件 URL
- `lyrics_language`：歌词语种

### 其他表
- `voice_samples`：音色样本（声音克隆用）
- `cloned_voices`：克隆音色记录

---

## 七、已知问题与解决方案（已修复）

| 问题 | 状态 | 修复方式 |
|------|------|---------|
| 音色预览播放失败 | ✅ 已修复 | `stopVoicePreview()` 不再清空 `_voicePlayPending`，避免 fetch early return |
| 歌词照搬用户指令 | ✅ 已修复 | 三层防抄袭：Prompt约束 + 主题提取 + 后端原创性校验 |
| 界面语言影响歌词语种 | ✅ 已修复 | 歌词语种由音色决定，与界面语言独立 |
| 歌词语言与音色不匹配 | ✅ 已修复 | 自动检测 + 翻译（`_detect_text_language` + `_translate_text`） |
| 播放器与整体风格不协调 | ✅ 已修复 | 深色玻璃态 + 圆形按钮 + 流畅动画 |
| 歌词生成太短 | ✅ 已修复 | 最短600字，后端自动拦截 |
| 音色列表不支持亮色主题 | ✅ 已修复 | 改用 CSS 变量（`--bg-secondary` 等） |
| 部分音色 preview 报错 2054 | ✅ 已优化 | 友好错误提示「This voice is not available」 |

---

## 八、近期提交历史（按时间倒序）

```
927ad75 — fix: prevent copied lyric prompts（Codex 执行）
e9c6309 — fix: 歌词生成语言翻译 + 防抄袭完整方案
04b578e — fix: 优化voice preview错误提示
ae915da — feat: 歌词附加要求字段 + 音色列表主题适配
e9892f2 — fix: 加强歌词生成防抄袭约束
d131fa6 — feat: 播放器UI全面重设计
b883fc3 — fix: 音色预览修复 + 歌词字数限制
2e1d59c — Fix: lyrics language match voice
a9c178f — fix: 歌词语言误报 + 语音预览可靠性
a92238c — feat: 歌名生成重写为 AI 两阶段生成法
c2c9783 — feat: 歌词生成精简 + 全屏播放器图标化
39e61e4 — fix: 音色预览音频叠加问题
2e42b8f — feat: 歌词滚动同步优化
3271900 — fix: 多项样式和逻辑修复
6a1d6b9 — fix: background cleanup thread + 减少 JOB_TIMEOUT
```

---

## 九、待处理任务（TODO）

### 高优先级
1. **歌词长度硬性规定** — 已在 prompt 中要求 600-2500 字，但后端应增加自动检测和重新生成逻辑
2. **附加要求字段优化** — `lyrics_extra` 字段已添加，可控制长度/情感/风格/节奏/结构

### 中优先级
1. **Play icon 阴影** — 亮色主题下 `.play-icon` 阴影可能不够明显
2. **歌词面板全屏模式下关闭按钮** — 应添加 hover 旋转动画

### 低优先级
1. **Codex 推理级别** — 从 `xhigh` 降到 `high`（Yuan 说要自己做）
2. **浏览器缓存** — 修改 JS 后需强制刷新（加 `?v=NUM` 查询参数）

---

## 十、调试技巧

### 本地启动
```bash
cd /Users/yuantao/Documents/codex/Terry\ Music
python3 app.py
# 访问 http://localhost:5050
```

### 测试歌词生成
```bash
curl -X POST http://localhost:5050/api/lyrics \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "prompt=electronic+pop&lyrics_idea=love+and+loss&voice_id=English_Trustworthy_Man&lyrics_language=English"
```

### 测试音色预览
```bash
curl "http://localhost:5050/api/voice/preview?voice_id=Chinese%20(Mandarin)_Reliable_Executive" -o preview.mp3
```

### 查看服务器日志
```bash
tail -f /tmp/terry_music.log
```

---

## 十一、文件结构

```
Terry Music/
├── app.py                  # 主应用（Flask 单文件）
├── app.py.v4.2            # 备份（v4.2）
├── app.py.v4.3            # 备份（v4.3）
├── app.py.v4.3.ui         # 备份（含UI改进）
├── music.db                # SQLite 数据库
├── CODEX_REVIEW_TASKS.md  # Codex 待执行任务列表
├── CODEX_TASKS_TODO.md    # Codex 任务汇总
├── PLAYER_UI_REDESIGN.md  # 播放器 UI 设计方案
├── PROJECT_HANDOVER.md    # 本交接文档
└── .git/                  # Git 仓库
```

---

## 十二、其他 Agent 注意事项

1. **Model**：本项目 AI 调用全部使用 MiniMax API（`MINIMAX_API_KEY`），不是 OpenAI API
2. **Codex CLI**：使用 ChatGPT Plus 账号登录，配置文件 `~/.codex/config.toml`
3. **Render**：约 2 分钟自动部署，push 到 main 分支后等待
4. **浏览器 Profile**：`openclaw` profile 用于自动化测试
5. **Bug 修复优先**：音色预览和歌词生成是核心功能，任何 regression 需优先处理

---

## 十三、Codex 补充

> Codex 于 2026-04-20 对 `app.py`、测试文件、部署配置和历史任务文档做了源码级核对。以下内容是对 MiniMax Agent 初稿的校正和补充，不删除原文，供后续 Agent 接手时优先参考。

### 13.1 关键校正（以当前 `app.py` 为准）

1. **当前主程序不是 Flask**：`app.py` 使用 `http.server.BaseHTTPRequestHandler` + `ThreadingHTTPServer`，核心类是 `MusicHandler`，入口是 `main()`。没有 `@app.route`、Flask app 对象，也没有 Flask request/session 体系。
2. **当前没有 SQLite Schema**：没有 `music.db` 初始化逻辑，也没有 `jobs`/`voice_samples`/`cloned_voices` 表。状态持久化是 JSON 文件：
   - `JOBS_DB = OUTPUT_DIR / "jobs.json"`
   - `DRAFTS_DB = OUTPUT_DIR / "drafts.json"`
   - 默认 `OUTPUT_DIR = ~/terry_music_outputs`
3. **部署方式是 Render Docker，不是 Python 3.11 buildpack**：`render.yaml` 配置 `runtime: docker`，`Dockerfile` 基于 `node:20-bookworm-slim`，安装 `python3` 和 `mmx-cli@1.0.7`，然后执行 `python3 app.py`。
4. **MiniMax Key 变量名兼容两套**：当前代码优先读取 `MINIMAX_API_KEY`，也接受 `MINIMAX_API_TOKEN`；Render 配置里写的是 `MINIMAX_API_TOKEN`。`MINIMAX_GROUP_ID`、`SECRET_KEY` 当前主程序未使用。
5. **请求体格式不是 form-urlencoded**：`read_json_body()` 只接受 JSON object。文档里的 `/api/lyrics` form-urlencoded curl 示例应改为 `Content-Type: application/json`。
6. **音乐生成 API 不是 `/api/generate`**：当前前端创建任务走 `POST /api/jobs`；声纹唱歌/翻唱任务走 `POST /api/jobs/voice`。不存在 `/api/generate`。
7. **当前不存在 `/api/tts`、`/api/song-title`、`/api/voice/clone/status`**：实际有 `POST /api/voice/sing`、`GET /api/admin/jobs`、`/api/drafts/<id>` 和 `/download/<id>`。
8. **音色数量不是硬编码 303**：`GET /api/voice` 运行时调用 `mmx speech voices --output json` 获取真实列表；失败时 fallback 到 `DEFAULT_SYSTEM_VOICES`（当前 23 个）。前端 `VOICE_LANG_GROUPS` 当前显式分组 17 个语种，顶部 UI 语言菜单 `LANG_LABELS` 支持 27 个界面语言标签。
9. **语音/音乐模型名需区分**：
   - 音乐生成：通过 `mmx music generate`，具体模型由 mmx CLI/后端服务处理。
   - 声音翻唱 fallback：`mmx music cover`。
   - TTS 试听：直接调 MiniMax `/v1/t2a_v2`，默认 `speech-2.8-hd`。
   - 声纹克隆：`/v1/files/upload` + `/v1/voice_clone`，clone payload 里使用 `speech-2.8-hd`。
   - 声纹唱歌优先路径：`VOICE_CLONE_SINGING_ENDPOINT` 默认 `/v1/voice_clone_singing`，`VOICE_CLONE_SINGING_MODEL` 默认 `music-2.6`；不可用时降级到 `music cover`。

### 13.2 当前真实 API 端点

| 端点 | 方法 | 实现位置/函数 | 请求体 | 说明 |
|------|------|---------------|--------|------|
| `/` | GET | `do_GET` | - | 返回内嵌 `INDEX_HTML`，`Cache-Control: no-store` |
| `/admin` | GET | `do_GET` | - | 返回 `ADMIN_HTML` |
| `/api/health` | GET | `do_GET` | - | 返回 MiniMax/Admin/SMTP/draft/job timeout 配置状态 |
| `/api/jobs` | GET | `do_GET` | - | 按 `X-Client-Id` 返回当前浏览器的任务列表 |
| `/api/jobs/<id>` | GET | `do_GET` | - | 按 `X-Client-Id` 查询单个任务 |
| `/api/jobs` | POST | `do_POST` | JSON | 创建普通音乐任务，返回 `202 Accepted` 和 `{job}` |
| `/api/jobs/voice` | POST | `handle_jobs_voice()` | JSON | 创建声纹唱歌/翻唱任务，要求本地存在 `voice_wav_<client>.wav` |
| `/api/jobs/<id>` | DELETE | `do_DELETE` | - | 删除当前 `X-Client-Id` 拥有的任务 |
| `/api/admin/jobs` | GET | `do_GET` | query `key` 或 header `X-Admin-Key` | 管理员查看所有任务 |
| `/api/drafts/<draft_id>` | GET | `handle_get_draft()` | - | 获取草稿，未找到时返回 `{"draft": null}` |
| `/api/drafts/<draft_id>` | POST | `handle_save_draft()` | JSON | 保存草稿，经过 `clean_draft_payload()` 白名单过滤 |
| `/api/drafts/<draft_id>` | DELETE | `handle_delete_draft()` | - | 删除草稿 |
| `/api/lyrics` | POST | `handle_lyrics_request()` | JSON | 生成歌词；成功返回 `200 {"lyrics": ...}`，失败可能返回本地 fallback |
| `/api/voice` | GET | `handle_get_voices()` | - | 调 `mmx speech voices`，失败时返回 `DEFAULT_SYSTEM_VOICES` |
| `/api/voice/preview?voice_id=...` | GET | `handle_voice_preview()` | - | 使用 `/v1/t2a_v2` 合成短试听 MP3 |
| `/api/voice/clone` | POST | `handle_voice_clone()` | multipart/form-data | 接收 `audio`，保存 WAV，并调用 MiniMax voice clone |
| `/api/voice/sing` | POST | `handle_voice_sing()` | JSON | 声纹唱歌试听；优先 `/v1/voice_clone_singing`，失败则 `music cover` |
| `/download/<job_id>` | GET | `handle_download()` | query `client_id` 或 `admin_key` | 下载已完成任务 MP3，校验 owner 或 admin |

### 13.3 后端模块实现细节

**状态与清理**

- `JOBS` 和 `DRAFTS` 是进程内 dict，分别由 `JOBS_LOCK`、`DRAFTS_LOCK` 保护。
- `load_jobs()`/`save_jobs_locked()` 和 `load_drafts()`/`save_drafts_locked()` 将状态读写到 JSON。
- `sweep_jobs_locked()` 会把 `queued`/`running` 且超过 `JOB_TIMEOUT_SECONDS`（默认 900 秒）的任务标记为 `error`，并删除超过 `JOB_RETENTION_SECONDS`（默认 604800 秒）的 terminal job。
- `_job_cleanup_loop()` 每 60 秒后台清理一次；`GET /api/jobs`、`GET /api/admin/jobs`、创建任务前也会触发 sweep。

**普通音乐生成**

- `POST /api/jobs` 校验 `prompt`、`song_title`、`lyrics`、`lyrics_idea` 长度。
- vocal track 必须提供 `lyrics`、`lyrics_idea` 或开启 `lyrics_optimizer`；instrumental 只需要 `prompt`。
- 后台线程调用 `generate_music(job_id)`：
  - 没有成稿歌词但有 brief/optimizer 时调用 `generate_lyrics_from_text_model(job)`。
  - 没有歌名时优先 `generate_title_from_text_model(job, lyrics)`，失败后 `fallback_song_title(job, lyrics)`。
  - 调 `mmx music generate --prompt ... --out ... --non-interactive`，并把 `genre/mood/instruments/tempo/bpm/key/vocals/structure/references/avoid/use_case/extra` 通过 `build_music_option_args()` 转成 CLI 参数。
  - 输出文件名由 `download_file_name()` 和 `safe_name()` 生成，文件保存在 `OUTPUT_DIR`。

**歌词生成与防抄袭**

- `generate_lyrics_from_text_model(job, timeout=...)` 的真实流程：
  1. `lyrics_language` 显式值优先，其次由 `voice_id` 经 `_detect_lang_from_voice_id()` 推断，最后默认 English。
  2. 如果 `lyrics_idea` 的语言和目标音色语言不一致，先用 `_detect_text_language()` + `_translate_text()` 翻译。
  3. 用 MiniMax text chat 把 `lyrics_idea` 提取成 `themes_keywords`，要求 5-8 个抽象 theme anchors。
  4. 生成 prompt 只传音乐风格、theme anchors、`lyrics_extra` 和高级参数，不直接把原句作为歌词素材。
  5. `clean_generated_lyrics()` 去掉 ANSI、代码块和前缀。
  6. `validate_generated_lyrics()` 校验最少 `GENERATED_LYRICS_MIN_CHARS = 600` 字符，并用 `_find_copied_source_fragment()` 检测英文 6-10 词连续片段或 CJK 6 字连续片段是否来自用户输入。
- 长度常量当前为：`GENERATED_LYRICS_TARGET_MIN_CHARS = 800`、`GENERATED_LYRICS_TARGET_MAX_CHARS = 2000`、`GENERATED_LYRICS_MAX_CHARS = 2500`、`LYRICS_CHAR_LIMIT = 6000`。
- `fallback_generated_lyrics()` 是本地快速 fallback，不调用模型；最后通过 `_finalize_fallback_lyrics()` 补足长度并再次调用 `validate_generated_lyrics()`。

**歌名生成**

- `generate_title_from_text_model()` 是两阶段：先让模型生成 5 个候选，再让模型基于完整歌词打分选择 1 个。
- `clean_song_title()` 负责去前缀、去代码块、去 `.mp3`、限制中文 12 字或英文 12 词以内。
- `normalize_generated_song_title()` 会拒绝直接使用歌词第一句。
- `fallback_song_title()` 不再取第一句，改用 `_title_signals()` 从歌词/prompt/lyrics_idea 中提取中英文意象、情绪、动作词，再在 `_chinese_title_candidates()` 或 `_english_title_candidates()` 中挑选。

**声纹克隆与声纹唱歌**

- 前端录音使用 `MediaRecorder`，5 段，每段 `SEGMENT_DURATION = 5000` ms。
- `convertToWav(blob)` 将浏览器录到的 webm 解码成 16kHz/16-bit/mono WAV；`mergeAudioBlobs(blobs)` 合并多段 WAV PCM。
- `POST /api/voice/clone` 手动解析 multipart，保存 `voice_wav_<client_id[:16]>.wav`，再临时写 `voice_sample_<hex>.<suffix>` 上传 MiniMax。
- clone 返回给前端：`{"ok": true, "voice_id": ..., "expires_in_hours": 168, "voice_wav_path": ...}`。当前 UI 文案也是 7 天，不是 24 小时。
- 生成声纹歌曲时前端根据 `voiceSingingMode` 设置 `voice_mode`：
  - `voice_clone_singing`：后端先试 `/v1/voice_clone_singing`。
  - `cover` 或 direct singing 不可用：走 `generate_voice_cover_audio()`，即 `mmx music cover --audio-file <voice_wav>`。

### 13.4 前端结构与关键 JS

**HTML 结构**

- `#splash`：启动页，`enterApp()` 淡出。
- `.app > .app-header`：Logo、音效按钮 `#soundBtn`、主题按钮 `#themeBtn`、语言菜单 `#langBtn/#langMenu`。
- `.app-body > aside.sidebar`：`data-view="create/library/favorites/history"` 的导航。
- `#view-create`：主创作表单 `#jobForm`。
- 表单关键字段：
  - `#songTitle`
  - `#prompt`
  - `.template-btn[data-template]`
  - `#voicePickerScroll` + hidden `#vocals`
  - `#lyricsIdea`
  - `#lyricsExtra`
  - `#generateLyricsBtn`
  - `#lyrics`
  - `#instrumental`
  - `#lyricsOptimizer`
  - `#voiceRecordBtn/#voicePreviewBtn/#voicePreviewAudio/#voiceSingingMode`
  - 高级参数：`#genre/#mood/#instruments/#tempo/#bpm/#key/#structure/#references/#avoid`
  - `#email`
- 任务列表：`#jobs`，另有 `#library-list/#favorites-list/#history-list` 但当前点击这些 view 只触发 `loadJobs()`，没有独立渲染到对应列表。
- 底部播放器：`#player`、`#playerPlay`、`#playerBar`、`#playerLyricsToggle`、`#lyricsFullscreenBtn`。
- 歌词面板：`#lyricsPanel/#lyricsLines`。
- 录音弹窗：`#recModal/#recModalBody`。
- 全屏歌词弹窗由 JS 动态创建：`#lyricsFullscreenModal`。

**核心 JS 函数/对象**

- 通用：`t()`、`headers()`、`escapeHtml()`、`showToast()`、`setTheme()`、`toggleSound()`。
- 音效：`SoundSystem.init/play/toggle`，支持 `click/success/error/complete/startup/record`。
- 草稿：`collectPayload()`、`restorePayload()`、`saveDraftLocal()`、`saveDraftRemote()`、`saveDraftSoon()`、`loadDraft()`。
- 任务：`renderJobs()`、`loadJobs()`、`playJob()`、`deleteJob()`。
- 歌词：`setLyricsAssistMessage()`、`syncInstrumentalFields()`、`parseLyrics()`、`getLyricRows()`、`renderLyricsPanel()`、`currentLyricRowIndex()`、`updateLyricsProgress()`、`_parseTimestamps()`。
- 音色选择器：`_voiceGroupsFromCache()`、`_voiceGroupForId()`、`_voiceDisplayName()`、`_attachScrollSound()`、`_buildVoicePicker()`、`selectVoice()`、`playVoicePreview()`、`stopVoicePreview()`、`loadVoicePicker()`。
- 录音/克隆：`openVoiceRecorder()`、`closeVoiceRecorder()`、`showSegment()`、`showCountdownAndRecord()`、`startSegmentRecording()`、`startRecordingSegment()`、`convertToWav()`、`showReview()`、`showAllDone()`、`mergeAudioBlobs()`。
- 全屏歌词：`_openLyricsModal()`、`_syncLfmFromPlayer()`、`_updateLfmProgress()`、`_closeLyricsModal()`。

**CSS 变量**

- 暗色 `:root`：`--bg-primary`、`--bg-secondary`、`--bg-tertiary`、`--bg-elevated`、`--accent`、`--accent-hover`、`--accent-dim`、`--text-primary`、`--text-secondary`、`--text-muted`、`--border`、`--border-light`、`--danger`、`--warning`、`--gradient-green`、`--shadow-sm/md/lg`、`--radius-sm/md/lg`、`--transition`。
- 亮色 `[data-theme="light"]` 覆盖背景、文字、边框、warning/danger、shadow 等变量。
- 音色选择器关键布局：`.voice-picker-shell { grid-template-columns: 156px minmax(0, 1fr); }`，移动端切为上下布局。
- 歌词面板动画：`.lyrics-panel` 用 `opacity + translateY + scale`，`.lyrics-panel.open` 切换显示；关闭按钮 hover 已有 `rotate(90deg)`。
- 全屏歌词动画：`#lyricsFullscreenModal.open` 使用 `lfm-in`，从 `scale(1.05)` 到 `scale(1)`。

### 13.5 Bug 历史与解决方案（源码/任务文档核对）

| 历史问题 | 当前解决点 |
|---------|------------|
| 系统音色没有传给歌词 API | `collectPayload()` 已使用 `voice_id: clonedVoiceId || _selectedVoiceId || ""` |
| 界面语言影响歌词语种 | `selectVoice()` 设置 `_lyricsLanguage = group.lang`；`collectPayload()` 传 `lyrics_language: _lyricsLanguage || "auto"` |
| 歌词语言与音色不一致 | 后端 `generate_lyrics_from_text_model()` 使用 `lyrics_language`/`voice_id` 决定目标语言，并用 `_detect_text_language()` + `_translate_text()` 先翻译用户 brief |
| 歌词照搬用户 brief | 当前为“两阶段抽象主题 + 生成 prompt 强约束 + `validate_generated_lyrics()` 后端片段检测” |
| 歌词太短 | 常量强制最少 600 字符，目标 800-2000，最大 2500；`validate_generated_lyrics()` 会拒绝短结果 |
| 音色预览 fetch 被提前取消 | `playVoicePreview()` 先 `stopVoicePreview()`，再设置 `_voicePlayPending = playId`；`stopVoicePreview()` 注释明确不再清空 pending |
| 任务卡在 running | `JOB_TIMEOUT_SECONDS` 默认 900 秒，`sweep_jobs_locked()` 会将过期 queued/running 改为 error；后台清理线程每分钟运行 |
| 清空草稿后仍使用旧克隆声音 | `clearDraftBtn` 清理 `clonedVoiceId`、`voiceCloneExpires`、`terry_music_voice_*` localStorage、voice UI 和 `_selectedVoiceId` |
| 歌名直接取第一句 | `normalize_generated_song_title()` 拒绝第一句；`generate_title_from_text_model()` 和 `fallback_song_title()` 均基于主题/意象 |
| 高级参数无法展开 | 当前 `window.toggleAdvancedPanel()` + `#advancedToggle` click/keyboard 均存在，`.advanced-panel.open` 控制显示 |
| 播放器 UI/歌词面板粗糙 | 当前底部播放器、歌词 slide-up 面板、全屏歌词 modal、SVG 图标和时间同步均已重写 |

### 13.6 当前仍需处理/验证的 TODO

**高优先级**

1. **修正交接文档中的架构误导**：后续文档应统一称为 Python HTTP Server + 原生前端 + mmx CLI，而不是 Flask + SQLite。
2. **修复已有克隆声音时的 JS 初始化顺序风险**：`if (clonedVoiceId && voiceCloneExpires...)` 块会在 `let _activeVoiceLang` 声明前访问 `_activeVoiceLang`。只要 localStorage 里存在有效 `terry_music_voice_id` 和未过期时间，就可能触发 TDZ `ReferenceError`，导致后续脚本中断。建议把 `_cachedVoices/_voiceAudio/_voicePlayPending/_selectedVoiceId/_activeVoiceLang` 的声明提前到首次使用前。
3. **修复 `lyrics_extra` 草稿丢失**：`collectPayload()` 会收集 `lyrics_extra`，`/api/lyrics` 也会读取它，但 `clean_draft_payload()` 白名单没有 `lyrics_extra`，`restorePayload()` 也没有恢复 `#lyricsExtra`。刷新或跨窗口草稿恢复会丢失附加要求。
4. **修复音色 ID sanitizer 对全角括号的潜在破坏**：`VOICE_ID_SAFE_RE` 允许 `（）`，但当前 `handle_voice_preview()` 没有使用它，反而 `re.sub(r"[^A-Za-z0-9_()./\- ]", "", raw_voice_id)` 会移除全角括号。fallback 里有 `Cantonese_ProfessionalHost（F)`，可能被改成无效 ID。建议改为 `VOICE_ID_SAFE_RE.fullmatch(raw_voice_id)` 校验后原样传给 MiniMax。
5. **更新 curl/测试用例**：文档里的 form-urlencoded 示例是错的；`tests/test_api.py` 里部分期望也偏旧，例如 `/api/lyrics` 成功实际返回 200，不是 202。

**中优先级**

1. **修复声纹过期描述不一致**：当前后端返回 `expires_in_hours: 168`，UI 文案也是 7 天；初稿写 24 小时，应以源码为准或确认 MiniMax 真实 SLA 后统一。
2. **补齐 Library/Favorites/History view 的渲染**：导航能切换视图并 `loadJobs()`，但 `renderJobs()` 只渲染 `#jobs`，`#library-list/#favorites-list/#history-list` 目前不会显示独立内容。
3. **Fallback 歌词语言质量**：`fallback_generated_lyrics()` 的 Korean/Japanese 模板仍混入中文/韩文/日文片段，需改成纯目标语言，否则模型超时 fallback 时会影响用户体验。
4. **音色语言分组覆盖**：运行时可能返回 303 音色，但 `VOICE_LANG_GROUPS` 只有 17 个显式分组；其他语言会落到 `Other`。如果目标是 29 语种，应补齐 `VOICE_LANG_GROUPS` 和 `VOICE_PREVIEW_TEXTS`。
5. **前端字数显示**：当前生成歌词后只在 assist message 显示 `lyrics.value.length + "字"`，未在歌词框下方提供持续字数统计；历史 TODO 中的“歌词字数：823 字”尚未完整实现。
6. **音量控件未绑定交互**：`volumeFill` 初始 70%，`audioPlayer.volume = 0.7`，但 `.volume-slider` 没有 click/drag listener。
7. **`deleteJob()` 没有 UI 入口**：函数存在，但 `renderJobs()` 当前只输出 Play/Download/状态，未渲染 Delete 按钮。

**低优先级**

1. `use_case`、`extra` 有后端字段和 i18n 文案，但当前 HTML 没有对应 input；可决定恢复字段或从 payload/草稿白名单中删掉。
2. `VOICE_ID_SAFE_RE` 当前未使用，应使用或删除，避免安全意图与实际逻辑分离。
3. `startSegmentRecording()` 和 `startRecordingSegment()` 两个函数名很接近，后者只是前者 alias；可保留但需避免后续误改。
4. `music.db`、SQLite 表说明应从正式交接文档中移除，除非未来确实迁移到 SQLite。

### 13.7 建议的正确调试命令

```bash
cd /Users/yuantao/Documents/codex/Terry\ Music
python3 -m py_compile app.py
python3 app.py
```

```bash
curl http://localhost:5050/api/health
curl http://localhost:5050/api/voice
curl "http://localhost:5050/api/voice/preview?voice_id=English_Trustworthy_Man" -o preview.mp3
```

```bash
curl -X POST http://localhost:5050/api/lyrics \
  -H "Content-Type: application/json" \
  -H "X-Client-Id: test-client-12345" \
  -d '{"prompt":"electronic pop, bright hook","lyrics_idea":"a song about love and loss","voice_id":"English_Trustworthy_Man","lyrics_language":"English"}'
```

```bash
curl -X POST http://localhost:5050/api/jobs \
  -H "Content-Type: application/json" \
  -H "X-Client-Id: test-client-12345" \
  -d '{"prompt":"upbeat pop music","lyrics":"[Verse]\nHello world\n[Chorus]\nMusic speaks"}'
```
