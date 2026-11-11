#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Obsidian「博客」文件夹 -> Hugo page bundles

用法（本地）：
    python scripts/sync.py --vault "C:\\Users\\johnn\\Documents\\ObsidianLifeLog"
用法（CI）：
    python scripts/sync.py --vault vault

做的事：
  1. 只读 Obsidian 笔记库里的「博客」文件夹（默认 博客/，可用 --blog-folder 改）
  2. frontmatter 里 draft: true 的笔记跳过；_drafts/ 目录跳过
  3. 每篇笔记 -> content/posts/<slug>/index.md（目录名即 URL 的 slug）
     - slug 优先取 frontmatter 的 slug；否则用发布日期 YYYYMMDD（同日多篇自动 -2/-3）
  4. 把 [[双链]] / [[双链|别名]] 转成指向 /basePath/posts/<slug>/ 的普通 Markdown 链接
     指向「未发布笔记」的双链自动降级成纯文本，不会产生死链
  5. ![[图片.png]] 等附件复制进文章目录，并改写成 ![](图片.png)
  6. 清理 content/posts 下的旧产物，然后打印统计
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 基础工具

FM_RE = re.compile(r"^\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
WIKI_RE = re.compile(r"(!?)\[\[([^\[\]]+?)\]\]")
MD_IMG_RE = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)(\))")
COMMENT_RE = re.compile(r"%%.*?%%", re.S)
DATE_PREFIX_RE = re.compile(r"^\d{4}[-_.]\d{1,2}[-_.]\d{1,2}[-_ ]*")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".avif", ".ico"}
NOTE_EXTS = {".md", ".markdown"}


def eprint(*args):
    print(*args, file=sys.stderr)


def split_frontmatter(text: str):
    m = FM_RE.match(text)
    if not m:
        return {}, text
    return parse_yaml_subset(m.group(1)), text[m.end():]


def parse_yaml_subset(block: str) -> dict:
    """只解析 Obsidian 属性里常见的那点 YAML：标量 / 列表 / 简单块文本。"""
    data: dict = {}
    key = None
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        raw = line.rstrip()
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.lstrip().startswith("- ") and key is not None:
            item = _strip_comment(raw.lstrip()[2:]).strip()
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_unquote(item))
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_\u4e00-\u9fff-]+)\s*:\s*(.*)$", raw)
        if not m:
            i += 1
            continue
        key, value = m.group(1), _strip_comment(m.group(2)).strip()
        if value in ("|", ">", "|-", ">-"):
            # 块文本：收集后续缩进行
            buf = []
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                buf.append(lines[j].strip())
                j += 1
            data[key] = "\n".join(buf).strip()
            i = j
            continue
        if value == "":
            data[key] = ""
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [_unquote(x.strip()) for x in inner.split(",")] if inner else []
        else:
            data[key] = _unquote(value)
        i += 1
    return data


def _strip_comment(value: str) -> str:
    """去掉 YAML 行内注释：只有前面是空白（或行首）的 # 才算注释。"""
    in_single = in_double = False
    for i, ch in enumerate(value):
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == "#" and not in_single and not in_double and (i == 0 or value[i - 1] in " \t"):
            return value[:i].rstrip()
    return value.rstrip()


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def is_true(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "on", "1")
    return False


def yaml_str(v: str) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def yaml_list(items) -> str:
    return "[" + ", ".join(yaml_str(x) for x in items) + "]"


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def slugify(value: str) -> str:
    """英文/数字/汉字之外的字符转连字符；主要用于手写 slug 的清洗。"""
    value = value.strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]", "", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


def base_path_from_hugo_toml(config: Path, fallback: str = "/") -> str:
    """从 hugo.toml 的 baseURL 里取出路径部分，例如 /blog。"""
    if not config.exists():
        return fallback
    text = config.read_text(encoding="utf-8")
    m = re.search(r'^\s*baseURL\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        return fallback
    url = m.group(1).rstrip("/")
    m2 = re.match(r"^[a-zA-Z]+://[^/]+(/.*)?$", url)
    path = (m2.group(1) or "") if m2 else ""
    if not path:
        return "/"
    return path.rstrip("/") + "/"


# ---------------------------------------------------------------- 主流程

class BlogFolder:
    def __init__(self, vault: Path, folder: str):
        self.vault = vault
        self.root = vault / folder
        if not self.root.is_dir():
            sys.exit(f"[x] 找不到博客文件夹：{self.root}（先在 Obsidian 里建一个「{folder}」文件夹）")

    def notes(self):
        out = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root)
            if any(part.startswith((".", "_drafts")) for part in rel.parts):
                continue
            if p.suffix.lower() in NOTE_EXTS:
                out.append(p)
        return out


