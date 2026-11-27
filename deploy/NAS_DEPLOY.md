# 태조왕건 더미서버 NAS 배포 순서

라크무(rakmu-live)·삼국지천명2(samchun-live)·전적 사이트(rtsarena)와 같은 방식이다. 이미지 빌드·SSH 없이 **File Station + Portainer** 만 쓴다.
서버는 Python 표준 라이브러리만 써서 추가 설치가 없다 (`python:3.12-slim` 이 볼륨의 `dummyserver.py` 를 그대로 실행).

## 0. 경로와 포트

| File Station | 컨테이너 안 | 내용 |
|---|---|---|
| `game-servers/wanggun-live/app/dummyserver.py` | `/mnt/game-servers/wanggun-live/app/dummyserver.py` | 서버 코드 |
| `game-servers/wanggun-live/data/` | `/mnt/game-servers/wanggun-live/data` | `accounts.json`(계정·전적), `matches.json`(전적 사이트용), `logs/` |
| `game-servers/wanggun-live/backups/` | (서버는 안 씀) | 사람이 백업 보관 |

| 포트 | 용도 | 공유기에서 열기 |
|---|---|---|
| TCP **6112** | 로비 (로그인·채팅·방·결과 보고) | **필요** (외부 6112 → VM 6112) |
| TCP **9000** | 인증/버전 확인 | **필요** (외부 9000 → VM 9000) |
| UDP **9000** | 게임이 보내는 UDP | **필요** (외부 9000 → VM 9000) |
| TCP 9010 | 전적·랭킹 JSON (`/healthz`, `/matches.json`, `/ranking.json`) | 필요 없음 (확인용) |

### 게임 서버 VM 전체 포트 (겹치지 않음 — 2026-11-28 각 저장소 compose 기준)

| 스택 | 네트워크 | 쓰는 VM 포트 |
|---|---|---|
| rakmu (라크무) | 포트 매핑 | TCP 30010, TCP 30011, UDP 30012 |
| rakmu-test | host | TCP 30000, TCP 30001, UDP 30002 |
| samchun (삼국지천명2) | host | TCP 4800, TCP 4901, TCP 4902 |
| rtsarena (전적 사이트) | 포트 매핑 | TCP 30013 |
| **wanggun (태조왕건)** | host | **TCP 6112, TCP 9000, UDP 9000, TCP 9010** |
| 사용 금지 | | 5001 (NAS 관리), 9443 (Portainer) |

공유기 전달 규칙도 겹치지 않는다: 라크무 11223·11225, 삼국지천명2 4800·4901, 태조왕건 6112·9000.
플레이어끼리 직접 연결(P2P)하는 포트(라크무 UDP 11223, 태조왕건 6112)는 각 플레이어 PC 쪽이라 NAS 와 무관하다.

> **확인할 것 하나**: 9000 은 Portainer 의 옛 기본 HTTP 포트다. 지금 Portainer 는 9443 으로 쓰고 있지만,
> 게임 서버 VM 안에서 Portainer 가 9000 도 열어 두었으면 태조왕건 서버만 9000 을 못 연다.
> host 네트워크 컨테이너(samchun 등)의 포트는 Portainer 의 Published Ports 에 안 보이므로, **배포 후 healthz 로 확인**한다 (아래 3-5).
>
> **기존 서버에는 영향 없음**: 이미 실행 중인 라크무·삼국지천명2 가 쓰는 포트는 새 컨테이너가 빼앗을 수 없다 (리눅스는 사용 중인 포트에
> 두 번째 프로그램이 bind 하면 실패한다. 서버 2.1 은 UDP 도 포트 공유 옵션을 쓰지 않는다). 겹치면 **태조왕건 서버만** 그 포트를 못 열고
> `[PORT CONFLICT]` 로그 + healthz `ok:false`(503) → Portainer 에 unhealthy 로 보인다. 이 스택을 배포·재시작해도 다른 스택은 재시작되지 않는다.
>
> 게임은 6112/9000 번호를 고정해서 쓰므로 외부 포트와 VM 포트 번호가 **같아야** 한다.
> 공유기는 30000~30999 만 VM 으로 전달하므로 **6112 TCP, 9000 TCP/UDP 전달 규칙을 공유기에 추가**해야 한다 (서버 관리자에게 요청).
>
> 플레이어끼리 게임은 방장 PC 로 직접(P2P, 6112) 접속한다 — 이건 NAS 가 아니라 각 플레이어 PC/공유기 쪽 문제다 (지금과 같음).

