# Music Speaks — 项目进度报告
> 生成时间: 2026-04-16 18:00 (BST/London)
> 报告范围: 2026-03 至 04-16

---

## 一、项目基本信息

| 项目 | 内容 |
|------|------|
| **名称** | Music Speaks（原 Terry Music） |
| **类型** | AI 音乐生成 Web 应用 |
| **技术栈** | Python 3 单文件后端 + Vanilla JS 前端 + MiniMax mmx CLI |
| **部署** | Render (Docker) + 本地开发服务器 |
| **GitHub** | https://github.com/circle2sixty-max/openclaw |
| **主要分支** | main |
| **本地端口** | 5050 |
| **Render 地址** | https://terry-music.onrender.com（推测） |

---

## 二、核心功能现状

### 2.1 已完成功能

| 功能 | 状态 | 说明 |
|------|------|------|
| 歌词生成 | ✅ 可用 | `lyrics_generation` 模型，专为歌词设计 |
| 歌曲标题生成 | ✅ 可用 | 调用 text model 生成简短标题 |
| 纯音乐生成 | ✅ 可用 | `--instrumental` 参数 |
| 有歌词音乐生成 | ✅ 可用 | `--lyrics` 参数传入完整歌词 |
| 声纹录制（5段×5秒） | ✅ 可用 | 浏览器 MediaRecorder → WAV 合并 |
| 声纹克隆 | ✅ 可用 | `voice_clone` API，返回 `voice_id` |
| 声纹音乐生成 | ✅ 可用 | `music cover --audio-file voice.wav`（风格迁移） |
| 邮件通知 | ⚠️ 可选 | SMTP 配置后可发送下载链接 |
| 草稿保存 | ✅ 可用 | 浏览器 localStorage + 服务端 drafts.json |
| 多语言界面 | ✅ 可用 | 中/英双语，i18n 系统 |
| Admin 管理面板 | ✅ 可用 | `/admin?key=ADMIN_KEY` |

### 2.2 有问题的功能

| 功能 | 状态 | 根本原因 |
|------|------|---------|
| TTS 预览（声纹） | ❌ 不可用 | MiniMax TTS API 的 voice_id 与 voice_clone API 不兼容 |
| 声纹"真唱歌" | ❌ 不可行 | MiniMax API 不支持用克隆 voice_id 做 TTS 后再生成歌曲 |

---

## 三、当前代码状态（app.py）

### 3.1 文件路径
```
/Users/yuantao/Documents/codex/Terry Music/app.py
```

### 3.2 关键函数

| 函数 | 行号 | 功能 |
|------|------|------|
| `clone_voice()` | 1338 | 上传 WAV + 调用 voice_clone API，返回 voice_id |
| `synthesize_speech()` | 1372 | TTS 合成（模型: speech-2.8-hd，格式: hex）|
| `generate_lyrics_from_text_model()` | 1457 | 调用 lyrics_generation 模型 |
| `generate_music()` | 1596 | 普通音乐生成（`mmx music generate`）|
| `generate_music_with_voice()` | 1645 | 声纹音乐生成（`mmx music cover --audio-file`）|
| `_call_minimax_api()` | 1296 | MiniMax REST API 底层调用（含 multipart 上传）|
| `_minimax_headers()` | 1289 | API 请求头 |

### 3.3 前端关键 JS

| 变量/函数 | 行号 | 说明 |
|-----------|------|------|
| `VOICE_SEGMENTS_EN/ZH` | 727/734 | 5段录音引导文案 |
| `SEGMENT_SCRIPTS_EN/ZH` | 741/748 | 5段录音内容文本 |
| `SEGMENT_DURATION` | 762 | 每段 5000ms（5秒）|
| `openVoiceRecorder()` | 771 | 打开录音模态框 |
| `showSegment(idx)` | 790 | 显示第 idx 段录音 UI |
| `convertToWav(blob)` | 871 | webm/opus → WAV 转换（AudioContext）|
| `mergeAudioBlobs(blobs)` | 962 | 合并多段 WAV PCM 数据 |

---

## 四、已知 API 限制（MiniMax）

### 4.1 TTS API
- **支持的模型**: `speech-2.8-hd`（唯一可用）
- **不支持的模型**: `speech-hd`, `speech-02-hd`, `speech-02`, `speech-01`, `speech` 等
- **output_format**: 只支持 `hex` 和 `url`，不支持 `mp3`
- **voice_id 兼容性**: voice_clone 返回的 voice_id 无法用于 TTS API（返回 "voice id not exist"）

