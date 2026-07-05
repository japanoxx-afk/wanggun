import socket
import threading
import struct
import datetime
import json
import os
import sys
import traceback
import weakref

# 서버 스크립트 버전 — 시작 배너와 런처에 표시된다.
# 어떤 버전이 실제로 돌고 있는지 확인하는 용도이므로 수정 시 반드시 올릴 것.
SERVER_VERSION = "1.8"

TCP_PORTS = [9000, 6112]
# UDP 6112는 게임의 IPX/P2P 연결 포트다. 서버가 이 포트를 바인딩하면
# 같은 머신에 호스트가 있을 때 상대의 게임 접속 패킷(0x1005 등)을 가로채
# 방 입장이 실패한다(진단용으로 넣었다가 발생한 회귀). 절대 바인딩하지 않는다.
UDP_PORTS = [9000]
# 방 정보 body가 1024를 넘는 경우 정상 패킷이 '깨진 헤더'로 오인되어
# 파서가 멈추는 사고를 막기 위해 여유 있게 잡는다 (size 필드는 u16).
MAX_PACKET_SIZE = 8192
ROOM_TTL_SECONDS = 10 * 60
DEFAULT_CHANNEL_NAME = b"\xf7\xbc\xf0\xd5\xe8\xdd\xcb\xef\x00"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", BASE_DIR),
    "TaejoWanggeonDummyServer",
)
ACCOUNTS_FILE = os.path.join(STATE_DIR, "accounts.json")
# 로그는 LOCALAPPDATA 아래에 남긴다. 런처 exe에 내장돼 실행되면 BASE_DIR이
# PyInstaller 임시 폴더가 되어 종료 시 로그가 함께 삭제되기 때문이다.
LOG_DIR = os.path.join(STATE_DIR, "logs")
LOG_KEEP_COUNT = 30  # 최근 30개만 보관, 나머지는 자동 삭제

accounts = {}
sessions = {}
rooms = []
clients = []
lock = threading.RLock()
log_lock = threading.RLock()

# 패킷 타입 이름 매핑 (로그 가독성용)
PACKET_NAMES = {
    0x8001: "AUTH_PING",   0x8002: "AUTH_VER",    0x8003: "AUTH_NAME",
    0x01FF: "VER_CHECK",   0x02FF: "GAME_VER",    0x03FF: "NEWS",
    0x04FF: "NEW_ACCT",    0x05FF: "LOGIN",        0x07FF: "ACCT_INFO",
    0x09FF: "CH_JOIN_1",   0x0AFF: "CH_JOIN_2",   0x0BFF: "ROOM_LIST",
    0x0CFF: "RL_START",    0x0DFF: "RL_ITEM",     0x0EFF: "ROOM_CREATE",
    0x10FF: "ROOM_JOIN",   0x11FF: "ROOM_EXIT",   0x12FF: "CHAT",
    0x1FFF: "USER_LIST",   0x24FF: "GAME_REPORT",
}

# 연결별 ID 카운터
_conn_counter = 0
_conn_counter_lock = threading.Lock()

# 연결별 send 락: 핸들러 스레드와 broadcast/알림 스레드가 같은 소켓에
# 동시에 sendall하면 패킷이 뒤섞여 클라이언트가 멈춘다. 소켓별로 직렬화.
_send_locks = weakref.WeakKeyDictionary()
_send_locks_guard = threading.Lock()

# 상대 클라이언트가 멈춰 소켓 버퍼가 가득 차도 서버 스레드가 영원히
# 붙잡히지 않도록 send 타임아웃(ms)을 건다.
SEND_TIMEOUT_MS = 5000


def get_send_lock(conn):
    with _send_locks_guard:
        lock_obj = _send_locks.get(conn)
        if lock_obj is None:
            lock_obj = threading.Lock()
            _send_locks[conn] = lock_obj
        return lock_obj


def safe_send(conn, data):
    """소켓별 락으로 직렬화된 sendall. 여러 스레드가 같은 conn에 보낼 때
    패킷 인터리빙을 방지한다."""
    with get_send_lock(conn):
        conn.sendall(data)


def set_send_timeout(conn, ms):
    try:
        # Windows: SO_SNDTIMEO = DWORD(밀리초)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, struct.pack("<L", ms))
    except OSError:
        pass


class TeeOutput:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        with log_lock:
            for stream in self.streams:
                try:
                    stream.write(text)
                    stream.flush()
                except OSError:
                    pass

    def flush(self):
        with log_lock:
            for stream in self.streams:
                try:
                    stream.flush()
                except OSError:
                    pass


def cleanup_old_logs():
    """LOG_DIR에서 오래된 로그를 지워 최근 LOG_KEEP_COUNT개만 남긴다."""
    try:
        logs = sorted(
            f for f in os.listdir(LOG_DIR)
            if f.startswith("dummyserver_") and f.endswith(".log")
        )
        for old in logs[:-LOG_KEEP_COUNT]:
            try:
                os.remove(os.path.join(LOG_DIR, old))
            except OSError:
                pass
    except OSError:
        pass


def setup_logging():
    log_name = (
        "dummyserver_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
    )

    # 1순위: LOCALAPPDATA\TaejoWanggeonDummyServer\logs (항상 유지되는 위치)
    # 2순위: 스크립트 폴더  3순위: 현재 폴더
    candidates = [
        os.path.join(LOG_DIR, log_name),
        os.path.join(BASE_DIR, log_name),
        os.path.abspath(log_name),
    ]

    log_file = None
    log_path = None
    for candidate in candidates:
        try:
            os.makedirs(os.path.dirname(candidate), exist_ok=True)
            log_file = open(candidate, "w", encoding="utf-8", buffering=1)
            log_path = candidate
            break
        except OSError:
            continue

    if log_file is None:
        print("[LOG FILE] 로그 파일을 열 수 없어 콘솔에만 출력합니다.")
        return

    sys.stdout = TeeOutput(sys.stdout, log_file)
    sys.stderr = TeeOutput(sys.stderr, log_file)
    print(f"[LOG FILE] {log_path}")

    cleanup_old_logs()


def now():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def alloc_conn_id():
    global _conn_counter
    with _conn_counter_lock:
        _conn_counter += 1
        return f"C{_conn_counter:03d}"


def plabel(packet_type):
    """패킷 타입을 읽기 쉬운 레이블로 반환: 0x05FF/LOGIN"""
    return f"0x{packet_type:04X}/{PACKET_NAMES.get(packet_type, '?')}"


