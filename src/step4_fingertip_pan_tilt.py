"""步骤4：识别食指指尖，并通过USB串口控制ESP32二自由度云台。"""

# 延迟求值类型注解，提升Python版本兼容性并简化类内部类型标注。
from __future__ import annotations

# argparse读取摄像头、串口、手别和控制参数。
import argparse
# time用于MediaPipe时间戳、FPS、串口复位等待和发送限频。
import time
# deque保存目标手最近一段指尖轨迹。
from collections import deque
# Path用于构造模型文件的绝对路径。
from pathlib import Path
# TypeAlias声明更直观的坐标类型名称。
from typing import TypeAlias

# OpenCV负责摄像头读取、画面显示和辅助图形绘制。
import cv2
# MediaPipe主体包负责把NumPy RGB数组包装为模型输入。
import mediapipe as mp
# pyserial负责通过USB虚拟串口向ESP32发送目标计数。
import serial
# list_ports用于自动列出并选择Windows中的COM端口。
from serial.tools import list_ports
# MediaPipe Tasks基础接口。
from mediapipe.tasks import python
# MediaPipe视觉任务接口，包含HandLandmarker。
from mediapipe.tasks.python import vision


# 当前文件位于“项目/src”，向上两级得到项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 默认使用项目内已经验证过的手部关键点模型。
DEFAULT_MODEL = PROJECT_ROOT / "models" / "hand_landmarker.task"
# PixelPoint表示一个整数像素坐标。
PixelPoint: TypeAlias = tuple[int, int]
# NormalizedPoint表示一个0到1之间的浮点归一化坐标。
NormalizedPoint: TypeAlias = tuple[float, float]

# 这些数值必须与ESP32固件中的实物校准结果一致。
CHANNEL0_CENTER = 317
CHANNEL1_CENTER = 307
CHANNEL0_MINIMUM = 215
CHANNEL0_MAXIMUM = 419
CHANNEL1_MINIMUM = 205
CHANNEL1_MAXIMUM = 409


def parse_args() -> argparse.Namespace:
    """定义并解析摄像头、串口和跟踪控制参数。"""

    # 创建命令行参数解析器。
    parser = argparse.ArgumentParser(
        description="Track one index fingertip and control an ESP32 pan-tilt mount."
    )
    # Windows默认摄像头编号通常为0。
    parser.add_argument("--camera", type=int, default=0, help="Camera device index")
    # 请求1280像素宽的画面。
    parser.add_argument("--width", type=int, default=1280, help="Requested frame width")
    # 请求720像素高的画面。
    parser.add_argument("--height", type=int, default=720, help="Requested frame height")
    # auto表示程序自动寻找最可能的USB串口，也可显式填写COM7等名称。
    parser.add_argument(
        "--port",
        default="auto",
        help="ESP32 serial port, for example COM7; default: auto",
    )
    # 波特率必须与ESP32固件中的Serial.begin保持一致。
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    # 默认只用右手控制，避免两只手同时出现时目标不断跳换。
    parser.add_argument(
        "--hand",
        choices=("Left", "Right", "Any"),
        default="Right",
        help="Which hand controls the mount",
    )
    # 发送频率默认25Hz，低于固件50Hz运动更新频率且足够流畅。
    parser.add_argument(
        "--send-hz",
        type=float,
        default=25.0,
        help="Maximum serial target update rate",
    )
    # 指数平滑系数越小越平稳但延迟越大，1表示完全不平滑。
    parser.add_argument(
        "--smoothing",
        type=float,
        default=0.25,
        help="Fingertip exponential smoothing factor in (0, 1]",
    )
    # 指尖消失0.6秒后让云台回中，短暂遮挡期间先保持最后目标。
    parser.add_argument(
        "--lost-timeout",
        type=float,
        default=0.6,
        help="Seconds to hold the last target before returning to center",
    )
    # 图像中心附近2.5%的区域吸附到精确中位，减少静止时的微小抖动。
    parser.add_argument(
        "--dead-zone",
        type=float,
        default=0.025,
        help="Half-width of the normalized center dead zone",
    )
    # 允许替换默认MediaPipe模型文件。
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    # dry-run只显示视觉目标，不打开串口，也不会让舵机动作。
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run vision and mapping without opening a serial port",
    )
    # 返回解析完成的参数对象。
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """在占用摄像头和串口前检查参数，尽早给出清楚的错误。"""

    # MediaPipe模型必须真实存在。
    if not args.model.is_file():
        raise FileNotFoundError(f"Hand landmark model not found: {args.model}")
    # 发送频率必须为正数。
    if args.send_hz <= 0:
        raise ValueError("--send-hz must be greater than 0")
    # 平滑系数必须位于(0, 1]。
    if not 0.0 < args.smoothing <= 1.0:
        raise ValueError("--smoothing must be in the interval (0, 1]")
    # 丢失等待时间不能为负数。
    if args.lost_timeout < 0:
        raise ValueError("--lost-timeout must be at least 0")
    # 死区必须小于半幅画面，否则中心之外将没有可用控制范围。
    if not 0.0 <= args.dead_zone < 0.5:
        raise ValueError("--dead-zone must be in the interval [0, 0.5)")


