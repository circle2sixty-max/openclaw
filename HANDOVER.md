# Music Speaks — AI 交接文档

> 供后续 AI 助手阅读，快速上手项目
> 最后更新: 2026-04-16 18:00 BST
> 当前开发周期: 2026-03 至 04-16

---

## 项目简介

**Music Speaks** 是一个 AI 音乐生成私有工具。用户输入歌词/风格描述 → AI 生成 MP3 歌曲文件。支持普通音乐生成和声纹音乐生成（用定制音色演绎歌曲）。

**核心技术**: Python 单文件 Web 应用 + MiniMax mmx CLI（音乐/语音/文本生成）+ 浏览器 MediaRecorder API（声纹录制）

**所有权**: 陶源 (Yuan Tao) | 英国 eBay 电商

---

## 快速启动

```bash
cd /Users/yuantao/Documents/codex/Terry\ Music
python3 app.py
# 打开 http://localhost:5050
```

---

## 核心文件

| 文件 | 作用 |
|------|------|
| `app.py` | **唯一代码文件**。包含所有后端逻辑 + 前端 HTML/JS/CSS（约 2200 行）|
| `start.sh` | 本地启动脚本 |
| `Dockerfile` | Render 部署用 Docker 镜像 |
| `render.yaml` | Render 服务配置 |
| `PROGRESS_REPORT.md` | 本次开发周期详细进度报告 |

---

## 关键代码位置

### 后端

| 功能 | 文件位置 |
|------|---------|
| MiniMax API 调用 | `app.py:1296` `_call_minimax_api()` |
| 声纹克隆 | `app.py:1338` `clone_voice()` |
| TTS 合成 | `app.py:1372` `synthesize_speech()` |
| 歌词生成 | `app.py:1457` `generate_lyrics_from_text_model()` |
| 普通音乐生成 | `app.py:1596` `generate_music()` |
| 声纹音乐生成 | `app.py:1645` `generate_music_with_voice()` |
| HTTP 服务 + 路由 | `app.py:1698` `MusicHandler` |
| 主入口 | `app.py:2184` `main()` |

### 前端（均在 app.py 的 HTML 字符串中）

| 功能 | 位置 |
|------|------|
| 声纹录制 UI 逻辑 | `app.py:771` `openVoiceRecorder()` |
| 分段录音 UI | `app.py:790` `showSegment(idx)` |
| WAV 转换 | `app.py:871` `convertToWav()` |
| WAV 合并 | `app.py:962` `mergeAudioBlobs()` |
| 录音引导文案（EN）| `app.py:727` `VOICE_SEGMENTS_EN` |
| 录音引导文案（ZH）| `app.py:734` `VOICE_SEGMENTS_ZH` |
| 录音文本内容（EN）| `app.py:741` `SEGMENT_SCRIPTS_EN` |
| 录音文本内容（ZH）| `app.py:748` `SEGMENT_SCRIPTS_ZH` |
| 每段时长 | `app.py:762` `SEGMENT_DURATION = 5000` |

---

## 已完成功能（本次开发周期）

### 1. 歌词生成（✅ 完成）
- `lyrics_generation` 模型（MiniMax 专用歌词模型）
- 切换自通用 text chat 模型
- 代码: `generate_lyrics_from_text_model()` 第 1457 行

### 2. 声纹录制（✅ 完成）
- 5 段引导录音 × 5 秒/段 = 25 秒总时长
- 浏览器 MediaRecorder API 录音
- 自动转换为 WAV 格式（AudioContext）
- 合并多段 PCM 数据为单一 WAV 文件
- 录音内容: 第 741-754 行（中英各 5 段引导文案）

### 3. 声纹克隆（✅ 完成）
- 上传 WAV 到 MiniMax `/v1/files/upload`
- 调用 `/v1/voice_clone` 获取 `voice_id`
- 支持 mp3/m4a/wav，最大 20MB
- `voice_id` 格式: `user_XXXXXXXX-XXXX-XXXX`

### 4. 声纹音乐生成（✅ 完成，但有限制）
- `generate_music_with_voice()` 使用 `mmx music cover --audio-file voice.wav`
- **当前方式**: WAV 作为音色参考（风格迁移）
- **限制**: MiniMax 不支持用克隆 voice_id 做 TTS 后再生成歌曲
- 无法实现真正的"声纹唱歌"，只能做音色迁移

