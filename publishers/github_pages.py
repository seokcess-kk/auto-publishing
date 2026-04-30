"""
GitHub Pages 발행 Publisher
- _posts/ 에 Markdown 파일 생성
- 로컬 빌드 (Python markdown) → gh-pages 브랜치 push
- Actions 없이 정적 HTML 서빙

참조: 00.Old_Source/github_pages/github_pages_naverdatalab_itemscout_coopang/
"""
import os
import re
import subprocess
import shutil
import tempfile
from datetime import datetime

import markdown

from common.logger import log
from .base import Publisher, PostResult


# ─── HTML 템플릿 ──────────────────────────────────────────────────────────────

_PAGE_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ max-width: 800px; margin: 2rem auto; padding: 0 1rem;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         line-height: 1.6; color: #333; }}
  h1 {{ border-bottom: 1px solid #eee; padding-bottom: .3em; }}
  img {{ max-width: 100%; height: auto; }}
  a {{ color: #0366d6; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .meta {{ color: #666; font-size: .9em; margin-bottom: 1.5em; }}
  .tags span {{ background: #f0f0f0; padding: 2px 8px; border-radius: 3px;
                margin-right: 4px; font-size: .85em; }}
  nav {{ margin: 2rem 0; }}
  nav a {{ margin-right: 1rem; }}
  footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #eee;
            font-size: .85em; color: #999; }}
</style>
</head>
<body>
<nav><a href="/">Home</a></nav>
{body}
<footer>&copy; {year} {site_title}</footer>
</body>
</html>
"""

_INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{site_title}</title>
<style>
  body {{ max-width: 800px; margin: 2rem auto; padding: 0 1rem;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         line-height: 1.6; color: #333; }}
  h1 {{ border-bottom: 1px solid #eee; padding-bottom: .3em; }}
  .post-list {{ list-style: none; padding: 0; }}
  .post-list li {{ margin-bottom: 1.5em; }}
  .post-list a {{ font-size: 1.2em; color: #0366d6; text-decoration: none; }}
  .post-list a:hover {{ text-decoration: underline; }}
  .post-date {{ color: #666; font-size: .9em; }}
  .tagline {{ color: #666; }}
  footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #eee;
            font-size: .85em; color: #999; }}
</style>
</head>
<body>
<h1>{site_title}</h1>
<p class="tagline">{site_description}</p>
<ul class="post-list">
{post_links}
</ul>
<footer>&copy; {year} {site_title}</footer>
</body>
</html>
"""


class GitHubPagesPublisher(Publisher):
    """GitHub Pages 발행기 — 로컬 빌드 + gh-pages push.

    _posts/ 에 Markdown 생성 → Python markdown으로 HTML 변환
    → gh-pages 브랜치에 정적 파일 push.
    """

    def __init__(self, repo_path: str, author: str = "Moon",
                 site_title: str = "", site_description: str = "",
                 site_url: str = "", git_user: str = "",
                 git_email: str = ""):
        self.repo_path = repo_path
        self.posts_dir = os.path.join(repo_path, "_posts")
        self.author = author
        self.site_title = site_title
        self.site_description = site_description
        self.site_url = site_url
        self.git_user = git_user or author
        self.git_email = git_email or "noreply@users.noreply.github.com"

    def login(self) -> bool:
        """git 레포 접근 가능 여부 확인."""
        if not os.path.isdir(self.repo_path):
            log(f"GitHub Pages 레포 없음: {self.repo_path}", "error")
            return False
        if not os.path.isdir(os.path.join(self.repo_path, ".git")):
            log(f"git 레포가 아닙니다: {self.repo_path}", "error")
            return False
        os.makedirs(self.posts_dir, exist_ok=True)
        log(f"GitHub Pages 레포 확인: {self.repo_path}", "ok")
        return True

    def _run_git(self, *args, cwd=None):
        return subprocess.run(
            ["git"] + list(args),
            cwd=cwd or self.repo_path,
            capture_output=True, text=True, timeout=30,
        )

    # ─── Markdown 포스트 생성 ─────────────────────────────────────────────

    def post(self, title: str, content: str,
             tags: list = None, category: str = "",
             image_url: str = "", **kwargs) -> PostResult:
        """Markdown 저장 → HTML 빌드 → gh-pages push.

        kwargs:
            keyword:   검색 키워드 (파일명에 사용)
            slug:      슬러그 (없으면 자동 생성)
            auto_push: git push 자동 실행 여부 (기본 True)
        """
        keyword = kwargs.get("keyword", "")
        auto_push = kwargs.get("auto_push", True)

        log(f"GitHub Pages 발행: {title}", "step")

        # Front Matter
        tag_list = tags or []
        tag_str = ", ".join(tag_list)
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S +0900")

        front_matter = (
            f'---\n'
            f'title: "{title}"\n'
            f'date: {date_str}\n'
            f'author: {self.author}\n'
        )
        if category:
            front_matter += f'categories: [{category}]\n'
        if tag_list:
            front_matter += f'tags: [{tag_str}]\n'
        if image_url:
            front_matter += f'image: {image_url}\n'
        front_matter += '---\n\n'

        post_content = front_matter
        if image_url:
            post_content += f"![{title}]({image_url})\n\n"
        post_content += content

        # 파일명
        date_prefix = datetime.now().strftime("%Y-%m-%d")
        safe_keyword = re.sub(r"[^\uAC00-\uD7A30-9a-zA-Z\s]", "", keyword)
        safe_slug = kwargs.get("slug", "")
        if not safe_slug:
            safe_slug = re.sub(r"[^\uAC00-\uD7A30-9a-zA-Z\s]", "",
                               title[:50])
        safe_slug = safe_slug.strip().replace(" ", "-")
        if safe_keyword:
            filename = f"{date_prefix}-{safe_keyword}-{safe_slug}.md"
        else:
            filename = f"{date_prefix}-{safe_slug}.md"
        filepath = os.path.join(self.posts_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(post_content)
            log(f"MD 파일 생성: {filename}", "ok")
        except Exception as e:
            log(f"MD 파일 생성 실패: {e}", "error")
            return PostResult(success=False, message=str(e))

        if auto_push:
            # main 브랜치에 커밋
            self._run_git("add", filepath)
            self._run_git(
                "-c", f"user.name={self.git_user}",
                "-c", f"user.email={self.git_email}",
                "commit", "-m", f"Add post: {title}",
            )
            self._run_git("push", "origin", "main")

            # 로컬 빌드 → gh-pages push
            ok = self.deploy()
            if not ok:
                return PostResult(success=False, message="gh-pages 배포 실패")

        # slug에서 날짜 접두사 제거
        slug = filename.rsplit(".", 1)[0]
        if len(slug) > 11 and slug[10] == "-":
            slug = slug[11:]

        post_url = f"{self.site_url}/posts/{slug}/"
        log(f"GitHub Pages 발행 완료: {post_url}", "ok")
        return PostResult(success=True, url=post_url, post_id=filename)

    # ─── 로컬 빌드 ───────────────────────────────────────────────────────

    def _parse_front_matter(self, raw: str):
        """YAML front matter 간단 파싱."""
        meta = {}
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                fm = parts[1].strip()
                body = parts[2].strip()
                for line in fm.split("\n"):
                    if ":" in line:
                        key, val = line.split(":", 1)
                        meta[key.strip()] = val.strip().strip('"').strip("'")
        return meta, body

    def _build_site(self) -> str:
        """_posts/ Markdown → HTML 변환, 임시 디렉토리에 정적 사이트 생성."""
        build_dir = tempfile.mkdtemp(prefix="ghpages_")

        # .nojekyll
        with open(os.path.join(build_dir, ".nojekyll"), "w") as f:
            f.write("")

        posts_out = os.path.join(build_dir, "posts")
        os.makedirs(posts_out, exist_ok=True)

        post_entries = []

        if os.path.isdir(self.posts_dir):
            for fname in sorted(os.listdir(self.posts_dir), reverse=True):
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(self.posts_dir, fname)
                with open(fpath, encoding="utf-8") as f:
                    raw = f.read()

                meta, body = self._parse_front_matter(raw)
                title = meta.get("title", fname.replace(".md", ""))
                date = meta.get("date", "")[:10]
                tags_raw = meta.get("tags", "")

                html_body = markdown.markdown(
                    body, extensions=["extra", "codehilite", "toc"]
                )

                # 태그 HTML
                tag_html = ""
                if tags_raw:
                    tag_list = [t.strip() for t in
                                tags_raw.strip("[]").split(",") if t.strip()]
                    if tag_list:
                        tag_html = '<div class="tags">' + "".join(
                            f"<span>{t}</span>" for t in tag_list
                        ) + "</div>"

                page_body = f"<h1>{title}</h1>\n"
                if date:
                    page_body += f'<div class="meta">{date}</div>\n'
                if tag_html:
                    page_body += tag_html + "\n"
                page_body += html_body

                page_html = _PAGE_TEMPLATE.format(
                    title=title, body=page_body,
                    year=datetime.now().year, site_title=self.site_title,
                )

                # slug
                slug = fname.rsplit(".", 1)[0]
                if len(slug) > 11 and slug[10] == "-":
                    slug = slug[11:]

                post_dir = os.path.join(posts_out, slug)
                os.makedirs(post_dir, exist_ok=True)
                with open(os.path.join(post_dir, "index.html"), "w",
                          encoding="utf-8") as f:
                    f.write(page_html)

                post_entries.append((date, title, slug))

        # index.html
        post_links = "\n".join(
            f'<li><span class="post-date">{date}</span><br>'
            f'<a href="/posts/{slug}/">{title}</a></li>'
            for date, title, slug in post_entries
        )
        index_html = _INDEX_TEMPLATE.format(
            site_title=self.site_title,
            site_description=self.site_description,
            post_links=post_links or "<li>아직 게시글이 없습니다.</li>",
            year=datetime.now().year,
        )
        with open(os.path.join(build_dir, "index.html"), "w",
                  encoding="utf-8") as f:
            f.write(index_html)

        return build_dir

    # ─── gh-pages 배포 ───────────────────────────────────────────────────

    def deploy(self) -> bool:
        """로컬 빌드 → gh-pages 브랜치 push."""
        log("GitHub Pages 로컬 빌드 & 배포", "step")
        build_dir = self._build_site()

        remote_url = self._run_git(
            "remote", "get-url", "origin"
        ).stdout.strip()

        # gh-pages 브랜치 존재 여부 (remote 포함)
        result = self._run_git("ls-remote", "--heads", "origin", "gh-pages")
        has_gh_pages = "gh-pages" in result.stdout

        work_dir = tempfile.mkdtemp(prefix="ghpages_deploy_")
        try:
            if has_gh_pages:
                subprocess.run(
                    ["git", "clone", "--branch", "gh-pages",
                     "--single-branch", "--depth", "1",
                     remote_url, work_dir],
                    capture_output=True, text=True, timeout=30,
                )
                for item in os.listdir(work_dir):
                    if item == ".git":
                        continue
                    path = os.path.join(work_dir, item)
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
            else:
                subprocess.run(
                    ["git", "init", work_dir],
                    capture_output=True, text=True,
                )
                subprocess.run(
                    ["git", "checkout", "--orphan", "gh-pages"],
                    cwd=work_dir, capture_output=True, text=True,
                )
                subprocess.run(
                    ["git", "remote", "add", "origin", remote_url],
                    cwd=work_dir, capture_output=True, text=True,
                )

            # 빌드 결과물 복사
            for item in os.listdir(build_dir):
                src = os.path.join(build_dir, item)
                dst = os.path.join(work_dir, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            subprocess.run(["git", "add", "-A"], cwd=work_dir,
                           capture_output=True, text=True)

            diff = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=work_dir, capture_output=True,
            )
            if diff.returncode == 0:
                log("변경사항 없음 — push 건너뜀", "info")
                return True

            subprocess.run(
                ["git", "-c", f"user.name={self.git_user}",
                 "-c", f"user.email={self.git_email}",
                 "commit", "-m",
                 f"Deploy: {datetime.now().strftime('%Y-%m-%d %H:%M')}"],
                cwd=work_dir, capture_output=True, text=True,
            )

            if has_gh_pages:
                push_result = subprocess.run(
                    ["git", "push", "origin", "gh-pages"],
                    cwd=work_dir, capture_output=True, text=True, timeout=30,
                )
            else:
                push_result = subprocess.run(
                    ["git", "push", "-u", "origin", "gh-pages"],
                    cwd=work_dir, capture_output=True, text=True, timeout=30,
                )

            if push_result.returncode == 0:
                log("gh-pages push 성공", "ok")
                return True
            else:
                log(f"push 실패: {push_result.stderr[:200]}", "error")
                return False
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            shutil.rmtree(build_dir, ignore_errors=True)

    # ─── 일괄 처리 ───────────────────────────────────────────────────────

    def batch_push(self, filepaths: list, message: str = "") -> bool:
        """여러 파일을 한 번에 커밋 & 푸시 + 빌드 배포."""
        try:
            for fp in filepaths:
                self._run_git("add", fp)
            msg = message or f"포스트 {len(filepaths)}건 일괄 발행"
            self._run_git(
                "-c", f"user.name={self.git_user}",
                "-c", f"user.email={self.git_email}",
                "commit", "-m", msg,
            )
            result = self._run_git("push", "origin", "main")
            if result.returncode != 0:
                log(f"git push 실패: {result.stderr[:200]}", "error")
                return False
            log(f"main push 완료: {len(filepaths)}건", "ok")
            return self.deploy()
        except Exception as e:
            log(f"일괄 push 오류: {e}", "error")
            return False

    def get_categories(self) -> list:
        return []
