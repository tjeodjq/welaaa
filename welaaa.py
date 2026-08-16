# -*- coding: utf-8 -*-
"""윌라(welaaa.com) 제목·web_id 메타데이터 검색 플러그인."""
import hashlib
import io
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape

from PIL import Image
from plugins.metadata.base import BaseMetadataProvider

PLUGIN_VERSION = "1.1.3"
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
            "key": "PROXY_URL",
            "label": "HTTP(S) 프록시 URL",
            "type": "password",
            "required": False,
            "default": "",
            "description": "선택. http://host:port 형식. 검색·상세·표지 다운로드에 적용됩니다.",
        },
        {
            "key": "WELAAA_COOKIE",
            "label": "윌라 Cookie",
            "type": "password",
            "required": False,
            "default": "",
            "description": "연령 제한 작품이 필요할 때만 입력합니다.",
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
                kinds = [parsed["kind"]] if parsed.get("kind") else ("audio", "ebook", "video")
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
        kind = self._kind_from_item(item_data)
        if web_id:
            detailed = self.fetch_detail_item(web_id, kind, cfg)
            if detailed:
                item_data = {**detailed, **{k: v for k, v in item_data.items() if v}}
                item_data = self._restore_original_title(item_data)

        gateway = self.get_db_gateway(db_type)
        table = self._media_table(db_type)
        try:
            book = gateway.fetch_one(
                f"""
                SELECT id, file_path, library_id, series_name
                FROM {table}
                WHERE id = ? AND COALESCE(is_deleted, 0) = 0
                """,
                (book_id,),
            )
            if not book:
                return False, "대상 도서를 찾을 수 없습니다."

            cover_filename = self._save_cover(book, item_data.get("cover"), cfg, db_type)
            description = self._clean_text(item_data.get("description"))
            author = self._clean_text(item_data.get("author"))
            publisher = self._clean_text(item_data.get("publisher")) or "윌라"
            isbn = self._clean_text(
                item_data.get("isbn") or item_data.get("web_id") or item_data.get("external_id")
            )
            release_date = self._clean_text(item_data.get("pubDate") or item_data.get("release_date"))
            link = item_data.get("link") or ""
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
            return True, f'"{title}" 윌라 메타데이터가 반영되었습니다.{extra}'
        except Exception as e:
            return False, f"DB 업데이트 오류: {e}"

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
        resolved = self._kind_from_course(book, kind)
        return self.item_from_book(book, resolved)

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
        cover = self._best_cover(book)
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
        cover = self._clean_text(
            hit.get("cover_image_url")
            or hit.get("klass_cover_image_url")
            or ((hit.get("cover_image_info") or {}).get("url") if isinstance(hit.get("cover_image_info"), dict) else "")
        )
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

    def _save_cover(self, book, cover_url, cfg, db_type=None):
        if not cover_url:
            return None
        try:
            dest_path, cover_filename = self._cover_location(
                book["library_id"], book["file_path"], db_type
            )
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            req = urllib.request.Request(
                cover_url,
                headers={"User-Agent": DEFAULT_USER_AGENT, "Referer": f"{SITE}/"},
            )
            opener = self._opener(cfg)
            with opener.open(req, timeout=15) as response:
                img_data = response.read()
            with Image.open(io.BytesIO(img_data)) as img:
                img.save(dest_path, "WEBP", quality=82)
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

    def _cover_location(self, library_id, file_path, db_type=None):
        if not file_path:
            raise ValueError("표지 파일명을 생성할 도서 경로가 없습니다.")
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        book_hash = hashlib.md5(str(file_path).encode("utf-8")).hexdigest()
        filename = f"book_{book_hash}.webp"
        kind = str(db_type or "").strip().lower()
        if kind in ("audiobook", "videobook"):
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

    def _kind_from_item(self, item):
        service = self._clean_text((item or {}).get("service_type")).lower()
        for kind, info in KIND_INFO.items():
            if service == info["service"] or service == kind:
                return kind
        link = self._clean_text((item or {}).get("link")).lower()
        if "/ebook/" in link:
            return "ebook"
        if "/video/" in link:
            return "klass"
        return "audio"

    def _normalize_kind(self, kind):
        raw = str(kind or "audio").strip().lower()
        aliases = {
            "audiobook": "audio",
            "class": "klass",
            "course": "klass",
            "videobook": "video",
        }
        raw = aliases.get(raw, raw)
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

    def _best_cover(self, book):
        images = book.get("images") if isinstance(book.get("images"), dict) else {}
        for key in ("large", "cover", "book", "main", "big", "wide", "list"):
            url = self._clean_text(images.get(key))
            if url.startswith("http"):
                return url
        for key in ("cover_image_url", "klass_cover_image_url", "image_url"):
            url = self._clean_text(book.get(key))
            if url.startswith("http") and "placeholder" not in url and not url.endswith("/"):
                return url
        info = book.get("cover_image_info_list") or []
        if isinstance(info, list):
            for row in info:
                if isinstance(row, dict) and str(row.get("image_source_type_string") or "") == "original-image":
                    url = self._clean_text(row.get("url"))
                    if url.startswith("http"):
                        return url
            for row in info:
                url = self._clean_text((row or {}).get("url"))
                if url.startswith("http"):
                    return url
        return self._clean_text(book.get("cover_image_url"))

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
