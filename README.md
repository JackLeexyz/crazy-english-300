# 李阳疯狂英语 300 句 · 互动朗读网站

把《李阳疯狂英语 300 句》整理文档做成一个**单文件、可离线、可分享**的互动学习网站。

🔗 **在线体验**：https://crazy-english-300.app.workbuddy.host/

---

## 功能

### 句型例句
- 286 个句型条目 / **603 条例句**，每条含中文释义
- 句子和句型都可点击 🔊 用标准美音朗读，语速可调（0.5x ~ 1.3x）
- **鼠标悬停任意单词**弹出释义气泡：音标 + 本句释义（置顶高亮）+ 其他释义 + 单词朗读
- 句型标题（如 According to）里的单词同样支持悬停查词

### 重点词汇
- 从例句中自动筛选 **450 个重点词汇**（柯林斯星级 + 牛津核心 + 考纲标签加权）
- 三种排序：字母 A-Z（带字母导航）/ 考频星级 / 句中出现顺序
- **词根词缀拆分**：`act·ion`、`im·port·ant`（119 个，橙色点 + 🧩 中文构词解析）
- **音节拆分**：`fa·ther`、`com·pa·ny`（126 个，灰色点，按发音拆）
- 🃏 闪卡复习：优先抽未掌握词汇，支持键盘操作

### 学习管理
- 句子 / 词汇勾选"已学"，进度存本地（localStorage）
- 双进度条：句型例句 x/603、重点词汇 x/450
- 🔥 连续学习天数 + 今日计数
- 25/50/75/100% 里程碑彩带庆祝
- 已学 / 未学筛选、🎲 随机学一句、朗读后自动标记

---

## 目录结构

```
index.html      构建产物（单文件网页，可直接双击打开 / 部署）
template.html   页面模板（HTML/CSS/JS 骨架，含 __DATA__ 等占位符）
build.py        构建脚本：解析数据 + 生成词典 → 注入模板 → 输出 index.html
data.json       从 docx 解析出的句型与例句数据
wordroot.txt    ECDICT 词根词缀库（用于拆分）
scripts/        辅助脚本（下载词典等）
```

## 重新构建

`index.html` 是构建产物，改了 `template.html` 或数据后需要重新生成：

```bash
# 1. 下载词典源文件（约 63MB，用于单词释义与拆分）
bash scripts/fetch_dict.sh

# 2. 安装依赖
pip install pyphen python-docx

# 3. 构建
python build.py
```

构建脚本会读取 `data.json`（句子）+ `ecdict.csv`（词典）+ `wordroot.txt`（词根），
生成 `DICT / CTX / VOCAB / SEG` 数据并注入 `template.html`，输出 `index.html`。

> `ecdict.csv` 体积较大，已通过 .gitignore 排除，不入库。

## 数据来源

- 句子内容：《李阳疯狂英语 300 句》OCR 整理版（docx）
- 词典：[ECDICT](https://github.com/skywind3000/ECDICT) 开源英汉词典（MIT）
- 词根词缀：ECDICT 的 `wordroot.txt`
- 音节拆分：[pyphen](https://github.com/Kozea/Pyphen)（OpenOffice 英文连字符词典）
- 发音：浏览器原生 SpeechSynthesis，无需联网

## 部署

纯静态页面，零后端依赖：

```bash
# 本地预览
open index.html

# 或起个静态服务
python3 -m http.server 8000
```

放到服务器（Nginx / 对象存储静态托管 / GitHub Pages）直接上传 `index.html` 即可。

## 已知限制

- 学习进度存在浏览器 localStorage，**各设备独立不互通**（如需多端同步，需加后端账号体系）
- 少量 OCR 残留错词（如 `didnt`、`bejing`）词典查不到，悬停会提示"未收录"
- 语音效果取决于浏览器：推荐 Chrome / Edge
