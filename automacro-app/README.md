# 오토매크로 안드로이드 앱 (React Native)

MacroDroid 구조(트리거 → 액션 → 제약 조건)를 따르는 안드로이드 자동화 앱입니다.

## 감지 (트리거)
알림 수신(카카오톡·은행·모든 앱) · 문자 수신 · 전화 수신/부재중/종료 · 시간/요일 · 반복 간격 · 배터리 · 충전기 · 화면 켜짐/꺼짐/잠금해제 · 이어폰 · 인터넷 · 재부팅 · 바로가기 · 변수 값 변경

## 실행 (액션)
카톡 등 알림에 자동 답장 · 문자 보내기 · 알림 지우기 · 알림 표시 · 팝업 · TTS · 진동 · 소리 · 앱 실행 · 웹사이트 · HTTP 웹훅 · 클립보드 · 변수 · If/Else/End If · 대기 · 다른 매크로 실행/켜기/끄기 · 중단 · 로그

## 구조
- `android/app/src/main/java/com/automacro/` — 네이티브 감지기
  - `NotificationListener.kt` 카톡 등 알림 감지 + 답장(RemoteInput)
  - `SmsReceiver.kt`, `CallReceiver.kt`, `BootReceiver.kt`, `AlarmReceiver.kt`
  - `KeepAliveService.kt` 항상 실행(포그라운드 서비스): 충전·화면·배터리·이어폰·네트워크
  - `MacroTaskService.kt` 이벤트를 JS 엔진(Headless JS)으로 전달
  - `AutoMacroModule.kt` JS에서 부르는 기능(문자 보내기, TTS, 알림 등)
- `src/engine.js` 매크로 실행 엔진, `src/catalog.js` 트리거/액션 목록, `src/App.js` 화면

## 빌드
`.github/workflows/automacro-android.yml` 이 푸시할 때마다 APK를 만들고
GitHub Releases `automacro-latest` 에 올립니다.
