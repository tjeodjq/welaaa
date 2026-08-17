# -*- coding: utf-8 -*-
"""윌라(welaaa.com) 제목·web_id 메타데이터 검색 플러그인."""
import hashlib
import io
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape

from PIL import Image, ImageEnhance, ImageFilter
from plugins.metadata.base import BaseMetadataProvider

PLUGIN_VERSION = "1.1.21"
POSTER_WIDTH = 600
POSTER_HEIGHT = 900
VIDEOBOOK_POSTER_MAX_W = 1920
VIDEOBOOK_POSTER_MAX_H = 1080
POSTER_BG = (15, 23, 42)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
SITE = "https://www.welaaa.com"
DETAIL_WORKERS = 4
URL_RE = re.compile(
    r"https?://(?:www\.)?welaaa\.com/(audio|ebook|video|content)/(?:detail/)?(\d+)",
    re.I,
)
ID_RE = re.compile(r"^\d{3,}$")
KIND_INFO = {
    "audio": {
        "path": "audio",
        "prop": "book",
        "source": "윌라 오디오",
        "service": "audiobook",
        "cfg": "SEARCH_AUDIO",
        "cfg_default": True,
    },
    "ebook": {
        "path": "ebook",
        "prop": "ebook",
        "source": "윌라 전자책",
        "service": "ebook",
        "cfg": "SEARCH_EBOOK",
        "cfg_default": True,
    },
    "video": {
        "path": "video",
        "prop": "course",
        "source": "윌라 비디오북",
        "service": "video",
        "cfg": "SEARCH_VIDEO",
        "cfg_default": True,
    },
    "klass": {
        "path": "video",
        "prop": "course",
        "source": "윌라 클래스",
        "service": "klass",
        "cfg": "SEARCH_CLASS",
        "cfg_default": True,
    },
}


