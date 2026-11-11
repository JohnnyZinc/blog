#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端自测：造一个假 Obsidian 库 -> 跑 sync.py -> hugo build -> 断言产物 -> 还原现场

    python scripts/selftest.py            # 跑完自动清理
    python scripts/selftest.py --keep     # 保留 .selftest/ 里的中间产物方便排查

检查点：slug 规则、同日冲突 -2、draft 跳过、双链重写（含未发布降级）、
附件复制（含库内其它目录的附件）、注释删除、baseURL 子路径、Hugo 构建产物。
"""

import argparse
import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # blog/
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)

failures = []
checks = 0


def check(name, cond, detail=""):
    global checks
    checks += 1
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(f"{name} {detail}".strip())
        print(f"  FAIL {name} {detail}")


def find_hugo():
    env = os.environ.get("HUGO")
    if env and Path(env).exists():
        return env
    found = shutil.which("hugo")
    if found:
        return found
    local = ROOT.parent / ".tools" / "hugo" / "hugo.exe"
    if local.exists():
        return str(local)
    sys.exit("[x] 找不到 hugo，可设 HUGO 环境变量指向 hugo.exe")


def find_sass_dir():
    """LoveIt 用 SCSS，Hugo 0.128+ 需要 Dart Sass（Hugo 扩展版只带 libsass 不够）。"""
    env = os.environ.get("SASS_DIR")
    if env and Path(env).is_dir():
        return env
    for cand in (ROOT.parent / ".tools" / "sass" / "dart-sass",
                 ROOT / ".tools" / "sass" / "dart-sass"):
        if (cand / "sass.bat").exists() or (cand / "sass").exists():
            return str(cand)
    found = shutil.which("sass")
    if found:
        return str(Path(found).parent)
    return None


def build_fixture(vault: Path):
    blog = vault / "博客"
    for d in ["附件", "系列", "_drafts"]:
        (blog / d).mkdir(parents=True, exist_ok=True)
    (vault / "日记").mkdir(parents=True, exist_ok=True)
    (vault / "attachments").mkdir(parents=True, exist_ok=True)

    def w(rel, text):
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    w("博客/2026-11-10 我的第一篇.md", """---
title: 我的第一篇
date: 2026-11-10
tags: [随笔, 折腾]
categories:
  - 博客
description: 这是描述
subtitle: 一个副标题
featuredimage: 封面.png
draft: false
---
# 我的第一篇

正文第一段，链接到 [[2026-11-12 第二篇]]，也带别名 [[2026-11-12 第二篇|第二篇笔记]]。
链接一个还没写的 [[还没写]]。

嵌入图片 ![[图片.png|300]]，普通图片 ![](附件/普通.png)，库外附件 ![[外部图片.png]]。

%%这段注释应该消失%%

- 列表项
""")

    w("博客/2026-11-10 同一天.md", """---
title: 同一天发的另一篇
date: 2026-11-10
---
同一天的第二篇，测 slug 冲突。
""")

    w("博客/2026-11-12 第二篇.md", """---
title: 第二篇
date: 2026-11-12
tags: 单个标签
---
第二篇正文，回链 [[我的第一篇]]。
""")

    w("博客/系列/2026-11-13 子文件夹里的一篇.md", """---
title: 子文件夹里的一篇
date: 2026-11-13
---
子文件夹里的笔记也应该被发布。
""")

    w("博客/草稿笔记.md", """---
title: 草稿笔记
draft: true
---
不该发布。
""")

    w("博客/_drafts/_drafts里的.md", """---