### 4.2 voice_clone API
- **模型**: `speech-2.8-hd`
- **支持的音频格式**: mp3, m4a, wav
- **最大文件大小**: 20MB
- **voice_id 用途**: 仅限于 music cover 的 `--audio-file` 参考，不能用于 TTS

### 4.3 music cover 命令
```
mmx music cover --prompt "风格描述" --audio-file voice.wav --lyrics "歌词" --out output.mp3
```
- WAV 文件作为音色参考（风格迁移），不是真声唱歌
- 支持 --lyrics 参数

---

## 五、最近 Git 提交（2026-04-01 后）

| 提交 | 时间 | 内容 |
|------|------|------|
| `8bdb359` | 04-16 | fix: TTS model speech-2.8-hd + hex; revert voice music to WAV style migration |
| `4e3daa2` | 04-16 | Use recorded WAV directly as music cover reference audio |
| `cc603cb` | 04-16 | Fix TTS output_format: use string 'mp3' not object |
| `a96672f` | 04-16 | Fix file_id: also check upload_resp.file.file_id |
| `b0095e6` | 04-16 | Add voice clone music generation pipeline |
| `830f073` | 04-16 | Fix file_id extraction: check top-level and nested data |
| `46d12b4` | 04-16 | Record audio as WAV, merge PCM properly, reduce to 5s segments |
| `484a06f` | 04-16 | Add Content-Type: multipart/form-data with boundary |
| `c2c3f79` | 04-16 | Fix multipart parsing and reduce recording to 5 segments × 10s |
| `7198a5c` | 04-16 | Add lyrics_generation model switch and voice clone feature |

**未推送到远程**: 上述所有提交仅在本地，未 `git push`

---

## 六、本地文件结构

```
/Users/yuantao/Documents/codex/Terry Music/
├── app.py                          # 主应用（单文件 ~2200 行）
├── app.py.backup_20260416_105716  # 备份文件
├── minimax_music_tool.py           # 旧版工具（已废弃）
├── minimax_music_tool.py.backup_20260416_105716
├── start.sh                        # 本地启动脚本
├── render.yaml                     # Render 部署配置
├── Dockerfile                      # Docker 镜像定义
├── README.md                       # 项目文档
└── .git/                           # Git 仓库

~/.openclaw/workspace-circle-claw-feishu/
├── memory/PALACE.md               # 热缓存
├── memory/wings/                   # 各 wing 笔记
└── memory/tunnels/                # 跨主题 tunnel

~/terry_music_outputs/
├── jobs.json                       # 任务记录
├── drafts.json                     # 草稿记录
├── voice_wav_*.wav                 # 声纹录音文件
└── terry_music_*.mp3               # 生成的歌曲
```

---

## 七、当前任务队列状态

```
Job ID                  状态      voice_id                    备注
wrnHcE7PWG93JWQ1      completed  NONE                        普通音乐生成（钟楼）
3kE0F5LCbRqPpyZn      completed  user_4d7286ab-183a-4e      声纹音乐（风格迁移）
rTtIA_6QHQB9UVbU      error      user_eb14fc89-a434-4       TTS voice_id 不存在
MJmDGFxlR8ZOOVLA      error      user_eb14fc89-a434-4       TTS voice_id 不存在
5ps_v_bJYQCYRTF1      completed  user_eb14fc89-a434-4       声纹音乐（2MB, 17:35生成）
```

---

## 八、未解决问题

### 8.1 TTS 预览功能
声纹克隆成功后，预览按钮调用 `synthesize_speech(lyrics, voice_id)` 但 MiniMax TTS API 不识别克隆出来的 voice_id。

**临时方案**: 预览功能只能使用内置声音（如 `female-tianmei`），不能使用克隆声纹。

### 8.2 真声唱歌
用户期望：克隆声音 → TTS 念歌词 → 用 TTS 结果生成歌曲（真声唱歌）。

**结论**: MiniMax API 不支持此流程。`music cover --audio-file` 使用的是音色迁移（风格参考），不是语音合成。可实现的最佳效果是"用你的声音特点来演绎歌曲"，不是真正的"唱歌"。

---

## 九、本地服务管理

### 启动
```bash
cd "/Users/yuantao/Documents/codex/Terry Music"
python3 app.py
# 或通过 launchctl
launchctl load /Library/LaunchAgents/com.terry.music.local.plist
```

### 重启
```bash
kill $(lsof -ti :5050)
cd "/Users/yuantao/Documents/codex/Terry Music"
python3 app.py &
```

### 查看日志
```bash
tail -f /tmp/claude-501/-Users-yuantao/717e2081-06b9-4cfc-92a4-33949165c01a/tasks/*.output
```

---

*报告生成时间: 2026-04-16 18:00 BST*
