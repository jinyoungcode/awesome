#Requires -Version 5.1
<#
  나는야 주식왕 — 윈도우 PC 준비 스크립트 (모의투자 전용)
  실행: 저장소 폴더에서 PowerShell 을 열고
      powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
  하는 일: Python 3.11+·git 확인 → 가상환경(.venv) 생성 → 패키지 설치 → 테스트 → 비밀값 파일 자리 만들기(값은 넣지 않음)
          → 오프라인 훈련 2종(증권사 접속 없음) → 결과를 한글로 요약
#>
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONUTF8 = '1'
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Results = New-Object System.Collections.ArrayList

function Add-Result([string]$Name, [bool]$Ok, [string]$Detail) {
    $mark = '실패'
    if ($Ok) { $mark = '통과' }
    [void]$Results.Add([pscustomobject]@{ Item = $Name; Result = $mark; Detail = $Detail })
    Write-Host ("[{0}] {1} {2}" -f $mark, $Name, $Detail)
}

function Find-Python {
    foreach ($cand in @('py -3', 'python')) {
        $parts = $cand -split ' '
        $exe = $parts[0]
        $rest = @($parts | Select-Object -Skip 1)
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            $v = & $exe @rest -c "import sys;print('%d.%d.%d' % sys.version_info[:3])" 2>$null
            if ($v -match '^(\d+)\.(\d+)\.(\d+)$') {
                return [pscustomobject]@{ Exe = $exe; Rest = $rest; Version = [version]$v }
            }
        }
    }
    return $null
}

Write-Host "== 나는야 주식왕: 윈도우 준비 시작 ($RepoRoot) =="

# 0. 저장소 위치 (OneDrive 안이면 경고 — 감사 로그·장부가 클라우드로 동기화됨)
$inOneDrive = $false
foreach ($od in @($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial)) {
    if ($od -and $RepoRoot.StartsWith($od, [System.StringComparison]::OrdinalIgnoreCase)) { $inOneDrive = $true }
}
if ($inOneDrive) {
    Add-Result '저장소 위치' $false 'OneDrive 폴더 안입니다. C:\stockking 처럼 OneDrive 밖으로 옮기길 권장합니다'
} else {
    Add-Result '저장소 위치' $true $RepoRoot
}

# 1. Python 3.11+
$py = Find-Python
if (-not $py) {
    Add-Result 'Python' $false '찾을 수 없음 — python.org 에서 3.11 이상 설치(설치 시 "Add python.exe to PATH" 체크) 후 다시 실행'
} elseif ($py.Version -lt [version]'3.11.0') {
    Add-Result 'Python' $false ("버전 {0} — 3.11 이상 필요" -f $py.Version)
    $py = $null
} else {
    Add-Result 'Python' $true ("{0} ({1})" -f $py.Version, $py.Exe)
}

# 2. git
if (Get-Command git -ErrorAction SilentlyContinue) {
    $branch = (& git rev-parse --abbrev-ref HEAD 2>$null)
    Add-Result 'git' $true ("브랜치 {0}" -f $branch)
} else {
    Add-Result 'git' $false 'git 이 없습니다 — git-scm.com 에서 설치 (코드 업데이트에 필요)'
}

# 3. 가상환경 + 설치 + 테스트
$vpy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if ($py) {
    if (-not (Test-Path $vpy)) { & $py.Exe @($py.Rest) -m venv .venv }
    if (Test-Path $vpy) {
        Add-Result '가상환경' $true '.venv'
        & $vpy -m pip install --quiet --upgrade pip | Out-Null
        & $vpy -m pip install --quiet -e ".[dev]"
        Add-Result '패키지 설치' ($LASTEXITCODE -eq 0) 'pip install -e ".[dev]"'
        $out = & $vpy -m pytest -q -p no:cacheprovider
        $last = ($out | Select-Object -Last 1)
        Add-Result '테스트' ($LASTEXITCODE -eq 0) $last
    } else {
        Add-Result '가상환경' $false '.venv 생성 실패'
    }
}

# 4. 비밀값 파일 자리 (값은 넣지 않음, 본인만 읽기 권한)
$secDir = Join-Path $env:APPDATA 'stockking'
$secFile = Join-Path $secDir 'secrets.env'
New-Item -ItemType Directory -Force -Path $secDir | Out-Null
if (-not (Test-Path $secFile)) {
    $template = @(
        '# 나는야 주식왕 — KIS 모의투자 비밀값. 이 파일은 저장소 밖에 있고 본인만 읽을 수 있습니다.',
        '# = 뒤에 값을 붙여 넣고 저장하세요(따옴표 없이). 이 파일을 채팅·메신저·저장소에 올리지 마세요.',
        'KIS_PAPER_APP_KEY=',
        'KIS_PAPER_APP_SECRET=',
        'KIS_PAPER_ACCOUNT_NO=',
        'KIS_PAPER_ACCOUNT_PRODUCT=01'
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($secFile, $template + "`r`n", (New-Object System.Text.UTF8Encoding $false))
    $created = '새로 만듦'
} else {
    $created = '이미 있음(덮어쓰지 않음)'
}
& icacls $secFile /inheritance:r /grant:r ("{0}:(R,W)" -f $env:USERNAME) | Out-Null
Add-Result '비밀값 파일' ($LASTEXITCODE -eq 0) ("{0} — {1}, 본인만 읽기 권한" -f $secFile, $created)
$filled = (Select-String -Path $secFile -Pattern '^KIS_PAPER_APP_KEY=.+' -Quiet)
if (-not $filled) {
    Write-Host ("  → 메모장으로 열어 키를 넣으세요:  notepad `"{0}`"" -f $secFile)
}

# 5. 오프라인 훈련 (증권사 접속 없음)
if (Test-Path $vpy) {
    & $vpy -m stockking.ops.restart_drill | Out-Host
    Add-Result '재시작 복구 훈련(오프라인)' ($LASTEXITCODE -eq 0) 'restart_drill'
    & $vpy -m stockking.ops.killswitch_drill --operator '대표' | Out-Host
    Add-Result '킬스위치 훈련(오프라인)' ($LASTEXITCODE -eq 0) 'killswitch_drill'
}

Write-Host ''
Write-Host '== 요약 =='
$Results | Format-Table -AutoSize -Property @{ Label = '항목'; Expression = { $_.Item } }, @{ Label = '결과'; Expression = { $_.Result } }, @{ Label = '내용'; Expression = { $_.Detail } } | Out-Host
$fail = @($Results | Where-Object { $_.Result -eq '실패' }).Count
if ($fail -eq 0) {
    Write-Host '모두 통과했습니다. 다음: 비밀값 파일에 모의투자 키를 넣고  scripts\paper_day.ps1 -Step check  를 실행하세요.'
    exit 0
} else {
    Write-Host ("실패 {0}건 — 위 '내용'을 보고 조치한 뒤 다시 실행하세요." -f $fail)
    exit 1
}