class WelaaaMetadataProvider(BaseMetadataProvider):
    id = "welaaa"
    name = "윌라 도서 검색"
    version = PLUGIN_VERSION
    is_searchable = True
    supports_refresh = True
    refresh_media_types = ("videobook",)
    update_manifest = {
        "enabled": True,
        "provider": "github-raw",
        "raw_base_url": "https://raw.githubusercontent.com/tjeodjq/welaaa/main",
        "files": ["welaaa.py", "__init__.py", "README.md", "VERSION"],
        "version_file": "VERSION",
        "version_key": "plugin version",
        "show_sample_update_button": True,
    }
    config_schema = [
        {
            "key": "SEARCH_AUDIO",
            "label": "오디오북 검색",
            "type": "checkbox",
            "required": False,
            "default": True,
        },
        {
            "key": "SEARCH_EBOOK",
            "label": "전자책 검색",
            "type": "checkbox",
            "required": False,
            "default": True,
        },
        {
            "key": "SEARCH_VIDEO",
            "label": "비디오북 검색",
            "type": "checkbox",
            "required": False,
            "default": True,
            "description": "윌라 /video 강의·영상 콘텐츠를 검색합니다.",
        },
        {
            "key": "SEARCH_CLASS",
            "label": "클래스 검색",
            "type": "checkbox",
            "required": False,
            "default": True,
            "description": "윌라 클래스(searchVideoCourse)를 검색합니다.",
        },
        {
            "key": "SEARCH_EXACT",
            "label": "정확한 제목만",
            "type": "checkbox",
            "required": False,
            "default": False,
        },
        {
            "key": "INCLUDE_ADULT",
            "label": "성인 결과 포함",
            "type": "checkbox",
            "required": False,
            "default": False,
        },
        {
            "key": "APPLY_COVER_TO_SERIES",
            "label": "같은 시리즈 전체에 표지 적용",
            "type": "checkbox",
            "required": False,
            "default": True,
        },
        {
            "key": "FIT_POSTER",
            "label": "포스터를 2:3 비율로 맞춤",
            "type": "checkbox",
            "required": False,
            "default": True,
            "description": "오디오북·전자책만 해당. 가로 이미지를 2:3 카드에 맞춥니다. 비디오북·클래스는 16:9를 유지합니다.",
        },
        {
            "key": "PROXY_URL",
            "label": "HTTP(S) 프록시 URL",
            "type": "password",
            "required": False,
            "default": "",
            "description": "선택. 비워 두면 직접 연결합니다. 수동 검색과 자동 새로고침에 동일합니다. NAS가 welaaa.com에 닿지 못할 때만 입력하세요.",
        },
        {
            "key": "WELAAA_COOKIE",
            "label": "윌라 Cookie",
            "type": "password",
            "required": False,
            "default": "",
            "description": "선택. 비워 두면 됩니다. 연령 제한 작품이 필요할 때만 입력합니다. 수동 검색과 자동 새로고침에 동일합니다.",
        },
    ]

    def search(self, db_type, query):
        parsed = self.parse_query(query)
        if not parsed.get("query") and not parsed.get("web_id"):
            return []

        cfg = self._get_config(db_type)
        results = []
        try:
            if parsed.get("web_id"):
                kinds = [parsed["kind"]] if parsed.get("kind") else ("video", "ebook", "audio")
                for kind in kinds:
                    item = self.fetch_detail_item(parsed["web_id"], kind, cfg)
                    if item:
                        results.append(item)
            else:
                results.extend(self._search_by_title(parsed["query"], cfg))
        except Exception as e:
            print(f"[WelaaaMetadataProvider] search failed: {e}")
            return []

        results = self._dedupe(results)
        if not parsed.get("web_id"):
            results = self._filter_relevance(results, parsed["query"], cfg)
        return [self._with_source_prefix(item) for item in results[: self._max_results(cfg)]]

    def apply(self, db_type, book_id, item_data):
        item_data = self._restore_original_title(item_data)
        cfg = self._get_config(db_type)
        web_id = self._clean_text(item_data.get("web_id") or item_data.get("external_id"))
        kind = self._kind_from_item(item_data, db_type=db_type)
        if web_id:
            detailed = self.fetch_detail_item(web_id, kind, cfg)
            if not detailed and kind in ("video", "klass"):
                other = "klass" if kind == "video" else "video"
                detailed = self.fetch_detail_item(web_id, other, cfg)
            if detailed:
                keep_detail_cover = self._is_videobook_cover(
                    db_type, detailed.get("service_type") or item_data.get("service_type")
                )
                detail_cover = detailed.get("cover")
                detail_candidates = list(detailed.get("cover_candidates") or [])
                incoming = {k: v for k, v in item_data.items() if v}
                if "/content/" in str(incoming.get("link") or "").lower():
                    incoming.pop("link", None)
                item_data = {**detailed, **incoming}
                item_data = self._restore_original_title(item_data)
                if keep_detail_cover:
                    # Search hits show landscape klass-cover; keep those URLs too.
                    merged = []
                    seen = set()
                    for value in (
                        detail_cover,
                        incoming.get("cover"),
                        *(detail_candidates or []),
                        *(incoming.get("cover_candidates") or []),
                    ):
                        url = self._cover_http(value)
                        if not url or url in seen:
                            continue
                        seen.add(url)
                        merged.append(url)
                    item_data["cover_candidates"] = merged
                    item_data["cover"] = detail_cover or incoming.get("cover") or (merged[0] if merged else "")
                if db_type == "videobook":
                    item_data["link"] = self._canonical_link(web_id, "video")

        gateway = self.get_db_gateway(db_type)
        table = self._media_table(db_type)
        try:
            book = gateway.fetch_one(
                f"""
                SELECT id, file_path, library_id, series_name, cover_image
                FROM {table}
                WHERE id = ? AND COALESCE(is_deleted, 0) = 0
                """,
                (book_id,),
            )
            if not book:
                return False, "대상 도서를 찾을 수 없습니다."

            cover_filename = self._save_cover(
                book,
                item_data.get("cover"),
                cfg,
                db_type,
                item_data.get("service_type"),
                item_data.get("cover_candidates"),
            )
            description = self._clean_text(item_data.get("description"))
            author = self._clean_text(item_data.get("author"))
            publisher = self._clean_text(item_data.get("publisher")) or "윌라"
            isbn = self._clean_text(
                item_data.get("isbn") or item_data.get("web_id") or item_data.get("external_id")
            )
            release_date = self._clean_text(item_data.get("pubDate") or item_data.get("release_date"))
            link = item_data.get("link") or ""
            if table == "videobooks" and web_id:
                link = self._canonical_link(web_id, "video")
            genre = self._clean_text(item_data.get("genre"))
            tags = self._clean_text(item_data.get("tags"))
            score = self._optional_score(item_data.get("score"))
            narrator = self._clean_text(item_data.get("narrator"))
            title = self._clean_text(item_data.get("title")) or "윌라 도서"

            series_cover_updates = []
            raw_series_name = book["series_name"] or ""
            if (
                cover_filename
                and self._clean_text(raw_series_name)
                and book["library_id"] is not None
                and self._truthy(cfg.get("APPLY_COVER_TO_SERIES"))
            ):
                series_cover_updates, _ = self._prepare_series_cover_files(
                    gateway, book, raw_series_name, db_type
                )

            set_parts = [
                "author = ?",
                "publisher = ?",
                "summary = ?",
                "link = ?",
                "release_date = COALESCE(?, release_date)",
                "genre = COALESCE(?, genre)",
                "tags = COALESCE(?, tags)",
                "score = COALESCE(?, score)",
                "cover_image = COALESCE(?, cover_image)",
                """cover_updated_at = CASE
                        WHEN ? IS NOT NULL THEN CURRENT_TIMESTAMP
                        ELSE cover_updated_at
                    END""",
            ]
            params = [
                author,
                publisher,
                description,
                link,
                release_date or None,
                genre or None,
                tags or None,
                score,
                cover_filename or None,
                cover_filename or None,
            ]
            if table != "videobooks":
                set_parts.insert(1, "isbn = COALESCE(?, isbn)")
                params.insert(1, isbn or None)
            if table == "audiobooks" and narrator:
                set_parts.append("narrator = COALESCE(?, narrator)")
                params.append(narrator)
            if not (table == "videobooks" and not cover_filename):
                set_parts.append("metadata_locked = 1")
            params.append(book_id)
            sql = f"""
                UPDATE {table}
                SET {", ".join(set_parts)}
                WHERE id = ? AND COALESCE(is_deleted, 0) = 0
            """

            count = gateway.execute(sql, tuple(params))
            if count == 0:
                existing = gateway.fetch_one(
                    f"SELECT id FROM {table} WHERE id = ? AND COALESCE(is_deleted, 0) = 0",
                    (book_id,),
                )
                count = 1 if existing else 0
            if count != 1:
                raise RuntimeError("대상 도서가 삭제되었거나 변경되어 메타데이터를 적용하지 못했습니다.")

            if series_cover_updates:
                gateway.execute_many(
                    f"""
                    UPDATE {table}
                    SET cover_image = ?,
                        cover_updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                      AND library_id = ?
                      AND series_name = ?
                      AND COALESCE(is_deleted, 0) = 0
                    """,
                    series_cover_updates,
                )

            extra = f" (시리즈 표지 {len(series_cover_updates)}권)" if series_cover_updates else ""
            if table == "videobooks":
                self._sync_videobook_folder_meta(book, item_data)
            if table == "videobooks" and not cover_filename:
                return False, (
                    f'"{title}" 저자·소개는 반영했지만 가로 클래스 표지를 저장하지 못했습니다.{extra}'
                )
            return True, f'"{title}" 윌라 메타데이터가 반영되었습니다.{extra}'
        except Exception as e:
            return False, f"DB 업데이트 오류: {e}"

    @staticmethod
    def _row_value(row, key, default=None):
        if row is None:
            return default
        try:
            if isinstance(row, dict):
                val = row.get(key, default)
            elif hasattr(row, "keys") and key in row.keys():
                val = row[key]
            else:
                val = default
        except Exception:
            return default
        return default if val is None else val

    def parse_refresh_identity(self, row):
        link = self._clean_text(self._row_value(row, "link"))
        if not link:
            return None
        parsed = self.parse_query(link)
        web_id = self._clean_text(parsed.get("web_id"))
        if not web_id:
            return None
        kind = self._clean_text(parsed.get("kind")) or "video"
        if kind in ("content",):
            kind = "video"
        return {
            "provider_id": self.id,
            "web_id": web_id,
            "kind": kind,
            "link": self._canonical_link(web_id, kind),
        }

    def refresh(self, db_type, book_id, fields="cover", force_locked=True, ident=None):
        field_set = {
            part.strip().lower()
            for part in str(fields or "cover").split(",")
            if part.strip()
        }
        if "cover" not in field_set:
            field_set.add("cover")

        gateway = self.get_db_gateway(db_type)
        table = self._media_table(db_type)
        row = gateway.fetch_one(
            f"""
            SELECT *
            FROM {table}
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (book_id,),
        )
        if not row:
            return False, "대상 도서를 찾을 수 없습니다."
        if not force_locked and int(self._row_value(row, "metadata_locked", 0) or 0) == 1:
            return False, "메타데이터가 잠겨 있습니다."

        parsed = self.parse_refresh_identity(row)
        if parsed:
            ident = parsed
        elif ident and ident.get("web_id"):
            kind = str(ident.get("kind") or "video").strip() or "video"
            if kind in ("content",):
                kind = "video"
            web_id = str(ident.get("web_id") or "").strip()
            ident = {
                "provider_id": self.id,
                "web_id": web_id,
                "kind": kind,
                "link": self._canonical_link(web_id, kind),
            }
        else:
            ident = None
        if not ident or not ident.get("web_id"):
            return False, "윌라 식별자(link)가 없습니다."

        cfg = self._get_config(db_type)
        kind = ident.get("kind") or "video"
        detailed = self.fetch_detail_item(ident["web_id"], kind, cfg)
        if not detailed and kind in ("video", "klass"):
            other = "klass" if kind == "video" else "video"
            detailed = self.fetch_detail_item(ident["web_id"], other, cfg)
        if not detailed:
            return False, "윌라 상세를 가져오지 못했습니다."

        if "text" in field_set:
            return self.apply(db_type, book_id, detailed)

        cover_filename = self._save_cover(
            {
                "id": self._row_value(row, "id"),
                "library_id": self._row_value(row, "library_id"),
                "file_path": self._row_value(row, "file_path"),
                "series_name": self._row_value(row, "series_name"),
                "cover_image": self._row_value(row, "cover_image"),
            },
            detailed.get("cover"),
            cfg,
            db_type,
            detailed.get("service_type"),
            detailed.get("cover_candidates"),
        )
        if not cover_filename:
            return False, "표지를 저장하지 못했습니다."
        link = self._canonical_link(ident["web_id"], ident.get("kind") or "video")
        gateway.execute(
            f"""
            UPDATE {table}
            SET cover_image = ?,
                cover_updated_at = CURRENT_TIMESTAMP,
                metadata_locked = 1,
                link = CASE WHEN TRIM(COALESCE(?, '')) = '' THEN link ELSE ? END
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (cover_filename, link, link, book_id),
        )
        extra = ""
        raw_series_name = self._clean_text(self._row_value(row, "series_name"))
        if (
            raw_series_name
            and self._row_value(row, "library_id") is not None
            and self._truthy(cfg.get("APPLY_COVER_TO_SERIES"))
        ):
            series_cover_updates, _ = self._prepare_series_cover_files(
                gateway,
                {
                    "id": self._row_value(row, "id"),
                    "library_id": self._row_value(row, "library_id"),
                    "file_path": self._row_value(row, "file_path"),
                },
                raw_series_name,
                db_type,
            )
            if series_cover_updates:
                gateway.execute_many(
                    f"""
                    UPDATE {table}
                    SET cover_image = ?,
                        cover_updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                      AND library_id = ?
                      AND series_name = ?
                      AND COALESCE(is_deleted, 0) = 0
                    """,
                    series_cover_updates,
                )
                extra = f" (시리즈 표지 {len(series_cover_updates)}권)"
        title = self._clean_text(detailed.get("title")) or "윌라 콘텐츠"
        return True, f'"{title}" 윌라 표지를 새로고침했습니다.{extra}'

    def get_context_menu_items(self, db_type, context):
        return [
            {
                "id": "open_welaaa_search",
                "label": "윌라에서 제목 검색",
                "icon": "fa-solid fa-headphones",
            }
        ]

    def run_context_menu_action(self, db_type, action_id, context):
        if action_id != "open_welaaa_search":
            return {"success": False, "error": f"지원하지 않는 액션입니다: {action_id}"}
        title = self._clean_text((context or {}).get("book_title"))
        if not title:
            return {"success": False, "error": "검색할 도서 제목 정보가 없습니다."}
        url = f"{SITE}/search-result?" + urllib.parse.urlencode({"search": title})
        return {
            "success": True,
            "message": "윌라 검색 페이지를 새 탭으로 엽니다.",
            "open_url": url,
        }

    @staticmethod
    def parse_query(query):
        text = unescape(str(query or "")).strip()
        match = URL_RE.search(text)
        if match:
            kind = match.group(1).lower()
            if kind == "content":
                kind = ""
            elif kind == "video":
                kind = "video"
            return {"query": "", "web_id": match.group(2), "kind": kind}
        if ID_RE.match(text):
            return {"query": "", "web_id": text, "kind": ""}
        return {"query": text, "web_id": "", "kind": ""}

    @staticmethod
    def _canonical_link(web_id, kind="video"):
        web_id = str(web_id or "").strip()
        kind = str(kind or "video").strip().lower()
        if kind in ("content", "class", "course", "videobook", ""):
            kind = "video"
        info = KIND_INFO.get(kind) or KIND_INFO["video"]
        return f"{SITE}/{info['path']}/detail/{web_id}" if web_id else SITE

    def fetch_detail_item(self, web_id, kind, cfg):
        kind = self._normalize_kind(kind)
        info = KIND_INFO[kind]
        url = f"{SITE}/{info['path']}/detail/{web_id}"
        try:
            html = self._http_text(url, cfg)
        except Exception as e:
            print(f"[WelaaaMetadataProvider] detail fetch failed ({kind}/{web_id}): {e}")
            return None
        nxt = self.extract_next_data(html)
        if not nxt:
            return None
        page = str(nxt.get("page") or "")
        if "content-expiration" in page:
            return None
        props = (nxt.get("props") or {}).get("pageProps") or {}
        book = props.get(info["prop"])
        if not isinstance(book, dict):
            book = props.get("book") or props.get("ebook") or props.get("course")
        if not isinstance(book, dict) or not book.get("title"):
            return None
        og = self._og_image(html)
        if og:
            book = dict(book)
            book["og_image"] = og
        resolved = self._kind_from_course(book, kind)
        return self.item_from_book(book, resolved)

    @staticmethod
    def _og_image(html):
        text = str(html or "")
        match = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            text,
            re.I,
        )
        if not match:
            match = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                text,
                re.I,
            )
        return unescape(match.group(1)).strip() if match else ""

    def item_from_book(self, book, kind):
        kind = self._normalize_kind(kind)
        info = KIND_INFO[kind]
        web_id = str(book.get("id") or "").strip()
        author = self._clean_text(
            book.get("author_name")
            or (book.get("teacher") or {}).get("name")
            or ""
        )
        publisher = self._clean_text((book.get("publisher") or {}).get("name") or "") or "윌라"
        cover_candidates = self._cover_candidates(book, kind)
        cover = cover_candidates[0] if cover_candidates else ""
        description = self._clean_text(
            book.get("description") or book.get("memo") or book.get("copy") or book.get("headline") or ""
        )
        isbn = self._clean_text(book.get("isbn") or "") or web_id
        genre = self._genre_from_book(book)
        tags = self._join_names(book.get("tags") or [])
        if not tags:
            tags = self._join_names(
                [
                    b.get("title")
                    for b in (book.get("badges") or [])
                    if isinstance(b, dict) and str(b.get("type") or "").upper() == "KEYWORD"
                ]
            )
        narrators = self._join_names(
            [va.get("name") for va in (book.get("voice_actors") or []) if isinstance(va, dict)]
        )
        score = self._score_from_book(book)
        pub_date = self._pub_date(book)
        link = f"{SITE}/{info['path']}/detail/{web_id}" if web_id else SITE
        return {
            "title": self._clean_text(book.get("title")),
            "author": author,
            "publisher": publisher,
            "isbn": isbn,
            "pubDate": pub_date,
            "cover": cover,
            "cover_candidates": cover_candidates,
            "description": description,
            "link": link,
            "genre": genre,
            "tags": tags,
            "score": score,
            "narrator": narrators,
            "source": info["source"],
            "web_id": web_id,
            "external_id": web_id,
            "service_type": info["service"],
            "raw_title": self._clean_text(book.get("title")),
        }

    def item_from_search_hit(self, hit, kind):
        kind = self._normalize_kind(kind)
        info = KIND_INFO[kind]
        web_id = str(hit.get("id") or "").strip()
        cover = self._best_cover(hit, kind)
        teacher = hit.get("teacher") if isinstance(hit.get("teacher"), dict) else {}
        author = self._join_names(
            [hit.get("author_name"), teacher.get("headline"), teacher.get("name")]
        )
        description = self._clean_text(hit.get("subtitle") or hit.get("headline") or hit.get("copy") or "")
        score = self._score_from_meta(hit.get("meta") or {})
        title = self._clean_text(hit.get("title") or hit.get("name"))
        return {
            "title": title,
            "author": author,
            "publisher": "윌라",
            "isbn": web_id,
            "pubDate": "",
            "cover": cover,
            "description": description,
            "link": f"{SITE}/{info['path']}/detail/{web_id}" if web_id else SITE,
            "genre": "",
            "tags": "",
            "score": score,
            "narrator": "",
            "source": info["source"],
            "web_id": web_id,
            "external_id": web_id,
            "service_type": info["service"],
            "raw_title": title,
            "is_adult": bool(hit.get("is_adult_content")),
        }

    @staticmethod
    def extract_next_data(html):
        text = str(html or "")
        marker = 'id="__NEXT_DATA__"'
        start = text.find(marker)
        if start < 0:
            return None
        start = text.find(">", start)
        if start < 0:
            return None
        start += 1
        end = text.find("</script>", start)
        if end < 0:
            return None
        try:
            return json.loads(text[start:end])
        except Exception:
            return None

    def _search_by_title(self, query, cfg):
        url = f"{SITE}/search-result?" + urllib.parse.urlencode({"search": query})
        try:
            html = self._http_text(url, cfg)
        except Exception as e:
            print(f"[WelaaaMetadataProvider] search page fetch failed: {e}")
            return []
        nxt = self.extract_next_data(html)
        if not nxt:
            print("[WelaaaMetadataProvider] search page missing __NEXT_DATA__")
            return []
        props = (nxt.get("props") or {}).get("pageProps") or {}
        groups = []
        include_adult = self._truthy(cfg.get("INCLUDE_ADULT"))
        buckets = (
            ("searchAudiobook", "audio", "SEARCH_AUDIO"),
            ("searchEbook", "ebook", "SEARCH_EBOOK"),
        )
        for bucket, kind, cfg_key in buckets:
            if not self._truthy(cfg.get(cfg_key, True)):
                continue
            group = []
            for hit in ((props.get(bucket) or {}).get("items") or []):
                if not include_adult and hit.get("is_adult_content"):
                    continue
                group.append(self.item_from_search_hit(hit, kind))
            if group:
                groups.append(group)

        if self._truthy(cfg.get("SEARCH_VIDEO", True)) or self._truthy(cfg.get("SEARCH_CLASS", True)):
            group = []
            for hit in ((props.get("searchVideoCourse") or {}).get("items") or []):
                if not include_adult and hit.get("is_adult_content"):
                    continue
                kind = self._video_hit_kind(hit, cfg)
                if not kind:
                    continue
                group.append(self.item_from_search_hit(hit, kind))
            if group:
                groups.append(group)

        hits = self._interleave(groups)

        # 상위 결과는 상세 페이지로 보강 (저자/소개/ISBN)
        enrich_ids = [(h.get("web_id"), self._kind_from_item(h)) for h in hits[:8]]
        details = self._parallel_map(lambda pair: self.fetch_detail_item(pair[0], pair[1], cfg), enrich_ids)
        by_id = {}
        for detail in details:
            if detail and detail.get("web_id"):
                by_id[(detail.get("service_type"), detail["web_id"])] = detail
        merged = []
        for hit in hits:
            key = (hit.get("service_type"), hit.get("web_id"))
            detail = by_id.get(key)
            if not detail:
                merged.append(hit)
                continue
            if not self._clean_text(detail.get("cover")):
                detail["cover"] = hit.get("cover") or ""
            if not self._clean_text(detail.get("title")):
                detail["title"] = hit.get("title") or ""
                detail["raw_title"] = hit.get("raw_title") or detail["title"]
            merged.append(detail)
        return merged

    @staticmethod
    def _interleave(groups):
        out = []
        index = 0
        while True:
            added = False
            for group in groups:
                if index < len(group):
                    out.append(group[index])
                    added = True
            if not added:
                break
            index += 1
        return out

    def _filter_relevance(self, results, query, cfg):
        nq = self._normalize(query)
        if not nq:
            return results
        tokens = re.findall(r"[0-9a-zA-Z가-힣]{2,}", unescape(str(query or "")).lower())
        if self._truthy(cfg.get("SEARCH_EXACT")):
            return [r for r in results if self._normalize(r.get("raw_title") or r.get("title")) == nq]

        matched = []
        for result in results:
            blob = self._normalize(
                " ".join(
                    [
                        str(result.get("raw_title") or ""),
                        str(result.get("title") or ""),
                        str(result.get("author") or ""),
                        str(result.get("description") or ""),
                    ]
                )
            )
            raw_title = unescape(str(result.get("raw_title") or result.get("title") or "")).lower()
            if nq in blob or blob in nq:
                matched.append(result)
            elif tokens and all(token in raw_title for token in tokens):
                matched.append(result)
        # 윌라가 이미 찾아 준 클래스/비디오는 제목 표기가 달라도 버리지 않는다.
        return matched or results

    def _get_config(self, db_type):
        try:
            cfg = self.get_plugin_config(db_type, default={}) or {}
        except Exception as e:
            print(f"[WelaaaMetadataProvider] config load failed: {e}")
            cfg = {}
        return cfg if isinstance(cfg, dict) else {}

    def _max_results(self, cfg):
        try:
            return max(1, min(int(cfg.get("MAX_RESULTS") or 20), 40))
        except Exception:
            return 20

    def _http_text(self, url, cfg, timeout=15):
        last_err = None
        for attempt in range(2):
            try:
                return self._http_text_once(url, cfg, timeout)
            except Exception as err:
                last_err = err
                print(f"[WelaaaMetadataProvider] http retry {attempt + 1}/2 ({url}): {err}")
                time.sleep(0.4 * (attempt + 1))
        raise last_err

    def _http_text_once(self, url, cfg, timeout=15):
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Referer": f"{SITE}/",
        }
        cookie = self._clean_text(cfg.get("WELAAA_COOKIE"))
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(url, headers=headers)
        opener = self._opener(cfg)
        with opener.open(req, timeout=timeout) as resp:
            chunks = []
            total = 0
            max_bytes = 4 * 1024 * 1024
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("윌라 응답이 너무 큽니다.")
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", "replace")

    def _opener(self, cfg):
        proxy_url = self._clean_text(cfg.get("PROXY_URL"))
        if not proxy_url:
            return urllib.request.build_opener()
        parsed = urllib.parse.urlsplit(proxy_url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            raise ValueError("PROXY_URL은 http://host:port 형식이어야 합니다.")
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        return urllib.request.build_opener(handler)

    def _save_cover(self, book, cover_url, cfg, db_type=None, service_type=None, cover_candidates=None):
        urls = []
        seen = set()
        for value in [cover_url, *(cover_candidates or [])]:
            url = self._cover_http(value)
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
            wide = self._widen_cover_url(url)
            if wide and wide not in seen:
                seen.add(wide)
                urls.append(wide)
        if not urls:
            return None
        try:
            dest_path, cover_filename = self._cover_location(
                book["library_id"], book["file_path"], db_type,
                existing_rel=book.get("cover_image"),
            )
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            opener = self._opener(cfg)
            videobook = self._is_videobook_cover(db_type, service_type)
            landscapes = []
            fallback = None
            cookie = self._clean_text(cfg.get("WELAAA_COOKIE"))
            for cover_url in urls:
                if videobook and self._is_dead_course_thumb(cover_url):
                    continue
                if videobook and self._cover_score(cover_url, course=True) < 0 and landscapes:
                    break
                try:
                    headers = {"User-Agent": DEFAULT_USER_AGENT, "Referer": f"{SITE}/"}
                    if cookie:
                        headers["Cookie"] = cookie
                    img_data = None
                    last_err = None
                    for attempt in range(2):
                        try:
                            req = urllib.request.Request(cover_url, headers=headers)
                            with opener.open(req, timeout=15) as response:
                                img_data = response.read()
                            last_err = None
                            break
                        except Exception as err:
                            last_err = err
                            time.sleep(0.4 * (attempt + 1))
                    if img_data is None:
                        raise last_err or RuntimeError("cover download failed")
                    with Image.open(io.BytesIO(img_data)) as img:
                        src = img.convert("RGB")
                        if videobook and src.width >= int(src.height * 1.2):
                            landscapes.append((src.width * src.height, src.copy()))
                            if src.width * src.height >= 640 * 210:
                                break
                            continue
                        if fallback is None:
                            fallback = src.copy()
                except Exception as err:
                    print(f"[WelaaaMetadataProvider] cover candidate failed ({cover_url}): {err}")
            if videobook:
                if not landscapes:
                    folder_src = self._folder_landscape_poster(book.get("file_path"))
                    if folder_src is None:
                        print("[WelaaaMetadataProvider] no landscape cover candidate; skip portrait fallback")
                        return None
                    src = folder_src
                else:
                    src = max(landscapes, key=lambda item: item[0])[1]
            else:
                src = fallback
            if src is None:
                return None
            if videobook:
                poster = self._fit_videobook_poster(src)
            elif self._truthy(cfg.get("FIT_POSTER", True)):
                poster = self._fit_poster(src)
            else:
                poster = src
            poster.save(dest_path, "WEBP", quality=82)
            if videobook:
                self._write_folder_poster(book.get("file_path"), poster)
            return cover_filename
        except Exception as e:
            print(f"[WelaaaMetadataProvider] cover download failed: {e}")
            return None

    def _prepare_series_cover_files(self, gateway, book, series_name, db_type=None):
        table = self._media_table(db_type)
        series_books = gateway.fetch_all(
            f"""
            SELECT id, file_path
            FROM {table}
            WHERE library_id = ?
              AND series_name = ?
              AND id <> ?
              AND COALESCE(is_deleted, 0) = 0
            """,
            (book["library_id"], series_name, book["id"]),
        )
        if not series_books:
            return [], 0
        source_path, _ = self._cover_location(book["library_id"], book["file_path"], db_type)
        if not os.path.isfile(source_path):
            return [], len(series_books)
        updates = []
        failures = 0
        for series_book in series_books:
            try:
                dest_path, cover_filename = self._cover_location(
                    book["library_id"], series_book["file_path"], db_type
                )
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copyfile(source_path, dest_path)
                updates.append(
                    (cover_filename, series_book["id"], book["library_id"], series_name)
                )
            except Exception:
                failures += 1
        return updates, failures

    @staticmethod
    def _media_table(db_type):
        raw = str(db_type or "").strip().lower()
        if raw == "audiobook":
            return "audiobooks"
        if raw == "videobook":
            return "videobooks"
        return "books"

    def _cover_location(self, library_id, file_path, db_type=None, existing_rel=None):
        if not file_path and not existing_rel:
            raise ValueError("표지 파일명을 생성할 도서 경로가 없습니다.")
        try:
            from utils.cover_repair_targets import media_server_dir
            base_dir = media_server_dir()
        except Exception:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        kind = str(db_type or "").strip().lower()
        if kind == "videobook":
            stamp = int(time.time())
            digest = hashlib.md5(str(file_path or existing_rel or stamp).encode("utf-8")).hexdigest()[:12]
            filename = f"welaaa_{digest}_{stamp}.webp"
            rel = f"videobook/{library_id}/{filename}"
            return os.path.join(base_dir, "covers", "videobook", str(library_id), filename), rel
        rel = str(existing_rel or "").strip().split("?", 1)[0].replace("\\", "/").lstrip("/")
        if rel.lower().startswith("covers/"):
            rel = rel[7:]
        if rel and ".." not in rel.split("/"):
            return os.path.join(base_dir, "covers", *rel.split("/")), rel
        book_hash = hashlib.md5(str(file_path).encode("utf-8")).hexdigest()
        filename = f"book_{book_hash}.webp"
        if kind == "audiobook":
            rel = f"{kind}/{library_id}/{filename}"
            return os.path.join(base_dir, "covers", kind, str(library_id), filename), rel
        return os.path.join(base_dir, "covers", str(library_id), filename), f"{library_id}/{filename}"

    def _parallel_map(self, func, values):
        values = [v for v in values if v and v[0]]
        if not values:
            return []

        def safe_call(value):
            try:
                return func(value)
            except Exception as e:
                print(f"[WelaaaMetadataProvider] detail enrich failed: {e}")
                return None

        if len(values) == 1:
            return [safe_call(values[0])]
        with ThreadPoolExecutor(max_workers=min(DETAIL_WORKERS, len(values))) as executor:
            return list(executor.map(safe_call, values))

    def _with_source_prefix(self, item):
        result = dict(item)
        source = self._clean_text(result.get("source"))
        title = self._clean_text(result.get("title"))
        result["raw_title"] = result.get("raw_title") or title
        prefix = f"[{source}]" if source else ""
        if prefix and title and not title.startswith(prefix):
            result["title"] = f"{prefix} {title}"
        return result

    def _restore_original_title(self, item):
        result = dict(item or {})
        original = self._clean_text(result.get("raw_title"))
        if not original:
            title = self._clean_text(result.get("title"))
            source = self._clean_text(result.get("source"))
            prefix = f"[{source}]" if source else ""
            original = title[len(prefix):].lstrip() if prefix and title.startswith(prefix) else title
        result["title"] = original
        return result

    def _kind_from_item(self, item, db_type=None):
        link = self._clean_text((item or {}).get("link")).lower()
        if db_type == "videobook":
            if "/ebook/" in link:
                return "ebook"
            if "/audio/" in link and "/video/" not in link and "/content/" not in link:
                return "audio"
            return "klass"
        service = self._clean_text((item or {}).get("service_type")).lower()
        for kind, info in KIND_INFO.items():
            if service == info["service"] or service == kind:
                return kind
        if "/ebook/" in link:
            return "ebook"
        if "/video/" in link or "/content/" in link:
            return "klass"
        if "/audio/" in link:
            return "audio"
        return "audio"

    def _normalize_kind(self, kind):
        raw = str(kind or "").strip().lower()
        aliases = {
            "audiobook": "audio",
            "class": "klass",
            "course": "klass",
            "videobook": "video",
            "content": "video",
        }
        raw = aliases.get(raw, raw)
        if not raw:
            return "video"
        return raw if raw in KIND_INFO else "audio"

    def _kind_from_course(self, course, requested):
        requested = self._normalize_kind(requested)
        service = str((course or {}).get("service_type") or "").lower()
        if service in ("klass", "class"):
            return "klass"
        for cat in (course or {}).get("categories") or []:
            if not isinstance(cat, dict):
                continue
            cat_type = str(cat.get("type") or "").lower()
            translated = str(cat.get("type_translated") or "")
            if cat_type.startswith("klass") or translated == "클래스":
                return "klass"
        if requested in ("video", "klass"):
            return requested
        return requested

    def _video_hit_kind(self, hit, cfg):
        want_class = self._truthy(cfg.get("SEARCH_CLASS", True))
        want_video = self._truthy(cfg.get("SEARCH_VIDEO", True))
        raw = str(hit.get("service_type") or "").lower()
        title = str(hit.get("service_type_title") or "")
        is_class = raw in ("klass", "class") or title == "클래스"
        if is_class and want_class:
            return "klass"
        if want_video:
            return "video"
        if want_class:
            return "klass"
        return None

    def _pub_date(self, book):
        raw = book.get("open_date") or book.get("created_at") or ""
        text = self._clean_text(raw)
        if text and len(text) >= 10 and text[0].isdigit():
            return text[:10]
        meta = book.get("meta") if isinstance(book.get("meta"), dict) else {}
        created = self._clean_text(meta.get("created_at"))
        if created and len(created) >= 10:
            return created[:10]
        return ""

    def _best_cover(self, book, kind=None):
        candidates = self._cover_candidates(book, kind)
        return candidates[0] if candidates else ""

    def _cover_candidates(self, book, kind=None):
        requested = self._normalize_kind(kind) if kind else ""
        service = str(book.get("service_type") or book.get("service_type_title") or "").lower()
        is_course = requested in ("video", "klass") or service in ("klass", "class", "video", "클래스")
        best = {}

        def add(value, meta=None, hint=""):
            url = self._cover_http(value)
            if not url:
                return
            score = self._cover_score(url, meta=meta, hint=hint, course=is_course)
            informed = 0
            if isinstance(meta, dict):
                try:
                    informed = 1 if int(meta.get("width") or 0) and int(meta.get("height") or 0) else 0
                except (TypeError, ValueError):
                    informed = 0
            prev = best.get(url)
            if prev is None:
                best[url] = (score, informed)
                return
            prev_score, prev_inf = prev
            if informed > prev_inf or (informed == prev_inf and score > prev_score):
                best[url] = (score, informed)

        images = book.get("images") if isinstance(book.get("images"), dict) else {}
        for key, value in images.items():
            add(value, hint=str(key))
        img_set = book.get("img_set") if isinstance(book.get("img_set"), dict) else {}
        for key, value in img_set.items():
            add(value, hint=str(key))
        for key in (
            "klass_cover_image_url",
            "cover_image_url",
            "image_url",
            "og_image",
        ):
            add(book.get(key), hint=key)
        info = book.get("cover_image_info") if isinstance(book.get("cover_image_info"), dict) else {}
        add(info, meta=info, hint=str(info.get("image_type_string") or ""))
        for row in book.get("cover_image_info_list") or []:
            if not isinstance(row, dict):
                continue
            hint = " ".join(
                str(row.get(k) or "")
                for k in ("image_type_string", "image_source_type_string")
            )
            add(row, meta=row, hint=hint)
        ranked = sorted(best.items(), key=lambda item: item[1][0], reverse=True)
        return [url for url, (score, _inf) in ranked if score > -100]

    @staticmethod
    def _cover_score(url, meta=None, hint="", course=False):
        meta = meta if isinstance(meta, dict) else {}
        blob = " ".join(
            [
                str(url or "").lower(),
                str(hint or "").lower(),
                str(meta.get("image_type_string") or "").lower(),
                str(meta.get("image_source_type_string") or "").lower(),
            ]
        )
        try:
            width = int(meta.get("width") or 0)
            height = int(meta.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        ratio = (width / float(height)) if width and height else 0.0
        score = 0
        raw_url = str(url or "").lower()
        if raw_url.endswith(("_big.jpg", "_big.png", "_big.webp")):
            score -= 200
        if "/static/courses/" in raw_url and any(
            token in raw_url for token in ("_big.", "_list.", "_wide.")
        ):
            score -= 80
        if "klass-cover-alt" in blob or "cover-alt" in blob:
            score -= 60
        if "klass-cover" in blob and "alt" not in blob:
            if ratio and ratio < 0.95:
                score -= 40
            else:
                score += 50
        if any(token in blob for token in ("wide", "horizontal", "landscape", "banner", "main")):
            score += 40
        if "original-image" in blob:
            score += 20
        if any(token in blob for token in ("list", "thumb", "portrait", "square")):
            score -= 25
        if width and height:
            if ratio >= 1.4:
                score += 90 + int((width * height) / 5000)
            elif ratio < 0.95:
                score -= 90
        elif course and "list" in blob:
            score -= 20
        return score

    def _cover_http(self, value):
        if isinstance(value, dict):
            for key in ("url", "image_url", "src", "path"):
                found = self._cover_http(value.get(key))
                if found:
                    return found
            return ""
        url = self._clean_text(value)
        if url.startswith("http") and "placeholder" not in url.lower() and not url.endswith("/"):
            return url
        return ""

    @staticmethod
    def _widen_cover_url(url):
        blob = str(url or "").strip()
        if not blob:
            return ""
        return (
            blob.replace("klass-cover-alt", "klass-cover")
            .replace("_list.jpg", "_wide.jpg")
            .replace("_list.png", "_wide.png")
            .replace("_list.webp", "_wide.webp")
        )

    @staticmethod
    def _is_dead_course_thumb(url):
        blob = str(url or "").lower()
        if "/static/courses/" not in blob:
            return False
        return any(token in blob for token in ("_wide.jpg", "_list.jpg", "_big.jpg", "_wide.png", "_list.png", "_big.png"))

    def _folder_landscape_poster(self, file_path):
        folder = os.path.dirname(str(file_path or "").strip())
        if not folder or not os.path.isdir(folder):
            return None
        try:
            names = os.listdir(folder)
        except Exception:
            return None
        lower_map = {name.lower(): name for name in names}
        chosen = None
        for candidate in ("poster.jpg", "poster.png", "poster.webp", "cover.jpg", "cover.png", "folder.jpg"):
            if candidate in lower_map:
                chosen = os.path.join(folder, lower_map[candidate])
                break
        if not chosen:
            return None
        try:
            with Image.open(chosen) as img:
                src = img.convert("RGB")
                if src.width >= int(src.height * 1.2):
                    return src.copy()
        except Exception:
            return None
        return None

    def _write_folder_poster(self, file_path, image):
        folder = os.path.dirname(str(file_path or "").strip())
        if file_path and os.path.isdir(str(file_path).strip()):
            folder = str(file_path).strip()
        if not folder or not os.path.isdir(folder) or image is None:
            return
        try:
            image.convert("RGB").save(os.path.join(folder, "poster.jpg"), "JPEG", quality=85)
        except Exception as err:
            print(f"[WelaaaMetadataProvider] folder poster update failed: {err}")

    def _sync_videobook_folder_meta(self, book, item_data):
        from datetime import datetime

        file_path = ""
        if book:
            file_path = book["file_path"] if isinstance(book, dict) else self._row_value(book, "file_path")
        folder = str(file_path or "").strip()
        if folder and not os.path.isdir(folder):
            folder = os.path.dirname(folder)
        web_id = self._clean_text(
            (item_data or {}).get("web_id") or (item_data or {}).get("external_id") or (item_data or {}).get("isbn")
        )
        if not folder or not os.path.isdir(folder) or not web_id.isdigit():
            return
        try:
            from tools.scanner.metadata.welaaa_json import pick_applied_cover_url, upsert_welaaa_sidecar
        except Exception as err:
            print(f"[WelaaaMetadataProvider] folder JSON helper missing: {err}")
            return
        cover_url = pick_applied_cover_url(
            (item_data or {}).get("cover"),
            (item_data or {}).get("cover_candidates"),
        )
        updates = {
            "type": "klass",
            "content_id": web_id,
            "web_id": web_id,
            "title": self._clean_text((item_data or {}).get("title") or (item_data or {}).get("raw_title")),
            "author_or_teacher": self._clean_text((item_data or {}).get("author")),
            "summary": self._clean_text((item_data or {}).get("description")),
            "cover_url": cover_url,
            "publisher": self._clean_text((item_data or {}).get("publisher")) or "윌라",
            "genre": self._clean_text((item_data or {}).get("genre")),
            "link": self._canonical_link(web_id, "video"),
            "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_from": "welaaa-apply",
        }
        tags = self._clean_text((item_data or {}).get("tags"))
        if tags:
            updates["keywords"] = [part.strip() for part in tags.split(",") if part.strip()]
        try:
            upsert_welaaa_sidecar(folder, updates)
        except Exception as err:
            print(f"[WelaaaMetadataProvider] folder JSON update failed: {err}")

    @staticmethod
    def _is_videobook_cover(db_type, service_type):
        if str(db_type or "").strip().lower() == "videobook":
            return True
        return str(service_type or "").strip().lower() in (
            "video", "klass", "class", "videobook",
        )

    @staticmethod
    def _fit_videobook_poster(img, max_w=VIDEOBOOK_POSTER_MAX_W, max_h=VIDEOBOOK_POSTER_MAX_H):
        """Pad to 16:9 (contain). Never crop class artwork to fill the box."""
        src = img.convert("RGB")
        target_ratio = 16 / 9.0
        src_w = max(1, src.width)
        src_h = max(1, src.height)
        src_ratio = src_w / float(src_h)
        if src_ratio >= target_ratio:
            canvas_w = min(src_w, max_w)
            canvas_h = max(1, int(round(canvas_w / target_ratio)))
            if canvas_h > max_h:
                canvas_h = max_h
                canvas_w = max(1, int(round(canvas_h * target_ratio)))
        else:
            canvas_h = min(src_h, max_h)
            canvas_w = max(1, int(round(canvas_h * target_ratio)))
            if canvas_w > max_w:
                canvas_w = max_w
                canvas_h = max(1, int(round(canvas_w / target_ratio)))
        scale = min(canvas_w / float(src_w), canvas_h / float(src_h))
        fg_w = max(1, int(round(src_w * scale)))
        fg_h = max(1, int(round(src_h * scale)))
        fg = src if (fg_w == src_w and fg_h == src_h) else src.resize((fg_w, fg_h), Image.LANCZOS)
        canvas = Image.new("RGB", (canvas_w, canvas_h), POSTER_BG)
        canvas.paste(fg, ((canvas_w - fg_w) // 2, (canvas_h - fg_h) // 2))
        return canvas

    @staticmethod
    def _fit_poster(img, target_w=POSTER_WIDTH, target_h=POSTER_HEIGHT):
        src = img.convert("RGB")
        src_ratio = src.width / float(src.height or 1)
        target_ratio = target_w / float(target_h)
        if abs(src_ratio - target_ratio) < 0.08:
            return src.resize((target_w, target_h), Image.LANCZOS)

        cover_scale = max(target_w / src.width, target_h / src.height)
        bg = src.resize(
            (max(1, int(src.width * cover_scale)), max(1, int(src.height * cover_scale))),
            Image.LANCZOS,
        )
        left = max(0, (bg.width - target_w) // 2)
        top = max(0, (bg.height - target_h) // 2)
        bg = bg.crop((left, top, left + target_w, top + target_h))
        bg = bg.filter(ImageFilter.GaussianBlur(radius=22))
        bg = ImageEnhance.Brightness(bg).enhance(0.55)

        # 가로는 칸을 채우되, 너무 납작해지지 않게 높이를 조금 키운다.
        min_h = int(target_h * 0.62) if src_ratio > target_ratio else 1
        scale = max(min(target_w / src.width, target_h / src.height), min_h / float(src.height or 1))
        fg = src.resize((max(1, int(src.width * scale)), max(1, int(src.height * scale))), Image.LANCZOS)
        if fg.width > target_w:
            x = (fg.width - target_w) // 2
            fg = fg.crop((x, 0, x + target_w, fg.height))
        if fg.height > target_h:
            y = (fg.height - target_h) // 2
            fg = fg.crop((0, y, fg.width, y + target_h))
        bg.paste(fg, ((target_w - fg.width) // 2, (target_h - fg.height) // 2))
        return bg

    def _genre_from_book(self, book):
        for badge in book.get("badges") or []:
            if isinstance(badge, dict) and str(badge.get("type") or "").upper() == "CATEGORY":
                name = self._clean_text(badge.get("title"))
                if name:
                    return name
        categories = book.get("categories") or []
        if isinstance(categories, list):
            for cat in categories:
                name = self._clean_text((cat or {}).get("name") or (cat or {}).get("title"))
                if name:
                    return name
        return ""

    def _score_from_book(self, book):
        meta = book.get("meta") if isinstance(book.get("meta"), dict) else {}
        return self._score_from_meta(meta) or self._score_from_meta(book.get("star_set") or {})

    def _score_from_meta(self, meta):
        if not isinstance(meta, dict):
            return ""
        raw = meta.get("star_average")
        if raw in (None, "", 0, "0"):
            raw = meta.get("all")
        try:
            rating = float(raw)
        except (TypeError, ValueError):
            return ""
        if rating <= 0 or rating > 5:
            return ""
        return max(1, min(100, int(round(rating * 20))))

    def _optional_score(self, value):
        try:
            score = int(value)
        except (TypeError, ValueError):
            return None
        if score <= 0:
            return None
        return max(1, min(100, score))

    def _dedupe(self, results):
        seen = set()
        out = []
        for item in results:
            key = (item.get("service_type"), item.get("web_id") or item.get("link") or item.get("title"))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def _join_names(self, values):
        names = []
        for value in values or []:
            text = self._clean_text(value)
            if text and text not in names:
                names.append(text)
        return ", ".join(names)

    @staticmethod
    def _normalize(text):
        value = unescape(str(text or "")).lower()
        value = re.sub(r"\[[^\]]+\]", "", value)
        return re.sub(r"[\s\[\](){}<>.,!?:;\"'`~_/\\-]+", "", value)

    @staticmethod
    def _truthy(value):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _clean_text(text):
        value = unescape(str(text or ""))
        value = re.sub(r"<[^>]+>", "", value)
        return re.sub(r"\s+", " ", value).strip()