## 1. 준비 (내 PC, 이 저장소 폴더)

```powershell
.\deploy\Prepare-NasUpload.ps1
```

`deploy\upload\wanggun-live\` 에 올릴 파일이 모인다 (`app\dummyserver.py`, `app\DEPLOYED_VERSION.txt`, `compose.yaml`, `NAS_DEPLOY.md`).
data 폴더는 들어 있지 않으므로 올려도 NAS 의 계정·전적이 덮어써지지 않는다.

### 기존 계정·전적 옮기기 (선택)

지금까지 PC 에서 돌린 계정·전적을 그대로 쓰려면 서버를 끈 뒤
`%LOCALAPPDATA%\TaejoWanggeonDummyServer\accounts.json` 을 아래 2-4 단계에서 `wanggun-live/data/` 에 올린다 (컨테이너를 켜기 **전에**).

## 2. File Station (https://akansas.synology.me:5001/)

1. `game-servers` 를 연다. 다른 프로젝트 폴더(rakmu-live, samchun-live, rtsarena 등)는 건드리지 않는다.
2. 새 폴더 `wanggun-live` → 그 안에 `app`, `data`, `backups` 폴더를 만든다.
3. `wanggun-live` 에 `compose.yaml`, `NAS_DEPLOY.md` 를 올린다.
4. `wanggun-live/app` 에 `dummyserver.py`, `DEPLOYED_VERSION.txt` 를 올린다. (기존 계정을 옮기면 `data` 에 `accounts.json`)
   최종 모양:
   ```
   game-servers/wanggun-live/compose.yaml
   game-servers/wanggun-live/app/dummyserver.py   ← 이 위치여야 함
   game-servers/wanggun-live/data/                 (비어 있거나 accounts.json)
   ```

## 3. Portainer (https://akansas.synology.me:9443/)

1. 게임 서버 Docker 환경(rakmu-server, samchun-server, rtsarena-web 이 보이는 곳)을 고른다.
2. Containers 의 Published Ports 와 host 네트워크 스택에서 **6112, 9000, 9010** 을 쓰는 것이 없는지 확인한다.
   (이 스택은 `network_mode: host` 라 VM 포트를 직접 쓴다 — 겹치면 로그에 `bind FAILED` 가 나온다.)
3. Stacks → **+ Add stack** → Name: `wanggun` → Build method: **Web editor** → `compose.yaml` 내용 전체 붙여 넣기.
   (환경변수는 필요 없다. `game_servers_data` 볼륨은 다른 스택이 쓰는 그 외부 볼륨이다.)
4. **Deploy the stack**.
5. Containers → `wanggun-server` 가 running, 1분 뒤 **healthy** 인지 확인. **unhealthy 면 포트가 겹친 것** — Logs 에서 `[PORT CONFLICT]` 줄을 보고
   스택을 Stop 한다 (다른 게임 서버는 그대로 동작한다). Logs 에 아래가 보이면 정상:
   ```
   태조왕건 더미서버 v2.1 시작. Enter 키로 종료.
   전적/랭킹 HTTP on port 9010 (/healthz /matches.json /ranking.json)
   UDP listening on port 9000
   TCP listening on port 9000
   TCP listening on port 6112
   ```
6. File Station 에서 `wanggun-live/data` 에 `accounts.json`, `logs/` 가 생겼는지 확인 (`matches.json` 은 첫 경기 후 생김).

## 4. 접속 확인

- (VM 안 / 같은 망) 브라우저 `http://<VM 내부 IP>:9010/healthz` → `{"ok": true, "version": "2.1", "ports": {"tcp/9000": "ok", "tcp/6112": "ok", "udp/9000": "ok"}, ...}`
- 게임 PC 런처: IP 칸에 **`112.159.73.235`** → **IP 적용** (hosts 의 wanggun.trigger.co.kr 등 3개 도메인이 NAS 로 간다).
  NAS 서버를 쓸 때는 런처의 **서버 시작** 버튼을 누르지 않는다 (그건 내 PC 에 서버를 켜고 hosts 를 127.0.0.1 로 바꾼다).
