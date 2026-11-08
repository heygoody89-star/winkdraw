# WinkDraw ✦

> 📚 **이 저장소는 패스트캠퍼스(FastCampus) 강의자료입니다.**

웹캠과 두 손으로 화면 위에 그림을 그리는 윈도우 프로그램이에요. 그림은 네온 형광펜처럼 빛나고, 반짝임이 나며, 글씨에서 가루가 떨어집니다.
모든 창 위에 투명하게 떠 있고 마우스 클릭은 아래 창으로 그대로 전달돼요.

## 실행
`run.bat`을 더블클릭하세요. 작업표시줄 트레이에 ✦ 아이콘이 생깁니다.

## 조작
| 동작 | 결과 |
|---|---|
| 오른손 검지 | 커서 이동 |
| 왼손 주먹 ✊ | 그리기 |
| 왼손 펴기 ✋ / 화면에 안 보임 | 커서만 이동 |
| 왼손 브이 ✌ 1초 | 다음 색 (네온 핑크 → 레인보우 → 시안 → 라임 → 옐로 → 바이올렛 → 코랄) |
| 오른손 손바닥 ✋ 2초 (그리는 중이 아닐 때) | 전체 지우기 |

단축키 `Ctrl+Alt` + `Z` 되돌리기 · `C` 지우기 · `N` 색 · `[` `]` 굵기 · `P` 카메라 미리보기 · `S` 그림 숨김 · `D` 일시정지 · `H` 도움말 · `Q` 종료

## 조정 (`winkdraw.py` 맨 위)
- `ACTIVE_X`, `ACTIVE_Y`: 카메라 화면 중 모니터 전체에 대응하는 영역 (미리보기의 노란 사각형)
- `PEN_ON_FRAMES`, `PEN_OFF_FRAMES`: 주먹 인식 반응 속도
- `PALETTE`, `PEN_WIDTHS`, `GLOW_LAYERS`: 색, 굵기, 빛 번짐

## 새 PC에 설치할 때
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
`models\hand_landmarker.task`도 같이 복사해야 해요. 오류가 나면 `winkdraw.log`에 기록됩니다.

손 인식 모델(`hand_landmarker.task`)은 Google MediaPipe에서 Apache 2.0 라이선스로 배포하는 모델입니다.