def available_port_description() -> str:
    """生成当前COM端口列表，供自动选择失败时显示。"""

    # 读取系统中所有串口对象。
    ports = list(list_ports.comports())
    # 没有串口时返回明确文字。
    if not ports:
        return "no serial ports found"
    # 将设备名和描述拼成便于复制检查的一行文本。
    return "; ".join(f"{port.device} ({port.description})" for port in ports)


def choose_serial_port(requested_port: str) -> str:
    """使用显式端口，或在auto模式下选择最可能的ESP32端口。"""

    # 用户明确填写COM端口时直接使用，不擅自替换。
    if requested_port.lower() != "auto":
        return requested_port

    # 把迭代器转换为列表，后面需要多次筛选。
    ports = list(list_ports.comports())
    # 只有一个端口时，它就是最合理的自动选择。
    if len(ports) == 1:
        return ports[0].device

    # 常见ESP32、CH343和USB串口描述中可能出现的关键词。
    likely_keywords = ("ESP32", "CH343", "CH340", "USB", "UART", "SERIAL")
    # 将描述、厂商和硬件ID合在一起进行不区分大小写的匹配。
    likely_ports = [
        port
        for port in ports
        if any(
            keyword in f"{port.description} {port.manufacturer} {port.hwid}".upper()
            for keyword in likely_keywords
        )
    ]
    # 恰好找到一个高可能端口时返回它。
    if len(likely_ports) == 1:
        return likely_ports[0].device

    # 多个候选时不猜测，要求用户通过--port明确指定，避免连接错误设备。
    raise RuntimeError(
        "Cannot choose one ESP32 serial port automatically. "
        f"Available ports: {available_port_description()}. "
        "Run again with --port COM7 (replace COM7 with the correct port)."
    )


