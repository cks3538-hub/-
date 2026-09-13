# ============================================================
#  PC 정리 분석 스크립트 (읽기 전용)
#  - 파일을 옮기거나 지우지 않습니다. 인터넷에 아무것도 보내지 않습니다.
#  - 결과는 바탕화면\PC정리_분석결과\ 폴더에만 저장됩니다.
#      분석요약.txt : 현재 상태 + 정리 후 미리보기  (이 파일을 붙여넣어 주세요)
#      전체매핑.csv : 파일별 "현재 위치 -> 정리 후 위치/이름" 전체 목록 (엑셀로 열림)
# ============================================================
$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

# ---------- 1. 분석 대상 폴더 ----------
# 다른 드라이브도 보고 싶으면 아래 줄의 @() 안에 'D:\자료' 처럼 따옴표로 추가하세요.
$extraTargets = @()
# 이 햇수 이상 손 안 댄 문서는 03_보관 으로 보내는 것으로 계산합니다.
$archiveAfterYears = 3

$targets = @(
  [Environment]::GetFolderPath('Desktop')
  [Environment]::GetFolderPath('MyDocuments')
  (Join-Path $env:USERPROFILE 'Downloads')
  [Environment]::GetFolderPath('MyPictures')
  [Environment]::GetFolderPath('MyVideos')
  [Environment]::GetFolderPath('MyMusic')
) + $extraTargets
if ($env:PC_SCAN_TARGETS) { $targets = $env:PC_SCAN_TARGETS -split ';' }   # (테스트용)
$targets = @($targets | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique)

$desktop = [Environment]::GetFolderPath('Desktop')
if (-not $desktop) { $desktop = $HOME }
$desktopRoot = $targets | Where-Object { (Split-Path $_ -Leaf) -match '^(Desktop|바탕 화면)$' } | Select-Object -First 1
if (-not $desktopRoot) { $desktopRoot = $desktop }
$outDir = Join-Path $desktop 'PC정리_분석결과'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

# ---------- 2. 분류 규칙 ----------
$ext2cat = @{}
foreach ($e in 'jpg','jpeg','png','gif','heic','heif','bmp','webp','tif','tiff','raw','cr2','nef','arw','dng') { $ext2cat[$e] = '사진' }
foreach ($e in 'mp4','mov','avi','mkv','wmv','m4v','3gp','mts') { $ext2cat[$e] = '영상' }
foreach ($e in 'mp3','wav','m4a','flac','aac','wma') { $ext2cat[$e] = '음악' }
foreach ($e in 'pdf','doc','docx','hwp','hwpx','txt','rtf','odt','md','pages') { $ext2cat[$e] = '문서' }
foreach ($e in 'xls','xlsx','xlsm','csv','numbers') { $ext2cat[$e] = '엑셀' }
foreach ($e in 'ppt','pptx','key') { $ext2cat[$e] = '발표' }
foreach ($e in 'zip','7z','rar','tar','gz','egg','alz') { $ext2cat[$e] = '압축' }
foreach ($e in 'exe','msi','iso','apk','dmg') { $ext2cat[$e] = '설치파일' }
foreach ($e in 'lnk','url','ini','tmp','crdownload','part','bak') { $ext2cat[$e] = '정리대상' }

# 파일명에 이 단어가 있으면 참고자료의 해당 주제 폴더로 보냅니다. (단어는 자유롭게 추가/수정)
$topicRules = [ordered]@{
  '01_업무_비더리치' = '비더리치|betherich|리치|원장|일일보고'
  '02_재무_세금'     = '세금|국세|종소세|부가세|원천|연말정산|세무|회계|재무|은행|대출|보험|카드'
  '03_부동산'        = '부동산|아파트|등기|임대|임차|매매|분양|경매|중개'
  '04_법인_사업'     = '법인|사업자|정관|사업계획|계약|견적|인보이스|invoice'
  '05_강의_학습'     = '강의|교재|수업|강좌|스터디|자격증|공부|lecture'
  '06_개인_생활'     = '가족|여행|병원|건강|학교|이력서|자기소개|주민|여권'
}

function FmtSize($b) {
  if ($b -ge 1GB) { return ('{0:N1} GB' -f ($b / 1GB)) }
  if ($b -ge 1MB) { return ('{0:N0} MB' -f ($b / 1MB)) }
  return ('{0:N0} KB' -f ($b / 1KB))
}

