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
    return datetime.datetime.now().strftime("%H:%M:%S")


def make_packet(packet_type, body=b""):
    return struct.pack("<HH", packet_type, 4 + len(body)) + body


def parse_packets(buffer):
    packets = []
    offset = 0

    while len(buffer) - offset >= 4:
        packet_type, packet_size = struct.unpack_from("<HH", buffer, offset)

        if packet_size < 4 or packet_size > MAX_PACKET_SIZE:
            print(f"[WARN] Invalid packet size: type=0x{packet_type:04X}, size={packet_size}")
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


def print_packet(port, addr, packet_type, packet_size, body):
    print()
    print(f"[{now()}] TCP:{port} from {addr}")
    print(f"TYPE      : 0x{packet_type:04X}")
    print(f"SIZE      : {packet_size}")
    print(f"BODY HEX  : {body.hex(' ')}")
    print(f"BODY TEXT : {decode_text(body)}")


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
            session["state"] = state
            session["room"] = room_name


def conn_label(conn):
    try:
        return f"{conn.getpeername()}->{conn.getsockname()}"
    except OSError:
        return hex(id(conn))


def print_state(label):
    with lock:
        session_users = {
            user_id: {
                "conn": conn_label(session["conn"]),
                "state": session.get("state", "lobby"),
                "room": session.get("room"),
            }
            for user_id, session in sessions.items()
        }
        client_users = [
            {
                "addr": client["addr"],
                "user_id": client.get("user_id"),
                "conn": hex(id(client["conn"])),
            }
            for client in clients
        ]
        room_state = [
            {
                "creator": room["creator"],
                "session_user": room.get("session_user"),
                "room": get_room_name(room["body"]),
                "players": list(room["players"]),
                "addr": room["addr"],
            }
            for room in rooms
        ]

    print(f"[STATE {label}] sessions={session_users}")
    print(f"[STATE {label}] clients={client_users}")
    print(f"[STATE {label}] rooms={room_state}")


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
    """Push user list + room list to all lobby-state clients.

    Called after a room is created/removed so guests see the update without
    having to press the room-list button again.
    """
    remove_stale_rooms()
    with lock:
        room_snapshot = [dict(room) for room in rooms]
        targets = [
            (client["conn"], client["addr"], client.get("user_id", "unknown"))
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
        return

    active_users = get_active_users()
    # 0x0BFF (채널 조인 알림)를 먼저 보내 클라이언트의 방 목록 수신 대기를
    # 트리거한다. test1 계열 클라이언트가 0x0BFF를 받아야 push된 방 목록을
    # 처리하는 것으로 보인다.
    combined = b"".join(
        [make_packet(0x0BFF, b"\x00" + DEFAULT_CHANNEL_NAME)]
        + make_channel_user_list_packets(active_users)
        + make_room_list_packets(room_snapshot)
    )

    for conn, addr, user_id in targets:
        try:
            conn.sendall(combined)
            print(f"[ROOM LIST PUSH] -> {addr} ({user_id})")
        except OSError as e:
            print(f"[ROOM LIST PUSH FAIL] {addr}: {e}")


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

    # The client parser expects an 18-byte user info block followed by a
    # null-terminated user id. Zeroed fields are enough for the lobby list.
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

    # Room make packets use an 8-byte prefix. The client's room-list parser
    # expects a 10-byte record header: 4 bytes of flags, current/max players,
    # then the byte length of the following detail block.
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
        print(f"[ROOM CLEANUP] removed stale rooms={removed}")


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


def leave_room_state(user_id):
    with lock:
        removed_rooms = 0

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
            old_conn = None

            with lock:
                if user_id not in accounts:
                    print(f"[LOGIN FAILED] unknown account id={user_id}")
                    return [make_packet(0x05FF, b"\x01\x00")]

                if accounts[user_id]["password"] != password:
                    print(f"[LOGIN FAILED] wrong password id={user_id}")
                    return [make_packet(0x05FF, b"\x02\x00")]

                if user_id in sessions:
                    print(f"[LOGIN REPLACED] duplicate login id={user_id}")
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
                print(f"[LOGIN REPLACED] closed old session id={user_id}, dropped={dropped}")

            set_client_user(conn, user_id)
            print(f"[LOGIN OK] id={user_id}")
            print_state("LOGIN")

            # 로그인 직후 채널 입장(0x09/0x0A/0x0B) + 유저/방 목록을 함께 보낸다.
            # 채널 입장만 보내면 일부 클라이언트(test1 등)가 "방목록 버튼" 클릭 시
            # 서버에 0x0BFF를 보내지 않고 서버 push를 기다리며 멈춘다.
            # 로그인 즉시 현재 목록을 내려주면 다이얼로그가 즉시 해소된다.
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

        print("[LOGIN FAILED] invalid body")
        return [make_packet(0x05FF, b"\x04\x00")]

    # 계정 / 닉네임 정보
    if packet_type == 0x07FF:
        user = get_client_user(conn)
        print(f"[ACCOUNT INFO] user={user}")
        return [make_packet(0x07FF, b"\x00\x00")]

    # 방 목록 요청
    # 현재 방목록 항목 구조가 확인되지 않았으므로 빈 목록만 응답
    if packet_type == 0x0BFF:
        user = get_client_user(conn)
        remove_stale_rooms()

        with lock:
            room_snapshot = [dict(room) for room in rooms]

        print(f"[ROOM LIST REQUEST] user={user}, rooms={len(room_snapshot)}")
        # 방 안에 있는 유저도 채널 유저 목록에 표시한다. 일부 클라이언트가
        # 유저 목록에 없는 유저가 만든 방 항목을 처리하지 못하기 때문이다.
        active_users = get_active_users()
        print(f"[ROOM LIST USERS] users={[item['user_id'] for item in active_users]}")

        for room in room_snapshot:
            print(
                "[ROOM LIST ITEM] "
                f"creator={room['creator']}, "
                f"created_at={room['created_at'].strftime('%H:%M:%S')}, "
                f"body={decode_text(room['body'])}"
            )

        return make_channel_user_list_packets(active_users) + make_room_list_packets(room_snapshot)

    if packet_type == 0x1FFF:
        user = get_client_user(conn)
        users = get_active_users()

        print(
            "[CHANNEL USER LIST REQUEST] "
            f"user={user}, users={[item['user_id'] for item in users]}"
        )

        return make_channel_user_list_packets(users)

    # 방 생성 요청
    if packet_type == 0x0EFF:
        session_user = get_client_user(conn)
        creator = session_user
        body_text = decode_text(body)
        parts = split_null_strings(body)
        body_owner = get_room_owner_from_body(body)
        remove_stale_rooms()

        if body_owner:
            creator = body_owner

        if session_user == "unknown" and creator != "unknown":
            set_client_user(conn, creator)

        if creator == "unknown":
            print("[ROOM CREATE REJECTED] unknown session")
            return [make_packet(0x0EFF, b"\x01\x00")]

        # 이 게임은 방 정보를 IPX 브로드캐스트로 퍼뜨려서, 방 주인이 아닌
        # 로비의 다른 클라이언트도 같은 방(owner=다른 유저)을 서버에 보고한다.
        # 예전엔 session_user != body_owner면 거부(0x02 → "같은 이름의 방 존재")
        # 했는데, 이게 옆 사람(test1) 화면을 깨고 방 주인의 방 등록까지 막아
        # 방장(user_a)은 "방만들기 요청중"에서 멈췄다. 거부하지 말고 방 주인
        # 명의로 등록한다.
        reported_by_other = (
            session_user != "unknown" and body_owner and session_user != body_owner
        )

        # 호스트 접속 주소는 방 주인의 세션 주소를 우선 사용한다. 주인이 아닌
        # 사람이 보고하면 그 사람 주소가 호스트로 잘못 박히기 때문이다.
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

        # 방이 생성됐음을 로비에 있는 다른 유저들에게 즉시 push한다.
        # 방목록 버튼 클릭 시 서버 push를 기다리는 클라이언트(test1 등)가
        # "방리스트 요청중" 다이얼로그에서 멈추지 않도록 한다.
        broadcast_room_list_to_lobby(except_conn=conn)

        if reported_by_other:
            print(f"[ROOM CREATE SYNCED] reporter={session_user}, owner={creator}")
        print("[ROOM CREATE OK]")
        print(f"CREATOR       : {creator}")
        print(f"ROOM BODY HEX : {body.hex(' ')}")
        print(f"ROOM BODY TEXT: {body_text}")
        print_state("ROOM_CREATE")

        # 원문 echo 금지, OK만 응답
        return [make_packet(0x0EFF, b"\x00\x00")]

    if packet_type == 0x10FF:
        user = get_client_user(conn)
        requested = split_null_strings(body)
        requested_name = requested[0] if requested else ""
        room = find_room_by_request(body)

        if not room:
            print(f"[ROOM JOIN FAILED] user={user}, requested={requested_name}")
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
            "[ROOM JOIN OK] "
            f"user={user}, requested={requested_name}, "
            f"room={decode_text(room_name_bytes)}, host_ip={host_ip}"
        )
        print_state("ROOM_JOIN")

        join_body = b"\x00" + room_name_bytes + encode_text(host_ip)
        return [make_packet(0x10FF, join_body)]

    # 방 취소 / 채널 재조인
    if packet_type == 0x11FF:
        user = get_client_user(conn)
        print(f"[ROOM EXIT / CHANNEL REJOIN] user={user}")
        if user != "unknown":
            remove_user_from_rooms(user)
            set_user_state(user, "lobby", None)
            print_state("ROOM_EXIT")

        # 방이 제거됐으므로 로비에 있는 다른 클라이언트에게 방 목록을 push한다.
        broadcast_room_list_to_lobby(except_conn=conn)

        # ack + 채널 재조인(0x09/0x0A/0x0B)만 보낸다.
        # inline으로 유저/방 목록까지 보내면 클라이언트가 채널 재조인 시퀀스 도중
        # 받은 목록 패킷을 처리하지 못하고 로비 복귀에 실패할 수 있다.
        # 클라이언트는 0x0BFF를 받은 후 자연스럽게 0x0BFF 요청을 보내고
        # 서버가 그에 응답해 목록을 받는다.
        return (
            [make_packet(0x11FF, b"\x00\x00")]
            + make_lobby_rejoin_packets()
        )

    # 채팅
    if packet_type == 0x12FF:
        message = decode_text(body)
        sender = get_client_user(conn)

        if message == "/status":
            return [make_packet(0x12FF, b"\x00\x00")]

        if sender == "unknown":
            print(f"[CHAT IGNORED] unknown sender message={message}")
            return [make_packet(0x12FF, b"\x01\x00")]

        print(f"[CHAT RECEIVED] {sender}: {message}")

        chat_packet = make_lobby_chat_response(sender, message)
        broadcast_chat(chat_packet)

        return []

    if packet_type == 0x24FF:
        user = get_client_user(conn)
        print(f"[GAME REPORT] user={user}")
        if user != "unknown":
            leave_room_state(user)
            set_user_state(user, "lobby", None)
            print_state("GAME_REPORT")

        # 게임 종료 후 점수판에서 '확인'을 누르면 로비 채널로 복귀해야 한다.
        # ack(0x24FF)만 보내면 "채널을 조인중입니다"에서 멈추므로
        # 채널 재조인(0x09/0x0A/0x0B)을 함께 내려준다.
        # 유저/방 목록은 inline으로 보내지 않고, 채널 재조인 후 클라이언트가
        # 자연스럽게 0x0BFF를 보내면 그 응답으로 보낸다.
        broadcast_room_list_to_lobby(except_conn=conn)
        return (
            [make_packet(0x24FF, b"\x00\x00")]
            + make_lobby_rejoin_packets()
        )

    return [make_packet(packet_type, b"\x00\x00")]


