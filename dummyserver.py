import socket
import threading
import struct
import datetime
import json
import os
import sys
import traceback

TCP_PORTS = [9000, 6112]
UDP_PORTS = [9000]
MAX_PACKET_SIZE = 1024
ROOM_TTL_SECONDS = 10 * 60
DEFAULT_CHANNEL_NAME = b"\xf7\xbc\xf0\xd5\xe8\xdd\xcb\xef\x00"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", BASE_DIR),
    "TaejoWanggeonDummyServer",
)
ACCOUNTS_FILE = os.path.join(STATE_DIR, "accounts.json")
RUN_LOG_FILE = os.path.join(
    BASE_DIR,
    "dummyserver_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".log",
)

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


def setup_logging():
    log_path = RUN_LOG_FILE
    try:
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)
    except OSError:
        log_path = os.path.abspath(
            "dummyserver_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
        )
        log_file = open(log_path, "w", encoding="utf-8", buffering=1)

    sys.stdout = TeeOutput(sys.stdout, log_file)
    sys.stderr = TeeOutput(sys.stderr, log_file)
    print(f"[LOG FILE] {log_path}")


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


def parse_packets(buffer):
    packets = []
    offset = 0

    while len(buffer) - offset >= 4:
        packet_type, packet_size = struct.unpack_from("<HH", buffer, offset)

        if packet_size < 4 or packet_size > MAX_PACKET_SIZE:
            print(f"[WARN] Invalid packet size: type={plabel(packet_type)}, size={packet_size}")
            break

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
    dead = []

    with lock:
        for client in clients:
            try:
                client["conn"].sendall(packet)
            except OSError:
                dead.append(client)

        for client in dead:
            if client in clients:
                clients.remove(client)


def broadcast_room_list_to_lobby(except_conn=None):
    """Push room list to all lobby-state clients (except_conn 제외).

    유저 목록(0x1FFF)은 보내지 않는다 — 클라이언트가 APPEND해서 무한 증가.
    """
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
            conn.sendall(combined)
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


def make_room_list_record(room_body):
    if len(room_body) < 9:
        print("[ROOM LIST SKIP] room body too short")
        return b""

    room_name, room_detail = split_first_null(room_body[8:])

    if not room_name or not room_detail:
        print("[ROOM LIST SKIP] missing room name/detail")
        return b""

    expected_detail_len = 0
    if len(room_body) >= 8:
        expected_detail_len = struct.unpack_from("<H", room_body, 6)[0]

    if (
        expected_detail_len > 0
        and len(room_detail) == expected_detail_len + 1
        and room_detail[:1] == b"\x00"
    ):
        room_detail = room_detail[1:]

    max_players = 2
    if len(room_body) >= 6:
        max_players = max(2, struct.unpack_from("<H", room_body, 4)[0])

    list_flags = b"\x00\x00\x00\x00"

    return (
        list_flags
        + struct.pack("<HHH", 1, max_players, len(room_detail))
        + room_name
        + room_detail
    )


def make_room_list_packets(room_snapshot):
    if not room_snapshot:
        return make_empty_room_list_packets()

    list_body = b""

    for room in room_snapshot:
        list_body += make_room_list_record(room["body"])

    if not list_body:
        return make_empty_room_list_packets()

    return [
        make_packet(0x0CFF, b""),
        make_packet(0x0DFF, list_body),
        make_packet(0x0DFF, b""),
    ]


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