def make_packet(packet_type, body=b""):
    return struct.pack("<HH", packet_type, 4 + len(body)) + body


def parse_packets(buffer, conn_id="C???"):
    packets = []
    offset = 0

    while len(buffer) - offset >= 4:
        packet_type, packet_size = struct.unpack_from("<HH", buffer, offset)

        if packet_size < 4 or packet_size > MAX_PACKET_SIZE:
            # 잘못된 헤더를 버퍼에 남겨두면 이후 도착하는 모든 패킷이
            # 그 뒤에 붙어 영원히 파싱되지 않는다(연결이 조용히 죽음).
            # 깨진 데이터는 로그로 남기고 버린 뒤 새로 시작한다.
            bad = buffer[offset:]
            print(
                f"[PARSE DESYNC] [{conn_id}] invalid header "
                f"type=0x{packet_type:04X} size={packet_size} — "
                f"{len(bad)}바이트 폐기: {bad[:64].hex(' ')}"
                + (" ..." if len(bad) > 64 else "")
            )
            return packets, b""

        if len(buffer) - offset < packet_size:
            break

        body = buffer[offset + 4:offset + packet_size]
        packets.append((packet_type, packet_size, body))
        offset += packet_size

    return packets, buffer[offset:]


def decode_text(data):
    return data.decode("cp949", errors="backslashreplace").strip("\x00")


def encode_text(text):
    return text.encode("cp949", errors="replace") + b"\x00"


def split_null_strings(body):
    return [p.decode("cp949", errors="backslashreplace") for p in body.split(b"\x00") if p]


def load_accounts():
    global accounts

    if not os.path.exists(ACCOUNTS_FILE):
        return

    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            accounts = data
            print(f"[ACCOUNTS LOADED] count={len(accounts)} file={ACCOUNTS_FILE}")
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ACCOUNTS LOAD FAILED] {e}")


def save_accounts():
    tmp_file = ACCOUNTS_FILE + ".tmp"

    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, ACCOUNTS_FILE)
    except OSError as e:
        print(f"[ACCOUNTS SAVE FAILED] {e}")


def ensure_default_accounts():
    changed = False

    with lock:
        for user_id, password in (("user_a", "1111"), ("user_b", "2222")):
            if user_id not in accounts:
                accounts[user_id] = {
                    "password": password,
                    "created_at": now(),
                    "addr": None,
                }
                changed = True

    if changed:
        save_accounts()
        print("[ACCOUNTS DEFAULTS CREATED] user_a/user_b")


# 전적 기본값. 점수는 1000점에서 시작한다.
RANK_BASE_SCORE = 1000
RANK_WIN_DELTA = 10
RANK_LOSS_DELTA = 10
RANK_MAX_ENTRIES = 20  # 클라이언트 순위 배열 크기(0x14)


def get_stats(user_id):
    """계정의 전적을 (점수, 승, 패, 무, dis)로 반환. 없으면 기본값."""
    acc = accounts.get(user_id, {})
    return (
        int(acc.get("score", RANK_BASE_SCORE)),
        int(acc.get("wins", 0)),
        int(acc.get("losses", 0)),
        int(acc.get("draws", 0)),
        int(acc.get("dis", 0)),
    )


def record_game_result(user_id, result_code):
    """게임 결과를 계정 전적에 반영한다. result_code: 1=승, 2=패, 3=무, 그외=dis.

    클라이언트가 게임 종료 시 0x24FF로 자신의 결과를 보고한다.
    """
    if user_id == "unknown":
        return

    with lock:
        acc = accounts.setdefault(user_id, {
            "password": "", "created_at": now(), "addr": None,
        })
        acc.setdefault("score", RANK_BASE_SCORE)
        for key in ("wins", "losses", "draws", "dis"):
            acc.setdefault(key, 0)

        if result_code == 1:      # 승
            acc["wins"] += 1
            acc["score"] += RANK_WIN_DELTA
            label = "승"
        elif result_code == 2:    # 패
            acc["losses"] += 1
            acc["score"] = max(0, acc["score"] - RANK_LOSS_DELTA)
            label = "패"
        elif result_code == 3:    # 무
            acc["draws"] += 1
            label = "무"
        else:                     # 접속 종료(dis) = 패 처리
            acc["dis"] += 1
            acc["losses"] += 1
            acc["score"] = max(0, acc["score"] - RANK_LOSS_DELTA)
            label = "Dis"

        score = acc["score"]
        w, l, d = acc["wins"], acc["losses"], acc["draws"]

    save_accounts()
    print(f"[GAME RESULT] {user_id}: {label}  점수={score} {w}승 {l}패 {d}무")


def make_rank_entry(user_id, score, wins, losses, draws, dis):
    """순위 엔트리(클라이언트 파서 형식).

    KAURI.dll 순위 파서(0x10051547) 역분석:
      [점수:4][승:4][패:4][무:4][Dis:4][등급:2] + 이름\\0 + 문자열2\\0
    """
    struct22 = struct.pack("<IIIIIH", score, wins, losses, draws, dis, 0)
    return struct22 + encode_text(user_id) + encode_text("")


def make_rank_list_packets(sort_byte):
    """순위 목록 응답: 0x16FF(시작) + 0x15FF(정렬+엔트리) + 0x17FF(끝).

    전적이 있는 계정을 점수 내림차순으로 최대 20개 내려준다.
    엔트리가 없으면 0x15FF는 sort 바이트만(size=5) 보내 안전하게 빈 목록 처리.
    """
    with lock:
        ranked = [
            (uid, *get_stats(uid))
            for uid in accounts
        ]
    # 게임을 한 번이라도 한 계정만, 점수 내림차순.
    ranked = [r for r in ranked if (r[2] + r[3] + r[4] + r[5]) > 0]
    ranked.sort(key=lambda r: r[1], reverse=True)
    ranked = ranked[:RANK_MAX_ENTRIES]

    entries = b"".join(
        make_rank_entry(uid, score, w, l, d, dis)
        for uid, score, w, l, d, dis in ranked
    )
    body_15 = sort_byte + entries

    print(f"[RANK LIST] 엔트리 {len(ranked)}개 전송")
    for uid, score, w, l, d, dis in ranked:
        print(f"  {uid}: 점수={score} {w}승 {l}패 {d}무 dis={dis}")

    return [
        make_packet(0x16FF, b""),
        make_packet(0x15FF, body_15),
        make_packet(0x17FF, b""),
    ]


