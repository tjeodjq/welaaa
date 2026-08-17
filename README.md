# 윌라 전용 플러그인

BookOasis용 윌라(welaaa.com) 메타데이터 검색 플러그인입니다.
| 항목 | 값 |
| :--- | :--- |
| 플러그인 버전 | `1.1.13` |
| 플러그인 ID | `welaaa` |
| 클래스 | `WelaaaMetadataProvider` |
| 유형 | 검색형 메타데이터 제공자 |

## 설치

### Git URL 설치

1. BookOasis **환경설정 → 플러그인 매니저 → Git 저장소 URL 설치**
2. URL: `https://github.com/tjeodjq/welaaa`
3. **Git 설치**

이미 `welaaa`가 있으면 덮어쓰기(force)를 허용하면 됩니다. BookOasis Git URL 설치는 **공개 저장소만** 됩니다. 비공개로 두면 ZIP 설치를 사용하세요.

### ZIP 설치

1. [Releases](https://github.com/tjeodjq/welaaa/releases)에서 `welaaa-1.1.13.zip`을 받습니다. 비공개 저장소는 Git URL 설치가 되지 않으므로 ZIP을 사용하세요.
2. BookOasis **환경설정 → 플러그인 매니저 → ZIP 설치**로 올립니다.

설치 후 목록에서 `윌라 도서 검색`을 활성화하고, 도서 우클릭 메타 검색에서 선택합니다.

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

## 식별자 기반 새로고침

비디오북 Health/카테고리의 「메타·포스터 새로고침」은 제목 검색 없이 `link`의 `welaaa.com/(video|audio|ebook)/detail/{id}` 만 사용합니다. 기본은 표지만 16:9 캔버스에 **여백으로 맞춤**(크롭 없음)으로 다시 받고 `metadata_locked`는 유지합니다. 검색 결과의 세로 `list` 썸네일로 덮지 않습니다.

## 설정

- 오디오북 / 전자책 / 비디오북 / 클래스 검색
- 정확한 제목만
- 성인 결과 포함
- 같은 시리즈 전체에 표지 적용
- 포스터를 2:3 비율로 맞춤 (오디오북·전자책만. 비디오북·클래스는 16:9 유지)
- 선택: HTTP(S) 프록시, 윌라 Cookie

## 폴더 구조

Git URL 설치용으로 플러그인 파일이 저장소 루트에 있습니다.

```text
__init__.py
welaaa.py
VERSION
README.md
```