class PanTiltSerial:
    """管理ESP32串口连接、命令发送和少量状态文本接收。"""

    def __init__(self, port: str, baud: int) -> None:
        """打开串口，等待ESP32复位，然后用PING确认固件正在运行。"""

        # 保存端口名用于窗口状态显示。
        self.port = port
        # 空字符串保存尚未组成完整行的ESP32返回数据。
        self._receive_buffer = ""
        # 保存最近一条完整状态消息。
        self.last_message = "opening serial port"
        # 打开非阻塞读取串口；写超时防止设备异常时程序永久卡住。
        self.connection = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=0,
            write_timeout=0.2,
        )
        # 打开串口通常会让ESP32复位；等待其1.5秒setup完成并额外留出余量。
        time.sleep(2.3)
        # 读取启动信息，避免它与后续PONG混在一起。
        self.read_messages()
        # 主动发送PING，确认当前端口连接的确实是我们的控制固件。
        self.send_line("PING")
        # 最多等待1秒接收PONG。
        deadline = time.perf_counter() + 1.0
        received_pong = False
        while time.perf_counter() < deadline:
            # 读取ESP32在本次轮询中返回的所有完整行。
            messages = self.read_messages()
            if "PONG" in messages:
                received_pong = True
                break
            # 10毫秒短暂等待避免高占用轮询。
            time.sleep(0.01)
        # 没有PONG通常表示烧录的不是serial_pan_tilt固件或端口选择错误。
        if not received_pong:
            self.connection.close()
            raise RuntimeError(
                f"{port} did not reply PONG. Upload firmware/serial_pan_tilt first "
                "and close the PlatformIO serial monitor."
            )
        # 连接成功后记录状态。
        self.last_message = "PONG - controller connected"

    def send_line(self, line: str) -> None:
        """给文本命令添加换行符并编码为ASCII后发送。"""

        # 固件按\n判断一条命令结束，因此这里必须追加换行符。
        payload = f"{line}\n".encode("ascii")
        # write把完整命令交给Windows串口驱动。
        self.connection.write(payload)

    def send_target(self, channel0: int, channel1: int) -> None:
        """发送T,<CH0>,<CH1>格式的双轴目标。"""

        # ESP32仍会执行第二次安全限位，形成电脑端与固件端双重保护。
        self.send_line(f"T,{channel0},{channel1}")

    def send_center(self) -> None:
        """请求两个轴以固件限速回到各自校准中位。"""

        self.send_line("CENTER")

    def read_messages(self) -> list[str]:
        """非阻塞读取ESP32返回内容，并提取所有以换行结束的完整消息。"""

        # in_waiting表示Windows已经收到但尚未读取的字节数。
        waiting = self.connection.in_waiting
        if waiting > 0:
            # errors=replace保证偶发无效字节不会让整个控制程序崩溃。
            incoming = self.connection.read(waiting).decode("ascii", errors="replace")
            self._receive_buffer += incoming

        # splitlines不能区分末尾半行，因此手动按\n逐行提取。
        messages: list[str] = []
        while "\n" in self._receive_buffer:
            # partition把第一个换行前后的内容分开。
            line, _, remaining = self._receive_buffer.partition("\n")
            self._receive_buffer = remaining
            # 去除Windows可能附带的\r和两端空格。
            line = line.strip()
            if line:
                messages.append(line)
                self.last_message = line
        return messages

    def close(self) -> None:
        """退出前请求回中，然后关闭Windows串口句柄。"""

        # 只有仍处于打开状态时才发送命令。
        if self.connection.is_open:
            try:
                # 正常退出时让云台回中，而不是停在画面边缘。
                self.send_center()
                # 给系统一点时间把短命令交给USB设备。
                time.sleep(0.1)
            except serial.SerialException:
                # 设备已被拔出时无法回中，但仍需继续释放本地句柄。
                pass
            finally:
                self.connection.close()


def hand_label(result: object, hand_index: int) -> str:
    """读取MediaPipe给出的Left/Right标签；缺失时返回Hand。"""

    # handedness存在对应项且分类列表非空时读取最高置信度类别。
    if hand_index < len(result.handedness) and result.handedness[hand_index]:
        return result.handedness[hand_index][0].category_name
    # 极少数没有左右手分类的情况使用通用名称。
    return "Hand"


def normalized_fingertip(landmark: object) -> NormalizedPoint:
    """读取食指指尖归一化坐标，并限制到0到1范围。"""

    # 防止模型在画面边缘给出略小于0或略大于1的坐标。
    x = min(max(float(landmark.x), 0.0), 1.0)
    y = min(max(float(landmark.y), 0.0), 1.0)
    return x, y


def select_control_fingertip(result: object, preferred_hand: str) -> tuple[str, NormalizedPoint] | None:
    """从本帧检测结果中选择控制云台的那只手及其食指指尖。"""

    # 按MediaPipe返回顺序遍历每一只手。
    for hand_index, landmarks in enumerate(result.hand_landmarks):
        # 获取当前手的Left、Right或Hand标签。
        label = hand_label(result, hand_index)
        # Any接受第一只手；指定Left/Right时只接受标签匹配的手。
        if preferred_hand == "Any" or label == preferred_hand:
            # 第8号关键点固定代表食指指尖。
            return label, normalized_fingertip(landmarks[8])
    # 当前帧没有目标手。
    return None