- 두 PC 로 방 만들기 → 입장 → 게임 1판 → 끝난 뒤 게임 안 **순위** 에 두 계정이 보이고, `http://<VM>:9010/matches.json` 에 경기 1개가 보이면 완료.
- 안 되면: 로그인에서 멈춤 = 6112/9000 전달 문제, 방에는 들어가는데 게임이 시작 안 됨 = 방장 PC 의 6112(P2P) 문제.

## 5. 전적 사이트(RTS ARENA)와 연결

rtsarena 저장소가 태조왕건을 지원한다 (드롭박스 "태조왕건"). NAS 에 반영하는 순서:

1. rtsarena 폴더에서 `.\deploy\Prepare-NasUpload.ps1` → File Station `rtsarena/app/arena` 덮어쓰기.
2. Portainer → Stacks → `rtsarena` → **Editor** 에 rtsarena 의 새 `deploy/compose.yaml` 붙여 넣기 → **Update the stack**.
   새 compose 에는 `WANGGUN_MATCHES_FILE=/mnt/game-servers/wanggun-live/data/matches.json` 이 들어 있다
   (같은 볼륨이라 파일을 바로 읽는다, 1분마다 동기화). 기존 `ARENA_UPLOAD_TOKEN` 환경변수는 그대로 둔다.
3. `http://akansas.synology.me:30013/healthz` 의 `sync.wanggun.error` 가 비어 있으면 연결됨.

## 6. 전적 규칙 (서버 2.0+)

- 게임이 끝나면 각 플레이어 게임이 0x24FF 로 자기 결과(1 승 / 2 패 / 3 무 / 그 외 = Dis)를 보낸다.
- 서버는 첫 결과가 올 때 그 방의 참가자 명단으로 경기 1개를 만들고, 나머지 참가자의 결과를 같은 경기에 붙인다 → `data/matches.json`.
- **사람 2명 이상인 경기만 게임 안 전적(순위)에 반영**한다 (혼자 방을 만들어 컴퓨터와 한 판은 제외 — 전적 사이트와 같은 기준).
  예전처럼 모두 반영하려면 스택 환경변수 `WANGGUN_COUNT_SOLO=1`.
- 점수 = 1000 + 승×10 − 패×10 (0 아래로 안 내려감). Dis 는 패로 센다. 전적 사이트 리더보드도 같은 규칙.

## 7. 운영

- **코드 업데이트**: `wanggun-live/app` 을 `backups/app-YYYYMMDD` 로 복사 → `Prepare-NasUpload.ps1` → `app/dummyserver.py` 덮어쓰기
  → Portainer Containers → wanggun-server → **Restart**. 접속자가 없을 때 한다 (재시작하면 방·접속이 끊긴다. 계정·전적은 유지).
- **백업**: 접속자 없을 때 `wanggun-live/data/accounts.json`, `matches.json` 을 `backups/data-YYYYMMDD/` 로 복사.
- **로그**: Portainer Logs 또는 `wanggun-live/data/logs/` (최근 30개 자동 보관. 접속 IP 가 있으니 공개하지 않는다).
- **보안**: 서버가 처음 켜질 때 시험용 계정 `user_a/1111`, `user_b/2222` 를 만든다. 공개 운영 전에 `accounts.json` 에서 지우거나 비밀번호를 바꾼다.
  게임 원래 프로토콜이라 암호화가 없다 — 다른 사이트와 같은 암호를 쓰지 말라고 안내한다.