def get_client(conn):
    with lock:
        for client in clients:
            if client["conn"] is conn:
                return client
    return None


def set_client_user(conn, user_id):
    client = get_client(conn)
    if client:
        client["user_id"] = user_id


def get_client_user(conn):
    client = get_client(conn)
    if client and client.get("user_id"):
        return client["user_id"]
    return "unknown"


def get_conn_id(conn):
    client = get_client(conn)
    return client.get("conn_id", "C???") if client else "C???"


def close_socket_quietly(conn):
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass

    try:
        conn.close()
    except OSError:
        pass


def drop_conn_state(conn):
    dropped_users = []

    with lock:
        for client in list(clients):
            if client["conn"] is conn:
                if client.get("user_id"):
                    dropped_users.append(client["user_id"])
                clients.remove(client)

        for user_id, session in list(sessions.items()):
            if session.get("conn") is conn:
                dropped_users.append(user_id)
                sessions.pop(user_id, None)

    return sorted(set(dropped_users))


def get_active_users():
    with lock:
        return [
            {
                "user_id": user_id,
                "addr": session.get("addr"),
                "conn": session.get("conn"),
                "state": session.get("state", "lobby"),
                "room": session.get("room"),
            }
            for user_id, session in sessions.items()
        ]


def get_lobby_users(include_user=None):
    with lock:
        return [
            {
                "user_id": user_id,
                "addr": session.get("addr"),
                "conn": session.get("conn"),
                "state": session.get("state", "lobby"),
                "room": session.get("room"),
            }
            for user_id, session in sessions.items()
            if session.get("state", "lobby") == "lobby" or user_id == include_user
        ]


def set_user_state(user_id, state, room_name=None):
    if user_id == "unknown":
        return

    with lock:
        session = sessions.get(user_id)
        if session:
            old_state = session.get("state", "?")
            session["state"] = state
            session["room"] = room_name
            print(f"  [STATE CHG] {user_id}: {old_state} → {state}"
                  + (f" room={room_name}" if room_name else ""))


def conn_label(conn):
    try:
        return f"{conn.getpeername()}->{conn.getsockname()}"
    except OSError:
        return hex(id(conn))


def print_state(label):
    with lock:
        sess_info = {
            uid: f"{s.get('state','?')}|{s.get('room') or '-'}"
            for uid, s in sessions.items()
        }
        room_info = [
            f"{get_room_name(r['body'])}@{r['creator']}[{','.join(r['players'])}]"
            for r in rooms
        ]
        cli_info = [
            f"{c.get('conn_id','?')}:{c.get('user_id','?')}"
            for c in clients
        ]
    print(f"[STATE:{label}] sess={sess_info}")
    print(f"[STATE:{label}] rooms={room_info}  clients={cli_info}")


def print_packet(conn, port, addr, packet_type, packet_size, body):
    conn_id = get_conn_id(conn)
    user = get_client_user(conn)
    tid = threading.current_thread().ident % 10000
    print(f"\n┌─ RECV [{now()}] [{conn_id}|{user}] port={port} tid={tid}")
    print(f"│  {plabel(packet_type)}  size={packet_size}")
    if body:
        bhex = body.hex(" ") if len(body) <= 32 else body[:32].hex(" ") + " ..."
        print(f"│  HEX : {bhex}")
        print(f"│  TEXT: {decode_text(body)}")
    print(f"└{'─'*55}")


def make_lobby_chat_response(sender, message):
    body = b"\x01" + b"\x00\x00" + encode_text(sender) + encode_text(message)
    return make_packet(0x12FF, body)


def broadcast_chat(packet):
    # 전역 lock을 잡은 채 sendall하면 한 클라이언트의 소켓이 막혔을 때
    # 서버 전체가 멈춘다. 대상을 스냅샷하고 lock 밖에서 보낸다.
    with lock:
        targets = [
            (c["conn"], c.get("conn_id", "C???"), c.get("user_id", "?"))
            for c in clients
        ]

    for conn, cid, uid in targets:
        try:
            safe_send(conn, packet)
        except OSError as e:
            print(f"[CHAT SEND FAIL] [{cid}|{uid}]: {e}")


def push_user_joined_to_lobby(new_user, except_conn):
    """새 유저가 로그인하면 기존 로비 클라이언트에게 그 유저를 증분 추가한다.

    클라이언트의 유저 목록(0x1FFF)은 append 방식이다(전체 목록을 다시 보내면
    쌓인다). 그래서 신규 유저 1명만 담은 showuser 패킷을 기존 클라이언트에게
    보내 목록에 추가되게 한다. 신규 유저 자신은 자기 0x07FF 응답에서 전체
    목록을 받으므로 제외한다.
    """
    with lock:
        targets = [
            (c["conn"], c.get("conn_id", "C???"), c.get("user_id", "?"))
            for c in clients
            if (
                c.get("user_id") not in (None, "unknown", "", new_user)
                and c["conn"] is not except_conn
                and sessions.get(c.get("user_id", ""), {}).get("state", "lobby") == "lobby"
            )
        ]

    if not targets:
        return

    pkt = b"".join(make_channel_user_list_packets([{"user_id": new_user}]))
    for conn, cid, uid in targets:
        try:
            safe_send(conn, pkt)
            print(f"[USER JOIN PUSH] → [{cid}|{uid}]: +{new_user}")
        except OSError as e:
            print(f"[USER JOIN PUSH FAIL] [{cid}|{uid}]: {e}")


def broadcast_room_list_to_lobby(except_conn=None):
    """방 목록을 로비 클라이언트에게 push하려던 함수 — 현재 비활성화.

    방 엔트리는 0x0BFF로 보내야 하는데(GS 디스패처 역분석), 요청받지 않은
    0x0BFF를 로비 클라이언트에 push하면 채널 조인 상태로 오인해 멈출 위험이
    있다. 따라서 방 목록은 클라이언트가 0x0BFF로 직접 요청(참전 버튼)할 때만
    내려준다. 실시간 방 목록 갱신이 필요하면 안전한 방식을 별도로 설계한다.
    """
    return