def smooth_point(
    previous: NormalizedPoint | None,
    current: NormalizedPoint,
    alpha: float,
) -> NormalizedPoint:
    """用指数移动平均平滑指尖坐标，减小检测噪声造成的舵机抖动。"""

    # 第一次检测没有历史值，直接以当前坐标开始。
    if previous is None:
        return current
    # 新值权重为alpha，历史值权重为1-alpha。
    smoothed_x = (1.0 - alpha) * previous[0] + alpha * current[0]
    smoothed_y = (1.0 - alpha) * previous[1] + alpha * current[1]
    return smoothed_x, smoothed_y


def apply_center_dead_zone(value: float, dead_zone: float) -> float:
    """将画面中心附近的小范围吸附到0.5，减少静止时微调。"""

    # 与画面中心的绝对距离不超过dead_zone时返回精确中心。
    if abs(value - 0.5) <= dead_zone:
        return 0.5
    # 中心死区之外保持原始位置，不改变画面边缘映射。
    return value


def map_coordinate_to_servo(
    coordinate: float,
    minimum: int,
    maximum: int,
    inverted: bool,
) -> int:
    """把0到1图像坐标线性映射为一个舵机安全计数。"""

    # 方向反转时用1-coordinate，让画面两端与舵机两端互换。
    mapped_coordinate = 1.0 - coordinate if inverted else coordinate
    # 线性插值得到浮点目标计数。
    pulse = minimum + mapped_coordinate * (maximum - minimum)
    # round取最近整数，随后再次限制范围以抵抗浮点误差。
    return min(max(round(pulse), minimum), maximum)


def to_pixel(point: NormalizedPoint, width: int, height: int) -> PixelPoint:
    """把归一化坐标转换为当前帧内的整数像素位置。"""

    # width-1和height-1保证坐标最大值不会越过数组边界。
    return round(point[0] * (width - 1)), round(point[1] * (height - 1))


