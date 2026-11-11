# blog

用 Obsidian 写笔记，自动发布成 Hugo 博客（GitHub Pages）。

只发布笔记库里的 `博客` 文件夹；笔记库中其它文件夹、其它文件不会进入这个仓库，也不会出现在站点上。

## 它是怎么工作的

```
私有笔记库 JohnnyZinc/ObsidianLifeLog           公开博客仓库 JohnnyZinc/blog
┌──────────────────────────────┐               ┌─────────────────────────────────────┐
│ 博客/                        │  sparse       │ scripts/sync.py                     │
│   2026-11-10 我的第一篇.md   │  checkout     │   ↓ 只读 vault/博客                 │
│   附件/图片.png              │  ──────────>  │ content/posts/20261110/index.md     │
│ 日记/ (不发布)               │  只拉这一个   │   + 图片、双链改写                  │
│ 其它笔记… (不发布)           │  文件夹       │   ↓ hugo build                      │
└──────────────────────────────┘               │ public/ → GitHub Pages              │
                                               └─────────────────────────────────────┘
```

- 触发：每天 06:00（北京时间）定时跑一次；也可以在 Actions 页面点 **Run workflow** 立刻发布；
  推送到本仓库 `main` 也会重新构建。
- 笔记库是私有仓库，它的 push 不会触发本仓库，所以「写完想马上发」= 手动点一下 Run workflow。
- 构建机用 `sparse-checkout` 只拉 `博客` 一个文件夹，其余笔记不会被下载（但 `VAULT_TOKEN` 本身有读全库权限，
  这是 GitHub 机制决定的）。

## 一次性配置（已经做过的部分可以跳过）

1. **建仓库**：GitHub 上建公开仓库 `JohnnyZinc/blog`（本仓库）。
2. **给博客仓库一个只读笔记库的 token**
   - GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
   - Repository access：Only select repositories → `ObsidianLifeLog`
   - Permissions：**Contents: Read-only**（其余全部 No access）
   - 复制 token，到博客仓库 Settings → Secrets and variables → Actions → New repository secret，
     名字必须是 **`VAULT_TOKEN`**
3. **开启 Pages**：博客仓库 Settings → Pages → Source 选 **GitHub Actions**（不是 Deploy from a branch）。
4. 打开 `https://johnnyzinc.github.io/blog/`，能看到文章就成功了。

## 日常使用

1. 在 Obsidian 的 `博客` 文件夹里正常写笔记，爱怎么用双链就怎么用。
2. obsidian-git 会把改动 commit + push 到私有笔记库。
3. 想发布：博客仓库 → Actions → 发布博客 → Run workflow。不着急就等第二天早上的定时构建。

### 发布规则

| 你想做的事 | 怎么做 |
| --- | --- |
| 不发布某篇 | frontmatter 里写 `draft: true`，或把文件放进 `博客/_drafts/` |
| 指定 URL | frontmatter 里写 `slug: my-post`（小写英文+连字符；**发布后别再改**，否则旧链接 404） |
| 不指定 URL | 默认用发布日期：`2026-11-10` → `/posts/20261110/`；同一天多篇自动 `-2`、`-3` |
| 自定义标题日期标签 | frontmatter 的 `title` / `date` / `tags` / `categories` / `description` 都会带到 Hugo |
| 封面图 / 副标题 / 目录 | `featuredimage`（文件名，图放库内任意位置）、`subtitle`、`toc: false` |
| 图片、PDF 等附件 | 直接 `![[图片.png]]` 或 `![](附件/图片.png)`，会复制进文章目录；文件放哪都行（库内任意位置都能找到） |
| 双链 | `[[另一篇笔记]]` / `[[另一篇笔记|显示文字]]` 会自动变成指向那篇文章的链接；指向未发布笔记的双链会降级成纯文本，不会有死链 |

### frontmatter 字段速查

笔记库里 `Template模板/Blog Template.md` 是同一份清单的模板版，新建文章直接复制它。

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `title` | 是 | 标题；不填用文件名 |
| `date` | 是 | 发布日期，也决定默认 URL；不填用文件创建时间 |
| `tags` | 是 | 标签列表，如 `[折腾, 工具]`；也接受 `折腾, 工具` 字符串 |
| `draft` | 是 | 模板默认 `true`；改成 `false` 才发布 |
| `slug` | 否 | 自定义 URL 尾巴，留空用发布日期，**发布后别改** |
| `description` | 否 | 摘要，用于列表页和搜索引擎 |
| `subtitle` | 否 | 副标题 |
| `categories` | 否 | 分类列表 |
| `keywords` | 否 | SEO 关键词 |
| `author` / `license` | 否 | 覆盖作者名 / 版权说明（HTML 片段） |
| `featuredimage` | 否 | 封面图文件名，会自动复制进文章目录 |
| `toc` / `math` | 否 | 显示目录 / 加载 KaTeX 数学公式 |
| `hiddenFromSearch` | 否 | `true` 表示站内搜索不收录 |
| `weight` | 否 | 排序权重，数字小的靠前，一般不用填 |
| `linkToMarkdown` | 否 | 是否显示「查看 Markdown 原文」链接 |
| `aliases` | 否 | 只用于 Obsidian 内 `[[别名]]` 解析，不输出到站点 |

写入规则：`sync.py` 只挑上面这些键透传给 Hugo，其余键（`created`、`cssclass`、`cover` 之类）会被忽略，不会污染站点。

### 已知限制

- Obsidian 的 **callout**（`> [!note]`）会渲染成普通引用块，主题不认这个语法。
- 嵌入整篇笔记（`![[某篇笔记]]`）不会展开内容，只会变成指向它的链接。
- `%%注释%%` 会被删掉；`==高亮==` 不会渲染。
- Excalidraw 画板（`.excalidraw`）和 Canvas（`.canvas`）不能直接嵌入，先在 Obsidian 里导出成 PNG 再 `![[图片.png]]`。
- 文章 URL 由发布时的 slug 决定，所以改文件名不影响 URL，改 `slug` 才会（并且会导致旧链接 404）。

## 本地预览

```powershell
# 1. 准备工具（只需一次）
#    Hugo extended 0.169.0 -> .tools/hugo/hugo.exe
#    Dart Sass            -> .tools/sass/dart-sass/sass.bat

# 2. 从笔记库同步 + 本地起服务
$env:PATH = "$PWD\..\.tools\sass\dart-sass;$env:PATH"
python scripts/sync.py --vault "$HOME\Documents\ObsidianLifeLog"
..\.tools\hugo\hugo.exe server
```

自测（造一个假笔记库，跑完整条流水线，断言产物）：`python scripts/selftest.py`

## 目录说明

```
hugo.toml                 站点配置（baseURL、菜单、主题参数、permalink）
scripts/sync.py           Obsidian 文件夹 -> content/posts/<slug>/ 的转换脚本
scripts/selftest.py       端到端自测
content/posts/            由 sync.py 生成，不入库（只保留 _index.md）
content/about.md          「关于」页
.github/workflows/        publish.yml：同步 → 构建 → 部署 Pages
themes/LoveIt             git submodule，钉在某个 commit 上
```

## 想换域名 / 换主题

- **换域名**：改 `hugo.toml` 的 `baseURL`，再到 Pages 设置里填自定义域名。
  `sync.py` 会自动从这个 `baseURL` 里取路径前缀，所以双链不会写错。
- **换主题**：把 `themes/` 换掉、改 `hugo.toml` 的 `theme`。注意新主题的配置项不一样，
  `hugo.toml` 里的 `[params]` 大部分是 LoveIt 专用的，需要按新主题重写。