def send_response(conn, addr, response):
    print(f"[{now()}] SEND to {addr}")
    print(response.hex(" "))

    try:
        conn.sendall(response)
    except OSError as e:
        print(f"[{now()}] SEND ERROR {addr}: {e}")


def cleanup_disconnect(conn, addr, port):
    # 연결이 어떤 식으로 끊겼든(정상 종료/소켓 에러/예외) 항상 호출되어
    # 죽은 연결을 clients/sessions에서 제거한다. 정리를 건너뛰면 죽은
    # 연결이 유령으로 남아 다른 유저 목록/접속을 망가뜨린다.
    disconnected_user = get_client_user(conn)

    if port != 6112:
        return

    with lock:
        for client in list(clients):
            if client["conn"] is conn:
                clients.remove(client)

        # 이 conn이 현재 세션의 주인일 때만 세션을 지운다.
        # (이미 다른 새 연결로 대체됐다면 그 세션은 건드리지 않는다.)
        if disconnected_user != "unknown":
            session = sessions.get(disconnected_user)
            if session and session.get("conn") is conn:
                sessions.pop(disconnected_user, None)

    if disconnected_user != "unknown":
        print(
            "[DISCONNECT] keep advertised rooms until TTL "
            f"user={disconnected_user}"
        )

    print(f"[DISCONNECT] user={disconnected_user}")
    print_state("DISCONNECT")