def notify_room_host_player_left(leaving_user):
    """참가자가 방을 떠났을 때 호스트에게 업데이트된 방 목록을 push한다."""
    host_info = find_room_host_for_player(leaving_user)
    if not host_info:
        print(f"[HOST NOTIFY] {leaving_user} 의 호스트 없음 (방에 없거나 이미 나감)")
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
        host_conn.sendall(combined)
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

            remove_stale_rooms()
            with lock:
                room_snapshot_login = [dict(room) for room in rooms]
            active_users_login = get_active_users()
            return (
                [make_packet(0x05FF, b"\x00\x00")]
                + make_lobby_rejoin_packets()
                + make_channel_user_list_packets(active_users_login)
                + make_room_list_packets(room_snapshot_login)
            )

        print(f"[LOGIN FAILED] [{conn_id}] invalid body")
        return [make_packet(0x05FF, b"\x04\x00")]

    # 계정 / 닉네임 정보
    # · 로그인 직후: body = 게임이름(太祖王建, 9+ bytes) → 목록 중복 방지로 ACK만
    # · 방 나가기 후: body = \x00 (1 byte) → 유저/방 목록 내려줌
    if packet_type == 0x07FF:
        print(f"[ACCT_INFO] [{conn_id}|{user}] body_len={len(body)}")

        if len(body) <= 1:
            print(f"  → post-room-exit 분기: 유저/방 목록 전송")
            remove_stale_rooms()
            with lock:
                room_snapshot_07 = [dict(room) for room in rooms]
            active_users_07 = get_active_users()
            return (
                [make_packet(0x07FF, b"\x00\x00")]
                + make_channel_user_list_packets(active_users_07)
                + make_room_list_packets(room_snapshot_07)
            )

        print(f"  → login-after 분기: ACK만")
        return [make_packet(0x07FF, b"\x00\x00")]

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
            # 참가자 퇴장 시 호스트에게 먼저 알린다 (방 제거 전에 호스트를 찾아야 함).
            threading.Thread(
                target=notify_room_host_player_left,
                args=(user,),
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
        print(f"[GAME REPORT] [{conn_id}|{user}]")
        if user != "unknown":
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

    print(f"[UNHANDLED] [{conn_id}|{user}] {plabel(packet_type)}")
    return [make_packet(packet_type, b"\x00\x00")]


def send_response(conn, addr, response):
    conn_id = get_conn_id(conn)
    user = get_client_user(conn)
    if len(response) >= 4:
        ptype = struct.unpack_from("<H", response, 0)[0]
        psize = struct.unpack_from("<H", response, 2)[0]
        pbody = response[4:4 + max(0, psize - 4)]
        bsummary = pbody.hex(" ") if len(pbody) <= 20 else pbody[:20].hex(" ") + "..."
        print(f"  → [{conn_id}|{user}] {plabel(ptype)} size={psize} body=[{bsummary}]")
    else:
        print(f"  → [{conn_id}|{user}] raw={response.hex(' ')}")
    try:
        conn.sendall(response)
    except OSError as e:
        print(f"  ! [{conn_id}|{user}] SEND ERROR: {e}")


def cleanup_disconnect(conn, addr, port):
    disconnected_user = get_client_user(conn)

    if port != 6112:
        return

    # 방에 참가자로 있었으면 호스트에게 알림 (세션/clients 제거 전에 해야 호스트를 찾을 수 있다).
    if disconnected_user != "unknown":
        threading.Thread(
            target=notify_room_host_player_left,
            args=(disconnected_user,),
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

                    packets, remain = parse_packets(remain + data)

                    if remain:
                        print(
                            f"  [PARTIAL] {conn_id} {len(remain)}바이트 대기중: "
                            f"{remain.hex(' ')}"
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

                        for response in responses:
                            send_response(conn, addr, response)
            finally:
                print(f"[{now()}] TCP DISCONNECT {conn_id} {addr}")
                close_socket_quietly(conn)
                cleanup_disconnect(conn, addr, port)

        threading.Thread(target=handle_client, daemon=True).start()


def udp_server(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))

    print(f"UDP listening on port {port}")

    while True:
        data, addr = server.recvfrom(4096)
        print()
        print(f"[{now()}] UDP {addr} port={port} {len(data)}B: {data[:32].hex(' ')}")


def main():
    setup_logging()
    load_accounts()
    ensure_default_accounts()

    print("=" * 60)
    print("태조왕건 더미서버 시작. Enter 키로 종료.")
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