def _broadcast_room_list_to_lobby_disabled(except_conn=None):
    remove_stale_rooms()
    with lock:
        room_snapshot = [dict(room) for room in rooms]
        targets = [
            (client["conn"], client["addr"], client.get("user_id", "unknown"),
             client.get("conn_id", "C???"))
            for client in list(clients)
            if (
                client.get("user_id") not in (None, "unknown")
                and (except_conn is None or client["conn"] is not except_conn)
                and sessions.get(
                    client.get("user_id", ""), {}
                ).get("state", "lobby") == "lobby"
            )
        ]

    if not targets:
        print("[ROOM LIST PUSH] 대상 없음 (로비 유저 없음)")
        return

    pkts = make_room_list_packets(room_snapshot)
    combined = b"".join(pkts)
    ptype_labels = [plabel(struct.unpack_from("<H", p, 0)[0]) for p in pkts if len(p) >= 2]

    for conn, addr, user_id, cid in targets:
        try:
            safe_send(conn, combined)
            print(f"[ROOM LIST PUSH] → [{cid}|{user_id}] pkts={ptype_labels}")
        except OSError as e:
            print(f"[ROOM LIST PUSH FAIL] [{cid}|{user_id}] {addr}: {e}")


def make_empty_room_list_packets():
    return [
        make_packet(0x0CFF, b""),
        make_packet(0x0DFF, b"")
    ]


def make_lobby_rejoin_packets():
    return [
        make_packet(0x09FF, b""),
        make_packet(0x0AFF, b""),
        make_packet(0x0BFF, b"\x00" + DEFAULT_CHANNEL_NAME),
    ]


def make_channel_user_record(user):
    user_id = user["user_id"]
    user_info = struct.pack("<IIIIH", 0, 0, 0, 0, 0)
    return user_info + encode_text(user_id)


def make_channel_user_list_packets(users):
    body = b"\x00" + b"".join(make_channel_user_record(user) for user in users)

    return [
        make_packet(0x1FFF, body),
        make_packet(0x1FFF, b"\x01"),
    ]


def split_first_null(data):
    pos = data.find(b"\x00")
    if pos < 0:
        return data, b""
    return data[:pos + 1], data[pos + 1:]


def get_room_name_bytes(room_body):
    if len(room_body) < 9:
        return b""
    room_name, _ = split_first_null(room_body[8:])
    return room_name


def get_room_name(room_body):
    return decode_text(get_room_name_bytes(room_body))


def get_room_owner_from_body(room_body):
    parts = split_null_strings(room_body)
    return parts[-1] if parts else ""


def make_room_list_record(room):
    """방 목록 레코드를 클라이언트의 방 목록 파서 형식으로 만든다.

    KAURI.dll의 방 목록 파서(ClientRoomPacket.cpp, VA 0x10050f77)를 역분석해
    확인한 엔트리 구조:
        [블록 B: 10바이트][방이름\\0][detail: B[8:10] 바이트]
      · B[4:6] = 현재 인원,  B[6:8] = 최대 인원,  B[8:10] = detail 길이
      · detail = [지하맵:1][타일셋:1][..][맵너비:2][맵높이:2][맵이름\\0][만든이\\0]

    클라이언트가 방 생성(0x0EFF) 시 보내는 원본 body는 헤더가 8바이트이고
    방이름 뒤에 1바이트 구분자(0x00)가 붙는다:
        [상태:2][현재:2][최대:2][detail길이:2][방이름\\0][0x00][detail]
    이를 리스트 엔트리로 변환하려면:
      · 10바이트 블록 B로 재구성(B[4:6]=현재, B[6:8]=최대, B[8:10]=detail길이)
      · 방이름은 그대로, detail은 선행 구분자(0x00)를 제거해 이어붙인다.
    이렇게 하면 파서가 방/인원/맵이름/만든이/맵크기를 정확히 읽고
    다음 엔트리 오프셋도 정확히 맞아떨어진다(역분석 시뮬레이션 검증 완료).
    """
    room_body = room["body"]
    if len(room_body) < 9:
        print("[ROOM LIST SKIP] room body too short")
        return b""

    room_name, room_detail = split_first_null(room_body[8:])
    if not room_name or not room_detail:
        print("[ROOM LIST SKIP] missing room name/detail")
        return b""

    # 방이름 뒤 선행 구분자(0x00)를 제거해 detail 길이를 맞춘다.
    expected_detail_len = struct.unpack_from("<H", room_body, 6)[0]
    if (
        expected_detail_len > 0
        and len(room_detail) == expected_detail_len + 1
        and room_detail[:1] == b"\x00"
    ):
        room_detail = room_detail[1:]

    max_players = max(2, struct.unpack_from("<H", room_body, 4)[0])
    cur_players = max(1, len(room.get("players", [])))

    # 10바이트 블록 B: [flags:4][현재:2][최대:2][detail길이:2]
    return (
        b"\x00\x00\x00\x00"
        + struct.pack("<HHH", cur_players, max_players, len(room_detail))
        + room_name
        + room_detail
    )


def make_room_list_packets(room_snapshot):
    # KAURI.dll GS 디스패처(SN_HandleGSPacket) 역분석 결과 방 목록 프로토콜:
    #   0x0CFF = 목록 시작 (클라이언트 리스트 클리어)
    #   0x0BFF = 방 엔트리   (엔트리 파서 0x10050f77이 읽어 리스트에 추가)
    #   0x0DFF = 목록 끝     (화면 갱신/refresh)
    # 이전에는 방 데이터를 0x0DFF에 넣었는데, 0x0DFF는 '끝(갱신)' 핸들러라
    # 데이터를 무시하고 빈 리스트를 그렸다(방이 안 보이는 근본 원인).
    # 방 데이터는 반드시 0x0BFF에 담아야 한다.
    list_body = b""
    for room in room_snapshot:
        list_body += make_room_list_record(room)

    packets = [make_packet(0x0CFF, b"")]
    if list_body:
        packets.append(make_packet(0x0BFF, list_body))
    packets.append(make_packet(0x0DFF, b""))
    return packets


def remove_stale_rooms():
    now_dt = datetime.datetime.now()

    with lock:
        before = len(rooms)
        rooms[:] = [
            room for room in rooms
            if (now_dt - room["created_at"]).total_seconds() < ROOM_TTL_SECONDS
        ]
        removed = before - len(rooms)

    if removed:
        print(f"[ROOM CLEANUP] stale rooms removed={removed}")