class Note:
    def __init__(self, path: Path, root: Path):
        self.path = path
        self.rel = path.relative_to(root).as_posix()
        self.text = path.read_text(encoding="utf-8")
        self.fm, self.body = split_frontmatter(self.text)
        self.title = str(self.fm.get("title") or "").strip() or DATE_PREFIX_RE.sub("", path.stem)
        self.date = (
            parse_date(self.fm.get("date"))
            or parse_date(self.fm.get("created"))
            or parse_date(self.fm.get("created_at"))
            or parse_date(self.fm.get("modified"))
            or datetime.fromtimestamp(path.stat().st_mtime)
        )
        self.draft = is_true(self.fm.get("draft"))
        self.explicit_slug = slugify(str(self.fm.get("slug") or ""))
        self.slug = ""
        self.bundle = None  # Path
        self.aliases = self._aliases()

    def _aliases(self):
        raw = self.fm.get("aliases") or self.fm.get("alias") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(x).strip().strip("[]") for x in raw if str(x).strip()]

    def keys(self):
        """这篇笔记可能被别人用哪些写法引用。"""
        stem = self.path.stem
        rel_no_ext = self.rel[: -len(self.path.suffix)] if self.path.suffix else self.rel
        out = {stem.lower(), rel_no_ext.lower()}
        out.add(DATE_PREFIX_RE.sub("", stem).lower())
        out.update(a.lower() for a in self.aliases)
        return {k for k in out if k}


def build_file_index(vault: Path, blog_root: Path) -> dict:
    """vault 里所有非 md 文件，按 basename 建索引（用于解析 ![[图片]]）。"""
    index: dict = {}
    for p in vault.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if p.suffix.lower() in NOTE_EXTS:
            continue
        index.setdefault(p.name.lower(), []).append(p)
    # 优先博客文件夹内部的附件
    for k, paths in index.items():
        paths.sort(key=lambda x: (blog_root not in x.parents, len(x.parts)))
    return index


def assign_slugs(notes, mode: str, map_path: Path | None):
    mapping = {}
    if map_path and map_path.exists():
        try:
            mapping = json.loads(map_path.read_text(encoding="utf-8"))
        except Exception:
            mapping = {}
    used = set()
    ordered = sorted(notes, key=lambda n: (n.date, n.rel.lower()))
    for n in ordered:
        if n.explicit_slug:
            n.slug = n.explicit_slug
        elif mode == "seq":
            if n.rel in mapping:
                n.slug = str(mapping[n.rel])
            else:
                next_id = max([int(v) for v in mapping.values() if str(v).isdigit()] or [0]) + 1
                n.slug = str(next_id)
                mapping[n.rel] = n.slug
        else:  # date 模式
            n.slug = n.date.strftime("%Y%m%d")
        base, n_slug = n.slug, n.slug
        i = 2
        while n_slug.lower() in used:
            n_slug = f"{base}-{i}"
            i += 1
        n.slug = n_slug
        used.add(n_slug.lower())
        mapping.setdefault(n.rel, n.slug)
    if map_path and mode == "seq":
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def convert(
    body: str,
    note: Note,
    note_url: dict,
    file_index: dict,
    bundle: Path,
    base_path: str,
    stats: dict,
) -> str:
    body = COMMENT_RE.sub("", body)

    def resolve_note(target: str):
        key = target.strip().lstrip("./").replace("\\", "/")
        key = re.sub(r"\.(md|markdown)$", "", key, flags=re.I)
        for cand in (key.lower(), key.split("/")[-1].lower()):
            if cand in note_url:
                return note_url[cand]
        return None

    def copy_asset(name: str) -> str | None:
        candidates = file_index.get(name.lower())
        if not candidates:
            return None
        src = candidates[0]
        dest = bundle / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        stats["assets"] += 1
        return src.name

    def repl_wiki(m):
        bang, inner = m.group(1), m.group(2)
        parts = inner.split("|", 1)
        target = parts[0].strip()
        alias = parts[1].strip() if len(parts) > 1 else ""
        stem = target.split("#", 1)[0].strip()
        fragment = target[len(stem) + 1:].strip() if "#" in target else ""
        suffix = f"#{fragment.lstrip('^')}" if fragment else ""

        if bang:  # 嵌入 ![[...]]
            ext = Path(stem).suffix.lower()
            if ext in IMAGE_EXTS or (not ext and stem.lower() in file_index):
                name = copy_asset(Path(stem).name)
                if not name:
                    stats["missing_assets"] += 1
                    eprint(f"  [warn] 找不到附件 {stem}（{note.rel}）")
                    return alias or stem
                if alias.isdigit():
                    return f'<img src="{name}" width="{alias}" alt="">'
                return f"![]({name})"
            url = resolve_note(stem)
            if url:
                label = alias or stem
                stats["embeds"] += 1
                return f"[{label}]({url}{suffix})"
            stats["unresolved"] += 1
            return alias or stem

        url = resolve_note(stem)
        if url:
            stats["links"] += 1
            return f"[{alias or stem}]({url}{suffix})"
        stats["unresolved"] += 1
        return alias or stem

    body = WIKI_RE.sub(repl_wiki, body)

    # Obsidian 里的 ![](attachments/a.png) / ![](a.png) 也把文件搬进文章目录
    def repl_md_img(m):
        src = m.group(2)
        if src.startswith(("http://", "https://", "/", "data:")):
            return m.group(0)
        name = copy_asset(Path(src).name)
        if not name:
            return m.group(0)
        return f"![]({name})"

    body = MD_IMG_RE.sub(repl_md_img, body)
    return body.strip() + "\n"