# ---------- 3. 스캔 ----------
Write-Host ""
Write-Host "[1/3] 파일 스캔 중... (파일이 많으면 몇 분 걸릴 수 있습니다)" -ForegroundColor Cyan
$skip = '[\\/](AppData|\$RECYCLE\.BIN|node_modules|\.git|\.cache|__pycache__|PC정리_분석결과)([\\/]|$)'
$files = foreach ($t in $targets) {
  Write-Host "   - $t"
  Get-ChildItem -LiteralPath $t -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch $skip -and -not ($_.Attributes -band [IO.FileAttributes]::Hidden) }
}
$dirs = foreach ($t in $targets) {
  Get-ChildItem -LiteralPath $t -Recurse -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch $skip -and -not ($_.Attributes -band [IO.FileAttributes]::Hidden) }
}
$files = @($files); $dirs = @($dirs)

# ---------- 4. 파일별 분류 + 정리안 ----------
Write-Host "[2/3] 분류 및 정리안 계산 중..." -ForegroundColor Cyan
$now = Get-Date
$rows = foreach ($f in $files) {
  $ext  = $f.Extension.TrimStart('.').ToLower()
  $cat  = if ($ext2cat.ContainsKey($ext)) { $ext2cat[$ext] } else { '기타' }
  $date = $f.LastWriteTime
  if ($f.CreationTime -lt $date) { $date = $f.CreationTime }
  if ($date.Year -lt 1990) { $date = $f.LastWriteTime }
  $year = $date.Year
  $age  = ($now - $date).TotalDays / 365.25
  $base = [IO.Path]::GetFileNameWithoutExtension($f.Name)

  $topic = '00_미분류'
  foreach ($k in $topicRules.Keys) { if ($base -match $topicRules[$k]) { $topic = $k; break } }

  $target = switch ($cat) {
    '사진'     { "04_사진영상\$year" }
    '영상'     { "04_사진영상\$year" }
    '음악'     { "04_사진영상\00_음악" }
    '설치파일' { "02_참고자료\09_설치파일_프로그램" }
    '정리대상' { "99_삭제_검토" }
    default {
      if (($cat -eq '기타' -or $cat -eq '압축') -and $topic -eq '00_미분류') { "05_기타" }
      elseif ($age -ge $archiveAfterYears) { "03_보관\$year\$topic" }
      else { "02_참고자료\$topic\$year" }
    }
  }
  # 중복 탐지용 키: "(1)", "- 복사본", "- copy" 꼬리표를 뗀 이름 + 확장자 + 크기
  $dupKey = (($base -replace '\s*\(\d+\)\s*$', '') -replace '\s*-?\s*(복사본|copy)(\s*\(\d+\))?\s*$', '').Trim().ToLower() + "|$ext|$($f.Length)"

  $clean = ($base -replace '[\\/:*?"<>|#%&]', '') -replace '\s+', '_'
  $clean = ($clean -replace '_+', '_').Trim('_')
  if (-not $clean) { $clean = '이름없음' }
  $newBase = if ($clean -match '^\d{8}') { $clean } else { '{0:yyyyMMdd}_{1}' -f $date, $clean }
  $newName = if ($ext) { "$newBase.$ext" } else { $newBase }

  $flags = @()
  if ($base -match '복사본|\(\d+\)\s*$|- copy|\bcopy\b') { $flags += '중복의심' }
  if ($base -match '최종|final|진짜|수정본|\bnew\b|새 파일|제목 없음|untitled|무제') { $flags += '버전혼란' }
  if ($base -match '\s') { $flags += '공백' }
  if ($base -match '[#%&*?<>|"]') { $flags += '특수문자' }
  if ($age -ge $archiveAfterYears) { $flags += "${archiveAfterYears}년이상" }

  $src = ''
  foreach ($t in $targets) { if ($f.FullName.StartsWith($t, [StringComparison]::OrdinalIgnoreCase)) { $src = Split-Path $t -Leaf; break } }

  [pscustomobject]@{
    현재폴더     = $f.DirectoryName
    현재파일명   = $f.Name
    확장자       = $ext
    분류         = $cat
    크기MB       = [math]::Round($f.Length / 1MB, 2)
    기준일       = $date.ToString('yyyy-MM-dd')
    연도         = $year
    출처         = $src
    정리후폴더   = $target
    정리후파일명 = $newName
    점검사항     = ($flags -join ',')
    _len         = $f.Length
    _age         = $age
    _dup         = $dupKey
  }
}
$rows = @($rows)

# ---------- 5. 요약 작성 ----------
$L = New-Object System.Collections.Generic.List[string]
$totalSize = ($rows | Measure-Object _len -Sum).Sum
if (-not $totalSize) { $totalSize = 0 }