### 5. TTS 合成（⚠️ 部分可用）
- 模型: `speech-2.8-hd`（TTS API 唯一支持的模型）
- output_format: `hex`（不支持 mp3）
- 内置声音可正常合成（如 `female-tianmei`）
- **不兼容**: 克隆出来的 voice_id 在 TTS API 中返回 "voice id not exist"

---

## 已知未解决问题

### 问题 1: 克隆声纹不能用于 TTS
**现象**: `synthesize_speech()` 调用失败，返回 `{'base_resp': {'status_code': 2054, 'status_msg': 'voice id not exist'}}`

**原因**: MiniMax voice_clone API 和 TTS API 使用不同的 voice_id 命名空间，克隆出来的 ID 不能用于 TTS 合成。

**影响**: 声纹预览功能不可用（预览依赖 TTS）

**可能的解决方向**:
1. 放弃预览功能，或改为用内置声音预览
2. 调研 MiniMax 是否有其他 API 支持"克隆声音唱歌"
3. 考虑换用支持此场景的其他 API（如 ElevenLabs, Resemble AI）

### 问题 2: 真声唱歌不可行
**用户期望**: 用自己克隆的声音唱出歌词

**实际效果**: `music cover --audio-file voice.wav` 是风格迁移，不是语音合成。生成的音乐使用的是音色参考，不是真正的唱歌。

**用户已接受此限制**: 见对话记录

---

## 重要配置

### 环境变量

| 变量 | 值/来源 | 说明 |
|------|---------|------|
| `MINIMAX_API_KEY` | 环境变量或 `~/Downloads/minimax_music_tool.py` | MiniMax API 密钥 |
| `OUTPUT_DIR` | `~/terry_music_outputs` | 生成文件存放目录 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `5050` | 监听端口 |
| `ADMIN_KEY` | 从 API_KEY 派生或环境变量 | 管理面板密钥 |

### 本地服务
- **launchctl label**: `com.terry.music.local`
- **端口**: 5050
- **进程管理**: launchd（Mac 系统服务）

---

## Git 使用

```bash
cd /Users/yuantao/Documents/codex/Terry\ Music

# 查看状态
git status

# 查看未推送提交
git log origin/main..HEAD

# 推送到远程
git push origin main

# 查看差异
git diff
```

**重要**: 本次开发周期的所有提交（8bdb359 之前）均未推送到远程。

---

## 数据库/状态文件

| 文件 | 路径 | 内容 |
|------|------|------|
| jobs.json | `~/terry_music_outputs/jobs.json` | 所有任务记录（含历史） |
| drafts.json | `~/terry_music_outputs/drafts.json` | 用户草稿 |
| voice_wav_*.wav | `~/terry_music_outputs/` | 声纹录音文件 |
| terry_music_*.mp3 | `~/terry_music_outputs/` | 生成的歌曲文件 |

---

## 与用户记忆宫殿的连接

| Wing | 路径 | 相关性 |
|------|------|--------|
| 🔧 技术工具间 | `~/.openclaw/workspace-circle-claw-feishu/memory/wings/技术工具间/` | Music Speaks 属于技术工具间 |
| 飞书机器人 | 同上 | Music Speaks 可能有集成需求 |

**热缓存**: `~/.openclaw/workspace-circle-claw-feishu/memory/PALACE.md`

---

## 给接任 AI 的话

1. **先读 `PROGRESS_REPORT.md`** — 详细的技术细节和决策过程都在那里
2. **app.py 是唯一代码文件** — 2200 行单文件，不要被这个规模吓到
3. **MiniMax API 限制是真实的** — voice_clone 和 TTS 不兼容，这不是 bug，是 API 设计问题
4. **用户陶源的主要需求是**: 本地可运行 + 功能完整 + 最终部署到 Render
5. **测试时用内置声音** — `female-tianmei` 可以正常 TTS 合成，用于验证流程
6. **commit 但不 push** — 陶源要求本地测试完成后才能部署线上

---

*交接文档 | 最后更新: 2026-04-16 18:00 BST*
