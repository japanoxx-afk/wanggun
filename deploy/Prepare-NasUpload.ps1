# NAS 에 올릴 파일을 deploy\upload\wanggun-live\ 에 모은다. data 폴더는 만들지 않는다 (NAS 계정·전적 보호).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $PSScriptRoot "upload\wanggun-live"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $out "app") | Out-Null
Copy-Item (Join-Path $root "dummyserver.py") (Join-Path $out "app\dummyserver.py")
Copy-Item (Join-Path $PSScriptRoot "compose.yaml") $out
Copy-Item (Join-Path $PSScriptRoot "NAS_DEPLOY.md") $out
$ver = (Select-String -Path (Join-Path $root "dummyserver.py") -Pattern '^SERVER_VERSION = "([^"]+)"').Matches[0].Groups[1].Value
$commit = ""
try { $commit = (git -C $root rev-parse --short HEAD 2>$null) } catch { }
"server $ver  commit $commit  prepared $(Get-Date -Format 'yyyy-MM-dd HH:mm')" |
    Set-Content -Encoding UTF8 (Join-Path $out "app\DEPLOYED_VERSION.txt")
Write-Host "준비 완료: $out"
Write-Host "File Station 의 game-servers/wanggun-live/ 에 이 폴더 내용을 덮어쓰기로 올리세요."
