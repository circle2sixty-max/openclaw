# 🎵 Music Speaks 项目完整进度报告

**项目名称**: Music Speaks (Terry Music)
**项目目录**: `/Users/yuantao/Documents/codex/Terry Music/`
**技术栈**: Python (app.py) + MiniMax API + HTML/CSS/JS
**部署**: https://terry-music.onrender.com
**部署方式**: GitHub push → Render 自动部署

---

## 一、项目背景

Music Speaks 是一个 AI 音乐生成工具，用户可以：
1. 输入歌词提示或完整歌词
2. 选择音色（系统音色或克隆自己的声音）
3. 生成原创歌曲

核心功能：
- 歌词生成（AI 辅助创作）
- 声音克隆（用用户自己的声音唱歌）
- 歌曲合成（TTS 技术）

---

## 二、当前已完成的修复

### ✅ Bug 1: 语种切换失效
**问题**: 选择某个语种后无法切换到另一个语种
**根因**: `_buildVoicePicker()` 覆盖了用户刚设置的 `_activeVoiceLang`
**修复**: 代码逻辑优化，确保语种选择正确传递

### ✅ Bug 2: 音色预览报错
**问题**: 只有 Cantonese 可用，其他音色点击都报错
**根因**: `safe_voice_id = re.sub(r"[^a-zA-Z0-9_()/\- ]", "", voice_id)` 正则删除了合法字符
**修复**: 改为 `re.fullmatch` 校验，合法则原样传入

### ✅ Bug 3: 中文试听样本是英文
**问题**: 选择中文语种后，点击试听播放的是英文样本
**修复**: 添加 `VOICE_PREVIEW_TEXTS` 多语言映射（15种语言），根据语种返回对应语言样本

### ✅ Bug 4: 歌词滚动显示缺失
**问题**: 播放器有 lyrics 按钮但无功能
**修复**: 新增歌词模态框 + CSS 滚动 + `renderLyricsPanel()` 函数

### ✅ Bug 5: 播放器 UI 太丑
**问题**: 使用 emoji，视觉效果差
**修复**: 替换 emoji 为 SVG 图标，添加紫色渐变背景 `#667eea → #764ba2`

### ✅ Bug 6: 歌词长度不足
**问题**: 歌词生成质量低，长度不够支撑 3-4 分钟歌曲
**修复**:
- `--max-tokens` 从 1600 改为 3200
- 歌词截断从 3500 改为 6000
- 前端 textarea maxlength 从 3500 改为 6000

### ✅ Bug 7: 歌词主题不贯穿全歌
**问题**: 用户输入的歌词提示被"堆"在一个段落，其他部分空洞无特色
**修复**: Prompt 重写，要求主题贯穿到 Verse1/Verse2/Pre-Chorus/Chorus/Bridge 每个段落

### ✅ Bug 8: Cantonese 歌词文字错误
**问题**: Cantonese 音色选了但歌词是简体中文，TTS 发普通话音
**修复**:
- Cantonese 歌词 prompt 明确要求使用**繁体+口语化写法**
- 列出粤语特有词汇：知唔知/我唔知/佢話/幾時等
- 禁止简体词：你知道/今天/为什么等

### ✅ Bug 9: 系统音色不传 lyrics 生成 API
**问题**: `collectPayload()` 只传 `clonedVoiceId`，系统音色 `_selectedVoiceId` 丢失
**修复**: `collectPayload` 现在同时支持 `clonedVoiceId`（克隆）和 `_selectedVoiceId`（系统音色）

### ✅ Bug 10: 生产环境音色列表只有 21 个
**问题**: MiniMax API 超时，fallback 到 21 个备用音色
**修复**: `VOICE_LIST_TIMEOUT` 从 3 秒增加到 15 秒

---

## 三、当前工作流程

```
用户选择音色（如 Cantonese）
      ↓
用户输入歌词提示
      ↓
点击"生成歌词" → voice_id 传给 /api/lyrics
      ↓
generate_lyrics_from_text_model 根据 voice_id 检测语种
      ↓
Cantonese → 生成繁体+口语化粤语歌词 ✅
English → 生成英文歌词
其他语种 → 生成对应语言歌词
      ↓
用户编辑歌词
      ↓
点击"生成音乐" → synthesize_voice_clone_singing
      ↓
MiniMax TTS 合成歌曲
```

---

## 四、关键代码位置

| 功能 | 代码位置 |
|------|---------|
| 音色列表获取 | `handle_get_voices()` 第 4139 行 |
| 音色预览 | `handle_voice_preview()` 第 4155 行 |
| 歌词生成 | `generate_lyrics_from_text_model()` 第 3348 行 |
| 歌词请求处理 | `handle_lyrics_request()` 第 4032 行 |
| Cantonese Prompt | 第 3367 行附近 |
| Fallback 歌词 | `fallback_generated_lyrics()` 第 3399 行 |
| 语种检测 | `_detect_lang_from_voice_id()` 第 104 行 |
| 前端音色选择 | `voiceLangList` 第 1947 行 |
| Payload 收集 | `collectPayload()` 第 1283 行 |

---

## 五、API Key

```
MINIMAX_API_KEY = "sk-cp-e-c1jHE917mtWGYeUk1SHXwuYXtonU_B2fQeoUCfm2EKVzHdxaqx0DCQjfEBrj7mLiM1pU-mSqEMAQkgTdaoTwdmYgwHjsIwQAfrOontP6qasH6S4oCS-kM"
```

---

## 六、版本历史

| 版本 | 日期 | 内容 |
|------|------|------|
| v3.1 | 2026-04-19 | 修复 voice_id 正则、多语言预览 |
| v3.2 | 2026-04-19 | 歌词长度、播放器UI、歌词滚动、bug修复汇总 |
| v4.0 | 2026-04-19 | 备份点：语种联动歌词生成修改前 |
| 当前 | 2026-04-19 | voice_id 传递修复、Cantonese 繁体+口语化 |

---

## 七、待完成功能

### 🔴 高优先级
1. **界面语言切换**: 目前界面只有中文/英文，需要根据用户选择的语言动态切换整个界面
2. **音色列表双语显示**: 音色名称根据界面语言显示对应翻译

### 🟡 中优先级
1. **韩语支持**: 界面和歌词生成都支持韩语
2. **更多音色**: 探索 MiniMax 是否有更多可用音色

---

## 八、部署信息

- **GitHub**: https://github.com/circle2sixty-max/openclaw
- **生产环境**: https://terry-music.onrender.com
- **本地服务**: http://localhost:5050
- **部署方式**: Git push → Render 自动部署（约1-2分钟）

---

## 九、经验总结

### 部署成功的关键
1. 每次修改后立即 `commit + push`
2. Render 监听 main 分支，有 push 自动部署
3. 代码修改比 Claude Code 执行更稳定（不被 SIGKILL）

### 修改代码流程
```bash
cd /Users/yuantao/Documents/codex/Terry Music

# 1. 语法检查
python3 -m py_compile app.py

# 2. 备份
cp app.py app.py.v{X}.{description}

# 3. 提交并部署
git add app.py && git commit -m "描述" && git push origin main
```

### Mac 内存问题
- Mac 内存紧张时 Claude Code/Codex 会被 SIGKILL
- 关闭 Lark、WeChat 等不必要的应用可以释放内存
- 直接修改代码比通过 Claude Code 更可靠

---

*报告生成时间: 2026-04-19 16:49 BST*
*生成者: OpenClaw (circle-claw-feishu)*
