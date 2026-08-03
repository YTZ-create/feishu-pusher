# 投资评论自动推送

自动抓取 BlackRock、HSBC 和 J.P. Morgan 的投资评论，通过飞书群机器人推送到群聊。

## 数据源

- **贝莱德每周投资评论**: [BlackRock Global Weekly Commentary](https://www.blackrock.com/cn/global-weekly-commentary)
- **汇丰最新市场动态**: [HSBC Latest Market Views](https://www.hsbc.com.cn/content/hsbc/cn/zh_cn/wealth/insights.html/#Latest-views)
- **摩根大通财富洞察**: [J.P. Morgan Wealth Management Insights](https://www.jpmorgan.com/wealth-management/wealth-partners/insights)

## 快速开始

### 1. 安装依赖

```bash
pip install requests beautifulsoup4 lxml playwright
playwright install chromium
```

### 2. 配置

```bash
cp config.example.json config.json
```

编辑 `config.json`，填入你的飞书群机器人 Webhook URL。

### 3. 运行

```bash
python fetcher.py
```

### 4. 设置定时任务（Windows）

以管理员身份运行：

```powershell
powershell -ExecutionPolicy Bypass -File setup_task.ps1
```

每天上午 10:00 自动检查并推送新文章。

## 工作原理

- **BlackRock**: HTTP 请求 + BeautifulSoup 解析 HTML meta 标签
- **HSBC / J.P. Morgan**: Playwright 无头 Chromium 渲染 SPA 页面后提取内容
- **去重**: 基于 MD5(source:title:date) 的 `seen.json` 记录已推送文章
- **推送**: 飞书互动消息卡片（有新文章蓝色卡片，无新文章灰色每日汇总）

## 文件说明

| 文件 | 说明 |
|------|------|
| `fetcher.py` | 主脚本 |
| `config.json` | 配置文件（已 gitignore） |
| `config.example.json` | 配置文件模板 |
| `seen.json` | 推送记录（已 gitignore） |
| `setup_task.ps1` | Windows 定时任务创建脚本 |