$L.Add('=====================================================')
$L.Add(('  PC 정리 분석 요약   (생성: {0:yyyy-MM-dd HH:mm})' -f $now))
$L.Add('  * 읽기 전용 분석입니다. 파일을 옮기거나 지우지 않았습니다.')
$L.Add('=====================================================')
$L.Add('')
$L.Add('■ 스캔 폴더')
foreach ($t in $targets) { $L.Add("  - $t") }
$L.Add('')
$L.Add(('■ 전체: 파일 {0:N0}개 / 폴더 {1:N0}개 / {2}' -f $rows.Count, $dirs.Count, (FmtSize $totalSize)))
$L.Add('')

$L.Add('■ [현재] 출처별 (파일 수 / 용량)')
foreach ($g in ($rows | Group-Object 출처 | Sort-Object Count -Descending)) {
  $L.Add(('  {0,-14}: {1,7:N0}개 / {2}' -f $g.Name, $g.Count, (FmtSize (($g.Group | Measure-Object _len -Sum).Sum))))
}
$L.Add('')

$L.Add('■ [현재] 종류별 (파일 수 / 용량)')
foreach ($g in ($rows | Group-Object 분류 | Sort-Object Count -Descending)) {
  $L.Add(('  {0,-8}: {1,7:N0}개 / {2}' -f $g.Name, $g.Count, (FmtSize (($g.Group | Measure-Object _len -Sum).Sum))))
}
$L.Add('')

$L.Add('■ [현재] 연도별 (기준일 = 수정일/생성일 중 빠른 날짜)')
foreach ($g in ($rows | Group-Object 연도 | Sort-Object Name -Descending)) {
  $L.Add(('  {0}: {1,7:N0}개 / {2}' -f $g.Name, $g.Count, (FmtSize (($g.Group | Measure-Object _len -Sum).Sum))))
}
$L.Add('')

$desktopFiles  = @($files | Where-Object { $_.DirectoryName -eq $desktopRoot }).Count
$downloadFiles = @($rows | Where-Object { $_.출처 -eq 'Downloads' }).Count
$newFolderDirs = @($dirs | Where-Object { $_.Name -match '^(새 폴더|New folder|제목 없음|무제)' }).Count
$emptyDirs     = @($dirs | Where-Object { -not (Get-ChildItem -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue | Select-Object -First 1) }).Count
$dupGroups     = @($rows | Group-Object _dup | Where-Object { $_.Count -gt 1 })
$dupCount      = 0; foreach ($g in $dupGroups) { $dupCount += ($g.Count - 1) }
$dupSize       = 0; foreach ($g in $dupGroups) { $dupSize += ($g.Group[0]._len * ($g.Count - 1)) }
$verFiles      = @($rows | Where-Object { $_.점검사항 -match '버전혼란' }).Count
$badNameFiles  = @($rows | Where-Object { $_.점검사항 -match '공백|특수문자' }).Count
$oldRows       = @($rows | Where-Object { $_._age -ge $archiveAfterYears })
$oldSize       = ($oldRows | Measure-Object _len -Sum).Sum; if (-not $oldSize) { $oldSize = 0 }
$maxDepth = 0
foreach ($d in $dirs) {
  foreach ($t in $targets) {
    if ($d.FullName.StartsWith($t, [StringComparison]::OrdinalIgnoreCase)) {
      $depth = @($d.FullName.Substring($t.Length) -split '[\\/]' | Where-Object { $_ }).Count
      if ($depth -gt $maxDepth) { $maxDepth = $depth }
      break
    }
  }
}

$L.Add('■ [현재] 정리 신호')
$L.Add(('  - 바탕화면에 직접 놓인 파일      : {0:N0}개  (권장 10개 이하)' -f $desktopFiles))
$L.Add(('  - 다운로드 폴더 파일             : {0:N0}개  (권장 0개 - 대기실)' -f $downloadFiles))
$L.Add(('  - "새 폴더" 이름 그대로인 폴더    : {0:N0}개' -f $newFolderDirs))
$L.Add(('  - 빈 폴더                        : {0:N0}개' -f $emptyDirs))
$L.Add(('  - 중복 의심(이름+크기 동일)       : {0:N0}개 / {1} 낭비' -f $dupCount, (FmtSize $dupSize)))
$L.Add(('  - 버전혼란 이름(최종/final/복사본) : {0:N0}개' -f $verFiles))
$L.Add(('  - 공백/특수문자 포함 파일명       : {0:N0}개' -f $badNameFiles))
$L.Add(('  - {2}년 이상 손 안 댄 파일          : {0:N0}개 / {1}' -f $oldRows.Count, (FmtSize $oldSize), $archiveAfterYears))
$L.Add(('  - 가장 깊은 폴더 단계             : {0}단계  (권장 4단계 이하)' -f $maxDepth))
$L.Add('')