def remove_user_from_rooms(user_id):
    with lock:
        for room in rooms:
            if user_id in room["players"]:
                room["players"].remove(user_id)

        before = len(rooms)
        rooms[:] = [
            room for room in rooms
            if room["creator"] != user_id and room.get("session_user") != user_id
        ]
        removed = before - len(rooms)

    if removed:
        print(f"[ROOM REMOVED] creator={user_id}, rooms={removed}")


def add_or_replace_room(room):
    room_name = get_room_name(room["body"])

    with lock:
        before = len(rooms)
        rooms[:] = [
            existing for existing in rooms
            if (
                existing["creator"] != room["creator"]
                and existing["body"] != room["body"]
                and get_room_name(existing["body"]) != room_name
            )
        ]
        removed = before - len(rooms)
        rooms.append(room)

    if removed:
        print(
            "[ROOM DEDUP] "
            f"creator={room['creator']}, room={room_name}, removed={removed}"
        )


def find_room_host_for_player(user_id):
    """user_id가 참가자(creator가 아닌)로 있는 방의 호스트 conn/addr을 반환."""
    with lock:
        for room in rooms:
            if user_id in room.get("players", []) and room["creator"] != user_id:
                host_session = sessions.get(room["creator"])
                if host_session and host_session.get("conn"):
                    return (
                        host_session["conn"],
                        host_session.get("addr"),
                        room["creator"],
                    )
    return None


def notify_room_host_player_left(leaving_user, host_info):
    """참가자가 방을 떠났을 때 호스트에게 업데이트된 방 목록을 push한다.

    host_info는 호출자가 방 제거 *전에* find_room_host_for_player()로
    미리 캡처해서 넘긴다. 이 함수 안에서 찾으면 별도 스레드 실행 시점에
    방이 이미 제거돼 호스트를 못 찾는 레이스가 생긴다.
    """
    if not host_info:
        print(f"[HOST NOTIFY] {leaving_user} 의 호스트 없음 (방에 없거나 이미 나감)")
        return

    # 방 목록(0x0BFF 엔트리)을 요청받지 않은 상태의 호스트에게 push하면
    # 채널 조인 상태로 오인해 멈출 위험이 있어 비활성화한다. 호스트는
    # 참전 목록을 다시 열면(0x0BFF 요청) 최신 상태를 받는다.
    print(f"[HOST NOTIFY] {leaving_user} 퇴장 — 방 목록 push 생략(안전)")
    return

    host_conn, host_addr, host_user = host_info
    host_cid = get_conn_id(host_conn)

    remove_stale_rooms()
    with lock:
        room_snapshot = [dict(room) for room in rooms]

    pkts = make_room_list_packets(room_snapshot)
    combined = b"".join(pkts)
    ptype_labels = [plabel(struct.unpack_from("<H", p, 0)[0]) for p in pkts if len(p) >= 2]
    try:
        safe_send(host_conn, combined)
        print(
            f"[HOST NOTIFY] → [{host_cid}|{host_user}]: "
            f"{leaving_user} 퇴장  pkts={ptype_labels}"
        )
    except OSError as e:
        print(f"[HOST NOTIFY FAIL] [{host_cid}|{host_user}] {host_addr}: {e}")


def leave_room_state(user_id):
    with lock:
        for room in rooms:
            if user_id in room["players"]:
                room["players"].remove(user_id)

        before = len(rooms)
        rooms[:] = [
            room for room in rooms
            if (
                room["creator"] != user_id
                and room.get("session_user") != user_id
                and room["players"]
            )
        ]
        removed_rooms = before - len(rooms)

    if removed_rooms:
        print(f"[ROOM STATE CLEARED] user={user_id}, rooms={removed_rooms}")


def is_loopback_ip(ip):
    return ip.startswith("127.") or ip in ("::1", "localhost")


def get_join_host_ip(conn, room):
    host_ip = room["addr"][0]

    if is_loopback_ip(host_ip):
        try:
            local_ip = conn.getsockname()[0]
            if local_ip and not is_loopback_ip(local_ip) and local_ip != "0.0.0.0":
                return local_ip
        except OSError:
            pass

    return host_ip


def find_room_by_request(body):
    requested = split_null_strings(body)
    requested_name = requested[0] if requested else ""

    with lock:
        for room in rooms:
            room_name = get_room_name(room["body"])
            if requested_name in (room_name, room["creator"]):
                return dict(room)

        if requested_name:
            for room in rooms:
                room_name = get_room_name(room["body"])
                if requested_name in room_name or requested_name in room["creator"]:
                    return dict(room)

    return None


def room_body_contains_user(body, user_id):
    return user_id in split_null_strings(body)