title: 下划线草稿
---
不该发布。
""")

    w("日记/2026-11-11 日记.md", "# 私人日记\n\n不该发布。\n")

    (blog / "附件" / "图片.png").write_bytes(PNG_1PX)
    (blog / "附件" / "普通.png").write_bytes(PNG_1PX)
    (blog / "附件" / "封面.png").write_bytes(PNG_1PX)
    (vault / "attachments" / "外部图片.png").write_bytes(PNG_1PX)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    hugo = find_hugo()
    tmp = ROOT / ".selftest"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    vault = tmp / "vault"
    build_fixture(vault)

    # 备份真实 content/posts（自测要用真的站点配置构建）
    posts = ROOT / "content" / "posts"
    backup = tmp / "backup-posts"
    if posts.exists():
        shutil.copytree(posts, backup)
    public = tmp / "public"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    sass_dir = find_sass_dir()
    if sass_dir:
        env["PATH"] = sass_dir + os.pathsep + env.get("PATH", "")
    else:
        print("[!] 没找到 Dart Sass（sass），LoveIt 的 SCSS 编译会失败；"
              "设 SASS_DIR 环境变量或把 sass 放进 PATH")

    try:
        print("[1/4] 运行 sync.py")
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "sync.py"),
             "--vault", str(vault), "--blog-folder", "博客",
             "--out", str(posts), "--config", str(ROOT / "hugo.toml")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env,
        )
        print("   ", (r.stdout or "").strip().replace("\n", "\n    "))
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            sys.exit("[x] sync.py 失败")

        print("[2/4] 检查生成的 bundle")
        bundles = sorted(p.name for p in posts.iterdir() if p.is_dir())
        check("slug 用发布日期", "20261110" in bundles, str(bundles))
        check("同日冲突加 -2", "20261110-2" in bundles, str(bundles))
        check("按 frontmatter 日期", "20261112" in bundles, str(bundles))
        check("子文件夹笔记也发布", "20261113" in bundles, str(bundles))
        check("只有 4 个 bundle", len(bundles) == 4, str(bundles))

        all_md = "\n".join(p.read_text(encoding="utf-8") for p in posts.rglob("index.md"))
        check("draft:true 跳过", "草稿笔记" not in all_md)
        check("_drafts/ 跳过", "下划线草稿" not in all_md)
        check("博客文件夹外的笔记不发布", "私人日记" not in all_md)
        check("注释被删除", "这段注释应该消失" not in all_md)
        check("没有残留双链", "[[" not in all_md)
        check("双链 -> 绝对链接",
              all_md.count("/blog/posts/20261112/") >= 2, "应为 2 处（裸链接 + 别名链接）")
        check("别名保留", "第二篇笔记" in all_md)
        check("未发布目标降级为纯文本", "还没写" in all_md and "[还没写](" not in all_md)
        check("回链到第一篇", "/blog/posts/20261110-2/" in all_md or "/blog/posts/20261110/" in all_md)
        check("frontmatter 有 draft: false", "draft: false" in all_md)
        check("tags 保留为列表", 'tags: ["随笔", "折腾"]' in all_md, all_md[:300])
        check("description 保留", 'description: "这是描述"' in all_md)
        check("行内 tags 字符串也能用", 'tags: ["单个标签"]' in all_md)
        check("subtitle 透传", 'subtitle: "一个副标题"' in all_md)

        bundle_first = posts / "20261110-2"
        check("图片复制进 bundle", (bundle_first / "图片.png").exists())
        check("普通图片也复制", (bundle_first / "普通.png").exists())
        check("库外目录附件也能找到", (bundle_first / "外部图片.png").exists())
        check("封面图复制进 bundle", (bundle_first / "封面.png").exists())
        first_md = (bundle_first / "index.md").read_text(encoding="utf-8")
        check("featuredimage 改成文件名", 'featuredimage: "封面.png"' in first_md, first_md[:400])
        check("数字别名转 width", 'width="300"' in first_md)
        check("图片引用改成文件名", "![](普通.png)" in first_md, first_md[-400:])

        print("[3/4] hugo build")
        r = subprocess.run([hugo, "--quiet", "--destination", str(public)],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        if r.returncode != 0:
            print(r.stdout)
            print(r.stderr, file=sys.stderr)
        check("hugo 构建成功", r.returncode == 0, (r.stderr or "")[-500:])

        print("[4/4] 检查构建产物")
        idx = public / "posts" / "20261110-2" / "index.html"
        check("URL = /posts/<slug>/", idx.exists(), str(idx))
        if idx.exists():
            html = idx.read_text(encoding="utf-8")
            check("页面里链接带 basePath", "/blog/posts/20261112/" in html)
            check("图片被打进站点", "图片.png" in html)
            check("副标题渲染出来", "一个副标题" in html)
        check("列表页存在", (public / "posts" / "index.html").exists()
              or (public / "posts" / "page" / "1" / "index.html").exists())
        check("Markdown 输出格式", (public / "posts" / "20261110-2" / "index.md").exists())
    finally:
        if posts.exists():
            shutil.rmtree(posts)
        if backup.exists():
            shutil.copytree(backup, posts)
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"[x] {len(failures)}/{checks} 项失败：")
        for f in failures:
            print("   -", f)
        sys.exit(1)
    print(f"[√] {checks}/{checks} 项全部通过")


if __name__ == "__main__":
    main()
