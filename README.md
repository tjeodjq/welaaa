# 윌라 전용 플러그인

BookOasis용 윌라(welaaa.com) 메타데이터 검색 플러그인입니다. 네이버·카카오·리디 플러그인과는 별도입니다.

| 항목 | 값 |
| :--- | :--- |
| 플러그인 버전 | `1.1.0` |
| 플러그인 ID | `willa` |
| 클래스 | `WillaMetadataProvider` |
| 유형 | 검색형 메타데이터 제공자 |

## 설치

1. [Releases](https://github.com/tjeodjq/willa/releases)에서 `willa-1.1.0.zip`을 받습니다.
2. BookOasis **환경설정 → 플러그인 매니저 → ZIP 설치**로 올립니다.
3. 목록에서 `윌라 도서 검색`을 활성화합니다.
4. 도서 우클릭 메타 검색 드롭다운에서 `윌라 도서 검색`을 선택합니다.

이미 설치한 경우 덮어쓰기(force)를 허용하면 됩니다.

## 검색 대상

- 오디오북
- 전자책
- 비디오북
- 클래스 (`https://www.welaaa.com/video`)

## 검색 방법

- 제목: `신 퇴마록`
- web_id: `213718`
- URL: `https://www.welaaa.com/audio/detail/213718`
- 클래스/비디오: `https://www.welaaa.com/video/detail/1510`

적용 필드: 저자, 출판사, 소개, ISBN, 출간일, 장르, 태그, 평점, 표지, 링크. 오디오북은 낭독자도 반영합니다.

## 설정

- 오디오북 / 전자책 / 비디오북 / 클래스 검색
- 정확한 제목만
- 성인 결과 포함
- 같은 시리즈 전체에 표지 적용
- 선택: HTTP(S) 프록시, 윌라 Cookie

## 폴더 구조

```text
willa/
├── __init__.py
├── willa.py
├── VERSION
└── README.md
```