def get_responses(conn, addr, packet_type, body):
    conn_id = get_conn_id(conn)
    user = get_client_user(conn)

    # 9000 초기 인증
    if packet_type == 0x8001:
        return [make_packet(0x8001, b"\x00\x00")]

    if packet_type == 0x8002:
        return [make_packet(0x8002, b"\x00\x00")]

    if packet_type == 0x8003:
        parts = split_null_strings(body)
        if parts:
            set_client_user(conn, parts[0])
        return [make_packet(0x8003, b"\x00\x00")]

    # 6112 초기 체크
    if packet_type == 0x01FF:
        return [make_packet(0x01FF, b"\x00\x00")]

    if packet_type == 0x02FF:
        parts = split_null_strings(body[1:] if len(body) > 1 else body)
        if parts:
            set_client_user(conn, parts[0])
        return [make_packet(0x02FF, b"\x00\x00")]

    # 공지 요청
    if packet_type == 0x03FF:
        return [make_packet(0x03FF, b"\x00\x00")]

    # 새 계정 만들기
    if packet_type == 0x04FF:
        parts = split_null_strings(body)

        if len(parts) >= 2:
            user_id = parts[0]
            password = parts[1]

            with lock:
                if user_id in accounts:
                    if accounts[user_id].get("password") == password:
                        print(f"[ACCOUNT CREATE OK] existing id={user_id}")
                        set_client_user(conn, user_id)
                        return [make_packet(0x04FF, b"\x00\x00")]

                    print(f"[ACCOUNT CREATE FAILED] duplicate id={user_id}")
                    return [make_packet(0x04FF, b"\x01\x00")]

                accounts[user_id] = {
                    "password": password,
                    "created_at": now(),
                    "addr": addr,
                }
                save_accounts()

            set_client_user(conn, user_id)
            print(f"[ACCOUNT CREATED] id={user_id}")

            return [make_packet(0x04FF, b"\x00\x00")]

        print("[ACCOUNT CREATE FAILED] invalid body")
        return [make_packet(0x04FF, b"\x02\x00")]

    # 로그인
    if packet_type == 0x05FF:
        parts = split_null_strings(body)

        if len(parts) >= 2:
            user_id = parts[0]
            password = parts[1]

            # IPX 릴레이 방지: 0x02FF에서 등록된 conn 유저와 로그인 user_id가
            # 다르면 다른 PC가 릴레이한 로그인이다. ACK만 보내고 무시.
            conn_user = get_client_user(conn)
            if conn_user not in ("unknown", user_id):
                print(
                    f"[LOGIN RELAY IGNORED] [{conn_id}] "
                    f"conn_user={conn_user}, login_user={user_id}"
                )
                return [make_packet(0x05FF, b"\x00\x00")]

            old_conn = None

            with lock:
                if user_id not in accounts:
                    accounts[user_id] = {
                        "password": password,
                        "created_at": now(),
                        "addr": addr,
                    }
                    save_accounts()
                    print(f"[AUTO ACCOUNT] created id={user_id}")

                if user_id in sessions:
                    print(f"[LOGIN REPLACED] duplicate login id={user_id} [{conn_id}]")
                    old_conn = sessions[user_id].get("conn")

                sessions[user_id] = {
                    "addr": addr,
                    "login_at": now(),
                    "conn": conn,
                    "state": "lobby",
                    "room": None,
                }

            if old_conn is not None and old_conn is not conn:
                dropped = drop_conn_state(old_conn)
                close_socket_quietly(old_conn)
                print(f"[LOGIN REPLACED] closed old conn id={user_id}, dropped={dropped}")

            set_client_user(conn, user_id)
            print(f"[LOGIN OK] [{conn_id}] id={user_id} addr={addr}")
            print_state("LOGIN")

            # 이미 로비에 있는 다른 클라이언트에게 이 신규 유저를 실시간 추가한다.
            threading.Thread(
                target=push_user_joined_to_lobby,
                args=(user_id, conn),
                daemon=True,
            ).start()

            # 로그인 응답에는 채널 조인(0x09/0x0A/0x0B)만 보낸다.
            # 유저/방 목록을 여기서 같이 묶어 보내면 클라이언트가 채널 조인과
            # 한 덩어리로 받아 유저 목록을 화면에 반영하지 못한다(로그인 시
            # 참전장수 목록이 비어 보이는 원인). 목록은 클라이언트가 채널 조인
            # 후 보내는 0x07FF 응답에서 따로 내려준다 — 이는 방 나간 후 흐름과
            # 동일하며, 그 흐름에선 목록이 정상 표시된다.
            return (
                [make_packet(0x05FF, b"\x00\x00")]
                + make_lobby_rejoin_packets()
            )

        print(f"[LOGIN FAILED] [{conn_id}] invalid body")
        return [make_packet(0x05FF, b"\x04\x00")]

    # 계정 / 닉네임 정보
    # 클라이언트는 채널 조인(0x09/0x0A/0x0B) 수신 후 0x07FF를 보낸다.
    #   · 로그인 직후: body = 게임이름(太祖王建, 9+ bytes)
    #   · 방 나가기 후: body = \x00 (1 byte)
    # 두 경우 모두 방금 채널에 (재)진입한 상태이므로 유저/방 목록을 내려준다.
    # 유저 목록(0x1FFF)은 이 시점에만 보내므로 append로 쌓이지 않는다
    # (0x0BFF 방목록 버튼 응답에는 유저 목록을 넣지 않음).
    if packet_type == 0x07FF:
        branch = "post-room-exit" if len(body) <= 1 else "login-after"
        print(f"[ACCT_INFO] [{conn_id}|{user}] body_len={len(body)} → {branch}: 유저 목록 전송")

        # 유저 목록만 보낸다. 방 엔트리(0x0BFF)는 요청받지 않은 상태에서 보내면
        # 로비 클라이언트가 채널 조인 상태로 오인해 멈출 수 있으므로,
        # 방 목록은 클라이언트가 0x0BFF로 직접 요청할 때만 내려준다.
        # 여기서는 빈 방 목록(클리어+갱신)만 보내 로비 상태를 정돈한다.
        active_users_07 = get_active_users()
        return (
            [make_packet(0x07FF, b"\x00\x00")]
            + make_channel_user_list_packets(active_users_07)
            + make_empty_room_list_packets()
        )

    # 방 목록 요청 (클라이언트→서버: 0x0BFF)
    # 주의: 서버→클라이언트의 0x0BFF는 채널 재조인 신호 — 의미가 다르다.
    if packet_type == 0x0BFF:
        remove_stale_rooms()

        with lock:
            room_snapshot = [dict(room) for room in rooms]

        print(f"[ROOM LIST REQ] [{conn_id}|{user}] rooms={len(room_snapshot)}")
        for room in room_snapshot:
            print(
                f"  room: {get_room_name(room['body'])!r}"
                f"  creator={room['creator']}"
                f"  players={room['players']}"
                f"  at={room['created_at'].strftime('%H:%M:%S')}"
            )
            # native 형식으로 되돌려주는 방 레코드의 실제 바이트를 남긴다.
            rec = make_room_list_record(room)
            print(f"  record({len(rec)}B): {rec.hex(' ')}")

        # 유저 목록(0x1FFF)은 보내지 않는다 — APPEND돼서 무한 증가.
        return make_room_list_packets(room_snapshot)

    if packet_type == 0x1FFF:
        users = get_active_users()
        print(
            f"[USER LIST REQ] [{conn_id}|{user}] "
            f"users={[u['user_id'] for u in users]}"
        )
        return make_channel_user_list_packets(users)

    # 방 생성 요청
    if packet_type == 0x0EFF:
        session_user = get_client_user(conn)
        creator = session_user
        body_text = decode_text(body)
        body_owner = get_room_owner_from_body(body)
        remove_stale_rooms()

        if body_owner:
            creator = body_owner

        if session_user == "unknown" and creator != "unknown":
            set_client_user(conn, creator)

        if creator == "unknown":
            print(f"[ROOM CREATE REJECTED] [{conn_id}] unknown session")
            return [make_packet(0x0EFF, b"\x01\x00")]

        # IPX 브로드캐스트로 다른 로비 클라이언트도 같은 방 정보를 서버로 보낸다.
        # 거부하면 방 주인의 방 등록이 막히므로, 방 주인 명의로 등록한다.
        reported_by_other = (
            session_user != "unknown" and body_owner and session_user != body_owner
        )

        host_addr = addr
        with lock:
            owner_session = sessions.get(creator)
            if owner_session and owner_session.get("addr"):
                host_addr = owner_session["addr"]

        add_or_replace_room({
            "creator": creator,
            "session_user": creator,
            "addr": host_addr,
            "body": body,
            "created_at": datetime.datetime.now(),
            "players": [creator],
        })
        set_user_state(creator, "room", get_room_name(body))

        threading.Thread(
            target=broadcast_room_list_to_lobby,
            args=(conn,),
            daemon=True,
        ).start()

        if reported_by_other:
            print(
                f"[ROOM CREATE SYNCED] [{conn_id}] "
                f"reporter={session_user}, owner={creator}"
            )
        print(
            f"[ROOM CREATE OK] [{conn_id}] creator={creator}"
            f"  room={get_room_name(body)!r}"
        )
        print_state("ROOM_CREATE")

        return [make_packet(0x0EFF, b"\x00\x00")]

    if packet_type == 0x10FF:
        requested = split_null_strings(body)
        requested_name = requested[0] if requested else ""
        room = find_room_by_request(body)

        if not room:
            print(
                f"[ROOM JOIN FAILED] [{conn_id}|{user}] "
                f"requested={requested_name!r} (방 없음)"
            )
            return [make_packet(0x10FF, b"\x01")]

        room_name_bytes = get_room_name_bytes(room["body"])
        host_ip = get_join_host_ip(conn, room)

        with lock:
            for stored_room in rooms:
                if stored_room["creator"] == room["creator"]:
                    if user != "unknown" and user not in stored_room["players"]:
                        stored_room["players"].append(user)
                    if user != "unknown":
                        set_user_state(user, "room", get_room_name(stored_room["body"]))
                    break

        print(
            f"[ROOM JOIN OK] [{conn_id}|{user}]"
            f"  room={decode_text(room_name_bytes)!r}"
            f"  host_ip={host_ip}"
        )
        print_state("ROOM_JOIN")

        join_body = b"\x00" + room_name_bytes + encode_text(host_ip)
        return [make_packet(0x10FF, join_body)]

    # 방 취소 / 채널 재조인
    if packet_type == 0x11FF:
        print(f"[ROOM EXIT] [{conn_id}|{user}]")
        if user != "unknown":
            # 호스트 정보는 방 제거 전에 동기적으로 캡처하고, 전송만 비동기로.
            host_info = find_room_host_for_player(user)
            threading.Thread(
                target=notify_room_host_player_left,
                args=(user, host_info),
                daemon=True,
            ).start()
            remove_user_from_rooms(user)
            set_user_state(user, "lobby", None)
            print_state("ROOM_EXIT")

        # 방 제거 후 로비 유저들에게 방 목록 push (비동기).
        threading.Thread(
            target=broadcast_room_list_to_lobby,
            args=(conn,),
            daemon=True,
        ).start()

        # ACK + 채널 재조인만. 유저/방 목록은 클라이언트가 0x0BFF 요청 후 받는다.
        return (
            [make_packet(0x11FF, b"\x00\x00")]
            + make_lobby_rejoin_packets()
        )

    # 채팅
    if packet_type == 0x12FF:
        message = decode_text(body)
        sender = get_client_user(conn)

        if message == "/status":
            print_state("CHAT_STATUS")
            return [make_packet(0x12FF, b"\x00\x00")]

        if sender == "unknown":
            print(f"[CHAT IGNORED] [{conn_id}] unknown sender msg={message!r}")
            return [make_packet(0x12FF, b"\x01\x00")]

        print(f"[CHAT] [{conn_id}|{sender}]: {message}")

        chat_packet = make_lobby_chat_response(sender, message)
        broadcast_chat(chat_packet)

        return []

    if packet_type == 0x24FF:
        # 게임 결과 보고. 0x24FF body = [결과:1][word:2][dword:4][이름...]
        # 결과코드: 1=승, 2=패, 3=무, 그외=dis. 보고자(conn 유저)의 전적을 갱신.
        result_code = body[0] if len(body) >= 1 else 0
        print(f"[GAME REPORT] [{conn_id}|{user}] result={result_code}")
        if user != "unknown":
            record_game_result(user, result_code)
            leave_room_state(user)
            set_user_state(user, "lobby", None)
            print_state("GAME_REPORT")

        threading.Thread(
            target=broadcast_room_list_to_lobby,
            args=(conn,),
            daemon=True,
        ).start()
        return (
            [make_packet(0x24FF, b"\x00\x00")]
            + make_lobby_rejoin_packets()
        )

    # 전적/순위(랭킹) 목록 요청. 클라이언트는 0x15FF로 랭킹을 요청한다.
    # KAURI.dll 랭킹 파서(0x10051547) 역분석:
    #   packet[4]=정렬타입, idx=5부터 idx < 패킷size 인 동안 엔트리를 읽는다.
    #   엔트리 = [점수:4][승:4][패:4][무:4][Dis:4][등급:2] + 이름\0 + 문자열2\0
    # 응답 시퀀스: 0x16FF(시작) + 0x15FF(정렬+엔트리) + 0x17FF(끝).
    # 엔트리가 없으면 0x15FF는 정렬 바이트만(size=5) 담겨 "CNT 0"로 안전 처리된다.
    if packet_type == 0x15FF:
        sort_byte = body[0:1] if body else b"\x00"
        print(f"[RANK LIST REQ] [{conn_id}|{user}] sort={sort_byte.hex()}")
        return make_rank_list_packets(sort_byte)

    print(f"[UNHANDLED] [{conn_id}|{user}] {plabel(packet_type)}")
    return [make_packet(packet_type, b"\x00\x00")]