LIST_KEYS = ("tags", "categories", "keywords")
TEXT_KEYS = ("description", "summary", "subtitle", "author", "authorlink", "license",
             "featuredimage", "featuredimagepreview")
RAW_KEYS = ("toc", "math", "hiddenFromSearch", "weight", "linkToMarkdown")
PASSTHROUGH_KEYS = LIST_KEYS + TEXT_KEYS + RAW_KEYS


def copy_featured(extra: dict, bundle: Path, file_index: dict, stats: dict):
    """封面图（featuredimage）也要搬进文章目录，否则站点上 404。"""
    for key in ("featuredimage", "featuredimagepreview"):
        val = extra.get(key)
        if not val:
            continue
        s = str(val).strip()
        if s.startswith(("http://", "https://", "/", "data:")):
            continue
        candidates = file_index.get(Path(s).name.lower())
        if not candidates:
            eprint(f"  [warn] 找不到封面图 {s}，已忽略（也可以写完整 http 地址）")
            extra.pop(key, None)
            continue
        src = candidates[0]
        if not (bundle / src.name).exists():
            shutil.copy2(src, bundle / src.name)
        extra[key] = src.name
        stats["assets"] += 1


def render_frontmatter(note: Note, extra: dict) -> str:
    lines = ["---", f"title: {yaml_str(note.title)}"]
    if note.date:
        lines.append("date: " + note.date.strftime("%Y-%m-%dT%H:%M:%S"))
    lines.append(f"slug: {yaml_str(note.slug)}")
    lines.append("draft: false")
    for key in LIST_KEYS + TEXT_KEYS:
        val = extra.get(key)
        if val is None or val == "" or val == []:
            continue
        if key in LIST_KEYS:
            if isinstance(val, str):
                val = re.split(r"[,，]", val)
            items = [str(x).strip() for x in val if str(x).strip()]
            if items:
                lines.append(f"{key}: {yaml_list(items)}")
        else:
            lines.append(f"{key}: {yaml_str(val)}")
    for key in RAW_KEYS:
        if extra.get(key) not in (None, ""):
            lines.append(f"{key}: {extra[key]}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True, help="Obsidian 笔记库根目录")
    ap.add_argument("--blog-folder", default="博客", help="笔记库里作为博客的文件夹名（默认 博客）")
    ap.add_argument("--out", default="content/posts", help="Hugo 输出目录")
    ap.add_argument("--config", default="hugo.toml", help="用于读取 baseURL 路径的 Hugo 配置文件")
    ap.add_argument("--mode", choices=["date", "seq"], default="date", help="slug 模式：date=YYYYMMDD，seq=自增序号")
    ap.add_argument("--map", default=".slug-map.json", help="seq 模式的映射文件")
    args = ap.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        sys.exit(f"[x] 笔记库不存在：{vault}")
    folder = BlogFolder(vault, args.blog_folder)
    out_dir = Path(args.out)
    base_path = base_path_from_hugo_toml(Path(args.config))

    notes = [Note(p, folder.root) for p in folder.notes()]
    drafts = [n for n in notes if n.draft]
    published = [n for n in notes if not n.draft]
    if not published:
        eprint(f"[!] 「{args.blog_folder}」里没有可发布的笔记（{len(drafts)} 篇是 draft），本次只清空产物。")

    assign_slugs(published, args.mode, Path(args.map) if args.mode == "seq" else None)

    # 清理旧产物（下划线开头的手写文件，比如 _index.md，保留）
    if out_dir.exists():
        for child in out_dir.iterdir():
            if child.name.startswith("_"):
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 笔记名 -> URL
    note_url = {}
    for n in published:
        url = f"{base_path}posts/{n.slug}/"
        for k in n.keys():
            note_url.setdefault(k, url)

    file_index = build_file_index(vault, folder.root)
    stats = {"links": 0, "embeds": 0, "unresolved": 0, "assets": 0, "missing_assets": 0}

    for n in published:
        bundle = out_dir / n.slug
        bundle.mkdir(parents=True, exist_ok=True)
        body = convert(n.body, n, note_url, file_index, bundle, base_path, stats)
        extra = {k: n.fm.get(k) for k in PASSTHROUGH_KEYS if n.fm.get(k) not in (None, "", [])}
        copy_featured(extra, bundle, file_index, stats)
        (bundle / "index.md").write_text(render_frontmatter(n, extra) + "\n" + body, encoding="utf-8")

    print(f"[√] 发布 {len(published)} 篇，跳过 draft {len(drafts)} 篇 -> {out_dir}")
    print(f"    双链 {stats['links']} 个，嵌入 {stats['embeds']} 个，未发布目标 {stats['unresolved']} 个，"
          f"附件 {stats['assets']} 个，缺失附件 {stats['missing_assets']} 个")
    if stats["missing_assets"]:
        eprint("[!] 有附件没找到，检查是不是设在「博客」文件夹之外的附件目录，或名字写错了。")
    print(f"    slug 模式 {args.mode}，basePath {base_path}")


if __name__ == "__main__":
    main()
