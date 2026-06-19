import socket
import threading
import struct
import datetime
import json
import os
import sys

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

            return [make_packet(0x05FF, b"\x00\x00")]

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
        active_users = get_lobby_users(include_user=user)
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
        users = get_lobby_users(include_user=user)

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

        if session_user != "unknown" and body_owner and session_user != body_owner:
            print("[ROOM CREATE REJECTED] session/body owner mismatch")
            print(f"SESSION USER : {session_user}")
            print(f"BODY OWNER   : {body_owner}")
            print(f"BODY TEXT    : {body_text}")
            print_state("ROOM_CREATE_REJECTED")
            return [make_packet(0x0EFF, b"\x02\x00")]

        add_or_replace_room({
            "creator": creator,
            "session_user": session_user,
            "addr": addr,
            "body": body,
            "created_at": datetime.datetime.now(),
            "players": [creator],
        })
        set_user_state(creator, "room", get_room_name(body))

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

        return [make_packet(0x11FF, b"\x00\x00")]

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
        return [make_packet(0x24FF, b"\x00\x00")]

    return [make_packet(packet_type, b"\x00\x00")]


def send_response(conn, addr, response):
    print(f"[{now()}] SEND to {addr}")
    print(response.hex(" "))

    try:
        conn.sendall(response)
    except OSError as e:
        print(f"[{now()}] SEND ERROR {addr}: {e}")


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

            with conn:
                while True:
                    try:
                        data = conn.recv(4096)
                    except ConnectionResetError:
                        print(f"[{now()}] TCP reset by client {addr}")
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

                        responses = get_responses(conn, addr, packet_type, body)

                        for response in responses:
                            send_response(conn, addr, response)

            disconnected_user = get_client_user(conn)

            if port == 6112:
                with lock:
                    for client in list(clients):
                        if client["conn"] is conn:
                            clients.remove(client)

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