def send_responses(conn, addr, responses):
    """핸들러 응답 여러 개를 하나의 버퍼로 묶어 send 락 아래에서 한 번에 보낸다.

    패킷 단위로 따로 보내면 로그인 응답(0x05FF + 채널조인 + 목록)처럼
    여러 패킷으로 된 시퀀스 중간에 broadcast 스레드의 push가 끼어들어
    클라이언트가 시퀀스를 잘못 해석할 수 있다.
    """
    if not responses:
        return

    conn_id = get_conn_id(conn)
    user = get_client_user(conn)

    for response in responses:
        if len(response) >= 4:
            ptype = struct.unpack_from("<H", response, 0)[0]
            psize = struct.unpack_from("<H", response, 2)[0]
            pbody = response[4:4 + max(0, psize - 4)]
            bsummary = pbody.hex(" ") if len(pbody) <= 20 else pbody[:20].hex(" ") + "..."
            print(f"  → [{conn_id}|{user}] {plabel(ptype)} size={psize} body=[{bsummary}]")
        else:
            print(f"  → [{conn_id}|{user}] raw={response.hex(' ')}")

    try:
        safe_send(conn, b"".join(responses))
    except OSError as e:
        print(f"  ! [{conn_id}|{user}] SEND ERROR: {e}")