def tcp_server(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(20)

    print(f"TCP listening on {port}")

    while True:
        conn, addr = server.accept()
        print(f"\n[{now()}] TCP connected {addr} -> port {port}")

        if port == 6112:
            with lock:
                clients.append({
                    "conn": conn,
                    "addr": addr,
                    "user_id": None,
                })

        def handle_client():
            remain = b""

            try:
                while True:
                    try:
                        data = conn.recv(4096)
                    except (ConnectionResetError, ConnectionAbortedError):
                        print(f"[{now()}] TCP reset by client {addr}")
                        break
                    except OSError as e:
                        # 소켓이 다른 경로에서 닫혔거나(WinError 10038 등) 비정상
                        # 종료된 경우. 스레드를 죽이지 말고 깔끔히 빠져나간다.
                        print(f"[{now()}] TCP recv error {addr}: {e}")
                        break

                    if not data:
                        print(f"[{now()}] TCP closed {addr}")
                        break

                    print()
                    print(f"[{now()}] TCP RAW from {addr} / port {port} / {len(data)} bytes")
                    print(data.hex(" "))

                    packets, remain = parse_packets(remain + data)

                    for packet_type, packet_size, body in packets:
                        print_packet(port, addr, packet_type, packet_size, body)

                        try:
                            responses = get_responses(conn, addr, packet_type, body)
                        except Exception:
                            # 한 패킷 처리 중 예외가 나도 연결을 끊지 않는다.
                            # 핸들러 예외로 스레드가 죽으면 해당 유저의 접속이
                            # 끊기고, 재접속/재로그인 과정에서 다른 유저(호스트)까지
                            # 튕기는 것처럼 보일 수 있으므로 방어한다.
                            print(f"[HANDLER ERROR] type=0x{packet_type:04X} from {addr}")
                            traceback.print_exc()
                            responses = [make_packet(packet_type, b"\x00\x00")]

                        for response in responses:
                            send_response(conn, addr, response)
            finally:
                # 정상 종료/소켓 에러/예외 어느 경우든 반드시 정리한다.
                close_socket_quietly(conn)
                cleanup_disconnect(conn, addr, port)

        threading.Thread(target=handle_client, daemon=True).start()


def udp_server(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))

    print(f"UDP listening on {port}")

    while True:
        data, addr = server.recvfrom(4096)
        print()
        print(f"[{now()}] UDP RAW from {addr} / port {port} / {len(data)} bytes")
        print(data.hex(" "))


def main():
    setup_logging()
    load_accounts()
    ensure_default_accounts()

    print("Dummy server running. Press Enter to stop.")
    print("Recommended accounts:")
    print("  A PC: user_a / 1111")
    print("  B PC: user_b / 2222")
    print(f"Rooms stay visible for {ROOM_TTL_SECONDS // 60} minutes after creation.")

    for port in UDP_PORTS:
        threading.Thread(target=udp_server, args=(port,), daemon=True).start()

    for port in TCP_PORTS:
        threading.Thread(target=tcp_server, args=(port,), daemon=True).start()

    input()


if __name__ == "__main__":
    main()