def main() -> None:
    """运行摄像头检测、坐标映射、串口发送和实时界面。"""

    # 解析并验证命令行参数。
    args = parse_args()
    validate_args(args)

    # 使用DirectShow打开Windows摄像头，通常能降低首次打开延迟。
    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    # 请求用户指定的宽高；摄像头可能根据自身能力选择最接近的值。
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    # 摄像头打开失败时不继续占用串口。
    if not camera.isOpened():
        raise RuntimeError(f"Cannot open camera {args.camera}. Try --camera 1.")

    # 串口对象在dry-run模式下保持None。
    controller: PanTiltSerial | None = None
    try:
        # 非dry-run模式选择并连接ESP32。
        if not args.dry_run:
            selected_port = choose_serial_port(args.port)
            print(f"Opening ESP32 controller on {selected_port} at {args.baud} baud...")
            controller = PanTiltSerial(selected_port, args.baud)
            # 连接建立后先确保目标处于中位。
            controller.send_center()
            print("ESP32 controller connected. Keep the PlatformIO monitor closed.")
        else:
            print("Dry-run mode: no serial port will be opened and no servo will move.")

        # 配置MediaPipe视频模式手部关键点模型。
        options = vision.HandLandmarkerOptions(
            # 指定本地模型文件。
            base_options=python.BaseOptions(model_asset_path=str(args.model)),
            # VIDEO模式利用相邻帧信息进行追踪。
            running_mode=vision.RunningMode.VIDEO,
            # 最多检测两只手，但只选择args.hand指定的一只控制。
            num_hands=2,
            # 初次检测、手存在和帧间追踪阈值沿用已经验证的0.5。
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        # 平滑后的指尖坐标；开始时尚未检测到手。
        filtered_fingertip: NormalizedPoint | None = None
        # 保存最近60个平滑像素点，用于显示控制轨迹。
        trail: deque[PixelPoint] = deque(maxlen=60)
        # 默认图像坐标增大对应舵机计数增大；可用H/V键现场切换。
        pan_inverted = False
        tilt_inverted = False
        # P键可以暂停或恢复跟踪。
        tracking_enabled = True
        # 记录最后一次看到目标手的时间。
        last_seen_time = time.perf_counter()
        # 初始尚未看到手，因此云台已经视为回中。
        center_command_sent = True
        # 限制串口命令发送频率。
        send_interval = 1.0 / args.send_hz
        last_send_time = 0.0
        # 记录上一次发出的目标，主要用于窗口显示。
        last_channel0_target = CHANNEL0_CENTER
        last_channel1_target = CHANNEL1_CENTER
        # MediaPipe视频时间戳必须严格递增。
        start_time = time.perf_counter()
        previous_timestamp_ms = -1
        # FPS指数平均所需状态。
        previous_frame_time = start_time
        smoothed_fps = 0.0

        # with结束时MediaPipe自动释放模型资源。
        with vision.HandLandmarker.create_from_options(options) as landmarker:
            # 持续处理摄像头帧，直到用户按Q或Esc。
            while True:
                # 读取一帧BGR图像。
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("The camera opened, but no frame could be read.")

                # 水平镜像，使画面交互符合照镜子的直觉。
                frame = cv2.flip(frame, 1)
                # 获取摄像头实际输出宽高。
                height, width = frame.shape[:2]
                # MediaPipe模型需要RGB颜色顺序。
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # 把NumPy数组包装为MediaPipe图像对象。
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

                # 生成严格递增的毫秒时间戳。
                timestamp_ms = int((time.perf_counter() - start_time) * 1000)
                timestamp_ms = max(timestamp_ms, previous_timestamp_ms + 1)
                previous_timestamp_ms = timestamp_ms
                # 执行本帧手部检测与追踪。
                result = landmarker.detect_for_video(mp_image, timestamp_ms)
                # 当前高精度计时点用于丢失判断和发送限频。
                now = time.perf_counter()

                # 默认本帧没有可控制的目标手。
                selected = None
                if tracking_enabled:
                    selected = select_control_fingertip(result, args.hand)

                # 找到目标手时更新平滑坐标和舵机目标。
                if selected is not None:
                    selected_label, raw_fingertip = selected
                    # 指数移动平均减少模型像素噪声。
                    filtered_fingertip = smooth_point(
                        filtered_fingertip, raw_fingertip, args.smoothing
                    )
                    last_seen_time = now
                    center_command_sent = False
                    # 分别给x和y应用中心死区。
                    control_x = apply_center_dead_zone(
                        filtered_fingertip[0], args.dead_zone
                    )
                    control_y = apply_center_dead_zone(
                        filtered_fingertip[1], args.dead_zone
                    )
                    # 把图像横坐标映射到CH0安全范围。
                    last_channel0_target = map_coordinate_to_servo(
                        control_x,
                        CHANNEL0_MINIMUM,
                        CHANNEL0_MAXIMUM,
                        pan_inverted,
                    )
                    # 把图像纵坐标映射到CH1安全范围。
                    last_channel1_target = map_coordinate_to_servo(
                        control_y,
                        CHANNEL1_MINIMUM,
                        CHANNEL1_MAXIMUM,
                        tilt_inverted,
                    )
                    # 达到发送周期时把最新目标交给ESP32。
                    if controller is not None and now - last_send_time >= send_interval:
                        controller.send_target(
                            last_channel0_target, last_channel1_target
                        )
                        last_send_time = now

                    # 将平滑后的指尖点加入显示轨迹。
                    fingertip_pixel = to_pixel(filtered_fingertip, width, height)
                    trail.append(fingertip_pixel)
                    # 绘制黄色实心目标点和白色外环。
                    cv2.circle(
                        frame, fingertip_pixel, 10, (0, 220, 255), -1, cv2.LINE_AA
                    )
                    cv2.circle(
                        frame, fingertip_pixel, 14, (255, 255, 255), 2, cv2.LINE_AA
                    )
                    # 显示当前控制手标签。
                    cv2.putText(
                        frame,
                        f"Control: {selected_label}",
                        (fingertip_pixel[0] + 18, fingertip_pixel[1] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (0, 220, 255),
                        2,
                        cv2.LINE_AA,
                    )
                else:
                    # 暂停时立即回中；丢失时等待lost-timeout后回中。
                    should_center = not tracking_enabled or (
                        now - last_seen_time >= args.lost_timeout
                    )
                    if should_center and not center_command_sent:
                        if controller is not None:
                            controller.send_center()
                        last_channel0_target = CHANNEL0_CENTER
                        last_channel1_target = CHANNEL1_CENTER
                        center_command_sent = True
                        # 清除历史平滑值，防止手重新出现时受到旧位置拖拽。
                        filtered_fingertip = None
                        trail.clear()

                # 连接轨迹中的相邻点。
                for index in range(1, len(trail)):
                    cv2.line(
                        frame,
                        trail[index - 1],
                        trail[index],
                        (0, 170, 255),
                        2,
                        cv2.LINE_AA,
                    )

                # 绘制画面中心十字，帮助理解图像坐标与云台中位的关系。
                center_x = width // 2
                center_y = height // 2
                cv2.line(
                    frame,
                    (center_x - 20, center_y),
                    (center_x + 20, center_y),
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                cv2.line(
                    frame,
                    (center_x, center_y - 20),
                    (center_x, center_y + 20),
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )

                # 非阻塞读取ESP32状态，避免其发送缓冲区长时间积累。
                if controller is not None:
                    controller.read_messages()

                # 计算并平滑显示FPS。
                instantaneous_fps = 1.0 / max(now - previous_frame_time, 1e-6)
                previous_frame_time = now
                smoothed_fps = (
                    instantaneous_fps
                    if smoothed_fps == 0.0
                    else 0.9 * smoothed_fps + 0.1 * instantaneous_fps
                )

                # 第一行显示性能、目标计数和运行状态。
                mode_text = "TRACKING" if tracking_enabled else "PAUSED/CENTER"
                cv2.putText(
                    frame,
                    (
                        f"FPS {smoothed_fps:.1f}  {mode_text}  "
                        f"CH0 {last_channel0_target}  CH1 {last_channel1_target}"
                    ),
                    (20, 36),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.72,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                # 第二行显示当前方向设置。
                cv2.putText(
                    frame,
                    f"Pan inverted: {pan_inverted}  Tilt inverted: {tilt_inverted}",
                    (20, 68),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.62,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                # 第三行给出运行中可使用的按键。
                cv2.putText(
                    frame,
                    "H: reverse pan  V: reverse tilt  P: pause  C: center  Q: quit",
                    (20, 100),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                # 第四行显示串口状态或dry-run提示。
                serial_text = (
                    f"Serial {controller.port}: {controller.last_message}"
                    if controller is not None
                    else "DRY RUN - servos are not being commanded"
                )
                cv2.putText(
                    frame,
                    serial_text[:100],
                    (20, height - 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (180, 220, 255),
                    1,
                    cv2.LINE_AA,
                )
                # 刷新实时控制窗口。
                cv2.imshow("Fingertip Pan-Tilt Controller - Step 4", frame)

                # 读取窗口键盘输入。
                key = cv2.waitKey(1) & 0xFF
                # Q或Esc结束程序；finally会发送CENTER。
                if key in (ord("q"), 27):
                    break
                # H切换CH0映射方向，并清除平滑历史避免突然跨越。
                if key == ord("h"):
                    pan_inverted = not pan_inverted
                    filtered_fingertip = None
                    trail.clear()
                # V切换CH1映射方向。
                if key == ord("v"):
                    tilt_inverted = not tilt_inverted
                    filtered_fingertip = None
                    trail.clear()
                # P暂停或恢复跟踪；暂停时立即请求回中。
                if key == ord("p"):
                    tracking_enabled = not tracking_enabled
                    filtered_fingertip = None
                    trail.clear()
                    if not tracking_enabled and controller is not None:
                        controller.send_center()
                        center_command_sent = True
                # C手动回中，但不退出跟踪模式。
                if key == ord("c"):
                    if controller is not None:
                        controller.send_center()
                    last_channel0_target = CHANNEL0_CENTER
                    last_channel1_target = CHANNEL1_CENTER
                    filtered_fingertip = None
                    trail.clear()
    finally:
        # 无论正常退出还是发生异常，都优先尝试让云台回中并释放串口。
        if controller is not None:
            controller.close()
        # 释放摄像头，避免下次运行提示设备被占用。
        camera.release()
        # 关闭所有OpenCV窗口。
        cv2.destroyAllWindows()


# 直接运行文件时启动main，被其他Python模块导入时不自动打开硬件。
if __name__ == "__main__":
    main()