$L.Add('■ [현재] 용량 TOP 10')
foreach ($r in ($rows | Sort-Object _len -Descending | Select-Object -First 10)) {
  $L.Add(('  {0,9}  {1}' -f (FmtSize $r._len), (Join-Path $r.현재폴더 $r.현재파일명)))
}
$L.Add('')

$L.Add('■ [현재] 파일이 가장 많은 폴더 TOP 10')
foreach ($g in ($rows | Group-Object 현재폴더 | Sort-Object Count -Descending | Select-Object -First 10)) {
  $L.Add(('  {0,6:N0}개  {1}' -f $g.Count, $g.Name))
}
$L.Add('')

$L.Add('■ [현재] 중복 의심 묶음 (이름+크기 같음, "(1)" "복사본" 꼬리표는 무시) - 큰 것부터 10묶음')
foreach ($g in ($dupGroups | Sort-Object { $_.Group[0]._len } -Descending | Select-Object -First 10)) {
  $L.Add(('  {0}  x{1}  ({2})' -f $g.Group[0].현재파일명, $g.Count, (FmtSize $g.Group[0]._len)))
  foreach ($r in $g.Group) { $L.Add(('      - {0}' -f (Join-Path $r.현재폴더 $r.현재파일명))) }
}
if ($dupGroups.Count -eq 0) { $L.Add('  (없음)') }
$L.Add('')

$L.Add('-----------------------------------------------------')
$L.Add('■ [정리 후] 예상 폴더 구조 (파일 수 / 용량)')
$L.Add('  01_진행중  : (자동 분류 안 함 - 진행 중 프로젝트는 직접 지정)')
$printed = @{}
foreach ($g in ($rows | Group-Object 정리후폴더 | Sort-Object Name)) {
  $parts = $g.Name -split '\\'
  for ($i = 0; $i -lt $parts.Count; $i++) {
    $key = ($parts[0..$i] -join '\')
    if ($printed.ContainsKey($key)) { continue }
    $printed[$key] = $true
    $indent = '  ' * ($i + 1)
    if ($i -eq $parts.Count - 1) {
      $sz = ($g.Group | Measure-Object _len -Sum).Sum
      $L.Add(('{0}{1}  : {2:N0}개 / {3}' -f $indent, $parts[$i], $g.Count, (FmtSize $sz)))
    } else {
      $L.Add("$indent$($parts[$i])")
    }
  }
}
$L.Add('')

$L.Add('■ [정리 후] 변환 예시 (무작위 20건)')
foreach ($r in ($rows | Get-Random -Count ([Math]::Min(20, $rows.Count)))) {
  $L.Add(('  [현재] {0}' -f (Join-Path $r.현재폴더 $r.현재파일명)))
  $L.Add(('  [이후] {0}\{1}' -f $r.정리후폴더, $r.정리후파일명))
  if ($r.점검사항) { $L.Add(('         점검: {0}' -f $r.점검사항)) }
  $L.Add('')
}
$L.Add('=====================================================')
$L.Add(('  전체 목록(CSV): {0}' -f (Join-Path $outDir '전체매핑.csv')))
$L.Add('=====================================================')

# ---------- 6. 저장 (UTF-8 BOM: 메모장/엑셀에서 한글 깨짐 방지) ----------
$utf8bom = New-Object System.Text.UTF8Encoding($true)
$sumPath = Join-Path $outDir '분석요약.txt'
$csvPath = Join-Path $outDir '전체매핑.csv'
[IO.File]::WriteAllLines($sumPath, $L, $utf8bom)
$csvLines = $rows | Select-Object 출처, 현재폴더, 현재파일명, 확장자, 분류, 크기MB, 기준일, 연도, 정리후폴더, 정리후파일명, 점검사항 | ConvertTo-Csv -NoTypeInformation
[IO.File]::WriteAllLines($csvPath, [string[]]$csvLines, $utf8bom)

Write-Host "[3/3] 완료!  결과: $outDir" -ForegroundColor Green
Write-Host "      분석요약.txt 가 메모장으로 열립니다. 전체 선택(Ctrl+A) → 복사(Ctrl+C) 해서 붙여넣어 주세요."
if ($env:OS -eq 'Windows_NT') {
  Start-Process notepad.exe $sumPath
  Start-Process explorer.exe $outDir
}