def cleanup_disconnect(conn, addr, port):
    disconnected_user = get_client_user(conn)

    if port != 6112:
        return

    # 방에 참가자로 있었으면 호스트에게 알림.
    # 호스트 정보는 방/세션 제거 전에 동기적으로 캡처하고, 전송만 비동기로.
    if disconnected_user != "unknown":
        host_info = find_room_host_for_player(disconnected_user)
        threading.Thread(
            target=notify_room_host_player_left,
            args=(disconnected_user, host_info),
            daemon=True,
        ).start()
        leave_room_state(disconnected_user)

    with lock:
        for client in list(clients):
            if client["conn"] is conn:
                clients.remove(client)

        # 이 conn이 현재 세션의 주인일 때만 세션을 지운다.
        if disconnected_user != "unknown":
            session = sessions.get(disconnected_user)
            if session and session.get("conn") is conn:
                sessions.pop(disconnected_user, None)

    print(f"[DISCONNECT] user={disconnected_user}")
    print_state("DISCONNECT")


def tcp_server(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(20)

    print(f"TCP listening on port {port}")

    while True:
        conn, addr = server.accept()
        conn_id = alloc_conn_id()

        # 멈춘 클라이언트의 소켓 버퍼가 가득 차도 send가 영원히 막히지 않게.
        set_send_timeout(conn, SEND_TIMEOUT_MS)

        print(f"\n{'═'*60}")
        print(f"[{now()}] TCP CONNECT {conn_id} {addr} port={port}")

        if port == 6112:
            with lock:
                clients.append({
                    "conn": conn,
                    "addr": addr,
                    "user_id": None,
                    "conn_id": conn_id,
                })

        # BUG FIX: 기본 인자로 값을 고정해 클로저 변수 공유 문제를 방지한다.
        # def handle_client(): 로 쓰면 conn/addr이 루프 변수를 공유해
        # 두 번째 클라이언트 접속 시 첫 번째 스레드가 두 번째 소켓으로 recv한다.
        def handle_client(conn=conn, addr=addr, conn_id=conn_id):
            remain = b""

            try:
                while True:
                    try:
                        data = conn.recv(4096)
                    except (ConnectionResetError, ConnectionAbortedError):
                        print(f"[{now()}] TCP RESET {conn_id} {addr}")
                        break
                    except OSError as e:
                        print(f"[{now()}] TCP RECV ERR {conn_id} {addr}: {e}")
                        break

                    if not data:
                        print(f"[{now()}] TCP CLOSED {conn_id} {addr}")
                        break

                    # 수신된 원시 바이트는 파싱 성공 여부와 무관하게 무조건 남긴다.
                    # "버튼을 눌렀는데 서버 로그에 아무것도 없다"를 판별하는 근거.
                    print(
                        f"\n[{now()}] RAW [{conn_id}|{get_client_user(conn)}] "
                        f"port={port} {len(data)}B: {data[:96].hex(' ')}"
                        + (" ..." if len(data) > 96 else "")
                    )

                    packets, remain = parse_packets(remain + data, conn_id)

                    if remain:
                        print(
                            f"  [PARTIAL] {conn_id} {len(remain)}바이트 대기중: "
                            f"{remain[:64].hex(' ')}"
                            + (" ..." if len(remain) > 64 else "")
                        )

                    for packet_type, packet_size, body in packets:
                        print_packet(conn, port, addr, packet_type, packet_size, body)

                        try:
                            responses = get_responses(conn, addr, packet_type, body)
                        except Exception:
                            print(
                                f"[HANDLER ERROR] {plabel(packet_type)} "
                                f"[{conn_id}]"
                            )
                            traceback.print_exc()
                            responses = [make_packet(packet_type, b"\x00\x00")]

                        send_responses(conn, addr, responses)
            finally:
                print(f"[{now()}] TCP DISCONNECT {conn_id} {addr}")
                close_socket_quietly(conn)
                cleanup_disconnect(conn, addr, port)

        threading.Thread(target=handle_client, daemon=True).start()


def udp_server(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("0.0.0.0", port))
    except OSError as e:
        # 6112 UDP는 게임/ipxwrapper가 이미 점유했을 수 있다.
        # 감청 실패는 치명적이지 않으므로 로그만 남긴다.
        print(f"UDP bind FAILED port={port}: {e}")
        return

    print(f"UDP listening on port {port}")

    while True:
        data, addr = server.recvfrom(8192)
        print(
            f"\n[{now()}] UDP {addr} port={port} {len(data)}B\n"
            f"  HEX : {data[:96].hex(' ')}" + (" ..." if len(data) > 96 else "") + "\n"
            f"  TEXT: {decode_text(data[:96])}"
        )


def main():
    setup_logging()
    load_accounts()
    ensure_default_accounts()

    print("=" * 60)
    print(f"태조왕건 더미서버 v{SERVER_VERSION} 시작. Enter 키로 종료.")
    print("※ 로그 첫 줄에 이 버전이 없으면 구버전 서버가 실행된 것입니다.")
    print("채팅창에서 /status 입력 시 현재 세션/방 상태를 로그에 출력합니다.")
    print(f"방은 생성 후 {ROOM_TTL_SECONDS // 60}분 동안 유지됩니다.")
    print("=" * 60)

    for port in UDP_PORTS:
        threading.Thread(target=udp_server, args=(port,), daemon=True).start()

    for port in TCP_PORTS:
        threading.Thread(target=tcp_server, args=(port,), daemon=True).start()

    input()


if __name__ == "__main__":
    main()
